"""Bounded optional view-index loading and production shortlist integration."""

from __future__ import annotations

import heapq
import json
import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from sabermetrics.assistant_card_views import build_card_views, retrieve_card_views
from sabermetrics.assistant_view_index import ViewQuerySession

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_VIEW_NOMINATIONS = 24


@lru_cache(maxsize=1)
def _load(path: str, modified: int, size: int) -> dict[str, Any]:
    if size > MAX_ARTIFACT_BYTES:
        raise ValueError("View index exceeds bounded artifact size")
    raw = Path(path).read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("View index exceeds bounded artifact size")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("records"), list)
        or len(value["records"]) > 10000
        or not all(isinstance(row, dict) for row in value["records"])
    ):
        raise ValueError("View index is malformed or exceeds record budget")
    return value


def _index_path(db_path: str | Path | None = None) -> str:
    explicit = os.environ.get("SABER_ASSISTANT_CARD_VIEW_VECTORS", "")
    if explicit:
        return explicit
    if db_path is not None:
        adjacent = Path(db_path).parent / "assistant-card-view-vectors.sqlite"
        if adjacent.is_file():
            return str(adjacent)
        adjacent = Path(db_path).parent / "assistant-card-view-vectors.json"
        if adjacent.is_file():
            return str(adjacent)
    return ""


def enrich_shortlist(
    cards: list[dict[str, Any]], query: str, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    artifact = None
    availability = "not_configured"
    configured = _index_path(db_path)
    if configured:
        try:
            p = Path(configured)
            stat = p.stat()
            artifact = (
                str(p)
                if _sqlite(p)
                else _load(str(p.resolve()), stat.st_mtime_ns, stat.st_size)
            )
            availability = "loaded"
        except (OSError, ValueError, TypeError):
            availability = "unavailable"
    if not cards or not query.strip():
        return {"status": "no_candidates", "artifact_status": availability}
    session = None
    try:
        if artifact:
            session = ViewQuerySession(artifact, query[:320])
        result = retrieve_card_views(cards[:64], query[:320], vector_session=session)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error):
        availability = "unavailable"
        result = retrieve_card_views(cards[:64], query[:320])
    finally:
        if session is not None:
            session.close()
    by_oracle = {c.get("oracle_id"): c for c in cards if c.get("oracle_id")}
    for row in result["results"]:
        views = row["card_views"]
        source = views["views"]["printed_effect"]["facts"]
        card = by_oracle.get(source.get("oracle_id"))
        if card is not None:
            card["retrieval_views"] = {
                "version": views["version"],
                "source_hash": views["source_hash"],
                "mode": result["mode"],
                "matched_channels": row["matched_channels"],
                "view_cosines": row["view_cosines"],
                "functional_roles": views["views"]["functional_role"]["roles"],
                "inference_scope": "Generated roles and prerequisite labels are discovery signals; original Oracle evidence remains authoritative.",
            }
    return {
        "version": result["version"],
        "mode": result["mode"],
        "artifact_status": availability,
        "matched_cards": len(result["results"]),
        "omitted_matches": result["omitted_matches"],
        "limitations": result["limitations"],
        "scope": "Additional views over the current eligible shortlist, not full-catalog recall or a strategic quality score.",
    }


def recall_view_scores(
    cards: dict[str, dict[str, Any]], query: str, *, db_path: str | Path | None = None
) -> tuple[dict[str, float], dict[str, Any]]:
    """Semantic view recall BEFORE shortlist bounds, over already eligible IDs.

    The artifact nominates identities only; current catalog facts and every view
    hash are validated by ViewQuerySession before scores can influence recall.
    """
    configured = _index_path(db_path)
    receipt: dict[str, Any] = {
        "status": "not_configured",
        "eligible_cards": len(cards),
        "indexed_eligible_cards": 0,
        "scored_cards": 0,
        "maximum_nominations": MAX_VIEW_NOMINATIONS,
        "ranking_method": "Top positive view cosines across the complete eligible indexed pool; no calibrated relevance or quality probability.",
        "scope": "Optional semantic discovery prior; exact current eligibility and source/view/model hashes still apply.",
    }
    if not configured:
        return {}, receipt
    try:
        p = Path(configured)
        stat = p.stat()
        artifact = (
            str(p)
            if _sqlite(p)
            else _load(str(p.resolve()), stat.st_mtime_ns, stat.st_size)
        )
        if isinstance(artifact, dict) and not any(
            row.get("oracle_id") for row in artifact["records"]
        ):
            return {}, {**receipt, "status": "legacy_index_requires_rebuild"}
        with_session = ViewQuerySession(artifact, query[:320])
        try:
            receipt["generation"] = with_session.meta.get("generation")
            receipt["refresh_success_at"] = with_session.meta.get("refresh_success_at")
            receipt["source"] = with_session.meta.get("source", {})

            def scored():
                for oid, card in cards.items():
                    if str(card.get("oracle_id") or "") != str(oid):
                        continue
                    views = build_card_views(card)
                    similarities = with_session.similarities(views)
                    if similarities:
                        receipt["indexed_eligible_cards"] += 1
                    score = max(similarities.values(), default=0)
                    if score > 0:
                        receipt["positive_scored_cards"] += 1
                        yield str(oid), score

            receipt["positive_scored_cards"] = 0
            # nsmallest retains only 24 rows while visiting the complete pool.
            scores = dict(
                heapq.nsmallest(
                    MAX_VIEW_NOMINATIONS, scored(), key=lambda item: (-item[1], item[0])
                )
            )
            receipt.update(
                status="available" if scores else "no_compatible_semantic_matches",
                scored_cards=len(scores),
                omitted_nominations=max(
                    0, receipt["positive_scored_cards"] - len(scores)
                ),
            )
        finally:
            with_session.close()
        return scores, receipt
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error):
        return {}, {**receipt, "status": "unavailable"}


def _sqlite(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"
