"""New research tools retain the existing copy-only experiment boundaries."""

import json

import pytest

from sabermetrics import db
from sabermetrics.assistant_runtime import _model_receipt, run_turn
from sabermetrics.assistant_tools import TOOL_ADAPTER, AssistantTools
from sabermetrics.deck_documents import DeckDocumentRepo, RevisionConflict
from tests.test_assistant_runtime import Gateway, call, step

pytest_plugins = ["tests.test_assistant_experiments"]


def tools_for(prepared, mode="suggest"):
    service, path, owner, deck_id, _, _ = prepared
    return AssistantTools(
        path, owner, deck_id, mode, {}, lambda: False, experiment_service=service
    )


def test_strategy_evidence_reaches_final_presentation_with_sources(prepared):
    service, path, owner, deck_id, _, _ = prepared
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO reference_chunks(id,document,section,tier,content) VALUES(?,?,?,?,?)",
            (
                "test-strategy",
                "https://example.org/strategy",
                "Recovery",
                2,
                "Recovery after a board wipe requires useful cards from an empty board.",
            ),
        )
        conn.commit()
    gateway = Gateway(
        [
            step(
                call("retrieve_strategy_evidence", {"query": "board wipe recovery"}),
                call("assess_deck_requirements"),
            ),
            step(
                content="Consider recovery options; these are unmeasured suggestions."
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Assess recovery"}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    evidence = result["presentation"]["strategy_evidence"]
    assert evidence["excerpts"][0]["citation"]["chunk_id"] == "test-strategy"
    assert (
        evidence["excerpts"][0]["citation"]["source_url"]
        == "https://example.org/strategy"
    )
    compact = _model_receipt({"name": "retrieve_strategy_evidence", "data": evidence})
    assert compact["data"]["excerpts"][0]["citation"]["chunk_id"] == "test-strategy"
    assert len(json.dumps(compact)) < 12000


def test_recommendations_use_server_held_ids_and_copy_only_experiment(
    prepared, monkeypatch
):
    from sabermetrics import assistant_deck_intelligence as intelligence
    from tests.test_assistant_experiments import report

    service, path, owner, deck_id, original, alternatives = prepared
    monkeypatch.setattr(
        intelligence,
        "recommend_replacements",
        lambda *a, **kw: {"alternatives": alternatives},
    )
    monkeypatch.setattr(
        service, "_simulate", lambda document, seed, settings: report(seed)
    )
    tools = tools_for(prepared, "automate")
    found = tools.execute(call("find_replacements", {"focus": "recovery"}))
    assert found["experiment_ready"]
    receipt = tools.execute(
        call(
            "test_recommended_alternatives",
            {"recommendation_id": found["recommendation_id"]},
        )
    )
    assert receipt["status"] == "done"
    assert len(receipt["alternatives"]) == 3
    assert receipt["original_changed"] is False
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    with pytest.raises(ValueError, match="Only one experiment"):
        tools.execute(
            call(
                "test_recommended_alternatives",
                {"recommendation_id": found["recommendation_id"]},
            )
        )


def test_suggest_unknown_and_insufficient_recommendations_cannot_execute(prepared):
    tools = tools_for(prepared)
    tools.recommendations["ready"] = prepared[-1]
    with pytest.raises(ValueError, match="Automate"):
        tools.execute(
            call("test_recommended_alternatives", {"recommendation_id": "ready"})
        )
    with pytest.raises(ValueError, match="unavailable"):
        tools.execute(
            call("test_recommended_alternatives", {"recommendation_id": "invented"})
        )
    tools.recommendations["short"] = prepared[-1][:2]
    with pytest.raises(ValueError, match="Three valid"):
        tools.execute(
            call("test_recommended_alternatives", {"recommendation_id": "short"})
        )
    assert tools.experiments.list_for_deck(tools.owner, tools.deck_id) == []


def test_recommendation_execution_rechecks_revision(prepared):
    tools = tools_for(prepared, "automate")
    tools.recommendations["ready"] = prepared[-1]
    with db.connect(tools.path) as conn:
        conn.execute(
            "UPDATE deck_documents SET revision=revision+1 WHERE id=?", (tools.deck_id,)
        )
        conn.commit()
    with pytest.raises(RevisionConflict):
        tools.execute(
            call("test_recommended_alternatives", {"recommendation_id": "ready"})
        )


def test_new_tool_arguments_are_bounded():
    from pydantic import ValidationError

    for raw in [
        call("retrieve_strategy_evidence", {"query": "x", "limit": 5}),
        call("find_replacements", {"focus": "x" * 301}),
        call("find_replacements", {"budget": 999}),
    ]:
        with pytest.raises(ValidationError):
            TOOL_ADAPTER.validate_python(raw)


def test_semantic_preferences_need_explicit_review_for_both_experiment_paths(prepared):
    tools = tools_for(prepared, "automate")
    tools.constraints = {"theme": "LOTR only", "power_level": "casual"}
    tools.recommendations["ready"] = prepared[-1]
    for name, args in [
        ("test_recommended_alternatives", {"recommendation_id": "ready"}),
        ("start_experiment", {"alternatives": prepared[-1]}),
    ]:
        with pytest.raises(ValueError, match="Review theme and power"):
            tools.execute(call(name, args))
        reviews = [
            {
                "alternative_index": i,
                "theme_fit": "unknown",
                "power_fit": "fit",
                "reasoning": "The provided card evidence does not establish the requested theme.",
            }
            for i in range(3)
        ]
        with pytest.raises(ValueError, match="unknown fit"):
            tools.execute(call(name, {**args, "semantic_reviews": reviews}))
    assert tools.experiments_started == 0


def test_multiple_strategy_queries_keep_previous_citations():
    from sabermetrics.assistant_runtime import _merge_strategy_evidence

    def evidence(identifier):
        return {
            "excerpts": [
                {
                    "citation": {"document": "guide", "chunk_id": identifier},
                    "excerpt": "A bounded source passage.",
                }
            ]
        }

    merged = _merge_strategy_evidence(evidence("a"), evidence("b"))
    assert [item["citation"]["chunk_id"] for item in merged["excerpts"]] == ["a", "b"]
    assert (
        _merge_strategy_evidence(merged, {"status": "no_matches", "excerpts": []})[
            "excerpts"
        ]
        == merged["excerpts"]
    )


def test_discovery_is_stable_and_workflow_pins_the_next_experiment(
    prepared, monkeypatch
):
    from types import SimpleNamespace

    from sabermetrics import assistant_deck_intelligence as intelligence
    from tests.test_assistant_experiments import report

    service, path, owner, deck_id, original, alternatives = prepared
    discoveries = []

    def discover(*args, **kwargs):
        discoveries.append(kwargs)
        return {"alternatives": alternatives, "requires_semantic_review": False}

    monkeypatch.setattr(intelligence, "recommend_replacements", discover)
    monkeypatch.setattr(
        service, "_simulate", lambda document, seed, settings: report(seed)
    )

    class WorkflowGateway:
        provider = "test"
        model_id = "workflow-test"
        calls = 0

        def generate(self, request):
            self.calls += 1
            workflow = json.loads(request.user)["state"]["workflow"]
            if self.calls == 1:
                answer = step(call("find_replacements", {"focus": "mana"}))
            elif self.calls == 2:
                assert workflow["recommendation"]["ready"]
                assert workflow["recommendation"]["do_not_repeat_discovery"]
                answer = step(
                    call(
                        "test_recommended_alternatives",
                        {
                            "recommendation_id": workflow["recommendation"][
                                "recommendation_id"
                            ]
                        },
                    )
                )
            else:
                assert workflow["experiment_status"] == "done"
                answer = step(content="Three alternatives were tested on copies.")
            return SimpleNamespace(value=answer)

    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Find and test three mana alternatives."}],
        mode="automate",
        gateway=WorkflowGateway(),
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert len(discoveries) == 1
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    tools = tools_for(prepared, "automate")
    first = tools.execute(call("find_replacements", {"focus": "mana"}))
    second = tools.execute(call("find_replacements", {"focus": "  MANA  "}))
    assert first["recommendation_id"] == second["recommendation_id"]
    assert second["already_completed"] is True
    assert len(discoveries) == 2
