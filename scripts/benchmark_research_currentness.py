#!/usr/bin/env python3
"""E002a: bounded offline synthetic preparation probe; never a full QA replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PIN = Path(
    "/Users/dylan/Projects/mtg/deck-lab/infra/.private/qa-builds/12af094c1a5adeeb2a90949b3a00f530b75f82f0/source"
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Meter:
    """Nested inclusive/exclusive wall and CPU; only exclusive values partition time."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.rows = defaultdict(
            lambda: dict(
                count=0, wall=0.0, cpu=0.0, exclusive_wall=0.0, exclusive_cpu=0.0
            )
        )
        self.stack = []

    @contextmanager
    def span(self, name):
        if not self.enabled:
            yield
            return
        frame = [time.perf_counter(), time.process_time(), 0.0, 0.0]
        self.stack.append(frame)
        try:
            yield
        finally:
            wall, cpu = time.perf_counter() - frame[0], time.process_time() - frame[1]
            self.stack.pop()
            row = self.rows[name]
            row["count"] += 1
            row["wall"] += wall
            row["cpu"] += cpu
            row["exclusive_wall"] += wall - frame[2]
            row["exclusive_cpu"] += cpu - frame[3]
            if self.stack:
                self.stack[-1][2] += wall
                self.stack[-1][3] += cpu


def fixture(path, payload):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """CREATE TABLE deck_documents(id TEXT, owner_id TEXT, revision INTEGER);
        CREATE TABLE build_assistant_sessions(deck_id TEXT, owner_id TEXT, payload_json TEXT);
        CREATE TABLE build_assistant_jobs(id TEXT, deck_id TEXT, owner_id TEXT, state TEXT, lease_owner TEXT, lease_deadline REAL);
        INSERT INTO deck_documents VALUES ('deck','owner',1);
        INSERT INTO build_assistant_jobs VALUES ('turn','deck','owner','queued',NULL,NULL);"""
        )
        conn.execute(
            "INSERT INTO build_assistant_sessions VALUES (?,?,?)",
            ("deck", "owner", json.dumps(payload)),
        )
        conn.commit()


def inputs():
    # 100 sources, 170 text sentences; compiler clause segmentation is measured separately.
    return [
        dict(
            oracle_id=f"synthetic-{i}",
            name=f"Synthetic {i}",
            type_line="Creature",
            oracle_text="{T}: Add {R}." + ("\n{T}: Draw a card." if i < 70 else ""),
        )
        for i in range(100)
    ]


def database_state(path):
    return {
        (suffix or "main"): dict(
            exists=(p := Path(str(path) + suffix)).exists(),
            bytes=p.stat().st_size if p.exists() else None,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,
        )
        for suffix in ("", "-wal", "-shm")
    }


