"""End-to-end tool contracts for bounded knowledge, planning and measurement."""

import json

import pytest

from sabermetrics.assistant_runtime import _encode_context, _model_receipt, run_turn
from sabermetrics.assistant_tools import AssistantTools
from sabermetrics.deck_documents import DeckDocumentRepo
from tests.test_assistant_runtime import Gateway, call, step

pytest_plugins = ["tests.test_assistant_experiments"]


def setup_plan(prepared, monkeypatch):
    from sabermetrics import assistant_deck_intelligence as intelligence
    from sabermetrics import assistant_strategy_evidence as evidence

    service, path, owner, deck_id, original, alternatives = prepared
    monkeypatch.setattr(
        intelligence,
        "recommend_replacements",
        lambda *a, **kw: {
            "alternatives": alternatives,
            "requires_semantic_review": False,
        },
    )
    monkeypatch.setattr(
        evidence,
        "retrieve_strategy_evidence",
        lambda *a, **kw: {
            "status": "ok",
            "excerpts": [
                {
                    "text": "Useful recovery needs an independent action.",
                    "citation": {
                        "document": "https://example.org/primer",
                        "chunk_id": "recovery",
                        "source_url": "https://example.org/primer",
                    },
                }
            ],
        },
    )
    return service, path, owner, deck_id, original, alternatives


def test_prepare_is_read_only_stable_and_retains_sources(prepared, monkeypatch):
    service, path, owner, deck_id, original, _ = setup_plan(prepared, monkeypatch)
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    plan = tools.execute(call("prepare_improvement", {"focus": "recovery"}))
    repeated = tools.execute(call("prepare_improvement", {"focus": "recovery"}))
    assert plan["recommendation_id"] == repeated["recommendation_id"]
    assert plan["execution_started"] is False
    assert plan["strategy_evidence"]["excerpts"]
    assert plan["assessment"]
    assert not service.list_for_deck(owner, deck_id)
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    with pytest.raises(ValueError, match="Automate"):
        tools.execute(
            call(
                "test_recommended_alternatives",
                {"recommendation_id": plan["recommendation_id"]},
            )
        )


def test_prepare_to_three_copy_execution_and_presentation(prepared, monkeypatch):
    from tests.test_assistant_experiments import report

    service, path, owner, deck_id, original, _ = setup_plan(prepared, monkeypatch)
    monkeypatch.setattr(
        service, "_simulate", lambda document, seed, settings: report(seed)
    )

    class PlanningGateway(Gateway):
        def generate(self, request):
            if len(self.requests) == 1:
                state = json.loads(request.user)
                recommendation = state["state"]["workflow"]["recommendation"]
                self.steps = iter(
                    [
                        step(
                            call(
                                "test_recommended_alternatives",
                                {
                                    "recommendation_id": recommendation[
                                        "recommendation_id"
                                    ],
                                },
                            )
                        ),
                        step(
                            content="Three copies were tested; unknown mechanics remain unknown."
                        ),
                    ]
                )
            return super().generate(request)

    # Scripted gateway verifies orchestration; it is not a model-quality claim.
    gateway = PlanningGateway(
        [
            step(call("prepare_improvement", {"focus": "recovery"})),
            step(),
            step(content="Three copies were tested; unknown mechanics remain unknown."),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Test three recovery alternatives."}],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    experiments = service.list_for_deck(owner, deck_id)
    assert len(experiments) == 1
    assert len(experiments[0]["alternatives"]) == 3
    assert result["presentation"]["strategy_evidence"]["excerpts"]
    assert DeckDocumentRepo(path).get(owner, deck_id) == original


def test_knowledge_receipts_never_expand_context_without_bound():
    receipt = {
        "name": "prepare_improvement",
        "data": {
            "strategy_evidence": {"excerpts": [{"text": "x" * 30000}] * 100},
            "recommendation_id": "stable",
            "experiment_ready": True,
        },
    }
    compact = _model_receipt(receipt)
    assert len(json.dumps(compact)) <= 12000
    encoded = _encode_context(
        {
            "constraints": {},
            "workflow": {"recommendation": {"recommendation_id": "stable"}},
        },
        "Improve recovery",
        [{"role": "tool", "result": receipt}] * 20,
    )
    assert len(encoded) <= 16000
    assert (
        json.loads(encoded)["state"]["workflow"]["recommendation"]["recommendation_id"]
        == "stable"
    )


def test_history_outcome_uses_metrics_omitted_from_presentation():
    from sabermetrics.assistant_memory import _metric_rows

    metrics = {
        f"metric_{i}": {"comparable": True, "before": 1, "after": 2, "delta": 1}
        for i in range(7)
    }
    rows, total, validated = _metric_rows(
        {"metric_deltas": {"opening": metrics}},
        {
            "comparison": {
                "metric_deltas": {"opening": {"metric_6": {"validated": True}}}
            }
        },
    )
    assert len(rows) == 6 and total == 7
    assert not any(row["held_out_validated"] for row in rows)
    assert validated is True


def test_memory_tool_reads_persisted_preferences_without_starting_experiment(prepared):
    from sabermetrics.assistant_store import AssistantStore

    service, path, owner, deck_id, original, _ = prepared
    AssistantStore(path).settings(
        owner, deck_id, "suggest", {"theme": "Keep the theme", "budget": 200}
    )
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    memory = tools.execute(call("research_memory"))
    assert memory["preferences"]["constraints"]["theme"] == "Keep the theme"
    assert memory["preferences"]["constraints"]["budget"] == 200
    assert not service.list_for_deck(owner, deck_id)
    assert DeckDocumentRepo(path).get(owner, deck_id) == original


def test_prepare_with_stopword_focus_still_assesses_and_prepares(prepared, monkeypatch):
    from sabermetrics import assistant_deck_intelligence as intelligence

    service, path, owner, deck_id, _, alternatives = prepared
    monkeypatch.setattr(
        intelligence,
        "recommend_replacements",
        lambda *a, **kw: {"alternatives": alternatives},
    )
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    result = tools.execute(call("prepare_improvement", {"focus": "how can I"}))
    assert result["experiment_ready"]
    assert result["assessment"]
    assert result["strategy_evidence"]["query_note"]
    assert not service.list_for_deck(owner, deck_id)


def test_large_plan_preserves_sources_and_proposal_handle_before_optional_details():
    sources = {
        "excerpts": [
            {
                "excerpt": "Exact source text " * 35,
                "citation": {
                    "document": "Primer",
                    "source_url": "https://example.org/primer",
                    "chunk_id": str(i),
                },
            }
            for i in range(4)
        ]
    }
    data = {
        "strategy_evidence": sources,
        "recommendation_id": "stable",
        "experiment_ready": True,
        "alternatives": [
            {
                "name": "Alternative",
                "rationale": "Useful bounded rationale",
                "replacements": [
                    {"outgoing_entry_id": "out", "incoming_card_id": "in"}
                ],
            }
        ]
        * 3,
        "candidate_evidence": [
            {"oracle_text": "x" * 1000, "conditions": ["y" * 1000] * 8}
        ]
        * 12,
        "assessment": {"requirements": [{"details": "z" * 1000}] * 12},
    }
    receipt = _model_receipt({"name": "prepare_improvement", "data": data})
    assert len(json.dumps(receipt)) < 12000
    assert (
        receipt["data"]["strategy_evidence"]["excerpts"][0]["excerpt"]
        == sources["excerpts"][0]["excerpt"]
    )
    assert receipt["data"]["recommendation_id"] == "stable"
    assert len(receipt["data"]["alternatives"]) == 3
    assert receipt["evidence_omitted"]
