"""Offline W01 capture/replay; no execution, scoring, QA writes or network.

Usage (PYTHONPATH=src): python -m sabermetrics.research_feasibility_capture
  --manifest frozen.json --attempts operator-records.json [--output capture.json]
Without --output this validates and prints a summary only. Output is exclusive.

Budget contract: arm configurations pin provider/settings/cache/identity and maximum
retries across all repeats. Transport timeout is per-attempt provider configuration,
not an end-to-end bound. Optional overall observation deadline runs from the first
slot attempt through retries, including gaps. Budget violations remain observations
and exports but cannot count as delivered within budget. Timeout provenance is a
sanitized external reason label; absent provenance leaves consistency unknown.
Cost caps distinguish spend so far from completed schedule spend; a schedule closes
on delivery, observed overall deadline exhaustion, or frozen retry budget exhaustion. No retry-until-success.

Freeze a Manifest before execution; its digest is the run reference. Each scheduled
Slot binds the exact case, source, build, provider/model/settings and protocol.
Attempts bind both manifest and slot digests. Times are operator-observed seconds
from started_at, not measurements made by this offline importer. Retain retries as
separate attempts, including failed attempts. Empty/partial schedules are allowed.
No labels are ingested: quality and useful latency remain null pending separate
blinded annotation of retained fragments. AI fixtures are development diagnostics,
never independent expertise. Hashes detect edits, not dishonest capture or identity. Unpinned replay is
structural-only and cannot authenticate completeness. External capture/manifest
anchors detect deleted attempts relative to a trusted envelope, not forged acquisition.
Settings use a bounded nonsecret allowlist; raw answers/receipts may contain user
data or secrets and are not universally sanitized. Fragment timestamps are operator
labels, not correctness judgments; final answers may differ from early fragments.
Answer hashes bind final text only, not the truth or continuity of fragment text.

answer_export uses the existing Answer schema, but is NOT a Run or blind packet:
failed/missing turns and repeated pairs cannot satisfy that format's complete,
one-pair-per-case contract. It also lacks a frozen Benchmark and independent human
reviews. Use evaluate_assistant_expertise.py only after separately constructing a
compatible complete Run and Benchmark; do not drop failures from this ledger or
interpret its counts as qualification. The protocol's topic rubric differs from
assistant_expertise's rubric; no score conversion or promotion is attempted here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_validator

from sabermetrics.assistant_expertise import (
    Answer,
    Digest,
    Identifier,
    Identity,
    Strict,
    digest,
    timestamp,
)

Seconds = Annotated[float, Field(ge=0, le=315360000, allow_inf_nan=False)]


class Settings(Strict):
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)]
    max_output_tokens: int = Field(ge=1, le=1000000)
    timeout_seconds: Seconds
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    )
    seed: int | None = Field(ge=0, le=2**32 - 1)


class Slot(Strict):
    id: Identifier
    pair_id: Identifier
    case_id: Identifier
    case_manifest_sha256: Digest
    source_snapshot_sha256: Digest
    arm: Literal["candidate", "baseline"]
    sequence_position: int = Field(ge=1)
    identity: Identity
    provider_id: Identifier
    effective_model_settings: Settings
    cache_state: Literal["cold", "warm", "unknown"]


class ArmConfiguration(Strict):
    identity: Identity
    provider_id: Identifier
    effective_model_settings: Settings
    cache_state: Literal["cold", "warm", "unknown"]
    max_retries: int = Field(ge=0, le=1000)
    overall_observation_deadline_seconds: Seconds | None = None


class Manifest(Strict):
    version: Literal["research-feasibility-manifest.v1"]
    run_id: Identifier
    frozen_at: str
    protocol_sha256: Digest
    evidence_kind: Literal["development_diagnostic", "operator_collected"]
    lane: Literal["single_arm_feasibility", "paired_comparison"]
    arm_configurations: dict[Literal["candidate", "baseline"], ArmConfiguration]
    systems_level_setting_differences: list[
        Literal[
            "temperature",
            "max_output_tokens",
            "timeout_seconds",
            "reasoning_effort",
            "seed",
            "max_retries",
            "overall_observation_deadline_seconds",
        ]
    ] = Field(max_length=7)
    cost_cap_usd: Seconds
    slots: list[Slot] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid(self):
        timestamp(self.frozen_at)
        for values in (
            [s.id for s in self.slots],
            [s.sequence_position for s in self.slots],
            [(s.pair_id, s.arm) for s in self.slots],
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate schedule identity")
        ordered = sorted(self.slots, key=lambda s: s.sequence_position)
        if [s.sequence_position for s in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError("Schedule positions must be contiguous")
        if set(self.arm_configurations) != {s.arm for s in self.slots}:
            raise ValueError("Arm configurations must exactly pin scheduled arms")
        if any(
            any(
                getattr(s, k) != getattr(self.arm_configurations[s.arm], k)
                for k in (
                    "identity",
                    "provider_id",
                    "effective_model_settings",
                    "cache_state",
                )
            )
            for s in self.slots
        ):
            raise ValueError("Slot differs from manifest-pinned arm configuration")
        if self.lane == "single_arm_feasibility" and len(self.arm_configurations) != 1:
            raise ValueError("Single feasibility lane requires one arm")
        cases = {}
        pairs = {}
        for slot in self.slots:
            signature = (
                slot.case_id,
                slot.case_manifest_sha256,
                slot.source_snapshot_sha256,
            )
            case_identity = signature[1:]
            if slot.case_id in cases and cases[slot.case_id] != case_identity:
                raise ValueError("Case identity changes across repeats")
            cases[slot.case_id] = case_identity
            if slot.pair_id in pairs and pairs[slot.pair_id] != signature:
                raise ValueError("Paired slots must share exact case and sources")
            pairs[slot.pair_id] = signature
        if self.lane == "paired_comparison":
            if len(self.arm_configurations) != 2 or (
                self.arm_configurations["candidate"].identity.revision,
                self.arm_configurations["candidate"].identity.config_sha256,
            ) == (
                self.arm_configurations["baseline"].identity.revision,
                self.arm_configurations["baseline"].identity.config_sha256,
            ):
                raise ValueError("Comparison requires distinct pinned builds")
            orders = []
            for pair_id in pairs:
                pair = [s for s in ordered if s.pair_id == pair_id]
                if (
                    len(pair) != 2
                    or pair[1].sequence_position != pair[0].sequence_position + 1
                ):
                    raise ValueError("Comparison requires complete adjacent pairs")
                a, b = pair
                if (a.provider_id, a.identity.model_id, a.cache_state) != (
                    b.provider_id,
                    b.identity.model_id,
                    b.cache_state,
                ):
                    raise ValueError("Pair provider/model/cache policy mismatch")
                differences = {
                    k
                    for k, v in a.effective_model_settings.model_dump().items()
                    if v != b.effective_model_settings.model_dump()[k]
                }
                differences.update(
                    k
                    for k in ("max_retries", "overall_observation_deadline_seconds")
                    if getattr(self.arm_configurations[a.arm], k)
                    != getattr(self.arm_configurations[b.arm], k)
                )
                if differences - set(self.systems_level_setting_differences):
                    raise ValueError("Undeclared systems-level settings difference")
                orders.append(a.arm)
            if orders.count("candidate") != orders.count("baseline"):
                raise ValueError("Comparison requires balanced AB/BA ordering")
        digest(self)  # reject nonfinite/non-JSON effective settings
        return self


class Fragment(Strict):
    kind: Literal["source", "explanation"]
    published_seconds: Seconds
    text: str = Field(min_length=1, max_length=12000)


class Attempt(Strict):
    id: Identifier
    manifest_sha256: Digest
    slot_id: Identifier
    slot_sha256: Digest
    retry_index: int = Field(ge=0)
    started_at: str
    finished_seconds: Seconds
    terminal_status: Literal["delivered", "failed", "timeout", "cancelled"]
    error_class: Identifier | None = None  # sanitized class, never credentials
    timeout_provenance: Identifier | None = None
    stage_times: dict[str, Seconds]
    tokens: dict[Literal["input", "output", "reasoning"], int | None]
    configured_cost_usd: Seconds | None
    cleanup_status: Literal["complete", "failed", "unknown"]
    fragments: list[Fragment] = Field(max_length=1000)
    answer: Answer | None = None
    evidence_receipts: list[dict[str, Any]]

    @model_validator(mode="after")
    def valid(self):
        timestamp(self.started_at)
        if self.error_class is not None and not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{0,63}", self.error_class
        ):
            raise ValueError("Invalid sanitized error class")
        if self.timeout_provenance is not None and self.terminal_status != "timeout":
            raise ValueError("Timeout provenance requires timeout status")
        if set(self.tokens) != {"input", "output", "reasoning"}:
            raise ValueError("All token telemetry keys required; use null for unknown")
        if (self.terminal_status == "delivered") != (self.answer is not None):
            raise ValueError("Only delivered attempts have a final answer")
        if self.terminal_status != "delivered" and self.error_class is None:
            raise ValueError("Unsuccessful attempts need a sanitized error class")
        if self.terminal_status == "delivered" and self.error_class is not None:
            raise ValueError("Delivered attempt cannot hide an error")
        for key in [
            *self.stage_times,
            *([self.error_class] if self.error_class else []),
            *([self.timeout_provenance] if self.timeout_provenance else []),
        ]:
            if key.lower().startswith(
                ("sk_", "sk-", "pk_live_", "rk_live_")
            ) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key):
                raise ValueError("Invalid sanitized telemetry label")
        times = [f.published_seconds for f in self.fragments]
        if times != sorted(times) or any(t > self.finished_seconds for t in times):
            raise ValueError("Invalid publication chronology")
        if any(t > self.finished_seconds for t in self.stage_times.values()):
            raise ValueError("Stage duration exceeds attempt duration")
        if any(v is not None and v < 0 for v in self.tokens.values()):
            raise ValueError("Negative token count")
        digest(self)
        return self


def capture(manifest: Manifest, attempts: list[Attempt]) -> dict:
    """Bind supplied observations to a frozen schedule without granting truth credit."""
    slots = {s.id: s for s in manifest.slots}
    seen, records = set(), []
    retries: dict[str, int] = {}
    previous_end = timestamp(manifest.frozen_at)
    previous_position = 1
    delivered_slots = set()
    slot_starts: dict[str, datetime] = {}
    deadline_exhausted_slots = set()
    for attempt in attempts:
        slot = slots.get(attempt.slot_id)
        if (
            slot is None
            or attempt.manifest_sha256 != digest(manifest)
            or attempt.slot_sha256 != digest(slot)
        ):
            raise ValueError("Attempt does not match frozen manifest/slot")
        if timestamp(attempt.started_at) < timestamp(manifest.frozen_at):
            raise ValueError("Attempt predates manifest freeze")
        start = timestamp(attempt.started_at)
        if start < previous_end or slot.sequence_position < previous_position:
            raise ValueError("Attempts must follow serialized schedule chronology")
        if slot.id in delivered_slots:
            raise ValueError("Retry after delivered slot prohibited")
        try:
            previous_end = start + timedelta(seconds=attempt.finished_seconds)
        except OverflowError as exc:
            raise ValueError("Attempt end exceeds supported calendar range") from exc
        slot_starts.setdefault(slot.id, start)
        config = manifest.arm_configurations[slot.arm]
        elapsed = (previous_end - slot_starts[slot.id]).total_seconds()
        violations = []
        if attempt.retry_index > config.max_retries:
            violations.append("retry_budget_exceeded")
        if (
            config.overall_observation_deadline_seconds is not None
            and elapsed > config.overall_observation_deadline_seconds
        ):
            violations.append("overall_observation_deadline_exceeded")
        if (
            config.overall_observation_deadline_seconds is not None
            and elapsed >= config.overall_observation_deadline_seconds
        ):
            deadline_exhausted_slots.add(slot.id)
        previous_position = slot.sequence_position
        if attempt.terminal_status == "delivered":
            delivered_slots.add(slot.id)
        expected = retries.get(slot.id, 0)
        if attempt.id in seen or attempt.retry_index != expected:
            raise ValueError("Duplicate attempt or missing/out-of-order retry")
        seen.add(attempt.id)
        retries[slot.id] = expected + 1
        timeout_status = "not_applicable"
        if attempt.terminal_status == "timeout":
            timeout_status = (
                "external_reason_supplied" if attempt.timeout_provenance else "unknown"
            )
        record = attempt.model_dump(mode="json")
        record.update(
            answer_sha256=digest(attempt.answer) if attempt.answer else None,
            evidence_receipt_sha256=digest(attempt.evidence_receipts),
            first_source_seconds=next(
                (f.published_seconds for f in attempt.fragments if f.kind == "source"),
                None,
            ),
            first_explanation_fragment_seconds=next(
                (
                    f.published_seconds
                    for f in attempt.fragments
                    if f.kind == "explanation"
                ),
                None,
            ),
            useful_explanation_seconds=None,
            quality=None,
            assessment_status="unassessed",
            protocol_violations=violations,
            delivered_within_budget=(
                attempt.terminal_status == "delivered" and not violations
            ),
            timeout_consistency=timeout_status,
            stage_telemetry_status=(
                "missing"
                if not attempt.stage_times
                else "operator_supplied_partial_or_complete"
            ),
            fragment_answer_binding="separately_hashed_final_answer; operator_fragment_labels; continuity_unverified",
            missing_telemetry=[k for k, v in attempt.tokens.items() if v is None]
            + (["configured_cost_usd"] if attempt.configured_cost_usd is None else []),
        )
        records.append(record)
    known_spend = sum(
        (
            Decimal(str(a.configured_cost_usd))
            for a in attempts
            if a.configured_cost_usd is not None
        ),
        Decimal(0),
    )
    spent_status = "pass"
    if not attempts or any(a.configured_cost_usd is None for a in attempts):
        spent_status = "unknown"
    if known_spend > Decimal(str(manifest.cost_cap_usd)):
        spent_status = "fail"
    closed_slots = {
        slot_id
        for slot_id, count in retries.items()
        if slot_id in delivered_slots
        or slot_id in deadline_exhausted_slots
        or count > manifest.arm_configurations[slots[slot_id].arm].max_retries
    }
    complete = len(closed_slots) == len(slots)
    within_budget_slots = {
        r["slot_id"] for r in records if r["delivered_within_budget"]
    }
    pairs: dict[str, set[str]] = {}
    if manifest.lane == "paired_comparison":
        for slot in slots.values():
            pairs.setdefault(slot.pair_id, set()).add(slot.id)
    pair_counts = {
        "scheduled_pairs": len(pairs),
        "completed_pairs": sum(ids <= closed_slots for ids in pairs.values()),
        "delivered_pairs": sum(ids <= delivered_slots for ids in pairs.values()),
        "delivered_within_budget_pairs": sum(
            ids <= within_budget_slots for ids in pairs.values()
        ),
        "partial_pairs": sum(
            bool(ids & set(retries)) and not ids <= closed_slots
            for ids in pairs.values()
        ),
        "failed_pairs": sum(
            ids <= closed_slots and not ids <= delivered_slots for ids in pairs.values()
        ),
        "unattempted_pairs": sum(not ids & set(retries) for ids in pairs.values()),
    }
    full_cap_status = spent_status
    if not complete and spent_status != "fail":
        full_cap_status = "unknown"
    body = {
        "version": "research-feasibility-capture.v1",
        "manifest": manifest.model_dump(mode="json"),
        "manifest_sha256": digest(manifest),
        "attempts": records,
        "counts": {
            "scheduled": len(slots),
            "attempted_slots": len(retries),
            "attempts": len(records),
            "delivery_denominator_slots": len(slots),
            "missing_token_values": sum(
                v is None for a in attempts for v in a.tokens.values()
            ),
            "unknown_cost_attempts": sum(
                a.configured_cost_usd is None for a in attempts
            ),
            "delivered": sum(a.terminal_status == "delivered" for a in attempts),
            "delivered_within_budget": sum(
                r["delivered_within_budget"] for r in records
            ),
            "protocol_violation_attempts": sum(
                bool(r["protocol_violations"]) for r in records
            ),
            "unassessed": len(records),
            "unattempted_slots": len(set(slots) - set(retries)),
        },
        "per_arm_counts": {
            arm: {
                "scheduled_slots": len(ids),
                "attempted_slots": len(ids & set(retries)),
                "attempts": sum(r["slot_id"] in ids for r in records),
                "closed_slots": len(ids & closed_slots),
                "delivered_slots": len(ids & delivered_slots),
                "delivered_within_budget_slots": len(ids & within_budget_slots),
            }
            for arm in manifest.arm_configurations
            for ids in [{s.id for s in slots.values() if s.arm == arm}]
        },
        "pair_counts": pair_counts,
        "systems_level_confound_disclosure": {
            "fixed_declared_differences": manifest.systems_level_setting_differences,
            "interpretation": "Fixed arm-specific systems comparison; declared differences confound build attribution",
        },
        "cost": {
            "known_sum_usd": float(known_spend),
            "cap_usd": manifest.cost_cap_usd,
            "spent_so_far_cap_status": spent_status,
            "schedule_complete": complete,
            "cap_status": full_cap_status,
        },
        "answer_export_kind": "unblinded_Answer_data",
        "claim_scope": manifest.lane
        + "; systems-level only; no causal graph gain claim",
        "answer_export": [
            {
                "attempt_id": a.id,
                "slot_id": a.slot_id,
                "answer": a.answer.model_dump(mode="json"),
            }
            for a in attempts
            if a.answer is not None
        ],
        "qualification": {
            "compatible_run": False,
            "quality": None,
            "reason": "Requires separate complete paired Benchmark/Run and independent reviews; see module documentation.",
        },
    }
    return {"capture": body, "capture_sha256": digest(body)}


def _replay(
    value: dict,
    expected_manifest_sha256=None,
    expected_capture_sha256=None,
    expected_protocol_sha256=None,
) -> dict:
    """Verify envelope AND recompute derived hashes/counts from original observations."""
    for anchor in (
        expected_manifest_sha256,
        expected_capture_sha256,
        expected_protocol_sha256,
    ):
        if anchor is not None:
            TypeAdapter(Digest).validate_python(anchor)
    if (
        set(value) != {"capture", "capture_sha256"}
        or digest(value["capture"]) != value["capture_sha256"]
    ):
        raise ValueError("Capture integrity failure")
    body = value["capture"]
    if (
        expected_capture_sha256 is not None
        and value["capture_sha256"] != expected_capture_sha256
    ):
        raise ValueError("External capture anchor mismatch")
    if (
        expected_manifest_sha256 is not None
        and body["manifest_sha256"] != expected_manifest_sha256
    ):
        raise ValueError("External manifest anchor mismatch")
    if (
        expected_protocol_sha256 is not None
        and body["manifest"]["protocol_sha256"] != expected_protocol_sha256
    ):
        raise ValueError("External protocol anchor mismatch")
    manifest = Manifest.model_validate(body["manifest"])
    fields = set(Attempt.model_fields)
    attempts = [
        Attempt.model_validate({k: v for k, v in a.items() if k in fields})
        for a in body["attempts"]
    ]
    rebuilt = capture(manifest, attempts)
    if rebuilt != value:
        raise ValueError("Capture derived record mismatch")
    return rebuilt


def replay(
    value,
    *,
    expected_manifest_sha256=None,
    expected_capture_sha256=None,
    expected_protocol_sha256=None,
):
    """External anchors verify equality, not acquisition authenticity.

    Without both manifest and capture anchors this is structural-only verification.
    Self-consistent forged observations require trusted acquisition to detect.
    """
    try:
        result = _replay(
            value,
            expected_manifest_sha256,
            expected_capture_sha256,
            expected_protocol_sha256,
        )
        return {
            **result,
            "verification": (
                "externally_pinned_envelope"
                if expected_manifest_sha256 is not None
                and expected_capture_sha256 is not None
                else "structural_only"
            ),
        }
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        OverflowError,
    ):
        return {"status": "invalid"}


def read(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    if path.stat().st_size > 8 * 1024**2:
        raise ValueError("Input exceeds 8 MiB")
    return json.loads(path.read_text(), object_pairs_hook=unique)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--expected-capture-sha256")
    parser.add_argument("--expected-protocol-sha256")
    args = parser.parse_args(argv)
    try:
        for anchor in (
            args.expected_manifest_sha256,
            args.expected_capture_sha256,
            args.expected_protocol_sha256,
        ):
            if anchor is not None:
                TypeAdapter(Digest).validate_python(anchor)
        if args.replay:
            if args.manifest or args.attempts:
                raise ValueError("Replay cannot also import observations")
            result = replay(
                read(args.replay),
                expected_manifest_sha256=args.expected_manifest_sha256,
                expected_capture_sha256=args.expected_capture_sha256,
                expected_protocol_sha256=args.expected_protocol_sha256,
            )
            if result.get("status") == "invalid":
                raise ValueError("Invalid replay")
        else:
            if not args.manifest or not args.attempts:
                raise ValueError("Manifest and attempts required")
            if args.expected_capture_sha256 is not None:
                raise ValueError("Capture anchor is replay-only")
            manifest = Manifest.model_validate(read(args.manifest))
            if (
                args.expected_manifest_sha256 is not None
                and digest(manifest) != args.expected_manifest_sha256
            ) or (
                args.expected_protocol_sha256 is not None
                and manifest.protocol_sha256 != args.expected_protocol_sha256
            ):
                raise ValueError("External import anchor mismatch")
            result = capture(
                manifest,
                [Attempt.model_validate(a) for a in read(args.attempts)],
            )
        verification = result.pop("verification", None)
        if args.output:
            # Exclusive creation also rejects symlinks and accidental input overwrite.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(
            json.dumps(
                {
                    **result["capture"]["counts"],
                    **({"verification": verification} if verification else {}),
                }
            )
        )
        return 0
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
        OverflowError,
    ):
        print(json.dumps({"status": "invalid"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
