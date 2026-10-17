"""Narrow planner contract and routing tests; all models/transports are mocked."""

import json

from sabermetrics.assistant_request_planner import (
    RequestPlan,
    planner_system,
    planning_payload,
    synthesis_system,
)
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.assistant_tools import AssistantTools
from tests.test_assistant_conversation_runtime import seed_card
from tests.test_assistant_runtime import Gateway, call, step

pytest_plugins = ["tests.test_assistant_experiments"]


def plan(
    request,
    *,
    named=(),
    empirical=False,
    route="use_tools",
    answer=None,
    assessment=None,
):
    return RequestPlan.model_validate(
        {
            "route": route,
            "direct_answer": answer,
            "research_task": {
                "kind": "compare",
                "current_request_quote": request,
                "deliverables": [
                    "Answer the current comparison using the requested evidence"
                ],
                "requires_empirical_evidence": empirical,
                "named_cards": list(named),
                "preference_assessment": assessment
                or {
                    "goal": None,
                    "multifunctionality": None,
                    "card_type": None,
                    "draw_control": None,
                    "mana": None,
                    "excluded_cards": None,
                },
            },
        }
    )


def test_first_schema_and_prompt_omit_execution_tool_choices():
    schema = json.dumps(RequestPlan.model_json_schema())
    assert len(schema) < 12000
    assert "tool_calls" not in schema and "SearchCall" not in schema
    assert "Proposal" not in schema and "ResearchHypothesis" not in schema
    assert "route" in RequestPlan.model_json_schema()["required"]
    assert len(planner_system()) < 3500
    assert len(synthesis_system()) < 3000


def test_planner_payload_prioritizes_current_request_without_assistant_or_tool_history():
    state = {
        "conversation_context": {
            "user_statements": [
                {"text": "Earlier USER preference"},
                {"text": "The current request"},
            ],
            "preferences": [
                {
                    "dimension": "card_type",
                    "value": "creature",
                    "strength": "required",
                    "quote": "Earlier USER preference",
                    "source_turn": "omit-private-id",
                }
            ],
        },
        "commanders": [
            {"name": "Current Commander", "oracle_text": "Printed source facts."}
        ],
        "deck_composition": {"library_card_count": 92, "front_land_count": 34},
        "workflow": {"assistant": "OLD ASSISTANT ANSWER", "tools": "OLD TOOL HISTORY"},
        "consulted_cards": [{"card": {"name": "OLD RETRIEVED SHORTLIST"}}],
    }
    encoded = planning_payload("The current request", state)
    result = json.loads(encoded)
    assert result["current_user_request"] == "The current request"
    assert result["capacity"]["open_slots"] == 7
    assert result["saved_preferences"][0]["value"] == "creature"
    assert result["recent_user_statements"] == [{"text": "Earlier USER preference"}]
    for excluded in (
        "OLD ASSISTANT ANSWER",
        "OLD TOOL HISTORY",
        "OLD RETRIEVED SHORTLIST",
        "omit-private-id",
    ):
        assert excluded not in encoded


