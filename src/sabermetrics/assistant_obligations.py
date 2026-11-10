"""Persistable request obligations without an additional model call.

This module records an already validated planning interpretation. It does not
prove semantics from word presence, certify answers, or infer card legality.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyModel(BaseModel):
    """Reject unknown fields at quality-policy module boundaries."""

    model_config = ConfigDict(extra="forbid")


class Obligation(PolicyModel):
    """An auditable requirement and its explicit interpretation provenance."""

    id: str
    kind: str
    description: str = Field(min_length=1, max_length=600)
    origin: Literal["current_request", "validated_plan", "saved_constraint", "inferred"]
    strength: Literal["hard", "soft", "interpretation"] = "interpretation"
    request_quote: str | None = None
    requested_count: int | None = Field(default=None, ge=1, le=99)
    scope: str | None = None


class AnswerObligations(PolicyModel):
    """Frozen run input: obligations are separate from factual review results."""

    schema_version: Literal["assistant.obligations.v1"] = "assistant.obligations.v1"
    version: int = Field(ge=1)
    deck_revision: int = Field(ge=0)
    request: str = Field(min_length=1, max_length=16000)
    request_hash: str
    task_class: str = "unknown"
    named_subjects: list[str] = Field(default_factory=list, max_length=24)
    discovery_queries: list[str] = Field(default_factory=list, max_length=3)
    items: list[Obligation] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list)


class FulfillmentAssessment(PolicyModel):
    """Coverage receipt; correctness and strategic usefulness are not implied."""

    fulfillment: Literal["complete", "partial", "unknown"]
    legacy_fulfillment: str | None = None
    completed_obligations: list[str] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    reason: str


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def build_obligations(
    request: str,
    revision: int,
    *,
    task: Any = None,
    constraints: dict[str, Any] | None = None,
    version: int = 1,
) -> AnswerObligations:
    """Record exact request and a previously validated current-request plan.

    Free-form deliverables remain planner interpretations, not literal user
    constraints. An ungrounded task is ignored, never promoted to user intent.
    Existing constraints are labeled saved constraints rather than current quotes.
    """
    result = AnswerObligations(
        request=request,
        request_hash=_digest(request),
        deck_revision=revision,
        version=version,
    )

    def add(kind: str, description: str, origin: Any, **fields: Any) -> None:
        key = {"kind": kind, "description": description, **fields}
        result.items.append(
            Obligation(
                id=_digest(key)[:20],
                kind=kind,
                description=description,
                origin=origin,
                **fields,
            )
        )

    add(
        "answer",
        "Answer the current request, or state an evidence-based shortfall.",
        "current_request",
        strength="hard",
        request_quote=request[:600],
    )
    if hasattr(task, "model_dump"):
        task = task.model_dump()
    if isinstance(task, dict):
        quote = task.get("current_request_quote")
        grounded = (
            isinstance(quote, str)
            and len(quote.strip()) >= min(8, len(request.strip()))
            and quote in request
        )
        if grounded:
            assert isinstance(quote, str)
            result.task_class = str(task.get("kind") or "unknown")
            result.named_subjects = [
                name
                for name in task.get("named_cards", [])
                if isinstance(name, str) and name.casefold() in request.casefold()
            ][:24]
            for label in task.get("deliverables", [])[:6]:
                if isinstance(label, str) and label.strip():
                    add(
                        "deliverable",
                        label[:600],
                        "validated_plan",
                        request_quote=quote[:600],
                    )
            for row in task.get("discovery_lists", [])[:3]:
                if not isinstance(row, dict):
                    continue
                row_quote = row.get("request_quote")
                query = row.get("query")
                if (
                    not isinstance(row_quote, str)
                    or not row_quote.strip()
                    or row_quote not in request
                ):
                    continue
                if not isinstance(query, str) or not 1 <= len(query) <= 300:
                    continue
                result.discovery_queries.append(query)
                count = row.get("requested_count")
                if type(count) is not int or not 1 <= count <= 20:
                    count = None
                add(
                    "candidate_list",
                    str(row.get("label") or "Requested candidates")[:600],
                    "validated_plan",
                    request_quote=row_quote[:600],
                    requested_count=count,
                    scope=query,
                )
            if result.task_class in {"discover", "compare", "fill", "swap"}:
                add(
                    "costs_and_prerequisites",
                    "Explain relevant casting and activation costs, repeatability limits and setup requirements.",
                    "inferred",
                )
                add(
                    "deck_interaction",
                    "Explain supported interaction with the supplied deck, preserving uncertainty.",
                    "inferred",
                )
            if result.task_class == "compare":
                add(
                    "comparison",
                    "Explain the pairwise tradeoffs determining the comparison or ordering.",
                    "inferred",
                )
            if task.get("requires_empirical_evidence") is True:
                add(
                    "empirical_evidence",
                    "Distinguish observed measurements from unmeasured hypotheses and identify sample context.",
                    "validated_plan",
                    request_quote=quote[:600],
                )
        else:
            result.limitations.append(
                "No validated current-request planning quote; only the original request and saved constraints are binding."
            )
    for key in (
        "protected_cards",
        "excluded_candidates",
        "prioritized_candidates",
        "power_level",
        "theme",
        "objective",
    ):
        value = (constraints or {}).get(key)
        if value:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            # Keep exact constraints outside this compact audit label if too large.
            description = f"Preserve saved {key}; fingerprint {_digest(value)}."
            if len(encoded) <= 400:
                description += " Value: " + encoded
            add(
                "saved_constraint",
                description,
                "saved_constraint",
                strength=(
                    "hard"
                    if key in {"protected_cards", "excluded_candidates"}
                    else "soft"
                ),
                scope=key,
            )
    return result


def assess_fulfillment(
    obligations: AnswerObligations | dict[str, Any],
    *,
    legacy_fulfillment: str | None,
    completed_obligation_ids: list[str] | tuple[str, ...] = (),
    missing_obligation_ids: list[str] | tuple[str, ...] = (),
    answer_present: bool = True,
) -> FulfillmentAssessment:
    """Combine explicit coverage receipts without reading answer prose.

    A legacy `complete` verdict does not establish individual obligation
    coverage. Missing/unknown coverage cannot silently become a complete answer.
    """
    obligations = AnswerObligations.model_validate(obligations)
    expected = {item.id for item in obligations.items}
    completed = set(completed_obligation_ids)
    missing = set(missing_obligation_ids)
    if not completed <= expected or not missing <= expected:
        raise ValueError("Coverage receipt references an unknown obligation")
    if completed & missing:
        raise ValueError("An obligation cannot be both completed and missing")
    if not answer_present or legacy_fulfillment == "nonanswer":
        return FulfillmentAssessment(
            fulfillment="partial",
            legacy_fulfillment=legacy_fulfillment,
            missing_obligations=["answer"],
            reason="No substantive answer was supplied.",
        )
    if missing or legacy_fulfillment == "partial":
        return FulfillmentAssessment(
            fulfillment="partial",
            legacy_fulfillment=legacy_fulfillment,
            completed_obligations=sorted(completed),
            missing_obligations=sorted(missing),
            reason="Explicitly incomplete request coverage.",
        )
    if completed == expected and expected and legacy_fulfillment == "complete":
        return FulfillmentAssessment(
            fulfillment="complete",
            legacy_fulfillment=legacy_fulfillment,
            completed_obligations=sorted(completed),
            reason="All persisted obligations have explicit coverage receipts; factual accuracy is separate.",
        )
    return FulfillmentAssessment(
        fulfillment="unknown",
        legacy_fulfillment=legacy_fulfillment,
        completed_obligations=sorted(completed),
        reason="Per-obligation coverage is unavailable or incomplete; no semantic coverage inferred from prose.",
    )
