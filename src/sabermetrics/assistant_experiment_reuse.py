"""Exact provenance gate for historical native diagnostics; no new win claims."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def measurement_identity(report: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(report, dict):
        return None
    settings, coverage = report.get("settings"), report.get("coverage")
    required = (
        "engine_version",
        "policy",
        "effects_sha256",
        "strategy_pack_sha256",
        "settings_fingerprint",
    )
    if (
        not isinstance(settings, dict)
        or not isinstance(coverage, dict)
        or not coverage
        or any(not settings.get(k) for k in required)
    ):
        return None

    # Matching totals alone cannot establish matching modeled effects. Full
    # authored coverage identity and catalog/version provenance are retained.
    def digest(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    return {
        "settings_sha256": digest(settings),
        "coverage_sha256": digest(coverage),
        "engine_version": str(settings["engine_version"]),
    }


def reuse_decision(
    historical: dict[str, str] | None,
    current: dict[str, str] | None,
    *,
    same_deck: bool,
    completed: bool,
) -> dict[str, Any]:
    missing = []
    if not same_deck:
        missing.append("deck_content")
    if not completed:
        missing.append("completed_measurement")
    if historical is None or current is None:
        missing.append("measurement_provenance")
    elif historical != current:
        missing.append("settings_engine_or_modeled_coverage")
    return {
        "eligible": not missing,
        "missing_or_changed": missing,
        "historical_identity": historical,
        "current_identity": current,
        "scope": "Reuse only the recorded descriptive experiment. This does not establish deck superiority or multiplayer win rate.",
    }
