"""Conversational orchestration exercises real scoped tools with a mock model."""

import json
from types import SimpleNamespace

import pytest

from sabermetrics import db
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.assistant_swap_review import SwapReviewResponse
from sabermetrics.assistant_tools import TOOL_ADAPTER, AssistantTools
from sabermetrics.cedh.errors import ModelConfigurationError
from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound

pytest_plugins = ["tests.test_assistant_experiments"]


class Gateway:
    provider = "test"
    model_id = "typed-mock-model"

    def __init__(self, steps):
        self.steps = iter(steps)
        self.requests = []
        self.review_requests = []

    def generate(self, request):
        if request.schema is SwapReviewResponse:
            self.review_requests.append(request)
            return SimpleNamespace(value=_accepted_review(request))
        self.requests.append(request)
        value = next(self.steps)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(value=value)


def _accepted_review(request):
    pairs = json.loads(request.user)["candidates"]
    return SwapReviewResponse.model_validate(
        {
            "decisions": [
                {
                    "candidate_index": pair["candidate_index"],
                    "decision": "accept",
                    "theme_fit": "fit",
                    "power_fit": "fit",
                    "reason": "Suitable for an isolated mock experiment.",
                    "benefit": "Adds the supplied modeled artifact function.",
                    "tradeoffs": [
                        "Loses the outgoing card function; performance remains unmeasured."
                    ],
                    "citations": [
                        {
                            "side": side,
                            "source_field": (
                                "oracle_text"
                                if pair[side].get("oracle_text")
                                else "type_line"
                            ),
                            "quote": pair[side].get("oracle_text")
                            or pair[side]["type_line"],
                        }
                        for side in ("incoming", "outgoing")
                    ],
                }
                for pair in pairs
            ]
        }
    )


@pytest.fixture(autouse=True)
def complete_review_sources(request):
    if "prepared" not in request.fixturenames:
        return
    _service, path, _owner, _deck, _document, _alternatives = request.getfixturevalue(
        "prepared"
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET oracle_text='{T}: Draw a card.', mana_cost='{2}', cmc=2 WHERE id LIKE 'incoming-%'"
        )
        conn.commit()


def call(name, arguments=None):
    return {"name": name, "arguments": arguments or {}}


def test_topic_inspection_returns_real_entry_handles_with_explicit_fallback(prepared):
    _, path, owner, deck, document, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    result = tools.execute(
        call("inspect_deck", {"query": "sacrifice outlet and high-impact slots"})
    )
    assert result["literal_name_matches"] == 0
    assert result["query_match_mode"] in {"function_ranked", "library_fallback"}
    assert result["entries"]
    assert {e["id"] for e in result["entries"]} <= {
        e["id"] for e in document["entries"]
    }


def test_exact_name_survives_a_wrong_oracle_filter_without_weakening_other_constraints(
    prepared,
):
    _, path, owner, deck, _, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    result = tools.execute(
        call(
            "search_cards",
            {
                "query": "Incoming 0",
                "oracle_text": "A remembered clause that is not printed.",
            },
        )
    )
    assert result["oracle_text_filter_mismatch"]
    assert [c["name"] for c in result["cards"]] == ["Incoming 0"]
    assert not result["cards"][0]["already_in_library"]
    assert (
        result["cards"][0]["oracle_text"] != "A remembered clause that is not printed."
    )
    constrained = tools.execute(
        call(
            "search_cards",
            {"query": "Incoming 0", "oracle_text": "wrong", "type_line": "Sorcery"},
        )
    )
    assert constrained["cards"] == []
    fuzzy = tools.execute(
        call("search_cards", {"query": "Incoming", "oracle_text": "wrong"})
    )
    assert fuzzy["cards"] == []


def test_inspected_handles_and_catalog_facts_survive_later_reads(prepared):
    service, path, owner, deck, _, _ = prepared

    gateway = Gateway(
        [
            step(
                call("inspect_deck", {"query": "topic phrase without literal names"}),
                call("search_cards", {"query": "Incoming 0"}),
            ),
            step(call("retrieve_deck_facts", {"query": "draw"})),
            step(content="Source-backed research, unmeasured."),
        ]
    )
    result = run_turn(
        path, owner, deck, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "done"
    observed = json.loads(gateway.requests[-1].user)["state"]
    assert observed["deck_entry_handles"]
    assert any(c["card"]["name"] == "Incoming 0" for c in observed["consulted_cards"])


def test_search_annotations_never_modify_shared_catalog_rows(prepared, monkeypatch):
    _, path, owner, deck, _, _ = prepared
    shared = {
        "id": "incoming-0",
        "oracle_id": "incoming-oracle-0",
        "name": "Incoming 0",
    }
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.search_cards", lambda *args, **kwargs: [shared]
    )
    result = AssistantTools(path, owner, deck, "suggest", {}, lambda: False).execute(
        call("search_cards", {"query": "Incoming 0"})
    )
    assert result["cards"][0] is not shared
    assert "current_entry_ids" not in shared and "already_in_library" not in shared


def step(*calls, content=""):
    return AssistantStep.model_validate({"content": content, "tool_calls": list(calls)})


def test_package_transport_matches_existing_experiment_and_joint_review_bounds():
    from pydantic import ValidationError

    proposal = {
        "name": "Joint package",
        "rationale": "Review the complete two-card transition.",
        "replacements": [
            {"outgoing_entry_id": f"old-{n}", "incoming_card_id": f"new-{n}"}
            for n in range(2)
        ],
    }
    typed = TOOL_ADAPTER.validate_python(
        call("propose_replacements", {"proposal": proposal})
    )
    assert len(typed.arguments.proposal.replacements) == 2
    proposal["replacements"] *= 2
    with pytest.raises(ValidationError):
        TOOL_ADAPTER.validate_python(
            call("propose_replacements", {"proposal": proposal})
        )


def test_last_model_call_is_reserved_for_answer_and_does_not_execute_tools(prepared):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    gateway = Gateway(
        [step(call("inspect_deck", {"query": f"topic {i}"})) for i in range(5)]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [
            {
                "role": "user",
                "content": "Compare the actual deck functions; do not simulate.",
            }
        ],
        mode="suggest",
        constraints={},
        gateway=gateway,
    )
    assert len(gateway.requests) == 5
    assert "Return a final answer" in gateway.requests[-1].system
    assert len([a for a in result["actions"] if a.get("type") == "tool_started"]) == 4
    assert result["status"] == "budget_exhausted"
    assert "analysis is incomplete" in result["content"]
    assert "did not start an experiment" not in result["content"]


