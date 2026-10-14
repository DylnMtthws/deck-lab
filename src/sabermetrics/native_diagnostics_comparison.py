"""Descriptive comparisons of frozen native diagnostic reports."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, cast

NOTE = (
    "Descriptive before/after differences only; shared indexed seeds are not "
    "statistical proof of paired improvement. Catalog and modeling differences "
    "are not isolated from card changes. No winner verdict is inferred."
)

OPENING_COVERAGE_ALGORITHM = "opening-policy-dependencies.v2"
OPENING_POLICY_METRICS = {
    "qualified_opening_rate": "proportion",
    "mean_mulligans": "mulligans",
    "mean_kept_cards": "cards",
}


def _opening_coverage(test: Any, key: str) -> dict[str, Any] | None:
    """Accept only the native producer's bounded-policy accounting contract."""
    if key != "opening" or not isinstance(test, dict):
        return None
    proof = test.get("comparison_coverage")
    fields = {
        "version",
        "scope",
        "algorithm",
        "dependency_accounting_complete",
        "dependency_fingerprint",
    }
    if not isinstance(proof, dict) or set(proof) != fields:
        return None
    fingerprint = proof.get("dependency_fingerprint")
    if (
        type(proof.get("version")) is not int
        or proof["version"] != 1
        or proof.get("scope") != "native_opening_policy"
        or proof.get("algorithm") != OPENING_COVERAGE_ALGORITHM
        or proof.get("dependency_accounting_complete") is not True
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        return None
    return proof


def _comparison_coverage(value: Any) -> Any:
    """Ignore card identity/order in detailed audit rows for comparison only."""
    if not isinstance(value, dict):
        return value
    audit = value.get("card_coverage")
    if not isinstance(audit, dict) or not isinstance(audit.get("cards"), list):
        return value
    normalized = dict(value)
    normalized_audit = dict(audit)
    rows = []
    for row in audit["cards"]:
        if isinstance(row, dict):
            rows.append(
                {key: item for key, item in row.items() if key not in {"name", "slot"}}
            )
        else:
            rows.append(row)
    # A sorted multiset retains duplicate quantities while ignoring row order.
    normalized_audit["cards"] = sorted(
        Counter(
            json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
        ).items()
    )
    normalized["card_coverage"] = normalized_audit
    return normalized


def compare_reports(after: dict[str, Any], before: object) -> dict[str, Any]:
    settings = after.get("settings")
    fingerprint = (
        settings.get("settings_fingerprint") if isinstance(settings, dict) else None
    )
    result: dict[str, Any] = {
        "comparable": False,
        "reason": "No comparable diagnostics baseline: the prior run has no native diagnostics.",
        "settings_fingerprint": fingerprint,
        "note": NOTE,
        "metric_deltas": {},
    }
    if not isinstance(before, dict):
        return result
    if not after.get("schema_version") or after.get("schema_version") != before.get(
        "schema_version"
    ):
        result["reason"] = (
            "No comparable diagnostics baseline: diagnostics schema changed."
        )
        return result
    if (
        not fingerprint
        or not isinstance(settings, dict)
        or not settings.get("engine_version")
        or settings != before.get("settings")
    ):
        result["reason"] = (
            "No comparable diagnostics baseline: engine, settings, or settings fingerprint changed or are unavailable."
        )
        return result
    same_coverage = _comparison_coverage(after.get("coverage")) == _comparison_coverage(
        before.get("coverage")
    )
    old_tests = before.get("tests", {})
    new_tests = after.get("tests", {})
    scoped = (
        isinstance(old_tests, dict)
        and isinstance(new_tests, dict)
        and _opening_coverage(new_tests.get("opening"), "opening") is not None
        and _opening_coverage(old_tests.get("opening"), "opening") is not None
    )
    if not same_coverage and not scoped:
        result["reason"] = (
            "No comparable diagnostics baseline: modeled coverage changed; card and modeling differences cannot be isolated."
        )
        return result
    result.update(comparable=True, reason="")
    if not isinstance(old_tests, dict) or not isinstance(new_tests, dict):
        result.update(
            comparable=False,
            reason="No comparable diagnostics baseline: test reports are unavailable.",
        )
        return result
    for key, test in new_tests.items():
        if not isinstance(test, dict):
            continue
        old = old_tests.get(key, {})
        old = old if isinstance(old, dict) else {}
        new_scope, old_scope = _opening_coverage(test, key), _opening_coverage(old, key)
        has_scope = "comparison_coverage" in test or "comparison_coverage" in old
        coverage_compatible = (
            new_scope is not None and new_scope == old_scope
            if has_scope
            else same_coverage
        )
        metrics = test.get("metrics", {})
        old_metrics = old.get("metrics", {})
        if not isinstance(metrics, dict) or not isinstance(old_metrics, dict):
            continue
        deltas = {}
        for name, metric in metrics.items():
            metric = metric if isinstance(metric, dict) else {}
            prior = old_metrics.get(name, {})
            prior = prior if isinstance(prior, dict) else {}
            left, right = prior.get("value"), metric.get("value")
            measured = all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in (left, right)
            )
            same_status = test.get("status") == old.get("status") and test.get(
                "status"
            ) in {"supported", "partial"}
            same_unit = bool(metric.get("unit")) and metric.get("unit") == prior.get(
                "unit"
            )
            scoped_metric = not has_scope or (
                name in OPENING_POLICY_METRICS
                and metric.get("unit") == OPENING_POLICY_METRICS[name]
            )
            compatible = (
                measured
                and same_status
                and same_unit
                and coverage_compatible
                and scoped_metric
            )
            reason = (
                ""
                if compatible
                else (
                    "Metric dependency coverage changed or is unavailable."
                    if not coverage_compatible or not scoped_metric
                    else (
                        "Test status changed or is unmeasured."
                        if not same_status
                        else (
                            "Metric units changed or are unavailable."
                            if not same_unit
                            else "Metric is unmeasured in one or both runs."
                        )
                    )
                )
            )
            deltas[name] = {
                "before": left,
                "after": right,
                "delta": cast(float, right) - cast(float, left) if compatible else None,
                "unit": metric.get("unit"),
                "status": test.get("status"),
                "comparable": compatible,
                "reason": reason,
                "comparison_scope": (
                    "native_opening_policy"
                    if new_scope and old_scope
                    else "report_coverage"
                ),
            }
        result["metric_deltas"][key] = deltas
    if not same_coverage:
        result["comparable"] = any(
            metric["comparable"]
            for metrics in result["metric_deltas"].values()
            for metric in metrics.values()
        )
        result["reason"] = (
            "Only metrics with unchanged native opening policy dependencies are comparable; other modeled coverage changed. These are descriptive partial-policy comparisons, not real opening success."
            if result["comparable"]
            else "No comparable diagnostics baseline: metric dependency coverage changed or is unavailable."
        )
    elif any(
        isinstance(test, dict) and "comparison_coverage" in test
        for tests in (old_tests, new_tests)
        for test in tests.values()
    ):
        result["comparable"] = any(
            metric["comparable"]
            for metrics in result["metric_deltas"].values()
            for metric in metrics.values()
        )
        if not result["comparable"]:
            result["reason"] = (
                "No comparable diagnostics baseline: no measured metric has compatible dependency evidence, status and units."
            )
    return result
