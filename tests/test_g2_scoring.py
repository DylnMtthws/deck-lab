"""Gate G2 counts fully answered questions, and refuses an unearned claim.

Everything here runs on synthetic questions and synthetic runs. The point is
the arithmetic and the refusals, not the corpus: a scorer that inflates a
number is a worse failure than a substrate that misses a card, because the
number is what decides whether R4 gets built.
"""

from __future__ import annotations

from datetime import date

import pytest

from sabermetrics.assistant.envelope import (
    CardRow,
    CorpusProvenance,
    Coverage,
    NoFieldEvidence,
    PlanRun,
    StatedAbsenceRecord,
    StepNotRun,
    StepResult,
)
from sabermetrics.assistant.eval.g2 import (
    G2_SCORECARD_SCHEMA,
    G2InputError,
    G2Run,
    authoritative_g2,
    g2_scorecard,
)
from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    GoldenQuestion,
    GoldenQuestionSet,
)
from sabermetrics.assistant.eval.plans import HandWrittenPlan, HandWrittenPlanSet
from sabermetrics.assistant.eval.runner import identity_id_map
from sabermetrics.assistant.ir import ResearchPlan
from sabermetrics.substrate.models import RetrievalAvailability
from sabermetrics.substrate.settings import load_research_settings

HEX = "a" * 64
PROVENANCE = CorpusProvenance(
    bundle_id=HEX,
    corpus_source_view="mtg_v1.card_any_medium",
    corpus_row_count=34_551,
    corpus_sha256=HEX,
    document_version="card-document.v2",
    retrieval_config_sha256=HEX,
    tag_library_sha256=HEX,
    tag_content_sha256=HEX,
    tag_row_count=1234,
)


def _question(question_id, **overrides):
    payload = {
        "id": question_id,
        "ask": "a question long enough to validate",
        "clarified_ask": "a clarified question long enough to validate",
        "required_oracle_ids": [],
        "forbidden_oracle_ids": [],
        "expected_absences": [],
        "category": "mechanic_search",
        "difficulty": "basic",
        "labeller": "test",
        "labelled_on": date(2026, 9, 9),
        "contested": False,
    }
    payload.update(overrides)
    return GoldenQuestion(**payload)


def _card(oracle_id, rank):
    return CardRow(
        oracle_id=oracle_id,
        name=f"Card {oracle_id}",
        type_line="Artifact",
        mana_cost="{1}",
        mana_value=1.0,
        color_identity=(),
        types=("Artifact",),
        tags=(),
        rank=rank,
    )


def _result(step_id, oracle_ids, *, ordering="ranked", eligible=None):
    cards = tuple(_card(value, index) for index, value in enumerate(oracle_ids, 1))
    total = len(cards) if eligible is None else eligible
    return StepResult(
        step_id=step_id,
        kind="card_search",
        tier="fact",
        ordering=ordering,
        provenance=PROVENANCE,
        coverage=Coverage(
            eligible=total,
            eligible_is_exact=True,
            examined=total,
            returned=len(cards),
            dropped=total - len(cards),
            truncated=total > len(cards),
            set_input_incomplete=False,
        ),
        field=NoFieldEvidence(reason="not_a_field_query"),
        availability=RetrievalAvailability(lexical=True, dense=True, reranked=True),
        cards=cards,
        elapsed_ms=1.0,
    )


def _plan(intent="a synthetic plan intent"):
    return ResearchPlan(
        intent=intent,
        clarification_required=True,
    )


def _run(question_id, outcome=None, *, absences=(), clarification=False):
    steps = () if outcome is None else (outcome,)
    return PlanRun(
        plan_sha256=HEX,
        question_id=question_id,
        intent="a synthetic plan intent",
        steps=steps,
        answer_step=None if outcome is None else outcome.step_id,
        clarification_required=clarification,
        stated_absences=tuple(absences),
        elapsed_ms=1.0,
    )


def _observation(question_id, returned=(), **overrides):
    payload = {
        "question_id": question_id,
        "returned_oracle_ids": list(returned),
        "result_set_oracle_ids": list(returned),
    }
    payload.update(overrides)
    return CorrectnessObservation(**payload)


