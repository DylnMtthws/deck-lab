"""Bounded, source-preserving ability decomposition; never a rules parser."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

VERSION = "assistant-ability-clauses.v1"


def _resource_costs(cost: str) -> list[dict[str, Any]]:
    """Literal consumed-resource records, kept separate from effect hand gain."""
    resources = []
    for kind, pattern in (
        ("permanent", r"\bsacrifice [^,:]+"),
        ("hand_card", r"\bdiscard [^,:]+"),
        ("counter", r"\bremove [^,:]+ counters? from [^,:]+"),
        ("life", r"\bpay [^,:]+ life"),
        ("tap", r"\{T\}"),
    ):
        for match in re.finditer(pattern, cost, re.IGNORECASE):
            resources.append(
                {
                    "resource": kind,
                    "source_quote": match[0],
                    "replenishment": "unverified",
                }
            )
    return resources


def card_identity(card: dict[str, Any]) -> str:
    return str(
        card.get("oracle_id")
        or card.get("card_id")
        or card.get("id")
        or card.get("name")
        or ""
    )


@lru_cache(maxsize=4096)
def _line_parts(line: str) -> tuple[
    str,
    str,
    str | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
    tuple[tuple[str, str], ...],
]:
    """Exact-source parse cache containing immutable values only."""
    cost, effect = line.split(":", 1) if ":" in line else ("", line)
    trigger = re.match(
        r"(?:When|Whenever|At the beginning)[^,]*,", effect.strip(), re.IGNORECASE
    )
    restrictions = re.findall(
        r"[^.\n]*(?:\bif\b|\bunless\b|\bonly\b|\bexcept\b|\bwith\b|\bwithout\b)[^.\n]*\.?",
        effect,
        re.IGNORECASE,
    )
    timing = re.findall(
        r"[^.\n]*(?:\bactivate\b|\bonce each turn\b|\byour turn\b|\bend step\b)[^.\n]*\.?",
        effect,
        re.IGNORECASE,
    )
    targets = re.findall(r"\btarget\b[^.,;\n]*", effect, re.IGNORECASE)
    complex_restriction = False
    if re.search(
        r"\bif\b|\bunless\b|\bexcept\b|\bchosen\b|\bwith\b|\bwithout\b|\band/or\b|\bfor each\b",
        line,
        re.IGNORECASE,
    ):
        complex_restriction = True
    return (
        cost,
        effect.strip()[trigger.end() :].strip() if trigger else effect.strip(),
        trigger[0] if trigger else None,
        tuple(restrictions),
        tuple(timing),
        tuple(targets),
        complex_restriction,
        tuple((r["resource"], r["source_quote"]) for r in _resource_costs(cost)),
    )


def ability_clauses(card: dict[str, Any]) -> dict[str, Any]:
    raw = str(card.get("oracle_text") or "")
    unknowns = []
    if not raw or card.get("oracle_text_truncated"):
        unknowns.append("missing_or_incomplete_text")
    if (
        card.get("card_faces")
        or "//" in str(card.get("type_line") or "")
        or card.get("layout")
        in {"transform", "modal_dfc", "split", "adventure", "flip", "meld"}
    ):
        unknowns.append("multiface")
    if "•" in raw:
        unknowns.append("modal")
    rows = []
    for i, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        (
            cost,
            effect,
            trigger,
            restrictions,
            timing,
            targets,
            complex_restriction,
            resources,
        ) = _line_parts(line)
        row_unknowns = list(unknowns)
        if complex_restriction:
            row_unknowns.append("complex_restriction")
        # Timing is preserved but never discharged by a matching ingredient.
        rows.append(
            {
                "clause_id": f"{card_identity(card)}:ability:{i}",
                "source_quote": line,
                "kind": (
                    "activated"
                    if cost
                    else "triggered" if trigger else "static_or_spell"
                ),
                "costs": [cost.strip()] if cost else [],
                "consumed_resources": [
                    {
                        "resource": kind,
                        "source_quote": quote,
                        "replenishment": "unverified",
                    }
                    for kind, quote in resources
                ],
                "triggers": [trigger] if trigger else [],
                "effects": [effect],
                "targets": list(targets),
                "restrictions": list(restrictions),
                "timing": list(timing),
                "coverage": "unknown" if row_unknowns else "bounded_decomposition",
                "unknowns": row_unknowns,
            }
        )
    return {
        "version": VERSION,
        "oracle_id": card.get("oracle_id"),
        "casting_cost": card.get("mana_cost"),
        "clauses": rows,
        "coverage": "unknown" if unknowns else "partial",
        "unknowns": unknowns,
        "scope": "Literal clause boundaries only; effects, targets, timing and legality are not executed.",
    }


def bounded_compatibility(
    card: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep legacy demands, but abstain on incomplete or complex source clauses."""
    from sabermetrics.assistant_compatibility import ability_compatibility

    clauses = ability_clauses(card)
    parsed_entries = [(c, ability_clauses(c)) for c in entries]
    safe_parsed = [
        (c, parsed) for c, parsed in parsed_entries if parsed["coverage"] != "unknown"
    ]
    safe = [c for c, _ in safe_parsed]
    result = ability_compatibility(card, safe)
    candidate_rows = {r["source_quote"]: r for r in clauses["clauses"]}
    provider_rows = {card_identity(c): parsed for c, parsed in safe_parsed}
    for requirement in result["requirements"]:
        candidate = candidate_rows.get(requirement["candidate_quote"], {})
        if clauses["coverage"] == "unknown" or candidate.get("coverage") == "unknown":
            requirement["providers"] = []
            requirement["status"] = "unknown"
            requirement["coverage_unknowns"] = candidate.get(
                "unknowns", clauses["unknowns"]
            )
            continue
        kept = []
        for provider in requirement["providers"]:
            parsed = provider_rows.get(
                str(provider.get("oracle_id") or provider.get("name"))
            )
            # Providers without Oracle IDs are still resolved by literal source/name.
            if parsed is None:
                parsed = next(
                    (
                        parsed
                        for c, parsed in safe_parsed
                        if c.get("name") == provider.get("name")
                    ),
                    None,
                )
            if parsed and any(
                r["source_quote"] == provider["source_quote"]
                and r["coverage"] != "unknown"
                for r in parsed["clauses"]
            ):
                kept.append(provider)
        requirement["providers"] = kept
        requirement["status"] = "compatible_printed_ingredient" if kept else "unknown"
    return result
