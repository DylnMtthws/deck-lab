import copy
import json

from sabermetrics.assistant_conversation_context import PreferenceUpdate
from sabermetrics.assistant_runtime import _encode_context
from sabermetrics.assistant_session_state import expand_preferences


def followup_state():
    oracle = (
        "Pay 1 life, Sacrifice another creature: Draw a card. "
        + "Printed mechanical context remains supplied. " * 18
        + "Activate only once each turn."
    )
    cards = [
        {
            "id": f"candidate-{i}",
            "name": f"Candidate {i}",
            "mana_cost": "{2}{B}",
            "type_line": "Creature",
            "oracle_text": oracle,
        }
        for i in range(8)
    ]
    quote = "My core cards are: " + "; ".join(f"Core {i}" for i in range(6))
    preferences = [
        PreferenceUpdate(
            dimension="core_card", value=f"Core {i}", strength="required", quote=quote
        ).model_dump()
        for i in range(6)
    ]
    preferences += [
        PreferenceUpdate(
            dimension="card_type",
            value="creature",
            strength="required",
            quote="Hard rule: creatures only for new recommendations",
        ).model_dump()
    ]
    return {
        "conversation_context": {"preferences": preferences},
        "consulted_cards": [
            {
                "card": card,
                "scope": "Repeated source boilerplate. " * 6,
                "provenance": {"source": "card_catalog", "retrieved_at": "2026-10-18"},
            }
            for card in cards
        ],
        "focused_card_names": [card["name"] for card in cards],
        "session_evidence": {
            "discovery_candidates": [
                {
                    "card_id": card["id"],
                    "name": card["name"],
                    "printed_fields": {
                        "mana_cost": card["mana_cost"],
                        "oracle_text": oracle,
                    },
                }
                for card in cards
            ]
        },
        "prior_conclusions": {
            "items": [
                {
                    "claim": "Prior assistant reasoning remains unverified. " * 8,
                    "evidence_refs": [f"card:candidate-{i}"],
                    "review_status": "unverified",
                }
                for i in range(4)
            ]
        },
        "research_task": {"kind": "discover", "preference_updates": preferences},
    }


def test_followup_deduplicates_printed_fields_without_losing_focused_facts():
    state = followup_state()
    before = copy.deepcopy(state)
    payload = json.loads(
        _encode_context(
            state,
            "Rank seven additional draw creatures, keeping my prior preferences.",
            [],
        )
    )
    assert state == before
    assert len(json.dumps(payload, ensure_ascii=False)) <= 24000
    cards = {
        row["card"]["id"]: row["card"] for row in payload["state"]["consulted_cards"]
    }
    for candidate in payload["state"]["session_evidence"]["discovery_candidates"]:
        assert "oracle_text" in candidate["fields_in_consulted_cards"]
        assert cards[candidate["card_id"]]["oracle_text"].endswith(
            "Activate only once each turn."
        )
    assert payload["state"]["prior_conclusions"] == state["prior_conclusions"]
    preferences = payload["state"]["conversation_context"]["preferences"]
    assert (
        expand_preferences(preferences)
        if isinstance(preferences, dict)
        else preferences
    ) == state["conversation_context"]["preferences"]


def test_focused_evidence_can_use_bounded_extension_instead_of_early_raise():
    state = followup_state()
    # These extra independent fields cannot be deduplicated against the session
    # ledger. They recreate the live all-focused branch at 16-24k characters.
    state["session_evidence"]["discovery_candidates"] = []
    state["session_evidence"]["session_findings"] = [
        {
            "kind": "rules",
            "claim": "Supplied rules text. " * 170,
            "evidence": [{"reference": "rules:1", "quote": "Actual source quote"}],
        }
    ]
    payload = json.loads(
        _encode_context(
            state, "Compare all eight focused candidates under the same rules.", []
        )
    )
    assert len(payload["state"]["consulted_cards"]) == 8
    assert payload["context_budget_extended"]
    assert len(json.dumps(payload, ensure_ascii=False)) <= 24000


def test_shared_facts_are_restored_when_consulted_source_is_evicted():
    state = followup_state()
    state["focused_card_names"] = []
    payload = json.loads(
        _encode_context(state, "A long current instruction. " * 150, [])
    )
    consulted = {row["card"]["id"] for row in payload["state"]["consulted_cards"]}
    for card in payload["state"]["session_evidence"]["discovery_candidates"]:
        if card.get("fields_in_consulted_cards"):
            assert card["card_id"] in consulted
        else:
            assert card["printed_fields"]["oracle_text"].endswith(
                "Activate only once each turn."
            )


