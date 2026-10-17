import json

import pytest

from sabermetrics import db
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.assistant_tools import AssistantTools
from tests.test_assistant_runtime import Gateway, call, step

pytest_plugins = ["tests.test_assistant_experiments"]

ELEGY = "Lifelink\nWhenever one or more creatures you control deal combat damage to a player, you draw a card and lose 1 life.\nVoid — At the beginning of your end step, if a nonland permanent left the battlefield this turn, create a 2/2 colorless Robot artifact creature token."


def seed_card(path, cid, name, text=ELEGY):
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,mana_cost,cmc,color_identity,is_legal_in_99) VALUES(?,?,?,'Creature',?,'{2}{B}{B}',4,'[]',1)",
            (cid, cid, name, text),
        )
        conn.commit()


def test_named_followup_research_precedes_discovery_and_forces_synthesis(prepared):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {
                        "kind": "compare",
                        "named_cards": ["Elegy Acolyte"],
                    },
                    "tool_calls": [call("search_cards", {"oracle_text": "draw"})],
                }
            ),
            step(call("search_cards", {"oracle_text": "dies"})),
            step(
                content="Elegy Acolyte draws on combat damage; it is not a death-draw engine."
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "What about Elegy Acolyte?"}],
        gateway=gateway,
        experiment_service=service,
    )
    tools = [a["name"] for a in result["actions"] if a["type"] == "tool_started"]
    assert tools == ["card_details"]
    assert gateway.requests[-1].schema.__name__ == "AssistantAnswerStep"
    assert any(
        c["card"]["oracle_text"] == ELEGY
        for c in json.loads(gateway.requests[-1].user)["state"]["consulted_cards"]
    )
    assert result["status"] == "done"


def test_previous_card_receipt_and_preferences_survive_tool_noise(prepared):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    facts = AssistantTools(path, owner, deck, "suggest", {}, lambda: False).execute(
        call("card_details", {"card_id": "elegy"})
    )
    messages = [
        {"id": "prior", "role": "user", "content": "Prefer optional draw."},
        {
            "role": "tool",
            "action": {
                "type": "tool_result",
                "name": "card_details",
                "ok": True,
                "data": facts,
            },
        },
        {
            "role": "assistant",
            "content": "Saved research.",
            "research": {
                "conversation_preferences": [
                    {
                        "dimension": "draw_control",
                        "value": "optional",
                        "strength": "preferred",
                        "quote": "Prefer optional draw",
                        "source_turn": "prior",
                    }
                ]
            },
        },
        *[{"role": "tool", "content": "log"} for _ in range(20)],
        {"role": "user", "content": "Compare Elegy Acolyte again."},
    ]
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {
                        "kind": "compare",
                        "named_cards": ["Elegy Acolyte"],
                    },
                    "content": "Its combat draw remains mandatory.",
                }
            )
        ]
    )
    result = run_turn(
        path, owner, deck, messages, gateway=gateway, experiment_service=service
    )
    payload = json.loads(gateway.requests[0].user)
    assert payload["state"]["consulted_cards"][0]["card"]["oracle_text"] == ELEGY
    assert result["research"]["conversation_preferences"][0]["value"] == "optional"
    assert not result["actions"]


