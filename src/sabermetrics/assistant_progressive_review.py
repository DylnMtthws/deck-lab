"""Publish exact printed-cost facts, then run at most one semantic review.

Callbacks retain original indices and bind to original spans, parts and sources.
Deterministic facts never establish whole-request fulfillment or model usage.
Unknown costs, rules, strategy and comparisons stay in the semantic call.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from sabermetrics.assistant_answer_review import (
    AnswerPatch,
    AnswerReview,
    apply_patches,
)
from sabermetrics.assistant_claim_fragments import VERSION, matching_judgment, text_hash
from sabermetrics.assistant_claim_review import (
    claim_units,
    focused_sources,
    review_claims,
)
from sabermetrics.assistant_claim_subspans import accepted_subspans
from sabermetrics.assistant_exact_facts import exact_cost_facts
from sabermetrics.assistant_provider_budget import (
    budget_gateway,
    check_active,
)

MAX_REVIEW_JOBS = 1
MAX_LOCAL_JOBS = 0


def _merge(parts, receipts, sources, protected):
    patches = [
        AnswerPatch.model_validate(p) for r in receipts for p in r.get("patches", [])
    ]
    # Each input job already passed the <=6-patch schema. Revalidate evidence,
    # exact positions and overlaps across the bounded combined patch set.
    return apply_patches(
        parts,
        sources,
        AnswerReview.model_construct(patches=patches),
        protected,
        list(getattr(sources, "card_names", [])),
    )


def _bound_receipt(
    parts, corrected, receipt, ids, units, sources, protected, evidence_indices
):
    """Reject the entire job on duplicate IDs, stale hashes or source rebasing."""
    judgments = receipt.get("claim_judgments", [])
    if (
        receipt.get("claim_unit_version") != VERSION
        or len(judgments) != len(ids)
        or {j.get("claim_id") for j in judgments} != ids
        or receipt.get("source_sha256") != [text_hash(s) for s in sources]
        or receipt.get("evidence_indices") != sorted(evidence_indices)
        or receipt.get("missing_claim_ids")
    ):
        return False
    expected_parts = {
        str(units[cid]["part_index"]): parts[units[cid]["part_index"]] for cid in ids
    }
    if receipt.get("reviewed_parts") != expected_parts:
        return False
    for judgment in judgments:
        unit = units[judgment["claim_id"]]
        if (
            not matching_judgment(unit, judgment)
            or judgment.get("reviewed_claim") != unit["claim"]
        ):
            return False
        for citation in judgment.get("citations", []):
            index = citation.get("source_index")
            quote = citation.get("quote")
            if (
                not isinstance(index, int)
                or index not in evidence_indices
                or not isinstance(quote, str)
                or not quote
                or quote not in sources[index]
            ):
                return False
        if judgment.get("supported_subspans") and not accepted_subspans(
            unit, judgment, receipt, sources
        ):
            return False
    try:
        patches = [AnswerPatch.model_validate(p) for p in receipt.get("patches", [])]
        if any(
            p.part_index
            not in {u["part_index"] for cid, u in units.items() if cid in ids}
            for p in patches
        ):
            return False
        expected = apply_patches(
            parts,
            sources,
            AnswerReview(patches=patches),
            protected,
            list(getattr(sources, "card_names", [])),
        )
    except (ValueError, TypeError):
        return False
    return corrected == expected


def progressive_review(
    gateway,
    parts: list[str],
    sources: list[str],
    user_request: str,
    *,
    research_context: dict[str, Any] | None = None,
    protected_parts: set[int] | None = None,
    on_reviewed: Callable[[list[str], dict[str, Any]], Any] | None = None,
    check_current: Callable[[], Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    original = list(parts)
    sources = deepcopy(sources)
    context = deepcopy(research_context or {})
    protected = set(protected_parts or ()) | {
        i
        for i, p in enumerate(original)
        if re.search(
            r"\b(?:measured|experiment|goldfish|simulation|win.rate)\b|%",
            p,
            re.IGNORECASE,
        )
    }
    check_active(check_current)
    cancelled = threading.Event()

    def check_review_current():
        check_active(lambda: not cancelled.is_set())
        # Review and generate are synchronous on the caller's thread. Keep its
        # cancellation/deadline check active while queued for admission too;
        # nested gateway wrappers retain their own scoped checks.
        check_active(check_current)

    checker = budget_gateway(gateway, check_current=check_review_current)
    units = {u["claim_id"]: u for u in claim_units(original, protected)}
    focused = focused_sources(original, sources)
    exact_result = exact_cost_facts(original, focused, protected)
    exact_ids = {j["claim_id"] for j in exact_result[1]["claim_judgments"]}
    jobs = []
    accepted: list[dict[str, Any]] = []
    corrected = original[:]

    def consume(result, ids, stage, evidence_indices=None, *, fulfillment_only=False):
        nonlocal corrected
        checked, receipt = result
        check_active(check_current)
        bound = _bound_receipt(
            original,
            checked,
            receipt,
            ids,
            units,
            focused,
            protected,
            set(range(len(focused))) if evidence_indices is None else evidence_indices,
        )
        jobs.append(
            {
                "stage": stage,
                "claim_ids": sorted(ids),
                "bound": bound,
                "status": receipt.get("status", "unverified"),
                "reason": receipt.get("reason"),
                "method": receipt.get("method", "semantic_model_review"),
                **(
                    {"review_model": receipt["review_model"]}
                    if "review_model" in receipt
                    else {}
                ),
                "evidence_indices": receipt.get("evidence_indices", []),
                "evidence_bytes_sent": receipt.get("evidence_bytes_sent", 0),
            }
        )
        if bound:
            try:
                merged = _merge(original, [*accepted, receipt], focused, protected)
            except (ValueError, TypeError):
                bound = False
                jobs[-1]["bound"] = False
                jobs[-1]["reason"] = "patch_merge_rejected"
        if not bound:
            return False
        if fulfillment_only:
            return True
        accepted.append(receipt)
        corrected = merged
        if on_reviewed and any(
            j.get("supported_subspans")
            or (
                not j.get("unverified_remainder")
                and (
                    j.get("verdict") == "supported"
                    or (
                        j.get("verdict") == "contradicted"
                        and j.get("correction_applied")
                    )
                )
            )
            for j in receipt["claim_judgments"]
        ):
            check_active(check_current)
            # Subset fulfillment is never a whole-request publication verdict.
            publication = deepcopy(receipt)
            publication["fulfillment"] = "unknown"
            on_reviewed(list(checked), publication)
        return True

    def run(ids, job_context, evidence_indices=None):
        return review_claims(
            checker,
            original,
            sources,
            user_request,
            claim_ids=ids,
            research_context=job_context,
            protected_parts=protected,
            evidence_indices=evidence_indices,
        )

    global_receipt = None
    try:
        if exact_ids:
            consume(
                exact_result,
                exact_ids,
                "exact",
                set(exact_result[1]["evidence_indices"]),
            )
        check_active(check_current)
        reviewed = {j["claim_id"] for r in accepted for j in r["claim_judgments"]}
        remaining = set(units) - reviewed
        # A semantic call always sees the complete selection, including the
        # application-owned lead/caveats in part zero.
        global_ids = remaining or ({min(units)} if units else set())
        global_context = context
        if original:
            # Compact selection context, never conversation/tool transcripts.
            # Preserve original prose and disclose corrections to the final
            # whole-selection comparison without transferring their verdicts.
            global_part_indices = {units[cid]["part_index"] for cid in global_ids}
            global_context = {
                **context,
                "progressive_selection": {
                    "additional_parent_parts": {
                        str(i): part
                        for i, part in enumerate(original)
                        if i not in global_part_indices
                    },
                    "selection_order": list(range(len(original))),
                    "local_corrections": [
                        p for r in accepted for p in r.get("patches", [])
                    ],
                    "instruction": "Combine parent_parts and additional_parent_parts in selection_order for the entire selection. Assess whole-request fulfillment and joint selection tradeoffs. Judge only supplied claim IDs. Exact checks establish only literal printed costs, never payable/net costs, draw roles, repeatability, strength or fulfillment.",
                },
            }
        # review_claims requires a nonempty unit set. If every claim is exact,
        # recheck one original unit solely to obtain semantic fulfillment; keep
        # its deterministic judgment and do not duplicate publication/verdicts.
        # Its semantic qualifications are retained separately below.
        final_result = run(global_ids, global_context)
        if consume(final_result, global_ids, "global", fulfillment_only=not remaining):
            global_receipt = final_result[1]
        check_active(check_current)
    finally:
        cancelled.set()

    judgments = sorted(
        [j for r in accepted for j in r["claim_judgments"]],
        key=lambda j: int(j["claim_id"][1:]),
    )
    missing = sorted(set(units) - {j["claim_id"] for j in judgments})
    # A failed/invalid semantic batch leaves explicit unknown entries for every
    # original ID. These are abstentions, never inferred provider judgments.
    for cid in missing:
        unit = units[cid]
        judgments.append(
            {
                **{
                    key: unit[key]
                    for key in (
                        "claim_id",
                        "part_index",
                        "start",
                        "end",
                        "claim_sha256",
                        "part_sha256",
                    )
                },
                "category": "strategic_judgment",
                "verdict": "unverified",
                "reason": "No bound judgment supplied; category is unknown.",
                "method": "unreviewed_abstention",
                "citations": [],
                "reviewed_claim": unit["claim"],
                "rendered_claim": unit["claim"],
                "correction_applied": False,
                "unverified_remainder": True,
            }
        )
    judgments.sort(key=lambda j: int(j["claim_id"][1:]))
    unresolved = list(
        dict.fromkeys(
            [s for r in accepted for s in r.get("unresolved", [])]
            + (global_receipt.get("unresolved", []) if global_receipt else [])
            + [
                j["claim_id"]
                + ": semantic recheck remains qualified or disagrees with the exact fact check."
                for j in (global_receipt or {}).get("claim_judgments", [])
                if not remaining
                and (j.get("verdict") != "supported" or j.get("unverified_remainder"))
            ]
            + [cid + ": no bound judgment supplied" for cid in missing]
        )
    )
    patches = [p for r in accepted for p in r.get("patches", [])]
    unverified = bool(
        unresolved
        or getattr(focused, "omitted", [])
        or global_receipt is None
        or global_receipt.get("status") == "unverified"
    )
    fulfillment = (global_receipt or {}).get("fulfillment", "unknown")
    if unverified and fulfillment == "complete":
        fulfillment = "unknown"
    receipt = {
        **(
            global_receipt
            or {
                k: v
                for k, v in final_result[1].items()
                if k in {"reason", "review_model"}
            }
        ),
        "status": (
            "unverified"
            if unverified
            else "corrected" if patches else "no_supported_correction"
        ),
        "fulfillment": fulfillment,
        "claim_unit_version": VERSION,
        "reviewed_parts": {
            str(j["part_index"]): original[j["part_index"]] for j in judgments
        },
        "claim_judgments": judgments,
        "patches": patches,
        "unresolved": unresolved,
        "claim_count": len(units),
        "missing_claim_ids": missing,
        "source_sha256": [text_hash(s) for s in focused],
        "evidence_indices": list(range(len(focused))),
        "partial_facts_retained": sum(
            len(j.get("supported_subspans", [])) for j in judgments
        ),
        "partial_facts_unknown": sum(
            j["verdict"] != "supported" or j.get("unverified_remainder", False)
            for j in judgments
        ),
        "episode_subspans": "omitted; parent hypotheses retain their original verdicts",
        "omitted_sources": len(getattr(focused, "omitted", [])),
        "omitted_source_labels": list(getattr(focused, "omitted", [])),
        "progressive_review": {
            "enabled": any(r.get("method") == "EXACT_source_check" for r in accepted),
            "method": "exact_cost_then_one_semantic_review",
            "model_job_count": sum("review_model" in j for j in jobs),
            "exact_fact_count": sum(
                j.get("method") == "EXACT_source_check" for j in judgments
            ),
            "job_limit": MAX_REVIEW_JOBS,
            "local_job_limit": MAX_LOCAL_JOBS,
            "jobs": jobs,
            "output_token_limit": MAX_REVIEW_JOBS * 4096,
            "full_evidence_bytes": sum(len(s.encode()) for s in focused),
            "evidence_bytes_sent": sum(j["evidence_bytes_sent"] for j in jobs),
            "input_chars": sum(
                (j.get("review_model") or {}).get("input_chars", 0) for j in jobs
            ),
            "job_count": sum(j["stage"] == "global" for j in jobs),
            "global_review_complete": global_receipt is not None,
            # Preserve the bound semantic assessment without overwriting exact
            # proof or presenting its proposed edits as applied output patches.
            "semantic_recheck": (
                {"receipt": deepcopy(global_receipt), "applied": False}
                if global_receipt is not None and not remaining
                else None
            ),
        },
    }
    return corrected, receipt
