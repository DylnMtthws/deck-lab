"""Bounded, content-free measurements from one server monotonic clock.

These receipts describe observed timings; they do not measure correctness,
prove billing, or subtract browser timestamps from server timestamps.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskClass = Literal[
    "exact_fact",
    "rules",
    "discovery",
    "comparison",
    "deck_review",
    "simulation",
    "unknown",
]
Milestone = Literal[
    "request_received",
    "accepted",
    "worker_started",
    "first_useful_persisted",
    "final_persisted",
]
_MILESTONES = frozenset(
    {
        "request_received",
        "accepted",
        "worker_started",
        "first_useful_persisted",
        "final_persisted",
    }
)
_PHASES = frozenset(
    {
        "ledger_wait",
        "provider_permit_wait",
        "transport",
        "gateway_execution",
        "checkpoint_callback",
    }
)
MAX_INTERVALS = 512
MAX_PROVIDER_CALLS = 128


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _union(intervals: list[tuple[float, float]]) -> float:
    end = -math.inf
    total = 0.0
    for start, finish in sorted(intervals):
        total += max(0.0, finish - max(start, end))
        end = max(end, finish)
    return round(total, 6)


class LatencyReport(BaseModel):
    """Content-free offsets and interval unions within one server process."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal["assistant.latency.v1"] = "assistant.latency.v1"
    run_id: str = Field(min_length=1, max_length=200)
    task_class: TaskClass
    clock_domain: Literal["server_monotonic"] = "server_monotonic"
    origin_scope: Literal["request", "worker", "runtime"]
    milestones_seconds: dict[str, float]
    durations_seconds: dict[str, float | None]
    phase_wall_seconds: dict[str, float]
    version_hashes: dict[str, str]
    usage_tokens_observed: dict[str, int] | None
    usage_calls_observed: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    provider_attempts_observed: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    dropped_intervals: int = Field(ge=0)
    dropped_provider_calls: int = Field(ge=0)
    scope: str = (
        "Offsets use one server monotonic origin. Phase values are interval unions "
        "and overlap across categories; never add them as total wall time. Missing "
        "milestones mean unobserved, not zero. Browser rendering is measured separately."
    )