def _hand(question_id):
    return HandWrittenPlan(
        question_id=question_id,
        plan=_plan(),
        rationale="a synthetic rationale",
        author="test",
        authored_on=date(2026, 9, 9),
        review_status="owner_verified",
    )


def _plan_set(question_ids):
    return HandWrittenPlanSet(
        schema_version="research-r3-plans.v1",
        plans=tuple(_hand(value) for value in question_ids),
    )


def _g2run(**overrides):
    settings = load_research_settings()
    payload = {
        "bundle_id": HEX,
        "corpus_source_view": "mtg_v1.card_any_medium",
        "corpus_row_count": 34_551,
        "corpus_sha256": HEX,
        "embedding_model_id": settings.embedding.model_id,
        "embedding_revision": settings.embedding.revision,
        "reranker_model_id": settings.reranker.model_id,
        "reranker_revision": settings.reranker.revision,
    }
    payload.update(overrides)
    return G2Run(**payload)


def _score(questions, observations, runs, **overrides):
    ids = [question.id for question in questions]
    payload = {
        "plans": _plan_set(ids),
        "id_map": identity_id_map(),
        "run": _g2run(),
    }
    payload.update(overrides)
    return g2_scorecard(
        GoldenQuestionSet(questions=questions), observations, runs, **payload
    )


def test_a_question_passes_only_with_full_required_recall():
    question = _question("q-full", required_oracle_ids=["a", "b"])
    partial = _score(
        [question],
        [_observation("q-full", ["a"])],
        [_run("q-full", _result("s", ["a"]))],
    )
    assert partial["gates"]["retrieval"]["passed"] == 0
    complete = _score(
        [question],
        [_observation("q-full", ["a", "b"])],
        [_run("q-full", _result("s", ["a", "b"]))],
    )
    assert complete["gates"]["retrieval"]["passed"] == 1


def test_mean_recall_and_the_g2_count_are_different_numbers():
    """Three of four required cards is 0.75 mean recall and zero answered."""
    question = _question("q-mean", required_oracle_ids=["a", "b", "c", "d"])
    card = _score(
        [question],
        [_observation("q-mean", ["a", "b", "c"])],
        [_run("q-mean", _result("s", ["a", "b", "c"]))],
    )
    assert card["correctness"]["quality"]["required_recall_at_50"] == pytest.approx(
        0.75
    )
    assert card["gates"]["retrieval"]["pass_rate"] == 0.0


def test_a_forbidden_card_fails_the_question():
    question = _question(
        "q-forbidden", required_oracle_ids=["a"], forbidden_oracle_ids=["x"]
    )
    card = _score(
        [question],
        [_observation("q-forbidden", ["a", "x"])],
        [_run("q-forbidden", _result("s", ["a", "x"]))],
    )
    assert card["gates"]["retrieval"]["passed"] == 0
    assert card["gates"]["retrieval"]["forbidden_hit_anywhere"] == ["q-forbidden"]


def test_required_and_forbidden_use_the_same_window():
    """A forbidden card outside the window must not fail a passing question."""
    question = _question(
        "q-window", required_oracle_ids=["a"], forbidden_oracle_ids=["x"]
    )
    returned = ["a", *(f"f{index}" for index in range(60)), "x"]
    card = _score(
        [question],
        [_observation("q-window", returned)],
        [_run("q-window", _result("s", returned))],
    )
    gate = card["gates"]["retrieval"]
    assert gate["passed"] == 1, "the forbidden card is beyond rank 50"
    assert gate["forbidden_hit_anywhere"] == ["q-window"], (
        "and the untruncated hit is still reported, so the window is not a "
        "quiet loosening"
    )


def test_a_small_unranked_answer_is_scored_over_its_whole_returned_set():
    """Slicing an Oracle-id ordering at k would measure the alphabet."""
    question = _question("q-unranked", required_oracle_ids=["z"])
    returned = [*(f"f{index}" for index in range(8)), "z"]
    outcome = _result("s", returned, ordering="oracle_id")
    card = _score(
        [question],
        [_observation("q-unranked", returned)],
        [_run("q-unranked", outcome)],
    )
    gate = card["gates"]["retrieval"]
    assert gate["windows"]["q-unranked"] == "full_returned_set"
    assert gate["passed"] == 1


