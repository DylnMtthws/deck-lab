"""Offline durable event/replay, targeting and migration acceptance tests."""

import json

import pytest

from sabermetrics import assistant_event_store as journal
from sabermetrics import db
from sabermetrics.assistant_events import reduce_event
from sabermetrics.assistant_store import AssistantStore, RunConflict
from sabermetrics.deck_documents import DeckNotFound
from tests.test_resimulate_delta import _client, _seed_deck


@pytest.fixture
def state(tmp_path):
    client, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    return client, path, owner, deck, AssistantStore(path)


def target(turn):
    return {
        "expected_run_id": turn["turn_id"],
        "expected_generation": turn["research_workspace"]["generation"],
        "expected_revision": turn["revision"],
    }


def test_new_projection_replays_exactly_and_terminalizes_once(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "What is Basalt Monolith?", 0, request_id="r")
    store.claim(owner, deck)
    store.append(
        owner,
        deck,
        turn["turn_id"],
        {"action": {"type": "tool_started", "name": "card_details"}},
    )
    store.append(
        owner,
        deck,
        turn["turn_id"],
        {"action": {"type": "tool_result", "name": "card_details", "ok": True}},
    )
    store.finish(
        owner,
        deck,
        turn["turn_id"],
        {
            "content": "Exact source facts remain conditional.",
            "status": "done",
            "answer_review": {"fulfillment": "partial"},
        },
    )
    store.finish(owner, deck, turn["turn_id"], {"content": "Late replacement"})
    with db.connect(path) as conn:
        row = journal.row_for(conn, turn["turn_id"])
        replay = json.loads(row["public_baseline_json"])
        events = [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT event_json FROM build_assistant_events WHERE run_id=? ORDER BY sequence",
                (turn["turn_id"],),
            )
        ]
        for event in events:
            replay = reduce_event(replay, event)
        assert replay == json.loads(row["public_projection_json"])
        assert sum(e["kind"] == "run.ended" for e in events) == 1
        assert replay["run"]["lifecycle"] == "completed"
        assert replay["run"]["fulfillment"] == "partial"
        assert replay["entities"]["answer_blocks"][0]["review_state"] == "unverified"
        assert "Late replacement" not in json.dumps(replay)


def test_snapshot_and_delta_modes_are_exclusive_with_shared_multitab_bundle(state):
    _, _, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    first = store.public_events(owner, deck)
    second = store.public_events(owner, deck)
    assert first["snapshot_page"]["token"] == second["snapshot_page"]["token"]
    assert "events" not in first
    store.claim(owner, deck)
    delta = store.public_events(
        owner,
        deck,
        run_id=turn["turn_id"],
        after_sequence=first["cursor"]["through_sequence"],
    )
    assert delta["mode"] == "delta" and "snapshot_page" not in delta
    assert delta["events"][-1]["kind"] == "run.started"
    for cursor in (-1, 99999):
        assert (
            store.public_events(owner, deck, after_sequence=cursor)["mode"]
            == "reset_required"
        )


def test_stale_tab_stop_cannot_stop_replacement_or_close_its_binding(state):
    _, _, owner, deck, store = state
    first = store.begin(owner, deck, "Research", 0)
    body = {
        **target(first),
        "client_request_id": "steer",
        "action": "message",
        "message": "Also compare conditions",
    }
    second = store.steer(owner, deck, body, 0)
    with pytest.raises(RunConflict):
        store.stop(owner, deck, expected=target(first))
    assert store.get(owner, deck)["turn_id"] == second["turn_id"]
    assert store.get(owner, deck)["busy"]
    # Same steering ID returns its original replacement without another generation.
    replay = store.steer(owner, deck, body, 0)
    assert replay["turn_id"] == second["turn_id"]


def test_stop_old_revision_remains_allowed_and_first_terminal_wins(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    with db.connect(path) as conn:
        conn.execute("UPDATE deck_documents SET revision=1 WHERE id=?", (deck,))
        conn.commit()
    stopped = store.stop(owner, deck, expected=target(turn))
    assert not stopped["busy"]
    store.finish(
        owner, deck, turn["turn_id"], {"content": "late answer", "status": "done"}
    )
    assert store.public_events(owner, deck)["run"]["lifecycle"] == "stopped"
    assert not store.public_events(owner, deck)["snapshot_page"]["entities"].get(
        "answer_blocks"
    )


def test_projection_owner_and_run_scoping_and_expired_snapshot(state):
    _, path, owner, deck, store = state
    store.begin(owner, deck, "Research", 0)
    receipt = store.public_events(owner, deck)
    with pytest.raises(DeckNotFound):
        store.public_events("other", deck)
    with pytest.raises(DeckNotFound):
        store.public_events(owner, deck, run_id="other-run")
    with db.connect(path) as conn:
        conn.execute("UPDATE build_assistant_public_snapshots SET expires_at=0")
        conn.commit()
    expired = store.public_events(
        owner, deck, snapshot_token=receipt["snapshot_page"]["token"]
    )
    assert expired["mode"] == "reset_required"


def test_failed_answer_is_not_completed_and_started_provider_does_not_imply_interruption(
    state,
):
    _, _, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.checkpoint(
        owner, deck, turn["turn_id"], {"call_id": "one", "state": "started"}
    )
    store.finish(
        owner,
        deck,
        turn["turn_id"],
        {"status": "failed", "content": "Could not finish"},
    )
    public = store.public_events(owner, deck)
    assert public["run"]["lifecycle"] == "failed"
    assert public["operational"]["cost_pending"] is True
    second = store.begin(owner, deck, "Another request", 0)
    store.claim(owner, deck)
    store.checkpoint(
        owner, deck, second["turn_id"], {"call_id": "two", "state": "started"}
    )
    store.finish(
        owner, deck, second["turn_id"], {"status": "done", "content": "Answer"}
    )
    assert store.public_events(owner, deck)["run"]["lifecycle"] == "completed"


def test_compaction_resets_old_cursor_and_retained_baseline_still_replays(
    state, monkeypatch
):
    _, path, owner, deck, store = state
    monkeypatch.setattr(journal, "MAX_RUN_EVENTS", 2)
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.append(
        owner,
        deck,
        turn["turn_id"],
        {"action": {"type": "tool_started", "name": "card_details"}},
    )
    assert (
        store.public_events(owner, deck, after_sequence=0)["mode"] == "reset_required"
    )
    with db.connect(path) as conn:
        row = journal.row_for(conn, turn["turn_id"])
        replay = json.loads(row["public_baseline_json"])
        for (text,) in conn.execute(
            "SELECT event_json FROM build_assistant_events WHERE run_id=? ORDER BY sequence",
            (turn["turn_id"],),
        ):
            replay = reduce_event(replay, json.loads(text))
        assert replay == json.loads(row["public_projection_json"])


def test_legacy_timestamps_not_invented_and_migration_is_idempotent(state):
    _, path, owner, deck, store = state
    store.begin(owner, deck, "Research", 0)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE build_assistant_jobs SET public_projection_json=NULL,public_baseline_json=NULL,last_sequence=0"
        )
        conn.execute("DELETE FROM build_assistant_events")
        conn.commit()
    AssistantStore(path)
    AssistantStore(path)
    public = store.public_events(owner, deck)
    assert public["run"]["accepted_at"] is None
    assert public["run"]["worker_started_at"] is None
    assert public["run"]["legacy_outcome_uncertain"]
    assert public["cursor"]["through_sequence"] == 0


