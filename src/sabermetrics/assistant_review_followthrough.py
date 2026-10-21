"""Preserve literal candidate facts while removing unverified discovery assertions."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sabermetrics.assistant_answer_review import _mentions_card_name
from sabermetrics.assistant_claim_subspans import (
    accepted_subspans,
    source_manifest_matches,
)


def _literal_oracle(prefix: str, sources: list[str]) -> tuple[str, str]:
    records = [
        s
        for s in sources
        if s.startswith("Card: ")
        and _mentions_card_name(prefix, s.split("\n", 1)[0][6:])
    ]
    # Multiple records/names are ambiguous; never choose the most convenient.
    if len(records) != 1:
        return "", ""
    source = records[0]
    if "\nOracle text:\n" not in source or re.search(
        r"\b(?:excerpt|truncat\w*|omission\w*|unavailable)\b|…|\.\.\.",
        source,
        re.IGNORECASE,
    ):
        return "", ""
    oracle = source.split("\nOracle text:\n", 1)[1].split(
        "\nPrinted characteristics:", 1
    )[0]
    if not oracle.strip() or "\nPrinted characteristics:" not in source:
        return "", ""
    return source.split("\n", 1)[0][6:], oracle


def narrow_unverified_findings(
    original: list[str],
    corrected: list[str],
    sources: list[str],
    receipt: dict[str, Any],
    protected_parts: set[int] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Application-owned fallback, never a model rewrite or a supported verdict.

    Only canonical discovery rows with the renderer's separator can be narrowed.
    Unknown/missing judgments cannot leave affirmative model strategy behind.
    Measurements and protected claims remain untouched.
    """
    from sabermetrics.assistant_claim_review import claim_units

    protected = set(protected_parts or set()) | {
        i
        for i, part in enumerate(original)
        if re.search(
            r"\b(?:measured|experiment|goldfish|simulation|win.rate)\b|%",
            part,
            re.IGNORECASE,
        )
    }
    from sabermetrics.assistant_claim_fragments import VERSION, matching_judgment

    atomic = receipt.get("claim_unit_version") == VERSION
    # Modern receipts carry the focused source manifest. Stale source content or
    # rebased indices invalidate full claims as well as optional partial facts.
    from sabermetrics.assistant_claim_review import focused_sources

    focused = focused_sources(original, sources)
    sources_bound = "source_sha256" not in receipt or source_manifest_matches(
        receipt, focused
    )
    units = claim_units(original, protected, atomic=atomic)
    judgments = {j["claim_id"]: j for j in receipt.get("claim_judgments", [])}
    narrowed: list[dict[str, Any]] = []
    partial_facts_retained = 0
    unknown_claim_ids: set[str] = set()
    result = corrected[:]
    by_part: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        by_part[unit["part_index"]].append(unit)
    for index, group in by_part.items():
        if index in protected:
            continue
        kept = []
        rejected = []
        for unit in group:
            judgment = judgments.get(unit["claim_id"], {})
            bound = sources_bound and (not atomic or matching_judgment(unit, judgment))
            accepted = (
                bound
                and not judgment.get("unverified_remainder")
                and (
                    judgment.get("verdict") == "supported"
                    or (
                        judgment.get("verdict") == "contradicted"
                        and judgment.get("correction_applied")
                    )
                )
            )
            if accepted:
                text = judgment.get("rendered_claim", unit["claim"])
                # A successful correction is still bound to the actual visible
                # corrected text. Stale/replayed receipts cannot retain a rewrite.
                if text in corrected[index]:
                    kept.append(text)
                    continue
            rejected.append((unit, judgment))
            unknown_claim_ids.add(unit["claim_id"])
            if atomic:
                partial = [
                    span["text"]
                    for span in accepted_subspans(unit, judgment, receipt, focused)
                    if span["text"] in corrected[index]
                ]
                kept.extend(partial)
                partial_facts_retained += len(partial)
        if not rejected:
            if atomic:
                # Reconstruct only exact reviewed fragments. Substring presence
                # cannot authorize appended or interleaved unreviewed prose.
                prefix = (
                    original[index].split(" — ", 1)[0] + " — "
                    if " — " in original[index]
                    else ""
                )
                result[index] = prefix + " ".join(kept)
            continue
        if atomic and kept and " — " in original[index]:
            prefix = original[index].split(" — ", 1)[0]
            preserved_fragments = len(kept)
            _, oracle = _literal_oracle(prefix, sources)
            if oracle and not any(oracle in text for text in kept):
                kept.append(
                    "Printed card text (ability availability unverified): “"
                    + oracle
                    + "”"
                )
            # Keep unresolved prerequisites visible without promoting them to
            # facts or letting favorable prose lose its qualification. A patched
            # condition may be shown only as unverified, with its exact visible
            # text; known-false uncorrected statements are never restored.
            conditional = re.compile(
                r"^(?:needs?|requires?|if|unless|without|depends? on|only works? if)\b|\bmust (?:be|have|remain)\b",
                re.IGNORECASE,
            )
            conditions = []
            for unit, judgment in rejected:
                if not conditional.search(unit["claim"]):
                    continue
                text = judgment.get("rendered_claim", unit["claim"])
                if (
                    atomic
                    and sources_bound
                    and matching_judgment(unit, judgment)
                    and (
                        judgment.get("verdict") in {"unverified", "supported"}
                        or judgment.get("correction_applied")
                    )
                    and isinstance(text, str)
                    and len(text) <= 500
                    and text in corrected[index]
                ):
                    conditions.append(text)
            # Missing conditions can be phrased arbitrarily. Qualification
            # must not depend on the wording recognizer used for optional quotes.
            qualification = (
                "Fit assessment remains conditional; some claims remain unverified. "
            )
            unknowns = " ".join(
                "Unverified prerequisite statement: “" + text + "”"
                for text in conditions[:2]
            )
            result[index] = (
                prefix
                + " — "
                + qualification
                + " ".join(kept)
                + (" " + unknowns if unknowns else "")
                + " Further claims remain unverified."
            )
            narrowed.extend(
                {
                    "claim_id": u["claim_id"],
                    "part_index": index,
                    "method": "preserve_reviewed_fragments",
                    "reason": j.get("reason") or "No bound evidence judgment.",
                    "preserved_fragments": preserved_fragments,
                }
                for u, j in rejected
            )
            continue
        unit, judgment = rejected[0]
        if " — " not in original[index]:
            continue
        prefix = original[index].split(" — ", 1)[0]
        name, oracle = _literal_oracle(prefix, sources)
        if name:
            source = next(s for s in sources if s.split("\n", 1)[0] == "Card: " + name)
            cost = next(
                (
                    line[11:]
                    for line in source.splitlines()
                    if line.startswith("Mana cost: ")
                ),
                "unknown",
            )
            prefix = name + " (" + cost + ")"
        note = (
            "Setup, repeatability and strategic fit remain unverified for this request."
        )
        if oracle:
            note = "Printed text: “" + oracle + "” " + note
        replacement = prefix + " — Fit assessment remains conditional. " + note
        result[index] = replacement
        narrowed.append(
            {
                "claim_id": unit["claim_id"],
                "part_index": index,
                "card_name": name,
                "method": "literal_facts_and_explicit_unknowns",
                "reason": judgment.get("reason")
                or receipt.get("reason")
                or "Missing or incomplete evidence judgment.",
            }
        )
    if narrowed:
        receipt = {
            **receipt,
            "status": "unverified",
            "fulfillment": receipt.get("fulfillment", "partial"),
            "narrowed_claims": narrowed,
            "partial_facts_retained": partial_facts_retained,
            "partial_facts_unknown": len(unknown_claim_ids),
        }
    return result, receipt
