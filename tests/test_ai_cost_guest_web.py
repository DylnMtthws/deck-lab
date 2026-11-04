"""Web ownership stays private while preview quota subjects share a network."""

import time

from sabermetrics.deck_documents import DeckDocumentRepo

pytest_plugins = ["tests.test_assistant_cost_web"]


def test_guest_identity_is_server_derived_and_session_allowance_is_displayed(
    web, monkeypatch
):
    client, path, owner, deck, control = web
    client.application.config["AI_COST_ENVIRONMENT"] = "preview"
    client.application.extensions["ai_cost_guest_network"] = (
        lambda user: "guest-network:trusted"
    )
    now = [time.time()]
    monkeypatch.setattr("sabermetrics.ai_cost_control.time.time", lambda: now[0])
    url = f"/api/decks/{deck}/assistant"
    body = dict(
        message="Compare",
        expected_revision=DeckDocumentRepo(path).get(owner, deck)["revision"],
        client_request_id="one",
        quota_user="attacker",
        guest_session="attacker",
        environment="main",
    )
    assert client.get("/api/assistant/cost-status").json["remaining_turns"] == 2
    first = client.post(url + "/messages", json=body)
    assert first.status_code == 202
    turn = next(iter(control.status()["turns"].values()))
    assert turn["user"] == "guest-network:trusted"
    assert turn["guest_session"] == "guest-session:" + owner
    assert turn["env"] == "preview"
    assert client.get("/api/assistant/cost-status").json["remaining_turns"] == 1
    assert client.post(url + "/stop", json={}).status_code == 200
    # Exact replay does not consume another turn or trigger the cooldown.
    assert (
        client.post(url + "/messages", json=body).json["turn_id"]
        == first.json["turn_id"]
    )
    assert (
        client.post(
            url + "/messages", json=dict(body, client_request_id="two")
        ).status_code
        == 429
    )
    now[0] += 31
    assert (
        client.post(
            url + "/messages", json=dict(body, client_request_id="two")
        ).status_code
        == 202
    )
    assert client.post(url + "/stop", json={}).status_code == 200
    now[0] += 31
    assert (
        client.post(
            url + "/messages", json=dict(body, client_request_id="three")
        ).status_code
        == 429
    )
    status = client.get("/api/assistant/cost-status").json
    assert status["remaining_turns"] == 0
    assert status["remaining_micros"] == 100000
    assert "guest-network" not in str(status)


def test_unavailable_network_identity_fails_closed_without_breaking_browsing(web):
    client, path, owner, deck, control = web
    client.application.config["AI_COST_ENVIRONMENT"] = "preview"

    def unavailable(user):
        raise OSError("identity unavailable")

    client.application.extensions["ai_cost_guest_network"] = unavailable
    status = client.get("/api/assistant/cost-status")
    assert status.status_code == 200 and status.json["configured"] is False
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    assert client.post(
        f"/api/decks/{deck}/assistant/messages",
        json=dict(
            message="Compare", expected_revision=revision, client_request_id="one"
        ),
    ).status_code in {429, 503}
    assert not control.status()["turns"]
    assert client.get(f"/build/deck/{deck}").status_code == 200


def test_guest_resolver_cannot_change_main_identity(web):
    client, path, owner, deck, control = web
    client.application.extensions["ai_cost_guest_network"] = (
        lambda user: "guest-network:trusted"
    )
    assert client.get("/api/assistant/cost-status").json["configured"] is False
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    assert client.post(
        f"/api/decks/{deck}/assistant/messages",
        json=dict(
            message="Compare", expected_revision=revision, client_request_id="one"
        ),
    ).status_code in {429, 503}
    assert not control.status()["turns"]
