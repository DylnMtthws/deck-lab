"""Bounded source-cited answer cross-check through the existing costed gateway.

One call, <=3072 output tokens; expected incremental cost < $0.01. This is a
model judgment, not a rules proof. Exact citation/span validation stays in code.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from sabermetrics.assistant_tools import StrictModel
from sabermetrics.cedh.errors import ModelProviderError, ModelValidationError
from sabermetrics.cedh.model_gateway import ReasoningMode, StructuredRequest
from sabermetrics.deck_documents import _is_public_library_zone
from sabermetrics.errors import LLMCostCeilingExceeded


class AnswerPatchRejected(ValueError):
    """Controlled diagnostic; never includes provider payloads or credentials."""


class Citation(StrictModel):
    source_index: int = Field(ge=0)
    quote: str = Field(min_length=12, max_length=1200)


class AnswerPatch(StrictModel):
    part_index: int = Field(ge=0, le=31)
    claim: str = Field(min_length=1, max_length=1500)
    replacement: str = Field(min_length=1, max_length=1800)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class AnswerReview(StrictModel):
    fulfillment: Literal["complete", "partial", "nonanswer", "unknown"] = "unknown"
    patches: list[AnswerPatch] = Field(default_factory=list, max_length=6)
    unresolved: list[str] = Field(default_factory=list, max_length=3)


class AnswerReviewDecision(AnswerReview):
    fulfillment: Literal["complete", "partial", "nonanswer", "unknown"] = Field(
        description="Whether the draft actually answers the current request: complete, partial, nonanswer (only plans/progress/deferral/caveats), or unknown if supplied evidence cannot establish fulfillment. This is separate from mechanical correctness."
    )


class SourceEvidence(list[str]):
    """List-compatible sources with explicit disclosure of selection omissions."""

    def __init__(self) -> None:
        super().__init__()
        self.omitted: list[str] = []
        self.card_names: list[str] = []


def whole_deck_evidence_sources(
    base: list[str], cards: list[dict[str, Any]]
) -> SourceEvidence:
    """Complete global source coverage with shared metadata labels.

    Original records/provenance remain local. No Oracle text is clipped. Any
    later wire omission is disclosed by review_answer and stays unverified.
    """
    result = SourceEvidence()
    restored_names = {str(c.get("name")) for c in cards}
    result.omitted = [
        label for label in getattr(base, "omitted", []) if label not in restored_names
    ]
    fields = (
        "id",
        "mana_cost",
        "type_line",
        "cmc",
        "mana_value",
        "power",
        "toughness",
        "loyalty",
        "defense",
        "color_identity",
        "layout",
        "keywords",
    )
    provenance: list[dict[str, Any]] = []
    result.append(
        "Complete card sources below. Printed fields column order: "
        + json.dumps(fields)
        + "; null means unknown. Oracle text and face scopes are complete. Deck membership does not establish battlefield availability or strategic suitability."
    )
    for card in cards:
        prov = {
            k: v
            for k, v in (card.get("provenance") or {}).items()
            if k not in {"printing_id", "catalog_id", "catalog_differences"}
        }
        if prov not in provenance:
            provenance.append(prov)
        pieces = [
            "Card: " + _card_name(card),
            "Printed fields: "
            + json.dumps(
                [card.get(k) for k in fields], ensure_ascii=False, separators=(",", ":")
            ),
            "Oracle text:\n" + str(card.get("oracle_text") or ""),
        ]
        for face in card.get("card_faces") or []:
            pieces.extend(
                [
                    "Face: " + str(face.get("name") or "unknown"),
                    "Printed fields: "
                    + json.dumps(
                        [face.get(k) for k in fields],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "Oracle text:\n" + str(face.get("oracle_text") or ""),
                    "Face colors: " + json.dumps(face.get("colors")),
                ]
            )
        pieces.append("Provenance ref: " + str(provenance.index(prov)))
        result.append("\n".join(pieces))
        result.card_names.append(_card_name(card))
    # Replace duplicate card renderings; retain rules, facts and omissions.
    covered = {"Card: " + _card_name(c) for c in cards}
    result.extend(s for s in base if s.split("\n", 1)[0] not in covered)
    result.append(
        "Shared source provenance: " + json.dumps(provenance, ensure_ascii=False)
    )
    return result


def _normalized_name_text(text: str, *, preserve_case: bool = False) -> str:
    """Normalize typography without converting words into card-name matches."""
    text = unicodedata.normalize("NFKC", text)
    if not preserve_case:
        text = text.casefold()
    text = text.translate(str.maketrans("’‘ʼ–—‑", "'''---"))
    return " ".join(text.split())


def _mentions_card_name(text: str, name: str) -> bool:
    """Match whole names or a known face alias, never a substring of a word."""
    normalized = _normalized_name_text(text)
    aliases = [
        alias
        for alias in re.split(
            r"\s*//\s*", _normalized_name_text(name, preserve_case=True)
        )
        if alias
    ]
    full_name = r"\s*//\s*".join(re.escape(alias.casefold()) for alias in aliases)
    if full_name and re.search(r"(?<!\w)" + full_name + r"(?!\w)", normalized):
        return True
    # Single-word split faces (Life, Death, Return, etc.) are ordinary prose too.
    # Require their canonical capitalization unless the complete card is named.
    raw = _normalized_name_text(text, preserve_case=True)
    return any(
        re.search(
            r"(?<!\w)"
            + re.escape(alias if len(alias.split()) == 1 else alias.casefold())
            + r"(?!\w)",
            raw if len(alias.split()) == 1 else normalized,
        )
        for alias in aliases
    )


def _card_name(card: dict[str, Any]) -> str:
    """Retain canonical and face names as aliases of one supplied card."""
    names = [str(card.get("name") or "")]
    names.extend(
        str(face.get("name") or "")
        for face in (card.get("card_faces") or [])
        if isinstance(face, dict)
    )
    aliases: dict[str, str] = {}
    for name in names:
        for alias in re.split(r"\s*//\s*", name):
            if alias.strip():
                aliases.setdefault(_normalized_name_text(alias), alias.strip())
    return " // ".join(aliases.values())


def _card_source(card: dict[str, Any]) -> str:
    return (
        "Card: "
        + (_card_name(card) or "unknown")
        + "\nMana cost: "
        + str(card.get("mana_cost") or "unknown")
        + "\nType: "
        + str(card.get("type_line") or "unknown")
        + "\nOracle text"
        + (" (excerpt; omissions unknown)" if card.get("oracle_text_truncated") else "")
        + ":\n"
        + str(card.get("oracle_text") or "unavailable")
        + "\nPrinted characteristics: "
        + json.dumps(
            {
                key: card.get(key)
                for key in (
                    "cmc",
                    "mana_value",
                    "power",
                    "toughness",
                    "loyalty",
                    "defense",
                    "color_identity",
                )
            },
            ensure_ascii=False,
        )
        + (
            "\nCard faces (printed fields): "
            + "\n".join(
                "Face: "
                + str(face.get("name") or "unknown")
                + "\nMana cost: "
                + str(face.get("mana_cost") or "unknown")
                + "\nType: "
                + str(face.get("type_line") or "unknown")
                + "\nOracle text:\n"
                + str(face.get("oracle_text") or "unavailable")
                + "\nPrinted characteristics: "
                + json.dumps(
                    {
                        key: face.get(key)
                        for key in (
                            "power",
                            "toughness",
                            "loyalty",
                            "defense",
                            "colors",
                        )
                    },
                    ensure_ascii=False,
                )
                for face in card["card_faces"]
                if isinstance(face, dict)
            )
            if card.get("card_faces")
            else ""
        )
        + "\nSource identity/provenance: "
        + json.dumps(
            {
                key: card.get(key)
                for key in ("id", "oracle_id", "provenance")
                if card.get(key)
            },
            ensure_ascii=False,
        )
    )


def resolve_review_cards(
    db_path: str | Path,
    document: dict[str, Any],
    parts: list[str],
    known_cards: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Refresh discussed identities locally, without an LLM/tool round trip.

    Candidate-ledger rows preserve identities even when synthesis context or
    prior tool receipts were compacted. Do not look up model-invented names.
    """
    from sabermetrics.assistant_card_facts import card_facts

    text = " ".join(parts)
    selected: dict[str, dict[str, Any]] = {}
    for card in [*document.get("entries", []), *known_cards, *(candidate_rows or [])]:
        if not card.get("is_commander") and not _mentions_card_name(
            text, _card_name(card)
        ):
            continue
        cid = card.get("card_id") or card.get("id")
        if not cid or cid in selected:
            continue
        selected[cid] = card
    refreshed = []
    for cid, previous in selected.items():
        data = card_facts(db_path, cid)
        card = data.get("card")
        if not card:
            refreshed.append(previous)
            continue
        # Preserve deck metadata separately; source text comes from the local
        # versioned fact reader, whose provenance remains attached for review.
        refreshed.append(
            {
                **previous,
                **card,
                "provenance": data.get("provenance"),
                "oracle_text_truncated": bool(
                    any(
                        (data.get("truncation") or {}).get(k, 0)
                        for k in ("characters", "items")
                    )
                ),
            }
        )
    return refreshed


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
        return _mentions_card_name(answer, _card_name(card))

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

    # Commander facts are mandatory even when the draft uses a short name.
    for card in document.get("entries", []):
        if card.get("is_commander") or named(card):
            cards.append(card)
    # A locally refreshed complete record takes precedence over receipt excerpts.
    cards = [*(additional_cards or []), *cards]
    for action in actions:
        if action.get("type") != "tool_result" or not action.get("ok"):
            continue
        data = action.get("data") or {}
        if action.get("name") == "card_details" and data.get("card"):
            cards.append(data["card"])
        if action.get("name") in {"search_cards", "search_card_functions"}:
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
        if document.get("validation") is not None:
            candidates.append(
                (
                    "Current deck validation",
                    "Computed current deck validation at revision "
                    + str(document.get("revision", "unknown"))
                    + ": "
                    + json.dumps(document["validation"], ensure_ascii=False),
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
    # Preserve priority rules/cost comparisons while preventing unrelated card
    # paragraphs from displacing the commander's text.
    # Deduplicate identities, preferring untruncated records with actual text.
    preferred: dict[str, dict[str, Any]] = {}
    for card in cards:
        key = _normalized_name_text(_card_name(card)) or str(card.get("oracle_id"))
        old = preferred.get(key)
        quality = lambda c: (
            not bool(c.get("oracle_text_truncated")),
            bool(c.get("oracle_text")),
            bool(c.get("provenance")),
        )
        if old is None or quality(card) > quality(old):
            preferred[key] = card
    cards = list(preferred.values())
    candidates = [
        *candidates[:1],
        *(
            (str(card.get("name") or "Card"), _card_source(card))
            for card in cards
            if card.get("is_commander")
        ),
        *candidates[1:],
        *(
            (str(card.get("name") or "Card"), _card_source(card))
            for card in cards
            if not card.get("is_commander")
        ),
    ]
    # Types are separate bounded sources: long type lines cannot displace the
    # deck's cost comparison as one oversized combined table.
    if cards:
        type_rows: list[str] = []
        for card in document.get("entries", []):
            if not card.get("is_commander") and not _is_public_library_zone(
                card.get("zone_name")
            ):
                continue
            row = (
                str(card.get("name") or "")
                + ": "
                + str(card.get("type_line") or "unknown")
            )
            if sum(len(text) + 1 for text in type_rows) + len(row) > 3000 and type_rows:
                candidates.append(
                    (
                        "Current deck printed types (part)",
                        "Current deck printed types:\n" + "\n".join(type_rows),
                    )
                )
                type_rows = []
            type_rows.append(row)
        if type_rows:
            candidates.append(
                (
                    "Current deck printed types (part)",
                    "Current deck printed types:\n" + "\n".join(type_rows),
                )
            )
    result = SourceEvidence()
    for card in cards:
        if card.get("oracle_text_truncated") or not card.get("oracle_text"):
            # Basic lands may legitimately have empty Oracle text. A named
            # card without text still cannot establish an ability claim.
            result.omitted.append(
                str(card.get("name") or "Card") + " complete Oracle text"
            )
    result.card_names = [
        _card_name(c) for c in [*document.get("entries", []), *cards] if c.get("name")
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
            raise AnswerPatchRejected("Unknown reply part")
        if patch.part_index in (protected_parts or set()):
            raise AnswerPatchRejected(
                "Measurement-derived reply parts cannot be rewritten"
            )
        text = parts[patch.part_index]
        if text.count(patch.claim) != 1:
            raise AnswerPatchRejected("Claim is absent or ambiguous")
        for citation in patch.citations:
            if (
                citation.source_index >= len(sources)
                or citation.quote not in sources[citation.source_index]
            ):
                raise AnswerPatchRejected("Citation is not in supplied evidence")
        allowed = patch.claim + " " + " ".join(c.quote for c in patch.citations)
        token_pattern = r"\{[^{}]+\}|\b\d+(?:[.,]\d+)*\b|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|x)\b"
        tokens = re.findall(token_pattern, patch.replacement, re.IGNORECASE)
        allowed_tokens = {
            token.casefold()
            for token in re.findall(token_pattern, allowed, re.IGNORECASE)
        }
        if any(token.casefold() not in allowed_tokens for token in tokens):
            raise AnswerPatchRejected(
                "Correction introduces an unsupported number or mana symbol"
            )
        names = [
            source.split("\n", 1)[0][6:]
            for source in sources
            if source.startswith("Card: ")
        ]
        names.extend(known_names or [])
        if any(
            _mentions_card_name(patch.replacement, name)
            and not _mentions_card_name(allowed, name)
            for name in names
        ):
            raise AnswerPatchRejected(
                "Correction introduces a card name outside its cited evidence"
            )
        start = text.index(patch.claim)
        end = start + len(patch.claim)
        used = spans.setdefault(patch.part_index, [])
        if any(
            start < previous_end and end > previous_start
            for previous_start, previous_end, _ in used
        ):
            raise AnswerPatchRejected("Overlapping answer corrections")
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
    research_context: dict[str, Any] | None = None,
    full_deck_sources: bool = False,
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
            "research_context": research_context or {},
        },
        ensure_ascii=False,
    )
    omitted_sources = 0
    payload_limit = 48000 if full_deck_sources else 32768
    while len(payload.encode()) > payload_limit and sources:
        sources.pop()
        omitted_sources += 1
        payload = json.dumps(
            {
                "request": user_request[:4000],
                "reply_parts": parts,
                "sources": sources,
                "protected_part_indices": sorted(protected_parts),
                "research_context": research_context or {},
            },
            ensure_ascii=False,
        )
    if len(payload.encode()) > payload_limit or not sources:
        return parts, {"status": "unverified", "reason": "review_context_limit"}
    request = StructuredRequest(
        prompt_version="assistant-answer-review.v7",
        system=(
            Path(__file__).parent / "prompts/assistant_answer_review.txt"
        ).read_text(),
        user=payload,
        schema=AnswerReviewDecision,
        mode=ReasoningMode.EXPLAIN,
        call_type="build:answer-review",
        max_output_tokens=3072,
        evidence_hash=hashlib.sha256(payload.encode()).hexdigest(),
    )
    review: AnswerReview | None = None
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
        receipt: dict[str, Any] = {"status": "unverified", "reason": type(exc).__name__}
        if review is not None:
            receipt["fulfillment"] = review.fulfillment
            receipt["unresolved"] = review.unresolved
        if isinstance(exc, AnswerPatchRejected):
            receipt["reason_detail"] = str(exc)
        return parts, receipt
    return corrected, {
        "fulfillment": review.fulfillment,
        "status": (
            "unverified"
            if review.unresolved or initially_omitted or omitted_sources
            else "corrected" if review.patches else "no_supported_correction"
        ),
        "patches": [p.model_dump() for p in review.patches],
        "unresolved": review.unresolved,
        "omitted_sources": len(initially_omitted) + omitted_sources,
        "omitted_source_labels": initially_omitted[:24],
        "evidence_categories": {
            "printed_facts": "Supplied literal Oracle records; omissions disclosed.",
            "deck_facts": "Computed current document composition and supplied validation.",
            "rules_interactions": "Only supported by supplied rules; otherwise unverified.",
            "strategic_judgments": "Heuristic fit, not printed fact or measured improvement.",
        },
        "source_sha256": [hashlib.sha256(s.encode()).hexdigest() for s in sources],
        "context_budget_bytes": payload_limit,
        "scope": "Bounded model cross-check against supplied sources; not an independent rules proof.",
    }
