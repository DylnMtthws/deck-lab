"""Bounded diagnostics for the exact public Research cohort, never fallback data."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from typing import Any

from sabermetrics.research import ResearchRepo

MAX_ALTERNATIVE_WINDOWS = 2
MAX_STATE_BYTES = 4096


def coverage_receipt(start: str, end: str) -> dict[str, Any]:
    return {
        "state": "unknown",
        "eligible_lists": None,
        "count_is_lower_bound": False,
        "window_start": start,
        "window_end_exclusive": end,
        "min_event_size": None,
        "scope": "exact Research commander/partner, date, event floor and recorded-list predicates",
        "source_provenance": {
            "state": "unknown",
            "local_refreshed_at": None,
            "upstream_fetched_at": None,
            "generation": None,
            "recorded_min_event_size": None,
            "recorded_snapshot_counts": {},
        },
        "alternative_windows": {
            "state": "not_checked",
            "windows": [],
            "maximum_windows": MAX_ALTERNATIVE_WINDOWS,
            "interpretation": "Availability only; these lists do not enter recommendation counts or establish associations.",
        },
    }


def read_source_provenance(conn: sqlite3.Connection, receipt: dict[str, Any]) -> None:
    """Read only the published sync receipt, without guessing lineage from dates.

    research_sync.apply_snapshot writes these fields in the replacement transaction.
    Counts describe that recorded refresh, not independently recounted current rows.
    """
    columns = {r[1] for r in conn.execute("PRAGMA table_info(research_source_state)")}
    if not {"id", "state_json"} <= columns:
        return
    row = conn.execute(
        "SELECT state_json FROM research_source_state WHERE id=1 "
        "AND length(CAST(state_json AS BLOB))<=?",
        (MAX_STATE_BYTES,),
    ).fetchone()
    if row is None:
        return
    try:
        state = json.loads(row[0])
    except (ValueError, TypeError):
        return
    if not isinstance(state, dict):
        return
    provenance = receipt["source_provenance"]
    stamp = state.get("refreshed_at")
    if isinstance(stamp, str):
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is not None:
                provenance.update(
                    state="recorded_local_refresh", local_refreshed_at=stamp
                )
        except ValueError:
            pass
    if type(state.get("min_event_size")) is int and state["min_event_size"] >= 0:
        provenance["recorded_min_event_size"] = state["min_event_size"]
    provenance["recorded_snapshot_counts"] = {
        key: state[key]
        for key in ("cards", "decks", "entries", "deck_cards")
        if type(state.get(key)) is int and state[key] >= 0
    }
    provenance["interpretation"] = (
        "Local refresh receipt only; no upstream retrieval time, corpus generation, "
        "or guarantee of current row counts is recorded."
    )


def record_count(
    receipt: dict[str, Any], n: int, maximum: int, floor: int | None
) -> None:
    receipt.update(
        state="no_eligible_cohort" if n == 0 else "eligible_cohort",
        eligible_lists=n,
        count_is_lower_bound=n > maximum,
        min_event_size=floor,
    )
    if n > maximum:
        receipt["state"] = "cohort_too_large"


def alternative_windows(
    conn: sqlite3.Connection,
    receipt: dict[str, Any],
    *,
    cte: str,
    params: list[Any],
    window_days: int,
    maximum: int,
    deadline: float,
) -> None:
    """At most two bounded counts, preserving every non-date cohort predicate."""
    diagnostic = receipt["alternative_windows"]
    broader = [
        w for w in (7, 30, 90, 365, 0) if window_days and (w == 0 or w > window_days)
    ]
    diagnostic["state"] = "available" if broader else "no_broader_supported_window"
    for window in broader[:MAX_ALTERNATIVE_WINDOWS]:
        try:
            if time.monotonic() > deadline:
                raise sqlite3.OperationalError("interrupted")
            start, end, _ = ResearchRepo._scope(window)
            n = conn.execute(
                cte + " SELECT COUNT(*) FROM (SELECT deck_id FROM cohort LIMIT ?)",
                [start, end, *params[2:], maximum + 1],
            ).fetchone()[0]
            if time.monotonic() > deadline:
                raise sqlite3.OperationalError("interrupted")
            diagnostic["windows"].append(
                {
                    "window_days": window,
                    "window_start": start,
                    "window_end_exclusive": end,
                    "eligible_lists": n,
                    "count_is_lower_bound": n > maximum,
                }
            )
        except sqlite3.Error as exc:
            diagnostic["state"] = (
                "query_budget_exceeded" if "interrupted" in str(exc) else "unavailable"
            )
            break
