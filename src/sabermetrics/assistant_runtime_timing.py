"""Intervals augment existing runtime receipts; overlapping stages are not added."""

from __future__ import annotations

import math
import time
from typing import Any


def timing_window(origin: float, begin: float) -> dict[str, float]:
    return {
        "start_seconds": round(max(0.0, begin - origin), 6),
        "end_seconds": round(max(0.0, time.monotonic() - origin), 6),
    }


def timing_coverage(elapsed: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    intervals: list[tuple[float, float]] = []
    missing = 0
    for row in rows:
        a, b = row.get("start_seconds"), row.get("end_seconds")
        if (
            not isinstance(a, (int, float))
            or not isinstance(b, (int, float))
            or isinstance(a, bool)
            or isinstance(b, bool)
            or not math.isfinite(a)
            or not math.isfinite(b)
            or not 0 <= a <= b
        ):
            missing += 1
            continue
        intervals.append((min(a, elapsed), min(b, elapsed)))
    intervals.sort()
    merged: list[tuple[float, float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(b, merged[-1][1])
        else:
            merged.append((a, b))
    union = sum(b - a for a, b in merged)
    return {
        "covered_wall_seconds": round(union, 3),
        "unattributed_wall_seconds": round(max(0, elapsed - union), 3),
        "overlapping_interval_seconds": round(
            sum(b - a for a, b in intervals) - union, 3
        ),
        "intervals": len(intervals),
        "rows_without_intervals": missing,
        "scope": "Union of instrumented intervals; parent/child and concurrent durations overlap. Per-stage durations must not be summed as wall time. Persistence after run_turn is outside this clock.",
    }
