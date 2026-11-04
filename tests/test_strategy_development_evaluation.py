"""Synthetic scorer unit checks only; not actual assistant quality measurements."""

import copy
import hashlib
import shutil

import pytest

from sabermetrics.strategy_development_evaluation import (
    DEFAULT_SUITE,
    compare,
    evaluate,
    load_suite,
    score_receipt,
)


def observation(case, label):
    # Deliberately synthetic: only tests scoring arithmetic and validation.
    return {
        "case_id": case["id"],
        "suite_manifest_sha256": hashlib.sha256(
            (DEFAULT_SUITE / "manifest.json").read_bytes()
        ).hexdigest(),
        "answer": "Test annotation.",
        "status": "completed",
        "claims": [
            {"id": key, "value": value, "start": 0, "end": 15}
            for key, value in label["claim_values"].items()
        ],
        "preserved": [
            {"id": key, "start": 0, "end": 15} for key in label["preserve_ids"]
        ],
        "topics": [
            {"id": key, "start": 0, "end": 15} for key in case["requested_topics"]
        ],
        "telemetry": {"latency_ms": 100, "cost_usd": 0},
    }


def test_frozen_development_scope_and_honest_authorship(tmp_path):
    cases, labels, manifest = load_suite()
    assert len(cases) == len(labels) == 10
    assert not manifest["independent_author"] and not manifest["expert_reviewed"]
    root = tmp_path / "suite"
    shutil.copytree(DEFAULT_SUITE, root)
    (root / "cases.json").write_text("[]")
    with pytest.raises(ValueError, match="Frozen bytes"):
        load_suite(root)


def test_empty_run_has_no_factual_or_latency_credit():
    result = evaluate([])
    assert result["status"] == "no_actual_receipts"
    assert result["conditional_truth_accuracy"] is None
    assert result["claim_coverage"] == result["fulfillment_rate"] == 0
    assert result["mean_usefulness_loss"] == 1
    assert result["latency_ms"]["p95"] is None
    assert result["cost_usd"]["total_observed"] is None


@pytest.mark.parametrize("digest", [None, "", "wrong-suite", "0" * 64])
def test_unbound_or_wrong_suite_receipts_fail_closed(digest):
    cases, labels, _ = load_suite()
    receipt = observation(cases[0], labels[0])
    if digest is None:
        receipt.pop("suite_manifest_sha256")
    else:
        receipt["suite_manifest_sha256"] = digest
    original = copy.deepcopy(receipt)
    with pytest.raises(ValueError, match="capture-time"):
        evaluate([receipt])
    assert receipt == original


def test_matching_receipt_digest_is_retained_without_mutation():
    cases, labels, _ = load_suite()
    receipt = observation(cases[0], labels[0])
    original = copy.deepcopy(receipt)
    assert (
        evaluate([receipt])["suite_manifest_sha256"] == receipt["suite_manifest_sha256"]
    )
    assert receipt == original


def test_preservation_coverage_and_speed_regression_are_separate():
    cases, labels, _ = load_suite()
    before = [observation(case, label) for case, label in zip(cases, labels)]
    after = copy.deepcopy(before)
    after[0]["claims"][0]["value"] = not after[0]["claims"][0]["value"]
    after[0]["preserved"] = []
    after[0]["telemetry"]["latency_ms"] = 10
    baseline, candidate = evaluate(before), evaluate(after)
    assert baseline["fulfillment_rate"] == 1
    assert candidate["factual_errors"] == 1
    assert candidate["mean_usefulness_loss"] > 0
    comparison = compare(baseline, candidate)
    assert comparison["mean_latency_delta_ms"] == -9
    assert comparison["quality_regressions"] == 1
    after[0]["claims"] = []
    assert compare(baseline, evaluate(after))["quality_regressions"] == 1


@pytest.mark.parametrize(
    "mutation", ["duplicate", "span", "bool_span", "claim_type", "telemetry", "nan"]
)
def test_invalid_observations_fail(mutation):
    cases, labels, _ = load_suite()
    receipt = observation(cases[0], labels[0])
    if mutation == "duplicate":
        receipt["claims"].append(receipt["claims"][0])
    elif mutation == "span":
        receipt["claims"][0]["end"] = 500
    elif mutation == "bool_span":
        receipt["claims"][0]["start"] = False
    elif mutation == "claim_type":
        receipt["claims"][0]["value"] = 1
    elif mutation == "telemetry":
        receipt["telemetry"]["latency_ms"] = -1
    else:
        receipt["telemetry"]["cost_usd"] = float("nan")
    with pytest.raises(ValueError):
        score_receipt(cases[0], labels[0], receipt)


def test_unknowns_and_abstentions_do_not_receive_credit():
    cases, labels, _ = load_suite()
    receipt = observation(cases[0], labels[0])
    receipt["claims"][0]["value"] = None
    receipt["claims"].append({"id": "unreviewed", "value": True, "start": 0, "end": 15})
    result = score_receipt(cases[0], labels[0], receipt)
    assert result["claim_coverage"] < 1
    assert result["unreviewed_annotations"] == ["unreviewed"]
    assert not result["fulfilled"]
    with pytest.raises(ValueError, match="Duplicate or unknown"):
        evaluate([receipt, receipt])
    with pytest.raises(ValueError, match="keys"):
        evaluate({"wrong-id": receipt})