def test_an_unranked_answer_larger_than_the_window_cannot_pass():
    """The deck-dump loophole: returning the bound deck is not retrieving.

    An unranked result has no best-first order, so there is no principled top
    k to take. Scoring it whole would let a plan pass every deck-bound
    question by returning all 100 cards and never searching for anything.
    """
    question = _question("q-dump", required_oracle_ids=["z"])
    returned = [*(f"f{index}" for index in range(60)), "z"]
    outcome = _result("s", returned, ordering="declaration")
    card = _score(
        [question],
        [_observation("q-dump", returned)],
        [_run("q-dump", outcome)],
    )
    gate = card["gates"]["retrieval"]
    assert gate["passed"] == 0
    assert gate["failed"] == ["q-dump"]
    assert gate["windows"]["q-dump"] == "unranked_over_window"
    assert gate["unranked_over_window"] == ["q-dump"]
    assert gate["answer_returned"]["q-dump"] == 61


def test_questions_without_required_labels_are_named_not_counted():
    scored = _question("q-scored", required_oracle_ids=["a"])
    unscored = _question("q-unscored")
    card = _score(
        [scored, unscored],
        [_observation("q-scored", ["a"]), _observation("q-unscored")],
        [_run("q-scored", _result("s", ["a"])), _run("q-unscored")],
    )
    gate = card["gates"]["retrieval"]
    assert gate["applicable"] == 1
    assert gate["pass_rate"] == 1.0
    assert gate["not_applicable_ids"] == ["q-unscored"]
    assert gate["denominator_name"] == "questions_with_required_labels"


def test_an_empty_required_set_cannot_buy_a_free_pass():
    """`set() <= anything` would hand every unlabelled question a pass."""
    card = _score(
        [_question(f"q-{index}") for index in range(5)],
        [_observation(f"q-{index}") for index in range(5)],
        [_run(f"q-{index}") for index in range(5)],
    )
    assert card["gates"]["retrieval"]["applicable"] == 0
    assert card["status"] == "not_authoritative"


def test_the_population_each_answer_was_drawn_from_is_reported():
    question = _question("q-pop", required_oracle_ids=["a"])
    card = _score(
        [question],
        [_observation("q-pop", ["a"])],
        [_run("q-pop", _result("s", ["a"], eligible=34_551))],
    )
    assert card["gates"]["retrieval"]["eligible_population"]["q-pop"] == 34_551


def test_the_no_finding_gate_is_not_measured_without_a_narrator():
    question = _question(
        "q-healthy",
        category="healthy_deck",
        no_finding_expected=True,
        context_id="cedh:kinnan-healthy-baseline",
    )
    card = _score([question], [_observation("q-healthy")], [_run("q-healthy")])
    gate = card["gates"]["no_finding"]
    assert gate["status"] == "not_measured"
    assert gate["applicable_ids"] == ["q-healthy"]
    assert gate["pass_rate"] == 0.0


def test_every_embedded_quality_number_carries_a_measurement_status():
    """One document must not report a gate as passing and not measured at once."""
    card = _score([_question("q-any")], [_observation("q-any")], [_run("q-any")])
    assert set(card["correctness_quality_status"]) == set(
        card["correctness"]["quality"]
    )
    assert card["correctness_quality_status"]["manufactured_finding_rate"] == (
        "not_measured"
    )
    assert card["correctness_quality_status"]["required_recall_at_50"] == "measured"


