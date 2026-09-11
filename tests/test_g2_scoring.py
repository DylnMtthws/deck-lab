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


def _named_map_for(questions):
    return _named_id_map(questions)


def test_discovery_excludes_questions_whose_ask_names_its_own_answer():
    """A name lookup is a pass, and it is not discovery evidence."""
    lookup = _question(
        "q-lookup",
        ask="does Card a still counter a spell?",
        clarified_ask="explain what Card a does",
        required_oracle_ids=["a"],
    )
    discovery = _question(
        "q-discovery",
        ask="find artifacts that tap for two colourless",
        clarified_ask="artifacts with a printed tap-for-mana ability",
        required_oracle_ids=["b"],
    )
    questions = [lookup, discovery]
    card = _score(
        questions,
        [_observation("q-lookup", ["a"]), _observation("q-discovery", ["b"])],
        [
            _run("q-lookup", _result("s", ["a"])),
            _run("q-discovery", _result("s", ["b"])),
        ],
        id_map=_named_map_for(questions),
    )
    retrieval, disc = card["gates"]["retrieval"], card["gates"]["discovery"]
    assert retrieval["applicable"] == 2, "the headline gate counts both"
    assert disc["applicable"] == 1, "discovery counts only the unnamed one"
    assert disc["excluded_name_lookups"] == ["q-lookup"]


def test_discovery_and_retrieval_can_disagree():
    """The split is worth having only if the two rates can differ."""
    lookup = _question(
        "q-lookup",
        ask="what does Card a do?",
        clarified_ask="explain Card a",
        required_oracle_ids=["a"],
    )
    discovery = _question(
        "q-discovery",
        ask="find a card that does the thing",
        clarified_ask="a mechanic query naming nothing",
        required_oracle_ids=["b"],
    )
    questions = [lookup, discovery]
    card = _score(
        questions,
        [_observation("q-lookup", ["a"]), _observation("q-discovery", ["zzz"])],
        [
            _run("q-lookup", _result("s", ["a"])),
            _run("q-discovery", _result("s", ["zzz"])),
        ],
        id_map=_named_map_for(questions),
    )
    assert card["gates"]["retrieval"]["pass_rate"] == 0.5
    assert card["gates"]["discovery"]["pass_rate"] == 0.0, (
        "the only discovery question failed, so discovery is 0 while the "
        "headline is 0.5 — that gap is the reason the split exists"
    )


def test_breadth_reports_how_wide_each_answer_was():
    """A pass over fifty rows and a pass over one score identically."""
    question = _question("q-wide", required_oracle_ids=["a"])
    returned = ["a", *(f"f{index}" for index in range(49))]
    card = _score(
        [question],
        [_observation("q-wide", returned)],
        [_run("q-wide", _result("s", returned))],
    )
    gate = card["gates"]["retrieval"]
    assert gate["passed"] == 1
    assert gate["breadth"]["returned_per_required"]["q-wide"] == 50.0
    assert gate["breadth"]["widest"][0]["question_id"] == "q-wide"
    assert gate["breadth"]["widest"][0]["returned"] == 50


