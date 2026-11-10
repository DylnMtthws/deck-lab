"""Fail-closed early publication of source-rendered, request-relevant evidence.

Only trusted runtime tool receipts enter this module. Text supplied by a model
cannot become evidence by labeling itself a receipt. These findings do not grant
mutation authority or strategic approval.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field

from sabermetrics.assistant_obligations import AnswerObligations, PolicyModel

MAX_FINDINGS = 6
MAX_TEXT_BYTES = 8 * 1024


class PublishedSource(PolicyModel):
    """Captured source identity and its explicitly limited freshness."""

    source_id: str
    source_kind: Literal[
        "card_printed_text", "rule", "card_ruling", "catalog_candidate"
    ]
    title: str
    relevant_excerpt: str
    source_url: str | None = None
    source_version: str | None = None
    content_hash: str
    truncated: bool = False
    freshness: Literal["not_verified_current"] = "not_verified_current"


class PublishableFinding(PolicyModel):
    """Deterministic text, never generated strategic advice."""

    finding_id: str
    version: int = 1
    type: Literal["printed_fact", "rule_excerpt", "unranked_candidate"]
    text: str
    card_ids: list[str] = Field(default_factory=list)
    sources: list[PublishedSource]
    review_state: Literal["source_rendered", "retrieved_unranked"]
    deck_revision: int
    request_hash: str
    limitations: list[str]


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme == "https"
            and parts.hostname
            and not parts.username
            and not parts.password
        ):
            return value
    except ValueError:
        pass
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _bounded(value: str) -> bool:
    return len(value.encode()) <= MAX_TEXT_BYTES


def _subject_match(name: str, request: str) -> bool:
    # Whole printed identity, not token overlap or strategic inference.
    return (
        re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", request, re.IGNORECASE)
        is not None
    )


def early_findings(
    tool_name: str,
    data: dict[str, Any],
    obligations: AnswerObligations | dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
) -> list[PublishableFinding]:
    """Render eligible exact tool evidence; all other receipts publish nothing.

    Card details require an explicitly named printed identity. Rule and
    functional-search queries must exactly match the request or a validated
    discovery query. This intentionally abstains on ambiguous relevance.
    """
    obligations = AnswerObligations.model_validate(obligations)
    if not isinstance(data, dict):
        return []
    arguments = arguments or {}
    result: list[PublishableFinding] = []

    def finding(
        kind: Any, text: str, source: PublishedSource, card_id: str | None = None
    ) -> None:
        if not _bounded(text) or not _bounded(source.relevant_excerpt):
            return
        result.append(
            PublishableFinding(
                finding_id=_hash(
                    [
                        obligations.request_hash,
                        source.source_id,
                        source.content_hash,
                        kind,
                    ]
                )[:32],
                type=kind,
                text=text,
                card_ids=[card_id] if card_id else [],
                sources=[source],
                review_state=(
                    "retrieved_unranked"
                    if kind == "unranked_candidate"
                    else "source_rendered"
                ),
                deck_revision=obligations.deck_revision,
                request_hash=obligations.request_hash,
                limitations=[
                    "Captured source is not verified current.",
                    "Retrieved text is not strategic approval or a complete rules interpretation.",
                    *(
                        [
                            "Excerpt truncated; omitted text may contain relevant conditions."
                        ]
                        if source.truncated
                        else []
                    ),
                ],
            )
        )

    if tool_name == "card_details":
        card, provenance = data.get("card"), data.get("provenance")
        if not isinstance(card, dict) or not isinstance(provenance, dict):
            return []
        if data.get("status") not in {"canonical_snapshot", "catalog_only"}:
            return []
        truncation = data.get("truncation") or {}
        if (
            not isinstance(truncation, dict)
            or any(truncation.get(k) for k in ("characters", "items"))
            or card.get("oracle_text_truncated")
            or bool(card.get("card_faces"))
        ):
            return []
        name, identity, oracle = (
            _text(card.get("name")),
            _text(card.get("oracle_id") or card.get("id")),
            _text(card.get("oracle_text")),
        )
        source_kind = provenance.get("source")
        if (
            not name
            or not identity
            or not oracle
            or source_kind not in {"scryfall_snapshot", "local_catalog"}
        ):
            return []
        if not _subject_match(name, obligations.request):
            return []
        printed = {
            k: card.get(k)
            for k in (
                "id",
                "oracle_id",
                "name",
                "mana_cost",
                "type_line",
                "oracle_text",
                "card_faces",
            )
        }
        # Source bytes are copied literally; no generated interpretation or prices.
        source = PublishedSource(
            source_id=f"{source_kind}:{identity}",
            source_kind="card_printed_text",
            title=name,
            relevant_excerpt=oracle,
            source_url=_safe_url(card.get("scryfall_uri")),
            source_version=_text(provenance.get("version")),
            content_hash=_hash(printed),
        )
        finding("printed_fact", f"{name} — printed text:\n{oracle}", source, identity)
        return result

    query = data.get("query") or arguments.get("query")
    if tool_name == "search_card_functions":
        effective = data.get("effective_search")
        if not isinstance(effective, dict):
            return []
        query = effective.get("query")
    allowed = {obligations.request.strip(), *obligations.discovery_queries}
    if not isinstance(query, str) or query.strip() not in allowed:
        return []
    if tool_name == "retrieve_rules":
        if data.get("status") != "available":
            return []
        hits = data.get("hits")
        if not isinstance(hits, list):
            return []
        for hit in hits[:MAX_FINDINGS]:
            if not isinstance(hit, dict):
                continue
            text, identity, digest, version = (
                _text(hit.get(k))
                for k in ("text", "source_id", "content_sha256", "version")
            )
            if not all((text, identity, digest, version)) or hit.get("kind") not in {
                "rule",
                "card_ruling",
            }:
                continue
            assert text is not None and identity is not None and digest is not None
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            source = PublishedSource(
                source_id=identity,
                source_kind=hit["kind"],
                title=identity,
                relevant_excerpt=text,
                source_url=_safe_url(hit.get("source_url")),
                source_version=version,
                content_hash=digest,
                truncated=hit.get("text_truncated") is True,
            )
            finding("rule_excerpt", f"Retrieved excerpt ({identity}):\n{text}", source)
    elif tool_name in {"search_cards", "search_card_functions"}:
        expected_source = (
            "Imported Commander-legal card catalog"
            if tool_name == "search_cards"
            else "Shared Commander-legal local catalog; function-first union recall"
        )
        if data.get("source") != expected_source or not obligations.discovery_queries:
            return []
        if data.get("revision", obligations.deck_revision) != obligations.deck_revision:
            return []
        cards = data.get("cards")
        if not isinstance(cards, list):
            return []
        for card in cards[:MAX_FINDINGS]:
            if not isinstance(card, dict):
                continue
            name, identity = _text(card.get("name")), _text(
                card.get("oracle_id") or card.get("id")
            )
            if not name or not identity:
                continue
            source = PublishedSource(
                source_id="local_catalog:" + identity,
                source_kind="catalog_candidate",
                title=name,
                relevant_excerpt=name,
                content_hash=_hash(
                    {k: card.get(k) for k in ("id", "oracle_id", "name")}
                ),
            )
            finding(
                "unranked_candidate",
                "Retrieved candidate — unranked, eligibility/fit not yet reviewed: "
                + name,
                source,
                identity,
            )
    return result
