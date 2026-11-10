"""Bounded public assistant contract; never an action or billing authority."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION: Literal["assistant.events.v1"] = "assistant.events.v1"
TERMINAL = frozenset({"completed", "stopped", "failed", "interrupted", "superseded"})
COLLECTION_LIMITS = {
    "answer_blocks": 100,
    "findings": 152,
    "claims": 256,
    "citations": 256,
    "activity": 256,
    "steering": 100,
    "obligations": 100,
}
# Below the approved 7.5-MiB ceiling: 5 MiB also guarantees <=64 pages
# with 28-KiB indivisible entities and 16-KiB per-page header reserve.
MAX_PROJECTION_BYTES = 5 * 1024 * 1024
MAX_EVENT_BYTES = 32 * 1024
PAGE_BYTES = 128 * 1024


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PublicError(ContractModel):
    code: str
    message: str
    retryable: bool


class PublicRun(ContractModel):
    run_id: str
    client_request_id: str | None = None
    generation: int
    deck_revision: int
    accepted_at: str | None = None
    worker_started_at: str | None = None
    ended_at: str | None = None
    budget_group_id: str | None = None
    budget_started_at: str | None = None
    deadline_at: str | None = None
    steered_from_run_id: str | None = None
    superseded_by_run_id: str | None = None
    lifecycle: Literal[
        "queued",
        "running",
        "completed",
        "stopped",
        "failed",
        "interrupted",
        "superseded",
    ]
    evidence_status: Literal["not_started", "partial", "reviewed", "unavailable"] = (
        "not_started"
    )
    fulfillment: Literal["unknown", "complete", "partial"] = "unknown"
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    legacy_fulfillment: str | None = None
    legacy_outcome_uncertain: bool = False
    stop_requested_at: str | None = None
    error: PublicError | None = None
    last_sequence: int = 0
    history_compacted: bool = False

    @model_validator(mode="after")
    def bound_header(self) -> PublicRun:
        """Reserve wire space for page metadata independently of entity bodies."""
        if len(serialized(self.model_dump()).encode()) > 12 * 1024:
            raise ValueError("Run header exceeds the public metadata bound.")
        return self


class PublicEvent(ContractModel):
    contract_version: Literal["assistant.events.v1"] = VERSION
    run_id: str
    generation: int
    sequence: int = Field(ge=1)
    event_id: str
    occurred_at: str
    kind: Literal[
        "run.accepted",
        "run.started",
        "run.ended",
        "activity.started",
        "activity.finished",
        "finding.upserted",
        "finding.withdrawn",
        "answer.block.upserted",
        "claim.updated",
        "steering.received",
        "steering.applied",
        "steering.rejected",
        "stop.requested",
        "obligation.upserted",
        "obligations.refined",
    ]
    payload: dict[str, Any]


class ProjectionTooLarge(ValueError):
    """A record must use an explicit bounded fallback before publication."""


def serialized(value: Any) -> str:
    """Canonical JSON used for size limits, fingerprints and persistence."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    """Identify captured content, never current remote source contents."""
    return hashlib.sha256(serialized(value).encode()).hexdigest()


def timestamp(value: float | None = None) -> str | None:
    """Format known epoch timestamps; invalid legacy values stay unavailable."""
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return None


def empty_projection(run: PublicRun) -> dict[str, Any]:
    """Create a replay baseline with all entity collections explicit."""
    return {"run": run.model_dump(), "entities": {k: [] for k in COLLECTION_LIMITS}}


def reduce_event(projection: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Apply one contiguous event to its own run, including terminal invariants."""
    item = PublicEvent.model_validate(event).model_dump()
    result = copy.deepcopy(projection)
    run = result["run"]
    if item["run_id"] != run["run_id"] or item["generation"] != run["generation"]:
        raise ValueError("Event identity does not match its run.")
    if item["sequence"] <= run["last_sequence"]:
        return result
    if item["sequence"] != run["last_sequence"] + 1:
        raise ValueError("Event sequence gap; refresh the snapshot.")
    if run["lifecycle"] in TERMINAL:
        raise ValueError("Terminal runs cannot publish new domain events.")
    data = item["payload"]
    if "collection" in data:
        name = data["collection"]
        if name not in COLLECTION_LIMITS:
            raise ValueError("Unknown entity collection.")
        record = data.get("entity")
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError("Entity identity is required.")
        if record.get("run_id") != run["run_id"]:
            raise ValueError("Entity run mismatch.")
        if len(serialized(record).encode()) > 28 * 1024:
            raise ProjectionTooLarge(
                "Entity metadata and text exceed the public bound."
            )
        for key in ("text", "relevant_excerpt"):
            value = record.get(key)
            if isinstance(value, str) and len(value.encode()) > 16 * 1024:
                raise ProjectionTooLarge("Public text exceeds the complete-text bound.")
        records = result["entities"][name]
        index = next(
            (i for i, v in enumerate(records) if v["id"] == record["id"]), None
        )
        if index is None:
            if len(records) >= COLLECTION_LIMITS[name]:
                raise ProjectionTooLarge("Collection limit reached.")
            records.append(copy.deepcopy(record))
        else:
            records[index] = copy.deepcopy(record)
    if "run" in data:
        updated = PublicRun.model_validate(data["run"]).model_dump()
        if (updated["run_id"], updated["generation"]) != (
            run["run_id"],
            run["generation"],
        ):
            raise ValueError("Run update identity mismatch.")
        result["run"] = updated
    result["run"]["last_sequence"] = item["sequence"]
    if item["kind"] == "run.ended" and result["run"]["lifecycle"] not in TERMINAL:
        raise ValueError("run.ended requires a terminal outcome.")
    if len(serialized(result).encode()) > MAX_PROJECTION_BYTES:
        raise ProjectionTooLarge("Public projection exceeds the aggregate limit.")
    return result


