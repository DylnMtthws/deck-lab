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


def test_history_matching_content_does_not_certify_current_engine(
    prepared, monkeypatch
):
    from sabermetrics.assistant_memory import research_memory
    from tests.test_assistant_memory import complete

    _, path, owner, deck_id, _, _ = prepared
    complete(prepared, monkeypatch)
    item = research_memory(path, owner, deck_id)["experiments"]["items"][0]
    assert item["freshness"] == "current"
    assert item["freshness_scope"] == "deck_content_only"
    assert item["measurement_freshness"] == "not_revalidated"
    assert "simulator version" in item["measurement_note"]


def test_combination_tool_is_read_only_scoped_and_strips_internal_models(
    prepared, monkeypatch
):
    from sabermetrics import combination_intelligence

    service, path, owner, deck_id, original, _ = prepared
    from tests.test_combination_intelligence import fixture

    seen = []
    real_analysis = combination_intelligence.analyze_combinations

    def analyze(document):
        seen.append(document)
        return real_analysis(fixture()[0])

    monkeypatch.setattr(combination_intelligence, "analyze_combinations", analyze)
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    result = tools.execute(call("analyze_deck_combinations"))
    assert seen == [original]
    assert result["state_origin"] == "deck_list_only"
    assert result["execution_started"] is False
    assert result["original_changed"] is False
    assert "model_cards" not in result["combinations"][0]
    assert result["evidence"]
    assert all(
        "relationships" not in row and len(row["oracle_excerpt"]) <= 240
        for row in result["evidence"].values()
    )
    assert result["combinations"][0]["replay"]["status"] == "not_run"
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    assert not service.list_for_deck(owner, deck_id)
    with pytest.raises(Exception):
        tools.execute(call("analyze_deck_combinations", {"owner_id": "other"}))


def test_hypothetical_replay_uses_current_deck_and_never_claims_game_observation(
    prepared, monkeypatch
):
    from tests.test_combination_intelligence import fixture

    service, path, owner, deck_id, original, _ = prepared
    document, state = fixture()
    document["entries"].append({"name": "Other"})
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    monkeypatch.setattr(tools, "check", lambda: document)
    result = tools.execute(
        call(
            "replay_deck_combination",
            {"combination_index": 0, "hypothetical_state": state},
        )
    )
    assert result["state_origin"] == "hypothetical_tool_input_not_observed_game"
    assert result["replay"]["status"] == "verified_within_bounded_model"
    assert result["replay"]["win_verified"] is False
    assert result["goldfish_executed"] is False
    assert result["original_changed"] is False
    state["mana"] = {"R": 3}
    rejected = tools.execute(
        call(
            "replay_deck_combination",
            {"combination_index": 0, "hypothetical_state": state},
        )
    )
    assert rejected["replay"]["status"] == "rejected"
    assert not rejected["replay"]["trace"]
    with pytest.raises(ValueError, match="index unavailable"):
        tools.execute(
            call(
                "replay_deck_combination",
                {"combination_index": 11, "hypothetical_state": state},
            )
        )
    state["mana"] = {"U": True}
    with pytest.raises(ValueError):
        tools.execute(
            call(
                "replay_deck_combination",
                {"combination_index": 0, "hypothetical_state": state},
            )
        )
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    assert not service.list_for_deck(owner, deck_id)


