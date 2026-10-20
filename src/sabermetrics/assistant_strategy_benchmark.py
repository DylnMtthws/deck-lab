"""Frozen split benchmarks for retrieval, strategic ordering and factual signals.

The fixture corpus is synthetic and bounded. These checks are separate from
live answer/expert evaluation; a passing ranking pair is not expert approval.
Held-out labels never enter inference. Running held-out cases consumes that
split for this release; future tuning requires a new untouched split.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant_functional_search import (
    FunctionalSearchArgs,
    functional_search,
)
from sabermetrics.assistant_jev import run_trial, trial_request
from sabermetrics.card_search import ensure_card_search_schema


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactCheck(Strict):
    card_id: str
    path: str
    expected: Any


class ClaimCheck(Strict):
    claim: str
    source: str
    category: Literal["printed_fact", "rules_interaction", "strategic_judgment"]
    expected: Literal["supports", "contradicts", "not_established"]


class Case(Strict):
    id: str
    split: Literal["development", "heldout"]
    strategy: str
    document: dict[str, Any]
    catalog: list[dict[str, Any]] = Field(min_length=1)
    args: FunctionalSearchArgs
    required_ids: list[str] = Field(min_length=1)
    preferred_pairs: list[tuple[str, str]] = Field(default_factory=list)
    facts: list[FactCheck] = Field(default_factory=list)
    claims: list[ClaimCheck] = Field(default_factory=list)


class Suite(Strict):
    version: Literal["assistant-strategy-benchmark.v1"]
    provenance: str
    cases: list[Case] = Field(min_length=1)


def _seed(path: Path, cards: list[dict[str, Any]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE cards (
          id TEXT PRIMARY KEY, oracle_id TEXT, name TEXT, type_line TEXT,
          mana_cost TEXT, cmc REAL, oracle_text TEXT, color_identity TEXT,
          image_uri TEXT, rarity TEXT, is_legal_commander INTEGER,
          is_legal_in_99 INTEGER, legalities TEXT
        )""")
        for c in cards:
            conn.execute(
                "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c["id"],
                    c.get("oracle_id", c["id"]),
                    c["name"],
                    c["type_line"],
                    c.get("mana_cost"),
                    c.get("mana_value", 2),
                    c.get("oracle_text", ""),
                    json.dumps(c.get("color_identity", [])),
                    None,
                    "common",
                    0,
                    c.get("is_legal_in_99", 1),
                    None,
                ),
            )
        ensure_card_search_schema(conn)


