"""Integration regressions for sourced memory, early facts and list completeness."""

import json

from sabermetrics.assistant_runtime import AssistantStep, _encode_context, run_turn
from tests.test_assistant_conversation_runtime import seed_card
from tests.test_assistant_runtime import Gateway, call, step

pytest_plugins = ["tests.test_assistant_experiments"]


def test_focused_card_route_renders_authoritative_facts_without_model_call(prepared):
    service, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "fact-card",
        "Confirmed Scholar",
        "When this creature enters, draw a card.",
    )
    # First lookup supplies an authoritative record for the subsequent focused turn.
    first = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Look up Confirmed Scholar."}],
        gateway=Gateway(
            [
                step(call("card_details", {"card_id": "fact-card"})),
                AssistantStep(
                    content="Confirmed Scholar has a printed enter trigger.",
                    findings=["Its printed ability draws a card."],
                ),
            ]
        ),
        experiment_service=service,
    )
    events = []
    gateway = Gateway(
        [
            AssistantStep(
                content="Confirmed Scholar draws a card when it enters.",
                findings=["This is a printed triggered ability."],
                next_action="none",
            )
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            {"role": "assistant", **first},
            {"role": "user", "content": "What does Confirmed Scholar do?"},
        ],
        gateway=gateway,
        direct_routes=True,
        on_event=events.append,
        experiment_service=service,
    )
    assert result["status"] == "done", result
    assert len(gateway.requests) == 0
    assert result["answer_review"]["status"] == "source_rendered"
    assert events[0]["type"] == "tool_started"
    assert any(event["type"] == "tool_result" and event["ok"] for event in events)
    assert "When this creature enters, draw a card." in result["content"]
    assert result["presentation"]["reply"]["next_action"] == ""
    assert result["research"]["runtime"]["model_calls"] == []


def test_requested_list_cannot_finish_without_retrieved_manifest(prepared):
    service, path, owner, deck, _, _ = prepared
    answer = AssistantStep(
        content="Here are seven recommendations.",
        findings=["An unsupported candidate."],
    )
    gateway = Gateway([answer, answer])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me seven card recommendations."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert len(gateway.requests) == 2
    assert result["status"] == "incomplete"
    assert result["reason"] == "discovery_incomplete"
    assert not result["research"]["discovery_validation"]["complete"]


def test_source_ledger_survives_followup_and_refreshes_membership(prepared):
    service, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "remember-card",
        "Remembered Scholar",
        "Whenever a creature dies, draw a card.",
    )
    first = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Look up Remembered Scholar."}],
        gateway=Gateway(
            [
                step(call("card_details", {"card_id": "remember-card"})),
                AssistantStep(
                    content="Remembered Scholar has a death trigger.",
                    findings=["Its printed trigger draws a card."],
                ),
            ]
        ),
        experiment_service=service,
    )
    assert (
        first["research"]["discovery_ledger"]["candidates"][0]["name"]
        == "Remembered Scholar"
    )
    gateway = Gateway(
        [
            AssistantStep(
                content="The retained printed source is still available.",
                findings=["Remembered Scholar draws from its death trigger."],
            )
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            {"role": "assistant", **first},
            {"role": "user", "content": "What was that candidate?"},
        ],
        gateway=gateway,
        experiment_service=service,
    )
    state = json.loads(gateway.requests[0].user)["state"]
    assert (
        state["session_evidence"]["discovery_candidates"][0]["name"]
        == "Remembered Scholar"
    )
    assert not result["research"]["discovery_ledger"]["candidates"][0]["in_deck"]


def test_context_preserves_all_scoped_preferences_and_untruncated_request():
    preferences = [
        {
            "dimension": "core_card",
            "value": f"Core {i}",
            "strength": "required",
            "scope": "deck_membership",
            "polarity": "include",
            "quote": "Keep this core card.",
        }
        for i in range(20)
    ]
    request = "A" * 4300 + " CURRENT FINAL REQUIREMENT"
    context = json.loads(
        _encode_context(
            {"conversation_context": {"preferences": preferences}}, request, []
        )
    )
    assert context["current_user_request"] == request
    assert context["state"]["conversation_context"]["preferences"] == preferences


