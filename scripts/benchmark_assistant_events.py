"""Bounded synthetic SQLite journal measurements; no model, network or live data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import subprocess
import tempfile
import time
import types
from pathlib import Path
from typing import Any

from sabermetrics import assistant_event_store as current
from sabermetrics.assistant_events import PublicRun
from sabermetrics.assistant_jobs import initialize as initialize_jobs

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = "src/sabermetrics/assistant_event_store.py"


def _reference(revision: str) -> types.ModuleType:
    """Load a trusted repository revision into this process; no checkout needed."""
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{MODULE_PATH}"], cwd=ROOT, text=True
    )
    module = types.ModuleType("journal_benchmark_reference")
    # Trusted source from this repository revision, loaded for comparison only.
    exec(  # noqa: S102
        compile(source, f"git:{revision}:{MODULE_PATH}", "exec"), module.__dict__
    )
    module.benchmark_source_hash = hashlib.sha256(source.encode()).hexdigest()
    return module


def _percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def _connection(path: Path, module: Any) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE deck_documents(id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO deck_documents VALUES ('deck')")
    initialize_jobs(conn)
    module.initialize(conn)
    conn.execute(
        "INSERT INTO build_assistant_jobs(id,owner_id,deck_id,generation,state,snapshot_json,created_at,updated_at) "
        "VALUES ('run','owner','deck',1,'running','{\"revision\":0}',0,0)"
    )
    module.create_run(
        conn,
        PublicRun(run_id="run", generation=1, deck_revision=0, lifecycle="queued"),
        owner="owner",
        deck="deck",
    )
    module.update_run(conn, "run", "run.started", lifecycle="running")
    conn.commit()
    return conn


def _event(index: int) -> dict[str, Any]:
    return {
        "collection": "activity",
        "entity": {
            "id": "operation",
            "run_id": "run",
            "state": "completed",
            "version": index,
        },
    }


def measure(module: Any, preload: int, samples: int) -> dict[str, Any]:
    """Measure serial BEGIN IMMEDIATE-to-commit holds on a temporary WAL DB."""
    with tempfile.TemporaryDirectory(prefix="assistant-journal-benchmark-") as folder:
        conn = _connection(Path(folder) / "synthetic.sqlite", module)
        try:
            for index in range(preload):
                conn.execute("BEGIN IMMEDIATE")
                module.append(conn, "run", "activity.finished", _event(index))
                conn.commit()
            scans: list[str] = []

            def trace(statement: str) -> None:
                upper = statement.upper()
                if "BUILD_ASSISTANT_EVENTS" in upper and (
                    "COUNT(*)" in upper or "SUM(LENGTH" in upper
                ):
                    scans.append(statement)

            conn.set_trace_callback(trace)
            durations = []
            for index in range(preload, preload + samples):
                started = time.perf_counter()
                conn.execute("BEGIN IMMEDIATE")
                module.append(conn, "run", "activity.finished", _event(index))
                conn.commit()
                durations.append((time.perf_counter() - started) * 1000)
            conn.set_trace_callback(None)
            cleanup_scans: list[str] = []
            conn.set_trace_callback(cleanup_scans.append)
            started = time.perf_counter()
            conn.execute("BEGIN IMMEDIATE")
            module.cleanup(conn, time.time())
            conn.commit()
            cleanup_ms = (time.perf_counter() - started) * 1000
            conn.set_trace_callback(None)
            return {
                "preloaded_events": preload,
                "measured_transactions": samples,
                "writer_transaction_p50_ms": round(statistics.median(durations), 6),
                "writer_transaction_sample_p95_ms": round(
                    _percentile(durations, 0.95), 6
                ),
                "writer_transaction_max_ms": round(max(durations), 6),
                "journal_aggregate_scan_queries": len(scans),
                "cleanup_ms": round(cleanup_ms, 6),
                "cleanup_joins_event_history": any(
                    "JOIN BUILD_ASSISTANT_JOBS" in query.upper()
                    and "BUILD_ASSISTANT_EVENTS" in query.upper()
                    for query in cleanup_scans
                ),
                "scope": "one connection, tiny fixed projection, serial local WAL writes; not concurrent lock wait or production chat latency",
            }
        finally:
            conn.close()


def main() -> None:
    """Write a small reproducible receipt; never inspect a live database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference")
    parser.add_argument("--preloads", nargs="+", type=int, default=[0, 256, 1024])
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 4 <= args.samples <= 64 or any(not 0 <= n <= 2048 for n in args.preloads):
        parser.error("Use 4..64 samples and preload counts in 0..2048")
    source = (ROOT / MODULE_PATH).read_bytes()
    result: dict[str, Any] = {
        "scope": "synthetic offline storage microbenchmark; no live latency/cost/quality claim",
        "sqlite_version": sqlite3.sqlite_version,
        "synchronous": "SQLite WAL default FULL",
        "current_source_sha256": hashlib.sha256(source).hexdigest(),
        "current": [measure(current, n, args.samples) for n in args.preloads],
    }
    if args.reference:
        reference = _reference(args.reference)
        result["reference_revision"] = args.reference
        result["reference_source_sha256"] = reference.benchmark_source_hash
        result["reference"] = [
            measure(reference, n, args.samples) for n in args.preloads
        ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
