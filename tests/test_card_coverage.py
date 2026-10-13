"""Coverage taxonomy is evidence, not a reinterpretation of legacy author labels."""

import copy
import json

import pytest

from sabermetrics.assistant_runtime import _model_receipt
from sabermetrics.assistant_tools import TOOL_ADAPTER
from sabermetrics.cedh.simulator import SimulatorContractError, _validate_card_coverage


def coverage():
    categories = ("supported", "partial", "unsupported", "inactive")
    return {
        "total_cards": 4,
        "card_coverage": {
            "version": 1,
            "unit": "card_slots",
            "counts": dict.fromkeys(categories, 1),
            "cards": [
                {
                    "slot": i,
                    "name": "Example " + str(i),
                    "status": status,
                    "supported_effects": ["mana"] if status in categories[:2] else [],
                    "limitations": ["combat not implemented"],
                }
                for i, status in enumerate(categories)
            ],
        },
    }


def test_optional_coverage_reconciles_without_reclassifying_legacy():
    _validate_card_coverage({"total_cards": 100, "unauthored_cards": 75})
    _validate_card_coverage(coverage())


@pytest.mark.parametrize("bad", ["total", "count", "duplicate", "status"])
def test_inconsistent_slot_audits_rejected(bad):
    value = coverage()
    if bad == "total":
        value["total_cards"] = 5
    elif bad == "count":
        value["card_coverage"]["counts"]["partial"] = 2
    elif bad == "duplicate":
        value["card_coverage"]["cards"][1]["slot"] = 0
    else:
        value["card_coverage"]["cards"][1]["status"] = "supported"
    with pytest.raises(SimulatorContractError, match="reconcile"):
        _validate_card_coverage(value)


def test_receipt_keeps_counts_but_omits_large_per_card_details_explicitly():
    value = coverage()
    value["card_coverage"]["cards"] *= 100
    receipt = {"name": "latest_goldfish", "data": {"view": {}, "coverage": value}}
    original = copy.deepcopy(receipt)
    compact = _model_receipt(receipt)
    audit = compact["data"]["coverage"]["card_coverage"]
    assert audit["counts"] == value["card_coverage"]["counts"]
    assert audit["omitted_slots"] == 400
    assert "cards" not in audit
    assert "goldfish_card_coverage" in json.dumps(compact["evidence_omitted"])
    assert receipt == original
    assert len(json.dumps(compact)) < 2000


def test_per_card_lookup_is_bounded_and_read_only():
    call = TOOL_ADAPTER.validate_python(
        {"name": "goldfish_card_coverage", "arguments": {"query": "Vivi", "limit": 2}}
    )
    assert call.arguments.limit == 2
    with pytest.raises(ValueError):
        TOOL_ADAPTER.validate_python(
            {"name": "goldfish_card_coverage", "arguments": {"limit": 100}}
        )


def test_coverage_ui_disclosures_and_legacy_uncertainty():
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("node"):
        pytest.skip("node unavailable")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "node",
            str(root / "tests/coverage_harness.mjs"),
            str(root / "src/sabermetrics/ui/static/deck-lab-coverage.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