def test_use_tools_route_executes_next_stage_instead_of_publishing_progress(prepared):
    service, path, owner, deck, _, _ = prepared
    request = "Compare actual current deck ingredients."
    gateway = Gateway(
        [
            plan(
                request,
                answer={
                    "content": "PLANNER PROGRESS: I will fetch the facts.",
                    "findings": [
                        "Routing text must not be published as a completed answer."
                    ],
                },
            ),
            step(call("inspect_deck")),
            AssistantStep(
                content="The current deck ingredients are available in the confirmed receipt.",
                findings=["The inspected deck provides actual current entries."],
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
    assert len(gateway.requests) == 3
    assert gateway.requests[0].schema is RequestPlan
    assert gateway.requests[1].schema.__name__ == "AssistantStep"
    assert "PLANNER PROGRESS" not in result["content"]
    assert any(
        event["type"] == "tool_started" and event["name"] == "inspect_deck"
        for event in result["actions"]
    )
    assert result["research"]["task"]["kind"] == "compare"


def test_narrow_named_lookup_uses_two_calls_and_small_final_schema(prepared):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    request = "Assess Elegy Acolyte using its printed text."
    gateway = Gateway(
        [
            plan(request, named=["Elegy Acolyte"]),
            AssistantStep(
                content="Elegy Acolyte offers combat-dependent drawing and conditional tokens.",
                findings=["Its draw requires combat damage to a player."],
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
    assert len(gateway.requests) == 2
    assert gateway.requests[-1].schema.__name__ == "AssistantAnswerStep"
    assert gateway.requests[-1].system.startswith(synthesis_system())
    assert len(gateway.requests[-1].system) < 3500
    assert "find_replacements" not in gateway.requests[-1].system
    assert [
        event["name"] for event in result["actions"] if event["type"] == "tool_started"
    ] == ["card_details"]
    assert "combat damage" in gateway.requests[-1].user


def test_current_three_preferences_merge_without_erasing_inherited_type(
    prepared, monkeypatch
):
    service, path, owner, deck, _, _ = prepared
    request = "Compare options for late-game draw density, several useful roles, and draw I can choose to use."
    evidence = {
        "goal": {
            "value": "late-game draw density",
            "strength": "preferred",
            "quote": "late-game draw density",
        },
        "multifunctionality": {
            "value": "several useful roles",
            "strength": "preferred",
            "quote": "several useful roles",
        },
        "draw_control": {
            "value": "optional",
            "strength": "preferred",
            "quote": "draw I can choose to use",
        },
        "card_type": None,
        "mana": None,
        "excluded_cards": None,
    }
    received = []
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda path, document, args: received.append(args)
        or {"status": "available", "cards": []},
    )
    gateway = Gateway(
        [
            plan(request, assessment=evidence),
            step(call("search_card_functions", {})),
            AssistantStep(
                content="The options have different costs and enabling conditions.",
                findings=[
                    "Current draw control and multiple-role preferences remain active."
                ],
            ),
        ]
    )
    messages = [
        {"role": "user", "id": "earlier", "content": "Only creatures are candidates."},
        {
            "role": "assistant",
            "research": {
                "conversation_preferences": [
                    {
                        "dimension": "card_type",
                        "value": "creature",
                        "strength": "required",
                        "quote": "Only creatures are candidates",
                        "source_turn": "earlier",
                    }
                ]
            },
        },
        {"role": "user", "id": "current", "content": request},
    ]
    result = run_turn(
        path,
        owner,
        deck,
        messages,
        gateway=gateway,
        request_planner=True,
        experiment_service=service,
    )
    assert {
        row["dimension"] for row in result["research"]["conversation_preferences"]
    } == {"goal", "multifunctionality", "draw_control", "card_type"}
    assert (
        received[0].type_line == "creature" and received[0].optionality == "preferred"
    )
    assert received[0].prefer_recurring and received[0].prefer_multifunctional
    assert received[0].mana_max is None


def test_empirical_named_comparison_does_not_short_circuit_to_printed_facts(
    prepared, monkeypatch
):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    request = "Assess Elegy Acolyte and retrieve actual Goldfish measurements."
    original = AssistantTools.execute
    empirical = []

    def execute(self, tool):
        if tool.name == "latest_goldfish":
            empirical.append(tool.name)
            return {
                "status": "not_simulated",
                "report_status": "unavailable",
                "observations": [],
            }
        return original(self, tool)

    monkeypatch.setattr(AssistantTools, "execute", execute)
    gateway = Gateway(
        [
            plan(
                request,
                named=["Elegy Acolyte"],
                empirical=True,
                route="answer",
                answer={
                    "content": "UNSUPPORTED PLANNER MEASUREMENTS are already ready.",
                    "findings": ["This planner text must be discarded."],
                },
            ),
            step(call("latest_goldfish")),
            AssistantStep(
                content="The current lookup did not supply measured Goldfish results.",
                findings=["Printed card text is not a measured deck result."],
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
    assert len(gateway.requests) == 3
    assert gateway.requests[1].schema.__name__ == "AssistantStep"
    assert empirical == ["latest_goldfish"]
    assert "UNSUPPORTED PLANNER MEASUREMENTS" not in result["content"]
    assert result["research"]["task"]["requires_empirical_evidence"] is True


def test_plan_routing_preserves_call_budget_and_cancellation(prepared, monkeypatch):
    service, path, owner, deck, _, _ = prepared
    request = "Compare actual current ingredients."
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_MODEL_CALLS", 2)
    gateway = Gateway([plan(request), step(call("inspect_deck"))])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": request}],
        gateway=gateway,
        request_planner=True,
        experiment_service=service,
    )
    assert len(gateway.requests) == 2
    assert result["status"] == "budget_exhausted"
    assert not result["actions"]
    stopped = []

    class StoppingGateway(Gateway):
        def generate(self, request):
            answer = super().generate(request)
            stopped.append(True)
            return answer

    gateway = StoppingGateway([plan(request)])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": request}],
        gateway=gateway,
        request_planner=True,
        should_stop=lambda: bool(stopped),
        experiment_service=service,
    )
    assert result["status"] == "stopped" and len(gateway.requests) == 1
    assert not result["actions"]
