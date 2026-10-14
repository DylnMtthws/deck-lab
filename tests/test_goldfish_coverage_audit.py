"""The audit reconciles exact slots and never fabricates ability execution."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "coverage_audit", Path(__file__).parents[1] / "scripts/audit_goldfish_coverage.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def report():
    rows = [
        {
            "slot": 0,
            "name": "Unknown",
            "status": "unsupported",
            "supported_effects": [],
            "limitations": ["missing"],
        },
        {
            "slot": 1,
            "name": "Draw",
            "status": "partial",
            "supported_effects": ["Draw"],
            "limitations": ["unverified"],
        },
    ]
    return {
        "coverage": {
            "total_cards": 2,
            "card_coverage": {
                "version": 1,
                "unit": "card_slots",
                "cards": rows,
                "counts": {
                    "supported": 0,
                    "partial": 1,
                    "unsupported": 1,
                    "inactive": 0,
                },
            },
        },
        "tests": {
            "development": {
                "status": "partial",
                "metrics": {"draw": {"value": None, "unit": "cards"}},
                "samples": [{"turns": [{}]}],
            }
        },
    }


def test_exact_slot_counts_and_unknown_trace_abilities():
    result = module.audit_report(report())
    assert result["card_status_counts"] == {
        "supported": 0,
        "partial": 1,
        "unsupported": 1,
        "inactive": 0,
    }
    assert result["executable_definition_slot_counts"] == {"Draw": 1}
    assert result["unsupported_card_slot_counts"] == {"Unknown": 1}
    assert result["ability_inventory"]["status"] == "unavailable"
    scenario = result["scenarios"]["development"]
    assert scenario["sampled_turn_rows"] == 1
    assert scenario["metrics"]["draw"]["measured"] is False
    assert scenario["effect_execution_proof"].startswith("unavailable")


@pytest.mark.parametrize("mutation", ["counts", "missing", "duplicate", "status"])
def test_inconsistent_audit_rejected(mutation):
    value = report()
    detail = value["coverage"]["card_coverage"]
    if mutation == "counts":
        detail["counts"]["partial"] = 2
    elif mutation == "missing":
        detail["cards"].pop()
    elif mutation == "duplicate":
        detail["cards"][1]["slot"] = 0
    else:
        detail["cards"][0]["status"] = "unknown-new-status"
    with pytest.raises(ValueError):
        module.audit_report(value)


def test_unsupported_evidence_manifest_cannot_promote_card():
    value = report()
    detail = value["coverage"]["card_coverage"]
    detail["cards"][1]["status"] = "supported"
    detail["counts"].update(supported=1, partial=0)
    result = module.audit_report(value)
    assert not result["support_release_gate"]["passed"]
    assert result["effective_status_counts"]["supported"] == 0
    assert result["effective_status_counts"]["partial"] == 1


def test_support_gate_requires_hash_bound_positive_and_negative_evidence(tmp_path):
    value = report()
    detail = value["coverage"]["card_coverage"]
    detail["cards"][1]["status"] = "supported"
    detail["counts"].update(supported=1, partial=0)
    source = tmp_path / "source.txt"
    source.write_text("definition")
    evidence_file = tmp_path / "tests.json"
    evidence_file.write_text('{"positive":"passed","negative":"passed"}')
    evidence = {
        "report_sha256": "a" * 64,
        "sources": [
            {
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "claims": {
            "1": {
                "tests": [
                    {
                        "kind": kind,
                        "outcome": "passed",
                        "evidence_path": str(evidence_file),
                        "sha256": hashlib.sha256(
                            evidence_file.read_bytes()
                        ).hexdigest(),
                    }
                    for kind in ["positive", "negative_or_interaction"]
                ]
            }
        },
    }
    assert module.audit_report(value, evidence, report_sha256="a" * 64)[
        "support_release_gate"
    ]["passed"]
    source.write_text("changed definition")
    assert not module.audit_report(value, evidence, report_sha256="a" * 64)[
        "support_release_gate"
    ]["passed"]
