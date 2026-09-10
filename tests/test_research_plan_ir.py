"""The Query IR refuses a plan before a plan can name its own answer.

Structural rules only: nothing here executes a step, so nothing here needs a
corpus — except the anti-cheat tests, which take their Oracle id from the
portable bundle. A literal card identity is exactly what a hand-written plan
would smuggle in to score perfect recall, so the id being refused is a real one
from the corpus a plan would actually run against, not a made-up UUID.
"""

from __future__ import annotations

from typing import get_args

import pytest

from sabermetrics.assistant.ir import (
    DEFERRED_STEP_KINDS,
    DanglingInputError,
    DeferredStepKindError,
    PlanNamesACardError,
    PlanValidationError,
    StepKind,
    UnknownStepKindError,
    UnknownTagError,
    parse_plan,
    validate_plan,
)
from sabermetrics.assistant.sources import BundleCardSource
from sabermetrics.mechanics.tags.registry import ALL_TAGS

#: Taken from the shipped library rather than written as a literal: the tag
#: vocabulary is versioned elsewhere, and pinning one of its members here would
#: turn a library edit into an IR failure.
SHIPPED_TAG = min(definition.id for definition in ALL_TAGS)

#: A tag the design names and the library does not ship yet.
UNSHIPPED_TAG = "draw:cantrip"

#: A registered deck context. Validation must not resolve it — that is the
#: executor's job — so any registered id would do.
CONTEXT_ID = "cedh:kinnan-basalt-fixture"


def _plan(**overrides):
    """A minimally valid plan payload, before the override under test."""
    payload = {
        "intent": "find the cheapest mana rocks this commander can use",
        "steps": [],
    }
    payload.update(overrides)
    return payload


def _search(step_id="hits", **overrides):
    step = {
        "id": step_id,
        "kind": "card_search",
        "query": {"text": "counter target spell", "top_k": 10},
        "field_absence": "not_a_field_query",
    }
    step.update(overrides)
    return step


def _tag_filter(step_id="rocks", **overrides):
    step = {
        "id": step_id,
        "kind": "tag_filter",
        "filters": {"required_tags": [SHIPPED_TAG]},
        "field_absence": "not_a_field_query",
    }
    step.update(overrides)
    return step


def _rules(step_id="rules", **overrides):
    step = {
        "id": step_id,
        "kind": "rules_lookup",
        "question": "when does a mana ability use the stack",
    }
    step.update(overrides)
    return step


def _union(step_id, inputs):
    return {"id": step_id, "kind": "union", "inputs": list(inputs)}


def _absence(**overrides):
    absence = {
        "reason": "price_is_not_in_the_engine",
        "detail": "the engine carries no price field to read",
    }
    absence.update(overrides)
    return absence


@pytest.fixture(scope="module")
def corpus_oracle_id(research_facade):
    """One real card identity, resolved through the substrate boundary."""
    source = BundleCardSource(research_facade)
    return source.resolve_names(["Sol Ring"])["Sol Ring"][0]


# -- the closed vocabulary --------------------------------------------------


def test_an_unknown_step_kind_is_refused_by_name_and_names_the_closed_set():
    with pytest.raises(UnknownStepKindError) as exc:
        parse_plan(
            _plan(steps=[{"id": "hits", "kind": "card_lookup"}], answer_step="hits")
        )
    message = str(exc.value)
    assert "card_lookup" in message
    for kind in get_args(StepKind):
        assert kind in message


#: Every deferred kind, and the phase the error must name.
_DEFERRED_CASES = (
    ("field_stats", "R5"),
    ("sim_study", "R6"),
    ("combo_lookup", "D4"),
    ("deck_diff", "R5"),
)


@pytest.mark.parametrize(("kind", "phase"), _DEFERRED_CASES)
def test_a_deferred_step_kind_names_the_phase_that_owns_it(kind, phase):
    # The step carries none of its required fields, so a passing assertion also
    # shows the kind is checked before the schema complains about anything else.
    with pytest.raises(DeferredStepKindError) as exc:
        parse_plan(_plan(steps=[{"id": "later", "kind": kind}], answer_step="later"))
    message = str(exc.value)
    assert kind in message
    assert phase in message


def test_every_deferred_step_kind_has_a_case_in_this_file():
    assert set(DEFERRED_STEP_KINDS) == {kind for kind, _ in _DEFERRED_CASES}


# -- the step graph ---------------------------------------------------------


def test_a_set_op_naming_a_step_declared_after_it_is_dangling():
    payload = _plan(
        steps=[_union("both", ["wide", "narrow"]), _search("wide"), _search("narrow")],
        answer_step="both",
    )
    with pytest.raises(DanglingInputError) as exc:
        parse_plan(payload)
    assert "not declared before it" in str(exc.value)


