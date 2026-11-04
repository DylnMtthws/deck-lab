"""Focused admission integration tests; no providers or worker threads."""

import threading

import pytest

from sabermetrics.ai_cost_control import Authority, BudgetConfig
from sabermetrics.assistant_store import AssistantStore
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui import assistant_routes
from tests.test_resimulate_delta import RecordingExecutor, _client, _seed_deck


@pytest.fixture
def web(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    config = BudgetConfig(
        prices=(("fake", 1, 1),),
        credentials=(("main", "deepseek", "TEST_MAIN"),),
        authorized=True,
    )
    control = Authority(str(tmp_path / "cost.sqlite"), config, local=True)
    client.application.extensions["ai_cost_authority"] = control
    client.application.config["AI_COST_ENVIRONMENT"] = "main"
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": True})
    monkeypatch.setattr(assistant_routes, "_EXECUTOR", RecordingExecutor())
    monkeypatch.setattr(
        assistant_routes, "_WORKER_SLOTS", threading.BoundedSemaphore(2)
    )
    return client, path, owner, deck, control


def test_duplicate_conflict_cancel_and_private_status(web):
    client, path, owner, deck, control = web
    url = f"/api/decks/{deck}/assistant"
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    body = dict(message="Compare", expected_revision=revision, client_request_id="one")
    first = client.post(url + "/messages", json=body)
    assert first.status_code == 202
    replay = client.post(url + "/messages", json=body)
    assert replay.status_code == 202
    assert replay.json["turn_id"] == first.json["turn_id"]
    assert len(control.status()["turns"]) == 1
    assert (
        client.post(url + "/messages", json=dict(body, message="different")).status_code
        == 409
    )
    status = client.get("/api/assistant/cost-status").json
    assert status["remaining_turns"] == 9
    assert status["remaining_micros"] == 500000
    assert "turns" not in status and "attempts" not in status
    assert client.post(url + "/stop", json={}).status_code == 200
    assert all(t["closed"] for t in control.status()["turns"].values())
    assert client.get("/api/admin/assistant/cost-control").status_code == 403


def test_outage_preserves_browsing(web, monkeypatch):
    client, _path, _owner, deck, control = web

    def outage():
        raise OSError("offline")

    monkeypatch.setattr(control, "status", outage)
    response = client.get(f"/api/decks/{deck}/assistant")
    assert response.status_code == 200
    assert response.json["cost_control"]["configured"] is False


def test_replay_after_later_turn_returns_original(web):
    _client, path, owner, deck, _control = web
    store = AssistantStore(path)
    first = store.begin(owner, deck, "first", 0, request_id="first")
    store.stop(owner, deck)
    second = store.begin(owner, deck, "second", 0, request_id="second")
    replay = store.begin(owner, deck, "first", 0, request_id="first")
    assert replay["turn_id"] == first["turn_id"] != second["turn_id"]
    assert not replay["busy"]
