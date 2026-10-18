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
