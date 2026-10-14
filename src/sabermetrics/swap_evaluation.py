"""Conservative, bounded objective evidence for immutable swap experiments.

Intervals are marginal 95% intervals, not paired samples or familywise proof.
No native solo metric establishes an overall deck upgrade.
"""

from __future__ import annotations

import math
from typing import Any

from sabermetrics.goldfish_validation import _coverage_complete
from sabermetrics.native_diagnostics_comparison import compare_reports

OBJECTIVES = {
    "qualified_opening_rate": ("opening", "qualified_opening_rate", 1, 0.01),
    "mean_missed_land_drops": ("development", "mean_missed_land_drops", -1, 0.1),
    "positive_finish_proof_rate": ("finish", "positive_finish_proof_rate", 1, 0.01),
    "engine_online_rate": ("development", "engine_online_rate", 1, 0.01),
    "recovery_draw_rate": ("commander_loss", "recovery_draw_rate", 1, 0.01),
    "recovery_board_rate": ("creature_wipe", "recovery_board_rate", 1, 0.01),
}


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (float, int))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _evidence(before: dict, after: dict, objective: str) -> dict[str, Any]:
    test, name, direction, threshold = OBJECTIVES[objective]
    comparison = compare_reports(after, before)
    delta = comparison.get("metric_deltas", {}).get(test, {}).get(name, {})
    result = {
        "comparable": bool(delta.get("comparable")),
        "reason": delta.get("reason") or comparison.get("reason"),
        "scope": delta.get("comparison_scope"),
        "delta": delta.get("delta"),
        "improvement_delta": (
            direction * delta["delta"] if _finite(delta.get("delta")) else None
        ),
        "interval_direction": "positive_means_improvement",
        "direction": direction,
        "minimum_improvement": threshold,
        "difference_interval": None,
        "verdict": "indeterminate",
    }
    if not result["comparable"]:
        result["verdict"] = "incomparable"
        return result
    identity = ("engine_version", "policy", "effects_sha256", "strategy_pack_sha256")
    scoped_opening = (
        delta.get("comparison_scope") == "native_opening_policy" and test == "opening"
    )
    if (
        not scoped_opening
        and (
            delta.get("status") != "supported"
            or not all(_coverage_complete(r) for r in (before, after))
        )
        or not all(
            before.get("settings", {}).get(k)
            and before["settings"][k] == after.get("settings", {}).get(k)
            for k in identity
        )
    ):
        result["reason"] = (
            "Supported tests, complete explicit effect coverage and matching engine/policy/catalog provenance are required."
        )
        return result
    metrics = [
        r.get("tests", {}).get(test, {}).get("metrics", {}).get(name, {})
        for r in (before, after)
    ]
    intervals = []
    for metric in metrics:
        ci = metric.get("ci95", {})
        value = metric.get("value")
        # Native Wilson arithmetic can return low=1e-19 for a zero estimate.
        # Permit floating-point roundoff only, and widen outward to include the
        # estimate. Never create an absent interval or tighten supplied bounds.
        finite_interval = isinstance(ci, dict) and all(
            _finite(ci.get(k)) for k in ("low", "high")
        )
        tolerance = (
            8 * math.ulp(max(1.0, abs(value), abs(ci["low"]), abs(ci["high"])))
            if finite_interval and _finite(value)
            else 0.0
        )
        if (
            type(metric.get("denominator")) is not int
            or metric["denominator"] < 100
            or not isinstance(ci, dict)
            or not all(_finite(ci.get(k)) for k in ("low", "high"))
            or not _finite(metric.get("value"))
            or ci["low"] > ci["high"]
            or not ci["low"] - tolerance <= value <= ci["high"] + tolerance
        ):
            result["reason"] = (
                "At least 100 measured trials and finite ordered uncertainty intervals containing each estimate are required."
            )
            return result
        intervals.append({"low": min(ci["low"], value), "high": max(ci["high"], value)})
    old, new = intervals
    low, high = new["low"] - old["high"], new["high"] - old["low"]
    if direction == -1:
        low, high = -high, -low
    result["difference_interval"] = {"low": low, "high": high}
    result["verdict"] = (
        "improved"
        if low > threshold
        else "regressed" if high < -threshold else "indeterminate"
    )
    result["reason"] = (
        "Marginal intervals separated beyond the objective threshold."
        if result["verdict"] != "indeterminate"
        else "Uncertainty overlaps the minimum meaningful change; absence of regression is not demonstrated."
    )
    return result


