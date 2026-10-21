"""Benchmark integrity and scoring tests; never decode heldout labels."""

import hashlib
import json
import shutil

import pytest

from sabermetrics import assistant_quality_evaluation as quality


@pytest.fixture
def dev():
    cases = quality.load_cases()
    labels = json.loads(
        (quality.DEFAULT_CORPUS / "development.labels.json").read_text()
    )
    return cases[0], labels[cases[0]["id"]]


def receipt(case, label):
    """Explicit harness calibration data, not an actual assistant answer."""
    return {
        "case_id": case["id"],
        "status": "completed",
        "candidates": label["relevant_candidates"],
        "recommendations": [
            {
                "card": next(
                    n for n, v in label["recommendation_scores"].items() if v == 2
                ),
                "rationale": "Calibration only",
            }
        ],
        "claims": [{"id": k, "value": v} for k, v in label["claim_values"].items()],
        "packages": label["packages"],
        "cuts": [
            {
                "card": next(n for n, v in label["cut_scores"].items() if v == 2),
                "rationale": "Calibration only",
            }
        ],
        "addressed_topics": case["requested"]["topics"],
    }


def test_frozen_manifest_and_disjoint_public_sources():
    manifest = quality.verify_freeze()
    dev = quality.load_cases("development")
    held = quality.load_cases("heldout")
    assert len(dev) == len(held) == 16
    assert {c["id"] for c in dev}.isdisjoint(c["id"] for c in held)
    assert {c["archetype"] for c in dev}.isdisjoint(c["archetype"] for c in held)

    def cards(cases):
        return {
            e["id"] for c in cases for e in c["evidence"] if e["id"].startswith("card:")
        }

    assert cards(dev).isdisjoint(cards(held))
    assert manifest["candidate_evaluated_before_freeze"] is False
    assert all(c["evidence"] for c in dev + held)


def test_hash_tampering_detected_before_scoring(tmp_path):
    corpus = tmp_path / "corpus"
    shutil.copytree(quality.DEFAULT_CORPUS, corpus)
    path = corpus / "development.cases.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Frozen content changed"):
        quality.load_cases(corpus=corpus)


def test_missing_required_manifest_hash_rejected(tmp_path):
    corpus = tmp_path / "corpus"
    shutil.copytree(quality.DEFAULT_CORPUS, corpus)
    path = corpus / "manifest.json"
    data = json.loads(path.read_text())
    del data["files_sha256"]["heldout.labels.json"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="missing required"):
        quality.verify_freeze(corpus)


def test_public_loading_never_decodes_labels(monkeypatch):
    original = quality._read

    def guarded(path):
        assert not path.name.endswith(".labels.json")
        return original(path)

    monkeypatch.setattr(quality, "_read", guarded)
    assert len(quality.load_cases("heldout")) == 16


