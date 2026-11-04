"""Local discovery and admission of exact facts; no generated text is accepted.

Admission requires both complete boundaries and a bounded local evidence check.
A category label or quote membership never establishes arbitrary entailment.
Ambiguous prose is omitted; the complete Oracle fallback preserves information.
"""

from __future__ import annotations

import re
from typing import Any

from sabermetrics.assistant_claim_fragments import (
    _printed_quote_spans,
    matching_judgment,
    text_hash,
)


def _complete(unit, start, end):
    if type(start) is not int or type(end) is not int:
        return False
    left, right = unit["start"], unit["end"]
    if not left <= start < end <= right or (start, end) == (left, right):
        return False
    claim = unit["claim"]
    a, b = start - left, end - left
    # No clause/conjunction splitting or removal of quotation/level context.
    boundaries = {0, len(claim)}
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", claim):
        boundaries.update((match.start(), match.end()))
    if a not in boundaries or b not in boundaries:
        return False
    if any(x < a < y or x < b < y for x, y in _printed_quote_spans(claim)):
        return False
    text = claim[a:b]
    if text != text.strip() or not re.search(r"[.!?]$", text):
        return False
    # Exact quotations are checked in full against their source below; referents
    # inside quoted Oracle/rule text belong to that preserved context.
    outside_quotes = re.sub(r"“[^“”]*”", "", text)
    if re.search(
        r"\b(?:it|its|they|them|their|this|these|those|that|such|former|latter)\b",
        outside_quotes,
        re.IGNORECASE,
    ):
        return False
    if re.match(
        r"(?:And|But|Then|Therefore|Otherwise|Only|Unless|Without|Needs?|Requires?|Spend|Activate)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if re.match(r"If\b", text, re.IGNORECASE) and not re.search(
        r",\s+(?:then\s+)?\S", text, re.IGNORECASE
    ):
        return False
    # Any ordinary dependent continuation could supply an omitted cost/context.
    # Only these bounded research/strategy caveats are known not to qualify a
    # printed property. Do not try to enumerate every wording of a cost.
    following = claim[b:].lstrip()
    if re.match(
        r"(?:It|Its|They|Them|Their|This|These|Those|That|Such)\b",
        following,
        re.IGNORECASE,
    ) and not re.fullmatch(
        r"It needs (?:a (?:repeatable untap )?loop|(?:more )?research)\.",
        following,
        re.IGNORECASE,
    ):
        return False
    return not re.match(
        r"(?:Only|Unless|Without|Spend|Activate|Use|Needs?|Requires?|Must|"
        r"You may .* only)\b",
        following,
        re.IGNORECASE,
    )


def _locally_supported(text, category, sources, citations):
    """Small, auditable grammar; unsupported semantic facts stay unknown."""
    # There is no local inventory/count entailment contract here. In particular,
    # relabeling a card or rules assertion as deck_fact must not bypass checks.
    if category == "deck_fact":
        return False
    cited = {c["source_index"] for c in citations}
    if category == "rules_interaction":
        # Preserve a complete attributed rule verbatim, never infer an
        # interaction from a matching phrase or an unrelated rule citation.
        for index in cited:
            source = sources[index]
            header, sep, body = source.partition("\n")
            if (
                sep
                and re.fullmatch(r"Rule \d+(?:\.\d+)*[a-z]?\.?", header)
                and sum(s.split("\n", 1)[0] == header for s in sources) == 1
                and complete_rule_source(source)
                and body.strip()
                and any(
                    c["source_index"] == index and body in c["quote"] for c in citations
                )
                and text == f'{header.rstrip(":")}: “{body}”.'
            ):
                return True
        return False
    for index in cited:
        source = sources[index]
        header = source.split("\n", 1)[0]
        if not header.startswith("Card: ") or _incomplete_source(source):
            continue
        name = header[6:]
        # Complete, unique, exact identity: never pick a convenient duplicate or
        # use Mystic's record to establish Other Engine's printed properties.
        if not name or sum(s.split("\n", 1)[0] == header for s in sources) != 1:
            continue
        if (
            source.count("\nOracle text:\n") != 1
            or source.count("\nPrinted characteristics:") != 1
        ):
            continue
        oracle, terminator, _ = source.split("\nOracle text:\n", 1)[1].partition(
            "\nPrinted characteristics:"
        )
        if not terminator or not oracle.strip():
            continue
        quotes = [c["quote"] for c in citations if c["source_index"] == index]
        if text == f"{name} Oracle text: “{oracle}”." and any(
            oracle in quote for quote in quotes
        ):
            return True
        if text == f"{name} has a tap ability." and any(
            # {T} must be an activation cost, not a symbol mentioned in an
            # effect, reminder, or quoted/granted ability.
            re.fullmatch(r"\{T\}: [^\n]+", line)
            and any(line in quote for quote in quotes)
            for line in [oracle]
        ):
            return True
    return False