def test_oversized_restart_rolls_back_before_readmission(state):
    _, _, owner, deck, store = state
    first = store.begin(owner, deck, "a" * 14000, 0)
    admitted = []
    with pytest.raises(ValueError, match="16,000"):
        store.steer(
            owner,
            deck,
            {
                **target(first),
                "client_request_id": "too-large",
                "action": "message",
                "message": "b" * 3000,
            },
            0,
            admission=lambda: admitted.append(True),
        )
    assert admitted == []
    assert store.get(owner, deck)["turn_id"] == first["turn_id"]


def test_real_progress_stage_and_unknown_tool_completion_keep_identity(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    for action in [
        {
            "type": "partial_finding",
            "name": "research_progress",
            "data": {"stage": "planning"},
        },
        {"type": "tool_started", "name": "search_card_functions"},
        {"type": "tool_result", "name": "search_card_functions", "ok": True},
    ]:
        store.append(owner, deck, turn["turn_id"], {"action": action})
    with db.connect(path) as conn:
        projection = json.loads(
            journal.row_for(conn, turn["turn_id"])["public_projection_json"]
        )
    activities = projection["entities"]["activity"]
    assert len(activities) == 2
    assert activities[0]["label"] == "Reading the request"
    assert activities[1]["state"] == "completed"


def test_released_executor_claim_is_terminal_and_journaled(state):
    _, _, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.release_claim(owner, deck, turn["turn_id"])
    public = store.public_events(owner, deck)
    assert public["run"]["lifecycle"] == "interrupted"
    assert not store.get(owner, deck)["busy"]
    assert store.claim(owner, deck) is None


def test_nonjournaled_writer_rebaselines_without_reusing_old_event_ids(state):
    _, path, owner, deck, store = state
    store.begin(owner, deck, "Research", 0)
    old = store.public_events(owner, deck)
    with db.connect(path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM build_assistant_sessions WHERE deck_id=?", (deck,)
        ).fetchone()
        legacy = json.loads(row[0])
        legacy["constraints"]["theme"] = "changed by an old writer"
        conn.execute(
            "UPDATE build_assistant_sessions SET payload_json=? WHERE deck_id=?",
            (json.dumps(legacy), deck),
        )
        conn.commit()
    response = store.public_events(
        owner, deck, after_sequence=old["cursor"]["through_sequence"]
    )
    assert response["mode"] == "reset_required"
    assert response["run"]["last_sequence"] > old["run"]["last_sequence"]
    assert response["run"]["legacy_outcome_uncertain"]


def test_shared_public_contract_fixture_and_exported_schemas():
    from pathlib import Path

    from sabermetrics.assistant_events import PublicEvent, PublicRun

    root = Path(__file__).resolve().parents[1]
    trace = json.loads(
        (root / "fixtures/assistant-events-v1/contract-trace.json").read_text()
    )
    replay = trace["baseline"]
    for event in trace["events"]:
        replay = reduce_event(replay, event)
    assert replay == trace["expected"]
    assert (
        json.loads(
            (
                root / "docs/specs/assistant-events-v1/event-envelope.schema.json"
            ).read_text()
        )
        == PublicEvent.model_json_schema()
    )
    assert (
        json.loads(
            (root / "docs/specs/assistant-events-v1/run.schema.json").read_text()
        )
        == PublicRun.model_json_schema()
    )


def test_escaped_answer_segments_remain_wire_bounded_and_exact():
    from sabermetrics.assistant_events import final_entities, serialized

    text = '"\\\n' * 10000
    events = final_entities("r", {"content": text})
    assert "".join(payload["entity"]["text"] for _, payload in events) == text
    assert all(len(serialized(payload).encode()) < 32 * 1024 for _, payload in events)
    assert len({payload["entity"]["logical_text_hash"] for _, payload in events}) == 1
