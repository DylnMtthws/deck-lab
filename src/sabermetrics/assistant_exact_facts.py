"""Conservative literal printed-cost checks. No provider or semantic inference.

A match certifies only the cost printed before one Oracle ability's colon. It
does not establish a payable/net cost, draw role, repeatability or strength.
Unrecognized or ambiguous records abstain and remain for semantic review.
"""

from __future__ import annotations

import re

from sabermetrics.assistant_claim_fragments import VERSION, text_hash
from sabermetrics.assistant_claim_review import claim_units

METHOD = "EXACT_source_check"
_SYMBOLS = r"(?:\{(?:[0-9]+|[WUBRGCSXYZT]|[WUBRGC]/[WUBRGCP]|2/[WUBRG])\})+"
_COST = rf"(?:{_SYMBOLS}|\{{T\}}, Pay six \{{E\}})"
_CLAIM = re.compile(rf"Activation cost: (?P<cost>{_COST})\.")


def _ability(source: str, name: str, cost: str) -> str | None:
    if source.split("\n", 1)[0] != "Card: " + name:
        return None
    # Do not treat face text, excerpts, quoted/granted abilities or incomplete
    # records as the card's own complete activated ability.
    if re.search(
        r"//|\b(?:face|faces|excerpt|truncat\w*|omission\w*)\b|…|\.\.\.",
        source,
        re.IGNORECASE,
    ):
        return None
    if source.count("\nOracle text:\n") != 1:
        return None
    oracle = source.split("\nOracle text:\n", 1)[1]
    oracle = re.split(
        r"\n(?:Printed characteristics:|Source identity/provenance:)", oracle, 1
    )[0]
    if re.search(r'["“”]', oracle):
        return None
    lines = [line for line in oracle.splitlines() if line.strip()]
    abilities = [line for line in lines if ":" in line]
    if len(abilities) != 1:
        return None
    line = abilities[0]
    # A cost under a level/class/keyword/static grant is not necessarily an
    # unconditional ability of this card. Admit only an isolated activated
    # ability, optionally alongside ordinary complete triggered paragraphs
    # (including Roil Cartographer's Landfall), and abstain on other layouts.
    if any(
        item != line and not re.fullmatch(r"(?:Landfall — )?Whenever .+\.", item)
        for item in lines
    ) or re.search(r"\b(?:gains?|has|have|following)\b", oracle, re.IGNORECASE):
        return None
    if (
        len(line) < 12
        or len(line) > 1200
        or line.count(":") != 1
        or not line.endswith(".")
        or line.split(":", 1)[0] != cost
        or not line.startswith(cost + ": ")
        or re.search(
            r"\b(?:if|unless|only|during|gains?|has|have)\b", line, re.IGNORECASE
        )
        or any(not re.match(r"[A-Z{(]", item) for item in lines)
    ):
        return None
    return line


def exact_cost_facts(parts, sources, protected_parts=None):
    """Return a receipt for matches only, bound to original parts and sources.

    ``sources`` must be the same focused source basis used by the semantic
    reviewer. Nothing is rewritten and abstentions do not produce judgments.
    """
    judgments = []
    selected = set()
    for unit in claim_units(parts, set(protected_parts or ())):
        match = _CLAIM.fullmatch(unit["claim"])
        if unit["protected"] or not match:
            continue
        part = parts[unit["part_index"]]
        if " — " not in part:
            continue
        prefix = part.split(" — ", 1)[0]
        candidates = []
        for index, source in enumerate(sources):
            if not source.startswith("Card: "):
                continue
            name = source.split("\n", 1)[0][6:]
            if re.fullmatch(
                r"(?:[^:\n]+: )?"
                + re.escape(name)
                + r" \((?:(?:\{[^}\n]+\})*|mana cost not retrieved)\)",
                prefix,
            ):
                candidates.append((index, source, name))
        if len(candidates) != 1:
            continue
        index, source, name = candidates[0]
        line = _ability(source, name, match["cost"])
        if line is None:
            continue
        selected.add(index)
        judgments.append(
            {
                **{
                    key: unit[key]
                    for key in (
                        "claim_id",
                        "part_index",
                        "start",
                        "end",
                        "claim_sha256",
                        "part_sha256",
                    )
                },
                "category": "printed_fact",
                "verdict": "supported",
                "reason": "Exact complete printed activation cost; no playability inference.",
                "replacement": None,
                "correction_span": None,
                "unverified_remainder": False,
                "invalid_evidence_ids": [],
                "citations": [{"source_index": index, "quote": line}],
                "reviewed_claim": unit["claim"],
                "rendered_claim": unit["claim"],
                "correction_applied": False,
                "method": METHOD,
                "source_sha256": text_hash(source),
                "source_index": index,
            }
        )
    return list(parts), {
        "status": "no_supported_correction",
        "method": METHOD,
        "claim_unit_version": VERSION,
        "fulfillment": "unknown",
        "reviewed_parts": {
            str(j["part_index"]): parts[j["part_index"]] for j in judgments
        },
        "claim_judgments": judgments,
        "claim_count": len(judgments),
        "missing_claim_ids": [],
        "patches": [],
        "unresolved": [],
        "source_sha256": [text_hash(s) for s in sources],
        "evidence_indices": sorted(selected),
        "evidence_bytes_sent": 0,
        "scope": "Literal printed activation cost only; no payable/net cost, role, repeatability or strength judgment.",
    }
