"""Hermetic scorer tests with synthetic evidence, never live model measurements."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sabermetrics.assistant_conversation_eval import (
    Benchmark,
    Review,
    Run,
    answer_digest,
    benchmark_digest,
    capture_turn,
    compare,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/assistant_marchesa_conversation.json"


def evidence():
    benchmark = Benchmark.model_validate(json.loads(FIXTURE.read_text()))
    turns = []
    for i, case in enumerate(benchmark.cases):
        preferences = {"dimensions": ["goal"]}
        if i >= 1:
            preferences.update(
                late_game_draw_density=True,
                multifunctionality=True,
                optionality="preferred",
            )
        if i == 2:
            preferences.update(
                creatures_only=True,
                rejected_cards=["Harvester of Souls"],
                mana_max=None,
            )
        turns.append(
            {
                "case_id": case.id,
                "answer": f"Synthetic response for scorer contract test {i}; not an actual assistant answer.",
                "status": "done",
                "latency_ms": 1000.0,
                "state": {
                    "request_sha256": answer_digest(case.prompt),
                    "task": {
                        "kind": "discover" if i == 0 else "compare",
                        "requested_additions": 0,
                        "requested_cuts": 0,
                    },
                    "deck_mutated": False,
                    "preferences": preferences,
                    "evidence": {
                        "TevalsJudgment": "resolved",
                        "ElegyAcolyte": "resolved",
                    },
                    "deliverables": {
                        "two_draw_lists": True,
                        "named_card_answered": True,
                    },
                },
                "retrievals": [
                    {"canonical_name": "Teval's Judgment", "status": "resolved"},
                    {"canonical_name": "Elegy Acolyte", "status": "resolved"},
                ],
                "answer_card_names": ["Teval's Judgment", "Elegy Acolyte"],
                "unavailable_card_names": [],
            }
        )
    run = Run.model_validate(
        {
            "version": "assistant-conversation-run.v1",
            "benchmark_sha256": benchmark_digest(benchmark),
            "revision": "synthetic-only",
            "evidence_kind": "controlled_test",
            "turns": turns,
        }
    )
    reviews = [
        Review.model_validate(
            {
                "case_id": case.id,
                "answer_sha256": answer_digest(turn.answer),
                "reviewer_id": "synthetic-reviewer",
                "kind": "model_judge",
                "independent": True,
                "correctness": 4,
                "usefulness": 4,
                "criteria": {key: "pass" for key in case.semantic_criteria},
                "rationale": "Synthetic judgment for scorer test only.",
            }
        )
        for case, turn in zip(benchmark.cases, run.turns, strict=True)
    ]
    return benchmark, run, reviews


@pytest.mark.parametrize("digest", [None, answer_digest("A different request")])
def test_missing_or_wrong_request_cannot_pass_frozen_scenario(digest):
    benchmark, run, reviews = evidence()
    run.turns[0].state["request_sha256"] = digest
    report = evaluate(benchmark, run, reviews)
    assert report["quality_gate_pass"] is False
    assert "request_mismatch" in report["cases"][0]["deterministic"]["failures"]


def test_fixture_is_privacy_minimized_three_turn_development_scenario():
    benchmark, _, _ = evidence()
    assert len(benchmark.cases) == 3
    assert benchmark.context["open_slots"] == 7
    assert (
        "one that hasn't been chosen this turn"
        in benchmark.references["Teval's Judgment"]
    )
    assert "combat damage to a player" in benchmark.references["Elegy Acolyte"]
    assert "owner_id" not in FIXTURE.read_text()
    assert "deck_id" not in FIXTURE.read_text()


def test_controlled_success_never_qualifies_live_quality():
    benchmark, run, reviews = evidence()
    result = evaluate(benchmark, run, reviews)
    assert result["status"] == "pass"
    assert result["quality_gate_pass"] is False
    assert result["broad_expertise_qualified"] is False
    assert all(case["semantic"]["correctness"] == 4 for case in result["cases"])


def test_missing_semantic_evidence_remains_unknown():
    benchmark, run, _ = evidence()
    result = evaluate(benchmark, run, [])
    assert result["status"] == "unknown"
    assert all(case["deterministic"]["status"] == "pass" for case in result["cases"])
    assert all(case["semantic"]["correctness"] is None for case in result["cases"])


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "missing",
        "blank",
        "budget",
        "identity",
        "denied",
        "cuts",
        "seven",
        "preference",
        "creatures",
    ],
)
def test_historical_failure_shapes_cannot_pass(change):
    benchmark, run, reviews = evidence()
    data = run.model_dump()
    if change == "empty":
        data["turns"] = []
    elif change == "missing":
        data["turns"].pop()
    elif change == "blank":
        data["turns"][2]["answer"] = "  "
    elif change == "budget":
        data["turns"][2]["status"] = "budget_error"
    elif change == "identity":
        data["turns"][2]["answer_card_names"] = ["Elegy Lookalike"]
    elif change == "denied":
        data["turns"][0]["unavailable_card_names"] = ["Teval's Judgment"]
    elif change == "cuts":
        data["turns"][1]["state"]["task"]["requested_cuts"] = 7
    elif change == "seven":
        data["turns"][0]["state"]["task"]["requested_additions"] = 7
    elif change == "preference":
        del data["turns"][2]["state"]["preferences"]["optionality"]
    else:
        data["turns"][2]["state"]["preferences"]["creatures_only"] = False
    changed = Run.model_validate(data)
    # Dropped cases have no review; changed answers require refreshed assessment.
    keep = {turn.case_id: turn for turn in changed.turns}
    adjusted = [
        review.model_copy(
            update={"answer_sha256": answer_digest(keep[review.case_id].answer)}
        )
        for review in reviews
        if review.case_id in keep
    ]
    result = evaluate(benchmark, changed, adjusted)
    assert result["status"] in {"fail", "incomplete"}
    assert result["quality_gate_pass"] is False


def test_semantic_errors_fail_despite_perfect_structural_metadata():
    benchmark, run, reviews = evidence()
    reviews[1].criteria["voluntary_activation"] = "fail"
    result = evaluate(benchmark, run, reviews)
    assert result["cases"][1]["deterministic"]["status"] == "pass"
    assert result["status"] == "fail"


def test_incomplete_or_nonindependent_ratings_are_unknown():
    benchmark, run, reviews = evidence()
    reviews[0].independent = False
    del reviews[1].criteria["voluntary_activation"]
    assert evaluate(benchmark, run, reviews)["status"] == "unknown"


def test_hash_or_duplicate_review_cannot_be_replayed():
    benchmark, run, reviews = evidence()
    with pytest.raises(ValueError):
        evaluate(benchmark, run, reviews + [reviews[0]])
    reviews[0].answer_sha256 = "0" * 64
    with pytest.raises(ValueError):
        evaluate(benchmark, run, reviews)
    run.benchmark_sha256 = "0" * 64
    with pytest.raises(ValueError):
        evaluate(benchmark, run, [])


def test_invalid_empty_suite_and_duplicate_cases_rejected():
    benchmark, run, _ = evidence()
    data = benchmark.model_dump()
    data["cases"] = []
    with pytest.raises(ValidationError):
        Benchmark.model_validate(data)
    data["cases"] = [benchmark.cases[0].model_dump()] * 2
    with pytest.raises(ValidationError):
        Benchmark.model_validate(data)
    data = run.model_dump()
    data["turns"].append(data["turns"][0])
    with pytest.raises(ValidationError):
        Run.model_validate(data)


def test_time_scored_independently_and_baseline_regressions_explicit():
    benchmark, run, reviews = evidence()
    prior = evaluate(benchmark, run, reviews)
    run.turns[0].latency_ms = 200000.0
    reviews[1].usefulness = 3
    after = evaluate(benchmark, run, reviews)
    assert after["cases"][0]["semantic"]["status"] == "pass"
    assert after["cases"][0]["latency"]["status"] == "fail"
    comparison = compare(benchmark, after, prior)
    assert comparison["status"] == "fail"
    assert {entry["dimension"] for entry in comparison["regressions"]} >= {
        "latency",
        "latency_ratio",
        "usefulness",
    }


def test_baseline_missing_cases_is_incomplete():
    benchmark, run, reviews = evidence()
    prior = evaluate(benchmark, run.model_copy(update={"turns": []}), [])
    assert (
        compare(benchmark, evaluate(benchmark, run, reviews), prior)["status"]
        == "incomplete"
    )


def cli():
    spec = importlib.util.spec_from_file_location(
        "conversation_cli", ROOT / "scripts/evaluate_assistant_conversation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def test_cli_unknown_invalid_and_controlled_exit_codes(tmp_path):
    _, run, reviews = evidence()
    runfile, reviewfile, report = [
        tmp_path / name for name in ("run.json", "reviews.json", "report.json")
    ]
    runfile.write_text(run.model_dump_json())
    reviewfile.write_text(json.dumps([review.model_dump() for review in reviews]))
    args = ["--benchmark", str(FIXTURE), "--run", str(runfile), "--report", str(report)]
    assert cli()(args) == 2
    assert json.loads(report.read_text())["status"] == "unknown"
    assert cli()(args + ["--reviews", str(reviewfile)]) == 1
    assert json.loads(report.read_text())["quality_gate_pass"] is False
    runfile.write_text('{"version": "a", "version": "b"}')
    assert cli()(args) == 1
    assert json.loads(report.read_text())["status"] == "invalid"


def test_boolean_cannot_satisfy_exact_zero_constraint():
    benchmark, run, reviews = evidence()
    run.turns[0].state["task"]["requested_cuts"] = False
    assert evaluate(benchmark, run, reviews)["status"] == "fail"


def test_capture_adapter_uses_receipts_and_does_not_invent_deliverables():
    result = {
        "status": "done",
        "content": "Elegy Acolyte is combat dependent.",
        "research": {
            "task": {
                "kind": "compare",
                "additions": 0,
                "cuts": 0,
                "deliverables_evaluated": False,
            },
            "conversation_preferences": [
                {
                    "dimension": "card_type",
                    "value": "creatures only",
                    "strength": "required",
                    "quote": "private quote",
                    "source_turn": "private-id",
                },
                {
                    "dimension": "draw_control",
                    "value": "optional draw",
                    "strength": "preferred",
                },
            ],
        },
        "actions": [
            {
                "type": "tool_result",
                "name": "card_details",
                "ok": True,
                "data": {
                    "card": {
                        "name": "Elegy Acolyte",
                        "oracle_text": "Actual retrieved text.",
                    }
                },
            }
        ],
        "owner_id": "must-not-survive",
        "deck_id": "must-not-survive",
    }
    turn = capture_turn(
        "elegy-focused", "private request", result, 123.0, deck_unchanged=True
    )
    assert turn.state["evidence"]["ElegyAcolyte"] == "resolved"
    assert turn.answer_card_names == ["Elegy Acolyte"]
    assert turn.state["task"]["requested_additions"] == 0
    assert turn.state["deck_mutated"] is False
    assert "deliverables" not in turn.state
    assert turn.state["deliverables_evaluated"] is False
    text = turn.model_dump_json()
    for private in (
        "must-not-survive",
        "private request",
        "private quote",
        "private-id",
    ):
        assert private not in text
    assert (
        capture_turn("elegy-focused", "request", result, 123.0).state["deck_mutated"]
        is None
    )


def test_capture_reuses_prior_successful_receipts_not_failed_lookup():
    _, run, _ = evidence()
    result = {
        "status": "done",
        "content": "Teval's Judgment remains relevant.",
        "actions": [
            {
                "type": "tool_result",
                "name": "card_details",
                "ok": False,
                "data": {"card": {"name": "Wrong card", "oracle_text": "Bad"}},
            }
        ],
    }
    turn = capture_turn(
        "draw-preferences", "request", result, 100.0, prior_turns=[run.turns[0]]
    )
    assert "Teval's Judgment" in turn.answer_card_names
    assert all(row.canonical_name != "Wrong card" for row in turn.retrievals)


def test_turn_order_is_required():
    benchmark, run, _ = evidence()
    run.turns.reverse()
    with pytest.raises(ValueError, match="out of order"):
        evaluate(benchmark, run, [])