def run_one(path, cards, warm, instrumented, noop=False, active_factory=None):
    from sabermetrics import assistant_inference_facts as facts
    from sabermetrics import assistant_store as store_module
    from sabermetrics import db
    from sabermetrics.assistant_deck_inference import (
        _facts_for_turn,
        clear_compilation_cache,
    )
    from sabermetrics.assistant_inference_search import discover_connections

    store = object.__new__(store_module.AssistantStore)
    store.db_path = path
    if active_factory is not None:
        store.active = active_factory(path, store.active)
    meter = Meter(instrumented)
    counts = defaultdict(int)
    original_connect = sqlite3.connect
    original_loads = json.loads
    original_compile = facts.compile_deck_facts

    def compile_facts(*args, **kwargs):
        with meter.span("compiler_including_source_validation"):
            return original_compile(*args, **kwargs)

    live = set()

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            with meter.span("sql_fetch"):
                return self.cursor.fetchone()

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            with meter.span(
                "pragma" if sql.lstrip().upper().startswith("PRAGMA") else "sql_execute"
            ):
                return Cursor(super().execute(sql, *args, **kwargs))

        def close(self):
            with meter.span("connection_close"):
                super().close()
                live.discard(id(self))

    def connect(*args, **kwargs):
        counts["connections"] += 1
        if kwargs.get("uri") and str(args[0]).endswith("?mode=ro"):
            counts["readonly_connections"] += 1
        kwargs["factory"] = Connection
        with meter.span("connection_open"):
            conn = original_connect(*args, **kwargs)
        live.add(id(conn))
        return conn

    def loads(value):
        with meter.span("payload_decode"):
            return original_loads(value)

    def current():
        counts["callbacks"] += 1
        if noop:
            return True
        with meter.span("callback"):
            # Explicit reconstruction of runtime -> routes.stopped -> store.active.
            if not store.active("owner", "deck", "turn"):
                raise RuntimeError("inactive fixture")
            with db.connect(path) as conn:
                row = conn.execute(
                    "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
                    ("deck", "owner"),
                ).fetchone()
            if row is None or row["revision"] != 1:
                raise RuntimeError("revision changed")
            with closing(
                sqlite3.connect(
                    path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2
                )
            ) as conn:
                row = conn.execute(
                    "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
                    ("deck", "owner"),
                ).fetchone()
            if row is None or int(row[0]) != 1:
                raise RuntimeError("revision changed")
        return True

    clear_compilation_cache()
    rules = {"sources": []}
    if warm:
        _facts_for_turn(cards, rules, lambda: True)
    before = database_state(path)
    start, cpu = time.perf_counter(), time.process_time()
    # Both meter modes use identical hooks. NO-OP uses real unpatched compiler/search.
    # Keep preparation timer inside patch entry/exit to preserve attribution scope.
    with (  # noqa: SIM117
        nullcontext() if noop else patch.object(sqlite3, "connect", connect),
        (
            nullcontext()
            if noop
            else patch.object(store_module, "json", SimpleNamespace(loads=loads))
        ),
        (
            nullcontext()
            if noop
            else patch.object(facts, "compile_deck_facts", compile_facts)
        ),
    ):
        with meter.span("preparation"):
            with meter.span("compilation_cache"):
                bundle, hit = _facts_for_turn(cards, rules, current)
            compiler_callbacks = counts["callbacks"]
            with meter.span("search"):
                search = discover_connections(bundle, check_current=current)
            with meter.span("manifest"):
                manifest_hash = digest({"bundle": bundle, "search": search})
    elapsed, cpu_elapsed = time.perf_counter() - start, time.process_time() - cpu
    assert not live, "leaked measured connections"
    after = database_state(path)
    assert before["main"] == after["main"], "main fixture mutated"
    return dict(
        warm=warm,
        instrumented=instrumented,
        noop=noop,
        database_before=before,
        database_after=after,
        wall=elapsed,
        cpu=cpu_elapsed,
        callbacks=counts["callbacks"],
        compiler_cache_callbacks=compiler_callbacks,
        search_callbacks=counts["callbacks"] - compiler_callbacks,
        connections=counts["connections"],
        readonly_connections=counts["readonly_connections"],
        cache_hit=hit,
        bundle_coverage={
            key: value
            for key, value in bundle.get("coverage", {}).items()
            if key
            in (
                "input_sources",
                "clauses",
                "interpreted",
                "uninterpreted",
                "facts",
                "complete",
            )
        },
        manifest_hash=manifest_hash,
        timing=dict(meter.rows),
        fixture_unchanged=True,
        fixture_unchanged_scope="Main database bytes only; WAL/SHM lifecycle and bytes captured separately, not asserted identical.",
        sidecars_unchanged=before == after,
        measured_connections_closed=True,
    )


