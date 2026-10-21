"""Exact review spans with shared context; splitting is never verification."""

from __future__ import annotations

import hashlib
import re
from typing import Any

VERSION = "assistant-claim-fragments.v1"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fragment_units(
    parts: list[str], protected: set[int], *, atomic: bool = True
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for index, part in enumerate(parts[1:], 1):
        if not part.strip():
            continue
        # Only application-rendered discovery rows have a known context prefix.
        body_start = part.index(" — ") + 3 if atomic and " — " in part else 0
        starts = [body_start]
        if atomic and body_start and index not in protected:
            # Keep conditional clauses in their sentence. No comma/semicolon
            # splitting: that could detach a consequence from its antecedent.
            starts += [
                body_start + match.end()
                for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", part[body_start:])
                if not re.match(
                    r"(?:It|This|That|They|These|Those|Then|Therefore|Otherwise)\b",
                    part[body_start + match.end() :],
                )
            ]
        for start, end in zip(starts, [*starts[1:], len(part)]):
            while start < end and part[start].isspace():
                start += 1
            while end > start and part[end - 1].isspace():
                end -= 1
            if end <= start:
                continue
            claim = part[start:end]
            units.append(
                {
                    "claim_id": f"c{len(units)}",
                    "part_index": index,
                    "claim": claim,
                    "protected": index in protected,
                    "start": start,
                    "end": end,
                    "claim_sha256": text_hash(claim),
                    "part_sha256": text_hash(part),
                }
            )
    return units


def matching_judgment(unit: dict[str, Any], judgment: dict[str, Any]) -> bool:
    return all(
        judgment.get(key) == unit[key]
        for key in ("part_index", "start", "end", "claim_sha256", "part_sha256")
    )


def reviewed_sections(
    parts: list[str], corrected: list[str], review: dict[str, Any]
) -> list[str]:
    """Emit only bound checked fragments while remaining gaps are investigated."""
    if review.get("claim_unit_version") != VERSION:
        return []
    judgments = {j["claim_id"]: j for j in review.get("claim_judgments", [])}
    by_part: dict[int, list[str]] = {}
    for unit in fragment_units(parts, set()):
        judgment = judgments.get(unit["claim_id"], {})
        if not matching_judgment(unit, judgment) or judgment.get(
            "unverified_remainder"
        ):
            continue
        if judgment.get("verdict") != "supported" and not (
            judgment.get("verdict") == "contradicted"
            and judgment.get("correction_applied")
        ):
            continue
        index = unit["part_index"]
        text = judgment.get("rendered_claim", "")
        if text and text in corrected[index] and " — " in parts[index]:
            by_part.setdefault(index, []).append(text)
    return [
        parts[i].split(" — ", 1)[0] + " — " + " ".join(texts)
        for i, texts in by_part.items()
    ]


def reviewed_claim_text(
    review: dict[str, Any], judgment: dict[str, Any], parts: list[str]
) -> str:
    """Memory binds to the exact judged span, never an unrelated paragraph."""
    if review.get("claim_unit_version") != VERSION:
        cid = str(judgment.get("claim_id", ""))
        legacy_index = int(cid[1:]) + 1 if re.fullmatch(r"c\d+", cid) else -1
        return parts[legacy_index] if 0 <= legacy_index < len(parts) else ""
    index, start, end = (judgment.get(key) for key in ("part_index", "start", "end"))
    if (
        not isinstance(index, int)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= index < len(parts)
        or not 0 <= start < end <= len(parts[index])
        or judgment.get("part_sha256") != text_hash(parts[index])
    ):
        return ""
    claim = parts[index][start:end]
    return claim if judgment.get("claim_sha256") == text_hash(claim) else ""