def test_an_absence_must_answer_the_expectation_it_claims_to():
    question = _question(
        "q-absence",
        category="out_of_scope",
        expected_absences=["price is absent", "collection is absent"],
    )
    half = _run(
        "q-absence",
        absences=[
            StatedAbsenceRecord(
                reason="price_is_not_in_the_engine",
                detail="no price field exists",
                answers_expectation=0,
            )
        ],
    )
    card = _score([question], [_observation("q-absence")], [half])
    assert card["gates"]["absence"]["failed"] == ["q-absence"]
    both = _run(
        "q-absence",
        absences=[
            StatedAbsenceRecord(
                reason="price_is_not_in_the_engine",
                detail="no price field exists",
                answers_expectation=0,
            ),
            StatedAbsenceRecord(
                reason="collection_is_not_in_the_engine",
                detail="no collection input exists",
                answers_expectation=1,
            ),
        ],
    )
    card = _score([question], [_observation("q-absence")], [both])
    assert card["gates"]["absence"]["passed"] == 1
    assert card["gates"]["absence"]["status"] == "declared_not_measured"


def test_a_deferred_capability_is_named_with_the_phase_that_owns_it():
    question = _question("q-deferred", category="metagame")
    run = _run(
        "q-deferred",
        absences=[
            StatedAbsenceRecord(
                reason="field_statistics_deferred_to_r5",
                detail="field statistics arrive with R5",
            )
        ],
    )
    card = _score([question], [_observation("q-deferred")], [run])
    assert "q-deferred" in card["deferred"]["by_question"]
    assert "R5" in card["deferred"]["by_question"]["q-deferred"][0]


def test_status_thresholds_follow_the_spec():
    def rate(passed, total):
        questions = [
            _question(f"q-{index}", required_oracle_ids=["a"]) for index in range(total)
        ]
        observations = []
        runs = []
        for index in range(total):
            found = ["a"] if index < passed else ["b"]
            observations.append(_observation(f"q-{index}", found))
            runs.append(_run(f"q-{index}", _result("s", found)))
        return _authoritative(questions, observations, runs)["status"]

    assert rate(10, 10) == "pass"
    assert rate(8, 10) == "pass"
    assert rate(7, 10) == "fail_target"
    assert rate(6, 10) == "stop_and_fix"


def test_the_scorecard_pins_its_schema_and_flags_non_authoritative_runs():
    card = _score([_question("q-any")], [_observation("q-any")], [_run("q-any")])
    assert card["schema_version"] == G2_SCORECARD_SCHEMA
    assert card["authoritative"] is False
    assert card["status"] == "not_authoritative", (
        "a measured run must not stamp a gate status; `status` is what a gate "
        "reads, and this run is explicitly not the gate"
    )
    assert "measured_pass_rate" in card


def _named_id_map(questions):
    """An identity map that can also name every labelled card.

    ``authoritative_g2`` refuses a map that cannot, because the
    plan-names-its-answer check would otherwise report a clean zero over a
    check that never ran.
    """
    labelled = {
        oracle_id
        for question in questions
        for oracle_id in (
            *question.required_oracle_ids,
            *question.forbidden_oracle_ids,
        )
    }
    return identity_id_map().model_copy(
        update={"names_by_label_id": {value: f"Card {value}" for value in labelled}}
    )


def _authoritative(questions, observations, runs, **overrides):
    ids = [question.id for question in questions]
    payload = {
        "plans": _plan_set(ids),
        "id_map": _named_id_map(questions),
        "run": _g2run(),
        "settings": load_research_settings(),
        "corpus_oracle_ids": {"a", "b", "x"},
    }
    payload.update(overrides)
    return authoritative_g2(
        GoldenQuestionSet(questions=questions), observations, runs, **payload
    )


def test_authoritative_g2_passes_on_a_fully_green_input():
    """The non-raising path is reachable, so the refusals are real branches."""
    question = _question("q-green", required_oracle_ids=["a"])
    card = _authoritative(
        [question],
        [_observation("q-green", ["a"])],
        [_run("q-green", _result("s", ["a"]))],
    )
    assert card["authoritative"] is True
    assert card["status"] == "pass"


def test_authoritative_g2_refuses_a_contested_golden_set():
    question = _question("q-contested", required_oracle_ids=["a"], contested=True)
    with pytest.raises(G2InputError, match="owner-reviewed golden set"):
        _authoritative(
            [question],
            [_observation("q-contested", ["a"])],
            [_run("q-contested", _result("s", ["a"]))],
        )


