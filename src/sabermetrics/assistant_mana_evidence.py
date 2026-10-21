"""Offline, bounded Kinnan mana evidence; no fit score or availability inference.

Integration contract (all mappings are JSON serializable):
* deck_document: current ``entries`` with typed ``card_id``, ``oracle_id``,
  ``is_commander``. Candidate sources need not already be in the deck.
* card_records: complete single-face Oracle records with id (or card_id),
  oracle_id, name, mana_cost, type_line, layout, oracle_text, is_current=True,
  oracle_text_complete=True and source_sha256=card_source_hash(record).
  The hash is this module's canonical fact hash, not a snapshot/file hash.
* rules_evidence: ``hits`` using assistant_rules.RuleHit fields, plus
  is_current=True and complete=True on each hit. Complete text must hash to
  content_sha256. CR hits require official Wizards URLs; card rulings require
  attributed Wizards authorship with a Wizards or Scryfall transport URL, and
  card_oracle_id. Bulk attribution is retained exactly as retrieve_rules emits it.

Adapters must establish currentness/completeness from their trusted snapshot,
not silently set these flags on truncated retrieval hits. Hashes bind evidence;
they do not authenticate a publisher or establish freshness by themselves.
Unknown inputs remain inspectable but admit no interaction conclusion.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

VERSION = "assistant-mana-evidence.v1"
KINNAN_TRIGGER = (
    "Whenever you tap a nonland permanent for mana, add one mana of any type "
    "that permanent produced."
)
KINNAN_ACTIVATION = (
    "{5}{G}{U}: Look at the top five cards of your library. You may put a non-Human "
    "creature card from among them onto the battlefield. Put the rest on the "
    "bottom of your library in a random order."
)
KINNAN_ORACLE_ID = "8d11aa49-d4cd-48b1-aa0f-8548fa733416"
_CARD_FIELDS = (
    "id",
    "card_id",
    "oracle_id",
    "name",
    "mana_cost",
    "type_line",
    "layout",
    "oracle_text",
    "card_faces",
    "oracle_text_truncated",
    "oracle_text_complete",
    "is_current",
)
_WIZARDS = {"magic.wizards.com", "www.magic.wizards.com", "media.wizards.com"}


class CardIdentity(TypedDict):
    card_id: str
    oracle_id: str
    name: str
    source_sha256: str


class ManaConstraint(TypedDict):
    kind: Literal["legendary_spell_only", "activated_abilities_only"]
    quote: str


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def card_source_hash(card: dict[str, Any]) -> str:
    """Public canonical fact binding; must be recomputed when facts change."""
    return _hash({key: card[key] for key in _CARD_FIELDS if key in card})


def _identity(card: dict[str, Any]) -> CardIdentity:
    return {
        "card_id": str(card.get("id") or card.get("card_id") or ""),
        "oracle_id": str(card.get("oracle_id") or ""),
        "name": str(card.get("name") or ""),
        "source_sha256": card_source_hash(card),
    }


def _unknowns(card: dict[str, Any]) -> list[str]:
    issues = []
    identity_fields = (
        card.get("id") or card.get("card_id"),
        card.get("oracle_id"),
        card.get("name"),
    )
    if any(
        not isinstance(value, str) or not value.strip() for value in identity_fields
    ):
        issues.append("missing_typed_card_identity")
    if card.get("id") and card.get("card_id") and card["id"] != card["card_id"]:
        issues.append("conflicting_printing_identity")
    if card.get("source_sha256") != card_source_hash(card):
        issues.append("card_source_hash_mismatch")
    if card.get("is_current") is not True:
        issues.append("current_card_facts_unverified")
    if (
        card.get("oracle_text_complete") is not True
        or card.get("oracle_text_truncated")
        or not isinstance(card.get("oracle_text"), str)
        or not card.get("oracle_text")
    ):
        issues.append("incomplete_oracle_text")
    if not all(
        isinstance(card.get(k), str) and card[k]
        for k in ("mana_cost", "type_line", "layout")
    ):
        issues.append("incomplete_card_facts")
    if card.get("layout") != "normal" or card.get("card_faces"):
        issues.append("unsupported_layout")
    text = str(card.get("oracle_text") or "")
    if "•" in text or re.search(r"\blevel\b|\bclass\b", text, re.IGNORECASE):
        issues.append("unsupported_modal_or_progression")
    if "Land" in str(card.get("type_line") or "").split("—")[0].split():
        issues.append("land_source_out_of_scope")
    main_types = set(str(card.get("type_line") or "").split("—")[0].split())
    if not main_types & {
        "Creature",
        "Artifact",
        "Enchantment",
        "Planeswalker",
        "Battle",
    }:
        issues.append("unsupported_nonpermanent_source")
    return issues


def _normal(text: str) -> str:
    return " ".join(text.replace("“", '"').replace("”", '"').replace("’", "'").split())


def _rule_binding(hit: dict[str, Any], oracle_id: str) -> bool:
    text = hit.get("text")
    if (
        not isinstance(text, str)
        or not text
        or hit.get("text_truncated")
        or hit.get("complete") is not True
        or hit.get("is_current") is not True
        or not hit.get("source_id")
        or not hit.get("version")
        or hit.get("content_sha256") != hashlib.sha256(text.encode()).hexdigest()
    ):
        return False
    url = urlsplit(str(hit.get("source_url") or ""))
    if url.scheme != "https" or url.username or url.password:
        return False
    if hit.get("kind") == "rule":
        return url.hostname in _WIZARDS
    source = str(hit.get("source") or "")
    bulk_attribution = bool(
        re.fullmatch(
            r"Scryfall bulk rulings .+ \(sha256 [0-9a-f]{12}\); original source "
            r"labeled 'wotc' by Scryfall; stored ruling, source authentication not independently verified",
            source,
        )
    )
    wotc_attribution = source in {
        "wotc",
        "wotc; stored ruling, source authentication not independently verified",
    }
    return (
        hit.get("kind") == "card_ruling"
        and (wotc_attribution or bulk_attribution)
        and hit.get("card_oracle_id") == oracle_id
        and (
            url.hostname in _WIZARDS | {"api.scryfall.com", "scryfall.com"}
            or (bulk_attribution and url.hostname == "data.scryfall.io")
        )
    )


# Deliberately bounded text admission. A new edition/wording requires review;
# rule numbers, semantic similarity or an official URL alone admit nothing.
_CR_106_12 = (
    'To "tap [a permanent] for mana" is to activate a mana ability of that '
    "permanent that includes the {T} symbol in its activation cost. See rule 605, "
    '"Mana Abilities."'
)
_CR_106_12A = (
    'An ability that triggers whenever a permanent "is tapped for mana" or is '
    "tapped for mana of a specified type triggers whenever such a mana ability "
    "resolves and produces mana or the specified type of mana."
)
_KINNAN_RULING = (
    "Kinnan doesn't care about any restrictions or riders the nonland permanent "
    "put on the mana it produced. The additional mana Kinnan produces won't "
    "have any restrictions or riders."
)


def _rule_dependencies(
    hits: list[dict[str, Any]], oracle_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    selected = []
    missing = []
    for key, body in (
        ("CR 106.12", _CR_106_12),
        ("CR 106.12a", _CR_106_12A),
        ("kinnan_mana_ruling", _KINNAN_RULING),
    ):
        matches = []
        for hit in hits:
            if not _rule_binding(hit, oracle_id):
                continue
            text = _normal(hit["text"])
            if key.startswith("CR "):
                text = re.sub(r"^" + re.escape(key[3:]) + r"\.?\s+", "", text)
                eligible = hit.get("kind") == "rule" and hit.get("source_id") == key
            else:
                eligible = hit.get("kind") == "card_ruling"
            if eligible and text == _normal(body):
                matches.append(hit)
        conflicting_rule = key.startswith("CR ") and any(
            _rule_binding(hit, oracle_id)
            and hit.get("kind") == "rule"
            and hit.get("source_id") == key
            and _normal(re.sub(r"^" + re.escape(key[3:]) + r"\.?\s+", "", hit["text"]))
            != _normal(body)
            for hit in hits
        )
        if conflicting_rule:
            missing.append("conflicting_current_" + key.replace(" ", "_"))
        elif (
            key.startswith("CR ")
            and len({(hit["version"], hit["source_url"]) for hit in matches}) > 1
        ):
            missing.append("ambiguous_current_snapshot_" + key.replace(" ", "_"))
        elif matches:
            selected.append(deepcopy(min(matches, key=_hash)))
        else:
            missing.append("missing_complete_applicable_" + key.replace(" ", "_"))
    cr_records = [hit for hit in selected if hit["kind"] == "rule"]
    if len({(hit["version"], hit["source_url"]) for hit in cr_records}) > 1:
        missing.append("mixed_rules_snapshots")
    return selected, missing


def _abilities(card: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Only literal tap-only mana abilities with these exact restriction forms."""
    rows = []
    raw = card["oracle_text"]
    offset = 0
    for line in raw.splitlines(keepends=True):
        quote = line.rstrip("\r\n")
        match = re.fullmatch(
            r"\{T\}: Add (?P<output>(?:\{[WUBRGC]\})+|one mana of any color)\."
            r"(?P<tail>.*)",
            quote,
        )
        if not match:
            return None
        tail = match["tail"]
        constraints: list[ManaConstraint] = []
        benefit = None
        if (
            tail
            == " Spend this mana only to cast a legendary spell, and that spell can't be countered."
        ):
            constraints.append({"kind": "legendary_spell_only", "quote": tail.strip()})
            benefit = {
                "kind": "spell_cant_be_countered",
                "condition": "Spend this source mana to cast that legendary spell.",
                "quote": tail.strip(),
            }
        elif tail == " Spend this mana only to activate abilities.":
            constraints.append(
                {"kind": "activated_abilities_only", "quote": tail.strip()}
            )
        elif tail:
            return None
        output = match["output"]
        rows.append(
            {
                "quote": quote,
                "char_start": offset,
                "char_end": offset + len(quote),
                "activation_cost": "{T}",
                "source_mana": {
                    "quantity": (
                        1
                        if output == "one mana of any color"
                        else len(re.findall(r"\{[WUBRGC]\}", output))
                    ),
                    "type_options": (
                        list("WUBRG")
                        if output == "one mana of any color"
                        else list(dict.fromkeys(re.findall(r"\{([WUBRGC])\}", output)))
                    ),
                    "constraints": constraints,
                    "spell_benefit": benefit,
                    "constraint_scope": "This source ability's mana only.",
                },
            }
        )
        offset += len(line)
    return rows or None