def test_a_step_naming_itself_is_dangling():
    payload = _plan(
        steps=[
            _search("wide"),
            {"id": "loop", "kind": "difference", "left": "loop", "right": "wide"},
        ],
        answer_step="loop",
    )
    with pytest.raises(DanglingInputError) as exc:
        parse_plan(payload)
    assert "'loop'" in str(exc.value)


def test_an_answer_step_that_is_not_a_declared_step_is_dangling():
    with pytest.raises(DanglingInputError) as exc:
        parse_plan(_plan(steps=[_search("hits")], answer_step="somewhere_else"))
    assert "is not a declared step" in str(exc.value)


def test_duplicate_step_ids_are_refused():
    payload = _plan(steps=[_search("hits"), _search("hits")], answer_step="hits")
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "duplicate step ids: hits" in str(exc.value)


def test_a_set_op_consuming_a_rules_lookup_is_refused():
    payload = _plan(
        steps=[_search("hits"), _rules("rules"), _union("both", ["hits", "rules"])],
        answer_step="both",
    )
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "produces no cards" in str(exc.value)


def test_step_lookup_raises_for_an_id_the_plan_does_not_declare():
    plan = parse_plan(_plan(steps=[_search()], answer_step="hits"))
    assert plan.step_ids == ("hits",)
    with pytest.raises(KeyError):
        plan.step("missing")


def test_validate_plan_catches_a_plan_that_was_built_around_construction():
    plan = parse_plan(_plan(steps=[_search()], answer_step="hits"))
    # model_copy skips validators, which is the whole reason validate_plan is
    # exposed separately from the constructor.
    smuggled = plan.model_copy(update={"answer_step": "nope"})
    with pytest.raises(DanglingInputError):
        validate_plan(smuggled)


# -- zero-step plans and the answer step ------------------------------------


def test_a_zero_step_plan_that_declares_nothing_is_refused():
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(_plan())
    assert "require clarification or state an absence" in str(exc.value)


def test_a_zero_step_plan_that_requires_clarification_is_accepted():
    plan = parse_plan(_plan(clarification_required=True))
    assert plan.steps == ()
    assert plan.answer_step is None
    assert plan.clarification_required


def test_a_zero_step_plan_that_states_an_absence_is_accepted():
    plan = parse_plan(_plan(stated_absences=[_absence()]))
    assert plan.steps == ()
    assert plan.answer_step is None
    assert plan.stated_absences[0].reason == "price_is_not_in_the_engine"


def test_a_plan_with_steps_must_name_its_answer_step():
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(_plan(steps=[_search()]))
    assert "must name its answer step" in str(exc.value)


def test_a_plan_with_no_steps_must_not_name_an_answer_step():
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(_plan(clarification_required=True, answer_step="hits"))
    assert "cannot name an answer step" in str(exc.value)


def test_a_plan_cannot_both_require_clarification_and_run_steps():
    payload = _plan(steps=[_search()], answer_step="hits", clarification_required=True)
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "must not also run steps" in str(exc.value)


def test_two_absences_cannot_answer_the_same_expectation():
    payload = _plan(
        stated_absences=[
            _absence(answers_expectation=0),
            _absence(reason="collection_is_not_in_the_engine", answers_expectation=0),
        ]
    )
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "same expectation" in str(exc.value)


# -- the anti-cheat rule ----------------------------------------------------


def _intent_names_a_card(oracle_id):
    return _plan(
        intent=f"explain why {oracle_id} belongs in this list",
        clarification_required=True,
    )


def _note_names_a_card(oracle_id):
    return _plan(
        steps=[_search(note=f"the asker already owns {oracle_id}")],
        answer_step="hits",
    )


def _query_text_names_a_card(oracle_id):
    return _plan(
        steps=[_search(query={"text": f"cards like {oracle_id}", "top_k": 10})],
        answer_step="hits",
    )


def _rules_question_names_a_card(oracle_id):
    return _plan(
        steps=[_rules(question=f"how does {oracle_id} resolve")],
        answer_step="rules",
    )


_CARD_NAMING_PLANS = (
    ("intent", _intent_names_a_card),
    ("step_note", _note_names_a_card),
    ("query_text", _query_text_names_a_card),
    ("rules_question", _rules_question_names_a_card),
)


@pytest.mark.parametrize(
    "build",
    [build for _, build in _CARD_NAMING_PLANS],
    ids=[name for name, _ in _CARD_NAMING_PLANS],
)
def test_a_plan_naming_an_oracle_id_anywhere_is_refused(build, corpus_oracle_id):
    with pytest.raises(PlanNamesACardError) as exc:
        parse_plan(build(corpus_oracle_id))
    assert "may not contain an Oracle id" in str(exc.value)