def test_sourced_combinations_keep_citations_and_do_not_run_experiments(
    prepared, monkeypatch
):
    from sabermetrics import combo_catalog

    service, path, owner, deck_id, original, _ = prepared
    monkeypatch.setattr(
        combo_catalog,
        "known_combinations",
        lambda *a, **kw: {
            "status": "matched",
            "execution_verified": False,
            "matches": [
                {
                    "variant_id": "1369-6586",
                    "ingredient_match": "all_named_cards_present",
                    "step_excerpt": "The source describes an interaction under its stated prerequisites.",
                    "citation": {
                        "url": "https://commanderspellbook.com/combo/1369-6586/",
                        "retrieved_at": "2026-10-14T16:00:00+00:00",
                        "snapshot_sha256": "a" * 64,
                    },
                }
            ],
        },
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Find sourced combinations in this deck."}],
        gateway=Gateway(
            [
                step(call("research_known_combinations")),
                step(
                    content="The source describes a conditional interaction; execution is unverified."
                ),
            ]
        ),
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert "unverified" in result["content"]
    assert "https://commanderspellbook.com/combo/1369-6586/" in json.dumps(
        result["presentation"]
    )
    source = result["presentation"]["strategy_evidence"]["excerpts"][0]
    assert source["excerpt"]
    assert source["source_kind"] == "external_combo_claim"
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    assert not service.list_for_deck(owner, deck_id)


def test_combination_claim_identity_survives_full_receipt_eviction():
    from sabermetrics.assistant_runtime import _retain_combination_context

    state = {"constraints": {}}
    source = {
        "matches": [
            {
                "variant_id": "1369-6586",
                "cards": [{"name": "Quicksilver Elemental"}],
                "ingredient_match": "all_named_cards_present",
                "citation": {"url": "https://commanderspellbook.com/combo/1369-6586/"},
                "prerequisite_excerpt": "Setup requirements remain necessary.",
                "unresolved_template_count": 0,
            }
        ]
    }
    _retain_combination_context(state, "research_known_combinations", source)
    _retain_combination_context(
        state,
        "analyze_deck_combinations",
        {
            "combinations": [
                {
                    "combination_index": 3,
                    "cards": ["Vivi Ornitier", "Curiosity", "Opt"],
                    "status": "inferred",
                    "kind": "cast_damage_draw",
                }
            ]
        },
    )
    _retain_combination_context(
        state,
        "replay_deck_combination",
        {
            "cards": ["Vivi Ornitier"],
            "replay": {
                "status": "verified_prefix_only",
                "unresolved": "Spell remains on stack.",
            },
        },
    )
    transcript = [
        {
            "role": "tool",
            "result": {
                "name": name,
                "data": {"details": {str(i): "x" * 1100 for i in range(10)}},
            },
        }
        for name in ["research_known_combinations", "analyze_deck_combinations"]
    ]
    encoded = _encode_context(
        state, "Explain the conditions and compare evidence.", transcript
    )
    assert len(encoded) <= 16000
    parsed = json.loads(encoded)
    assert parsed["history_truncated"] is True
    retained = parsed["state"]
    assert retained["combination_sources"][0]["variant_id"] == "1369-6586"
    assert retained["combination_sources"][0]["execution_verified"] is False
    assert retained["combination_models"][0]["combination_index"] == 3
    assert retained["combination_replay"]["status"] == "verified_prefix_only"
    assert retained["combination_replay"]["state_origin"].startswith("hypothetical")
    assert retained["combination_replay"]["win_verified"] is False


def test_hypothetical_attachment_prose_is_rejected_with_exact_name_repair_hint(
    prepared, monkeypatch
):
    from copy import deepcopy

    from tests.test_combination_intelligence import reference_decks

    service, path, owner, deck_id, original, _ = prepared
    document = reference_decks()["affordable-vivi"]
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    monkeypatch.setattr(tools, "check", lambda: document)
    state = {
        "battlefield": ["Vivi Ornitier", "Curiosity attached to Vivi Ornitier"],
        "hand": ["Opt"],
        "attachments": {"Curiosity": "Vivi Ornitier"},
        "mana": {"U": 1},
        "library_count": 90,
        "hand_count": 1,
        "opponent_count": 3,
        "priority": True,
        "stack_empty": True,
        "no_interference": True,
        "no_other_effects": True,
        "choose_optional_draw": True,
    }
    initial = deepcopy(state)
    lines = tools.execute(call("analyze_deck_combinations"))["combinations"]
    # Full list index remains stable even when the summary chooses another cast.
    from sabermetrics.combination_intelligence import analyze_combinations

    index = next(
        i
        for i, l in enumerate(analyze_combinations(document)["combinations"])
        if l["cards"] == ["Vivi Ornitier", "Opt", "Curiosity"]
    )
    assert lines
    rejected = tools.execute(
        call(
            "replay_deck_combination",
            {"combination_index": index, "hypothetical_state": state},
        )
    )
    replay = rejected["replay"]
    assert replay["status"] == "rejected"
    assert replay["rejection_kind"] == "state_name_representation"
    assert replay["invalid_names"] == [
        {"field": "battlefield", "supplied_name": "Curiosity attached to Vivi Ornitier"}
    ]
    assert "Curiosity" in replay["expected_exact_combination_names"]
    assert "attachments" in replay["repair_hint"]
    assert "not a failed card interaction" in replay["reason"]
    assert state == initial
    state["battlefield"] = ["Vivi Ornitier", "Curiosity"]
    corrected = tools.execute(
        call(
            "replay_deck_combination",
            {"combination_index": index, "hypothetical_state": state},
        )
    )
    assert corrected["replay"]["status"] == "verified_prefix_only"
    assert corrected["replay"]["final_resources"]["mana"] == {"U": 0}
    assert corrected["replay"]["final_resources"]["hand_count"] == 3
    assert corrected["replay"]["win_verified"] is False
    assert DeckDocumentRepo(path).get(owner, deck_id) == original
    assert not service.list_for_deck(owner, deck_id)
