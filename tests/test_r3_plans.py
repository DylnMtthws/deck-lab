"""The hand-written plan artefact answers the golden set, and does not cheat.

A plan author holds the answer key. These tests are what stops that from
quietly turning G2 into a measure of the author rather than of the substrate.
"""

from __future__ import annotations

import re

import pytest

from sabermetrics.assistant.eval.models import load_questions
from sabermetrics.assistant.eval.plans import (
    CARD_PRODUCING_KINDS,
    PLAN_SET_SCHEMA,
    PlanSetError,
    check_plan_coverage,
    load_hand_written_plans,
    mentions_card_name,
    plans_naming_the_answer,
)
from sabermetrics.assistant.eval.runner import (
    CAPABILITY_ABSENCES,
    STRUCTURAL_ABSENCES,
    load_label_id_map,
)
from sabermetrics.assistant.ir import CardSearchStep, RulesLookupStep

_ORACLE_ID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def questions():
    return load_questions()


@pytest.fixture(scope="module")
def plans():
    return load_hand_written_plans()


@pytest.fixture(scope="module")
def id_map():
    return load_label_id_map()


def test_every_golden_question_has_exactly_one_plan(questions, plans):
    planned = plans.by_question_id
    assert len(plans.plans) == len(planned), "a question is planned twice"
    assert set(planned) == {question.id for question in questions.questions}


def test_the_plan_set_covers_the_golden_set(questions, plans):
    check_plan_coverage(plans, questions)


def test_coverage_refuses_a_missing_plan(questions, plans):
    thinner = plans.model_copy(update={"plans": plans.plans[1:]})
    with pytest.raises(PlanSetError, match="no plan for"):
        check_plan_coverage(thinner, questions)


def test_the_artefact_pins_its_schema(plans):
    assert plans.schema_version == PLAN_SET_SCHEMA


def test_every_plan_is_attributable_and_drafts_say_what_to_check(plans):
    for plan in plans.plans:
        assert plan.author.strip(), plan.question_id
        assert plan.authored_on is not None, plan.question_id
        assert plan.rationale.strip(), plan.question_id
        if plan.review_status == "draft":
            assert plan.review_note.strip(), plan.question_id


def test_no_plan_is_owner_verified_yet(plans):
    """R3 ships drafts; an authoritative G2 refuses until Dylan reviews them.

    This is a real state, not a placeholder. If it ever fails because plans
    were promoted, the authoritative gate becomes reachable and that is a
    deliberate event worth noticing.
    """
    assert set(plans.unverified) == set(plans.by_question_id)


def test_plan_context_matches_the_question(questions, plans):
    planned = plans.by_question_id
    for question in questions.questions:
        assert planned[question.id].plan.context_id == question.context_id


def test_no_plan_contains_an_oracle_id(plans):
    for plan in plans.plans:
        serialized = plan.plan.model_dump_json()
        assert not _ORACLE_ID.search(serialized), plan.question_id


def test_no_plan_restricts_a_search_to_specific_cards(plans):
    """Only the executor may populate ``allowed_oracle_ids``."""
    for plan in plans.plans:
        for step in plan.plan.steps:
            filters = getattr(step, "filters", None) or getattr(
                getattr(step, "query", None), "filters", None
            )
            if filters is not None:
                assert filters.allowed_oracle_ids == (), plan.question_id


def test_no_plan_names_a_required_or_forbidden_card_the_ask_does_not(
    questions, plans, id_map
):
    """The one cheat the schema cannot catch: a card name in free text.

    Naming a card the asker already named is a legitimate lookup. Supplying a
    name the asker never wrote turns retrieval into a lookup table, so the
    plan set must contain none of those.
    """
    leaked = plans_naming_the_answer(plans, questions, id_map.names_by_label_id)
    assert leaked == {}, (
        "these plans supply a required card name their question does not: " f"{leaked}"
    )


def test_a_planted_card_name_is_detected(questions, plans, id_map):
    """The honesty check is not vacuous: planting a name must trip it."""
    candidates = [
        (question, name)
        for question in questions.questions
        for oracle_id in question.required_oracle_ids[:1]
        if (name := id_map.names_by_label_id.get(oracle_id))
        and name.casefold() not in f"{question.ask} {question.clarified_ask}".casefold()
        and any(
            isinstance(step, CardSearchStep)
            for step in plans.by_question_id[question.id].plan.steps
        )
    ]
    assert candidates, "no question left to plant a name into"
    question, name = candidates[0]
    hand = plans.by_question_id[question.id]
    steps = tuple(
        (
            step.model_copy(
                update={"query": step.query.model_copy(update={"text": f"find {name}"})}
            )
            if isinstance(step, CardSearchStep)
            else step
        )
        for step in hand.plan.steps
    )
    planted = plans.model_copy(
        update={
            "plans": (
                hand.model_copy(
                    update={"plan": hand.plan.model_copy(update={"steps": steps})}
                ),
            )
        }
    )
    found = plans_naming_the_answer(planted, questions, id_map.names_by_label_id)
    assert found.get(question.id) == (name,)


def test_an_answer_step_can_produce_the_cards_its_question_requires(questions, plans):
    planned = plans.by_question_id
    for question in questions.questions:
        if not question.required_oracle_ids:
            continue
        plan = planned[question.id].plan
        assert plan.answer_step is not None, question.id
        assert plan.step(plan.answer_step).kind in CARD_PRODUCING_KINDS, question.id


