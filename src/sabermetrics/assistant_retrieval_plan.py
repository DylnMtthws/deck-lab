"""Deterministic bounded research decomposition, with a local corpus runner.

Tasks are retrieval attention only: they grant no action authority and do not
replace request interpretation, legality, owner, revision or cancellation guards.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

PLAN_VERSION = "assistant-retrieval-plan.v1"


def compile_retrieval_plan(request: str) -> dict[str, Any]:
    """Compile literal cues into at most five tasks; retain ambiguity explicitly."""
    if not isinstance(request, str) or not request.strip() or len(request) > 4000:
        raise ValueError("request must contain 1–4000 characters")
    request = request.strip()
    lowered = request.casefold()
    kinds = ["discovery"]
    comparison = bool(
        re.search(
            r"\b(compare|comparison|versus|vs|better|instead|difference)\b", lowered
        )
    )
    if comparison or re.search(
        r"\b(prerequisites?|setup|costs?|engine|loop|requires?|needs?)\b", lowered
    ):
        kinds.append("prerequisites")
    if comparison or re.search(
        r"\b(drawbacks?|limitations?|weakness|recovery|risks?)\b", lowered
    ):
        kinds.append("drawbacks")
    if re.search(
        r"\b(compare|comparison|versus|vs|better|instead|difference)\b", lowered
    ):
        kinds.append("comparison")
    if re.search(
        r"\b(rules?|legal|trigger|stack|timing|infinite|loop|interaction)\b", lowered
    ):
        kinds.append("rules")
    tasks = [
        {
            "id": f"task-{i + 1}",
            "kind": kind,
            "query": request[:320],
            "focus": kind,
            "evidence_class": "official_rules" if kind == "rules" else "strategy",
            "limit": 2,
        }
        for i, kind in enumerate(kinds)
    ]
    canonical = PLAN_VERSION + "\0" + request
    return {
        "version": PLAN_VERSION,
        "request": request,
        "request_sha256": hashlib.sha256(request.encode()).hexdigest(),
        "plan_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "tasks": tasks,
        "max_tasks": 5,
        "query_truncated": len(request) > 320,
        "limitations": [
            "Literal cue decomposition; ambiguous intent is unresolved.",
            "Retrieval does not certify rules, strategy, measurements or permission to edit.",
        ],
    }


def run_retrieval_plan(
    db_path: Path,
    plan: dict[str, Any],
    *,
    commander: str = "",
    cards: list[dict[str, Any]] | None = None,
    card_view_vectors: dict[str, Any] | None = None,
    check_current: Callable[[], Any] | None = None,
    limit: int = 2,
) -> dict[str, Any]:
    """Run canonical compiled tasks, rechecking caller guards before every read.

    check_current must raise on cancellation/stale revision; exceptions propagate.
    The caller still owns all session, legal, owner and execution budgets.
    """
    from sabermetrics.assistant_strategy_evidence import retrieve_strategy_evidence

    expected = compile_retrieval_plan(plan.get("request", ""))
    if plan != expected:
        raise ValueError("plan must be an unchanged compile_retrieval_plan result")
    if not 1 <= limit <= 4:
        raise ValueError("retrieval limit must be between one and four")
    aspects = {
        "prerequisites": r"\b(?:setup|requires?|prerequisite|fodder|resources?|costs?)\b",
        "drawbacks": r"\b(?:drawbacks?|risks?|limitations?|weakness|fails?|recovery|awkward)\b",
        "comparison": r"\b(?:compare|comparison|tradeoffs?|instead|alternative|versus|substitute)\b",
    }

    def aspect_hits(excerpts, kind):
        pattern = aspects.get(kind)
        if pattern is None:
            return excerpts[:limit]
        scored = [
            (
                len(
                    re.findall(
                        pattern, str(e.get("excerpt") or e.get("text") or "").lower()
                    )
                ),
                i,
                e,
            )
            for i, e in enumerate(excerpts)
        ]
        return [
            e
            for count, _, e in sorted(scored, key=lambda row: (-row[0], row[1]))
            if count
        ][:limit]

    results: list[dict[str, Any]] = []
    cache: dict[str, Any] = {}
    for task in expected["tasks"]:
        if check_current:
            check_current()
        channel = task["evidence_class"]
        if channel not in cache:
            cache[channel] = retrieve_strategy_evidence(
                db_path,
                task["query"],
                commander=commander,
                limit=(
                    4 if channel == "strategy" and len(expected["tasks"]) > 1 else limit
                ),
                source_kinds=(
                    {"official_rules"} if channel == "official_rules" else None
                ),
            )
        shared = cache[channel]
        if channel == "strategy" and not results:
            # Retain aspect-diverse passages from one shared read; do not copy
            # the same generic passage into nominally independent investigations.
            pool = shared.get("excerpts", [])
            retained = []
            for future in expected["tasks"]:
                if future["kind"] in aspects:
                    for hit in aspect_hits(pool, future["kind"])[:1]:
                        if hit not in retained:
                            retained.append(hit)
            for hit in pool:
                if hit not in retained:
                    retained.append(hit)
            shared = {**shared, "excerpts": retained[:limit]}
            cache[channel] = shared
        hits = aspect_hits(shared.get("excerpts", []), task["kind"])
        evidence = {**shared, "excerpts": hits}
        if not hits:
            evidence["status"] = "no_matches"
        results.append(
            {
                "task_id": task["id"],
                "kind": task["kind"],
                "evidence_class": channel,
                "coverage": "retrieved_aspect_passages" if hits else "uncovered",
                "source_indices": [
                    shared.get("excerpts", []).index(hit) for hit in hits
                ],
                "evidence": evidence,
            }
        )
    output = {
        "version": PLAN_VERSION,
        "plan_hash": expected["plan_hash"],
        "results": results,
        "limitations": expected["limitations"],
        "retrieval_reads": len(cache),
        "shared_evidence": cache,
    }
    if cards is not None:
        if check_current:
            check_current()
        from sabermetrics.assistant_card_views import retrieve_card_views

        output["card_views"] = retrieve_card_views(
            cards, expected["request"][:320], vector_artifact=card_view_vectors
        )
    return output
