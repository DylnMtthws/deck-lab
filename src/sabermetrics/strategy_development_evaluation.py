"""Frozen development-only receipt evaluation, independent of assistant imports.

Data authorship is NOT independent: see frozen RUBRIC.md. No semantic inference,
model calls, heldout access, synthetic answers or production candidate imports.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_SUITE = (
    Path(__file__).resolve().parents[2]
    / "docs/evaluations/strategy-review-development-20261021"
)
FROZEN_FILES = {"cases.json", "labels.json", "evidence.json", "RUBRIC.md"}


def load_suite(root: Path = DEFAULT_SUITE) -> tuple[list[dict], list[dict], dict]:
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        set(manifest["files_sha256"]) != FROZEN_FILES
        or manifest["split"] != "development"
    ):
        raise ValueError("Expected development-only frozen contract")
    for name, expected in manifest["files_sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen bytes changed: {name}")
    cases, labels, evidence = [
        json.loads((root / name).read_text())
        for name in ("cases.json", "labels.json", "evidence.json")
    ]
    if (
        len({c["id"] for c in cases}) != len(cases)
        or {c["id"] for c in cases} != {l["case_id"] for l in labels}
        or len(labels) != len(cases)
    ):
        raise ValueError("Cases and labels require unique matching IDs")
    by_evidence = {e["id"]: e for e in evidence}
    for case in cases:
        case["evidence"] = [by_evidence[key] for key in case["evidence_ids"]]
    return cases, labels, manifest


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Telemetry must be finite nonnegative numbers or null")
    return float(value)


def _annotations(receipt: dict, key: str, answer: str) -> dict[str, dict]:
    rows = receipt.get(key, [])
    if not isinstance(rows, list):
        raise TypeError(f"{key} must be a list")
    indexed = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or row["id"] in indexed
        ):
            raise ValueError(f"{key} requires unique nonempty IDs")
        start, end = row.get("start"), row.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(answer)
            or not answer[start:end].strip()
        ):
            raise ValueError(f"{key} must bind nonblank spans of the actual answer")
        indexed[row["id"]] = row
    return indexed


def score_receipt(case: dict, label: dict, receipt: dict) -> dict:
    if not isinstance(receipt, dict) or receipt.get("case_id") != case["id"]:
        raise ValueError("Receipt requires matching case_id")
    answer = receipt.get("answer", "")
    if not isinstance(answer, str):
        raise TypeError("answer must be captured text")
    claims, preserved, topics = [
        _annotations(receipt, key, answer) for key in ("claims", "preserved", "topics")
    ]
    for row in claims.values():
        if row.get("value") is not None and type(row["value"]) is not bool:
            raise ValueError("Claim values must be boolean or null")
    truth = label["claim_values"]
    answered = {
        key: row["value"]
        for key, row in claims.items()
        if key in truth and row.get("value") is not None
    }
    errors = sum(value != truth[key] for key, value in answered.items())
    missing_preserved = sorted(set(label["preserve_ids"]) - preserved.keys())
    missing_topics = sorted(set(case["requested_topics"]) - topics.keys())
    telemetry = receipt.get("telemetry", {})
    if not isinstance(telemetry, dict):
        raise TypeError("telemetry must be an object")
    return {
        "case_id": case["id"],
        "strategy": case["strategy"],
        "conditional_truth_accuracy": 1 - errors / len(answered) if answered else None,
        "factual_errors": errors,
        "claims_answered": len(answered),
        "claims_expected": len(truth),
        "claim_coverage": len(answered) / len(truth) if truth else None,
        "usefulness_loss": (
            len(missing_preserved) / len(label["preserve_ids"])
            if label["preserve_ids"]
            else None
        ),
        "missing_preserved": missing_preserved,
        "missing_topics": missing_topics,
        "unreviewed_annotations": sorted(
            (claims.keys() - truth.keys())
            | (preserved.keys() - set(label["preserve_ids"]))
            | (topics.keys() - set(case["requested_topics"]))
        ),
        "fulfilled": receipt.get("status") == "completed"
        and bool(answer.strip())
        and len(answered) == len(truth)
        and errors == 0
        and not missing_preserved
        and not missing_topics,
        "telemetry": {
            key: _number(telemetry.get(key))
            for key in ("latency_ms", "cost_usd", "input_tokens", "output_tokens")
        },
        "scope": "Structured span annotations only; remaining prose unaudited; preservation is not expert strategic scoring.",
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def evaluate(
    receipts: list[dict] | dict[str, dict], root: Path = DEFAULT_SUITE
) -> dict:
    cases, labels, manifest = load_suite(root)
    rows = list(receipts.values()) if isinstance(receipts, dict) else receipts
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Receipts must be a list or object of actual receipt objects")
    indexed = {row.get("case_id"): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) - {c["id"] for c in cases}:
        raise ValueError("Duplicate or unknown receipt case ID")
    if isinstance(receipts, dict) and any(
        key != row.get("case_id") for key, row in receipts.items()
    ):
        raise ValueError("Receipt object keys must match case_id")
    suite_digest = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    for row in rows:
        if row.get("suite_manifest_sha256") != suite_digest:
            raise ValueError(
                "Receipt requires matching capture-time suite_manifest_sha256; older unbound receipts cannot be scored"
            )
    by_label = {label["case_id"]: label for label in labels}
    scores = [
        score_receipt(
            case, by_label[case["id"]], indexed.get(case["id"], {"case_id": case["id"]})
        )
        for case in cases
    ]
    answered, expected, errors = (
        sum(row[key] for row in scores)
        for key in ("claims_answered", "claims_expected", "factual_errors")
    )
    latency = [
        row["telemetry"]["latency_ms"]
        for row in scores
        if row["telemetry"]["latency_ms"] is not None
    ]
    cost = [
        row["telemetry"]["cost_usd"]
        for row in scores
        if row["telemetry"]["cost_usd"] is not None
    ]
    losses = [
        row["usefulness_loss"] for row in scores if row["usefulness_loss"] is not None
    ]
    return {
        "status": "scored" if rows else "no_actual_receipts",
        "suite_manifest_sha256": suite_digest,
        "provenance": {
            key: manifest[key]
            for key in ("author", "independent_author", "expert_reviewed", "split")
        },
        "receipts_observed": len(rows),
        "cases_expected": len(cases),
        "cases": scores,
        "conditional_truth_accuracy": 1 - errors / answered if answered else None,
        "factual_errors": errors,
        "claim_coverage": answered / expected if expected else None,
        "mean_usefulness_loss": sum(losses) / len(losses) if losses else None,
        "fulfillment_rate": sum(row["fulfilled"] for row in scores) / len(scores),
        "latency_ms": {
            "p50": _percentile(latency, 0.5),
            "p95": _percentile(latency, 0.95),
            "observed": len(latency),
        },
        "cost_usd": {
            "total_observed": sum(cost) if cost else None,
            "observed": len(cost),
        },
        "limitations": "Same AI authored development cases and scorer; no independent expert validation. Missing receipts are omissions. Structured annotations require independent audit.",
    }


def compare(baseline: dict, candidate: dict) -> dict:
    """Show speed beside coverage/preservation regressions; never hide a tradeoff."""
    if baseline["suite_manifest_sha256"] != candidate["suite_manifest_sha256"]:
        raise ValueError("Cannot compare different frozen suites")
    before = {row["case_id"]: row for row in baseline["cases"]}
    pairs = []
    for after in candidate["cases"]:
        prior = before[after["case_id"]]
        a, b = prior["telemetry"]["latency_ms"], after["telemetry"]["latency_ms"]
        pairs.append(
            {
                "case_id": after["case_id"],
                "latency_delta_ms": b - a if a is not None and b is not None else None,
                "quality_regression": after["factual_errors"] > prior["factual_errors"]
                or after["claims_answered"] < prior["claims_answered"]
                or len(after["missing_preserved"]) > len(prior["missing_preserved"])
                or len(after["missing_topics"]) > len(prior["missing_topics"])
                or (prior["fulfilled"] and not after["fulfilled"]),
            }
        )
    deltas = [p["latency_delta_ms"] for p in pairs if p["latency_delta_ms"] is not None]
    return {
        "cases": pairs,
        "paired_latency_cases": len(deltas),
        "mean_latency_delta_ms": sum(deltas) / len(deltas) if deltas else None,
        "quality_regressions": sum(p["quality_regression"] for p in pairs),
    }