def discover_subspans(unit, sources, lookup, selected):
    """Find only the three literal forms already proved by local admission.

    Search the original unit, preserving absolute offsets and catalog identities.
    A single catalog citation must contain the entire required body; adjacent
    lines are never assembled into a new quote or a broader semantic claim.
    """
    if unit.get("protected"):
        return []
    found = {}
    for index, source in enumerate(sources):
        if index not in selected:
            continue
        header, separator, body = source.partition("\n")
        forms = []
        if header.startswith("Card: ") and source.count("\nOracle text:\n") == 1:
            oracle, terminator, _ = source.split("\nOracle text:\n", 1)[1].partition(
                "\nPrinted characteristics:"
            )
            if terminator:
                name = header[6:]
                forms = [
                    (f"{name} has a tap ability.", "printed_fact", oracle),
                    (f"{name} Oracle text: “{oracle}”.", "printed_fact", oracle),
                ]
        elif separator and header.startswith("Rule "):
            forms = [(f"{header}: “{body}”.", "rules_interaction", body)]
        for text, category, required in forms:
            eid = next(
                (
                    eid
                    for eid, (source_index, quote) in lookup.items()
                    if source_index == index
                    and required
                    and required in quote
                    and _locally_supported(
                        text,
                        category,
                        sources,
                        [{"source_index": index, "quote": quote}],
                    )
                ),
                None,
            )
            if eid is None:
                continue
            for match in re.finditer(re.escape(text), unit["claim"]):
                start = unit["start"] + match.start()
                end = unit["start"] + match.end()
                if _complete(unit, start, end):
                    found.setdefault(
                        (start, end, category),
                        {
                            "start": start,
                            "end": end,
                            "category": category,
                            "evidence_ids": [eid],
                            "dependency_refs": [],
                        },
                    )
    return [found[key] for key in sorted(found)][:3]


def complete_rule_source(source: str) -> bool:
    """Reject known incomplete/placeholder records at every review boundary.

    Snapshot authentication remains the caller's responsibility. Prefix presence
    alone never establishes that usable complete rule text was supplied.
    """
    header, separator, body = source.partition("\n")
    return bool(
        separator
        and header.startswith(("Rule ", "Rule:", "Ruling"))
        and body.strip()
        and len(body.strip()) >= 12
        and not _incomplete_source(source)
        and not re.search(
            r"\b(?:no rule text|text unavailable|no evidence|not available)\b",
            body,
            re.IGNORECASE,
        )
    )


def _incomplete_source(source):
    return bool(
        re.search(
            r"\b(?:excerpt|truncat\w*|omission\w*|unavailable|placeholder)\b|…|\.\.\.",
            source,
            re.IGNORECASE,
        )
    )


def _patch_intersects(unit, start, end, patches):
    for patch in patches:
        p = patch.model_dump() if hasattr(patch, "model_dump") else patch
        if p.get("part_index") != unit["part_index"]:
            continue
        phrase = p.get("claim", "")
        if phrase and phrase not in unit["claim"]:
            continue
        # The original parent is available locally; an ambiguous patch is unsafe.
        if not phrase or unit["claim"].count(phrase) != 1:
            return True
        pos = unit["start"] + unit["claim"].index(phrase)
        if start < pos + len(phrase) and pos < end:
            return True
    return False


def bind_subspans(unit, proposals, sources, lookup, selected, patches, verdict):
    if unit.get("protected") or verdict == "supported":
        return []
    proposed = [p.model_dump() if hasattr(p, "model_dump") else p for p in proposals]
    ranges = [(p.get("start"), p.get("end")) for p in proposed]
    if any(type(a) is not int or type(b) is not int for a, b in ranges):
        return []
    if any(
        a < d and c < b for i, (a, b) in enumerate(ranges) for c, d in ranges[i + 1 :]
    ):
        return []
    bound = []
    for p in proposed:
        start, end = p["start"], p["end"]
        ids = p.get("evidence_ids", [])
        category = p.get("category")
        if (
            not _complete(unit, start, end)
            or p.get("dependency_refs")
            or category not in {"printed_fact", "deck_fact", "rules_interaction"}
            or not ids
            or len(set(ids)) != len(ids)
            or any(e not in lookup or lookup[e][0] not in selected for e in ids)
            or _patch_intersects(unit, start, end, patches)
        ):
            continue
        citations = [{"source_index": lookup[e][0], "quote": lookup[e][1]} for e in ids]
        text = unit["claim"][start - unit["start"] : end - unit["start"]]
        if not _locally_supported(text, category, sources, citations):
            continue
        bound.append(
            {
                **p,
                "text": text,
                "sha256": text_hash(text),
                "parent_claim_sha256": unit["claim_sha256"],
                "parent_part_sha256": unit["part_sha256"],
                "source_sha256": [text_hash(s) for s in sources],
                "citations": citations,
            }
        )
    return bound


def accepted_subspans(unit, judgment, review, sources):
    """Reapply the identical guards at publication/followthrough, failing closed."""
    try:
        return _accepted_subspans(unit, judgment, review, sources)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return []


def _accepted_subspans(unit, judgment, review, sources):
    if sources is None or not matching_judgment(unit, judgment):
        return []
    manifest = [text_hash(s) for s in sources]
    if review.get("source_sha256") != manifest:
        return []
    from sabermetrics.assistant_claim_review import evidence_catalog

    _, lookup = evidence_catalog(sources)
    selected = set(review.get("evidence_indices", []))
    saved = judgment.get("supported_subspans", [])
    rebound = bind_subspans(
        unit,
        saved,
        sources,
        lookup,
        selected,
        review.get("patches", []),
        judgment.get("verdict"),
    )
    # Exact local receipt identity: hashes, text, evidence indices and citations
    # cannot be replaced/rebased while retaining permission to publish.
    return rebound if rebound == saved else []


def source_manifest_matches(review: dict[str, Any], sources: list[str]) -> bool:
    return review.get("source_sha256") == [text_hash(s) for s in sources]
