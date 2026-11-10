"""Request-coverage policy regressions, without model calls or prose grading."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sabermetrics.assistant_obligations import (
    AnswerObligations,
    assess_fulfillment,
    build_obligations,
)
from sabermetrics.assistant_quality_evaluation import diagnose_retrieval_stages


def plan(request, **extra):
    return {"kind": "discover", "current_request_quote": request, **extra}


def test_count_prerequisites_and_interaction_preserve_interpretation_provenance():
    request = "Find three additional repeatable draw creatures for Marchesa."
    task = plan(
        request,
        named_cards=["Marchesa", "Invented Card"],
        discovery_lists=[
            {
                "label": "draw creatures",
                "query": "repeatable draw creatures",
                "request_quote": request,
                "requested_count": 3,
            }
        ],
        deliverables=["Three candidates"],
    )
    result = build_obligations(
        request, 7, task=task, constraints={"protected_cards": ["card-1"]}
    )
    assert result.named_subjects == ["Marchesa"]
    assert result.deck_revision == 7
    row = next(row for row in result.items if row.kind == "candidate_list")
    assert row.requested_count == 3
    assert row.origin == "validated_plan"
    assert {row.kind for row in result.items} >= {
        "costs_and_prerequisites",
        "deck_interaction",
        "saved_constraint",
    }
    assert (
        next(
            row for row in result.items if row.kind == "costs_and_prerequisites"
        ).origin
        == "inferred"
    )
    assert AnswerObligations.model_validate(result.model_dump()) == result


def test_historical_or_tiny_planner_quotes_do_not_become_new_obligations():
    for quote in ("old request", "a"):
        result = build_obligations(
            "Compare those two cards",
            0,
            task={
                "kind": "discover",
                "current_request_quote": quote,
                "discovery_lists": [
                    {"query": "draw", "request_quote": quote, "requested_count": 3}
                ],
            },
        )
        assert result.discovery_queries == []
        assert [row.kind for row in result.items] == ["answer"]
        assert result.limitations


def test_followup_does_not_inherit_discovery_count_or_slots():
    request = "Compare those two without finding additional cards."
    result = build_obligations(
        request, 2, task=plan(request, kind="compare", available_slots=30)
    )
    assert all(row.requested_count is None for row in result.items)
    assert "comparison" in {row.kind for row in result.items}


def test_fulfillment_requires_receipts_not_legacy_complete_or_word_presence():
    result = build_obligations("Explain this card", 0)
    assert (
        assess_fulfillment(result, legacy_fulfillment="complete").fulfillment
        == "unknown"
    )
    complete = assess_fulfillment(
        result,
        legacy_fulfillment="complete",
        completed_obligation_ids=[row.id for row in result.items],
    )
    assert complete.fulfillment == "complete"
    assert complete.reason.endswith("factual accuracy is separate.")
    absent = assess_fulfillment(result, legacy_fulfillment="nonanswer")
    assert absent.fulfillment == "partial"
    assert absent.missing_obligations == ["answer"]
    assert (
        assess_fulfillment(result, legacy_fulfillment="pending").fulfillment
        == "unknown"
    )


def test_contradictory_or_foreign_coverage_is_rejected():
    result = build_obligations("Explain this card", 0)
    with pytest.raises(ValueError, match="unknown obligation"):
        assess_fulfillment(
            result,
            legacy_fulfillment="complete",
            completed_obligation_ids=["other-run"],
        )
    with pytest.raises(ValueError, match="both"):
        assess_fulfillment(
            result,
            legacy_fulfillment="complete",
            completed_obligation_ids=[result.items[0].id],
            missing_obligation_ids=[result.items[0].id],
        )
    with pytest.raises(ValidationError):
        build_obligations("", 0)


def test_source_constraints_are_not_mislabeled_as_current_user_quotes():
    result = build_obligations(
        "Explain my choices",
        0,
        constraints={
            "protected_cards": [str(i) for i in range(100)],
            "prioritized_candidates": ["p"],
        },
    )
    for row in result.items[1:]:
        assert row.origin == "saved_constraint"
        assert row.request_quote is None
        assert len(row.description) <= 600


@pytest.mark.parametrize(
    "hand,model,fulfillment,expected",
    [
        ([], [], "unknown", "retrieval_or_corpus_gap"),
        (["x"], [], "unknown", "planning_gap"),
        (["x"], ["x"], "partial", "synthesis_or_fulfillment_gap"),
        (["x"], ["x"], "complete", "no_structured_gap_observed"),
        (["x"], ["x"], "unknown", "synthesis_unassessed"),
    ],
)
def test_retrieval_diagnostic_separates_failure_stage(
    hand, model, fulfillment, expected
):
    receipt = diagnose_retrieval_stages(
        ["x"], hand, model, synthesis_fulfillment=fulfillment
    )
    assert receipt["diagnosis"] == expected
    assert receipt["limitations"]
    assert diagnose_retrieval_stages([], [], [])["diagnosis"] == "insufficient_label"


def test_policy_corpus_is_frozen_and_reports_its_limitations():
    corpus = Path(__file__).resolve().parents[1] / "fixtures/assistant-events-v1"
    manifest = json.loads((corpus / "quality-manifest.json").read_text())
    for name, expected in manifest["files_sha256"].items():
        assert hashlib.sha256((corpus / name).read_bytes()).hexdigest() == expected
    cases = json.loads((corpus / "quality-cases.json").read_text())
    assert len(cases["cases"]) >= 7
    assert {row["id"] for row in cases["cases"]} >= {
        "marchesa-discovery",
        "kinnan-comparison",
        "tayam-win-condition",
        "insufficient-evidence",
    }
    assert manifest["limitations"]


@pytest.mark.parametrize("length", [4000, 16000])
def test_preserves_full_admitted_and_steered_request(length):
    request = "x" * length
    result = build_obligations(request, 0)
    assert result.request == request
    assert (
        result.request_hash
        == hashlib.sha256(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_rejects_request_above_cumulative_steering_bound():
    with pytest.raises(ValidationError):
        build_obligations("x" * 16001, 0)


def test_successful_model_retrieval_does_not_prove_a_corpus_gap():
    from sabermetrics.assistant_quality_evaluation import diagnose_retrieval_stages

    receipt = diagnose_retrieval_stages(
        ["x"], [], ["x"], synthesis_fulfillment="complete"
    )
    assert receipt["diagnosis"] == "hand_plan_gap_model_retrieval_succeeded"
