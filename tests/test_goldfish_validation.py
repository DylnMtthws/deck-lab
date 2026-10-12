import copy

import pytest

from sabermetrics.goldfish_validation import compare_validation, validation_seed


def report(seed, value=0.5, status="supported"):
    return {
        "schema_version": "native.v1",
        "settings": {
            "games": 2000,
            "seed": seed,
            "engine_version": "v1",
            "settings_fingerprint": "same",
        },
        "coverage": {"inert_cards": 1, "unauthored_cards": 0},
        "tests": {
            "opening": {
                "status": status,
                "metrics": {
                    "keep": {
                        "value": value,
                        "unit": "probability",
                        "denominator": 2000,
                        "ci95": {"low": 0.4, "high": 0.6},
                    }
                },
            }
        },
    }


def test_seed_is_deterministic_and_independent():
    assert validation_seed(17) == validation_seed(17)
    assert validation_seed(17) != 17
    assert validation_seed(17) != validation_seed(18)


def test_reused_training_seed_rejected():
    with pytest.raises(ValueError, match="independent"):
        compare_validation(report(17), report(17), training_seed=17)


def test_partial_coverage_is_descriptive_and_frozen():
    seed = validation_seed(17)
    before, after = report(seed), report(seed, 0.9, "partial")
    frozen = copy.deepcopy(after)
    result = compare_validation(before, after, training_seed=17)
    metric = result["comparison"]["metric_deltas"]["opening"]["keep"]
    assert not metric["validated"]
    assert metric["verdict"] == "descriptive"
    assert after == frozen


def test_unknown_is_not_improvement():
    seed = validation_seed(17)
    result = compare_validation(report(seed), report(seed, None), training_seed=17)
    metric = result["comparison"]["metric_deltas"]["opening"]["keep"]
    assert metric["delta"] is None
    assert not metric["validated"]


def test_settings_drift_suppresses_comparison():
    seed = validation_seed(17)
    before, after = report(seed), report(seed, 0.9)
    after["settings"]["engine_version"] = "v2"
    assert not compare_validation(before, after, training_seed=17)["comparison"][
        "comparable"
    ]


@pytest.mark.parametrize("defect", ["owner", "missing_frozen", "invalid_snapshot"])
def test_invalid_frozen_lineage_never_runs_validation(monkeypatch, tmp_path, defect):
    from types import SimpleNamespace

    from sabermetrics import deck_simulation
    from sabermetrics.cedh.simulator import HttpSimulatorClient

    snapshot = {"id": "deck", "owner_id": "owner"}
    prior = {
        "deck_id": "deck",
        "owner_id": "foreign" if defect == "owner" else "owner",
        "status": "done",
    }
    monkeypatch.setattr(
        deck_simulation.db,
        "DeckDocumentSimulationsRepo",
        lambda path: SimpleNamespace(get=lambda id: prior),
    )
    candidate = SimpleNamespace(deck_sha256="new")
    outcome = SimpleNamespace(raw_document={"diagnostics": report(17)})
    stored = {"comparison_id": "prior", "snapshot": snapshot}
    if defect == "invalid_snapshot":
        stored["frozen_baseline"] = {"snapshot": {}, "provenance": {}}
    client = HttpSimulatorClient("http://sim", diagnostics=True)
    monkeypatch.setattr(
        client,
        "simulate",
        lambda candidate: pytest.fail("invalid lineage must not simulate"),
    )
    deck_simulation._attach_heldout_validation(
        tmp_path, stored, candidate, outcome, client
    )
    assert outcome.raw_document["heldout_validation"]["status"] == "unavailable"


def test_trial_count_drift_is_not_comparable():
    seed = validation_seed(17)
    before, after = report(seed), report(seed)
    after["settings"]["games"] = 1000
    result = compare_validation(before, after, training_seed=17)
    assert not result["comparison"]["comparable"]
    assert result["comparison"]["metric_deltas"] == {}


def test_unvalidated_reasons_explain_scope_and_provenance():
    seed = validation_seed(17)
    result = compare_validation(report(seed), report(seed), training_seed=17)
    metric = result["comparison"]["metric_deltas"]["opening"]["keep"]
    assert "Coverage is incomplete" in metric["unvalidated_reason"]
    assert "provenance is missing" in metric["unvalidated_reason"]


def test_fully_supported_same_catalog_metric_can_be_validated_without_verdict():
    seed = validation_seed(17)
    before, after = report(seed), report(seed, 0.6)
    for item in (before, after):
        item["coverage"] = {"inert_cards": 0, "unauthored_cards": 0}
        item["settings"].update(
            policy="pilot", effects_sha256="global-catalog", strategy_pack_sha256="pack"
        )
    metric = compare_validation(before, after, training_seed=17)["comparison"][
        "metric_deltas"
    ]["opening"]["keep"]
    assert metric["validated"]
    assert metric["unvalidated_reason"] == ""
    assert metric["verdict"] == "descriptive"