def test_authoritative_g2_refuses_a_draft_plan():
    question = _question("q-draft", required_oracle_ids=["a"])
    drafts = HandWrittenPlanSet(
        schema_version="research-r3-plans.v1",
        plans=(
            _hand("q-draft").model_copy(
                update={"review_status": "draft", "review_note": "check this"}
            ),
        ),
    )
    with pytest.raises(G2InputError, match="owner-verified plans"):
        _authoritative(
            [question],
            [_observation("q-draft", ["a"])],
            [_run("q-draft", _result("s", ["a"]))],
            plans=drafts,
        )


def test_authoritative_g2_refuses_a_partial_run():
    """Running only the easy questions must not report a perfect score."""
    questions = [
        _question("q-easy", required_oracle_ids=["a"]),
        _question("q-hard", required_oracle_ids=["b"]),
    ]
    with pytest.raises(G2InputError, match="one observation per golden question"):
        _authoritative(
            questions,
            [_observation("q-easy", ["a"])],
            [_run("q-easy", _result("s", ["a"]))],
        )


def test_authoritative_g2_refuses_a_development_bundle():
    question = _question("q-dev", required_oracle_ids=["a"])
    with pytest.raises(G2InputError, match="mtg_v1.card_any_medium"):
        _authoritative(
            [question],
            [_observation("q-dev", ["a"])],
            [_run("q-dev", _result("s", ["a"]))],
            run=_g2run(corpus_source_view="scryfall:oracle_cards"),
        )


def test_authoritative_g2_refuses_a_corpus_below_the_full_card_floor():
    question = _question("q-small", required_oracle_ids=["a"])
    with pytest.raises(G2InputError, match="full corpus"):
        _authoritative(
            [question],
            [_observation("q-small", ["a"])],
            [_run("q-small", _result("s", ["a"]))],
            run=_g2run(corpus_row_count=100),
        )


def test_authoritative_g2_refuses_an_unpinned_model():
    question = _question("q-model", required_oracle_ids=["a"])
    with pytest.raises(G2InputError, match="pinned local models"):
        _authoritative(
            [question],
            [_observation("q-model", ["a"])],
            [_run("q-model", _result("s", ["a"]))],
            run=_g2run(embedding_model_id="some/other-model"),
        )


def test_authoritative_g2_refuses_a_label_that_does_not_resolve():
    question = _question("q-unresolved", required_oracle_ids=["missing"])
    with pytest.raises(G2InputError, match="do not resolve in the corpus"):
        _authoritative(
            [question],
            [_observation("q-unresolved", ["missing"])],
            [_run("q-unresolved", _result("s", ["missing"]))],
        )


def test_authoritative_g2_refuses_a_rules_lookup_with_no_reference_index():
    question = _question("q-rules", required_oracle_ids=["a"], category="rules")
    absent = StepNotRun(
        step_id="cr",
        kind="rules_lookup",
        reason="reference_index_absent",
        detail="no active reference generation",
        provenance=PROVENANCE,
    )
    run = PlanRun(
        plan_sha256=HEX,
        question_id="q-rules",
        intent="a synthetic plan intent",
        steps=(_result("s", ["a"]), absent),
        answer_step="s",
        elapsed_ms=1.0,
    )
    with pytest.raises(G2InputError, match="no reference index"):
        _authoritative([question], [_observation("q-rules", ["a"])], [run])


def test_authoritative_g2_refuses_an_unreviewed_id_map():
    from sabermetrics.assistant.eval.runner import LabelIdMap

    question = _question("q-map", required_oracle_ids=["a"])
    unreviewed = LabelIdMap(
        schema_version="research-deck-context-id-map.v1",
        status="awaiting_owner_review",
        label_to_canonical={"a": "a"},
        canonical_to_label={"a": "a"},
    )
    with pytest.raises(G2InputError, match="reviewed id map"):
        _authoritative(
            [question],
            [_observation("q-map", ["a"])],
            [_run("q-map", _result("s", ["a"]))],
            id_map=unreviewed,
        )
