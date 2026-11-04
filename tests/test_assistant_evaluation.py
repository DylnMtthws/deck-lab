"""The evaluator measures labeled cases and never reports success by default."""

import copy
import json
from pathlib import Path

import pytest

from sabermetrics.assistant_evaluation import (
    SUITE_VERSION,
    SuiteError,
    exit_code,
    load_suite,
    lookup,
    run_suite,
    validate_suite,
)
from scripts.evaluate_assistant_intelligence import main

SUITE = Path(__file__).parent / "fixtures" / "assistant_intelligence_eval.json"


@pytest.fixture(scope="module")
def baseline():
    suite = load_suite(SUITE)
    return suite, run_suite(suite)


def _subset(suite, kind):
    chosen = copy.deepcopy(suite)
    chosen["cases"] = [case for case in chosen["cases"] if case["kind"] == kind]
    return chosen


def _write(tmp_path, suite):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite))
    return path


def test_baseline_report_is_measured_and_internally_consistent(baseline):
    suite, report = baseline
    totals = report["totals"]
    assert totals["cases"] == len(suite["cases"]) >= 20
    assert totals["cases"] == sum(
        totals[key] for key in ("passed", "failed", "errored", "unavailable")
    )
    assert report["suite_sha256"] and report["suite_version"] == SUITE_VERSION
    for group in ("by_kind", "by_capability", "by_label"):
        assert sum(b["denominator"] for b in report[group].values()) >= totals["cases"]
    assert set(report["by_kind"]) == {"memory", "retrieval", "requirements"}
    # Withheld and stated intent labels are measured separately.
    assert report["by_label"]["intent=withheld"]["denominator"] >= 3
    assert report["by_label"]["intent=stated"]["denominator"] >= 2
    assert report["retrieval_ranking"]["denominator"] >= 5
    assert all(r["latency_ms"] >= 0 for r in report["cases"])
    assert (
        report["latency_ms"]["memory"]["count"]
        == report["by_kind"]["memory"]["denominator"]
    )
    failed_ids = {f["id"] for f in report["failures"]}
    assert failed_ids == {r["id"] for r in report["cases"] if r["outcome"] != "passed"}
    for failure in report["failures"]:
        assert failure["checks"] or failure["detail"]
    assert exit_code(report) == (0 if not failed_ids else 1)


def test_memory_cases_pass_completely(baseline):
    _suite, report = baseline
    memory = report["by_kind"]["memory"]
    assert memory["denominator"] == 12
    assert memory["passed"] == memory["denominator"]
    for capability in ("memory.ownership", "memory.freshness", "memory.bounds"):
        assert report["by_capability"][capability]["pass_rate"] == 1.0


def test_empty_suite_fails(tmp_path):
    report = run_suite({"version": SUITE_VERSION, "cases": []})
    assert report["status"] == "fail"
    assert "Empty suite" in report["reason"]
    assert exit_code(report) == 1
    path = _write(tmp_path, {"version": SUITE_VERSION, "cases": []})
    assert main(["--suite", str(path)]) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda case: case.pop("checks"),
        lambda case: case.update(checks=[]),
        lambda case: case.pop("expect_abstention"),
        lambda case: case.update(kind="vibes"),
        lambda case: case["checks"][0].update(op="looks_good"),
        lambda case: case["input"].update(scenario="missing"),
    ],
)
def test_malformed_cases_are_rejected_before_running(baseline, mutate):
    suite, _report = baseline
    broken = copy.deepcopy(suite)
    mutate(broken["cases"][0])
    with pytest.raises(SuiteError):
        validate_suite(broken)


def test_duplicate_case_ids_are_rejected(baseline):
    suite, _report = baseline
    broken = copy.deepcopy(suite)
    broken["cases"].append(copy.deepcopy(broken["cases"][0]))
    with pytest.raises(SuiteError):
        validate_suite(broken)


def test_wrong_expectation_gives_exact_failure_and_nonzero_cli_exit(baseline, tmp_path):
    suite, _report = baseline
    wrong = _subset(suite, "memory")
    wrong["cases"] = wrong["cases"][:1]
    wrong["cases"][0]["checks"] = [
        {"path": "experiments.items[1].freshness", "op": "equals", "value": "stale"}
    ]
    out = tmp_path / "report.json"
    assert main(["--suite", str(_write(tmp_path, wrong)), "--report", str(out)]) == 1
    report = json.loads(out.read_text())
    assert report["failures"] == [
        {
            "id": wrong["cases"][0]["id"],
            "kind": "memory",
            "outcome": "failed",
            "detail": "",
            "checks": [
                {
                    "path": "experiments.items[1].freshness",
                    "op": "equals",
                    "expected": "stale",
                    "actual": "current",
                }
            ],
        }
    ]


def test_passing_memory_suite_exits_zero(baseline, tmp_path):
    suite, _report = baseline
    assert main(["--suite", str(_write(tmp_path, _subset(suite, "memory")))]) == 0


def test_unavailable_database_is_reported_not_passed(tmp_path):
    suite = {
        "version": SUITE_VERSION,
        "cases": [
            {
                "id": "db-retrieval",
                "kind": "retrieval",
                "capability": "retrieval.database",
                "expect_abstention": False,
                "input": {"corpus": "database", "query": "landfall"},
                "checks": [{"path": "status", "op": "equals", "value": "available"}],
            }
        ],
    }
    for db_path in (None, tmp_path / "missing.db"):
        report = run_suite(suite, db_path=db_path)
        assert report["status"] == "unavailable"
        assert report["totals"]["unavailable"] == 1
        assert report["totals"]["passed"] == 0
        assert report["failures"][0]["outcome"] == "unavailable"
        assert exit_code(report) == 2
    assert (
        run_suite(suite, db_path=tmp_path / "missing.db")["database"]["status"]
        == "unavailable"
    )
    assert not (tmp_path / "missing.db").exists()


def test_api_exception_is_an_error_not_a_pass():
    suite = {
        "version": SUITE_VERSION,
        "cases": [
            {
                "id": "bad-input",
                "kind": "requirements",
                "capability": "requirements.archetype",
                "expect_abstention": False,
                "input": {"library": []},
                "checks": [{"path": "archetype", "op": "equals", "value": "general"}],
            }
        ],
    }
    report = run_suite(suite)
    assert report["totals"]["errored"] == 1
    assert report["failures"][0]["detail"].startswith("KeyError")
    assert exit_code(report) == 1


def test_abstention_mismatch_and_latency_budget_fail(baseline):
    suite, _report = baseline
    case = copy.deepcopy(
        next(c for c in suite["cases"] if c["id"] == "memory-no-history")
    )
    case["expect_abstention"] = False
    case["max_latency_ms"] = 10
    ticks = iter(range(0, 1000, 1))  # every clock read advances one second
    report = run_suite({**suite, "cases": [case]}, clock=lambda: float(next(ticks)))
    paths = [check["path"] for check in report["failures"][0]["checks"]]
    assert paths == ["<abstention>", "<latency_ms>"]
    assert report["abstentions"]["false_abstentions"] == ["memory-no-history"]


def test_lookup_paths_distinguish_missing_from_null():
    value = {"a": [{"b": None}], "c": {"d": 1}}
    assert lookup(value, "a[0].b") is None
    assert lookup(value, "c.d") == 1
    assert repr(lookup(value, "a[1].b")) == repr(lookup(value, "c.missing"))
    assert lookup(value, "a[1].b") is not None