class ClientLatencyObservation(BaseModel):
    """Optional client-only durations, never authority for server or financial state."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    run_id: str = Field(min_length=1, max_length=200)
    clock_domain: Literal["client_monotonic"] = "client_monotonic"
    received_to_rendered_seconds: float = Field(ge=0, allow_inf_nan=False)
    first_useful: bool


class LatencyTrace:
    """Collect bounded timings for one run without recording conversation content.

    Args:
        run_id: Existing persisted run identity, never a new execution identity.
        task_class: Bounded task classification supplied by existing planning.
        origin_scope: Where the monotonic origin was actually captured.
        origin_monotonic: Optional origin from the SAME process monotonic clock.
        clock: Injectable monotonic clock for deterministic offline tests.
    """

    def __init__(
        self,
        run_id: str,
        task_class: TaskClass = "unknown",
        *,
        origin_scope: Literal["request", "worker", "runtime"] = "runtime",
        origin_monotonic: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        version_hashes: Mapping[str, str] | None = None,
    ) -> None:
        self._clock = clock
        origin = _number(clock() if origin_monotonic is None else origin_monotonic)
        if origin is None:
            raise ValueError("A finite monotonic origin is required.")
        versions = dict(version_hashes or {})
        allowed = {"prompt", "schema", "model", "policy", "evidence"}
        for name, value in versions.items():
            if (
                name not in allowed
                or not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError(
                    "Version telemetry requires approved keys and SHA-256 values."
                )
        # Validate bounded identifiers and vocabulary before accepting samples.
        LatencyReport(
            run_id=run_id,
            task_class=task_class,
            origin_scope=origin_scope,
            milestones_seconds={},
            durations_seconds={},
            phase_wall_seconds={},
            version_hashes=versions,
            usage_tokens_observed=None,
            usage_calls_observed=0,
            provider_calls=0,
            provider_attempts_observed=0,
            cache_hits=0,
            dropped_intervals=0,
            dropped_provider_calls=0,
        )
        self.run_id = run_id
        self.task_class = task_class
        self.origin_scope = origin_scope
        self._origin = origin
        self._versions = versions
        self._usage: dict[str, tuple[int, int, int, int]] = {}
        self._milestones: dict[str, float] = {}
        self._intervals: list[tuple[str, float, float]] = []
        self._calls: dict[str, bool] = {}
        self._attempts = 0
        self._attempt_spans: set[tuple[float, float]] = set()
        self._cache_hits = 0
        self._dropped_intervals = 0
        self._dropped_calls = 0

    def mark(self, name: Milestone, *, at: float | None = None) -> bool:
        """Record the first observed milestone; never fabricate absent boundaries."""
        if name not in _MILESTONES:
            raise ValueError("Unknown latency milestone.")
        if name in self._milestones:
            return False
        value = _number(self._clock() if at is None else at)
        if value is None or value < self._origin:
            return False
        offset = value - self._origin
        order = [
            "request_received",
            "accepted",
            "worker_started",
            "first_useful_persisted",
            "final_persisted",
        ]
        position = order.index(name)
        for other, existing in self._milestones.items():
            other_position = order.index(other)
            if (other_position < position and existing > offset) or (
                other_position > position and existing < offset
            ):
                return False
        self._milestones[name] = offset
        return True

    def phase(self, name: str, start: float, end: float) -> bool:
        """Accept a known provider phase from existing operation_phase receipts."""
        a, b = _number(start), _number(end)
        if name not in _PHASES or a is None or b is None or a < self._origin or b < a:
            return False
        if len(self._intervals) >= MAX_INTERVALS:
            self._dropped_intervals += 1
            return False
        self._intervals.append((name, a - self._origin, b - self._origin))
        return True

    def provider_checkpoint(self, event: Mapping[str, Any]) -> None:
        """Adapt existing gateway checkpoints, once per call, without copying text."""
        if event.get("type") != "provider_checkpoint":
            return
        call_id = event.get("call_id")
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 200:
            return
        state = event.get("state")
        if state not in {"started", "finished"}:
            return
        if call_id not in self._calls:
            if len(self._calls) >= MAX_PROVIDER_CALLS:
                self._dropped_calls += 1
                return
            self._calls[call_id] = False
        if state != "finished" or self._calls[call_id]:
            return
        self._calls[call_id] = True
        rows = event.get("phase_timings")
        if not isinstance(rows, list):
            return
        for row in rows[:MAX_INTERVALS]:
            if not isinstance(row, dict):
                continue
            name = row.get("phase")
            start = _number(row.get("started_monotonic"))
            end = _number(row.get("finished_monotonic"))
            if not isinstance(name, str) or start is None or end is None:
                continue
            accepted = self.phase(name, start, end)
            if (
                accepted
                and name == "transport"
                and (start, end) not in self._attempt_spans
            ):
                self._attempt_spans.add((start, end))
                self._attempts += 1
        self._dropped_intervals += max(0, len(rows) - MAX_INTERVALS)

    def usage(
        self,
        call_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> bool:
        """Record known usage once; absence is unknown and this never settles cost."""
        values = (input_tokens, cached_input_tokens, output_tokens, reasoning_tokens)
        if (
            not isinstance(call_id, str)
            or not 1 <= len(call_id) <= 200
            or call_id in self._usage
        ):
            return False
        if len(self._usage) >= MAX_PROVIDER_CALLS or any(
            type(v) is not int or not 0 <= v <= 10**12 for v in values
        ):
            return False
        if cached_input_tokens > input_tokens:
            return False
        self._usage[call_id] = values
        return True

    def cache_hit(self) -> None:
        """Count an actual verified receipt reuse, not a similarity suggestion."""
        self._cache_hits += 1

    def snapshot(self) -> LatencyReport:
        """Return a detached receipt; unobserved intervals remain explicitly absent."""

        def duration(begin: str, finish: str) -> float | None:
            if begin not in self._milestones or finish not in self._milestones:
                return None
            return round(self._milestones[finish] - self._milestones[begin], 6)

        return LatencyReport(
            run_id=self.run_id,
            task_class=self.task_class,
            origin_scope=self.origin_scope,
            milestones_seconds={
                key: round(value, 6) for key, value in self._milestones.items()
            },
            durations_seconds={
                "acknowledgment": duration("request_received", "accepted"),
                "queue": duration("accepted", "worker_started"),
                "first_useful": duration("accepted", "first_useful_persisted"),
                "completion": duration("accepted", "final_persisted"),
                "worker_to_first_useful": duration(
                    "worker_started", "first_useful_persisted"
                ),
                "worker_to_final": duration("worker_started", "final_persisted"),
            },
            phase_wall_seconds={
                name: _union(
                    [(a, b) for phase, a, b in self._intervals if phase == name]
                )
                for name in sorted({phase for phase, _, _ in self._intervals})
            },
            version_hashes=dict(self._versions),
            usage_tokens_observed=(
                dict(
                    zip(
                        (
                            "input_tokens",
                            "cached_input_tokens",
                            "output_tokens",
                            "reasoning_tokens",
                        ),
                        map(sum, zip(*self._usage.values(), strict=True)),
                        strict=True,
                    )
                )
                if self._usage
                else None
            ),
            usage_calls_observed=len(self._usage),
            provider_calls=len(self._calls),
            provider_attempts_observed=self._attempts,
            cache_hits=self._cache_hits,
            dropped_intervals=self._dropped_intervals,
            dropped_provider_calls=self._dropped_calls,
        )
