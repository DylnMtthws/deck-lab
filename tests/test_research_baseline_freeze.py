"""A frozen baseline stays honest only if its input hash is checked.

The hash exists to separate two things that look identical from outside: a
number still describing the current question set, and a number describing a set
that has since been edited. So it must move when the labels or the plans move,
and it must NOT move when someone rewrites a review note — a hash that broke on
prose would be re-frozen reflexively and would stop meaning anything.
"""

from __future__ import annotations

from datetime import date

import pytest

from sabermetrics.assistant.eval.baseline import (
    BASELINE_DIR,
    adjudication_set,
    evaluation_inputs_sha256,
    frozen_baselines,
)
from sabermetrics.assistant.eval.models import GoldenQuestionSet, load_questions
from sabermetrics.assistant.eval.plans import (
    HandWrittenPlan,
    HandWrittenPlanSet,
    load_hand_written_plans,
)
from sabermetrics.assistant.ir import ResearchPlan


@pytest.fixture(scope="module")
def questions():
    return load_questions()


@pytest.fixture(scope="module")
def plans():
    return load_hand_written_plans()


def _plan(question_id: str, **overrides) -> HandWrittenPlan:
    payload = {
        "question_id": question_id,
        "plan": ResearchPlan(
            intent="a synthetic plan intent", clarification_required=True
        ),
        "rationale": "a synthetic rationale",
        "author": "test",
        "authored_on": date(2026, 9, 10),
        "review_status": "draft",
    }
    payload.update(overrides)
    return HandWrittenPlan(**payload)


def _set(*plans_: HandWrittenPlan) -> HandWrittenPlanSet:
    return HandWrittenPlanSet(schema_version="research-r3-plans.v1", plans=plans_)


def _questions(*questions_) -> GoldenQuestionSet:
    return GoldenQuestionSet(questions=list(questions_))


def test_the_hash_ignores_review_prose(questions, plans):
    """Rewriting a note or promoting an author must not stale a baseline."""
    original = plans.plans[0]
    reworded = original.model_copy(
        update={
            "rationale": "a completely different rationale",
            "author": "somebody else",
            "review_note": "an entirely new note about what to confirm",
        }
    )
    edited = _set(reworded, *plans.plans[1:])
    assert edited.sha256() != plans.sha256(), "the plan-file hash should notice prose"
    assert evaluation_inputs_sha256(questions, edited) == evaluation_inputs_sha256(
        questions, plans
    )


def test_the_hash_moves_when_a_label_moves(questions, plans):
    """A card added to a required list is a different measurement."""
    scored = next(
        question for question in questions.questions if question.required_oracle_ids
    )
    relabelled = scored.model_copy(
        update={"required_oracle_ids": [*scored.required_oracle_ids, "an-added-id"]}
    )
    edited = _questions(
        *(
            relabelled if question.id == scored.id else question
            for question in questions.questions
        )
    )
    assert evaluation_inputs_sha256(edited, plans) != evaluation_inputs_sha256(
        questions, plans
    )


def test_the_hash_moves_when_a_plan_bound_moves(questions, plans):
    """Widening a window changes what the plan can return, so it counts."""
    original = next(
        plan
        for plan in plans.plans
        if plan.plan.steps and getattr(plan.plan.steps[0], "query", None)
    )
    step = original.plan.steps[0]
    widened = original.model_copy(
        update={
            "plan": original.plan.model_copy(
                update={
                    "steps": (
                        step.model_copy(
                            update={
                                "query": step.query.model_copy(
                                    update={"top_k": step.query.top_k + 1}
                                )
                            }
                        ),
                        *original.plan.steps[1:],
                    )
                }
            )
        }
    )
    edited = _set(
        *(
            widened if plan.question_id == original.question_id else plan
            for plan in plans.plans
        )
    )
    assert evaluation_inputs_sha256(questions, edited) != evaluation_inputs_sha256(
        questions, plans
    )


def test_every_frozen_baseline_still_describes_the_checked_in_inputs(questions, plans):
    """The staleness guard. A mismatch means the number outlived its inputs."""
    baselines = frozen_baselines()
    if not baselines:
        pytest.skip("no frozen baseline yet")
    current = evaluation_inputs_sha256(questions, plans)
    stale = sorted(
        key for key, value in baselines.items() if value.inputs_sha256 != current
    )
    assert not stale, (
        f"frozen baseline(s) {stale} were measured over questions or plans that "
        "have since changed. Re-run G2 and re-freeze rather than quoting the "
        f"old number; see {BASELINE_DIR}"
    )


def test_a_frozen_baseline_names_its_adjudication_set_and_its_limits():
    baselines = frozen_baselines()
    if not baselines:
        pytest.skip("no frozen baseline yet")
    assert adjudication_set() in baselines, (
        "the current adjudication set has no frozen baseline; a ruling that "
        "changed a label needs its own preserved result"
    )
    for baseline in baselines.values():
        assert baseline.limitations, "a preserved number must state what it is not"
        assert baseline.kind in {"development_baseline", "authoritative_gate"}