def test_budget_refusal_preserves_sources_and_reserves_final_answer(
    prepared, monkeypatch
):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 2)
    gateway = Gateway(
        [
            step(call("card_details", {"card_id": "Elegy Acolyte"})),
            step(call("inspect_deck"), call("search_cards", {"query": "Yawgmoth"})),
            step(
                content="Elegy Acolyte offers combat-dependent draw and token support."
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Assess Elegy Acolyte."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert len([a for a in result["actions"] if a["type"] == "tool_result"]) == 1
    assert gateway.requests[-1].schema.__name__ == "AssistantAnswerStep"
    assert "combat damage" in gateway.requests[-1].user


def test_normalized_name_resolves_unique_teval_spelling(prepared):
    _, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "teval",
        "Teval's Judgment",
        "Whenever one or more cards leave your graveyard, draw a card.",
    )
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    data = tools.execute(call("card_details", {"card_id": "Teval’s Judgement"}))
    assert data["card"]["name"] == "Teval's Judgment"
    assert data["reference_resolution"] == "normalized_name"


def test_functional_tool_uses_session_preferences_without_inventing_mana_ceiling(
    prepared, monkeypatch
):
    _, path, owner, deck, _, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    tools.research_preferences = [
        {"dimension": "card_type", "value": "creature", "strength": "required"},
        {"dimension": "draw_control", "value": "optional", "strength": "preferred"},
        {
            "dimension": "excluded_card",
            "value": "Harvester of Souls",
            "strength": "rejected",
        },
    ]
    received = []
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda path, document, args: received.append(args) or {"cards": []},
    )
    tools.execute(call("search_card_functions", {}))
    assert received[0].type_line == "creature"
    assert received[0].optionality == "preferred"
    assert received[0].mana_max is None
    assert received[0].excluded_names == ["Harvester of Souls"]


def test_retained_cards_refresh_text_and_discard_old_deck_annotations(prepared):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    stale = {
        "card": {
            "id": "elegy",
            "name": "Elegy Acolyte",
            "oracle_text": "Old printed text.",
        },
        "commander_cast_interactions": [
            {
                "commander": "Old Commander",
                "matches_trigger": True,
                "reason": "Stale reason",
            }
        ],
    }
    gateway = Gateway([step(content="I can compare the printed draw conditions.")])
    run_turn(
        path,
        owner,
        deck,
        [
            {
                "role": "tool",
                "action": {
                    "type": "tool_result",
                    "name": "card_details",
                    "ok": True,
                    "data": stale,
                },
            }
        ],
        gateway=gateway,
        experiment_service=service,
    )
    state = json.loads(gateway.requests[0].user)["state"]
    assert state["consulted_cards"][0]["card"]["oracle_text"] == ELEGY
    assert "Old Commander" not in json.dumps(state["consulted_cards"])


@pytest.mark.parametrize(
    "strength,value,expected",
    [("required", "optional", "required"), ("preferred", "any", "any")],
)
def test_optional_draw_preference_strength_and_correction(
    prepared, monkeypatch, strength, value, expected
):
    _, path, owner, deck, _, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    tools.research_preferences = [
        {"dimension": "draw_control", "value": value, "strength": strength}
    ]
    received = []
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda path, document, args: received.append(args) or {"cards": []},
    )
    tools.execute(call("search_card_functions", {}))
    assert received[0].optionality == expected


@pytest.mark.parametrize(
    "prompt,expected",
    [("Find creature draw engines", None), ("Find zero-mana draw cards", 0)],
)
def test_zero_mana_requires_user_intent_but_remains_a_valid_filter(
    prepared, monkeypatch, prompt, expected
):
    _, path, owner, deck, _, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    tools.research_request = prompt
    received = []
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda path, document, args: received.append(args) or {"cards": []},
    )
    result = tools.execute(call("search_card_functions", {"mana_max": 0}))
    assert received[0].mana_max == expected
    assert bool(result["filter_corrections"]) == (expected is None)