def test_broad_followup_replaces_stale_selected_notes_with_source_handles():
    state = followup_state()
    state["focused_card_names"] = []
    state["conversation_context"]["user_statements"] = [
        {"id": f"u{i}", "text": "Previous user statement. " * 35} for i in range(4)
    ]
    state["session_evidence"]["discovery_candidates"].extend(
        {
            "card_id": f"older-candidate-{i}",
            "name": f"Earlier Candidate {i}",
            "rank": i + 1,
            "group_ids": [f"list_{i % 3 + 1}"],
            "in_deck": False,
            "printed_fields": {"type_line": "Creature", "mana_cost": "{2}{B}"},
        }
        for i in range(24)
    )
    state["prior_selected_candidates"] = {
        "groups": [
            {"group_id": "list_1", "card_ids": [f"candidate-{i}" for i in range(8)]}
        ],
        "claims": [
            {
                "card_id": "candidate-0",
                "source_id": "card:candidate-0",
                "fact_id": "oracle_text",
                "kind": "oracle_text",
                "value": "Repeated prior value. " * 50,
            }
        ],
        "notes": [
            {
                "card_id": f"candidate-{i}",
                "note": "Prior model interpretation repeats the retrieved Oracle text. "
                * 8,
            }
            for i in range(8)
        ],
    }
    before = copy.deepcopy(state)
    transcript = [
        {
            "role": "tool",
            "result": {
                "name": "search_cards",
                "data": {
                    "cards": [
                        {
                            "id": f"candidate-{i}",
                            "oracle_text": "Duplicate receipt facts. " * 100,
                        }
                        for i in range(8)
                    ]
                },
            },
        }
    ]
    encoded = _encode_context(
        state,
        "Find three additional repeatable draw creatures under the inherited preferences.",
        transcript,
    )
    payload = json.loads(encoded)
    assert len(encoded) <= 24000
    selected = payload["state"]["prior_selected_candidates"]
    assert selected["groups"] == before["prior_selected_candidates"]["groups"]
    assert selected["note_card_ids"] == [f"candidate-{i}" for i in range(8)]
    assert selected["notes_omitted_count"] == 8
    assert selected["claims"][0]["source_id"] == "card:candidate-0"
    assert selected["claims"][0]["value_omitted"]
    assert "notes" not in selected
    assert payload["state"]["prior_conclusions"] == before["prior_conclusions"]
    assert state == before


def test_impossible_context_reports_numeric_slot_sizes_without_source_text():
    import pytest

    from sabermetrics.assistant_runtime import AssistantBudgetExceeded

    with pytest.raises(AssistantBudgetExceeded) as raised:
        _encode_context(
            {"constraints": {}}, "Unshortenable current user request. " * 1000, []
        )
    sizes = raised.value.context_sizes
    assert sizes and sizes["_total"] > 24000
    assert all(isinstance(value, int) for value in sizes.values())


def test_protected_identities_survive_pressure_before_supplemental_eviction():
    state = followup_state()
    protected = [f"protected-card-{index}" for index in range(24)]
    state["constraints"] = {"protected_cards": protected}
    state["engine_graph"] = {
        "nodes": [{"details": "Supplemental engine prose. " * 60} for _ in range(12)]
    }
    payload = json.loads(_encode_context(state, "Keep all protected cards.", []))
    assert "engine_graph" in payload.get(
        "state_evidence_shortened", []
    ) or "engine_graph" in payload.get("state_evidence_omitted", [])
    assert payload["state"]["constraints"]["protected_cards"] == protected
    assert state["constraints"]["protected_cards"] == protected


def test_prior_selection_keeps_more_than_twelve_group_members():
    state = {
        "prior_selected_candidates": {
            "groups": [
                {"group_id": "list_1", "card_ids": [f"card-{i}" for i in range(20)]}
            ],
            "claims": [],
            "notes": [],
        }
    }
    payload = json.loads(_encode_context(state, "Compare those candidates.", []))
    assert (
        payload["state"]["prior_selected_candidates"]["groups"]
        == state["prior_selected_candidates"]["groups"]
    )


def test_omission_metadata_names_each_field_once_but_counts_all_facts():
    from sabermetrics.assistant_discovery_contract import (
        DiscoveryCandidate,
        DiscoveryFact,
        DiscoveryLedger,
    )
    from sabermetrics.assistant_session_state import (
        SessionState,
        protected_session_context,
    )

    facts = [
        DiscoveryFact(
            fact_id=f"condition-{i}",
            kind="condition",
            value=f"Condition {i}",
            source_id="card:c",
        )
        for i in range(8)
    ]
    state = SessionState(
        ledger=DiscoveryLedger(
            candidates=[
                DiscoveryCandidate(card_id="c", name="Source Card", source_facts=facts)
            ]
        )
    )
    context = protected_session_context(state, [], max_chars=9000)
    assert context["discovery_candidates"][0]["omitted_fields"] == ["condition"]
    assert context["candidate_facts_omitted"] == 8


def test_extension_checks_actual_envelope_instead_of_guessing_a_margin():
    state = {
        "session_evidence": {
            "discovery_candidates": [{"card_id": "c", "name": "Source"}]
        }
    }
    base = len(_encode_context(state, "q", [])) - 1
    request = "q" * (23910 - base)
    wire = _encode_context(state, request, [])
    assert len(wire) <= 24000
    payload = json.loads(wire)
    assert payload["current_user_request"] == request
    assert payload["context_budget_extended"] is True