def test_heldout_gate_precedes_any_data_read(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Read before heldout authorization")

    monkeypatch.setattr(quality, "_read", fail)
    with pytest.raises(ValueError, match="final_code_freeze"):
        quality.run(lambda c: {}, split="heldout")


def test_independent_metrics_and_perfect_calibration(dev):
    case, label = dev
    row = quality.score_case(case, label, receipt(case, label))
    assert row["candidate_recall"] == 1
    assert row["recommendation_usefulness"] == 1
    assert row["factual_error_rate"] == 0
    assert row["package_coherence_accuracy"] == 1
    assert row["cut_quality"] == 1
    assert row["completed"]
    assert row["resources"]["cost_usd"] is None


def test_absence_is_coverage_failure_not_factual_success(dev):
    case, label = dev
    row = quality.score_case(
        case, label, {"case_id": case["id"], "status": "completed"}
    )
    assert row["candidate_recall"] == 0
    assert row["factual_error_rate"] is None
    assert row["factual_coverage"] == 0
    assert row["package_coherence_accuracy"] is None
    assert row["recommendation_usefulness"] == row["cut_quality"] == 0
    assert not row["completed"]


def test_prose_does_not_become_structured_claims(dev):
    case, label = dev
    row = quality.score_case(
        case,
        label,
        {"case_id": case["id"], "answer": "A confident but unsupported answer"},
    )
    assert row["prose_present_unreviewed"]
    assert row["factual_answered"] == 0


def test_wrong_claim_does_not_hide_behind_recall(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["claims"][0]["value"] = not actual["claims"][0]["value"]
    row = quality.score_case(case, label, actual)
    assert row["candidate_recall"] == 1
    assert row["factual_errors"] == 1
    assert row["factual_error_rate"] == 1


def test_duplicate_and_nonboolean_claims_rejected(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["claims"] *= 2
    with pytest.raises(ValueError, match="Duplicate claim"):
        quality.score_case(case, label, actual)
    actual["claims"] = [{"id": "probe-1", "value": "false"}]
    with pytest.raises(ValueError, match="true, false, or null"):
        quality.score_case(case, label, actual)


def test_protected_cut_package_error_and_unknown_alternative(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["cuts"][0]["card"] = label["protected_cuts"][0]
    actual["recommendations"][0]["card"] = "Unreviewed alternative"
    actual["packages"] = [
        {
            "cards": label["packages"][0]["cards"],
            "coherent": not label["packages"][0]["coherent"],
        }
    ]
    row = quality.score_case(case, label, actual)
    assert row["cut_quality"] == 0
    assert row["protected_cut_violations"]
    assert row["package_errors"] == 1
    assert row["recommendation_unknown"] == ["Unreviewed alternative"]
    assert row["recommendation_usefulness"] == 0


def test_candidate_duplicates_cannot_inflate_recall(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["candidates"] = label["relevant_candidates"] * 100
    assert quality.score_case(case, label, actual)["candidate_recall"] == 1


def test_resource_validation_and_missing_cost(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["telemetry"] = {"latency_ms": 0, "cost_usd": float("nan")}
    with pytest.raises(ValueError, match="finite nonnegative"):
        quality.score_case(case, label, actual)
    actual["telemetry"] = {"latency_ms": 0}
    row = quality.score_case(case, label, actual)
    assert row["resources"]["latency_ms"] == 0
    assert row["resources"]["cost_usd"] is None


def test_callback_failures_count_and_mutation_cannot_change_cases():
    observed = []

    def callback(case):
        case["id"] = "mutated"
        raise RuntimeError("intentional harness failure")

    report = quality.run(callback, captured_receipts=observed)
    assert report["summary"]["cases"] == 16
    assert report["summary"]["completion_rate"] == 0
    assert report["summary"]["factual_answered"] == 0
    assert report["summary"]["resources"]["cost_usd"]["sum"] is None
    assert len(observed) == 16
    assert all(r["case_id"].startswith("dev-") for r in observed)
    assert all("RuntimeError" in r["capture_error"] for r in report["cases"])
    assert "aggregate_score" not in report["summary"]


def test_report_identifies_exact_frozen_manifest():
    report = quality.run(lambda c: {"case_id": c["id"]})
    assert (
        report["manifest_sha256"]
        == hashlib.sha256(
            (quality.DEFAULT_CORPUS / "manifest.json").read_bytes()
        ).hexdigest()
    )
    assert set(report["by_archetype"]) == {
        "sacrifice_recursion",
        "spellslinger",
        "mana_activation",
        "lands",
    }


def test_no_membership_shortcut_in_public_cases():
    for split in ("development", "heldout"):
        for case in quality.load_cases(split):
            assert set(case["context"]["deck"]) == set(
                case["context"]["candidate_pool"]
            )
            assert (
                case["context"]["recommendation_mode"]
                == "prioritize_or_retain_from_current_shell"
            )


def test_same_recommendation_and_cut_cannot_complete(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["cuts"][0]["card"] = actual["recommendations"][0]["card"]
    row = quality.score_case(case, label, actual)
    assert not row["completed"]
    assert not row["completion_checks"]["distinct_recommendation_and_cut"]
    assert row["protected_cut_violations"]


def test_extra_package_does_not_satisfy_exact_deliverable_count(dev):
    case, label = dev
    actual = receipt(case, label)
    actual["packages"] = list(actual["packages"]) + [
        {"cards": ["Unknown"], "coherent": True}
    ]
    row = quality.score_case(case, label, actual)
    assert row["package_unknown"] == 1
    assert not row["completion_checks"]["packages"]
