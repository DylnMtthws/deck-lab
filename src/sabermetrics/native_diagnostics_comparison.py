"""Descriptive comparisons of frozen native diagnostic reports."""

from __future__ import annotations

import math
from typing import Any, cast

NOTE = (
    "Descriptive before/after differences only; shared indexed seeds are not "
    "statistical proof of paired improvement. Catalog and modeling differences "
    "are not isolated from card changes. No winner verdict is inferred."
)


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
    result.update(comparable=True, reason="")
    old_tests = before.get("tests", {})
    new_tests = after.get("tests", {})
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
            compatible = measured and same_status and same_unit
            reason = (
                ""
                if compatible
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
            deltas[name] = {
                "before": left,
                "after": right,
                "delta": cast(float, right) - cast(float, left) if compatible else None,
                "unit": metric.get("unit"),
                "status": test.get("status"),
                "comparable": compatible,
                "reason": reason,
            }
        result["metric_deltas"][key] = deltas
    return result