def test_complete_deck_evidence_tool_is_owner_scoped_and_revision_checked(prepared):
    _service, path, owner, deck_id, document, _alternatives = prepared
    tools = AssistantTools(path, owner, deck_id, "suggest", {}, lambda: False)
    facts = tools.execute(call("retrieve_deck_facts", {"query": "draw"}))
    assert facts["version"] == "assistant-deck-facts.v1"
    assert facts["library_entries_scanned"] > 0
    assert facts["revision"] == document["revision"]
    rules = tools.execute(call("retrieve_rules", {"query": "tap ability"}))
    assert rules["status"] == "unavailable"
    with pytest.raises(DeckNotFound):
        AssistantTools(path, "different-owner", deck_id, "suggest", {}, lambda: False)


def test_multi_turn_search_inspect_propose_is_validated_and_source_unchanged(prepared):
    service, path, owner, deck_id, document, alternatives = prepared
    gateway = Gateway(
        [
            step(call("search_cards", {"query": "Incoming"})),
            step(call("inspect_deck", {"query": "Island"})),
            step(call("propose_replacements", {"proposal": alternatives[0]})),
            step(
                content="This is a preview of one swap. Its performance is unmeasured."
            ),
        ]
    )
    events = []
    result = run_turn(
        path,
        owner,
        deck_id,
        [
            {"role": "user", "content": "Keep the blue commander."},
            {"role": "assistant", "content": "We can inspect its mana."},
            {"role": "user", "content": "Find a colorless replacement and preview it."},
        ],
        gateway=gateway,
        experiment_service=service,
        on_event=events.append,
    )
    assert result["status"] == "done"
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["expected_revision"] == document["revision"]
    assert [event["name"] for event in events if event["type"] == "tool_result"] == [
        "search_cards",
        "inspect_deck",
        "propose_replacements",
    ]
    assert all(event["ok"] for event in events if event["type"] == "tool_result")
    assert DeckDocumentRepo(path).get(owner, deck_id) == document
    assert "Keep the blue commander" in gateway.requests[0].user
    assert '"name": "search_cards"' in gateway.requests[1].user
    assert "untrusted data" in gateway.requests[0].system
    assert gateway.requests[0].call_type == "build:assistant"
    assert gateway.requests[0].max_output_tokens == 4096
    assert "requested facts first" in gateway.requests[0].system
    assert "under 300 words" in gateway.requests[0].system