def benchmark(repeats=3):
    if not 1 <= repeats <= 5:
        raise ValueError("repeats must be between 1 and 5")
    # Force pinned imports even if caller uses the requested PYTHONPATH=src.
    if any(name.startswith("sabermetrics") for name in sys.modules):
        raise RuntimeError("run in fresh interpreter to guarantee pinned imports")
    sys.path.insert(0, str(PIN / "src"))
    from sabermetrics import db

    assert Path(db.__file__).resolve().is_relative_to(PIN)
    cards = inputs()
    source_files = [
        "assistant_store.py",
        "assistant_jobs.py",
        "db.py",
        "assistant_deck_inference.py",
        "assistant_inference_facts.py",
        "assistant_inference_search.py",
        "assistant_runtime.py",
        "ui/assistant_routes.py",
    ]
    commit = "12af094c1a5adeeb2a90949b3a00f530b75f82f0"

    def committed(relative):
        return subprocess.check_output(["git", "show", f"{commit}:{relative}"])

    source_hashes = {}
    for name in source_files:
        prepared = (PIN / "src/sabermetrics" / name).read_bytes()
        if prepared != committed("src/sabermetrics/" + name):
            raise RuntimeError("Pinned source mismatch: " + name)
        source_hashes[name] = hashlib.sha256(prepared).hexdigest()
    spec_path = "docs/specs/research-assistant/deck-inference-v1.json"
    spec_bytes = committed(spec_path)
    if (PIN / spec_path).read_bytes() != spec_bytes:
        raise RuntimeError("Pinned spec mismatch")
    runs = []
    payload_sizes = []
    with tempfile.TemporaryDirectory(prefix="e002a-currentness-") as directory:
        for target_bytes in (29000, 1000000, 5000000):
            payload = dict(
                busy=True,
                turn_id="turn",
                messages=[{"role": "assistant", "content": ""}],
            )
            payload["messages"][0]["content"] = "x" * (
                target_bytes - len(json.dumps(payload).encode())
            )
            payload_sizes.append(len(json.dumps(payload).encode()))
            path = Path(directory) / f"probe-{target_bytes}.sqlite"
            fixture(path, payload)
            with db.connect(path):
                pass
            # Discard one full pass per payload/cache/mode before recording samples.
            for repeat in range(-1, repeats):
                for warm in (False, True):
                    modes = [(False, True), (False, False), (True, False)]
                    if repeat % 2:
                        modes.reverse()
                    for instrumented, noop in modes:
                        row = run_one(path, cards, warm, instrumented, noop)
                        if repeat >= 0:
                            runs.append(
                                dict(repeat=repeat, payload_bytes=target_bytes, **row)
                            )
    for size in payload_sizes:
        for warm in (False, True):
            matched = [
                r for r in runs if r["payload_bytes"] == size and r["warm"] == warm
            ]
            for key in (
                "manifest_hash",
                "bundle_coverage",
                "callbacks",
                "compiler_cache_callbacks",
                "search_callbacks",
                "cache_hit",
            ):
                assert (
                    len({json.dumps(r[key], sort_keys=True) for r in matched}) == 1
                ), key
    report = dict(
        experiment="E002a",
        pinned_commit="12af094c1a5adeeb2a90949b3a00f530b75f82f0",
        scope="Synthetic callback-chain reconstruction; not actual full QA runtime or identical frozen E001 input.",
        hypothesis="Currentness callbacks dominate measured preparation (>50% instrumented wall time).",
        repeats=repeats,
        input_hash=digest(
            {
                "cards": cards,
                "rules": {"sources": []},
                "payload_sizes_bytes": payload_sizes,
            }
        ),
        payload_sizes_bytes=payload_sizes,
        warmup_policy="Before measurement discard one complete cold/warm pass of all three modes for each payload size; cold clears compilation cache only.",
        source_verification="All listed prepared source files and canonical spec byte-compared with git show of pinned commit; fail on mismatch.",
        cache_state_hashes={
            mode: digest({"compiler_cache": mode, "input": digest(cards)})
            for mode in ("empty", "prepopulated_same_input")
        },
        cache_hash_note="Logical controlled cache-state recipes, not serialized internal cache hashes.",
        canonical_spec_hash=hashlib.sha256(spec_bytes).hexdigest(),
        source_hashes=source_hashes,
        limitations=[
            "Low-branching graph fixture: search callback counts do not represent maximum expansions.",
            "Wall subtraction is noisy and includes hook overhead; NO-OP removes callback work with unpatched compiler/search. No causal QA attribution.",
            "No original E001 records; synthetic 100 sources/170 sentences (not necessarily compiler clauses) and synthetic session payload.",
            "Cold means empty compilation cache, not OS disk cache; warmed OS pages throughout.",
            "Attribution coverage includes explicitly named residual parent work; it is not evidence of complete low-level causal attribution. Compilation includes source validation without separate attribution.",
            "Overhead ratios are noisy paired observations, may be negative, and are not corrected away.",
            "Manifest is local bundle/search hashing, not full runtime packet assembly.",
            "Canonical source/document validation and packet assembly are unmeasured, not zero; whole validation twice in runtime is outside probe.",
            "Exclusive SQL includes Python wrapper overhead; meter-enabled versus meter-disabled overhead uses identical hooks; neither is an untouched baseline.",
            "Canonical freshness gap remains separate open safety work; revision guard does not establish canonical-source freshness.",
        ],
        fixture_cleanup=not Path(directory).exists(),
        runs=runs,
    )
    summaries = []
    for size in payload_sizes:
        for warm in (False, True):
            matched = [
                r for r in runs if r["warm"] == warm and r["payload_bytes"] == size
            ]
            raw = [r for r in matched if not r["instrumented"] and not r["noop"]]
            measured = [r for r in matched if r["instrumented"]]
            controls = [r for r in matched if r["noop"]]
            total = sum(r["wall"] for r in measured)
            callback = sum(r["timing"]["callback"]["wall"] for r in measured)
            differences = [
                next(r["wall"] for r in raw if r["repeat"] == i)
                - next(r["wall"] for r in controls if r["repeat"] == i)
                for i in range(repeats)
            ]
            summaries.append(
                dict(
                    payload_bytes=size,
                    warm=warm,
                    callback_wall_fraction=callback / total,
                    meter_enabled_vs_disabled_same_hooks_wall_ratio=total
                    / sum(r["wall"] for r in raw)
                    - 1,
                    callback_wall_subtraction_seconds=differences,
                    inclusive_callback_seconds=[
                        r["timing"]["callback"]["wall"] for r in measured
                    ],
                    callback_mean_ms=1000
                    * callback
                    / sum(r["callbacks"] for r in measured),
                    exclusive_attribution_coverage=sum(
                        sum(v["exclusive_wall"] for v in r["timing"].values())
                        for r in measured
                    )
                    / total,
                    dominates_measured_probe=callback / total > 0.5,
                )
            )
    report["qa_scale"] = dict(
        reference_cold_seconds=0.655,
        reference_callbacks=445,
        reference_ms_per_callback=0.655 / 445 * 1000,
        qa_seconds=15.4,
        required_ms_per_callback_if_all_qa_were_callbacks=15.4 / 445 * 1000,
        required_multiple_of_reference=15.4 / 0.655,
        unmeasured_factors=[
            "Actual QA payload size, graph branching, callback count and input shape",
            "OS contention, storage/cache state",
            "Canonical validation, runtime packet assembly, provider and other runtime work",
        ],
        interpretation="Synthetic scale comparison only; no causal QA attribution.",
    )
    report["scale_assessment"] = [
        dict(
            payload_bytes=s["payload_bytes"],
            warm=s["warm"],
            measured_callback_ms=s["callback_mean_ms"],
            additional_ms_per_callback_to_explain_all_qa=15.4 / 445 * 1000
            - s["callback_mean_ms"],
            qa_to_measured_callback_cost_ratio=(15.4 / 445 * 1000)
            / s["callback_mean_ms"],
        )
        for s in summaries
    ]
    report["record_change"] = (
        "Instrumentation and control improvement; no product optimization or score improvement claim."
    )
    report["summary"] = summaries
    report["next_decision"] = (
        "A bounded disposable prototype experiment is justified to test callback-cost reduction; an automatic guard fix is not justified. Capture matched actual QA inputs and unmeasured factors before causal attribution."
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(benchmark(args.repeats), indent=2) + "\n")


if __name__ == "__main__":
    main()
