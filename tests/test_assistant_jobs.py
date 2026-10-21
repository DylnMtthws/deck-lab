"""Durable generations, lease recovery and steering transaction boundaries."""

import json
import time

import pytest

from sabermetrics import db
from sabermetrics.assistant_store import AssistantStore, QueueFull, validate_constraints
from sabermetrics.assistant_workspace import catalog_card_record, workspace_event
from tests.test_resimulate_delta import _client, _seed_deck


@pytest.fixture
def state(tmp_path):
    client, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    return client, path, owner, deck, AssistantStore(path)


def publish(store, owner, deck, turn):
    card = catalog_card_record(
        {"id": "candidate", "name": "Candidate", "oracle_text": "Draw a card."}
    )
    store.append(
        owner,
        deck,
        turn["turn_id"],
        {"role": "tool", "action": workspace_event([card])},
    )


def steering(action, generation=1, **kwargs):
    return {
        "action": action,
        "expected_generation": generation,
        "expected_revision": 0,
        "client_request_id": "request-" + action,
        **kwargs,
    }


def test_reload_claim_is_unique_and_polling_live_job_is_safe(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    recovered = AssistantStore(path)
    assert recovered.claim(owner, deck)["turn_id"] == turn["turn_id"]
    assert store.claim(owner, deck) is None
    assert recovered.recover(owner, deck)["busy"]
    assert recovered.heartbeat(owner, deck, turn["turn_id"])


def test_expired_provider_call_requires_explicit_retry_and_fences_late_results(state):
    _, path, owner, deck, store = state
    first = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.checkpoint(
        owner,
        deck,
        first["turn_id"],
        {"type": "provider_checkpoint", "state": "started", "call_id": "paid"},
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE build_assistant_jobs SET lease_deadline=?", (time.time() - 1,)
        )
        conn.commit()
    interrupted = AssistantStore(path).recover(owner, deck)
    assert not interrupted["busy"]
    assert interrupted["research_workspace"]["job"]["retry_allowed"]
    assert interrupted["research_workspace"]["job"]["checkpoint"]["calls"] == {
        "paid": "started"
    }
    assert store.claim(owner, deck) is None
    publish(store, owner, deck, first)
    store.finish(owner, deck, first["turn_id"], {"content": "Late paid result"})
    assert store.get(owner, deck)["messages"] == interrupted["messages"]
    retried = store.steer(owner, deck, steering("retry"), 0)
    assert retried["research_workspace"]["generation"] == 2
    assert retried["turn_id"] != first["turn_id"]
    assert retried["research_workspace"]["job"]["state"] == "queued"


def test_completed_checkpoints_are_internal_and_finish_is_idempotent(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    for status in ("started", "finished"):
        store.checkpoint(
            owner,
            deck,
            turn["turn_id"],
            {"state": status, "call_id": "paid", "credential": "never stored"},
        )
    publish(store, owner, deck, turn)
    store.finish(owner, deck, turn["turn_id"], {"content": "Done", "status": "done"})
    completed = store.get(owner, deck)
    store.finish(owner, deck, turn["turn_id"], {"content": "duplicate"})
    assert AssistantStore(path).recover(owner, deck) == store.get(owner, deck)
    assert completed["messages"] == store.get(owner, deck)["messages"]
    assert completed["research_workspace"]["status"] == "partial"
    assert completed["research_workspace"]["job"]["state"] == "completed"
    assert "printed" not in json.dumps(completed["messages"])
    with db.connect(path) as conn:
        assert (
            "never stored"
            not in conn.execute(
                "SELECT checkpoint_json FROM build_assistant_jobs"
            ).fetchone()[0]
        )


def test_atomic_steer_preserves_sources_invalidates_claims_and_replays_ack(state):
    _, path, owner, deck, store = state
    first = store.begin(owner, deck, "Find draw", 0)
    publish(store, owner, deck, first)
    pin = store.steer(owner, deck, steering("pin", card_id="candidate"), 0)
    assert pin["turn_id"] == first["turn_id"]
    assert pin["research_workspace"]["cards"][0]["pinned"]
    body = steering("exclude", card_id="candidate")
    ack = store.steer(owner, deck, body, 0)
    card = ack["research_workspace"]["cards"][0]
    assert card["pinned"] and card["excluded"] and card["status"] == "superseded"
    assert ack["constraints"]["excluded_candidates"] == ["candidate"]
    assert not store.active(owner, deck, first["turn_id"])
    publish(store, owner, deck, first)
    assert store.steer(owner, deck, body, 0)["turn_id"] == ack["turn_id"]
    assert AssistantStore(path).get(owner, deck)["turn_id"] == ack["turn_id"]
    with pytest.raises(ValueError):
        store.steer(owner, deck, dict(body, action="prioritize"), 0)
    assert card["printed"]["oracle_text"] == "Draw a card."


def test_queue_full_steering_rolls_back_every_change(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Original", 0)
    publish(store, owner, deck, turn)
    before = store.get(owner, deck)
    with db.connect(path) as conn:
        for i in range(4):
            conn.execute(
                "INSERT INTO build_assistant_jobs(id,owner_id,deck_id,generation,state,snapshot_json,created_at,updated_at) VALUES (?,?,?,1,'queued','{}',0,0)",
                (f"other-{i}", owner, deck),
            )
        conn.commit()
    with pytest.raises(QueueFull):
        store.steer(owner, deck, steering("exclude", card_id="candidate"), 0)
    after = store.get(owner, deck)
    assert after["turn_id"] == before["turn_id"] and after["busy"]
    assert after["constraints"] == before["constraints"]
    assert after["research_workspace"] == before["research_workspace"]


def test_unknown_cards_and_stale_generation_rejected(state):
    _, _, owner, deck, store = state
    store.begin(owner, deck, "Research", 0)
    for body in (
        steering("pin", card_id="invented"),
        steering("message", generation=9, message="New"),
    ):
        with pytest.raises(ValueError):
            store.steer(owner, deck, body, 0)
    assert store.get(owner, deck)["research_workspace"]["generation"] == 1


def test_message_steering_retains_original_and_instruction_history(state):
    _, _, owner, deck, store = state
    store.begin(owner, deck, "Original", 0)
    store.steer(owner, deck, steering("message", message="First"), 0)
    second = store.steer(
        owner,
        deck,
        steering("message", generation=2, client_request_id="second", message="Second"),
        0,
    )
    assert (
        second["research_workspace"]["request_text"]
        == "Original\nAdditional instruction: First\nAdditional instruction: Second"
    )


def test_two_claims_max_and_stopped_queued_job_never_runs(state):
    _, path, owner, deck, store = state
    decks = [deck, _seed_deck(path, owner), _seed_deck(path, owner)]
    for item in decks:
        store.begin(owner, item, "Research", 0)
    assert store.claim(owner, decks[0])
    assert store.claim(owner, decks[1])
    assert store.claim(owner, decks[2]) is None
    store.stop(owner, decks[2])
    assert store.claim(owner, decks[2]) is None
    assert store.get(owner, decks[2])["research_workspace"]["job"]["state"] == "stopped"


@pytest.mark.parametrize("value", [[1], ["x"] * 101, "x"])
def test_steering_constraints_are_bounded_ids(value):
    with pytest.raises(ValueError):
        validate_constraints({"excluded_candidates": value})


def test_old_busy_only_session_migrates_without_replaying(state):
    _, path, owner, deck, store = state
    store.get(owner, deck)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE build_assistant_sessions SET payload_json=? WHERE deck_id=?",
            (
                json.dumps(
                    {
                        "busy": True,
                        "turn_id": "legacy",
                        "mode": "suggest",
                        "constraints": {},
                        "messages": [{"role": "user", "content": "Old request"}],
                        "revision": 0,
                    }
                ),
                deck,
            ),
        )
        conn.commit()
    migrated = store.get(owner, deck)
    assert not migrated["busy"]
    assert migrated["research_workspace"]["job"]["state"] == "interrupted"
    assert migrated["research_workspace"]["job"]["retry_allowed"]
    assert store.claim(owner, deck) is None


def test_dead_process_lease_recovers_before_deadline(state):
    _, path, owner, deck, store = state
    store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE build_assistant_jobs SET lease_owner='999999999:dead',lease_deadline=?",
            (time.time() + 500,),
        )
        conn.commit()
    assert (
        store.recover(owner, deck)["research_workspace"]["job"]["state"]
        == "interrupted"
    )


def test_saved_steering_ack_cannot_roll_back_a_newer_generation(state):
    _, _, owner, deck, store = state
    store.begin(owner, deck, "Original", 0)
    first_body = steering("message", message="First")
    first_ack = store.steer(owner, deck, first_body, 0)
    second = store.steer(
        owner,
        deck,
        steering("message", generation=2, client_request_id="second", message="Second"),
        0,
    )
    assert store.steer(owner, deck, first_body, 0)["turn_id"] == first_ack["turn_id"]
    assert store.get(owner, deck)["turn_id"] == second["turn_id"]
    assert store.get(owner, deck)["research_workspace"]["generation"] == 3


def test_next_request_keeps_pinned_source_cards_but_supersedes_assessments(state):
    _, _, owner, deck, store = state
    turn = store.begin(owner, deck, "Original", 0)
    publish(store, owner, deck, turn)
    store.steer(owner, deck, steering("pin", card_id="candidate"), 0)
    store.stop(owner, deck)
    next_turn = store.begin(owner, deck, "Different question", 0)
    card = next_turn["research_workspace"]["cards"][0]
    assert card["pinned"] and card["status"] == "superseded"
    assert next_turn["research_workspace"]["generation"] == 2


@pytest.mark.parametrize("operation", ["finish", "invalidate"])
def test_stale_worker_terminalizes_only_its_own_turn(state, operation):
    _, path, owner, deck, store = state
    old = store.begin(owner, deck, "Original", 0)
    store.claim(owner, deck)
    replacement = store.steer(owner, deck, steering("message", message="Replace"), 0)
    before = store.get(owner, deck)
    args = (owner, deck, old["turn_id"])
    if operation == "finish":
        store.finish(*args, {"content": "Stale output"})
    else:
        store.invalidate(*args)
    after = store.get(owner, deck)
    assert after["busy"] and after["turn_id"] == replacement["turn_id"]
    assert after["messages"] == before["messages"]
    assert after["research_workspace"] == before["research_workspace"]
    with db.connect(path) as conn:
        assert (
            conn.execute(
                "SELECT state FROM build_assistant_jobs WHERE id=?", (old["turn_id"],)
            ).fetchone()[0]
            == "superseded"
        )


def test_finish_atomically_retires_job_when_deck_revision_changes(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Original", 0)
    store.claim(owner, deck)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_documents SET revision=revision+1 WHERE id=?", (deck,)
        )
        conn.commit()
    result = store.finish(owner, deck, turn["turn_id"], {"content": "Stale output"})
    assert not result["busy"]
    assert result["research_workspace"]["job"]["state"] == "superseded"
    assert [m["content"] for m in result["messages"]] == ["Original"]
    assert store.claim(owner, deck) is None