def test_automate_creates_exactly_three_isolated_copies_and_no_adoption(
    prepared, monkeypatch
):
    service, path, owner, deck_id, document, alternatives = prepared
    executed = []

    def execute(owner_id, experiment_id, should_stop):
        assert not should_stop()
        executed.append(experiment_id)

    monkeypatch.setattr(service, "execute", execute)
    gateway = Gateway(
        [
            step(call("start_experiment", {"alternatives": alternatives})),
            step(
                content="The three isolated alternatives were queued. None was adopted."
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Compare three options."}],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    receipt = next(
        event for event in result["actions"] if event["type"] == "tool_result"
    )
    assert receipt["ok"]
    assert receipt["data"]["status"] == "queued"
    assert len(receipt["data"]["alternatives"]) == 3
    assert len(executed) == 1
    assert DeckDocumentRepo(path).get(owner, deck_id) == document


def test_suggest_rejects_automatic_experiment_and_invalid_card_id(prepared):
    service, path, owner, deck_id, document, alternatives = prepared
    bad = {
        **alternatives[0],
        "replacements": [
            {
                "outgoing_entry_id": alternatives[0]["replacements"][0][
                    "outgoing_entry_id"
                ],
                "incoming_card_id": "invented",
            }
        ],
    }
    gateway = Gateway(
        [
            step(
                call("start_experiment", {"alternatives": alternatives}),
                call("propose_replacements", {"proposal": bad}),
            ),
            step(content="Those requested actions were rejected."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert not result["proposals"]
    receipts = [event for event in result["actions"] if event["type"] == "tool_result"]
    assert len(receipts) == 2 and all(not event["ok"] for event in receipts)
    assert service.list_for_deck(owner, deck_id) == []
    assert DeckDocumentRepo(path).get(owner, deck_id) == document


def test_out_of_scope_tool_and_excess_swaps_fail_typed_boundary():
    with pytest.raises(ValueError):
        TOOL_ADAPTER.validate_python(call("adopt", {"alternative_id": "a"}))
    with pytest.raises(ValueError):
        TOOL_ADAPTER.validate_python(
            call(
                "propose_replacements",
                {
                    "proposal": {
                        "name": "Four",
                        "rationale": "Too many",
                        "replacements": [
                            {"outgoing_entry_id": str(i), "incoming_card_id": str(i)}
                            for i in range(4)
                        ],
                    }
                },
            )
        )
    with pytest.raises(ValueError):
        TOOL_ADAPTER.validate_python(call("search_cards", {"limit": True}))


def test_mode_switch_stops_before_tool_mutation(prepared):
    service, path, owner, deck_id, document, alternatives = prepared
    gateway = Gateway([step(call("start_experiment", {"alternatives": alternatives}))])
    result = run_turn(
        path,
        owner,
        deck_id,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
        should_stop=lambda: bool(gateway.requests),
    )
    assert result["status"] == "stopped"
    assert not result["actions"]
    assert service.list_for_deck(owner, deck_id) == []
    assert DeckDocumentRepo(path).get(owner, deck_id) == document


def test_provider_failure_keeps_only_actual_receipts_and_no_fake_answer(prepared):
    service, path, owner, deck_id, document, _alternatives = prepared
    gateway = Gateway([step(call("inspect_deck")), RuntimeError("transport failed")])
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "provider_error"
    assert (
        len([event for event in result["actions"] if event["type"] == "tool_result"])
        == 1
    )
    assert result["proposals"] == []
    assert "could not complete" in result["content"]
    assert DeckDocumentRepo(path).get(owner, deck_id) == document


def test_missing_provider_is_explicit_without_fallback(prepared, monkeypatch):
    service, path, owner, deck_id, _document, _alternatives = prepared

    def missing(*args, **kwargs):
        raise ModelConfigurationError("No configured credential")

    monkeypatch.setattr("sabermetrics.assistant_runtime.build_gateway", missing)
    result = run_turn(path, owner, deck_id, [], experiment_service=service)
    assert result["status"] == "provider_unavailable"
    assert not result["actions"] and not result["proposals"]


def test_tool_budget_prevents_remaining_calls(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    gateway = Gateway(
        [
            *[step(*[call("latest_goldfish") for _ in range(3)]) for _ in range(3)],
            step(content="The recorded findings remain available."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "done"
    assert gateway.requests[-1].schema.__name__ == "AssistantAnswerStep"
    assert (
        len([event for event in result["actions"] if event["type"] == "tool_result"])
        == 6
    )


def test_second_experiment_write_is_rejected(prepared, monkeypatch):
    service, path, owner, deck_id, _document, alternatives = prepared
    monkeypatch.setattr(service, "execute", lambda *_args: None)
    gateway = Gateway(
        [
            step(call("start_experiment", {"alternatives": alternatives})),
            step(call("start_experiment", {"alternatives": alternatives})),
            step(content="Only one experiment was started."),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    receipts = [event for event in result["actions"] if event["type"] == "tool_result"]
    assert receipts[0]["ok"] and not receipts[1]["ok"]
    assert len(service.list_for_deck(owner, deck_id)) == 1


def test_owner_scope_and_revision_are_checked_before_model(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    from sabermetrics.deck_documents import DeckNotFound, RevisionConflict

    with pytest.raises(DeckNotFound):
        AssistantTools(
            path,
            "another-owner",
            deck_id,
            "suggest",
            {},
            lambda: False,
            experiment_service=service,
        )
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_documents SET revision=revision+1 WHERE id=?", (deck_id,)
        )
        conn.commit()
    with pytest.raises(RevisionConflict):
        tools.execute(call("inspect_deck"))


def test_long_chat_keeps_latest_user_instruction(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Old turn {i}"}
        for i in range(24)
    ]
    messages.append(
        {
            "role": "user",
            "content": "LATEST: keep my protected commander and explain only.",
        }
    )
    gateway = Gateway([step(content="I will explain.")])
    result = run_turn(
        path, owner, deck_id, messages, gateway=gateway, experiment_service=service
    )
    assert result["status"] == "done"
    assert "LATEST: keep my protected commander" in gateway.requests[0].user
    assert "Old turn 0" not in gateway.requests[0].user


def test_actual_experiment_values_survive_compact_model_context(prepared, monkeypatch):
    service, path, owner, deck_id, _document, alternatives = prepared
    from tests.test_assistant_experiments import report

    def execute(owner_id, experiment_id, should_stop):
        payload = service.get(owner_id, experiment_id)
        payload["status"] = "done"
        payload["baseline"]["training_report"] = report(1, 0.5)
        payload["baseline"]["validation_report"] = report(2, 0.45)
        for alternative in payload["alternatives"]:
            alternative["status"] = "done"
            alternative["training_report"] = report(1, 0.75)
            alternative["validation_report"] = report(2, 0.7)
            alternative["comparison"] = {
                "comparable": True,
                "metric_deltas": {
                    "opening": {
                        "kept": {
                            "before": 0.5,
                            "after": 0.75,
                            "delta": 0.25,
                            "comparable": True,
                        }
                    }
                },
            }
            alternative["validation"] = {"status": "descriptive"}
        service._save(owner_id, payload)

    monkeypatch.setattr(service, "execute", execute)
    gateway = Gateway(
        [
            step(call("start_experiment", {"alternatives": alternatives})),
            step(content="Differences remain descriptive."),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    context = json.loads(gateway.requests[1].user)
    receipt = next(
        item["result"] for item in context["conversation"] if item.get("role") == "tool"
    )
    metric = receipt["data"]["metric_summary"][0]
    assert metric["before"] == 0.5
    assert metric["after"] == 0.75
    assert metric["delta"] == 0.25
    assert metric["validation_before"] == 0.45
    assert metric["validation_after"] == 0.7


def test_failed_experiment_is_not_a_successful_tool_receipt(prepared, monkeypatch):
    service, path, owner, deck_id, _document, alternatives = prepared

    def execute(owner_id, experiment_id, should_stop):
        payload = service.get(owner_id, experiment_id)
        payload["status"] = "failed"
        payload["error"] = "Native diagnostics unavailable"
        service._save(owner_id, payload)

    monkeypatch.setattr(service, "execute", execute)
    gateway = Gateway(
        [
            step(call("start_experiment", {"alternatives": alternatives})),
            step(content="The experiment failed; no results were measured."),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    receipt = next(item for item in result["actions"] if item["type"] == "tool_result")
    assert receipt["ok"] is False
    assert receipt["status"] == "failed"
    assert receipt["data"]["error"] == "Native diagnostics unavailable"


def test_generator_pool_returns_only_resolved_real_catalog_cards(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,color_identity,is_legal_in_99) VALUES(?,?,?,?,?,?)",
            (
                "generator-card",
                "generator-oracle",
                "Arcane Signet",
                "Artifact",
                "[]",
                1,
            ),
        )
        conn.commit()
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    result = tools.execute(call("generator_intelligence"))
    assert any(
        item["id"] == "generator-card" and item["generator_role"] == "acceleration"
        for item in result["candidate_pool"]
    )
    assert not any(item["name"] == "Sol Ring" for item in result["candidate_pool"])
    assert result["role_targets"]["land"] == 37


def test_latest_goldfish_values_and_unknowns_survive_model_context(
    prepared, monkeypatch
):
    service, path, owner, deck_id, _document, _alternatives = prepared
    view = {
        "status": "done",
        "stale": False,
        "result": {
            "diagnostics": {
                "tests": {
                    "development": {
                        "status": "partial",
                        "limitations": ["Missing mechanics"],
                        "metrics": {
                            "engine_online_rate": {
                                "value": 0.3185,
                                "unit": "proportion",
                                "denominator": 2000,
                            }
                        },
                    },
                    "finish": {
                        "status": "unsupported",
                        "metrics": {
                            "verified_finish_rate": {
                                "value": None,
                                "unit": "proportion",
                            }
                        },
                    },
                }
            }
        },
    }
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.simulation_view", lambda *_args: view
    )
    gateway = Gateway(
        [
            step(call("latest_goldfish")),
            step(content="Engine events are partially measured; finish is unknown."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "done"
    context = json.loads(gateway.requests[1].user)
    receipt = next(
        item["result"] for item in context["conversation"] if item.get("role") == "tool"
    )
    observations = receipt["data"]["observations"]
    assert observations[0]["value"] == 0.3185
    assert observations[1]["value"] is None


def test_large_experiment_reports_keep_all_five_tests_for_three_alternatives(
    prepared, monkeypatch
):
    service, path, owner, deck_id, _document, alternatives = prepared

    def report(value):
        tests = {}
        for name in (
            "opening",
            "development",
            "commander_loss",
            "creature_wipe",
            "finish",
        ):
            tests[name] = {
                "status": "partial",
                "metrics": {
                    f"metric-{i}": {
                        "value": value,
                        "unit": "proportion",
                        "denominator": 2000,
                    }
                    for i in range(40)
                },
                "limitations": ["Missing mechanics " * 50] * 10,
            }
        return {
            "tests": tests,
            "coverage": {
                "modeled_cards": 38,
                "inert_cards": 16,
                "unauthored_cards": 46,
            },
        }

    def execute(owner_id, experiment_id, should_stop):
        payload = service.get(owner_id, experiment_id)
        payload["status"] = "done"
        payload["baseline"]["training_report"] = report(0.25)
        payload["baseline"]["validation_report"] = report(0.2)
        for alternative in payload["alternatives"]:
            alternative["status"] = "done"
            alternative["training_report"] = report(0.5)
            alternative["validation_report"] = report(0.45)
            alternative["comparison"] = {
                "comparable": False,
                "reason": "Coverage changed",
                "metric_deltas": {
                    test: {
                        f"metric-{i}": {
                            "before": 0.25,
                            "after": 0.5,
                            "delta": None,
                            "reason": "Coverage changed " * 20,
                        }
                        for i in range(40)
                    }
                    for test in report(0.5)["tests"]
                },
            }
            alternative["validation"] = {
                "status": "descriptive",
                "comparison": alternative["comparison"],
            }
        service._save(owner_id, payload)

    monkeypatch.setattr(service, "execute", execute)
    gateway = Gateway(
        [
            step(call("start_experiment", {"alternatives": alternatives})),
            step(
                content="Coverage changed; these observations do not establish improvement."
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert len(gateway.requests[1].user) < 16000
    context = json.loads(gateway.requests[1].user)
    data = next(
        item["result"]["data"]
        for item in context["conversation"]
        if item.get("role") == "tool"
    )
    rows = data["metric_summary"]
    assert len(rows) == 15
    assert len({row["alternative_id"] for row in rows}) == 3
    assert {row["test"] for row in rows} == {
        "opening",
        "development",
        "commander_loss",
        "creature_wipe",
        "finish",
    }
    assert all(
        row["before"] == 0.25
        and row["after"] == 0.5
        and row["validation_after"] == 0.45
        for row in rows
    )
    assert all(
        "comparison" not in alternative and "validation" not in alternative
        for alternative in data["alternatives"]
    )
    assert "remaining metrics" in data["summary_note"]


def test_goldfish_uses_configured_objective_turn(prepared, monkeypatch):
    service, path, owner, deck_id, document, _alternatives = prepared
    recorded = {}
    monkeypatch.setattr(
        "sabermetrics.cedh.settings.load_cedh_settings",
        lambda: SimpleNamespace(
            simulator=SimpleNamespace(
                mode="http",
                url="http://test.invalid",
                timeout_seconds=20,
                objective_turn=4,
            )
        ),
    )

    class Client:
        def __init__(self, url, **kwargs):
            recorded.update(kwargs)
            raise RuntimeError("Stop before network")

    monkeypatch.setattr("sabermetrics.cedh.simulator.HttpSimulatorClient", Client)
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    with pytest.raises(RuntimeError, match="Stop before network"):
        tools._run_goldfish(document)
    assert recorded["objective_turn"] == 4


def test_recovery_expires_only_old_marked_assistant_goldfish(prepared):
    from datetime import UTC, datetime, timedelta

    from sabermetrics.assistant_tools import recover_interrupted_goldfish

    _service, path, owner, deck_id, _document, _alternatives = prepared
    now = datetime.now(UTC)
    repo = db.DeckDocumentSimulationsRepo(path)

    def insert(age, marked=True, status="running"):
        payload = {
            "snapshot": {"real_evidence": "retained"},
            "provenance": {"source": "fixture"},
        }
        if marked:
            payload["assistant_run"] = {
                "schema_version": "decklab-assistant-goldfish.v1",
                "started_at": (now - timedelta(minutes=age)).isoformat(),
            }
        simulation = repo.insert(
            deck_id=deck_id, owner_id=owner, result_json=json.dumps(payload)
        )
        repo.update(simulation, status=status)
        return simulation

    old = insert(16)
    fresh = insert(14)
    unrelated = insert(30, marked=False)
    completed = insert(30, status="done")
    assert recover_interrupted_goldfish(path, owner, deck_id, now) == 1
    row = repo.get(old)
    assert row["status"] == "not_simulated"
    frozen = json.loads(row["result_json"])
    assert frozen["snapshot"]["real_evidence"] == "retained"
    assert frozen["provenance"]["source"] == "fixture"
    assert frozen["assistant_run"]["status"] == "interrupted"
    assert "interrupted" in row["reason"]
    assert repo.get(fresh)["status"] == "running"
    assert repo.get(unrelated)["status"] == "running"
    assert repo.get(completed)["status"] == "done"
    assert recover_interrupted_goldfish(path, owner, deck_id, now) == 0


@pytest.mark.parametrize("eventually_succeeds", [True, False])
def test_transient_provider_overload_has_three_bounded_attempts(
    prepared, monkeypatch, eventually_succeeds
):
    import httpx

    from sabermetrics.cedh.provider_deepseek import DeepSeekGateway

    service, path, owner, deck_id, _document, _alternatives = prepared
    attempts = []
    sleeps = []
    clients = []
    monkeypatch.setenv("HF_TOKEN", "test-only-dummy-token")
    monkeypatch.setattr("sabermetrics.cedh.provider_deepseek.time.sleep", sleeps.append)

    def handler(request):
        attempts.append(request)
        assert json.loads(request.content)["max_tokens"] == 4096
        if eventually_succeeds and len(attempts) == 3:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "content": "The typed provider response succeeded.",
                                        "research_task": {"additions": 0, "cuts": 0},
                                        "tool_calls": [],
                                    }
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 15},
                },
            )
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "engine_overloaded",
                    "message": "sensitive-provider-detail-token; Model busy, retry later",
                }
            },
        )

    def build(settings, **kwargs):
        assert settings.model.max_retries == 2
        assert settings.model.max_validation_retries == 0
        assert settings.model.timeout_seconds == 60
        from sabermetrics.cedh.model_gateway import ReasoningMode

        assert (
            settings.model.mode_profile(ReasoningMode.EXPLAIN).max_output_tokens == 4096
        )
        client = httpx.Client(
            base_url="https://mock.invalid", transport=httpx.MockTransport(handler)
        )
        clients.append(client)
        return DeepSeekGateway(settings, client=client, **kwargs)

    monkeypatch.setattr("sabermetrics.assistant_runtime.build_gateway", build)
    try:
        result = run_turn(
            path,
            owner,
            deck_id,
            [],
            experiment_service=service,
            answer_review_gateway=SimpleNamespace(
                generate=lambda request: SimpleNamespace(
                    value={"patches": [], "unresolved": []}
                )
            ),
        )
    finally:
        for client in clients:
            client.close()
    assert len(attempts) == 3
    assert sleeps == [1, 2]
    if eventually_succeeds:
        assert result["status"] == "done"
        assert result["content"] == "The typed provider response succeeded."
    else:
        assert result["status"] == "provider_error"
        assert result["reason"] == "provider_busy"
        assert "retry your message" in result["content"]
        assert "sensitive-provider-detail-token" not in json.dumps(result)
        assert not result["actions"] and not result["proposals"]


def test_inspection_counts_and_commanders_are_global_despite_query_pagination(
    prepared, monkeypatch
):
    import copy

    service, path, owner, deck_id, document, _alternatives = prepared
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    changed = copy.deepcopy(document)
    land = next(entry for entry in changed["entries"] if not entry["is_commander"])
    land["quantity"] = 96
    changed["entries"].extend(
        [
            {
                **land,
                "id": "spell-land",
                "card_id": "modal",
                "name": "Hydroelectric Specimen",
                "quantity": 2,
                "type_line": "Instant // Land",
            },
            {
                **land,
                "id": "transform-land",
                "card_id": "transform",
                "name": "Growing Rites",
                "quantity": 1,
                "type_line": "Enchantment // Land",
            },
        ]
    )
    monkeypatch.setattr(tools.repo, "get", lambda *_args: changed)
    result = tools.execute(call("inspect_deck", {"query": "Hydroelectric", "limit": 1}))
    assert len(result["entries"]) == 1
    assert result["front_land_count"] == 96
    assert result["land_back_face_count"] == 3
    assert result["commanders"] == [
        {key: entry.get(key) for key in ("name", "card_id", "oracle_id")}
        for entry in document["entries"]
        if entry["is_commander"]
    ]
    assert "not establish" in result["land_count_note"]
    assert "mdfc_land_option_count" not in result


def test_oversized_goldfish_receipt_continues_without_losing_measurements(
    prepared, monkeypatch
):
    service, path, owner, deck_id, _document, _alternatives = prepared
    full = {
        "view": {
            "trace": ["raw simulation detail " * 70 for _ in range(32)],
            "revision": -1,
            "deck_sha256": "measured-old-deck",
            "reason": None,
        },
        "observations": [
            {"test": "opening", "metric": "qualified_opening_rate", "value": 0.625},
            {"test": "finish", "metric": "verified_finish_rate", "value": None},
        ],
        "coverage": {"unauthored_cards": 75},
        "test_statuses": [
            {
                "test": name,
                "status": "partial",
                "limitations": ["unsupported mechanics " * 70],
            }
            for name in [
                "opening",
                "development",
                "commander_loss",
                "creature_wipe",
                "finish",
            ]
        ],
        "stale": True,
    }
    original = AssistantTools.execute

    def execute(self, tool):
        return full if tool.name == "latest_goldfish" else original(self, tool)

    monkeypatch.setattr(AssistantTools, "execute", execute)
    gateway = Gateway(
        [
            step(call("latest_goldfish"), call("generator_intelligence")),
            step(content="The old report is stale; finish remains unknown."),
        ]
    )
    events = []
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Explain the latest results"}],
        gateway=gateway,
        experiment_service=service,
        on_event=events.append,
    )
    assert result["status"] == "done"
    assert all(len(request.user) <= 16000 for request in gateway.requests)
    context = json.loads(gateway.requests[1].user)
    receipt = next(
        x["result"]
        for x in context["conversation"]
        if x.get("name") == "latest_goldfish"
    )
    assert "view" not in receipt["data"]
    assert receipt["data"]["observations"][0]["value"] == 0.625
    assert receipt["data"]["observations"][1]["value"] is None
    assert receipt["data"]["report_revision"] == -1
    assert receipt["data"]["report_deck_sha256"] == "measured-old-deck"
    assert receipt["data"]["report_reason"] is None
    assert receipt["data"]["stale"] is True
    assert receipt["data"]["coverage"]["unauthored_cards"] == 75
    assert receipt["evidence_omitted"]
    stored = next(
        x
        for x in events
        if x.get("type") == "tool_result" and x["name"] == "latest_goldfish"
    )
    assert "view" in stored["data"]


def test_context_budget_handles_single_large_receipt_and_preserves_request():
    from sabermetrics.assistant_runtime import _encode_context

    request = "Please inspect " + "specific detail " * 180 + " FINAL INSTRUCTION"
    protected = [f"protected-{i}" for i in range(100)]
    state = {"constraints": {"protected_cards": protected, "theme": "Keep the theme"}}
    context = json.loads(
        _encode_context(
            state,
            request,
            [
                {
                    "role": "tool",
                    "name": "search_cards",
                    "result": {
                        "name": "search_cards",
                        "ok": True,
                        "data": {"cards": ["x" * 1200] * 100, "source": "catalog"},
                    },
                }
            ],
        )
    )
    assert context["current_user_request"] == request
    assert context["state"]["constraints"]["protected_cards"] == protected
    assert context["conversation"][0]["result"]["evidence_omitted"] == ["cards"]


def test_presentation_resolves_only_known_cards_and_real_server_metadata(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET mana_cost=?,oracle_text=?,image_uri=? WHERE id=?",
            (
                "{2}",
                "Real stored card text",
                "https://cards.example/real.jpg",
                "incoming-0",
            ),
        )
        conn.commit()
    final = AssistantStep(
        content="Try **Incoming 0**. Its performance remains unmeasured.",
        card_ids=["incoming-0", "invented-id"],
    )
    gateway = Gateway([step(call("search_cards", {"query": "Incoming"})), final])
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "done"
    assert result["presentation"]["cards"] == [
        {
            "id": "incoming-0",
            "name": "Incoming 0",
            "mana_cost": "{2}",
            "type_line": "Artifact",
            "oracle_text": "Real stored card text",
            "image_uri": "https://cards.example/real.jpg",
        }
    ]
    assert "goldfish" not in result["presentation"]


def test_current_deck_mentioned_card_gets_preview_without_model_metadata(prepared):
    service, path, owner, deck_id, document, _alternatives = prepared
    card = next(entry for entry in document["entries"] if entry["is_commander"])
    gateway = Gateway(
        [
            AssistantStep(
                content="Your deck has a printed commander engine.",
                findings=[f"Your commander is **{card['name']}**."],
            )
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["presentation"]["cards"][0]["id"] == card["card_id"]
    assert result["presentation"]["cards"][0]["name"] == card["name"]
    # Even a catalog ID must first be resolved in this conversation or deck.
    gateway = Gateway(
        [
            AssistantStep(
                content="A card is not yet researched.", card_ids=["incoming-0"]
            )
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["presentation"]["cards"] == []


def test_goldfish_presentation_keeps_exact_native_values_unknowns_and_provenance(
    prepared, monkeypatch
):
    service, path, owner, deck_id, _document, _alternatives = prepared
    view = {
        "id": "measured-run",
        "revision": 3,
        "deck_sha256": "actual-stored-hash",
        "reason": "",
        "status": "done",
        "stale": True,
        "result": {
            "diagnostics": {
                "settings": {"seed": 20261012, "engine_version": "0.4.1"},
                "coverage": {
                    "unauthored_cards": 75,
                    "assumptions": [
                        "Partial Vivi effects execute while the card remains counted unauthored."
                    ],
                },
                "tests": {
                    "opening": {
                        "status": "partial",
                        "metrics": {
                            "qualified_opening_rate": {
                                "value": 0.625,
                                "unit": "proportion",
                                "denominator": 2000,
                            }
                        },
                    },
                    "finish": {
                        "status": "unsupported",
                        "metrics": {
                            "verified_finish_rate": {
                                "value": None,
                                "unit": "proportion",
                            }
                        },
                    },
                },
            }
        },
    }
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.simulation_view", lambda *_args: view
    )
    gateway = Gateway(
        [
            step(call("latest_goldfish")),
            step(content="This report is stale; the finish is unknown."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    data = result["presentation"]["goldfish"]
    assert data["report_revision"] == 3
    assert data["report_id"] == "measured-run"
    assert data["report_deck_sha256"] == "actual-stored-hash"
    assert data["stale"] is True
    assert data["observations"][0]["value"] == 0.625
    assert data["observations"][1]["value"] is None
    assert data["coverage"] == view["result"]["diagnostics"]["coverage"]
    assert "partial diagnostic effects" in data["coverage_note"]
    assert data["settings"] == view["result"]["diagnostics"]["settings"]


def test_server_card_presentation_is_capped_at_twelve(prepared):
    from sabermetrics.assistant_tools import resolve_presentation_cards

    _service, path, _owner, _deck_id, _document, _alternatives = prepared
    known = set()
    with db.connect(path) as conn:
        for index in range(15):
            card_id = f"presentation-{index}"
            known.add(card_id)
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,color_identity,is_legal_in_99) VALUES(?,?,?,?,?,?)",
                (
                    card_id,
                    f"presentation-oracle-{index}",
                    f"Real Preview {index}",
                    "Artifact",
                    "[]",
                    1,
                ),
            )
        conn.commit()
    names = "; ".join(f"Real Preview {index}" for index in range(15))
    cards = resolve_presentation_cards(path, known, names, [])
    assert len(cards) == 12
    assert all(card["id"] in known for card in cards)


def test_current_commander_printed_facts_are_in_bounded_model_context(prepared):
    service, path, owner, deck_id, document, _alternatives = prepared
    commander = next(entry for entry in document["entries"] if entry["is_commander"])
    printed = (
        "Whenever you cast a noncreature spell, put a +1/+1 counter on this commander."
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_entries SET mana_cost=?,type_line=?,oracle_text=? WHERE id=?",
            ("{1}{U}{R}", "Legendary Creature — Wizard", printed, commander["id"]),
        )
        conn.commit()
    gateway = Gateway(
        [
            step(
                content="The printed spell trigger supports noncreature spells; native performance remains partially measured."
            )
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Explain my commander's synergies."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    state = json.loads(gateway.requests[0].user)["state"]
    facts = state["commanders"][0]
    assert facts["card_id"] == commander["card_id"]
    assert facts["oracle_id"] == commander["oracle_id"]
    assert facts["mana_cost"] == "{1}{U}{R}"
    assert facts["type_line"] == "Legendary Creature — Wizard"
    assert facts["oracle_text"] == printed
    assert len(gateway.requests[0].user) < 16000
    assert "retrieve them" in gateway.requests[0].system


def test_final_findings_and_next_action_form_user_facing_answer(prepared):
    service, path, owner, deck_id, document, _alternatives = prepared
    commander = next(entry for entry in document["entries"] if entry["is_commander"])
    gateway = Gateway(
        [
            step(call("inspect_deck")),
            AssistantStep(
                content=f"Your commander is {commander['name']}.",
                findings=[
                    "The current library has 99 front-face lands.",
                    "I can find cards that fit your commander's printed rules.",
                    "I can run Goldfish and explain its measured limitations.",
                ],
                next_action="Choose card research or a Goldfish run.",
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [
            {
                "role": "user",
                "content": "Tell me my commander, land count and two ways you can help.",
            }
        ],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    assert "99 front-face lands" in result["content"]
    assert "- I can find cards" in result["content"]
    assert "- I can run Goldfish" in result["content"]
    assert result["content"].endswith("Choose card research or a Goldfish run.")
    reply = result["presentation"]["reply"]
    assert reply["lead"] == f"Your commander is {commander['name']}."
    assert len(reply["findings"]) == 3
    assert reply["next_action"] == "Choose card research or a Goldfish run."
    receipt = next(
        event for event in result["actions"] if event["type"] == "tool_result"
    )
    assert receipt["data"]["front_land_count"] == 99
    assert receipt["data"]["commanders"][0]["name"] == commander["name"]


def test_card_coverage_lookup_returns_only_requested_slots(prepared, monkeypatch):
    service, path, owner, deck_id, _document, _alternatives = prepared
    cards = [
        {
            "slot": index,
            "name": "Vivi " + str(index),
            "status": "partial",
            "supported_effects": ["spell trigger"],
            "limitations": ["combat absent"],
        }
        for index in range(100)
    ]
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.simulation_view",
        lambda *_: {
            "id": "audit",
            "freshness": "current",
            "freshness_scope": "deck_content_under_recorded_settings",
            "result": {
                "diagnostics": {
                    "coverage": {
                        "card_coverage": {"counts": {"partial": 100}, "cards": cards}
                    }
                }
            },
        },
    )
    gateway = Gateway(
        [
            step(
                call(
                    "goldfish_card_coverage",
                    {"query": "Vivi", "offset": 10, "limit": 2},
                )
            ),
            step(content="These two cards have partial coverage."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    receipt = next(
        item
        for item in result["actions"]
        if item.get("name") == "goldfish_card_coverage"
        and item.get("type") == "tool_result"
    )
    assert receipt["data"]["cards"] == cards[10:12]
    assert receipt["data"]["omitted_count"] == 98
    assert receipt["data"]["freshness_scope"] == "deck_content_under_recorded_settings"


def test_freshness_receipts_are_live_evidence_not_replayed_history():
    from sabermetrics.assistant_runtime import (
        SYSTEM,
        _conversation,
        _encode_context,
    )

    receipt = {
        "name": "latest_goldfish",
        "data": {
            "freshness": "current",
            "freshness_reason": "Matches the current deck under recorded settings.",
            "freshness_scope": "deck_content_under_recorded_settings",
            "stale": False,
            "view": {"id": "run-1", "revision": 4, "deck_sha256": "same-list"},
        },
    }
    old_tool = {"role": "tool", "result": receipt}
    assert _conversation([old_tool]) == []
    context = json.loads(_encode_context({"revision": 24}, "Explain", [old_tool]))
    data = context["conversation"][0]["result"]["data"]
    assert data["freshness"] == "current"
    assert data["report_revision"] == 4
    assert data["freshness_scope"] == "deck_content_under_recorded_settings"
    assert "revision mismatch alone does not make" in SYSTEM
    assert "Call latest_goldfish again" in SYSTEM


def test_full_turn_coverage_preserves_ui_audit_and_bounded_model_details(
    prepared, monkeypatch
):
    service, path, owner, deck_id, _document, _alternatives = prepared
    cards = [
        {
            "slot": i,
            "name": "Vivi " + str(i),
            "status": "partial",
            "supported_effects": ["spell damage trigger"],
            "limitations": ["combat not implemented"],
        }
        for i in range(100)
    ]
    view = {
        "id": "audit",
        "status": "done",
        "freshness": "current",
        "result": {
            "diagnostics": {
                "coverage": {
                    "total_cards": 100,
                    "card_coverage": {
                        "version": 1,
                        "unit": "card_slots",
                        "counts": {
                            "supported": 0,
                            "partial": 100,
                            "unsupported": 0,
                            "inactive": 0,
                        },
                        "cards": cards,
                    },
                },
                "tests": {
                    "opening": {
                        "status": "partial",
                        "metrics": {
                            "qualified_opening_rate": {
                                "value": 0.5,
                                "unit": "proportion",
                            }
                        },
                    }
                },
            }
        },
    }
    monkeypatch.setattr("sabermetrics.assistant_tools.simulation_view", lambda *_: view)
    gateway = Gateway(
        [
            step(call("latest_goldfish")),
            step(call("goldfish_card_coverage", {"query": "Vivi", "limit": 2})),
            step(content="Coverage remains partial."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert (
        result["presentation"]["goldfish"]["coverage"]["card_coverage"]["cards"]
        == cards
    )
    receipt = next(
        action
        for action in result["actions"]
        if action.get("name") == "latest_goldfish"
        and action.get("type") == "tool_result"
    )
    assert receipt["data"]["coverage"]["card_coverage"]["cards"] == cards
    context = json.loads(gateway.requests[1].user)
    latest = next(
        item["result"]
        for item in context["conversation"]
        if item.get("name") == "latest_goldfish"
    )
    audit = latest["data"]["coverage"]["card_coverage"]
    assert audit["omitted_slots"] == 100
    assert audit["counts"]["partial"] == 100
    assert "cards" not in audit
    context = json.loads(gateway.requests[2].user)
    detail = next(
        item["result"]
        for item in context["conversation"]
        if item.get("name") == "goldfish_card_coverage"
    )
    assert detail["data"]["cards"][0]["supported_effects"] == ["spell damage trigger"]
    assert detail["data"]["cards"][0]["limitations"] == ["combat not implemented"]
    assert all(len(request.user) <= 16000 for request in gateway.requests)


@pytest.mark.parametrize("tool", ["prepare_improvement", "find_replacements"])
def test_swap_receipt_preserves_card_facts_ahead_of_broad_assessment(tool):
    from sabermetrics.assistant_runtime import _model_receipt

    evidence = [
        {
            "name": "Astral Cornucopia",
            "type_line": "Artifact",
            "mana_cost": "{X}{X}{X}",
            "commander_cast_interactions": [
                {"matches_trigger": True, "status": "compatible"}
            ],
            "outgoing_card": {
                "name": "Chandra's Ignition",
                "roles_lost": ["broad_damage_payoff"],
            },
        }
    ]
    receipt = _model_receipt(
        {
            "name": tool,
            "data": {
                "candidate_evidence": evidence,
                "assessment": {f"detail-{i}": "x" * 1200 for i in range(30)},
                "alternatives": [{"name": "Test only"}],
                "recommendation_id": "server-id",
            },
        }
    )
    assert receipt["data"]["candidate_evidence"] == evidence
    assert "assessment" in receipt["evidence_omitted"]
    assert len(json.dumps(receipt)) < 12000


def test_swap_facts_survive_context_eviction_without_becoming_performance_proof():
    from sabermetrics.assistant_runtime import _encode_context, _retain_swap_context

    state = {"constraints": {}, **{f"context-{i}": "y" * 1200 for i in range(4)}}
    _retain_swap_context(
        state,
        {
            "candidate_evidence": [
                {
                    "name": "Astral Cornucopia",
                    "type_line": "Artifact",
                    "mana_cost": "{X}{X}{X}",
                    "conditions": ["variable_mana_investment_required"],
                    "commander_cast_interactions": [
                        {
                            "commander": "Vivi Ornitier",
                            "matches_trigger": True,
                            "status": "compatible",
                            "reason": "Printed types match",
                        }
                    ],
                    "outgoing_card": {
                        "name": "Chandra's Ignition",
                        "roles_lost": ["broad_damage_payoff"],
                    },
                }
            ]
        },
    )
    context = json.loads(
        _encode_context(
            state,
            "Recommend swaps",
            [{"role": "assistant", "content": "x" * 1200} for _ in range(10)],
        )
    )
    assert context["history_truncated"] is True
    evidence = context["state"]["swap_review"]
    assert (
        evidence["candidates"][0]["incoming"]["commander_cast_interactions"][0][
            "matches_trigger"
        ]
        is True
    )
    assert evidence["candidates"][0]["outgoing"]["roles_lost"] == [
        "broad_damage_payoff"
    ]
    assert "not measured upgrades" in evidence["note"]


def test_mixed_swap_blanket_trigger_claim_is_grounded_in_typed_facts():
    from sabermetrics.assistant_runtime import _ground_blanket_cast_claims

    state = {
        "swap_review": {
            "candidates": [
                {
                    "incoming": {
                        "name": name,
                        "commander_cast_interactions": [
                            {"commander": "Vivi Ornitier", "matches_trigger": eligible}
                        ],
                    }
                }
                for name, eligible in [
                    ("Island", False),
                    ("Astral Cornucopia", True),
                    ("Unknown", None),
                ]
            ]
        }
    }
    bad = "- All three incoming cards are noncreature, so each still triggers Vivi when cast. None is a measured upgrade."
    fixed = _ground_blanket_cast_claims(bad, state)
    assert "All three" not in fixed
    assert "Island → Vivi Ornitier: does not match" in fixed
    assert "Astral Cornucopia → Vivi Ornitier: matches when cast" in fixed
    assert "Unknown → Vivi Ornitier: unknown" in fixed
    assert "None is a measured upgrade." in fixed
    for scoped in [
        "All noncreature spells trigger Vivi Ornitier.",
        "Only these cards trigger Vivi Ornitier.",
        "All three incoming cards except Island trigger Vivi Ornitier.",
        "All three cards trigger a different engine.",
        "Both incoming artifacts trigger Vivi.",
    ]:
        assert _ground_blanket_cast_claims(scoped, state) == scoped


def test_live_swap_answer_and_presentation_both_use_cast_check(prepared, monkeypatch):
    from sabermetrics import assistant_deck_intelligence
    from sabermetrics.functional_candidates import oracle_facts

    service, path, owner, deck_id, document, alternatives = prepared
    evidence = [
        {
            "name": name,
            "commander_cast_interactions": [
                {"commander": "Vivi Ornitier", "matches_trigger": eligible}
            ],
        }
        for name, eligible in [("Island", False), ("Cornucopia", True)]
    ]
    for proposal, row in zip(alternatives, evidence):
        preview = service.validate_replacements(
            owner, deck_id, document["revision"], proposal["replacements"], {}
        )
        old = next(
            e
            for e in document["entries"]
            if e["id"] == proposal["replacements"][0]["outgoing_entry_id"]
        )
        incoming = next(
            e
            for e in preview["document"]["entries"]
            if e.get("card_id") == proposal["replacements"][0]["incoming_card_id"]
        )
        row.update(
            incoming_oracle_facts=oracle_facts(incoming),
            outgoing_oracle_facts=oracle_facts(old),
        )
    monkeypatch.setattr(
        assistant_deck_intelligence,
        "recommend_replacements",
        lambda *a, **k: {
            "alternatives": alternatives[:2],
            "candidate_evidence": evidence,
        },
    )
    gateway = Gateway(
        [
            step(call("find_replacements")),
            step(content="All incoming cards trigger Vivi when cast."),
        ]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert "Island → Vivi Ornitier: does not match" in result["content"]
    assert result["presentation"]["reply"]["lead"] == result["content"]
    assert result["grounding_corrections"]


def test_missing_review_gateway_returns_no_recommendations(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    tools = AssistantTools(
        path, owner, deck_id, "suggest", {}, lambda: False, experiment_service=service
    )
    result = tools.execute(call("find_replacements", {"focus": "draw"}))
    assert result["alternatives"] == []
    assert result["candidate_evidence"] == []
    assert result["experiment_ready"] is False
    assert tools.reviewed_proposals == set()


@pytest.mark.parametrize("tool_name", ["propose_replacements", "start_experiment"])
def test_direct_proposal_and_experiment_require_review(prepared, tool_name):
    service, path, owner, deck_id, document, alternatives = prepared
    tools = AssistantTools(
        path, owner, deck_id, "automate", {}, lambda: False, experiment_service=service
    )
    arguments = (
        {"proposal": alternatives[0]}
        if tool_name == "propose_replacements"
        else {"alternatives": alternatives}
    )
    with pytest.raises(ValueError, match="Strategic review"):
        tools.execute(call(tool_name, arguments))
    assert tools.proposals == []
    assert service.list_for_deck(owner, deck_id) == []
    assert DeckDocumentRepo(path).get(owner, deck_id) == document


def test_accepted_review_cache_avoids_repeated_semantic_calls(prepared):
    service, path, owner, deck_id, _document, alternatives = prepared
    gateway = Gateway([])
    tools = AssistantTools(
        path,
        owner,
        deck_id,
        "suggest",
        {},
        lambda: False,
        experiment_service=service,
        review_gateway=gateway,
    )
    tools.execute(call("propose_replacements", {"proposal": alternatives[0]}))
    tools.execute(call("propose_replacements", {"proposal": alternatives[0]}))
    assert len(gateway.review_requests) == 1
    assert gateway.requests == []


def test_rejected_pair_is_never_exposed_as_preview(prepared):
    service, path, owner, deck_id, _document, alternatives = prepared

    class RejectGateway(Gateway):
        def generate(self, request):
            response = super().generate(request)
            for decision in response.value.decisions:
                decision.decision = "reject"
                decision.reason = (
                    "This removes needed mana for an unrelated conditional draw effect."
                )
            return response

    gateway = RejectGateway([])
    tools = AssistantTools(
        path,
        owner,
        deck_id,
        "suggest",
        {},
        lambda: False,
        experiment_service=service,
        review_gateway=gateway,
    )
    with pytest.raises(ValueError, match="Strategic review"):
        tools.execute(call("propose_replacements", {"proposal": alternatives[0]}))
    assert tools.proposals == []
    assert tools.reviewed_proposals == set()
    assert len(gateway.review_requests) == 1


def test_selected_objective_is_preserved_through_review_and_experiment(
    prepared, monkeypatch
):
    service, path, owner, deck_id, _document, alternatives = prepared
    monkeypatch.setattr(service, "execute", lambda *_args: None)
    gateway = Gateway([])
    tools = AssistantTools(
        path,
        owner,
        deck_id,
        "automate",
        {"objective": "positive_finish_proof_rate"},
        lambda: False,
        experiment_service=service,
        review_gateway=gateway,
    )
    result = tools.execute(call("start_experiment", {"alternatives": alternatives}))
    payload = service.get(owner, result["experiment_id"])
    assert payload["constraints"]["objective"] == "positive_finish_proof_rate"
    assert (
        json.loads(gateway.review_requests[0].user)["focus"]
        == "positive_finish_proof_rate"
    )


def test_card_details_resolves_exact_names_without_model_search(prepared):
    service, path, owner, deck, _doc, _alternatives = prepared
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: False, experiment_service=service
    )
    facts = tools.execute(call("card_details", {"card_id": "Incoming 0"}))
    assert facts["card"]["id"] == "incoming-0"
    assert facts["reference_resolution"] == "exact_name"
    by_oracle = tools.execute(call("card_details", {"card_id": "incoming-oracle-0"}))
    assert by_oracle["card"]["id"] == "incoming-0"
    assert by_oracle["reference_resolution"] == "exact_oracle_id"
    with db.connect(path) as conn:
        conn.execute("UPDATE cards SET name='Incoming 0' WHERE id='incoming-1'")
        conn.commit()
    assert (
        tools.execute(call("card_details", {"card_id": "Incoming 0"}))["status"]
        == "ambiguous"
    )