def _load_run_g2():
    """Import ``scripts/run_g2.py`` as a module, the way test_automation does."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "run_g2.py"
    spec = importlib.util.spec_from_file_location("run_g2_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chunk_payload(bundle, corpus, question_ids):
    return {
        "bundle_id": bundle,
        "corpus_sha256": corpus,
        "rows": [
            {
                "observation": _observation(qid, ["a"]).model_dump(mode="json"),
                "run": _run(qid, _result("s", ["a"])).model_dump(mode="json"),
            }
            for qid in question_ids
        ],
    }


def test_chunks_reading_different_bundles_cannot_be_merged(monkeypatch):
    """Two corpora produce two different results; merging them invents a third."""
    module = _load_run_g2()
    questions = GoldenQuestionSet(questions=[_question("q-one"), _question("q-two")])
    payloads = [
        _chunk_payload("bundle-a", "corpus-a", ["q-one"]),
        _chunk_payload("bundle-b", "corpus-b", ["q-two"]),
    ]
    monkeypatch.setattr(module, "_spawn_chunk", lambda *a, **k: payloads.pop(0))
    with pytest.raises(module.ChunkMismatchError, match="different bundles"):
        module._run_chunks(
            questions, _plan_set(["q-one", "q-two"]), identity_id_map(), 1, None
        )


def test_a_question_executed_by_two_chunks_is_refused(monkeypatch):
    """A duplicate would be scored twice and quietly change the denominator."""
    module = _load_run_g2()
    questions = GoldenQuestionSet(questions=[_question("q-one"), _question("q-two")])
    payloads = [
        _chunk_payload("b", "c", ["q-one"]),
        _chunk_payload("b", "c", ["q-one"]),
    ]
    monkeypatch.setattr(module, "_spawn_chunk", lambda *a, **k: payloads.pop(0))
    with pytest.raises(module.ChunkMismatchError, match="two chunks"):
        module._run_chunks(
            questions, _plan_set(["q-one", "q-two"]), identity_id_map(), 1, None
        )


def test_an_incomplete_chunked_run_is_refused(monkeypatch):
    """Scoring a partial run would report a rate over a silent subset."""
    module = _load_run_g2()
    questions = GoldenQuestionSet(questions=[_question("q-one"), _question("q-two")])
    payloads = [
        _chunk_payload("b", "c", ["q-one"]),
        _chunk_payload("b", "c", []),
    ]
    monkeypatch.setattr(module, "_spawn_chunk", lambda *a, **k: payloads.pop(0))
    with pytest.raises(module.ChunkMismatchError, match="does not cover"):
        module._run_chunks(
            questions, _plan_set(["q-one", "q-two"]), identity_id_map(), 1, None
        )


def test_a_complete_chunked_run_merges_in_golden_question_order(monkeypatch):
    """The merge restores question order regardless of how it was chunked."""
    module = _load_run_g2()
    questions = GoldenQuestionSet(
        questions=[_question("q-one"), _question("q-two"), _question("q-three")]
    )
    payloads = [
        _chunk_payload("b", "c", ["q-two"]),
        _chunk_payload("b", "c", ["q-three", "q-one"]),
    ]
    monkeypatch.setattr(module, "_spawn_chunk", lambda *a, **k: payloads.pop(0))
    observations, runs = module._run_chunks(
        questions, _plan_set(["q-one", "q-two", "q-three"]), identity_id_map(), 2, None
    )
    assert [row.question_id for row in observations] == ["q-one", "q-two", "q-three"]
    assert [row.question_id for row in runs] == ["q-one", "q-two", "q-three"]


def test_the_retrieval_denominator_partitions_every_question_exactly_once():
    """Applicable + pending + nothing-to-score must be the whole set.

    The earlier partition read only ``required_oracle_ids``, which put a
    question scored through its alternatives in both lists and left an
    unscored-pending question in neither. The two counts still summed to the
    total, so the arithmetic looked right while describing the wrong sets.
    """
    questions = [
        _question("q-required", required_oracle_ids=["a"]),
        _question("q-alternatives", satisfied_by_any_of=["a", "b"]),
        _question(
            "q-pending", required_oracle_ids=["a"], unscored_pending="R5 (no cohort)"
        ),
        _question("q-nothing"),
    ]
    card = _score(
        questions,
        [_observation(question.id, ["a"]) for question in questions],
        [_run(question.id, _result("s", ["a"])) for question in questions],
    )
    gate = card["gates"]["retrieval"]
    assert gate["partition"]["sums"] is True
    assert gate["partition"]["total"] == 4
    assert gate["applicable"] == 2
    assert gate["not_applicable_ids"] == ["q-nothing"]
    assert list(gate["unscored_pending"]) == ["q-pending"]


def test_an_alternatives_only_question_is_scored_rather_than_dropped():
    """A singular request still has to return one of its qualifying cards."""
    question = _question("q-any", satisfied_by_any_of=["a", "b"])
    missed = _score(
        [question],
        [_observation("q-any", ["z"])],
        [_run("q-any", _result("s", ["z"]))],
    )
    assert missed["gates"]["retrieval"]["failed"] == ["q-any"]
    hit = _score(
        [question],
        [_observation("q-any", ["b"])],
        [_run("q-any", _result("s", ["b"]))],
    )
    assert hit["gates"]["retrieval"]["passed"] == 1
    assert hit["gates"]["retrieval"]["alternative_coverage"]["q-any"] == {
        "returned": 1,
        "qualifying": 2,
    }


def test_a_pending_question_is_not_a_composite_pass():
    """An unscoreable criterion must not raise the composite rate."""
    question = _question(
        "q-pending", required_oracle_ids=["a"], unscored_pending="R5 (no cohort)"
    )
    card = _score(
        [question],
        [_observation("q-pending", ["a"])],
        [_run("q-pending", _result("s", ["a"]))],
    )
    composite = card["gates"]["composite"]
    assert composite["unscored_pending"] == ["q-pending"]
    assert composite["applicable"] == 0
    assert "q-pending" not in composite["failed"]


def test_retrieving_a_counterexample_is_not_a_failure():
    """A plan may return a trap; only an answer that offers one is wrong."""
    question = _question(
        "q-trap", required_oracle_ids=["a"], counterexample_oracle_ids=["trap"]
    )
    card = _score(
        [question],
        [_observation("q-trap", ["a", "trap"], recommended_oracle_ids=["a"])],
        [_run("q-trap", _result("s", ["a", "trap"]))],
    )
    gate = card["gates"]["counterexample"]
    assert gate["status"] == "measured"
    assert gate["failed"] == []
    assert gate["retrieved_not_a_failure"]["q-trap"] == 1
    assert card["gates"]["retrieval"]["passed"] == 1


def test_recommending_a_counterexample_fails_the_question():
    """Full required recall plus a presented trap is still a wrong answer."""
    question = _question(
        "q-trap", required_oracle_ids=["a", "b"], counterexample_oracle_ids=["trap"]
    )
    card = _score(
        [question],
        [
            _observation(
                "q-trap", ["a", "b", "trap"], recommended_oracle_ids=["a", "b", "trap"]
            )
        ],
        [_run("q-trap", _result("s", ["a", "b", "trap"]))],
    )
    assert card["gates"]["retrieval"]["passed"] == 1, "retrieval is not the criterion"
    gate = card["gates"]["counterexample"]
    assert gate["failed"] == ["q-trap"]
    assert gate["presented"] == {"q-trap": ["trap"]}
    assert "q-trap" in card["gates"]["composite"]["failed"]


def test_a_counterexample_with_no_narrator_is_unmeasured_not_clean():
    """R3 populates no recommendation, so zero presented traps proves nothing."""
    question = _question(
        "q-trap", required_oracle_ids=["a"], counterexample_oracle_ids=["trap"]
    )
    card = _score(
        [question],
        [_observation("q-trap", ["a", "trap"])],
        [_run("q-trap", _result("s", ["a", "trap"]))],
    )
    gate = card["gates"]["counterexample"]
    assert gate["status"] == "not_measured"
    assert gate["unmeasured"] == ["q-trap"]
    assert gate["applicable"] == 0
    assert gate["applicable_ids"] == ["q-trap"]


def test_naming_a_counterexample_as_an_exclusion_is_not_presenting_it():
    """An explanation may say "not Ikoria, because ..." and still be correct.

    This is the case the gate exists to get right. A narrator that names a trap
    in order to rule it out is doing the restriction check, which is the
    behaviour we want; scoring it as a failure would punish the correct answer
    and reward one that silently omitted the card. So the gate reads only what
    an answer PUT FORWARD, and a card that appears in the explanation without
    being recommended is not a presentation.
    """
    question = _question(
        "q-trap", required_oracle_ids=["a"], counterexample_oracle_ids=["trap"]
    )
    card = _score(
        [question],
        [
            _observation(
                "q-trap",
                ["a", "trap"],
                named_oracle_ids=["a", "trap"],
                recommended_oracle_ids=["a"],
            )
        ],
        [_run("q-trap", _result("s", ["a", "trap"]))],
    )
    gate = card["gates"]["counterexample"]
    assert gate["failed"] == []
    assert gate["passed"] == 1
    assert gate["presented"] == {}


def test_preflight_names_every_artefact_refusal_before_anything_runs():
    """The refusals knowable from the checked-in files, without executing."""
    from sabermetrics.assistant.eval.g2 import preflight_refusals

    questions = GoldenQuestionSet(
        questions=[_question("q-one", contested=True), _question("q-two")]
    )
    drafts = HandWrittenPlanSet(
        schema_version="research-r3-plans.v1",
        plans=(
            _hand("q-one").model_copy(
                update={
                    "review_status": "draft",
                    "review_note": "a synthetic note naming what to check",
                }
            ),
            _hand("q-two"),
        ),
    )
    refusals = preflight_refusals(questions, plans=drafts, id_map=identity_id_map())
    assert any("contested" in refusal for refusal in refusals)
    assert any("owner-verified" in refusal for refusal in refusals)
    assert len(refusals) == 2, refusals


def test_preflight_is_silent_when_nothing_blocks():
    """It must not invent a reason; a clean set has to produce an empty list."""
    from sabermetrics.assistant.eval.g2 import preflight_refusals

    questions = GoldenQuestionSet(questions=[_question("q-one")])
    assert (
        preflight_refusals(
            questions, plans=_plan_set(["q-one"]), id_map=identity_id_map()
        )
        == []
    )
