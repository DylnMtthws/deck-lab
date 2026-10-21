"""Retrieve local counterevidence for finalists within a fixed lookup budget."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from sabermetrics.assistant_rules import retrieve_rules

_RECURSION = (
    r"\brecur\w*|\breanimat\w*|"
    r"\breturn\w*\b[^.!?;]*(?:battlefield|graveyard|hand|counter)|"
    r"\b(?:it|creature|permanent|card) returns?\b|\bcomes? back\b"
)
_RECURSION_QUERY = "sacrifice dies return graveyard zone change new object delayed triggered ability token"
_RENEWAL_QUERY = "zone change new object counters retained return counter renewal"
_TAP_QUERY = "tap symbol activated ability summoning sickness haste"
_RESTRICTION_QUERY = "activation restriction only once each turn sorcery timing"
_SACRIFICE_QUERY = "sacrifice cost dies death triggered ability token"


def _counter_query(text: str) -> str:
    # Energy belongs to a player; it is not a +1/+1 counter on a permanent.
    # These are retrieval terms, never a conclusion about an interaction.
    if re.search(r"\benergy\b|\{e\}", text) and "+1/+1" not in text:
        return "energy counters player pay energy cost proliferate"
    if "+1/+1" in text and not re.search(r"\benergy\b|\{e\}", text):
        return (
            "+1/+1 counters permanent remove counters cost putting counters proliferate"
        )
    return "remove counters cost putting counters restrictions proliferate"


def _queries(text: str, claims: list[str]) -> list[str]:
    # Only rendered assertions can request recursion/renewal. In particular,
    # 'repeat' plus an unasserted support card's return ability is insufficient.
    fragments = [
        part for claim in claims for part in re.split(r"[.!?;\n]", claim.lower())
    ]
    queries = []
    for fragment in fragments:
        recursion = bool(re.search(_RECURSION, fragment))
        counters = bool(
            re.search(r"\bcounters?\b|\bproliferat\w*|\benergy\b|\{e\}", fragment)
        )
        object_counters = counters and (
            "+1/+1" in fragment or not re.search(r"\benergy\b|\{e\}", fragment)
        )
        if object_counters and (
            recursion
            or re.search(r"\bretain\w*|\brenew\w*|\breplenish\w*|\bkeep\w*", fragment)
        ):
            queries.append(_RENEWAL_QUERY)
        if recursion:
            queries.append(_RECURSION_QUERY)
        if counters:
            queries.append(_counter_query(fragment))
        if re.search(r"\{t\}|\b(?:un)?tap\w*|\bhaste\b|summoning sickness", fragment):
            queries.append(_TAP_QUERY)
        if re.search(r"only once|once each turn|only as a sorcery", fragment):
            queries.append(_RESTRICTION_QUERY)
        if not recursion and re.search(r"\bsacrific\w*|\bdies\b", fragment):
            queries.append(_SACRIFICE_QUERY)
    # Printed finalist mechanics fill spare slots after claim-specific needs.
    # Support cards remain retrieval scope, not additional assertions.
    if re.search(r"\bcounters?\b|proliferate|\benergy\b|\{e\}", text):
        queries.append(_counter_query(text))
    if re.search(r"\{t\}|\bhaste\b", text):
        queries.append(_TAP_QUERY)
    if re.search(r"only once|once each turn|only as a sorcery", text):
        queries.append(_RESTRICTION_QUERY)
    return list(dict.fromkeys(queries))


def finalist_evidence(
    db_path: Path,
    cards: list[dict[str, Any]],
    existing: dict[str, Any],
    support_cards: list[dict[str, Any]] | None = None,
    claims: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """At most two local lookups, three finalists, six retained distinct hits.

    Look for requirements and failure modes, never generate rules or infer that
    a matching rule establishes an interaction. No network or model call.
    """
    started = time.monotonic()
    finalists = cards[:3]
    text = "\n".join(str(c.get("oracle_text") or "") for c in finalists).lower()
    queries = _queries(text, claims or [])
    merged = dict(existing)
    fresh = []
    receipts = []
    scoped = [*finalists, *(support_cards or [])][:16]
    for query in queries[:2]:
        result = retrieve_rules(
            db_path,
            query,
            cards=scoped,
            limit=4 if "zone change" in query else 3,
            deadline_seconds=0.35,
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
