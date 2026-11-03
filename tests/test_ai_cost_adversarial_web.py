"""Adversarial failure-boundary regressions. No real providers or workers."""

import pytest

from sabermetrics.assistant_store import AssistantStore
from sabermetrics.deck_documents import DeckDocumentRepo

pytest_plugins = ("tests.test_assistant_cost_web",)


def test_failed_session_commit_poisoned_idempotency_retry(web, monkeypatch):
    client, path, owner, deck, control = web
    url = f"/api/decks/{deck}/assistant/messages"
    body = dict(
        message="research",
        client_request_id="commit-failure",
        expected_revision=DeckDocumentRepo(path).get(owner, deck)["revision"],
    )
    original = AssistantStore._save

    def fail_save(*args, **kwargs):
        raise OSError("injected session commit failure")

    monkeypatch.setattr(AssistantStore, "_save", fail_save)
    with pytest.raises(OSError, match="injected"):
        client.post(url, json=body)
    assert len(control.status()["turns"]) == 1
    assert all(t["closed"] for t in control.status()["turns"].values())
    monkeypatch.setattr(AssistantStore, "_save", original)
    response = client.post(url, json=body)
    assert response.status_code == 409
    assert response.json["retry_new_submission"] is True
    assert len(control.status()["turns"]) == 1
    assert not AssistantStore(path).get(owner, deck)["busy"]


def test_environment_shutdown_visible_to_user(web):
    client, _path, _owner, _deck, control = web
    control.set_kill_switch(True, environment="main")
    response = client.get("/api/assistant/cost-status")
    assert response.status_code == 200
    # Per-environment shutdown must be represented, not shown as enabled.
    assert response.json["kill_switch"] is True


def test_cross_deck_active_user_exclusion(web):
    client, path, owner, deck, control = web
    other = DeckDocumentRepo(path).create(owner, title="Other")

    def send(target, key):
        return client.post(
            f"/api/decks/{target}/assistant/messages",
            json={
                "message": "research",
                "client_request_id": key,
                "expected_revision": DeckDocumentRepo(path).get(owner, target)[
                    "revision"
                ],
            },
        )

    assert send(deck, "deck-a").status_code == 202
    assert send(other, "deck-b").status_code == 429
    assert len(control.status()["turns"]) == 1


def test_replay_does_not_replace_later_session(web):
    _client, path, owner, deck, _control = web
    store = AssistantStore(path)
    first = store.begin(owner, deck, "first", 0, request_id="first")
    store.stop(owner, deck)
    second = store.begin(owner, deck, "second", 0, request_id="second")
    replay = store.begin(owner, deck, "first", 0, request_id="first")
    assert replay["turn_id"] == first["turn_id"]
    assert store.get(owner, deck)["turn_id"] == second["turn_id"]


def test_exact_replay_still_readable_when_provider_disabled(web, monkeypatch):
    from sabermetrics.ui import assistant_routes

    client, path, owner, deck, _control = web
    url = f"/api/decks/{deck}/assistant"
    body = dict(
        message="research",
        client_request_id="replay-disabled",
        expected_revision=DeckDocumentRepo(path).get(owner, deck)["revision"],
    )
    first = client.post(url + "/messages", json=body)
    assert first.status_code == 202
    client.post(url + "/stop", json={})
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": False})
    response = client.post(url + "/messages", json=body)
    assert response.status_code == 202
    assert response.json["turn_id"] == first.json["turn_id"]