def _path(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return "__missing__"
        value = value[part]
    return value


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def run_benchmark(
    suite: Suite,
    *,
    split: str = "development",
    live_jev: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    cases = [c for c in suite.cases if c.split == split]
    if not cases or len({c.id for c in suite.cases}) != len(suite.cases):
        raise ValueError("Empty split or duplicate case identities")
    records: list[dict[str, Any]] = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="decklab-strategy-eval-") as root:
            path = Path(root) / "fixture.db"
            _seed(path, case.catalog)
            started = time.perf_counter()
            result = functional_search(path, case.document, case.args)
            elapsed = time.perf_counter() - started
        cards = result["cards"]
        ids = [c["id"] for c in cards]
        positions = {cid: i for i, cid in enumerate(ids)}
        checks = []
        indexed = {c["id"]: c for c in cards}
        for check in case.facts:
            actual = _path(indexed.get(check.card_id, {}), check.path)
            checks.append(
                {
                    **check.model_dump(),
                    "actual": actual,
                    "passed": actual == check.expected,
                }
            )
        pairs: list[dict[str, Any]] = [
            {
                "preferred": a,
                "over": b,
                "passed": a in positions
                and b in positions
                and positions[a] < positions[b],
            }
            for a, b in case.preferred_pairs
        ]
        trial: dict[str, Any] = {"status": "not_requested", "configured_cost_usd": 0.0}
        if live_jev:
            # Golden labels are never included in either shared state or questions.
            request = trial_request(
                case.args.query or case.args.function,
                cards,
                [c.model_dump(exclude={"expected"}) for c in case.claims],
            )
            trial = run_trial(request, api_key=api_key)
        jev_pairs, claim_checks = [], []
        if trial["status"] == "judged":
            answers = trial["answers"]
            for a, b in case.preferred_pairs:
                if (
                    a in positions
                    and b in positions
                    and f"fit_{positions[a]}" in answers
                    and f"fit_{positions[b]}" in answers
                ):
                    first, second = (
                        answers[f"fit_{positions[a]}"],
                        answers[f"fit_{positions[b]}"],
                    )
                    jev_pairs.append(
                        {
                            "preferred": a,
                            "over": b,
                            "passed": first["score"] > second["score"],
                        }
                    )
            for i, claim in enumerate(case.claims[: trial["claim_count"]]):
                answer = answers[f"claim_{i}"]
                claim_checks.append(
                    {
                        "expected": claim.expected,
                        "actual": answer["choice"],
                        "passed": answer["choice"] == claim.expected,
                        "confidence": answer["confidence"],
                    }
                )
        records.append(
            {
                "id": case.id,
                "strategy": case.strategy,
                "ranked_ids": ids,
                "candidate_recall": _ratio(
                    len(set(case.required_ids) & set(ids)), len(set(case.required_ids))
                ),
                "recommendation_pairs": pairs,
                "factual_checks": checks,
                "retrieval_ranking_seconds": round(elapsed, 6),
                "jev": trial,
                "jev_recommendation_pairs": jev_pairs,
                "jev_claim_checks": claim_checks,
            }
        )
    recalls = [r["candidate_recall"] for r in records]
    pairs = [p for r in records for p in r["recommendation_pairs"]]
    facts = [f for r in records for f in r["factual_checks"]]
    times = sorted(r["retrieval_ranking_seconds"] for r in records)
    cost_values = [r["jev"].get("configured_cost_usd") for r in records]
    jev_pairs = [p for r in records for p in r["jev_recommendation_pairs"]]
    jev_claims = [c for r in records for c in r["jev_claim_checks"]]
    jev_available = live_jev and all(r["jev"]["status"] == "judged" for r in records)
    base_quality = _ratio(sum(p["passed"] for p in pairs), len(pairs))
    trial_quality = _ratio(sum(p["passed"] for p in jev_pairs), len(jev_pairs))
    success = all(r["candidate_recall"]["rate"] == 1 for r in records) and all(
        p["passed"] for p in [*pairs, *facts]
    )
    return {
        "version": "assistant-strategy-report.v1",
        "split": split,
        "suite_sha256": hashlib.sha256(suite.model_dump_json().encode()).hexdigest(),
        "status": "pass" if success else "fail",
        "cases": records,
        "candidate_recall": _ratio(
            sum(r["numerator"] for r in recalls), sum(r["denominator"] for r in recalls)
        ),
        "recommendation_quality_pairs": _ratio(
            sum(p["passed"] for p in pairs), len(pairs)
        ),
        "factual_errors": _ratio(sum(not f["passed"] for f in facts), len(facts)),
        "jev_recommendation_quality_pairs": _ratio(
            sum(p["passed"] for p in jev_pairs), len(jev_pairs)
        ),
        "jev_claim_accuracy": _ratio(
            sum(c["passed"] for c in jev_claims), len(jev_claims)
        ),
        "unjudged_claims": sum(len(case.claims) for case in cases) - len(jev_claims),
        "jev_comparison": {
            "status": (
                "not_requested"
                if not live_jev
                else "judged" if jev_available else "unavailable"
            ),
            "baseline_pair_rate": base_quality["rate"],
            "jev_pair_rate": trial_quality["rate"],
            "pair_rate_delta": (
                trial_quality["rate"] - base_quality["rate"]
                if jev_available
                and base_quality["rate"] is not None
                and trial_quality["rate"] is not None
                else None
            ),
            "elapsed_seconds": (
                sum(r["jev"].get("elapsed_seconds", 0) for r in records)
                if jev_available
                else None
            ),
            "scope": "Shadow overhead and bounded judgment quality; no end-to-end answer speedup is inferred.",
        },
        "latency": {
            "retrieval_ranking_p50_seconds": times[len(times) // 2],
            "retrieval_ranking_max_seconds": max(times),
            "end_to_end_answer_seconds": None,
        },
        "cost": {
            "configured_usd": (
                sum(cost_values) if all(v is not None for v in cost_values) else None
            ),
            "live_jev_requested": live_jev,
        },
        "scope": "Controlled synthetic corpus, exact profile checks and predeclared ordering pairs. Not live narration, broad corpus recall or independent expert certification. Held-out split is consumed for this release once evaluated.",
    }
