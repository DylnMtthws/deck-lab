"""Preserve literal candidate facts while removing unverified discovery assertions."""

from __future__ import annotations

import re
from collections import defaultdict
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
    from sabermetrics.assistant_claim_fragments import VERSION, matching_judgment

    atomic = receipt.get("claim_unit_version") == VERSION
    units = claim_units(original, protected, atomic=atomic)
    judgments = {j["claim_id"]: j for j in receipt.get("claim_judgments", [])}
    narrowed: list[dict[str, Any]] = []
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
            bound = not atomic or matching_judgment(unit, judgment)
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
            result[index] = (
                prefix + " — " + " ".join(kept) + " Further claims remain unverified."
            )
            narrowed.extend(
                {
                    "claim_id": u["claim_id"],
                    "part_index": index,
                    "method": "preserve_reviewed_fragments",
                    "reason": j.get("reason") or "No bound evidence judgment.",
                    "preserved_fragments": len(kept),
                }
                for u, j in rejected
            )
            continue
        unit, judgment = rejected[0]
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
            "fulfillment": (
                receipt.get("fulfillment", "unknown")
                if all(
                    n.get("method") == "preserve_reviewed_fragments" for n in narrowed
                )
                else "partial"
            ),
            "narrowed_claims": narrowed,
        }
    return result, receipt
