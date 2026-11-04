"""One bounded local retrieval pass over exact atomic review receipts.

No model calls, edits, semantic proof, or rereview. The caller may append returned
strings to its source table and recheck only affected_claim_ids. A matching record
is candidate evidence, never a resolved claim. All gaps remain evidence scoped.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sabermetrics.assistant_rules import WIZARDS_HOSTS, retrieve_rules

VERSION = "assistant-corrective-retrieval.v1"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _bound_claim(judgment: dict[str, Any], parts: list[str]) -> str:
    index, start, end = (judgment.get(k) for k in ("part_index", "start", "end"))
    if type(index) is not int or type(start) is not int or type(end) is not int:
        return ""
    if not 0 <= index < len(parts) or not 0 <= start < end <= len(parts[index]):
        return ""
    claim = parts[index][start:end]
    if (
        judgment.get("reviewed_claim") != claim
        or judgment.get("part_sha256") != _hash(parts[index])
        or judgment.get("claim_sha256") != _hash(claim)
    ):
        return ""
    return claim


def _official(hit: dict[str, Any]) -> bool:
    parsed = urlsplit(str(hit.get("source_url") or ""))
    wizards = parsed.scheme == "https" and parsed.hostname in WIZARDS_HOSTS
    if hit.get("kind") == "rule":
        return wizards
    if hit.get("kind") == "card_ruling":
        # Scryfall is a carrier; only its Wizards-origin records qualify.
        return wizards or "original source labeled 'wotc' by Scryfall" in str(
            hit.get("source") or ""
        )
    return False


def corrective_evidence(
    db_path: Path,
    receipt: dict[str, Any],
    parts: list[str],
    existing_sources: list[str],
    cards: list[dict[str, Any]],
    document: Any,
    *,
    check_current: Callable[[], Any] | None = None,
    seen_queries: Any = (),
    max_queries: int = 2,
) -> dict[str, Any]:
    """Return append-only evidence and unresolved gaps; at most two lookups.

    ``check_current`` is called before/after retrieval and before return; its
    exceptions propagate. Inputs are never mutated. ``document`` is accepted for
    runtime contract compatibility; deck facts cannot be repaired by rule search.
    ``seen_queries`` contains previous query strings (case/whitespace insensitive).
    Complete records are bounded by the underlying local retriever (six hits per
    lookup); truncated records are omitted rather than silently made complete.
    """
    started = time.monotonic()
    guard = check_current or (lambda: None)
    guard()
    budget = min(2, max(0, int(max_queries)))
    seen = {" ".join(str(q).lower().split()) for q in seen_queries}
    sources: list[str] = []
    gaps: list[dict[str, Any]] = []
    lookups: list[dict[str, Any]] = []
    affected: list[str] = []
    # Existing source records can have different headers around the same body.
    evidence_hashes = {_hash(s) for s in existing_sources}
    for source in existing_sources:
        evidence_hashes.update(re.findall(r"\b[a-f0-9]{64}\b", source))
        if "\n" in source:
            evidence_hashes.add(_hash(source.split("\n", 1)[1]))
    results: dict[str, list[str]] = {}
    appended: dict[str, str] = {}
    source_indices: dict[str, int] = {}
    indices_by_claim: dict[str, list[int]] = {}
    source_diagnostics: list[dict[str, Any]] = []
    for judgment in receipt.get("claim_judgments", []):
        if judgment.get("verdict") not in {
            "unknown",
            "unverified",
            "conflict",
            "contradicted",
        }:
            continue
        if judgment.get("correction_applied"):
            continue
        guard()
        claim = _bound_claim(judgment, parts)
        reason = str(judgment.get("reason") or "")
        descriptor = judgment.get("retrieval_gap")
        descriptor = descriptor if isinstance(descriptor, dict) else {}
        proposition = descriptor.get("missing_proposition")
        proposition = proposition.strip() if isinstance(proposition, str) else ""
        category = judgment.get("category")
        gap: dict[str, Any] = {
            "claim_id": judgment.get("claim_id"),
            "missing_proposition": proposition,
            "kind": descriptor.get("kind"),
            "evidence_type": (
                "official_rule_or_ruling"
                if category == "rules_interaction"
                else str(category or "unknown")
            ),
            "target": [],
            "resolvable": False,
            "reason": reason,
            "status": "stale_receipt" if not claim else "not_locally_resolvable",
        }
        gaps.append(gap)
        if (
            not claim
            or receipt.get("claim_unit_version") != "assistant-claim-fragments.v1"
        ):
            gap["status"] = "stale_receipt"
            continue
        if not descriptor.get("kind") or not proposition or len(proposition) > 180:
            gap["status"] = "unspecified_review_gap"
            continue
        if judgment.get("verdict") == "unknown":
            gap["status"] = "unknown_review_verdict"
            continue
        if descriptor.get("kind") != "missing_rule":
            continue
        if category != "rules_interaction":
            continue
        scoped = [
            c
            for c in cards
            if c.get("name")
            and re.search(
                r"(?<!\w)" + re.escape(str(c["name"]).lower()) + r"(?!\w)",
                parts[judgment["part_index"]].lower(),
            )
        ]
        gap["target"] = [c.get("oracle_id") or c["name"] for c in scoped]
        gap["targets_omitted"] = max(0, len(scoped) - 16)
        gap["resolvable"] = True  # retrievable candidate evidence, not proof
        # The identified dependency gets the budget before surrounding prose.
        # A long claim must not truncate away the very rule we need to find.
        query = " ".join((proposition + " " + claim).split())[:300]
        key = query.lower()
        gap["query"] = query
        if key in results:
            gap["status"] = (
                "candidate_evidence" if results[key] else "no_new_complete_evidence"
            )
            if results[key] and judgment["claim_id"] not in affected:
                affected.append(judgment["claim_id"])
                indices_by_claim[judgment["claim_id"]] = sorted(
                    {source_indices[source] for source in results[key]}
                )
            continue
        if key in seen:
            gap["status"] = "query_already_seen"
            continue
        if len(lookups) >= budget:
            gap["status"] = "lookup_budget_omitted"
            continue
        seen.add(key)
        guard()
        result = retrieve_rules(
            db_path, query, cards=scoped[:16], limit=6, deadline_seconds=0.35
        ).model_dump()
        guard()
        fresh = []
        relevant = []
        omitted = {"incomplete_or_unofficial": 0, "repeated_evidence": 0}
        for hit in result["hits"]:
            text = str(hit.get("text") or "")
            digest = _hash(text)
            if (
                not text
                or hit.get("text_truncated")
                or digest != hit.get("content_sha256")
                or not _official(hit)
            ):
                omitted["incomplete_or_unofficial"] += 1
                continue
            if digest in appended:
                omitted["repeated_evidence"] += 1
                relevant.append(appended[digest])
                continue
            if digest in evidence_hashes or any(text in s for s in existing_sources):
                omitted["repeated_evidence"] += 1
                continue
            evidence_hashes.add(digest)
            card_id = hit.get("card_oracle_id")
            card_name = (
                next(
                    (
                        str(c.get("name"))
                        for c in cards
                        if c.get("oracle_id") == card_id
                    ),
                    "",
                )
                if card_id
                else ""
            )
            binding = f" | card {card_name} ({card_id})" if card_id else ""
            source = f"Rule source: {hit['source_id']} | {hit.get('version')} | {hit.get('source_url')}{binding} | {hit.get('source')} | sha256 {digest}\n{text}"
            index = len(existing_sources) + len(sources)
            source_indices[source] = index
            source_diagnostics.append(
                {
                    "source_index": index,
                    "source_id": hit["source_id"],
                    "kind": hit.get("kind"),
                    "version": hit.get("version"),
                    "source_url": hit.get("source_url"),
                    "card_oracle_id": card_id,
                    "content_sha256": digest,
                    "source_sha256": _hash(source),
                }
            )
            sources.append(source)
            fresh.append(source)
            relevant.append(source)
            appended[digest] = source
        results[key] = relevant
        lookups.append(
            {
                "query": query,
                "status": result["status"],
                "rulings_status": result["rulings_status"],
                "limitations": result["limitations"],
                "new_complete_records": len(fresh),
                "relevant_new_records": len(set(relevant)),
                "new_source_indices": [source_indices[source] for source in fresh],
                "candidate_source_indices": sorted(
                    {source_indices[source] for source in relevant}
                ),
                "omitted": omitted,
            }
        )
        gap["status"] = "candidate_evidence" if relevant else "no_new_complete_evidence"
        if relevant and judgment["claim_id"] not in affected:
            affected.append(judgment["claim_id"])
            indices_by_claim[judgment["claim_id"]] = sorted(
                {source_indices[source] for source in relevant}
            )
    judged_ids = {g["claim_id"] for g in gaps}
    for claim_id in receipt.get("missing_claim_ids", []):
        if claim_id not in judged_ids:
            gaps.append(
                {
                    "claim_id": claim_id,
                    "missing_proposition": "",
                    "evidence_type": "unknown",
                    "target": [],
                    "resolvable": False,
                    "reason": "No atomic judgment supplied; no exact proposition bound to evidence.",
                    "status": "missing_judgment",
                }
            )
            judged_ids.add(claim_id)
    guard()
    return {
        "additional_sources": sources,
        "affected_claim_ids": affected,
        "new_source_indices_by_claim": indices_by_claim,
        "lookup_sources": source_diagnostics,
        "gaps": gaps,
        "lookups": lookups,
        "stop_reason": (
            "new_complete_evidence"
            if sources
            else (
                "lookup_budget"
                if any(g["status"] == "lookup_budget_omitted" for g in gaps)
                else "no_new_complete_evidence" if lookups else "no_resolvable_gaps"
            )
        ),
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