def _evaluate_swap(
    before: dict,
    after: dict,
    *,
    objective: str,
    baseline_id: str,
    candidate_id: str,
    baseline_fingerprint: str,
    candidate_fingerprint: str,
    training_before: dict | None = None,
    training_after: dict | None = None,
    stale: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": 1,
        "objective": objective,
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "baseline_fingerprint": baseline_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
        "overall_upgrade": False,
        "no_regression_demonstrated": False,
        "verdict": "indeterminate",
        "note": "Objective-specific solo evidence only. Other objectives, theme, power and multiplayer performance remain unverified. Marginal interval separation is conservative, not paired or multiplicity-adjusted inference.",
    }
    if stale:
        return {
            **result,
            "verdict": "stale",
            "reason": "Source revision or deck fingerprint changed.",
        }
    if objective not in OBJECTIVES:
        return {
            **result,
            "reason": "Requested objective has no supported measurement contract.",
        }
    if (
        not all(
            isinstance(v, str) and v
            for v in (
                baseline_id,
                candidate_id,
                baseline_fingerprint,
                candidate_fingerprint,
            )
        )
        or baseline_fingerprint == candidate_fingerprint
    ):
        return {
            **result,
            "verdict": "incomparable",
            "reason": "Distinct frozen deck fingerprints and exact identities are required.",
        }
    heldout = _evidence(before, after, objective)
    result.update(
        verdict=heldout["verdict"], reason=heldout["reason"], evidence=heldout
    )
    result["settings_fingerprint"] = before.get("settings", {}).get(
        "settings_fingerprint"
    )
    if result["verdict"] in {"improved", "regressed"}:
        if not isinstance(training_before, dict) or not isinstance(
            training_after, dict
        ):
            return {
                **result,
                "verdict": "indeterminate",
                "reason": "Independent training and held-out agreement is unavailable.",
            }
        training = _evidence(training_before, training_after, objective)
        result["training_evidence"] = training
        old_settings, settings = (
            training_before.get("settings", {}),
            before.get("settings", {}),
        )
        same_profile = {
            k: v
            for k, v in old_settings.items()
            if k not in {"seed", "settings_fingerprint"}
        } == {
            k: v
            for k, v in settings.items()
            if k not in {"seed", "settings_fingerprint"}
        }
        if (
            training["verdict"] != heldout["verdict"]
            or old_settings.get("seed") is None
            or old_settings.get("seed") == settings.get("seed")
            or not same_profile
        ):
            result.update(
                verdict="indeterminate",
                reason="Training and independent held-out evidence do not establish the same meaningful change under one profile.",
            )
    return result


def present_outcome(result: dict[str, Any]) -> dict[str, Any]:
    """Expose the scope in the primary label, including limited policy proxies."""
    scope = result.get("evidence", {}).get("scope") or "unverified"
    verdict = result.get("verdict", "indeterminate")
    subject = (
        "Opening-policy proxy"
        if scope == "native_opening_policy"
        else "Selected measured objective"
    )
    labels = {
        "improved": subject + " improved",
        "regressed": subject + " regressed",
        "indeterminate": subject + ": inconclusive",
        "incomparable": "Measurements are not comparable",
        "stale": "Measurements refer to a previous deck revision",
    }
    result.update(
        scope=scope,
        display_label=labels.get(verdict, "No verified improvement"),
        coverage_requirement=(
            "matching_native_opening_policy_dependencies"
            if result.get("objective") == "qualified_opening_rate"
            else "complete_supported_effect_coverage"
        ),
    )
    return result


def evaluate_swap(
    before: dict,
    after: dict,
    *,
    objective: str,
    baseline_id: str,
    candidate_id: str,
    baseline_fingerprint: str,
    candidate_fingerprint: str,
    training_before: dict | None = None,
    training_after: dict | None = None,
    stale: bool = False,
) -> dict[str, Any]:
    return present_outcome(
        _evaluate_swap(
            before,
            after,
            objective=objective,
            baseline_id=baseline_id,
            candidate_id=candidate_id,
            baseline_fingerprint=baseline_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            training_before=training_before,
            training_after=training_after,
            stale=stale,
        )
    )
