"""Real HTTP/store/authority seams with local cost authority and no provider calls."""

from sabermetrics import db
from sabermetrics.assistant_store import AssistantStore

pytest_plugins = ["tests.test_assistant_cost_web"]


def binding(response):
    payload = response.json
    return {
        "expected_run_id": payload["turn_id"],
        "expected_generation": payload["research_workspace"]["generation"],
        "expected_revision": payload["revision"],
    }


def test_fresh_v1_target_submit_and_shared_deadline_restart_replay(web):
    client, _path, _owner, deck, control = web
    url = f"/api/decks/{deck}/assistant"
    initial = client.get(url + "?contract=assistant.events.v1")
    assert initial.status_code == 200
    assert initial.json["public_events"]["run"] is None
    first = client.post(
        url + "/messages",
        json={
            "message": "Compare artifact draw",
            "client_request_id": "one",
            "expected_run_id": None,
            "expected_generation": 0,
            "expected_revision": 0,
        },
    )
    assert first.status_code == 202, first.json
    before = client.get(url + "?contract=assistant.events.v1").json["public_events"]
    request = {
        **binding(first),
        "action": "message",
        "message": "Explain the conditions",
        "client_request_id": "restart",
    }
    restart = client.post(url + "/steer", json=request)
    assert restart.status_code == 200, restart.json
    assert restart.json["turn_id"] != first.json["turn_id"]
    repeated = client.post(url + "/steer", json=request)
    assert repeated.status_code == 200
    assert repeated.json["turn_id"] == restart.json["turn_id"]
    after = client.get(url + "?contract=assistant.events.v1").json["public_events"]
    assert before["run"]["budget_group_id"] == after["run"]["budget_group_id"]
    assert before["run"]["budget_started_at"] == after["run"]["budget_started_at"]
    assert before["run"]["deadline_at"] == after["run"]["deadline_at"]
    assert len(control.status()["turns"]) == 1
    # An old tab polling A must discover current B instead of following A forever.
    old_poll = client.get(
        url
        + f"?contract=assistant.events.v1&run_id={first.json['turn_id']}&after_sequence={before['run']['last_sequence']}"
    )
    assert old_poll.json["public_events"]["run"]["run_id"] == restart.json["turn_id"]
    rejected = client.post(url + "/stop", json=binding(first))
    assert rejected.status_code == 409
    assert all(not t["closed"] for t in control.status()["turns"].values())
    stopped = client.post(url + "/stop", json=binding(restart))
    assert stopped.status_code == 200
    assert all(t["closed"] for t in control.status()["turns"].values())


def test_stopping_an_exact_run_survives_a_deck_edit(web):
    client, path, owner, deck, control = web
    url = f"/api/decks/{deck}/assistant"
    accepted = client.post(
        url + "/messages",
        json={
            "message": "Research",
            "client_request_id": "one",
            "expected_revision": 0,
        },
    )
    with db.connect(path) as conn:
        conn.execute("UPDATE deck_documents SET revision=1 WHERE id=?", (deck,))
        conn.commit()
    response = client.post(url + "/stop", json=binding(accepted))
    assert response.status_code == 200, response.json
    assert (
        AssistantStore(path).public_events(owner, deck)["run"]["lifecycle"] == "stopped"
    )
    assert all(t["closed"] for t in control.status()["turns"].values())


def test_closed_original_budget_never_silently_readmits_steering(web):
    client, path, owner, deck, control = web
    url = f"/api/decks/{deck}/assistant"
    first = client.post(
        url + "/messages",
        json={
            "message": "Research",
            "client_request_id": "one",
            "expected_revision": 0,
        },
    )
    paid = first.json["cost_turn"]
    control.cancel_turn(paid["id"], paid["owner"])
    response = client.post(
        url + "/steer",
        json={
            **binding(first),
            "action": "message",
            "message": "Another condition",
            "client_request_id": "restart",
        },
    )
    assert response.status_code == 409, response.json
    assert len(control.status()["turns"]) == 1
    assert AssistantStore(path).get(owner, deck)["turn_id"] == first.json["turn_id"]


def test_worker_persists_useful_finding_and_content_free_timing_without_provider_call(
    web, monkeypatch
):
    import json

    from sabermetrics.assistant_obligations import build_obligations
    from sabermetrics.assistant_publishability import early_findings
    from sabermetrics.ui import assistant_routes
    from tests.test_assistant_publishability import card_receipt

    client, path, owner, deck, _control = web
    client.application.config["DECK_LAB_ASSISTANT_AI_ENABLED"] = True
    calls = []

    def offline_runtime(*args, **kwargs):
        calls.append(True)
        findings = early_findings(
            "card_details",
            card_receipt(),
            build_obligations("What does Basalt Monolith say?", 0),
        )
        kwargs["on_event"](
            {
                "type": "tool_result",
                "name": "card_details",
                "ok": True,
                "public_findings": [x.model_dump() for x in findings],
            }
        )
        return {
            "content": "Basalt Monolith has the supplied printed text.",
            "status": "done",
            "answer_review": {"fulfillment": "partial"},
        }

    monkeypatch.setattr("sabermetrics.assistant_runtime.run_turn", offline_runtime)
    url = f"/api/decks/{deck}/assistant"
    response = client.post(
        url + "/messages",
        json={
            "message": "What does Basalt Monolith say?",
            "expected_revision": 0,
            "client_request_id": "offline",
        },
    )
    assert response.status_code == 202
    function, args, kwargs = assistant_routes._EXECUTOR.jobs.pop(0)
    function(*args, **kwargs)
    with db.connect(path) as conn:
        row = conn.execute(
            "SELECT checkpoint_json FROM build_assistant_jobs WHERE id=?",
            (response.json["turn_id"],),
        ).fetchone()
    report = json.loads(row[0])["latency"]
    assert report["durations_seconds"]["first_useful"] is not None
    assert report["durations_seconds"]["completion"] is not None
    assert report["provider_calls"] == 0
    assert "Basalt" not in json.dumps(report)
    public = AssistantStore(path).public_events(owner, deck)
    assert public["run"]["lifecycle"] == "completed"
    assert public["snapshot_page"]["entities"]["findings"][0]["type"] == "printed_fact"
    assert calls == [True]


def test_unknown_usage_stays_reserved_and_visible_after_terminal(web):
    client, path, owner, deck, control = web
    url = f"/api/decks/{deck}/assistant"
    response = client.post(
        url + "/messages",
        json={
            "message": "Research",
            "expected_revision": 0,
            "client_request_id": "one",
        },
    )
    paid = response.json["cost_turn"]
    attempt = control.admit_attempt(paid["id"], paid["owner"], "fake", 20, 20)
    control.reconcile(attempt, None)
    store = AssistantStore(path)
    store.finish(
        owner,
        deck,
        response.json["turn_id"],
        {"content": "Partial answer", "status": "done"},
    )
    public = client.get(url + "?contract=assistant.events.v1").json["public_events"]
    assert public["run"]["lifecycle"] == "completed"
    assert public["operational"]["cost_pending"] is True
    assert control.status()["attempts"][attempt]["settled"] is False
    assert "bound" not in str(public["operational"])


def test_v1_mutation_cannot_omit_target_fields(web):
    client, _path, _owner, deck, _control = web
    response = client.post(
        f"/api/decks/{deck}/assistant/stop",
        json={},
        headers={"X-Assistant-Contract": "assistant.events.v1"},
    )
    assert response.status_code == 409
    assert response.json["error"] == "run_conflict"
