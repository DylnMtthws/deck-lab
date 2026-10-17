"""Offline conversation regression scoring, separate from broad expertise qualification.

The existing assistant_expertise gate remains the authority for blind, enrolled
expert comparisons. This small development benchmark checks captured conversation
state and accepts separately supplied, answer-hash-bound skeptical assessments.
Metadata checks do not prove prose correctness; absent semantic evidence is unknown.
No model, database, network, or deployment is invoked here.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=160)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Score = Annotated[int, Field(ge=0, le=4)]
Verdict = Literal["pass", "fail", "unknown"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Check(Strict):
    id: Identifier
    path: str = Field(min_length=1)
    op: Literal["equals", "contains"] = "equals"
    value: Any


class Case(Strict):
    id: Identifier
    prompt: str = Field(min_length=1)
    checks: list[Check] = Field(min_length=1)
    semantic_criteria: dict[str, str] = Field(min_length=1)
    max_latency_ms: float = Field(gt=0, allow_inf_nan=False)


class Benchmark(Strict):
    version: Literal["assistant-conversation-benchmark.v1"]
    privacy_note: str
    context: dict[str, Any]
    references: dict[str, str]
    cases: list[Case] = Field(min_length=1, max_length=20)
    latency_regression_ratio: float = Field(gt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_ids(self):
        _unique([case.id for case in self.cases])
        for case in self.cases:
            _unique([check.id for check in case.checks])
        return self


class Retrieval(Strict):
    canonical_name: Identifier
    status: Literal["resolved", "unavailable"]
    oracle_text: str | None = Field(default=None, max_length=8000)


class Turn(Strict):
    case_id: Identifier
    answer: str = Field(max_length=60000)
    status: Literal["done", "partial", "budget_error", "error"]
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    # Captured/normalized runtime artifacts, never model self-grading.
    state: dict[str, Any]
    retrievals: list[Retrieval]
    answer_card_names: list[Identifier]
    unavailable_card_names: list[Identifier]


class Run(Strict):
    version: Literal["assistant-conversation-run.v1"]
    benchmark_sha256: Digest
    revision: Identifier
    evidence_kind: Literal["live_capture", "controlled_test"]
    turns: list[Turn] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_turns(self):
        _unique([turn.case_id for turn in self.turns])
        return self


class Review(Strict):
    case_id: Identifier
    answer_sha256: Digest
    reviewer_id: Identifier
    kind: Literal["human_expert", "model_judge"]
    independent: bool
    correctness: Score
    usefulness: Score
    criteria: dict[str, Verdict]
    rationale: str = Field(min_length=10, max_length=8000)


def _unique(values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("Duplicate IDs")


def benchmark_digest(benchmark: Benchmark) -> str:
    return hashlib.sha256(
        json.dumps(
            benchmark.model_dump(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def answer_digest(answer: str) -> str:
    return hashlib.sha256(answer.encode()).hexdigest()


def capture_turn(
    case_id: str,
    request: str,
    result: dict[str, Any],
    elapsed_ms: float,
    *,
    prior_turns: list[Turn] | None = None,
    initial_cards: list[dict[str, Any]] | None = None,
    deck_unchanged: bool | None = None,
) -> Turn:
    """Adapt real run_turn artifacts; never manufacture fulfilled deliverables.

    Pass earlier captured turns to preserve resolved identity across the same
    conversation. Optional initial_cards are actual canonical context sources,
    restricted by the caller to the scenario's relevant cards. Confirm unchanged
    deck hashes externally before setting deck_unchanged; absent evidence stays
    None and cannot satisfy the no-mutation check. No user/deck IDs, quotes, full
    deck, source-turn IDs or tool error bodies are retained. Semantic fulfillment
    stays with the separately supplied reviewers.
    """
    research = result.get("research") or {}
    task = research.get("task") or {}
    preference_rows = research.get("conversation_preferences") or []
    dimensions = sorted(
        {
            row["dimension"]
            for row in preference_rows
            if isinstance(row, dict) and isinstance(row.get("dimension"), str)
        }
    )
    preferences: dict[str, Any] = {"dimensions": dimensions}
    excluded = []
    for row in preference_rows:
        if not isinstance(row, dict):
            continue
        dimension, strength, value = (
            row.get("dimension"),
            row.get("strength"),
            str(row.get("value") or ""),
        )
        if dimension == "card_type" and strength == "required":
            preferences["creatures_only"] = value.casefold() in {
                "creature",
                "creatures",
                "creatures only",
                "creature only",
                "creatures_only",
            }
        if dimension == "draw_control":
            preferences["optionality"] = strength
        if dimension == "multifunctionality":
            preferences["multifunctionality"] = strength in {"preferred", "required"}
        if dimension == "excluded_card" and strength == "rejected":
            excluded.append(value)
    preferences["rejected_cards"] = sorted(excluded)
    known: dict[str, Retrieval] = {}
    for prior in prior_turns or []:
        for row in prior.retrievals:
            if row.status == "resolved":
                known[row.canonical_name] = row
    cards = list(initial_cards or [])
    for action in result.get("actions") or []:
        if (
            not isinstance(action, dict)
            or action.get("type") != "tool_result"
            or not action.get("ok")
        ):
            continue
        data = action.get("data") or {}
        if action.get("name") == "card_details" and isinstance(data.get("card"), dict):
            cards.append(data["card"])
        elif action.get("name") in {"search_cards", "search_card_functions"}:
            cards.extend(data.get("cards") or [])
    for card in cards:
        if (
            isinstance(card, dict)
            and isinstance(card.get("name"), str)
            and card.get("oracle_text")
        ):
            name = card["name"]
            known[name] = Retrieval(
                canonical_name=name, status="resolved", oracle_text=card["oracle_text"]
            )
    answer = str(result.get("content") or "")

    def folded(text: str) -> str:
        return (
            unicodedata.normalize("NFKC", text)
            .casefold()
            .replace("’", "'")
            .replace("judgement", "judgment")
        )

    named = [
        name
        for name in known
        if re.search(r"(?<!\w)" + re.escape(folded(name)) + r"(?!\w)", folded(answer))
    ]
    # This evidence is derived from sources, not a model's availability claim.
    evidence = {re.sub(r"[^A-Za-z0-9]", "", name): "resolved" for name in known}
    # Prose contradiction detection is semantic. Do not pretend an empty list
    # proves that the assistant never denied retrieved evidence.
    status = result.get("status")
    return Turn(
        case_id=case_id,
        answer=answer,
        status=(
            status
            if status in {"done", "partial"}
            else "budget_error" if status == "budget_exhausted" else "error"
        ),
        latency_ms=elapsed_ms,
        state={
            "request_sha256": answer_digest(request),
            "task": {
                "kind": task.get("kind"),
                "requested_additions": task.get("additions"),
                "requested_cuts": task.get("cuts"),
            },
            "deck_mutated": None if deck_unchanged is None else not deck_unchanged,
            "preferences": preferences,
            "evidence": evidence,
            "deliverables_evaluated": task.get("deliverables_evaluated"),
        },
        retrievals=list(known.values()),
        answer_card_names=named,
        unavailable_card_names=[],
    )


def _lookup(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


_MISSING = object()


def _same(left: Any, right: Any) -> bool:
    # Python equates 0/False; captured task constraints must preserve types.
    return type(left) is type(right) and left == right


def evaluate(benchmark: Benchmark, run: Run, reviews: list[Review]) -> dict[str, Any]:
    """Fail closed on missing cases; score facts, expert judgment, time separately.

    Reviewer identity/independence is supplied evidence, not authenticated by this
    script. A passing development scenario is never a broad-expertise release.
    """
    digest = benchmark_digest(benchmark)
    if run.benchmark_sha256 != digest:
        raise ValueError("Run does not match the frozen benchmark")
    expected = {case.id for case in benchmark.cases}
    turns = {turn.case_id: turn for turn in run.turns}
    if set(turns) - expected:
        raise ValueError("Run has unknown cases")
    if list(turns) != [case.id for case in benchmark.cases if case.id in turns]:
        raise ValueError("Conversation turns are out of order")
    seen: set[tuple[str, str]] = set()
    for review in reviews:
        key = (review.case_id, review.reviewer_id)
        if key in seen or review.case_id not in turns:
            raise ValueError("Duplicate or unmatched review")
        seen.add(key)
        if review.answer_sha256 != answer_digest(turns[review.case_id].answer):
            raise ValueError("Review answer hash mismatch")
    records: list[dict[str, Any]] = []
    for case in benchmark.cases:
        turn = turns.get(case.id)
        if turn is None:
            records.append({"id": case.id, "status": "incomplete"})
            continue
        failed = []
        if not turn.answer.strip():
            failed.append("substantive_answer_missing")
        if turn.status not in {"done", "partial"}:
            failed.append("answer_failed")
        for check in case.checks:
            actual = _lookup(turn.model_dump(), check.path)
            passed = (
                _same(actual, check.value)
                if check.op == "equals"
                else isinstance(actual, list)
                and any(_same(item, check.value) for item in actual)
            )
            if not passed:
                failed.append(check.id)
        resolved = {
            item.canonical_name for item in turn.retrievals if item.status == "resolved"
        }
        if resolved & set(turn.unavailable_card_names):
            failed.append("resolved_evidence_denied")
        if not set(turn.answer_card_names) <= resolved:
            failed.append("answer_card_identity_not_grounded")
        applicable = [
            review
            for review in reviews
            if review.case_id == case.id and review.independent
        ]
        semantic_failures = []
        missing_criteria = []
        for criterion in case.semantic_criteria:
            verdicts = [
                review.criteria.get(criterion, "unknown") for review in applicable
            ]
            if "fail" in verdicts:
                semantic_failures.append(criterion)
            elif not verdicts or "unknown" in verdicts:
                missing_criteria.append(criterion)
        if any(r.correctness < 3 or r.usefulness < 3 for r in applicable):
            semantic_failures.append("rubric_below_3_of_4")
        semantic_status = (
            "fail" if semantic_failures else "unknown" if missing_criteria else "pass"
        )
        latency_status = "pass" if turn.latency_ms <= case.max_latency_ms else "fail"
        status = (
            "fail"
            if failed or semantic_status == "fail" or latency_status == "fail"
            else "unknown" if semantic_status == "unknown" else "pass"
        )
        records.append(
            {
                "id": case.id,
                "status": status,
                "answer_sha256": answer_digest(turn.answer),
                "deterministic": {
                    "status": "fail" if failed else "pass",
                    "failures": failed,
                },
                "semantic": {
                    "status": semantic_status,
                    "failures": semantic_failures,
                    "unknown_criteria": missing_criteria,
                    "correctness": min(
                        (r.correctness for r in applicable), default=None
                    ),
                    "usefulness": min((r.usefulness for r in applicable), default=None),
                    "reviewers": len(applicable),
                },
                "latency": {
                    "status": latency_status,
                    "ms": turn.latency_ms,
                    "limit_ms": case.max_latency_ms,
                },
            }
        )
    statuses = {record["status"] for record in records}
    status = next(
        (s for s in ("fail", "incomplete", "unknown") if s in statuses), "pass"
    )
    return {
        "version": "assistant-conversation-report.v1",
        "status": status,
        "benchmark_sha256": digest,
        "revision": run.revision,
        "evidence_kind": run.evidence_kind,
        "quality_gate_pass": status == "pass" and run.evidence_kind == "live_capture",
        "broad_expertise_qualified": False,
        "cases": records,
    }


def compare(
    benchmark: Benchmark, candidate: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Compare evaluated matching runs; report every per-case regression."""
    if candidate["benchmark_sha256"] != baseline["benchmark_sha256"]:
        raise ValueError("Cannot compare different benchmarks")
    if candidate["evidence_kind"] != baseline["evidence_kind"]:
        raise ValueError("Cannot compare controlled outputs with live captures")
    before = {case["id"]: case for case in baseline["cases"]}
    regressions = []
    incomplete = False
    for after in candidate["cases"]:
        prior = before.get(after["id"])
        if (
            prior is None
            or "deterministic" not in prior
            or "deterministic" not in after
        ):
            incomplete = True
            continue
        for dimension in ("deterministic", "semantic", "latency"):
            if (
                prior[dimension]["status"] == "pass"
                and after[dimension]["status"] != "pass"
            ):
                regressions.append({"id": after["id"], "dimension": dimension})
        for metric in ("correctness", "usefulness"):
            old, new = prior["semantic"][metric], after["semantic"][metric]
            if old is not None and new is not None and new < old:
                regressions.append({"id": after["id"], "dimension": metric})
        if (
            after["latency"]["ms"]
            > prior["latency"]["ms"] * benchmark.latency_regression_ratio
        ):
            regressions.append({"id": after["id"], "dimension": "latency_ratio"})
    return {
        "status": "fail" if regressions else "incomplete" if incomplete else "pass",
        "regressions": regressions,
        "claim": "Development regression comparison; not proof of broad expertise or causal improvement.",
    }