def test_planner_executes_separate_lists_without_intermediate_model_call(
    prepared, monkeypatch
):
    from sabermetrics.assistant_research_task import DiscoveryListPlan
    from sabermetrics.assistant_tools import AssistantTools
    from tests.test_assistant_request_planner import plan

    service, path, owner, deck, _, _ = prepared
    request = "Find draw engines and sacrifice outlets."
    planned = plan(request, scope="candidate_discovery")
    planned.research_task.discovery_lists = [
        DiscoveryListPlan.model_validate(row)
        for row in [
            {
                "label": "draw engines",
                "query": "repeatable card draw",
                "request_quote": "draw engines",
                "function": "draw",
            },
            {
                "label": "sacrifice outlets",
                "query": "free sacrifice outlets",
                "request_quote": "sacrifice outlets",
                "function": "any",
            },
        ]
    ]
    # Validate the typed boundary rather than trusting model_copy's unchecked updates.
    planned = type(planned).model_validate(planned.model_dump())
    original = AssistantTools.execute

    def execute(self, call):
        if call.name == "search_card_functions":
            name = (
                "Draw Scholar" if call.arguments.function == "draw" else "Free Outlet"
            )
            return {
                "status": "available",
                "cards": [
                    {
                        "id": name,
                        "name": name,
                        "type_line": "Creature",
                        "oracle_text": (
                            "Draw a card."
                            if name == "Draw Scholar"
                            else "Sacrifice another creature: Scry 1."
                        ),
                    }
                ],
            }
        return original(self, call)

    monkeypatch.setattr(AssistantTools, "execute", execute)
    gateway = Gateway(
        [
            planned,
            AssistantStep(
                content="Draw Scholar supplies draw; Free Outlet supplies a sacrifice outlet.",
                findings=["Their strategic fit remains a research hypothesis."],
                discovery_answer={
                    "groups": [
                        {"group_id": "list_1", "card_ids": ["Draw Scholar"]},
                        {"group_id": "list_2", "card_ids": ["Free Outlet"]},
                    ]
                },
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": request}],
        gateway=gateway,
        request_planner=True,
        experiment_service=service,
    )
    assert result["status"] == "done", result
    assert len(gateway.requests) == 2
    assert [
        action["name"]
        for action in result["actions"]
        if action["type"] == "tool_started"
    ] == ["search_card_functions", "search_card_functions"]
    assert {
        row["group_ids"][0]
        for row in result["research"]["discovery_ledger"]["candidates"]
    } == {"list_1", "list_2"}


def test_three_list_context_keeps_facts_when_core_quotes_repeat():
    from sabermetrics.assistant_discovery_contract import (
        DiscoveryLedger,
        candidate_from_card,
    )
    from sabermetrics.assistant_session_state import (
        SessionState,
        compact_preferences,
        expand_preferences,
        protected_session_context,
    )

    quote = "My six core cards are: " + "; ".join(
        f"Retained Core {i}" for i in range(6)
    )
    preferences = [
        {
            "dimension": "core_card",
            "value": f"Retained Core {i}",
            "strength": "required",
            "scope": "recommendations",
            "polarity": "include",
            "operation": "set",
            "quote": quote,
        }
        for i in range(6)
    ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(
                {
                    "id": f"card-{i}",
                    "name": f"Candidate {i}",
                    "type_line": "Creature",
                    "mana_cost": "{2}{B}",
                    "oracle_text": "When another creature dies, draw a card. " * 12,
                },
                rank=i % 8 + 1,
                group_ids=[f"list_{i//8+1}"],
            )
            for i in range(24)
        ]
    )
    state = {
        "conversation_context": {"preferences": preferences},
        "research_task": {
            "preference_updates": preferences,
            "discovery_lists": [{"label": "role", "query": "query" * 60}] * 3,
        },
        "session_evidence": protected_session_context(
            SessionState(ledger=ledger), [], max_chars=9000
        ),
        "consulted_cards": [
            {
                "card": {
                    "id": f"core-{i}",
                    "oracle_id": f"oracle-{i}",
                    "name": f"Retained Core {i}",
                    "oracle_text": "Printed core card text. " * 40,
                }
            }
            for i in range(6)
        ],
        "engine_graph": {"nodes": ["supplemental" * 100] * 12},
    }
    encoded = _encode_context(
        state, "Find three short candidate lists beyond these core cards.", []
    )
    assert len(encoded) <= 24000
    payload = json.loads(encoded)["state"]
    visible = payload["session_evidence"]["discovery_candidates"]
    assert all(
        any(
            row["group_ids"] == [f"list_{group}"]
            and row["printed_fields"].get("oracle_text")
            for row in visible
        )
        for group in (1, 2, 3)
    )
    packed = payload["conversation_context"]["preferences"]
    assert (
        expand_preferences(packed) if isinstance(packed, dict) else packed
    ) == preferences
    assert expand_preferences(compact_preferences(preferences)) == preferences


def test_context_packing_never_mutates_live_memory_under_pressure():
    from copy import deepcopy

    state = {
        "conversation_context": {
            "preferences": [
                {
                    "dimension": "core_card",
                    "value": f"Core {i}",
                    "strength": "required",
                    "quote": "Keep these core cards in the deck." * 6,
                }
                for i in range(20)
            ]
        },
        "research_task": {
            "preference_updates": [{"quote": "Required constraint."}] * 20,
            "discovery_lists": [{"query": "role query"}] * 3,
        },
    }
    before = deepcopy(state)
    _encode_context(
        state,
        "Recap my constraints.",
        [{"role": "assistant", "content": "old prose" * 1200}] * 10,
    )
    assert state == before


def test_named_role_override_handles_selection_mistag_without_recursion():
    from sabermetrics.assistant_research_task import DiscoveryListPlan

    value = DiscoveryListPlan(
        label="Sac outlets",
        request_quote="Find free sac outlets",
        query="Sacrifice another creature",
        function="selection",
    )
    assert value.function == "sacrifice"
    negative = DiscoveryListPlan(
        label="No more sac outlets",
        request_quote="No more sac outlets",
        query="New draw engines",
        function="selection",
    )
    assert negative.function == "any"