def test_a_zero_step_plan_declares_clarification_or_an_absence(plans):
    for plan in plans.plans:
        if plan.plan.steps:
            continue
        assert (
            plan.plan.clarification_required or plan.plan.stated_absences
        ), plan.question_id


def test_clarification_is_planned_exactly_where_it_is_expected(questions, plans):
    planned = plans.by_question_id
    for question in questions.questions:
        assert (
            planned[question.id].plan.clarification_required
            == question.clarification_expected
        ), question.id


def test_every_expected_absence_is_answered_by_a_stated_one(questions, plans):
    """An absence gate that only counts "something was stated" measures nothing."""
    planned = plans.by_question_id
    for question in questions.questions:
        if not question.expected_absences:
            continue
        answered = {
            absence.answers_expectation
            for absence in planned[question.id].plan.stated_absences
            if absence.answers_expectation is not None
        }
        assert answered == set(range(len(question.expected_absences))), (
            f"{question.id}: expected absences {question.expected_absences} "
            f"but the plan answers indices {sorted(answered)}"
        )


def test_every_stated_absence_reason_is_a_known_kind(plans):
    known = STRUCTURAL_ABSENCES | set(CAPABILITY_ABSENCES)
    for plan in plans.plans:
        for absence in plan.plan.stated_absences:
            assert absence.reason in known, f"{plan.question_id}: {absence.reason}"


def test_a_deferred_capability_is_named_rather_than_silently_skipped(questions, plans):
    """A question needing field_stats, a combo corpus or a study must say so."""
    planned = plans.by_question_id
    for question in questions.questions:
        if question.category != "metagame":
            continue
        reasons = {
            absence.reason for absence in planned[question.id].plan.stated_absences
        }
        assert "field_statistics_deferred_to_r5" in reasons, (
            f"{question.id} asks for a field statistic; the plan must state that "
            "field statistics are deferred rather than return a card and stop"
        )


def test_a_rules_plan_asks_the_rules_layer_rather_than_declaring_it_absent(
    questions, plans
):
    """A rules question must attempt a lookup, and must not pre-announce failure.

    These plans used to ALSO state ``rules_index_not_built``, which was true
    while no index existed. Now one does, and the runner grants a stated
    absence only to a plan whose run actually failed that way — so the
    declaration became a claim the run contradicts, and was refused. The
    typed absence still reaches the envelope on a machine with no index; it
    comes from the run, which is the only thing that can know.
    """
    planned = plans.by_question_id
    for question in questions.questions:
        if question.category != "rules":
            continue
        plan = planned[question.id].plan
        lookups = [step for step in plan.steps if isinstance(step, RulesLookupStep)]
        assert lookups, f"{question.id}: a rules question should attempt a rules lookup"
        # THE SHAPE IS THE CONTROL. Exactly one lookup at one fixed budget for
        # all ten, so the rules-support number is measured over a uniform,
        # unfitted shape. Decomposing by hand was measured to buy nothing that
        # did not come from newly authored text, and is R4 planner scope; see
        # the header of fixtures/research/r3_plans/rules.yaml.
        assert len(lookups) == 1, (
            f"{question.id}: {len(lookups)} rules_lookup steps; the R3 shape is "
            "one, and decomposition is measured by G3 against this control"
        )
        (lookup,) = lookups
        assert lookup.char_budget == 6300, (
            f"{question.id}: char_budget {lookup.char_budget}; the budget is "
            "the operating point the plans had under the 871-chunk index, "
            "pinned so two chunkings can be compared"
        )
        assert lookup.limit == 12, (
            f"{question.id}: limit {lookup.limit}; the chunk cap is 12 so it "
            "cannot bind before the character budget does"
        )
        assert "rules_index_not_built" not in {
            absence.reason for absence in plan.stated_absences
        }, (
            f"{question.id}: a plan cannot declare the rules index missing. "
            "Whether it is missing is a fact about the run, not about the plan"
        )


def test_search_text_stays_within_the_declared_bound(plans):
    """A step asking for more than the retrieval ceiling refuses at run time."""
    for plan in plans.plans:
        for step in plan.plan.steps:
            if isinstance(step, CardSearchStep):
                assert step.query.top_k <= 50, plan.question_id


def test_card_name_matching_ignores_case_on_both_sides():
    """A silent False here reads exactly like a clean anti-cheat result.

    An earlier version casefolded the card name but not the text it searched,
    so every caller passing ordinary prose got no matches on every capitalized
    card name — and both the plan-leak check and the name-lookup subset
    reported a confident zero over a check that never ran.
    """
    assert mentions_card_name("did I still cast Mental Misstep?", "Mental Misstep")
    assert mentions_card_name("MENTAL MISSTEP", "Mental Misstep")
    assert mentions_card_name(
        "play sink into stupor", "Sink into Stupor // Soporific Springs"
    )
    assert not mentions_card_name("Mental Misstepping", "Mental Misstep")
    assert not mentions_card_name("counter target spell", "Mental Misstep")


def test_the_name_lookup_subset_is_not_vacuous(questions, id_map):
    """Some golden asks really do print their own answer; the count says so.

    If this returns zero the detector is broken, not the question set: the
    rules category alone names its card in every ask.
    """
    from sabermetrics.assistant.eval.g2 import _asks_that_name_their_own_answer

    found = _asks_that_name_their_own_answer(questions, id_map)
    assert len(found) >= 10, found
    assert "rules-001" in found
    assert "mechanic-003" not in found, (
        "mechanic-003 asks for lands that double as interaction and names no "
        "card; counting it would overstate the name-lookup population"
    )
