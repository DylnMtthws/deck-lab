"""One bounded corrective pass; reuse exact original spans and unaffected reviews."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sabermetrics.assistant_answer_review import (
    AnswerPatch,
    AnswerReview,
    SourceEvidence,
    apply_patches,
)
from sabermetrics.assistant_claim_review import focused_sources, review_claims
from sabermetrics.assistant_corrective_retrieval import corrective_evidence


def corrective_review(
    gateway: Any,
    db_path: Any,
    parts: list[str],
    corrected: list[str],
    sources: list[str],
    receipt: dict[str, Any],
    user_request: str,
    cards: list[dict[str, Any]],
    document: dict[str, Any],
    *,
    check_current: Callable[[], Any],
    can_review: Callable[[], bool],
    protected_parts: set[int] | None = None,
    research_context: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    lookup = corrective_evidence(
        db_path,
        receipt,
        parts,
        sources,
        cards,
        document,
        check_current=check_current,
    )
    additional = lookup.pop("additional_sources")
    ids = set(lookup["affected_claim_ids"])
    result = {**receipt, "corrective_retrieval": lookup}
    if not additional or not ids:
        lookup["review_status"] = "not_needed"
        return corrected, sources, result
    if not can_review():
        lookup["review_status"] = "time_budget"
        return corrected, sources, result
    check_current()
    expanded = SourceEvidence()
    expanded.extend([*sources, *additional])
    expanded.omitted = list(getattr(sources, "omitted", []))
    expanded.card_names = list(getattr(sources, "card_names", []))
    _, followup = review_claims(
        gateway,
        parts,
        expanded,
        user_request,
        claim_ids=ids,
        protected_parts=protected_parts,
        research_context=research_context,
    )
    check_current()
    lookup["review_status"] = followup.get("status", "unverified")
    if followup.get("review_model"):
        lookup["review_model"] = followup["review_model"]
    new_judgments = followup.get("claim_judgments", [])
    # A failed or incomplete call cannot discard the prior evidence-bound result.
    if {j["claim_id"] for j in new_judgments} != ids:
        lookup["review_status"] = "incomplete_review"
        return corrected, sources, result
    replacements = {j["claim_id"]: j for j in new_judgments}
    judgments = [replacements.get(j["claim_id"], j) for j in receipt["claim_judgments"]]
    # Existing corrected fragments were excluded by retrieval, so their patches
    # remain disjoint from the new patches. Reapply both to the original text.
    patches = [*receipt.get("patches", []), *followup.get("patches", [])]
    focused = focused_sources(parts, expanded)
    try:
        merged = apply_patches(
            parts,
            focused,
            AnswerReview(patches=[AnswerPatch.model_validate(p) for p in patches]),
            set(protected_parts or set()),
            list(getattr(focused, "card_names", [])),
        )
    except ValueError:
        lookup["review_status"] = "patch_merge_rejected"
        return corrected, sources, result
    unresolved = [
        line
        for line in receipt.get("unresolved", [])
        if line.split(":", 1)[0] not in ids
    ]
    unresolved.extend(followup.get("unresolved", []))
    result.update(
        claim_judgments=judgments,
        patches=patches,
        unresolved=list(dict.fromkeys(unresolved)),
        source_sha256=followup.get("source_sha256", []),
        omitted_sources=followup.get("omitted_sources", 0),
        omitted_source_labels=followup.get("omitted_source_labels", []),
        source_bytes_before_focus=followup.get("source_bytes_before_focus", 0),
        source_bytes_after_focus=followup.get("source_bytes_after_focus", 0),
    )
    result["status"] = (
        "unverified"
        if unresolved or result["omitted_sources"]
        else "corrected" if patches else "no_supported_correction"
    )
    # A subset judgment cannot upgrade whole-request fulfillment.
    return merged, expanded, result
