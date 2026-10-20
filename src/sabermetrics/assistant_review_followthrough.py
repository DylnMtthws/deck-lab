"""Preserve literal candidate facts while removing unverified discovery assertions."""

from __future__ import annotations

import re
from typing import Any

from sabermetrics.assistant_answer_review import _mentions_card_name


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
    units = claim_units(original, protected)
    judgments = {j["claim_id"]: j for j in receipt.get("claim_judgments", [])}
    narrowed = []
    result = corrected[:]
    for unit in units:
        index = unit["part_index"]
        judgment = judgments.get(unit["claim_id"], {})
        if unit["protected"] or (
            (
                judgment.get("verdict") == "supported"
                or (
                    judgment.get("verdict") == "contradicted"
                    and judgment.get("correction_applied")
                )
            )
            and not judgment.get("unverified_remainder")
        ):
            continue
        if " — " not in original[index]:
            continue
        prefix = original[index].split(" — ", 1)[0]
        records = [
            s
            for s in sources
            if s.startswith("Card: ")
            and _mentions_card_name(prefix, s.split("\n", 1)[0][6:])
        ]
        if not records:
            continue
        source = max(
            records,
            key=lambda s: prefix.casefold().rfind(s.split("\n", 1)[0][6:].casefold()),
        )
        name = source.split("\n", 1)[0][6:]
        cost = next(
            (
                line[11:]
                for line in source.splitlines()
                if line.startswith("Mana cost: ")
            ),
            "unknown",
        )
        oracle = (
            source.split("\nOracle text:\n", 1)[-1].split(
                "\nPrinted characteristics:", 1
            )[0]
            if "\nOracle text:\n" in source
            else ""
        )
        note = (
            "Setup, repeatability and strategic fit remain unverified for this request."
        )
        if oracle and len(oracle) <= 1000:
            note = "Printed text: " + oracle + " " + note
        replacement = name + " (" + cost + ") — " + note
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
            "fulfillment": "partial",
            "narrowed_claims": narrowed,
        }
    return result, receipt
