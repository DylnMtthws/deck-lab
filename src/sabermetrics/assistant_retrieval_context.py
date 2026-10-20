"""Bounded optional view-index loading and production shortlist integration."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from sabermetrics.assistant_card_views import retrieve_card_views

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
    ):
        raise ValueError("View index is malformed or exceeds record budget")
    return value


def _index_path(db_path: str | Path | None = None) -> str:
    explicit = os.environ.get("SABER_ASSISTANT_CARD_VIEW_VECTORS", "")
    if explicit:
        return explicit
    if db_path is not None:
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
            artifact = _load(str(p.resolve()), stat.st_mtime_ns, stat.st_size)
            availability = "loaded"
        except (OSError, ValueError, TypeError):
            availability = "unavailable"
    if not cards or not query.strip():
        return {"status": "no_candidates", "artifact_status": availability}
    result = retrieve_card_views(cards[:64], query[:320], vector_artifact=artifact)
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
    hash are validated by retrieve_card_views before scores can influence recall.
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
        artifact = _load(str(p.resolve()), stat.st_mtime_ns, stat.st_size)
        ids = {
            str(row["oracle_id"])
            for row in artifact["records"]
            if isinstance(row, dict) and row.get("oracle_id")
        }
        if not ids:
            return {}, {**receipt, "status": "legacy_index_requires_rebuild"}
        selected = [card for oid, card in cards.items() if oid in ids]
        receipt["indexed_eligible_cards"] = len(selected)
        scores: dict[str, float] = {}
        for offset in range(0, len(selected), 64):
            found = retrieve_card_views(
                selected[offset : offset + 64],
                query[:320],
                limit=8,
                vector_artifact=artifact,
                all_matches=True,
            )
            for row in found["results"]:
                oid = str(
                    row["card_views"]["views"]["printed_effect"]["facts"].get(
                        "oracle_id"
                    )
                    or ""
                )
                score = max(row["view_cosines"].values(), default=0)
                if score > 0:
                    scores[oid] = max(scores.get(oid, 0), score)
        compatible = len(scores)
        scores = dict(
            sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
                :MAX_VIEW_NOMINATIONS
            ]
        )
        receipt.update(
            status="available" if scores else "no_compatible_semantic_matches",
            scored_cards=len(scores),
            positive_scored_cards=compatible,
            omitted_nominations=max(0, compatible - len(scores)),
        )
        return scores, receipt
    except (OSError, ValueError, TypeError, KeyError):
        return {}, {**receipt, "status": "unavailable"}
