"""Targeted offline benchmark contract tests, isolated from app imports."""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/benchmark_research_currentness.py"
)
spec = importlib.util.spec_from_file_location("currentness_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_nested_timing_partition():
    meter = probe.Meter()
    with meter.span("outer"), meter.span("inner"):
        sum(range(1000))
    outer, inner = meter.rows["outer"], meter.rows["inner"]
    for clock in ("wall", "cpu"):
        assert (
            abs(
                outer["exclusive_" + clock] + inner["exclusive_" + clock] - outer[clock]
            )
            < 1e-9
        )
    assert inner["count"] == 1


def test_disabled_has_no_fake_attribution():
    meter = probe.Meter(False)
    with meter.span("anything"):
        pass
    assert not meter.rows


def test_actual_pinned_compiler_search_counts_and_cleanup():
    with tempfile.TemporaryDirectory() as directory:
        _check_profile(Path(directory))


def _check_profile(tmp_path):
    output = tmp_path / "profile.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--repeats", "1", "--output", str(output)],
        check=True,
    )
    report = json.loads(output.read_text())
    assert report["fixture_cleanup"]
    assert report["payload_sizes_bytes"] == [29000, 1000000, 5000000]
    assert "assistant_jobs.py" in report["source_hashes"]
    assert "discard" in report["warmup_policy"]
    assert len(report["runs"]) == 18
    for row in report["runs"]:
        assert row["connections"] == (0 if row["noop"] else row["callbacks"] * 3)
        assert row["readonly_connections"] == (0 if row["noop"] else row["callbacks"])
        assert row["compiler_cache_callbacks"] > 0
        assert row["search_callbacks"] > 0
        assert row["fixture_unchanged"] and row["measured_connections_closed"]
        assert row["database_before"]["main"] == row["database_after"]["main"]
        assert set(row["database_after"]) == {"main", "-wal", "-shm"}
        if row["instrumented"]:
            assert row["timing"]["payload_decode"]["count"] == (
                0 if row["noop"] else row["callbacks"]
            )
            assert row["timing"]["connection_open"]["count"] == row["connections"]
            assert row["timing"]["pragma"]["count"] == row["callbacks"] * 8
        else:
            assert row["timing"] == {}
    for warm in (False, True):
        matched = [r for r in report["runs"] if r["warm"] == warm]
        assert len({r["manifest_hash"] for r in matched}) == 1
        assert len({r["callbacks"] for r in matched}) == 1
        assert all(r["cache_hit"] == warm for r in matched)
    assert all(s["exclusive_attribution_coverage"] <= 1 for s in report["summary"])


def test_repeat_bound():
    for count in (0, 6):
        try:
            probe.benchmark(count)
        except ValueError:
            pass
        else:
            raise AssertionError("unbounded repeat count accepted")
