"""Integration regressions for sourced memory, early facts and list completeness."""

import json

import pytest
from pydantic import ValidationError

from sabermetrics import db
from sabermetrics.assistant_runtime import (
    AssistantAnswerStep,
    AssistantStep,
    _encode_context,
    run_turn,
)
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
    request = "Find draw engines and free or cheap sacrifice outlets."
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
                "label": "free or cheap sacrifice outlets",
                "query": "sacrifice outlet",
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
            if call.arguments.function == "sacrifice":
                assert "free or cheap" in call.arguments.query
                assert "sacrifice outlet" in call.arguments.query
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


def test_truncated_synthesis_regenerates_full_json_once_with_retained_sources(prepared):
    from sabermetrics.cedh.errors import ModelValidationError

    service, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "repair-source",
        "Repair Scholar",
        "When this creature enters, draw a card.",
    )
    gateway = Gateway(
        [
            AssistantStep(
                research_task={
                    "kind": "compare",
                    "evidence_scope": "named_cards_only",
                    "current_request_quote": "What about Repair Scholar?",
                    "named_cards": ["Repair Scholar"],
                }
            ),
            ModelValidationError("Invalid JSON: EOF while parsing a string"),
            AssistantStep(
                content="Repair Scholar draws a card when it enters.",
                findings=["Its printed enter trigger is in the retained source."],
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "What about Repair Scholar?"}],
        gateway=gateway,
        schema_repair=True,
        experiment_service=service,
    )
    assert result["status"] == "done", result
    assert len(gateway.requests) == 3
    assert gateway.requests[-1].schema.__name__ == "AssistantAnswerStep"
    assert "Repair Scholar" in gateway.requests[-1].user
    assert any(event["type"] == "model_retry" for event in result["actions"])
    assert (
        result["research"]["runtime"]["model_calls"][1]["outcome"] == "schema_invalid"
    )


@pytest.mark.parametrize("remove_second", [False, True])
def test_discovery_notes_render_complete_card_rows_before_checker(
    prepared, monkeypatch, remove_second
):
    service, path, owner, deck, _, _ = prepared
    notes = [
        "Its printed enter trigger draws a card; this remains an unmeasured candidate.",
        "Its printed death trigger draws a card; retain its full conditions when comparing.",
    ]
    for index in range(2):
        seed_card(
            path,
            f"render-{index}",
            f"Rendered Scholar {index}",
            (
                "When this creature enters, draw a card."
                if index == 0
                else "When this creature dies, draw a card."
            ),
        )
    with db.connect(path) as conn:
        for index, cost in enumerate(["{B}", "{1}{B}"]):
            conn.execute(
                "UPDATE cards SET mana_cost=? WHERE id=?", (cost, f"render-{index}")
            )
        conn.commit()
    answer = AssistantAnswerStep(
        content="Here are two sourced candidates for comparison.",
        findings=[],
        discovery_answer={
            "groups": [
                {
                    "group_id": "list_1",
                    "claimed_count": 2,
                    "card_ids": ["render-0", "render-1"],
                }
            ],
            "notes": [
                {"card_id": f"render-{index}", "note": note}
                for index, note in enumerate(notes)
            ],
        },
    )
    checked = []
    closed = []

    class InjectedReviewer:
        def close(self):
            closed.append(True)

    def check(_gateway, parts, *_args, **_kwargs):
        checked.extend(parts)
        return ([parts[0], parts[1], parts[-1]] if remove_second else parts), {
            "status": "corrected" if remove_second else "no_supported_correction",
            "fulfillment": "complete",
        }

    monkeypatch.setattr("sabermetrics.assistant_runtime.review_answer", check)
    gateway = Gateway(
        [
            step(
                call("card_details", {"card_id": "render-0"}),
                call("card_details", {"card_id": "render-1"}),
            ),
            answer,
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two card recommendations."}],
        gateway=gateway,
        answer_review_gateway=InjectedReviewer(),
        experiment_service=service,
    )
    assert len(checked) == 4
    assert closed == [], "The caller retains ownership of its injected reviewer"
    assert "Rendered Scholar 0 ({B})" in checked[1]
    assert "Rendered Scholar 1 ({1}{B})" in checked[2]
    assert checked[1].endswith(notes[0])
    assert checked[2].endswith(notes[1])
    assert all("\n" not in row for row in checked[1:3])
    if remove_second:
        assert result["status"] == "incomplete", result
        assert not result["research"]["discovery_validation"]["complete"]
    else:
        assert result["status"] == "done", result
        rows = result["presentation"]["reply"]["findings"]
        assert len(rows) == 2
        assert rows == checked[1:3]


@pytest.mark.parametrize(
    "manifest",
    [None, {"groups": []}, {"groups": [{"group_id": "list_1", "card_ids": []}]}],
)
def test_empty_findings_require_selected_discovery_candidates(manifest):
    with pytest.raises(
        ValidationError, match="findings or selected discovery candidates"
    ):
        AssistantAnswerStep(
            content="This is a complete nonblank lead.",
            findings=[],
            discovery_answer=manifest,
        )


def test_owned_review_has_separate_bounded_budget_and_is_closed(prepared, monkeypatch):
    from sabermetrics.cedh.model_gateway import ReasoningMode

    service, path, owner, deck, _, _ = prepared
    settings_seen, closed = [], []

    def build(settings, **kwargs):
        index = len(settings_seen)
        settings_seen.append(settings.model)
        gateway = Gateway([step(content="The current facts support this answer.")])
        if index:
            from types import SimpleNamespace

            gateway.generate = lambda request: SimpleNamespace(
                value={"fulfillment": "complete", "patches": [], "unresolved": []}
            )
        gateway.close = lambda: closed.append(index)
        return gateway

    monkeypatch.setattr("sabermetrics.assistant_runtime.build_gateway", build)
    result = run_turn(
        path,
        owner,
        deck,
        [],
        experiment_service=service,
        request_planner=False,
        direct_routes=False,
    )
    assert result["status"] == "done"
    assert len(settings_seen) == 2
    main, review = settings_seen
    assert main.mode_profile(ReasoningMode.EXPLAIN).max_output_tokens == 6144
    assert main.max_retries == 2 and main.timeout_seconds == 60
    assert review.mode_profile(ReasoningMode.EXPLAIN).max_output_tokens == 3072
    assert review.max_retries == 0 and review.timeout_seconds == 45
    assert sorted(closed) == [0, 1]


def test_provider_failure_preserves_retrieved_facts_without_claiming_analysis(prepared):
    from sabermetrics.cedh.errors import ModelProviderError

    service, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "retained-source",
        "Retained Scholar",
        "When this creature enters, draw a card.",
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Assess Retained Scholar."}],
        gateway=Gateway(
            [
                step(call("card_details", {"card_id": "retained-source"})),
                ModelProviderError("provider returned 503: private detail"),
            ]
        ),
        experiment_service=service,
    )
    assert result["status"] == "provider_error" and result["reason"] == "provider_busy"
    assert "Retrieved source excerpt" in result["content"]
    assert "When this creature enters, draw a card." in result["content"]
    assert "requested analysis is incomplete" in result["content"]
    assert "private detail" not in result["content"]


