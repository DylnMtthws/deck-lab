"""Bounded literal interpretations, never a proof of playability or a loop.

Offsets are Python string offsets into source.oracle_text. ``copied_fields`` is
an exact JSON copy of the input record within the text and record byte budgets.
Overflow retains a hash ledger and as much original text as fits, and withholds
all interpretations. Rules accepts one source record or {"sources": [records]};
each rule needs kind, source, version, complete=True and text/oracle_text.
Cancellation callbacks may raise; returning False also cancels compilation.
No cache or external services are used.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

VERSION = "assistant-inference-facts-v1"
MAX_CARDS = 128
MAX_TEXT_BYTES = 100_000
MAX_RECORD_BYTES = 512_000


def _json(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raw_text_bytes(value):
    """Count rules/ability text, including nested faces, separately from provenance."""
    if isinstance(value, list):
        return sum(_raw_text_bytes(item) for item in value)
    if not isinstance(value, dict):
        return 0
    return sum(
        (
            len(item.encode("utf-8"))
            if key
            in {"oracle_text", "text", "printed_text", "reminder_text", "rules_text"}
            and isinstance(item, str)
            else _raw_text_bytes(item)
        )
        for key, item in value.items()
    )


def _check(callback):
    if callback is not None and callback() is False:
        raise InterruptedError("Fact compilation cancelled")


def _event(event, **fields):
    return {"event": event, **fields}


_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3}
_DRAW = r"[Dd]raw (a|one|two|three) cards?\."


def _self_reference(name, type_line, card_names):
    """Source-local references only; no substitution of the evidence text.

    Short legendary names must be complete first components, and must not
    identify another supplied card. Types come from the type section, never
    a subtype or an ability that might change the permanent's type.
    """
    types = set(type_line.split("—", 1)[0].split())
    references = [re.escape(name)]
    first, comma, _ = name.partition(",")
    if (
        "Legendary" in types
        and comma
        and first
        and all(
            other == name or other.partition(",")[0].casefold() != first.casefold()
            for other in card_names
        )
    ):
        references.append(re.escape(first))
    for label, compatible in (
        ("creature", "Creature" in types),
        ("artifact", "Artifact" in types),
        (
            "permanent",
            bool(
                types
                & {
                    "Artifact",
                    "Battle",
                    "Creature",
                    "Enchantment",
                    "Land",
                    "Planeswalker",
                }
            ),
        ),
    ):
        if compatible:
            references.append(r"(?i:this " + label + ")")
    return "(?:" + "|".join(references) + ")"


def _effect(text, self_reference, source_id):
    match = re.fullmatch(_DRAW, text)
    if match:
        return (
            [
                _event(
                    "draw",
                    actor="you",
                    subject="card",
                    from_zone="library",
                    to_zone="hand",
                    quantity=_NUMBERS[match[1]],
                )
            ],
            [],
            [],
        )
    if re.fullmatch(self_reference + r" explores\.", text):
        return (
            [_event("explore", actor="you", subject="self", source_id=source_id)],
            [],
            ["explore"],
        )
    if re.fullmatch(
        self_reference + r" gets \+[1-9][0-9]?/\+[1-9][0-9]? until end of turn\.",
        text,
    ) or re.fullmatch(self_reference + r" gains flying until end of turn\.", text):
        return [], [text], []
    if text == "Put the top card of your library into your hand.":
        return (
            [
                _event(
                    "put_into_hand",
                    actor="you",
                    subject="top card",
                    from_zone="library",
                    to_zone="hand",
                    quantity=1,
                )
            ],
            [],
            [],
        )
    return None


def _interpret(text, self_reference, source_id, type_line):
    """Return only full-scope matches; unknown suffixes always abstain."""
    match = re.fullmatch(r"(.+): (.+)", text)
    if match:
        costs = []
        inputs = []
        # Match entire cost tokens so commas inside a canonical name remain
        # part of that reference rather than becoming cost separators.
        symbols = r"(?:\{(?:[0-9]+|[WUBRGCST])\})+"
        cost_token = (
            r"(?:"
            + symbols
            + r"|Discard a card|Sacrifice "
            + self_reference
            + r"|Sacrifice a creature)"
        )
        if not re.fullmatch(cost_token + r"(?:, " + cost_token + r")*", match[1]):
            return None
        for token in re.finditer(cost_token, match[1]):
            cost = token.group()
            if re.fullmatch(r"(?:\{(?:[0-9]+|[WUBRGCST])\})+", cost):
                costs.append({"kind": "printed_symbols", "text": cost})
            elif cost == "Discard a card":
                costs.append({"kind": "discard", "text": cost, "quantity": 1})
                inputs.append(
                    _event(
                        "discard",
                        actor="you",
                        subject="card",
                        from_zone="hand",
                        to_zone="graveyard",
                        quantity=1,
                    )
                )
            elif (
                re.fullmatch(r"Sacrifice " + self_reference, cost)
                or cost == "Sacrifice a creature"
            ):
                subject = (
                    "a creature you control"
                    if cost == "Sacrifice a creature"
                    else "self"
                )
                costs.append({"kind": "sacrifice", "text": cost, "quantity": 1})
                inputs.append(
                    _event(
                        "sacrifice",
                        actor="you",
                        subject=subject,
                        from_zone="battlefield",
                        to_zone="graveyard",
                        quantity=1,
                    )
                )
            else:
                return None
        effect = _effect(match[2], self_reference, source_id)
        if not effect:
            return None
        outputs, conditions, rules = effect
        conditions = [
            "Activation occurs only after all printed costs are paid.",
            *conditions,
        ]
        if any(c["kind"] == "discard" for c in costs):
            conditions.append(
                "Each activation consumes a card in hand; no renewable fuel is established."
            )
        return "activated", inputs, outputs, costs, conditions, rules
    match = re.fullmatch(
        r"Whenever you discard (a card|one or more cards), "
        + self_reference
        + r" deals (1|that much|that many) damage to each opponent\.",
        text,
    )
    if match:
        # Correlated quantities only: retain the printed discard batch scope.
        if (match[1], match[2]) not in (
            ("a card", "1"),
            ("one or more cards", "that many"),
            ("one or more cards", "that much"),
        ):
            return None
        batch = match[1] == "one or more cards"
        return (
            "triggered",
            [
                _event(
                    "discard",
                    actor="you",
                    subject="card",
                    from_zone="hand",
                    to_zone="graveyard",
                    quantity="one_or_more" if batch else 1,
                )
            ],
            [
                _event(
                    "damage",
                    actor="self",
                    subject="self",
                    source_id=source_id,
                    source_types=type_line,
                    recipient="each opponent",
                    quantity="discarded_card_count" if batch else 1,
                )
            ],
            [],
            ["Once per discard batch." if batch else "Once per discarded card."],
            [],
        )
    match = re.fullmatch(
        r"Whenever one or more ([A-Za-z]+(?: and/or [A-Za-z]+)?) you control deal (combat )?damage to a player, (.+)",
        text,
    )
    if match:
        effect = _effect(match[3], self_reference, source_id)
        if effect:
            outputs, conditions, rules = effect
            return (
                "triggered",
                [
                    _event(
                        "damage",
                        actor="you",
                        subject="one or more sources you control",
                        source_types=match[1],
                        recipient="a player",
                        quantity="unknown",
                        combat_only=bool(match[2]),
                    )
                ],
                outputs,
                [],
                ["Once per qualifying damage event; one or more sources.", *conditions],
                rules,
            )
    match = re.fullmatch(
        r"Whenever ("
        + self_reference
        + r"|a creature you control|another creature you control) dies, (.+)",
        text,
    )
    if match:
        effect = _effect(match[2], self_reference, source_id)
        if effect:
            outputs, conditions, rules = effect
            return (
                "triggered",
                [
                    _event(
                        "death",
                        actor="you",
                        subject=(
                            match[1]
                            if match[1]
                            in {
                                "a creature you control",
                                "another creature you control",
                            }
                            else "self"
                        ),
                        from_zone="battlefield",
                        to_zone="graveyard",
                        quantity=1,
                    )
                ],
                outputs,
                [],
                conditions,
                rules,
            )
    # Bare effects are spell instructions only, never creature static text.
    if re.search(r"\b(?:Instant|Sorcery)\b", type_line):
        effect = _effect(text, self_reference, source_id)
        if effect:
            outputs, conditions, rules = effect
            return "spell", [], outputs, [], conditions, rules
    return None


_EXPLORE = re.compile(
    r"To explore, (?:a|the) creature's controller reveals the top card of their library\. "
    r"If (?:a land card is|it's a land card,? it is) revealed this way, (?:they put|that player puts) it into their hand\. "
    r"Otherwise, (?:they put|that player puts) a \+1/\+1 counter on (?:the|that) creature, then (?:they may|that player may) put "
    r"(?:the revealed|that) card into their graveyard\."
)


_EXPLORE_PERMANENT = re.compile(
    r"(?:[0-9]+\.[0-9]+[a-z] +)?Certain (?:spells and )?abilities instruct a permanent to explore\. "
    r"To do so, that permanent[’']s controller reveals the top card of their library\. "
    r"If a land card is revealed this way, that player puts that card into their hand\. "
    r"Otherwise, that player puts a \+1/\+1 counter on the exploring permanent(?:, then| and) may put "
    r"the revealed card into their graveyard\."
)


def _rule_interpret(text):
    permanent = _EXPLORE_PERMANENT.fullmatch(text)
    if not permanent and not _EXPLORE.fullmatch(text):
        return None
    unit = "permanent" if permanent else "creature"
    controller = f"{unit} controller"
    return (
        "rule_definition",
        [_event("explore", actor=controller, subject=f"exploring {unit}")],
        [
            _event(
                "put_into_hand",
                actor=controller,
                subject="revealed top card",
                from_zone="library",
                to_zone="hand",
                quantity=1,
                condition="revealed card is a land",
            ),
            _event(
                "move_to_graveyard",
                actor=controller,
                subject="revealed top card",
                from_zone="library",
                to_zone="graveyard",
                quantity=1,
                condition="revealed card is nonland and controller chooses to put it in graveyard",
                optional=True,
            ),
        ],
        [],
        [
            f"Reveal the top card of the exploring {unit} controller's library.",
            "If land: put the revealed card into hand.",
            f"If nonland: put a +1/+1 counter on the exploring {unit}; optionally put the revealed card into graveyard, otherwise leave it on top.",
            "Moving a revealed card from library to graveyard is not discarding.",
            "Library contents and cards in hand are finite supplied fuel; replenishment is not established.",
        ],
        [],
    )


_UNSUPPORTED_SCOPE = re.compile(
    r'\b(?:LEVEL|Level up|Class|[Ll]evel [0-9]|has|have|as long as|following abilit(?:y|ies)|only|unless|instead)\b|[“”"]|(?:^|\n)(?:If |Otherwise|[Ll]evel )',
    re.IGNORECASE,
)


def _scope_introducer(value):
    # Reminder text cannot open an enclosing ability scope on the next line.
    # Inspect positions in the original string; never normalize parsed text.
    depth = 0
    outside = set()
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        if depth == 0:
            outside.add(index)
        if char == ")":
            depth = max(0, depth - 1)
    return any(
        match.start() in outside
        for match in re.finditer(
            r"\b(?:LEVEL|Level up|[Ll]evel [0-9]|Class\b|[Aa]s long as|If |Otherwise)"
            r"|following abilit(?:y|ies)",
            value,
        )
    )


def _card_spans(text):
    """Keep enclosing scopes intact; a newline alone does not end a scope.

    Open-ended grants, conditional introductions and level/class blocks have
    no supported closing grammar, so conservatively retain their entire tail.
    Quoted grants can close explicitly. Restrictions belong to the preceding
    ability, including when a second restriction follows the first.
    """
    spans = []
    start = None
    quoted = False
    tail = False
    for line in re.finditer(r"[^\n]+", text):
        value = line.group()
        terminal = re.search(r'[.!?](?:[)”"]|\s*\([^\n]*\))*\s*$', value)
        restriction = re.match(
            r"(?:Activate|Cast|Use|Spend|This ability triggers|Trigger|Do this|Only|You may activate this ability|You can activate this ability)\b",
            value.lstrip(),
        )
        if start is None:
            start = line.start()
            if restriction and spans:
                start = spans.pop()[0]
        # Count opening/closing straight or curly quotes, never apostrophes.
        if sum(value.count(c) for c in ('"', "“", "”")) % 2:
            quoted = not quoted
        if _scope_introducer(value):
            tail = True
        if (
            not quoted
            and not terminal
            and (
                re.search(r"\b(?:has|have|gain|gains)\b", value)
                or re.match(r"(?:Whenever|When|At|Unless)\b", value)
                or ":" in value
            )
        ):
            tail = True
        if not quoted and not tail:
            spans.append((start, line.end(), text[start : line.end()]))
            start = None
    if start is not None:
        spans.append((start, len(text), text[start:]))
    return spans or [(0, 0, "")]


def compile_deck_facts(
    cards: list[dict], rules: dict | None = None, *, check_current=None
) -> dict:
    """Compile conservative, source-bound facts from JSON-compatible records.

    Missing identities, conflicting duplicates and truncation
    withhold a whole source. Unsupported scopes withhold their enclosing span.
    Coverage.complete means every supplied clause was
    interpreted, never that game mechanics were verified. Raw text and canonical records have separate byte budgets; nested face text
    counts toward the text budget, while provenance counts toward the record budget.
    """
    _check(check_current)
    rule_records = [] if rules is None else rules.get("sources", [rules])
    if not isinstance(rule_records, list):
        raise TypeError("rules.sources must be a list")
    records = []
    for kind, values in (("card", cards), ("rule", rule_records)):
        for record in values:
            _check(check_current)
            canonical = _json(record)
            copied = json.loads(canonical)
            digest = _hash(canonical)
            identity = (
                copied.get("oracle_id") or copied.get("card_id") or copied.get("id")
            )
            if kind == "card":
                sid = "card:" + str(identity or "missing:" + digest)
            else:
                sid = "rule:" + _hash(
                    _json(
                        [
                            copied.get("kind"),
                            copied.get("source"),
                            copied.get("version"),
                            digest,
                        ]
                    )
                )
            records.append((kind, copied, digest, identity, sid, canonical))
    card_names = {r[1].get("name", "") for r in records if r[0] == "card"}
    occurrences = Counter((r[4], r[2]) for r in records)
    identities: dict[str, set[str]] = {}
    rule_identities: dict[str, set[str]] = {}
    rule_keys = {}
    for kind, record, digest, _, sid, _ in records:
        if kind == "card":
            identities.setdefault(sid, set()).add(digest)
        else:
            # Raw source IDs include a digest for exact, unambiguous bindings.
            # Conflict detection must instead use the supplied logical identity.
            key = _json(
                [
                    record.get("id") or record.get("source_id"),
                    record.get("kind"),
                    record.get("source"),
                    record.get("version"),
                ]
            )
            rule_keys[sid] = key
            body = _json(record.get("oracle_text", record.get("text", "")))
            rule_identities.setdefault(key, set()).add(body)
    total_bytes = sum(len(r[5].encode("utf-8")) for r in records)
    total_text_bytes = sum(_raw_text_bytes(r[1]) for r in records)
    overflow = (
        len(cards) > MAX_CARDS
        or total_text_bytes > MAX_TEXT_BYTES
        or total_bytes > MAX_RECORD_BYTES
    )
    coverage: dict[str, Any] = {
        "complete": False,
        "input_cards": len(cards),
        "input_sources": len(records),
        "input_bytes": total_bytes,
        "input_record_bytes": total_bytes,
        "input_text_bytes": total_text_bytes,
        "limits": {
            "cards": MAX_CARDS,
            "bytes": MAX_TEXT_BYTES,
            "text_bytes": MAX_TEXT_BYTES,
            "record_bytes": MAX_RECORD_BYTES,
        },
        "overflow": overflow,
        "issues": ["input_limit_exceeded"] if overflow else [],
        "unresolved_rules": [],
        "raw_source_ledger": [],
    }
    result: dict[str, Any] = {
        "version": VERSION,
        "sources": [],
        "facts": [],
        "coverage": coverage,
    }
    remaining = MAX_RECORD_BYTES
    text_remaining = MAX_TEXT_BYTES
    seen = set()
    explore_rules: list[str] = []
    for kind, record, digest, identity, sid, canonical in sorted(
        records, key=lambda r: (r[4], r[2])
    ):
        _check(check_current)
        if (sid, digest) in seen:
            continue
        seen.add((sid, digest))
        issues = []
        text = record.get("oracle_text", record.get("text", ""))
        if not isinstance(text, str):
            text = ""
            issues.append("invalid_oracle_text")
        if not text:
            issues.append("missing_oracle_text")
        if kind == "card" and (
            not identity or not record.get("name") or not record.get("type_line")
        ):
            issues.append("missing_identity_or_printed_fields")
        if kind == "card" and len(identities[sid]) > 1:
            issues.append("conflicting_duplicate_identity")
        if (
            any(
                record.get(k)
                for k in (
                    "truncated",
                    "is_truncated",
                    "text_truncated",
                    "oracle_text_truncated",
                    "excerpt",
                )
            )
            or record.get("complete") is False
            or "…" in text
            or "..." in text
        ):
            issues.append("incomplete_source")
        if kind == "card" and (
            record.get("card_faces")
            or record.get("layout")
            in ("leveler", "transform", "modal_dfc", "split", "adventure")
        ):
            issues.append("unsupported_ability_scope")
        if kind == "rule":
            if len(rule_identities[rule_keys[sid]]) > 1:
                issues.append("conflicting_duplicate_identity")
            if (
                not all(record.get(k) for k in ("kind", "source", "version"))
                or record.get("complete") is not True
            ):
                issues.append("incomplete_rule_provenance")
            if record.get("text_hash") is not None and record["text_hash"] != _hash(
                text
            ):
                issues.append("rule_text_hash_mismatch")
        encoded = canonical.encode("utf-8")
        raw_bytes = _raw_text_bytes(record)
        if len(encoded) <= remaining and raw_bytes <= text_remaining:
            copied_fields = record
            remaining -= len(encoded)
            text_remaining -= raw_bytes
        else:
            copied_fields = {}
            text = text.encode("utf-8")[: min(remaining, text_remaining)].decode(
                "utf-8", errors="ignore"
            )
            retained_bytes = len(text.encode("utf-8"))
            remaining -= retained_bytes
            text_remaining -= retained_bytes
            issues.append("raw_record_omitted_byte_limit")
        if overflow:
            issues.append("input_limit_exceeded")
        source: dict[str, Any] = {
            "source_id": sid,
            "card_id": identity if kind == "card" else None,
            "name": record.get("name", ""),
            "source_sha256": digest,
            "oracle_text": text,
            "type_line": record.get("type_line", ""),
            "copied_fields": copied_fields,
            "clauses": [],
        }
        coverage["raw_source_ledger"].append(
            {
                "source_id": sid,
                "source_sha256": digest,
                "record_bytes": len(encoded),
                "raw_text_bytes": raw_bytes,
                "occurrences": occurrences[(sid, digest)],
                "retained_full_record": bool(copied_fields),
                "issues": list(issues),
            }
        )
        result["sources"].append(source)
        spans = [(0, len(text), text)] if kind == "rule" else _card_spans(text)
        if not spans:
            spans = [(0, 0, "")]
        for start, end, quote in spans:
            _check(check_current)
            clause_issues = list(issues)
            if kind == "card" and _UNSUPPORTED_SCOPE.search(quote):
                clause_issues.append("unsupported_ability_scope")
            cid = f"{sid}:{digest}:clause:{start}:{end}"
            parsed = (
                None
                if clause_issues
                else (
                    (
                        _rule_interpret(quote)
                        if record.get("kind") != "card_ruling"
                        else None
                    )
                    if kind == "rule"
                    else _interpret(
                        quote,
                        _self_reference(
                            source["name"], source["type_line"], card_names
                        ),
                        sid,
                        source["type_line"],
                    )
                )
            )
            clause: dict[str, Any] = {
                "clause_id": cid,
                "start": start,
                "end": end,
                "text": quote,
                "parse_status": "withheld" if clause_issues else "uninterpreted",
                "fact_ids": [],
                "issues": (
                    clause_issues
                    if clause_issues
                    else ([] if parsed else ["unrecognized_full_ability"])
                ),
            }
            source["clauses"].append(clause)
            if not parsed:
                continue
            fact_kind, inputs, outputs, costs, conditions, required = parsed
            if fact_kind in {"activated", "triggered"}:
                for output in outputs:
                    if output["event"] == "damage":
                        output["combat"] = False
            fid = cid + ":fact"
            fact: dict[str, Any] = {
                "fact_id": fid,
                "source_id": sid,
                "source_sha256": digest,
                "start": start,
                "end": end,
                "quote": quote,
                "kind": fact_kind,
                "inputs": inputs,
                "outputs": outputs,
                "costs": costs,
                "conditions": (
                    [
                        "You/your refers to this ability controller, or this spell controller for spell instructions."
                    ]
                    if kind == "card"
                    else []
                )
                + conditions,
                "required_rules": required,
                "status": "bounded_interpretation",
            }
            result["facts"].append(fact)
            clause.update(parse_status="interpreted", fact_ids=[fid])
            if kind == "rule":
                explore_rules.append(fid)
    for fact in result["facts"]:
        if "explore" in fact["required_rules"]:
            if len(explore_rules) == 1:
                fact["required_rules"] = explore_rules[:]
            else:
                coverage["unresolved_rules"] = ["explore"]
    counts = Counter(c["parse_status"] for s in result["sources"] for c in s["clauses"])
    coverage.update(
        {
            "clauses": sum(counts.values()),
            "interpreted": counts["interpreted"],
            "uninterpreted": counts["uninterpreted"],
            "withheld": counts["withheld"],
            "facts": len(result["facts"]),
        }
    )
    coverage["complete"] = not (
        overflow
        or counts["uninterpreted"]
        or counts["withheld"]
        or coverage["unresolved_rules"]
    )
    _check(check_current)
    return result
