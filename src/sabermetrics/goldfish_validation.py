"""Independent, reproducible validation of a frozen native comparison.

Validation observes supported native metrics; it never ranks whole decks or
infers a multiplayer victory from a solo diagnostic.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from sabermetrics.native_diagnostics_comparison import compare_reports


def _coverage_complete(report: dict[str, Any]) -> bool:
    """Legacy authored counts never establish complete effect coverage."""
    coverage = report.get("coverage")
    if not isinstance(coverage, dict):
        return False
    detail = coverage.get("card_coverage")
    if not isinstance(detail, dict) or detail.get("version") != 1:
        return False
    counts = detail.get("counts")
    cards = detail.get("cards")
    total = coverage.get("total_cards")
    return (
        detail.get("unit") == "card_slots"
        and isinstance(counts, dict)
        and type(total) is int
        and total > 0
        and isinstance(cards, list)
        and len(cards) == total
        and all(
            isinstance(card, dict)
            and card.get("status") == "supported"
            and type(card.get("slot")) is int
            for card in cards
        )
        and sorted(card["slot"] for card in cards) == list(range(total))
        and type(counts.get("supported")) is int
        and counts["supported"] == total
        and all(
            type(counts.get(key)) is int and counts[key] == 0
            for key in ("partial", "unsupported", "inactive")
        )
    )


def validation_seed(training_seed: int) -> int:
    if (
        isinstance(training_seed, bool)
        or not isinstance(training_seed, int)
        or training_seed < 0
    ):
        raise ValueError("Training seed must be a nonnegative integer.")
    seed = int.from_bytes(
        hashlib.sha256(f"goldfish-heldout.v1:{training_seed}".encode()).digest()[:4],
        "big",
    )
    return seed if seed != training_seed else seed + 1


def compare_validation(
    before: dict[str, Any], after: dict[str, Any], *, training_seed: int
) -> dict[str, Any]:
    """Reject reused seeds/profile drift and preserve unknown measurements."""
    expected = validation_seed(training_seed)
    if any(
        report.get("settings", {}).get("seed") != expected for report in (before, after)
    ):
        raise ValueError(
            "Validation reports must use the independent frozen validation seed."
        )
    comparison = compare_reports(after, before)
    # Native effects_sha256 hashes the complete authored effects file, not
    # the selected deck. Require the same catalog, engine and policy identity.
    identity = ("engine_version", "policy", "effects_sha256", "strategy_pack_sha256")
    settings = before.get("settings", {})
    provenance_complete = all(
        settings.get(key) and settings.get(key) == after.get("settings", {}).get(key)
        for key in identity
    )
    coverage_complete = all(_coverage_complete(report) for report in (before, after))
    for test, metrics in comparison["metric_deltas"].items():
        for name, metric in metrics.items():
            left = (
                before.get("tests", {}).get(test, {}).get("metrics", {}).get(name, {})
            )
            right = (
                after.get("tests", {}).get(test, {}).get("metrics", {}).get(name, {})
            )
            reasons = []
            if not metric["comparable"]:
                reasons.append(
                    metric["reason"] or "Metric or settings are not comparable."
                )
            if metric["status"] != "supported":
                reasons.append("Test is partial or unsupported.")
            if not provenance_complete:
                reasons.append(
                    "Engine, pilot, effects catalog, or strategy provenance is missing or differs."
                )
            if not coverage_complete:
                reasons.append(
                    "Coverage is incomplete or unverified: partial, unsupported, or inactive cards remain, or explicit effect-coverage counts are missing."
                )
            if not all(
                isinstance(item.get("denominator"), int)
                and not isinstance(item.get("denominator"), bool)
                and item["denominator"] > 0
                and isinstance(item.get("ci95"), dict)
                and all(
                    isinstance(item["ci95"].get(bound), (int, float))
                    for bound in ("low", "high")
                )
                for item in (left, right)
            ):
                reasons.append(
                    "A positive trial denominator or uncertainty interval is missing."
                )
            metric["validated"] = not reasons
            metric["unvalidated_reason"] = " ".join(reasons)
            metric["verdict"] = "descriptive"
    return copy.deepcopy(
        {
            "status": "descriptive",
            "training_seed": training_seed,
            "validation_seed": expected,
            "comparison": comparison,
            "note": "Independent held-out trials; differences remain descriptive. Partial coverage, unknown metrics, and catalog changes cannot establish deck superiority. No improvement ranking is inferred.",
        }
    )
