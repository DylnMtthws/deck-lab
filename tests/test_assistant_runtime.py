"""Conversational orchestration exercises real scoped tools with a mock model."""

import json
from types import SimpleNamespace

import pytest

from sabermetrics import db
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.assistant_tools import TOOL_ADAPTER, AssistantTools
from sabermetrics.cedh.errors import ModelConfigurationError
from sabermetrics.deck_documents import DeckDocumentRepo

pytest_plugins = ["tests.test_assistant_experiments"]


class Gateway:
    provider = "test"
    model_id = "typed-mock-model"

    def __init__(self, steps):
        self.steps = iter(steps)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        value = next(self.steps)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(value=value)


def call(name, arguments=None):
    return {"name": name, "arguments": arguments or {}}


def step(*calls, content=""):
    return AssistantStep.model_validate({"content": content, "tool_calls": list(calls)})


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
        [step(*[call("latest_goldfish") for _ in range(3)]) for _ in range(3)]
    )
    result = run_turn(
        path, owner, deck_id, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "budget_exhausted"
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
