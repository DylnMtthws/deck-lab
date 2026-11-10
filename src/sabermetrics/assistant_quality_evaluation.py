"""Offline, frozen independent benchmark. Imports no assistant implementation."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

DEFAULT_CORPUS = (
    Path(__file__).resolve().parents[2] / "docs/evaluations/assistant-quality-20261020"
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def verify_freeze(corpus: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    """Hash all frozen bytes without interpreting any heldout label content."""
    manifest = _read(corpus / "manifest.json")
    required = {
        f"{split}.{part}.json"
        for split in ("development", "heldout")
        for part in ("cases", "labels", "evidence")
    } | {"CONTRACT.md", "rules.evidence.json"}
    if not required.issubset(manifest["files_sha256"]):
        raise ValueError("Manifest is missing required frozen files")
    for name, expected in manifest["files_sha256"].items():
        if Path(name).name != name:
            raise ValueError("Manifest paths must be local filenames")
        actual = hashlib.sha256((corpus / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Frozen content changed: {name}")
    return cast(dict[str, Any], manifest)


def load_cases(
    split: str = "development", corpus: Path = DEFAULT_CORPUS
) -> list[dict[str, Any]]:
    """Load public inputs only; no labels are decoded by this function."""
    if split not in ("development", "heldout"):
        raise ValueError("Unknown split")
    verify_freeze(corpus)
    cases = _read(corpus / f"{split}.cases.json")
    evidence = {row["id"]: row for row in _read(corpus / f"{split}.evidence.json")}
    rules = _read(corpus / "rules.evidence.json")
    evidence[rules["id"]] = rules
    for case in cases:
        case["evidence"] = [evidence[key] for key in case["evidence_ids"]]
    return cast(list[dict[str, Any]], cases)


def _list(receipt: dict[str, Any], key: str) -> list[Any]:
    value = receipt.get(key, [])
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return value


def _rows(receipt: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = _list(receipt, key)
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{key} entries must be objects")
    return rows


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Telemetry must be finite nonnegative numbers or null")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Telemetry must be finite nonnegative numbers or null")
    return float(value)


def score_case(
    case: dict[str, Any], label: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    """Score explicit structured observations; never infer claims from prose."""
    if not isinstance(receipt, dict) or receipt.get("case_id") != case["id"]:
        raise ValueError("Receipt must contain matching case_id")
    candidates = _list(receipt, "candidates")
    if any(not isinstance(name, str) for name in candidates):
        raise ValueError("Candidate names must be strings")
    relevant = set(label["relevant_candidates"])
    recalled = len(relevant & set(candidates))
    recommendations = _rows(receipt, "recommendations")
    cuts = _rows(receipt, "cuts")
    claims = _rows(receipt, "claims")
    packages = _rows(receipt, "packages")
    errors: list[str] = []

    def grades(rows: list[dict[str, Any]], scores: dict[str, Any]):
        raw_names = [row.get("card") for row in rows]
        if any(not isinstance(name, str) for name in raw_names):
            raise ValueError("Recommendation/cut card names must be strings")
        names = cast(list[str], raw_names)
        if len(names) != len(set(names)):
            errors.append("duplicate recommendation/cut")
        known = [scores[name] for name in set(names) if scores.get(name) is not None]
        # Unknown alternatives require review; they never silently receive credit.
        unknown = [name for name in set(names) if scores.get(name) is None]
        denominator = max(1, len(names))
        credit = sum(known) / (2 * denominator)
        return credit, len(known), unknown

    usefulness, rec_known, rec_unknown = grades(
        recommendations, label["recommendation_scores"]
    )
    cut_quality, cut_known, cut_unknown = grades(cuts, label["cut_scores"])
    protected = [r["card"] for r in cuts if r["card"] in label["protected_cuts"]]
    invalid_cuts = [r["card"] for r in cuts if r["card"] not in case["context"]["deck"]]
    known_claims = label["claim_values"]
    answers: dict[str, Any] = {}
    extra_claims = 0
    for row in claims:
        key, value = row.get("id"), row.get("value")
        if not isinstance(key, str):
            raise TypeError("Claim IDs must be strings")
        if value is not None and type(value) is not bool:
            raise ValueError("Claim values must be true, false, or null")
        if key in answers:
            raise ValueError("Duplicate claim ID")
        if key not in known_claims:
            extra_claims += 1
        answers[key] = value
    scorable = {k: v for k, v in known_claims.items() if type(v) is bool}
    answered = {k: answers[k] for k in scorable if answers.get(k) is not None}
    factual_errors = sum(v != scorable[k] for k, v in answered.items())
    expected_packages = {
        tuple(sorted(row["cards"])): row["coherent"] for row in label["packages"]
    }
    assessed: dict[tuple[str, ...], bool] = {}
    for row in packages:
        names = row.get("cards")
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise ValueError("Package cards must be a list of names")
        if len(names) != len(set(names)) or type(row.get("coherent")) is not bool:
            raise ValueError("Package needs unique cards and boolean coherent")
        key = tuple(sorted(names))
        if key in assessed:
            raise ValueError("Duplicate package")
        assessed[key] = row["coherent"]
    package_known = expected_packages.keys() & assessed.keys()
    package_errors = sum(assessed[k] != expected_packages[k] for k in package_known)
    topics = _list(receipt, "addressed_topics")
    if any(not isinstance(topic, str) for topic in topics):
        raise ValueError("Topics must be strings")
    required = case["requested"]
    completion_checks = {
        "status": receipt.get("status") == "completed",
        "recommendations": len(recommendations) == required["recommendations"],
        "cuts": len(cuts) == required["cuts"] and not invalid_cuts,
        "packages": len(package_known) == len(packages) == required["packages"],
        "probes": len(answered) == len(scorable),
        "rationales": bool(recommendations and cuts)
        and all(
            isinstance(r.get("rationale"), str) and bool(r["rationale"].strip())
            for r in recommendations + cuts
        ),
        "topics": set(required["topics"]).issubset(topics),
        "unique_choices": not errors,
        "distinct_recommendation_and_cut": not (
            {r["card"] for r in recommendations} & {r["card"] for r in cuts}
        ),
    }
    telemetry = receipt.get("telemetry", {})
    if not isinstance(telemetry, dict):
        raise TypeError("telemetry must be an object")
    resources = {
        key: _number(telemetry.get(key))
        for key in ("latency_ms", "cost_usd", "input_tokens", "output_tokens")
    }
    resources["callback_wall_ms"] = _number(receipt.get("callback_wall_ms"))
    return {
        "case_id": case["id"],
        "archetype": case["archetype"],
        "cohort": case["cohort"],
        "candidate_recall": recalled / len(relevant) if relevant else None,
        "candidate_hits": recalled,
        "candidate_expected": len(relevant),
        "recommendation_usefulness": usefulness,
        "recommendation_known": rec_known,
        "recommendation_unknown": rec_unknown,
        "factual_errors": factual_errors,
        "factual_answered": len(answered),
        "factual_expected": len(scorable),
        "factual_error_rate": factual_errors / len(answered) if answered else None,
        "factual_coverage": len(answered) / len(scorable) if scorable else None,
        "factual_abstentions_or_missing": len(scorable) - len(answered),
        "unreviewed_extra_claims": extra_claims,
        "prose_present_unreviewed": bool(receipt.get("answer")),
        "package_errors": package_errors,
        "package_assessed": len(package_known),
        "package_expected": len(expected_packages),
        "package_coherence_accuracy": (
            1 - package_errors / len(package_known) if package_known else None
        ),
        "package_unknown": len(assessed.keys() - expected_packages.keys()),
        "cut_quality": cut_quality,
        "cut_known": cut_known,
        "cut_unknown": cut_unknown,
        "protected_cut_violations": protected,
        "invalid_cuts": invalid_cuts,
        "completed": all(completion_checks.values()),
        "completion_checks": completion_checks,
        "resources": resources,
        "validation_errors": errors,
        "capture_error": receipt.get("capture_error"),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(key):
        values = [r[key] for r in rows if r[key] is not None]
        return sum(values) / len(values) if values else None

    resources = {}
    for key in (
        "latency_ms",
        "callback_wall_ms",
        "cost_usd",
        "input_tokens",
        "output_tokens",
    ):
        values = sorted(
            r["resources"][key] for r in rows if r["resources"][key] is not None
        )
        resources[key] = {
            "observed_cases": len(values),
            "missing_cases": len(rows) - len(values),
            "sum": sum(values) if values else None,
            "median": (
                (values[(len(values) - 1) // 2] + values[len(values) // 2]) / 2
                if values
                else None
            ),
            "p95_nearest_rank": (
                values[math.ceil(len(values) * 0.95) - 1] if values else None
            ),
        }
    answered = sum(r["factual_answered"] for r in rows)
    errors = sum(r["factual_errors"] for r in rows)
    return {
        "cases": len(rows),
        "candidate_recall_macro": mean("candidate_recall"),
        "recommendation_usefulness_macro": mean("recommendation_usefulness"),
        "recommendation_unknown_count": sum(
            len(r["recommendation_unknown"]) for r in rows
        ),
        "factual_errors": errors,
        "factual_answered": answered,
        "factual_expected": sum(r["factual_expected"] for r in rows),
        "factual_error_rate": errors / answered if answered else None,
        "factual_coverage_macro": mean("factual_coverage"),
        "package_coherence_accuracy_macro": mean("package_coherence_accuracy"),
        "package_assessed": sum(r["package_assessed"] for r in rows),
        "package_expected": sum(r["package_expected"] for r in rows),
        "cut_quality_macro": mean("cut_quality"),
        "protected_cut_violations": sum(
            len(r["protected_cut_violations"]) for r in rows
        ),
        "completion_rate": (
            sum(r["completed"] for r in rows) / len(rows) if rows else None
        ),
        "prose_cases_requiring_review": sum(
            r["prose_present_unreviewed"] for r in rows
        ),
        "resources": resources,
    }


def run(
    actualresults: Callable[[dict[str, Any]], dict[str, Any]],
    split: str = "development",
    *,
    final_code_freeze: str | None = None,
    corpus: Path = DEFAULT_CORPUS,
    captured_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sequential callback evaluation; heldout labels require freeze attestation."""
    if split == "heldout" and not final_code_freeze:
        raise ValueError("Heldout labels require final_code_freeze attestation")
    cases = load_cases(split, corpus)
    # Only the selected label partition is interpreted, after the freeze gate.
    labels = _read(corpus / f"{split}.labels.json")
    rows = []
    for case in cases:
        started = time.perf_counter()
        try:
            # Callback gets its own copy, so it cannot alter evaluation inputs.
            receipt = actualresults(json.loads(json.dumps(case)))
            if not isinstance(receipt, dict):
                raise TypeError("Callback must return a receipt object")
            receipt = dict(receipt)
            receipt["callback_wall_ms"] = (time.perf_counter() - started) * 1000
            row = score_case(case, labels[case["id"]], receipt)
        except Exception as exc:
            receipt = {
                "case_id": case["id"],
                "status": "failed",
                "capture_error": f"{type(exc).__name__}: {exc}",
                "callback_wall_ms": (time.perf_counter() - started) * 1000,
            }
            row = score_case(case, labels[case["id"]], receipt)
        if captured_receipts is not None:
            captured_receipts.append(receipt)
        rows.append(row)
    return {
        "schema": "assistant-quality-report.v1",
        "split": split,
        "final_code_freeze": final_code_freeze,
        "manifest_sha256": hashlib.sha256(
            (corpus / "manifest.json").read_bytes()
        ).hexdigest(),
        "summary": _summary(rows),
        "by_archetype": {
            arch: _summary([r for r in rows if r["archetype"] == arch])
            for arch in sorted({r["archetype"] for r in rows})
        },
        "cases": rows,
        "limitations": [
            "AI-authored constrained rubric, not human/expert certification.",
            "Cached Oracle facts have not been independently primary-verified.",
            "Structured probe accuracy does not certify unstructured prose.",
            "Topic coverage and rationale presence do not certify semantic quality.",
            "Unknown recommendations/cuts require independent review; no automatic credit.",
            "Resource totals are observed-only; missing telemetry is not zero.",
            "Small source-disjoint archetype split confounds archetype and split difficulty.",
        ],
    }