def test_historical_long_card_keeps_excerpt_marker_and_full_checker_source(prepared):
    from types import SimpleNamespace

    service, path, owner, deck, _, _ = prepared
    text = (
        ELEGY
        + "\n"
        + "Additional printed text. " * 35
        + "Activate only once each turn."
    )
    seed_card(path, "elegy", "Elegy Acolyte", text)
    checker_requests = []
    checker = SimpleNamespace(
        generate=lambda request: checker_requests.append(request)
        or SimpleNamespace(value={"patches": [], "unresolved": []})
    )
    old_receipt = {
        "role": "tool",
        "action": {
            "type": "tool_result",
            "name": "card_details",
            "ok": True,
            "data": {
                "card": {
                    "id": "elegy",
                    "name": "Elegy Acolyte",
                    "oracle_text": "Old excerpt",
                }
            },
        },
    }
    gateway = Gateway([step(content="Elegy Acolyte has combat draw conditions.")])
    result = run_turn(
        path,
        owner,
        deck,
        [old_receipt, {"role": "user", "content": "Explain Elegy Acolyte."}],
        gateway=gateway,
        answer_review_gateway=checker,
        experiment_service=service,
    )
    state = json.loads(gateway.requests[0].user)["state"]
    assert state["consulted_cards"][0]["card"]["oracle_text_truncated"] is True
    assert any(
        text in source for source in json.loads(checker_requests[0].user)["sources"]
    )
    assert result["status"] == "done"


def test_rich_functional_shortlists_reach_model_context_instead_of_being_dropped():
    from sabermetrics.assistant_runtime import _model_receipt

    cards = []
    for index in range(20):
        cards.append(
            {
                "id": f"card-{index}",
                "oracle_id": f"oracle-{index}",
                "name": f"Candidate {index}",
                "oracle_text": "Whenever another nontoken creature dies, draw a card. "
                * 6,
                "functional_profile": {
                    "draw_routes": [
                        {
                            "kind": "trigger",
                            "draw_control": "mandatory_trigger",
                            "source_quote": "Full source duplicate " * 50,
                            "prerequisites": ["death_event_required"],
                        }
                    ],
                    "deck_support": {
                        "commander_sources": [
                            {"oracle_text": "Repeated commander text " * 60}
                        ]
                        * 3,
                        "relationship": "death_draw_with_recursion_and_sacrifice",
                    },
                },
            }
        )
    receipt = _model_receipt(
        {
            "name": "search_card_functions",
            "data": {"cards": cards, "channel_status": {"semantic": "available"}},
        }
    )
    assert 4 <= len(receipt["data"]["cards"]) <= 12
    assert receipt["data"]["cards"][0]["name"] == "Candidate 0"
    assert (
        receipt["data"]["cards"][0]["draw_routes"][0]["draw_control"]
        == "mandatory_trigger"
    )
    assert len(json.dumps(receipt)) < 12000
    assert receipt["data"]["cards_omitted"] > 0


def test_discovery_keeps_named_lookup_when_remaining_tools_are_fewer_than_batch(
    prepared, monkeypatch
):
    service, path, owner, deck, _, _ = prepared
    seed_card(path, "elegy", "Elegy Acolyte")
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 2)
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda *args: {"status": "available", "cards": []},
    )
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {
                        "kind": "discover",
                        "named_cards": ["Elegy Acolyte"],
                    },
                    "tool_calls": [
                        call("search_card_functions", {"query": "generic draw"}),
                        call("search_card_functions", {"query": "death draw"}),
                    ],
                }
            ),
            step(
                content="Elegy Acolyte is a combat-draw option; discovery remains limited."
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Find draw options including Elegy Acolyte."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert [a["name"] for a in result["actions"] if a["type"] == "tool_started"] == [
        "card_details",
        "search_card_functions",
    ]


def test_empty_functional_result_does_not_count_as_a_completed_list(
    prepared, monkeypatch
):
    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda path, document, args: {
            "status": "available",
            "cards": (
                []
                if args.query == "empty"
                else [
                    {
                        "id": "draw",
                        "name": "Real Draw Candidate",
                        "oracle_text": "Draw a card.",
                    }
                ]
            ),
        },
    )
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {"kind": "discover"},
                    "tool_calls": [call("search_card_functions", {"query": "empty"})],
                }
            ),
            step(call("search_card_functions", {"query": "nonempty"})),
            step(call("inspect_deck", {"query": "remaining evidence"})),
            step(content="Here are the available source-backed options."),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Find two draw lists."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert any(a["name"] == "inspect_deck" for a in result["actions"])
    assert gateway.requests[2].schema.__name__ == "AssistantStep"