@pytest.mark.parametrize("kind", ["card_search", "tag_filter"])
def test_a_step_populating_allowed_oracle_ids_is_refused(kind, corpus_oracle_id):
    if kind == "card_search":
        step = _search(
            query={"filters": {"allowed_oracle_ids": [corpus_oracle_id]}, "top_k": 10}
        )
    else:
        step = _tag_filter(filters={"allowed_oracle_ids": [corpus_oracle_id]})
    payload = _plan(steps=[step], answer_step=step["id"])
    with pytest.raises(PlanNamesACardError):
        parse_plan(payload)


def test_allowed_oracle_ids_is_refused_even_when_the_identity_is_not_a_uuid():
    # Proves the rule is the field, not the UUID pattern: a corpus that keyed
    # cards some other way would still be closed to a hand-written plan.
    payload = _plan(
        steps=[_tag_filter(filters={"allowed_oracle_ids": ["kinnan-basalt-piece"]})],
        answer_step="rocks",
    )
    with pytest.raises(PlanNamesACardError) as exc:
        parse_plan(payload)
    assert "allowed_oracle_ids" in str(exc.value)


# -- tags, scope and predicates ---------------------------------------------


@pytest.mark.parametrize("field", ["required_tags", "any_tags", "excluded_tags"])
def test_an_unshipped_mechanic_tag_is_refused_by_name(field):
    payload = _plan(
        steps=[_tag_filter(filters={field: [UNSHIPPED_TAG]})], answer_step="rocks"
    )
    with pytest.raises(UnknownTagError) as exc:
        parse_plan(payload)
    assert UNSHIPPED_TAG in str(exc.value)


def test_an_unshipped_mechanic_tag_in_a_card_search_is_refused_too():
    payload = _plan(
        steps=[
            _search(
                query={
                    "text": "one mana draw spells",
                    "filters": {"required_tags": [UNSHIPPED_TAG]},
                    "top_k": 10,
                }
            )
        ],
        answer_step="hits",
    )
    with pytest.raises(UnknownTagError) as exc:
        parse_plan(payload)
    assert UNSHIPPED_TAG in str(exc.value)


def test_a_shipped_mechanic_tag_is_accepted():
    plan = parse_plan(_plan(steps=[_tag_filter()], answer_step="rocks"))
    assert plan.step("rocks").filters.required_tags == (SHIPPED_TAG,)


@pytest.mark.parametrize(
    "step",
    [_search(scope="deck"), _tag_filter(scope="deck")],
    ids=["card_search", "tag_filter"],
)
def test_a_deck_scoped_step_without_a_bound_context_is_refused(step):
    payload = _plan(steps=[step], answer_step=step["id"])
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "binds no context" in str(exc.value)


def test_a_deck_scoped_step_is_accepted_once_the_plan_binds_a_context():
    plan = parse_plan(
        _plan(
            steps=[_search(scope="deck")],
            answer_step="hits",
            context_id=CONTEXT_ID,
        )
    )
    assert plan.step("hits").scope == "deck"
    # Scope is a request, not a card list: the ids stay the executor's to inject.
    assert plan.step("hits").query.filters.allowed_oracle_ids == ()


def test_a_deck_profile_without_a_bound_context_is_refused():
    payload = _plan(
        steps=[{"id": "profile", "kind": "deck_profile", "facets": ["card_list"]}],
        answer_step="profile",
    )
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "profiles a deck" in str(exc.value)


def test_a_tag_filter_that_constrains_nothing_is_refused():
    payload = _plan(steps=[_tag_filter(filters={})], answer_step="rocks")
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(payload)
    assert "at least one predicate" in str(exc.value)


def test_a_plan_may_not_carry_a_budget_field():
    with pytest.raises(PlanValidationError) as exc:
        parse_plan(_plan(clarification_required=True, budget_usd=50))
    assert "budget_usd" in str(exc.value)


# -- plan identity ----------------------------------------------------------


def test_the_plan_hash_is_stable_across_a_reparse():
    payload = _plan(steps=[_search()], answer_step="hits")
    plan = parse_plan(payload)
    assert len(plan.sha256()) == 64
    assert plan.sha256() == parse_plan(payload).sha256()
    assert plan.sha256() == parse_plan(plan.model_dump(mode="json")).sha256()


def test_the_plan_hash_changes_when_the_intent_changes():
    payload = _plan(steps=[_search()], answer_step="hits")
    reworded = _plan(
        steps=[_search()],
        answer_step="hits",
        intent="find the cheapest mana rocks, ranked by how fast they convert",
    )
    assert parse_plan(payload).sha256() != parse_plan(reworded).sha256()