def test_one_planned_list_synthesizes_once_with_complete_current_candidate_facts(
    prepared, monkeypatch
):
    from sabermetrics.assistant_research_task import DiscoveryListPlan
    from sabermetrics.assistant_tools import AssistantTools
    from tests.test_assistant_request_planner import plan

    service, path, owner, deck, _, _ = prepared
    request = "Find three repeatable draw creatures."
    planned = plan(request, scope="candidate_discovery")
    planned.research_task.discovery_lists = [
        DiscoveryListPlan(
            label="Repeatable draw creatures",
            query="repeatable creature card draw",
            request_quote=request,
            requested_count=3,
            function="draw",
        )
    ]
    oracle = (
        "{2}, {T}: Draw a card. "
        + "Printed context. " * 12
        + "Activate only during your turn."
    )
    cards = [
        {
            "id": f"draw-{i}",
            "name": f"Draw Scholar {i}",
            "mana_cost": "{2}{U}",
            "type_line": "Creature — Wizard",
            "oracle_text": oracle,
        }
        for i in range(8)
    ]
    original = AssistantTools.execute

    def execute(self, call):
        if call.name == "search_card_functions":
            return {"status": "available", "cards": cards}
        return original(self, call)

    monkeypatch.setattr(AssistantTools, "execute", execute)
    gateway = Gateway(
        [
            planned,
            AssistantStep(
                content="These three creatures offer activated draw with timing costs.",
                discovery_answer={
                    "groups": [
                        {
                            "group_id": "list_1",
                            "claimed_count": 3,
                            "card_ids": [c["id"] for c in cards[:3]],
                        }
                    ],
                    "notes": [
                        {
                            "card_id": c["id"],
                            "note": "Draw costs {2} and tapping, and activates only during your turn.",
                        }
                        for c in cards[:3]
                    ],
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
    assert result["status"] == "done"
    assert len(gateway.requests) == 2
    assert gateway.requests[-1].schema.__name__ == "AssistantAnswerStep"
    evidence = json.loads(gateway.requests[-1].user)["state"]["session_evidence"]
    supplied = {c["card_id"]: c for c in evidence["discovery_candidates"]}
    assert evidence["priority_bundles_retained"] == 8
    assert evidence["priority_bundle_shortfall"] is False
    consulted = {
        row["card"]["id"]: row["card"]
        for row in json.loads(gateway.requests[-1].user)["state"].get(
            "consulted_cards", []
        )
    }
    for card in cards:
        fields = supplied[card["id"]].get("printed_fields", {})
        shared = consulted.get(card["id"], {})
        for kind in ("oracle_text", "type_line"):
            if kind not in fields:
                assert kind in supplied[card["id"]]["fields_in_consulted_cards"]
        assert fields.get("oracle_text", shared.get("oracle_text")) == oracle
        assert fields.get("type_line", shared.get("type_line")) == card["type_line"]
        assert "oracle_text" not in supplied[card["id"]].get("excerpted_fields", [])


def test_empty_prior_selection_groups_fall_back_to_retained_source_facts(prepared):
    from sabermetrics.assistant_discovery_contract import (
        DiscoveryLedger,
        candidate_from_card,
    )
    from sabermetrics.assistant_session_state import (
        SessionState,
        session_state_research,
        update_session_state,
    )

    service, path, owner, deck, _, _ = prepared
    oracle = "{2}, {T}: Draw a card. Activate only during your turn."
    candidate = candidate_from_card(
        {
            "id": "retained",
            "name": "Retained Scholar",
            "oracle_text": oracle,
            "type_line": "Creature",
            "mana_cost": "{U}",
        },
        rank=1,
        in_deck=False,
    )
    state = update_session_state(
        SessionState(),
        ledger=DiscoveryLedger(candidates=[candidate]),
        evidence={
            "card:retained": json.dumps(
                {
                    "id": "retained",
                    "name": "Retained Scholar",
                    "oracle_text": oracle,
                    "type_line": "Creature",
                    "mana_cost": "{U}",
                }
            )
        },
        revision=0,
    )
    research = session_state_research(state)
    research["selected_candidates"] = {
        "groups": [{"group_id": "list_1", "card_ids": []}],
        "claims": [],
        "notes": [],
    }
    gateway = Gateway(
        [
            AssistantStep(
                content="Retained Scholar remains a sourced draw candidate.",
                findings=["Its activation has a timing restriction."],
            )
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            {
                "role": "assistant",
                "content": "An earlier list was incomplete.",
                "research": research,
            },
            {"role": "user", "content": "Summarize our findings."},
        ],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    view = json.loads(gateway.requests[0].user)["state"]["session_evidence"]
    assert view["priority_bundles_retained"] == 1
    assert view["discovery_candidates"][0]["printed_fields"]["oracle_text"] == oracle


def test_owned_bounded_synthesis_uses_smaller_profile_and_closes_clients(
    prepared, monkeypatch
):
    from types import SimpleNamespace

    from sabermetrics.cedh.model_gateway import ReasoningMode

    service, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "summary-source",
        "Retained Scholar",
        "When this creature enters, draw a card.",
    )
    created, closed = {}, []

    def build(settings, **kwargs):
        cap = settings.model.mode_profile(ReasoningMode.EXPLAIN).max_output_tokens
        if cap == 3072:
            client = SimpleNamespace(
                generate=lambda request: SimpleNamespace(
                    value={"fulfillment": "complete", "patches": [], "unresolved": []}
                )
            )
        else:
            client = Gateway(
                [
                    AssistantStep(
                        content="Retained Scholar draws a card when it enters.",
                        findings=["The printed trigger requires it to enter."],
                    )
                ]
            )
        client.close = lambda: closed.append(cap)
        created[cap] = client
        return client

    monkeypatch.setattr("sabermetrics.assistant_runtime.build_gateway", build)
    result = run_turn(
        path,
        owner,
        deck,
        [
            {
                "role": "assistant",
                "content": "Retained Scholar was retrieved.",
                "research": {"source_card_ids": ["summary-source"]},
            },
            {"role": "user", "content": "Tell me about Retained Scholar."},
        ],
        direct_routes=True,
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert not created[6144].requests
    assert len(created[4096].requests) == 1
    assert created[4096].requests[0].max_output_tokens == 4096
    assert sorted(closed) == [3072, 4096, 6144]
    assert (
        result["research"]["runtime"]["model_calls"][0]["output_token_budget"] == 4096
    )