def final_entities(
    run_id: str, message: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Segment final prose without manufacturing sentence-level review mappings."""
    text = message.get("content")
    if not isinstance(text, str) or not text:
        return []
    if len(serialized(text).encode()) > 1024 * 1024:
        text = (
            "Answer unavailable: the generated artifact exceeded the public size limit."
        )
    answer_id = "answer:" + run_id
    logical_hash = digest(text)
    # UTF-8-safe transport segmentation. No bytes/whitespace are lost.
    segments: list[str] = []
    current: list[str] = []
    size = 0
    for character in text:
        # JSON escaping counts toward the 32-KiB event wire limit too.
        length = len(serialized(character)[1:-1].encode())
        if size + length > 16 * 1024:
            segments.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += length
    if current:
        segments.append("".join(current))
    return [
        (
            "answer.block.upserted",
            {
                "collection": "answer_blocks",
                "entity": {
                    "id": f"{answer_id}:{i}",
                    "block_id": f"{answer_id}:{i}",
                    "run_id": run_id,
                    "answer_id": answer_id,
                    "answer_version": 1,
                    "logical_text_hash": logical_hash,
                    "segment_index": i,
                    "segment_count": len(segments),
                    "text": segment,
                    "claim_ids": [],
                    "review_state": "unverified",
                    "review_method": "no_exact_final_text_mapping",
                },
            },
        )
        for i, segment in enumerate(segments)
    ]


def review_outcome(message: dict[str, Any]) -> dict[str, Any]:
    """Map fulfillment without turning a completed execution into proof of quality."""
    review = message.get("answer_review") or {}
    legacy = review.get("fulfillment", "unknown")
    fulfillment = {
        "complete": "complete",
        "partial": "partial",
        "nonanswer": "partial",
    }.get(legacy, "unknown")
    missing = review.get("missing_obligations", [])
    if not isinstance(missing, list):
        missing = []
    if legacy == "nonanswer":
        missing = ["answer", *missing]
    return {
        "fulfillment": fulfillment,
        "legacy_fulfillment": str(legacy),
        "missing_obligations": [str(x)[:200] for x in missing[:32]],
        # No exact final-text receipt is currently supplied by legacy finish().
        "evidence_status": (
            "partial" if review.get("claim_judgments") else "unavailable"
        ),
    }