def diagnose_retrieval_stages(
    required_card_ids: list[str],
    hand_plan_card_ids: list[str],
    model_plan_card_ids: list[str],
    *,
    synthesis_fulfillment: str = "unknown",
) -> dict[str, Any]:
    """Attribute frozen structured failures without judging unstructured prose.

    Required IDs come from a separately authored case label. This deterministic
    diagnostic cannot certify that the label or its preferred strategy is right.
    Additional relevant alternatives outside that label receive no automatic
    credit. Unknown fulfillment is never interpreted as successful synthesis.
    """
    for values in (required_card_ids, hand_plan_card_ids, model_plan_card_ids):
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise ValueError("Diagnostic identities must be nonempty string lists")
    if synthesis_fulfillment not in {"complete", "partial", "nonanswer", "unknown"}:
        raise ValueError("Unknown synthesis fulfillment")
    required = set(required_card_ids)
    hand, model = set(hand_plan_card_ids), set(model_plan_card_ids)
    if not required:
        diagnosis = "insufficient_label"
    elif not required <= hand and not required <= model:
        diagnosis = "retrieval_or_corpus_gap"
    elif not required <= hand:
        diagnosis = "hand_plan_gap_model_retrieval_succeeded"
    elif not required <= model:
        diagnosis = "planning_gap"
    elif synthesis_fulfillment in {"partial", "nonanswer"}:
        diagnosis = "synthesis_or_fulfillment_gap"
    elif synthesis_fulfillment == "complete":
        diagnosis = "no_structured_gap_observed"
    else:
        diagnosis = "synthesis_unassessed"
    return {
        "schema": "assistant-stage-diagnostic.v1",
        "diagnosis": diagnosis,
        "required_count": len(required),
        "hand_plan_recall": len(required & hand) / len(required) if required else None,
        "model_plan_recall": (
            len(required & model) / len(required) if required else None
        ),
        "hand_plan_missing": sorted(required - hand),
        "model_plan_missing": sorted(required - model),
        "synthesis_fulfillment": synthesis_fulfillment,
        "limitations": [
            "Structured labels and receipts only; no certification of prose or strategic usefulness.",
            "A retrieval gap may reflect a wrong label and requires investigation.",
        ],
    }
