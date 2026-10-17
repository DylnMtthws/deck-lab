"""Bounded source-cited answer cross-check through the existing costed gateway.

One call, <=3072 output tokens; expected incremental cost < $0.01. This is a
model judgment, not a rules proof. Exact citation/span validation stays in code.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from sabermetrics.assistant_tools import StrictModel
from sabermetrics.cedh.errors import ModelProviderError, ModelValidationError
from sabermetrics.cedh.model_gateway import ReasoningMode, StructuredRequest
from sabermetrics.deck_documents import _is_public_library_zone
from sabermetrics.errors import LLMCostCeilingExceeded


class Citation(StrictModel):
    source_index: int = Field(ge=0)
    quote: str = Field(min_length=12, max_length=1200)


class AnswerPatch(StrictModel):
    part_index: int = Field(ge=0, le=31)
    claim: str = Field(min_length=1, max_length=1500)
    replacement: str = Field(min_length=1, max_length=1800)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class AnswerReview(StrictModel):
    patches: list[AnswerPatch] = Field(default_factory=list, max_length=6)
    unresolved: list[str] = Field(default_factory=list, max_length=3)


class SourceEvidence(list[str]):
    """List-compatible sources with explicit disclosure of selection omissions."""

    def __init__(self) -> None:
        super().__init__()
        self.omitted: list[str] = []
        self.card_names: list[str] = []


def _card_source(card: dict[str, Any]) -> str:
    return (
        "Card: "
        + str(card.get("name") or "unknown")
        + "\nMana cost: "
        + str(card.get("mana_cost") or "unknown")
        + "\nType: "
        + str(card.get("type_line") or "unknown")
        + "\nOracle text"
        + (" (excerpt; omissions unknown)" if card.get("oracle_text_truncated") else "")
        + ":\n"
        + str(card.get("oracle_text") or "unavailable")
    )


def evidence_sources(
    document: dict[str, Any],
    actions: list[dict[str, Any]],
    parts: list[str],
    rules: dict[str, Any] | None = None,
    additional_cards: list[dict[str, Any]] | None = None,
) -> SourceEvidence:
    """Prefer relevant rules and deck comparisons; preserve literal Oracle text."""
    answer = " ".join(parts).casefold()
    cards = []
    candidates: list[tuple[str, str]] = []

    def named(card):
        return any(
            name.strip().casefold() in answer
            for name in str(card.get("name") or "").split(" // ")
            if name.strip()
        )

    def rule_hits(data):
        for hit in data.get("hits", [])[:6]:
            if hit.get("text"):
                label = str(hit.get("source_id") or "Retrieved rule")
                candidates.append(
                    (
                        label,
                        "Rule source: "
                        + label
                        + (" (excerpt)" if hit.get("text_truncated") else "")
                        + "\n"
                        + str(hit["text"]),
                    )
                )

    for card in [*document.get("entries", []), *(additional_cards or [])]:
        if named(card):
            cards.append(card)
    for action in actions:
        if action.get("type") != "tool_result" or not action.get("ok"):
            continue
        data = action.get("data") or {}
        if action.get("name") == "card_details" and data.get("card"):
            cards.append(data["card"])
        if action.get("name") == "search_cards":
            cards.extend(card for card in data.get("cards", []) if named(card))
        if action.get("name") == "retrieve_rules":
            rule_hits(data)
        if action.get("name") in {"find_replacements", "prepare_improvement"}:

            def collect(value, depth=0):
                if depth > 5:
                    return
                if isinstance(value, dict):
                    if value.get("name") and value.get("oracle_text") and named(value):
                        cards.append(value)
                    for key in (
                        "incoming_oracle_facts",
                        "outgoing_oracle_facts",
                        "replacement_pairs",
                        "incoming",
                        "outgoing",
                    ):
                        if key in value:
                            collect(value[key], depth + 1)
                elif isinstance(value, list):
                    for row in value[:8]:
                        collect(row, depth + 1)

            collect(data.get("candidate_evidence", []))
    if cards or candidates:
        rule_hits(rules or {})
        candidates.append(
            (
                "Current deck printed mana costs",
                "Current deck printed mana costs (not alternate or effective costs):\n"
                + "\n".join(
                    str(c.get("name") or "")
                    + ": "
                    + str(c.get("mana_cost") or "unknown")
                    for c in document.get("entries", [])
                    if c.get("is_commander")
                    or _is_public_library_zone(c.get("zone_name"))
                ),
            )
        )
        from sabermetrics.assistant_deck_facts import library_composition

        candidates.append(
            (
                "Computed current deck composition",
                "Computed current deck composition: "
                + json.dumps(library_composition(document)),
            )
        )
    candidates.extend(
        (str(card.get("name") or "Card"), _card_source(card)) for card in cards
    )
    result = SourceEvidence()
    result.card_names = [
        str(c["name"]) for c in [*document.get("entries", []), *cards] if c.get("name")
    ]
    total = 0
    for label, source in candidates:
        if source in result:
            continue
        if len(source) > 6000 or total + len(source) > 18000 or len(result) >= 24:
            result.omitted.append(label)
            continue
        result.append(source)
        total += len(source)
    return result


def apply_patches(
    parts: list[str],
    sources: list[str],
    review: AnswerReview,
    protected_parts: set[int] | None = None,
    known_names: list[str] | None = None,
) -> list[str]:
    """Reject the entire patch set if a span, quote, source or overlap is invalid."""
    spans: dict[int, list[tuple[int, int, str]]] = {}
    for patch in review.patches:
        if patch.part_index >= len(parts):
            raise ValueError("Unknown reply part")
        if patch.part_index in (protected_parts or set()):
            raise ValueError("Measurement-derived reply parts cannot be rewritten")
        text = parts[patch.part_index]
        if text.count(patch.claim) != 1:
            raise ValueError("Claim is absent or ambiguous")
        for citation in patch.citations:
            if (
                citation.source_index >= len(sources)
                or citation.quote not in sources[citation.source_index]
            ):
                raise ValueError("Citation is not in supplied evidence")
        allowed = patch.claim + " " + " ".join(c.quote for c in patch.citations)
        token_pattern = r"\{[^{}]+\}|\b\d+(?:[.,]\d+)*\b|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|x)\b"
        tokens = re.findall(token_pattern, patch.replacement, re.IGNORECASE)
        allowed_tokens = {
            token.casefold()
            for token in re.findall(token_pattern, allowed, re.IGNORECASE)
        }
        if any(token.casefold() not in allowed_tokens for token in tokens):
            raise ValueError(
                "Correction introduces an unsupported number or mana symbol"
            )
        names = [
            source.split("\n", 1)[0][6:]
            for source in sources
            if source.startswith("Card: ")
        ]
        names.extend(known_names or [])
        if any(
            name.casefold() in patch.replacement.casefold()
            and name.casefold() not in allowed.casefold()
            for name in names
        ):
            raise ValueError(
                "Correction introduces a card name outside its cited evidence"
            )
        start = text.index(patch.claim)
        end = start + len(patch.claim)
        used = spans.setdefault(patch.part_index, [])
        if any(
            start < previous_end and end > previous_start
            for previous_start, previous_end, _ in used
        ):
            raise ValueError("Overlapping answer corrections")
        used.append((start, end, patch.replacement))
    corrected = parts[:]
    for index, patches in spans.items():
        for start, end, replacement in sorted(patches, reverse=True):
            corrected[index] = (
                corrected[index][:start] + replacement + corrected[index][end:]
            )
    return corrected


def review_answer(
    gateway,
    parts: list[str],
    sources: list[str],
    user_request: str,
    *,
    protected_parts: set[int] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    if not sources:
        return parts, {"status": "not_checked", "reason": "no_source_evidence"}
    initially_omitted = getattr(sources, "omitted", [])[:]
    known_names = getattr(sources, "card_names", [])[:]
    sources = sources[:]
    protected_parts = (protected_parts or set()) | {
        i
        for i, part in enumerate(parts)
        if re.search(
            r"\b(?:measured|experiment|goldfish|simulation|improved|win.rate)\b|%",
            part,
            re.IGNORECASE,
        )
    }
    payload = json.dumps(
        {
            "request": user_request[:4000],
            "reply_parts": parts,
            "sources": sources,
            "protected_part_indices": sorted(protected_parts),
        },
        ensure_ascii=False,
    )
    omitted_sources = 0
    while len(payload.encode()) > 32768 and sources:
        sources.pop()
        omitted_sources += 1
        payload = json.dumps(
            {
                "request": user_request[:4000],
                "reply_parts": parts,
                "sources": sources,
                "protected_part_indices": sorted(protected_parts),
            },
            ensure_ascii=False,
        )
    if len(payload.encode()) > 32768 or not sources:
        return parts, {"status": "unverified", "reason": "review_context_limit"}
    request = StructuredRequest(
        prompt_version="assistant-answer-review.v1",
        system=(
            Path(__file__).parent / "prompts/assistant_answer_review.txt"
        ).read_text(),
        user=payload,
        schema=AnswerReview,
        mode=ReasoningMode.EXPLAIN,
        call_type="build:answer-review",
        max_output_tokens=3072,
        evidence_hash=hashlib.sha256(payload.encode()).hexdigest(),
    )
    try:
        generated = gateway.generate(request).value
        review = AnswerReview.model_validate(
            generated.model_dump() if hasattr(generated, "model_dump") else generated
        )
        corrected = apply_patches(parts, sources, review, protected_parts, known_names)
    except LLMCostCeilingExceeded:
        return parts, {"status": "not_checked", "reason": "cost_ceiling"}
    except (
        ModelProviderError,
        ModelValidationError,
        ValidationError,
        ValueError,
    ) as exc:
        return parts, {"status": "unverified", "reason": type(exc).__name__}
    return corrected, {
        "status": (
            "partially_checked"
            if initially_omitted or omitted_sources
            else "corrected" if review.patches else "no_supported_correction"
        ),
        "patches": [p.model_dump() for p in review.patches],
        "unresolved": review.unresolved,
        "omitted_sources": len(initially_omitted) + omitted_sources,
        "omitted_source_labels": initially_omitted[:24],
        "source_sha256": [hashlib.sha256(s.encode()).hexdigest() for s in sources],
        "scope": "Bounded model cross-check against supplied sources; not an independent rules proof.",
    }
