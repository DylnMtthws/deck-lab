"""Freezing an evaluation result, and the input hash that keeps it honest.

A scorecard is a measurement of one substrate against one set of labels and one
set of plans. Quoting the number later is only meaningful if those inputs have
not moved since, and "has not moved" is exactly the kind of claim that decays
silently: a label gains a card, a plan gains a bound, and the frozen number
keeps being quoted as though it still described the current set.

So a frozen baseline carries a hash of its inputs, and a test recomputes that
hash from the checked-in files. Editing a label or a plan step does not corrupt
the baseline — it makes the baseline visibly stale, which forces an explicit
re-freeze rather than an unnoticed drift.

The hash covers what DETERMINES A SCORE and deliberately nothing else. Review
notes, rationales, authorship and prose can be rewritten freely; required and
forbidden ids, alternatives, counterexamples, pending markers and the plan IR
itself cannot. A freeze that broke every time someone improved a comment would
be re-frozen reflexively, which is the same as not having one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant.eval.models import GoldenQuestion, GoldenQuestionSet
from sabermetrics.assistant.eval.plans import HandWrittenPlanSet

EVALUATION_INPUTS_VERSION: Literal["research-evaluation-inputs.v1"] = (
    "research-evaluation-inputs.v1"
)
FROZEN_BASELINE_SCHEMA: Literal["research-frozen-baseline.v1"] = (
    "research-frozen-baseline.v1"
)
ROOT = Path(__file__).resolve().parents[4]
ADJUDICATIONS_PATH = ROOT / "fixtures" / "research" / "adjudications.yaml"
BASELINE_DIR = ROOT / "fixtures" / "research" / "baseline"


def load_adjudications(path: Path = ADJUDICATIONS_PATH) -> dict[str, Any]:
    """Read the owner-adjudication file, or an empty set if there is none."""
    if not path.is_file():
        return {}
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def adjudication_set(path: Path = ADJUDICATIONS_PATH) -> str:
    """Return the versioned adjudication set the labels currently reflect."""
    return str(load_adjudications(path).get("adjudication_set") or "none")


def _question_semantics(question: GoldenQuestion) -> dict[str, Any]:
    """The part of a question that can change a score."""
    return {
        "id": question.id,
        "clarified_ask": question.clarified_ask,
        "required_oracle_ids": sorted(question.required_oracle_ids),
        "satisfied_by_any_of": sorted(question.satisfied_by_any_of),
        "forbidden_oracle_ids": sorted(question.forbidden_oracle_ids),
        "counterexample_oracle_ids": sorted(question.counterexample_oracle_ids),
        "unscored_pending": question.unscored_pending,
        "expected_absences": sorted(question.expected_absences),
        "category": question.category,
        "context_id": question.context_id,
        "clarification_expected": question.clarification_expected,
        "no_finding_expected": question.no_finding_expected,
    }


def evaluation_inputs_sha256(
    question_set: GoldenQuestionSet,
    plans: HandWrittenPlanSet,
    *,
    adjudications: Path = ADJUDICATIONS_PATH,
) -> str:
    """Hash every input that can move a G2 number, and nothing that cannot.

    Args:
        question_set: The merged golden questions.
        plans: The hand-written plan set.
        adjudications: Path to the owner-adjudication file.

    Returns:
        A hex digest over the score-determining content of all three.
    """
    rulings = load_adjudications(adjudications)
    payload = {
        "version": EVALUATION_INPUTS_VERSION,
        "questions": sorted(
            (_question_semantics(question) for question in question_set.questions),
            key=lambda entry: str(entry["id"]),
        ),
        # The plan IR, not the plan file: an author, a date and a review note
        # are review metadata, and none of them reaches the executor.
        "plans": sorted(
            (
                {
                    "question_id": plan.question_id,
                    "plan": plan.plan.model_dump(mode="json"),
                }
                for plan in plans.plans
            ),
            key=lambda entry: str(entry["question_id"]),
        ),
        "adjudication_set": str(rulings.get("adjudication_set") or "none"),
        "ratifies_golden_set": bool(rulings.get("ratifies_golden_set", False)),
        "rulings": sorted(
            (
                {
                    "question_id": str(entry.get("question_id")),
                    "ruling": str(entry.get("ruling")),
                }
                for entry in (rulings.get("adjudications") or ())
            ),
            key=lambda entry: (entry["question_id"], entry["ruling"]),
        ),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FrozenBaseline(BaseModel):
    """One preserved evaluation result and the inputs it was measured over."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-frozen-baseline.v1"] = FROZEN_BASELINE_SCHEMA
    #: What this baseline IS. A development measurement is not a gate result,
    #: and the field exists so quoting one as the other requires editing it.
    kind: Literal["development_baseline", "authoritative_gate"]
    adjudication_set: str = Field(min_length=1)
    frozen_on: date
    #: Recomputable from the checked-in questions, plans and adjudications. A
    #: mismatch means the inputs moved and the numbers below describe a set
    #: that no longer exists.
    inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plans_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_id: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_row_count: int = Field(ge=1)
    embedding_model_id: str
    embedding_revision: str
    reranker_model_id: str
    reranker_revision: str
    retrieval_passed: int = Field(ge=0)
    retrieval_applicable: int = Field(ge=0)
    retrieval_failed: list[str]
    discovery_passed: int = Field(ge=0)
    discovery_applicable: int = Field(ge=0)
    name_lookup_count: int = Field(ge=0)
    unscored_pending: dict[str, str]
    alternative_coverage: dict[str, dict[str, int]]
    #: What this measurement does NOT establish. Free text, required, and read
    #: by a human rather than by code.
    limitations: list[str] = Field(min_length=1)

    @property
    def pass_rate(self) -> float:
        """Passed over applicable, or zero when nothing was applicable."""
        if not self.retrieval_applicable:
            return 0.0
        return self.retrieval_passed / self.retrieval_applicable


def load_frozen_baseline(path: Path) -> FrozenBaseline:
    """Load and validate one frozen baseline file."""
    return FrozenBaseline.model_validate(json.loads(path.read_text(encoding="utf-8")))


def frozen_baselines(directory: Path = BASELINE_DIR) -> dict[str, FrozenBaseline]:
    """Load every frozen baseline, keyed by adjudication set."""
    if not directory.is_dir():
        return {}
    return {
        baseline.adjudication_set: baseline
        for baseline in (
            load_frozen_baseline(path) for path in sorted(directory.glob("*.json"))
        )
    }
