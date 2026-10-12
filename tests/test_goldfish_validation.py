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
