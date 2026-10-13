"""Conversation ownership, cancellation, provider failures and persistence."""

import threading

import pytest

from sabermetrics import db
from sabermetrics.assistant_experiments import ExperimentService
from sabermetrics.assistant_store import AssistantStore, validate_constraints
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui import assistant_routes
from tests.test_resimulate_delta import RecordingExecutor, _client, _seed_deck


@pytest.fixture
def setup(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    executor = RecordingExecutor()
    monkeypatch.setattr(assistant_routes, "_EXECUTOR", executor)
    monkeypatch.setattr(assistant_routes, "_SLOTS", threading.BoundedSemaphore(4))
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": True})
    return client, path, owner, deck, executor


def test_settings_and_transcript_persist_per_owned_deck(setup):
    client, path, owner, deck, executor = setup
    url = f"/api/decks/{deck}/assistant"
    assert client.get(url).json["mode"] == "suggest"
    constraints = {
        "budget": 200,
        "power_level": "casual",
        "theme": "Orcs",
        "protected_cards": [],
    }
    assert (
        client.post(
            url + "/settings", json={"mode": "automate", "constraints": constraints}
        ).status_code
        == 200
    )
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    response = client.post(
        url + "/messages",
        json={"message": "Compare three alternatives", "expected_revision": revision},
    )
    assert response.status_code == 202
    stored = AssistantStore(path).get(owner, deck)
    assert stored["constraints"] == constraints
    assert (
        stored["busy"]
        and stored["messages"][-1]["content"] == "Compare three alternatives"
    )
    assert len(executor.jobs) == 1
    assert client.get("/api/decks/missing/assistant").status_code == 404
    with client.session_transaction() as session:
        session["_user_id"] = "someone-else"
    assert client.get(url).status_code in (302, 404)


def test_mode_change_cancels_old_turn_and_preserves_messages(setup):
    _client, path, owner, deck, _ = setup
    store = AssistantStore(path)
    first = store.begin(owner, deck, "Improve", 0)
    store.settings(owner, deck, "automate", {})
    assert not store.active(owner, deck, first["turn_id"])
    store.finish(
        owner,
        deck,
        first["turn_id"],
        {"role": "assistant", "content": "late completion"},
    )
    messages = store.get(owner, deck)["messages"]
    assert [m["content"] for m in messages] == ["Improve"]


def test_repeated_send_and_stale_deck_rejected(setup):
    client, path, owner, deck, _ = setup
    url = f"/api/decks/{deck}/assistant"
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    assert (
        client.post(
            url + "/messages", json={"message": "Hi", "expected_revision": revision + 1}
        ).status_code
        == 409
    )
    assert (
        client.post(
            url + "/messages", json={"message": "Hi", "expected_revision": revision}
        ).status_code
        == 202
    )
    assert (
        client.post(
            url + "/messages", json={"message": "Again", "expected_revision": revision}
        ).status_code
        == 409
    )
    assert client.post(url + "/stop").status_code == 200
    assert not client.get(url).json["busy"]


def test_no_fake_ai_when_provider_missing(setup, monkeypatch):
    client, path, owner, deck, executor = setup
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": False})
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    response = client.post(
        f"/api/decks/{deck}/assistant/messages",
        json={"message": "Hi", "expected_revision": revision},
    )
    assert response.status_code == 503 and not executor.jobs
    assert AssistantStore(path).get(owner, deck)["messages"] == []


@pytest.mark.parametrize(
    "constraints",
    [
        {"budget": True},
        {"budget": -1},
        {"budget": float("nan")},
        {"protected_cards": [1]},
        {"theme": []},
    ],
)
def test_invalid_constraints(constraints):
    with pytest.raises(ValueError):
        validate_constraints(constraints)


def test_protected_card_must_belong_to_deck(setup):
    client, _, _, deck, _ = setup
    assert (
        client.post(
            f"/api/decks/{deck}/assistant/settings",
            json={"mode": "suggest", "constraints": {"protected_cards": ["invented"]}},
        ).status_code
        == 400
    )


def test_tool_loop_results_saved_and_original_untouched(setup, monkeypatch):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    original = DeckDocumentRepo(path).get(owner, deck)

    def run(*args, **kwargs):
        assert not kwargs["should_stop"]()
        kwargs["on_event"](
            {"tool": "search_cards", "content": "Found two matching cards."}
        )
        return {
            "content": "Here are two options.",
            "proposals": [],
            "status": "done",
            "presentation": {"cards": [{"id": "resolved-card", "name": "Actual Card"}]},
        }

    monkeypatch.setattr(assistant_runtime, "run_turn", run)
    client.post(
        f"/api/decks/{deck}/assistant/messages",
        json={"message": "Find draw spells", "expected_revision": original["revision"]},
    )
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    result = client.get(f"/api/decks/{deck}/assistant").json
    assert not result["busy"]
    assert [m["role"] for m in result["messages"]] == ["user", "tool", "assistant"]
    assert result["messages"][-1]["content"] == "Here are two options."
    assert result["messages"][-1]["presentation"]["cards"][0]["id"] == "resolved-card"
    assert DeckDocumentRepo(path).get(owner, deck) == original


def test_apply_rejects_more_than_three_before_service(setup):
    client, path, owner, deck, _ = setup
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]
    response = client.post(
        f"/api/decks/{deck}/assistant/apply",
        json={"replacements": [{}] * 4, "expected_revision": revision},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("changed", ["protected", "theme", "budget"])
def test_adoption_rechecks_current_preferences(setup, monkeypatch, changed):
    from tests.test_assistant_experiments import report

    client, path, owner, deck, _ = setup
    original = DeckDocumentRepo(path).get(owner, deck)
    outgoing = next(e for e in original["entries"] if not e["is_commander"])
    with db.connect(path) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,color_identity,is_legal_in_99) VALUES(?,?,?,?,?,?)",
                (f"new-{i}", f"new-{i}", f"New {i}", "Artifact", "[]", 1),
            )
        conn.commit()
    plans = [
        {
            "name": f"Plan {i}",
            "rationale": "Test",
            "replacements": [
                {"outgoing_entry_id": outgoing["id"], "incoming_card_id": f"new-{i}"}
            ],
        }
        for i in range(3)
    ]
    service = ExperimentService(path)
    experiment = service.create(owner, deck, original["revision"], plans, {})
    monkeypatch.setattr(
        service, "_simulate", lambda document, seed, settings: report(seed)
    )
    service.execute(owner, experiment["id"])
    constraints = (
        {"protected_cards": [outgoing["card_id"]]}
        if changed == "protected"
        else ({"theme": "Elves only"} if changed == "theme" else {"budget": 10})
    )
    assert (
        client.post(
            f"/api/decks/{deck}/assistant/settings", json={"constraints": constraints}
        ).status_code
        == 200
    )
    response = client.post(
        f"/api/decks/{deck}/assistant/experiments/{experiment['id']}/adopt",
        json={
            "alternative_id": experiment["alternatives"][0]["id"],
            "expected_revision": original["revision"],
        },
    )
    assert response.status_code == 400
    assert DeckDocumentRepo(path).get(owner, deck) == original


def test_deleting_deck_removes_conversation(setup):
    _client, path, owner, deck, _ = setup
    store = AssistantStore(path)
    store.begin(owner, deck, "Private deck conversation", 0)
    DeckDocumentRepo(path).delete(owner, deck)
    with db.connect(path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM build_assistant_sessions WHERE deck_id=?", (deck,)
            ).fetchone()[0]
            == 0
        )


def test_expired_conversation_recovers_and_ignores_late_worker(setup, monkeypatch):
    from sabermetrics import assistant_store

    _client, path, owner, deck, _ = setup
    store = AssistantStore(path)
    started = store.begin(owner, deck, "Inspect", 0)
    assert store.get(owner, deck)["busy"]
    monkeypatch.setattr(
        assistant_store.time, "time", lambda: started["started_at"] + 1201
    )
    expired = store.get(owner, deck)
    assert not expired["busy"]
    assert expired["turn_id"] is None
    assert expired["messages"][-1]["status"] == "failed"
    store.finish(owner, deck, started["turn_id"], {"content": "Late result"})
    assert store.get(owner, deck)["messages"] == expired["messages"]
    assert store.begin(owner, deck, "Retry", 0)["busy"]
