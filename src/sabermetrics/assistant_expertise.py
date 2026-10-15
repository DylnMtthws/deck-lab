"""Offline, blinded expert comparisons; never equate model votes with expertise.

Inputs are operator-collected evidence, not a proof of reviewer identity or a
substitute for executing the assistant. Protected policy and reviewer enrollment
are trust roots. No model, network, database, or deployment calls occur here.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DOMAIN_NAMES = (
    "rules",
    "mana_and_sequencing",
    "synergy_and_combos",
    "deck_construction",
    "metagame_and_statistics",
    "uncertainty_and_constraints",
)
RUBRIC = ("correctness", "grounding", "strategic_fit", "tradeoffs", "uncertainty")
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=160)]
Slot = Literal["A", "B"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Reference(Strict):
    id: Identifier
    text: str = Field(min_length=1, max_length=16000)
    source: str = Field(min_length=1, max_length=500)


class Case(Strict):
    id: Identifier
    domain: Literal[
        "rules",
        "mana_and_sequencing",
        "synergy_and_combos",
        "deck_construction",
        "metagame_and_statistics",
        "uncertainty_and_constraints",
    ]
    commander: Identifier
    archetype: Identifier
    author_ids: list[Identifier] = Field(min_length=1, max_length=8)
    prompt: str = Field(min_length=1, max_length=4000)
    context: str = Field(min_length=1, max_length=20000)
    references: list[Reference] = Field(min_length=1, max_length=12)


class Benchmark(Strict):
    version: Literal["assistant-expertise-benchmark.v1"]
    split: Literal["sealed_holdout", "development"]
    sealed_at: str
    curator_id: Identifier
    cases: list[Case] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_cases(self):
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate benchmark case IDs")
        for case in self.cases:
            ids = [reference.id for reference in case.references]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate reference IDs")
        signatures = [
            digest({"prompt": c.prompt, "context": c.context}) for c in self.cases
        ]
        if len(set(signatures)) != len(signatures):
            raise ValueError("Duplicate questions/context cannot inflate coverage")
        timestamp(self.sealed_at)
        return self


class Identity(Strict):
    revision: Revision
    model_id: Identifier
    config_sha256: Digest
    corpus_sha256: Digest
    contributor_ids: list[Identifier] = Field(min_length=1, max_length=30)


class Citation(Strict):
    reference_id: Identifier
    quote: str = Field(min_length=12, max_length=2000)


class Answer(Strict):
    text: str = Field(min_length=1, max_length=12000)
    citations: list[Citation] = Field(max_length=30)


class Pair(Strict):
    case_id: Identifier
    candidate: Answer
    baseline: Answer


class Run(Strict):
    version: Literal["assistant-expertise-run.v1"]
    benchmark_sha256: Digest
    created_at: str
    candidate: Identity
    baseline: Identity
    total_cost_usd: float = Field(ge=0, le=1000, allow_inf_nan=False)
    pairs: list[Pair] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_pairs(self):
        ids = [pair.case_id for pair in self.pairs]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate run case IDs")
        timestamp(self.created_at)
        return self


class Review(Strict):
    case_id: Identifier
    packet_sha256: Digest
    reviewer_id: Identifier
    kind: Literal["human_expert", "model_judge"]
    scores: dict[Slot, dict[str, Annotated[int, Field(ge=0, le=4)]]]
    critical_errors: dict[Slot, list[Identifier]]
    preferred: Literal["A", "B", "tie"]
    rationale: str = Field(min_length=20, max_length=3000)

    @model_validator(mode="after")
    def complete_rubric(self):
        if set(self.scores) != {"A", "B"} or set(self.critical_errors) != {"A", "B"}:
            raise ValueError("Both blinded sides must be reviewed")
        if any(set(scores) != set(RUBRIC) for scores in self.scores.values()):
            raise ValueError("Every rubric dimension must be scored")
        return self


class Reviewer(Strict):
    person_id: Identifier
    domains: list[str] = Field(min_length=1, max_length=6)


class Policy(Strict):
    version: Literal["assistant-expertise-policy.v1"]
    min_cases: int = Field(ge=1)
    min_commanders: int = Field(ge=1)
    min_archetypes: int = Field(ge=1)
    min_cases_per_domain: int = Field(ge=1)
    reviews_per_case: int = Field(ge=2)
    min_dimension_score: int = Field(ge=1, le=4)
    min_pass_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_domain_pass_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    max_regression_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_decisive_commander_groups: int = Field(ge=1)
    min_group_win_lower_bound: float = Field(ge=0.5, le=1, allow_inf_nan=False)
    max_age_days: int = Field(ge=1, le=90)
    max_benchmark_age_days: int = Field(ge=1, le=365)
    max_total_cost_usd: float = Field(gt=0, le=100, allow_inf_nan=False)
    approved_curators: list[Identifier]
    approved_benchmarks: list[Digest]
    approved_experts: dict[Identifier, Reviewer]


class Key(Strict):
    version: Literal["assistant-expertise-key.v1"]
    benchmark_sha256: Digest
    run_sha256: Digest
    packet_sha256: Digest
    candidate_slots: dict[Identifier, Slot]


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Evaluation timestamps need an explicit timezone")
    return parsed.astimezone(UTC)


def digest(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _validate_pairs(benchmark: Benchmark, run: Run) -> None:
    if run.benchmark_sha256 != digest(benchmark):
        raise ValueError("Run does not match the frozen benchmark")
    if {p.case_id for p in run.pairs} != {c.id for c in benchmark.cases}:
        raise ValueError("Run must cover exactly every benchmark case")


def _packet(benchmark: Benchmark, run: Run, slots: dict[str, Slot]) -> dict[str, Any]:
    _validate_pairs(benchmark, run)
    if set(slots) != {c.id for c in benchmark.cases}:
        raise ValueError("Blinding map must cover exactly every case")
    pairs = {pair.case_id: pair for pair in run.pairs}
    cases = []
    for case in benchmark.cases:
        pair, slot = pairs[case.id], slots[case.id]
        cases.append(
            {
                "id": case.id,
                "domain": case.domain,
                "prompt": case.prompt,
                "context": case.context,
                "references": [ref.model_dump() for ref in case.references],
                "answers": {
                    slot: pair.candidate.model_dump(),
                    "B" if slot == "A" else "A": pair.baseline.model_dump(),
                },
            }
        )
    return {
        "version": "assistant-expertise-packet.v1",
        "rubric": list(RUBRIC),
        "cases": cases,
    }


def prepare_packet(benchmark: Benchmark, run: Run) -> tuple[dict[str, Any], Key]:
    """Random, balanced A/B labels; keep the key away from answerers/reviewers."""
    labels: list[Slot] = [
        "A" if i % 2 == 0 else "B" for i in range(len(benchmark.cases))
    ]
    secrets.SystemRandom().shuffle(labels)
    slots = {case.id: label for case, label in zip(benchmark.cases, labels)}
    packet = _packet(benchmark, run, slots)
    key = Key(
        version="assistant-expertise-key.v1",
        benchmark_sha256=digest(benchmark),
        run_sha256=digest(run),
        packet_sha256=digest(packet),
        candidate_slots=slots,
    )
    return packet, key


def wilson_lower(wins: int, total: int) -> float | None:
    """95% Wilson lower bound, reported for grouped paired preference votes."""
    if not total:
        return None
    z, rate = 1.959963984540054, wins / total
    return (
        rate
        + z * z / (2 * total)
        - z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    ) / (1 + z * z / total)


def evaluate(
    benchmark: Benchmark,
    run: Run,
    key: Key,
    reviews: list[Review],
    policy: Policy,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Emit blocked/failed/qualified_no_gain/qualified_improvement with reasons.

    Unknown human reviewers, incomplete coverage, stale data and disagreements
    never disappear from the denominator. Model judges cannot satisfy quorum.
    """
    _validate_pairs(benchmark, run)
    if (
        key.benchmark_sha256 != digest(benchmark)
        or key.run_sha256 != digest(run)
        or key.packet_sha256 != digest(_packet(benchmark, run, key.candidate_slots))
    ):
        raise ValueError("Review key or run content changed after blinding")
    current = now or datetime.now(UTC)
    blocked, failures = [], []
    if benchmark.split != "sealed_holdout":
        blocked.append("Development cases cannot qualify broad expertise")
    if benchmark.curator_id not in policy.approved_curators:
        blocked.append("Benchmark curator is not enrolled")
    if digest(benchmark) not in policy.approved_benchmarks:
        blocked.append("Benchmark digest is not enrolled in trusted policy")
    if timestamp(benchmark.sealed_at) > timestamp(run.created_at):
        blocked.append("Benchmark was not sealed before answer collection")
    age = current - timestamp(run.created_at)
    if age < timedelta(0) or age > timedelta(days=policy.max_age_days):
        blocked.append("Run is future-dated or stale")
    if current - timestamp(benchmark.sealed_at) > timedelta(
        days=policy.max_benchmark_age_days
    ):
        blocked.append("Benchmark requires freshness review")
    if run.total_cost_usd > policy.max_total_cost_usd:
        failures.append("Run exceeds evaluation cost budget")
    counts: Counter[str] = Counter(case.domain for case in benchmark.cases)
    if (
        len(benchmark.cases) < policy.min_cases
        or len({c.commander for c in benchmark.cases}) < policy.min_commanders
        or len({c.archetype for c in benchmark.cases}) < policy.min_archetypes
        or any(counts[d] < policy.min_cases_per_domain for d in DOMAIN_NAMES)
    ):
        blocked.append(
            "Insufficient benchmark size or domain/commander/archetype coverage"
        )
    if not policy.approved_experts:
        blocked.append("No independent experts have been enrolled")

    def person(identifier: str) -> str:
        enrolled = policy.approved_experts.get(identifier)
        return enrolled.person_id if enrolled else identifier

    contributors = {
        person(x) for x in run.candidate.contributor_ids + run.baseline.contributor_ids
    }
    if run.candidate == run.baseline:
        blocked.append("Candidate and baseline system identities are identical")
    if person(benchmark.curator_id) in contributors:
        blocked.append("Benchmark curator contributed to the compared systems")
    if any(
        contributors & {person(x) for x in case.author_ids} for case in benchmark.cases
    ):
        blocked.append("Benchmark question authors contributed to the compared systems")
    cases = {case.id: case for case in benchmark.cases}
    by_case: dict[str, list[Review]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    advisory = 0
    for review in reviews:
        if review.case_id not in cases or review.packet_sha256 != key.packet_sha256:
            raise ValueError("Review references a different packet or unknown case")
        if review.kind == "model_judge":
            advisory += 1
            continue
        expert = policy.approved_experts.get(review.reviewer_id)
        if expert is None:
            blocked.append(f"Unenrolled expert: {review.reviewer_id}")
            continue
        case = cases[review.case_id]
        identity = (case.id, expert.person_id)
        if identity in seen:
            raise ValueError("Duplicate reviewer/person for one case")
        seen.add(identity)
        if (
            case.domain not in expert.domains
            or expert.person_id in contributors
            or expert.person_id in {person(x) for x in case.author_ids}
        ):
            blocked.append(
                f"Reviewer lacks domain enrollment or independence: {case.id}"
            )
            continue
        by_case[case.id].append(review)
    outcomes: list[dict[str, Any]] = []
    commander_votes: dict[str, Counter[str]] = defaultdict(Counter)
    pairs = {pair.case_id: pair for pair in run.pairs}
    for case in benchmark.cases:
        votes, reasons = by_case[case.id], []
        slot = key.candidate_slots[case.id]
        if len(votes) < policy.reviews_per_case:
            blocked.append(f"Missing independent expert quorum: {case.id}")
        refs = {ref.id: ref.text for ref in case.references}
        for citation in pairs[case.id].candidate.citations:
            if citation.quote not in refs.get(citation.reference_id, ""):
                reasons.append("Candidate fabricated or mismatched an exact citation")
        preferences = {
            (
                "tie"
                if r.preferred == "tie"
                else "candidate" if r.preferred == slot else "baseline"
            )
            for r in votes
        }
        if pairs[case.id].candidate == pairs[case.id].baseline and preferences - {
            "tie"
        }:
            blocked.append(
                f"Identical answers received a non-tie preference: {case.id}"
            )
        if "candidate" in preferences and "baseline" in preferences:
            blocked.append(f"Unresolved expert disagreement: {case.id}")
        for vote in votes:
            if vote.critical_errors[slot]:
                reasons.append("Expert found a critical error")
            if min(vote.scores[slot].values()) < policy.min_dimension_score:
                reasons.append("Candidate missed a rubric floor")
        preference = (
            "baseline"
            if "baseline" in preferences
            else "candidate" if preferences == {"candidate"} else "tie"
        )
        passed = len(votes) >= policy.reviews_per_case and not reasons
        if any(vote.critical_errors[slot] for vote in votes) or any(
            "citation" in r for r in reasons
        ):
            failures.append(f"Critical factual failure: {case.id}")
        commander_votes[case.commander][preference] += 1
        outcomes.append(
            {
                "case_id": case.id,
                "domain": case.domain,
                "passed": passed,
                "preference": preference,
                "expert_reviews": len(votes),
                "reasons": sorted(set(reasons)),
            }
        )
    total = len(outcomes)
    rate = sum(case["passed"] for case in outcomes) / total
    domain_rates = {
        d: {
            "passed": sum(c["passed"] for c in outcomes if c["domain"] == d),
            "total": counts[d],
        }
        for d in DOMAIN_NAMES
    }
    if rate < policy.min_pass_fraction:
        failures.append("Absolute quality pass fraction below policy")
    if any(
        v["total"] and v["passed"] / v["total"] < policy.min_domain_pass_fraction
        for v in domain_rates.values()
    ):
        failures.append("At least one domain fell below its quality floor")
    regressions = sum(c["preference"] == "baseline" for c in outcomes)
    if regressions / total > policy.max_regression_fraction:
        failures.append("Too many cases prefer the baseline")
    group_wins = sum(v["candidate"] > v["baseline"] for v in commander_votes.values())
    group_losses = sum(v["baseline"] > v["candidate"] for v in commander_votes.values())
    decisive = group_wins + group_losses
    lower = wilson_lower(group_wins, decisive)
    gain = (
        decisive >= policy.min_decisive_commander_groups
        and lower is not None
        and lower > policy.min_group_win_lower_bound
    )
    status = (
        "blocked"
        if blocked
        else (
            "failed"
            if failures
            else "qualified_improvement" if gain else "qualified_no_gain"
        )
    )
    return {
        "version": "assistant-expertise-report.v1",
        "status": status,
        "promotion_eligible": status == "qualified_improvement",
        "benchmark_sha256": digest(benchmark),
        "run_sha256": digest(run),
        "policy_sha256": digest(policy),
        "packet_sha256": key.packet_sha256,
        "candidate": run.candidate.model_dump(),
        "baseline": run.baseline.model_dump(),
        "cases": total,
        "pass_fraction": rate,
        "by_domain": domain_rates,
        "baseline_preferred_cases": regressions,
        "commander_group_wins": group_wins,
        "commander_group_losses": group_losses,
        "group_win_wilson_lower_95": lower,
        "advisory_model_reviews": advisory,
        "blocked_reasons": sorted(set(blocked)),
        "failures": sorted(set(failures)),
        "case_results": outcomes,
        "scope": "This frozen benchmark and reviewed system identities only. Eligibility is evidence for owner review, never deployment authorization or universal MTG expertise.",
    }