def build_mana_evidence(
    deck_document: dict[str, Any],
    card_records: list[dict[str, Any]],
    rules_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Annotate source-bound conditional interactions, never rank or prune cards.

    Return one row per supplied non-Kinnan record, including unknown rows.
    ``admitted_claims`` are the *only* narrow interaction conclusions admitted.
    Every claim binds both current card identities and full rule records by
    evidence_id; exact quotes retain character offsets into complete Oracle text.
    """
    entries = deck_document.get("entries") or []
    commanders = [c for c in card_records if c.get("oracle_id") == KINNAN_ORACLE_ID]
    commander = commanders[0] if len(commanders) == 1 else None
    global_unknowns = []
    if commander is None:
        global_unknowns.append("missing_or_ambiguous_kinnan_record")
    else:
        global_unknowns.extend("commander_" + u for u in _unknowns(commander))
        if (
            commander.get("name") != "Kinnan, Bonder Prodigy"
            or commander.get("mana_cost") != "{G}{U}"
            or commander.get("type_line") != "Legendary Creature — Human Druid"
            or commander.get("oracle_text") != KINNAN_TRIGGER + "\n" + KINNAN_ACTIVATION
        ):
            global_unknowns.append("unsupported_kinnan_oracle")
        if not any(
            e.get("is_commander") is True
            and e.get("oracle_id") == KINNAN_ORACLE_ID
            and e.get("card_id") == _identity(commander)["card_id"]
            for e in entries
        ):
            global_unknowns.append("kinnan_not_bound_to_deck_commander")
    rule_records, missing = _rule_dependencies(
        rules_evidence.get("hits") or [], KINNAN_ORACLE_ID
    )
    global_unknowns.extend(missing)
    rows = []
    for card in card_records:
        if card.get("oracle_id") == KINNAN_ORACLE_ID:
            continue
        source_unknowns = _unknowns(card)
        unknowns = [*global_unknowns, *source_unknowns]
        abilities = _abilities(card) if not source_unknowns else None
        if abilities is None:
            unknowns.append("unsupported_source_ability")
        if sum(c.get("oracle_id") == card.get("oracle_id") for c in card_records) != 1:
            unknowns.append("ambiguous_source_identity")
        row: dict[str, Any] = {
            "source": _identity(card),
            "commander": _identity(commander) if commander else None,
            "source_oracle_text": card.get("oracle_text"),
            "source_casting_cost": card.get("mana_cost"),
            "commander_oracle_text": (
                commander.get("oracle_text") if commander else None
            ),
            "commander_casting_cost": commander.get("mana_cost") if commander else None,
            "abilities": abilities or [],
            "rules": deepcopy(rule_records),
            "status": "unknown" if unknowns else "bounded_conditional_evidence",
            "unknowns": list(dict.fromkeys(unknowns)),
            "conditions": [
                "Kinnan and the source must be under your control on the battlefield.",
                "Source tap ability must legally resolve and produce the stated mana.",
                "Pay every cost; creature tap eligibility, untaps, available mana and timing remain unverified.",
            ],
            "admitted_claims": [],
        }
        if not unknowns:
            for ability in row["abilities"]:
                ability["kinnan_extra_mana"] = {
                    "quantity": 1,
                    "type_choice": "One type actually produced by this source activation.",
                    "possible_types": ability["source_mana"]["type_options"],
                    "inherits_source_spending_restriction": False,
                    "inherits_source_spell_benefit": False,
                    "producer_oracle_id": KINNAN_ORACLE_ID,
                }
            row["admitted_claims"] = [
                "A qualifying source activation conditionally produces one separate Kinnan mana of a type actually produced.",
                "Source restrictions and spell benefits remain attached to source mana; they do not transfer to Kinnan's extra mana.",
                "Kinnan has a printed activated ability with full activation cost {5}{G}{U}; this establishes an outlet, not payment or availability.",
            ]
        row["evidence_id"] = VERSION + ":" + _hash(row)
        rows.append(row)
    return {
        "version": VERSION,
        "rows": rows,
        "scope": "Conditional source/commander mana evidence only; no overall deck fit, resource availability, legality execution, scoring or pruning.",
        "admission_policy": "Admit only listed claims on bounded_conditional_evidence rows with matching evidence_id and current source hashes. Unknown rows admit no interaction correction. Never describe restricted source mana as unrestricted.",
    }
