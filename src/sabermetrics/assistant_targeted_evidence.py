"""Retrieve local counterevidence for finalists within a fixed lookup budget."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from sabermetrics.assistant_rules import retrieve_rules


def finalist_evidence(
    db_path: Path,
    cards: list[dict[str, Any]],
    existing: dict[str, Any],
    support_cards: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """At most two local lookups, three finalists, six retained distinct hits.

    Look for requirements and failure modes, never generate rules or infer that
    a matching rule establishes an interaction. No network or model call.
    """
    started = time.monotonic()
    finalists = cards[:3]
    text = "\n".join(str(c.get("oracle_text") or "") for c in finalists).lower()
    queries = []
    for pattern, query in (
        (
            r"sacrifice|\bdies\b|return.*graveyard|return that card",
            "sacrifice dies return graveyard zone change new object delayed triggered ability token",
        ),
        (
            r"counter|proliferate",
            "remove counters cost putting counters restrictions proliferate",
        ),
        (r"\{t\}|\bhaste\b", "tap symbol activated ability summoning sickness haste"),
        (
            r"only once|once each turn|only as a sorcery",
            "activation restriction only once each turn sorcery timing",
        ),
    ):
        if re.search(pattern, text):
            queries.append(query)
    merged = dict(existing)
    fresh = []
    receipts = []
    scoped = [*finalists, *(support_cards or [])][:16]
    for query in queries[:2]:
        result = retrieve_rules(
            db_path, query, cards=scoped, limit=3, deadline_seconds=0.35
        ).model_dump()
        fresh.extend(result["hits"])
        receipts.append(
            {
                "query": query,
                "status": result["status"],
                "rulings_status": result["rulings_status"],
                "limitations": result["limitations"],
            }
        )
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for hit in [*fresh, *existing.get("hits", [])]:
        key = (hit.get("kind"), hit.get("source_id"), hit.get("content_sha256"))
        # Prefer full new evidence over the previous compact/excerpt receipt.
        if key not in unique or (
            unique[key].get("text_truncated") and not hit.get("text_truncated")
        ):
            unique[key] = hit
    merged["hits"] = list(unique.values())[:6]
    merged["hits_omitted"] = existing.get("hits_omitted", 0) + max(0, len(unique) - 6)
    if merged["hits"]:
        merged["status"] = "available"
    merged["targeted_lookups"] = receipts
    receipt = {
        "stage": "finalist_evidence",
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "lookup_count": len(receipts),
        "finalist_count": len(finalists),
        "finalists_omitted": max(0, len(cards) - 3),
        "hits_retained": len(merged["hits"]),
        "queries": receipts,
        "queries_omitted": max(0, len(queries) - 2),
        "sources": [
            {
                key: hit.get(key)
                for key in (
                    "kind",
                    "source_id",
                    "content_sha256",
                    "source_url",
                    "version",
                    "text_truncated",
                )
            }
            for hit in merged["hits"]
        ],
        "scope": "Bounded local evidence retrieval only. Missing sources remain unverified; source matching does not establish rules correctness.",
    }
    return merged, receipt
