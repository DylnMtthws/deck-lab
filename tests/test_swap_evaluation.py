import pytest

from sabermetrics.swap_evaluation import evaluate_swap


def report(seed, value, width=0.01):
    return {
        "schema_version": 1,
        "settings": {
            "seed": seed,
            "settings_fingerprint": f"seed-{seed}",
            "engine_version": "v1",
            "policy": "p",
            "effects_sha256": "e",
            "strategy_pack_sha256": "s",
        },
        "coverage": {"total_cards": 100, "inert_cards": 90},
        "tests": {
            "opening": {
                "status": "partial",
                "comparison_coverage": {
                    "version": 1,
                    "scope": "native_opening_policy",
                    "algorithm": "opening-policy-dependencies.v2",
                    "dependency_accounting_complete": True,
                    "dependency_fingerprint": "a" * 64,
                },
                "metrics": {
                    "qualified_opening_rate": {
                        "value": value,
                        "unit": "proportion",
                        "denominator": 2000,
                        "ci95": {"low": value - width, "high": value + width},
                    }
                },
            }
        },
    }


def evaluate(before=None, after=None, **kwargs):
    return evaluate_swap(
        before or report(2, 0.5),
        after or report(2, 0.7),
        objective=kwargs.pop("objective", "qualified_opening_rate"),
        baseline_id="deck",
        candidate_id="copy",
        baseline_fingerprint="b" * 64,
        candidate_fingerprint="c" * 64,
        training_before=kwargs.pop("training_before", report(1, 0.5)),
        training_after=kwargs.pop("training_after", report(1, 0.7)),
        **kwargs,
    )


def test_bounded_opening_improvement_has_identity_and_no_overall_upgrade():
    result = evaluate()
    assert result["verdict"] == "improved"
    assert result["evidence"]["scope"] == "native_opening_policy"
    assert result["candidate_id"] == "copy"
    assert result["baseline_fingerprint"] == "b" * 64
    assert result["overall_upgrade"] is False
    assert result["no_regression_demonstrated"] is False


def test_regression_agrees_in_independent_trials():
    result = evaluate(after=report(2, 0.3), training_after=report(1, 0.3))
    assert result["verdict"] == "regressed"


@pytest.mark.parametrize("value,width", [(0.51, 0.01), (0.7, 0.2), (0.5, 0.01)])
def test_small_or_noisy_change_abstains(value, width):
    assert evaluate(after=report(2, value, width))["verdict"] == "indeterminate"


def test_reused_seed_or_disagreement_abstains():
    assert (
        evaluate(training_before=report(2, 0.5), training_after=report(2, 0.7))[
            "verdict"
        ]
        == "indeterminate"
    )
    assert evaluate(training_after=report(1, 0.3))["verdict"] == "indeterminate"


@pytest.mark.parametrize(
    "mutation",
    ["dependency", "settings", "unsupported", "missing_interval", "nan", "few_trials"],
)
def test_untrustworthy_measurement_never_improves(mutation):
    after = report(2, 0.7)
    metric = after["tests"]["opening"]["metrics"]["qualified_opening_rate"]
    if mutation == "dependency":
        after["tests"]["opening"]["comparison_coverage"]["dependency_fingerprint"] = (
            "d" * 64
        )
    elif mutation == "settings":
        after["settings"]["effects_sha256"] = "changed"
    elif mutation == "unsupported":
        after["tests"]["opening"]["status"] = "unsupported"
    elif mutation == "missing_interval":
        metric.pop("ci95")
    elif mutation == "nan":
        metric["ci95"]["high"] = float("nan")
    else:
        metric["denominator"] = 2
    assert evaluate(after=after)["verdict"] in {"incomparable", "indeterminate"}


def test_stale_and_unknown_objectives_abstain():
    assert evaluate(stale=True)["verdict"] == "stale"
    assert evaluate(objective="multiplayer_win_rate")["verdict"] == "indeterminate"


def test_partial_deck_without_scoped_proof_abstains():
    before, after = report(2, 0.5), report(2, 0.7)
    for item in (before, after):
        item["tests"]["opening"].pop("comparison_coverage")
    assert evaluate(before, after)["verdict"] == "indeterminate"


def test_partial_opening_primary_label_and_scope_remain_explicit():
    result = evaluate()
    assert result["display_label"] == "Opening-policy proxy improved"
    assert result["scope"] == "native_opening_policy"
    assert result["overall_upgrade"] is False
    assert result["evidence"]["interval_direction"] == "positive_means_improvement"
    assert result["evidence"]["improvement_delta"] == pytest.approx(0.2)
    reversed_result = evaluate(after=report(2, 0.3), training_after=report(1, 0.3))
    assert reversed_result["display_label"] == "Opening-policy proxy regressed"


def test_other_objectives_expose_full_coverage_requirement():
    result = evaluate(objective="positive_finish_proof_rate")
    assert result["coverage_requirement"] == "complete_supported_effect_coverage"
    assert result["verdict"] != "improved"


def test_lower_is_better_objective_exposes_consistent_improvement_sign():
    def land_report(seed, value):
        item = report(seed, value)
        test = item["tests"].pop("opening")
        test.pop("comparison_coverage")
        test["status"] = "supported"
        metric = test["metrics"].pop("qualified_opening_rate")
        metric["unit"] = "lands/game"
        test["metrics"]["mean_missed_land_drops"] = metric
        item["tests"]["development"] = test
        item["coverage"] = {
            "total_cards": 1,
            "card_coverage": {
                "version": 1,
                "unit": "card_slots",
                "counts": {
                    "supported": 1,
                    "partial": 0,
                    "unsupported": 0,
                    "inactive": 0,
                },
                "cards": [{"slot": 0, "status": "supported"}],
            },
        }
        return item

    result = evaluate_swap(
        land_report(2, 2),
        land_report(2, 1),
        objective="mean_missed_land_drops",
        baseline_id="b",
        candidate_id="c",
        baseline_fingerprint="b" * 64,
        candidate_fingerprint="c" * 64,
        training_before=land_report(1, 2),
        training_after=land_report(1, 1),
    )
    assert result["verdict"] == "improved"
    assert result["evidence"]["delta"] == -1
    assert result["evidence"]["improvement_delta"] == 1
    assert result["evidence"]["difference_interval"]["low"] > 0
    assert result["display_label"] == "Selected measured objective improved"


def test_native_zero_success_wilson_roundoff_does_not_hide_improvement():
    before, training = report(2, 0), report(1, 0)
    # Exact native Wilson(0, 2000), z=1.959963984540054 output.
    for item in (before, training):
        metric = item["tests"]["opening"]["metrics"]["qualified_opening_rate"]
        metric.update(
            numerator=0,
            ci95={"low": 1.0842021724855044e-19, "high": 0.001917047281252934},
        )
    result = evaluate(
        before=before,
        after=report(2, 0.1),
        training_before=training,
        training_after=report(1, 0.1),
    )
    assert result["verdict"] == "improved"
    assert result["display_label"] == "Opening-policy proxy improved"
    assert result["evidence"]["difference_interval"]["high"] == pytest.approx(0.11)


@pytest.mark.parametrize(
    "low,high", [(0.01, 0.02), (0.01, -0.01), (float("nan"), 0.01)]
)
def test_real_interval_inconsistency_is_not_rounding(low, high):
    before = report(2, 0)
    before["tests"]["opening"]["metrics"]["qualified_opening_rate"]["ci95"] = {
        "low": low,
        "high": high,
    }
    assert evaluate(before=before)["verdict"] == "indeterminate"
