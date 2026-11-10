"""Regressions for the Claude integrated-review follow-up fixes (offline only)."""

import json

import pytest

from sabermetrics import assistant_event_store as journal
from sabermetrics import db
from sabermetrics.assistant_store import AssistantStore, RunConflict
from tests.test_assistant_public_events import target
from tests.test_resimulate_delta import _client, _seed_deck


@pytest.fixture
def state(tmp_path):
    client, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    return client, path, owner, deck, AssistantStore(path)


def projection(path, run_id):
    with db.connect(path) as conn:
        return json.loads(journal.row_for(conn, run_id)["public_projection_json"])


def progress(stage):
    return {
        "action": {
            "type": "partial_finding",
            "name": "research_progress",
            "data": {"stage": stage},
        }
    }


def test_stage_transition_closes_prior_stage_and_terminal_closes_all(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Compare these", 0)
    store.claim(owner, deck)
    store.append(owner, deck, turn["turn_id"], progress("planning"))
    store.append(owner, deck, turn["turn_id"], progress("comparison"))
    store.append(
        owner,
        deck,
        turn["turn_id"],
        {"action": {"type": "tool_started", "name": "check_answer"}},
    )
    stages = {
        a["operation_key"]: a
        for a in projection(path, turn["turn_id"])["entities"]["activity"]
    }
    assert stages["planning"]["state"] == "completed"
    assert stages["planning"]["ended_at"]
    assert stages["comparison"]["state"] == "started"
    store.finish(
        owner,
        deck,
        turn["turn_id"],
        {"content": "Answer", "status": "done", "answer_review": {}},
    )
    activity = projection(path, turn["turn_id"])["entities"]["activity"]
    assert all(a["state"] != "started" for a in activity)
    by_key = {a["operation_key"]: a["state"] for a in activity}
    assert by_key["comparison"] == "completed"
    # A tool whose result never arrived is not claimed as completed.
    assert by_key["check_answer"] == "unavailable"


def test_failed_terminal_marks_open_activity_failed(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.append(owner, deck, turn["turn_id"], progress("planning"))
    store.finish(
        owner,
        deck,
        turn["turn_id"],
        {"content": "Failure notice", "status": "failed"},
    )
    activity = projection(path, turn["turn_id"])["entities"]["activity"]
    assert [a["state"] for a in activity] == ["failed"]


def test_legacy_complete_with_unassessed_obligations_is_unknown_not_ready(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Suggest three ramp cards", 0)
    store.claim(owner, deck)
    store.finish(
        owner,
        deck,
        turn["turn_id"],
        {
            "content": "Three cards with reasons.",
            "status": "done",
            "answer_review": {
                "fulfillment": "complete",
                "claim_judgments": [{"verdict": "supported"}],
            },
        },
    )
    run = projection(path, turn["turn_id"])["run"]
    assert run["lifecycle"] == "completed"
    assert run["legacy_fulfillment"] == "complete"
    assert run["fulfillment"] == "unknown"
    assert run["fulfillment_reason"]
    # No claim/citation entities are published, so coverage is unavailable.
    assert run["evidence_status"] == "unavailable"


def test_steering_message_against_terminal_origin_is_rejected(state):
    _, _, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.finish(owner, deck, turn["turn_id"], {"content": "Answer", "status": "done"})
    with pytest.raises(RunConflict):
        store.steer(
            owner,
            deck,
            {
                **target(turn),
                "client_request_id": "late-steer",
                "action": "message",
                "message": "Also check conditions",
            },
            0,
        )
    assert store.get(owner, deck)["turn_id"] == turn["turn_id"]


def test_historical_run_with_old_writer_terminal_state_is_rebaselined(state):
    _, path, owner, deck, store = state
    first = store.begin(owner, deck, "Research", 0)
    assert store.public_events(owner, deck)["run"]["lifecycle"] == "queued"
    # A rolled-back writer ends the run without journaling a run.ended event.
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE build_assistant_jobs SET state='superseded' WHERE id=?",
            (first["turn_id"],),
        )
        conn.commit()
    response = store.public_events(owner, deck, run_id=first["turn_id"])
    assert response["run"]["lifecycle"] == "superseded"
    # A consistent run is not rebaselined on every read.
    again = store.public_events(owner, deck, run_id=first["turn_id"])
    assert again["run"]["last_sequence"] == response["run"]["last_sequence"]


def test_superseded_run_withdraws_published_findings(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    with db.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        journal.upsert(
            conn,
            turn["turn_id"],
            "finding.upserted",
            "findings",
            {
                "id": "f1",
                "run_id": turn["turn_id"],
                "type": "printed_fact",
                "text": "Fact",
                "status": "published",
                "version": 1,
            },
        )
        conn.commit()
    store.settings(owner, deck, "automate", {}, expected=target(turn))
    findings = projection(path, turn["turn_id"])["entities"]["findings"]
    assert projection(path, turn["turn_id"])["run"]["lifecycle"] == "superseded"
    assert [(f["status"], f["version"]) for f in findings] == [("superseded", 2)]


def test_journal_running_totals_match_actual_rows(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    for stage in ("planning", "comparison", "verification"):
        store.append(owner, deck, turn["turn_id"], progress(stage))
    store.finish(owner, deck, turn["turn_id"], {"content": "Answer", "status": "done"})

    def check(conn):
        actual = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(event_json AS BLOB))),0) "
            "FROM build_assistant_events WHERE deck_id=?",
            (deck,),
        ).fetchone()
        totals = conn.execute(
            "SELECT event_count,event_bytes FROM build_assistant_event_totals WHERE deck_id=?",
            (deck,),
        ).fetchone()
        run = journal.row_for(conn, turn["turn_id"])
        assert tuple(totals) == tuple(actual)
        assert run["retained_event_count"] == actual[0]
        assert run["retained_event_bytes"] == actual[1]
        return actual[0]

    with db.connect(path) as conn:
        assert check(conn) > 0
        conn.execute("BEGIN IMMEDIATE")
        removed = journal.discard_events(conn, turn["turn_id"])
        conn.commit()
        assert removed > 0
        assert check(conn) == 0


def test_client_latency_route_dedupes_and_never_changes_lifecycle(state):
    client, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    run = store.public_events(owner, deck)["run"]
    url = f"/api/decks/{deck}/assistant/latency"
    body = {
        "run_id": run["run_id"],
        "expected_run_id": run["run_id"],
        "expected_generation": run["generation"],
        "expected_revision": run["deck_revision"],
        "clock_domain": "client_monotonic",
        "kind": "first_useful",
        "first_useful": True,
        "received_to_rendered_seconds": 0.25,
    }
    first = client.post(url, json=body)
    assert first.status_code == 200 and first.json["recorded"] is True
    assert client.post(url, json=body).json["recorded"] is False
    stale = client.post(
        url,
        json={
            **body,
            "expected_generation": 99,
            "kind": "terminal",
            "first_useful": False,
        },
    )
    assert stale.status_code == 409
    bad = client.post(url, json={**body, "kind": "terminal"})
    assert bad.status_code == 400
    after = store.public_events(owner, deck)["run"]
    assert after["lifecycle"] == run["lifecycle"]
    assert after["last_sequence"] == run["last_sequence"]
    with db.connect(path) as conn:
        checkpoint = json.loads(
            journal.row_for(conn, turn["turn_id"])["checkpoint_json"]
        )
    assert set(checkpoint["client_latency"]) == {"first_useful"}


def test_reviewer_named_missing_obligations_survive_assessment():
    from sabermetrics.assistant_events import review_outcome
    from sabermetrics.assistant_obligations import build_obligations

    obligations = build_obligations("Suggest three ramp cards and explain why", 0)
    outcome = review_outcome(
        {
            "content": "x",
            "answer_review": {
                "fulfillment": "partial",
                "missing_obligations": ["explain why"],
            },
        },
        obligations.model_dump(),
    )
    assert outcome["fulfillment"] == "partial"
    assert "explain why" in outcome["missing_obligations"]


def test_interrupted_run_marks_open_activity_unavailable_not_failed(state):
    _, path, owner, deck, store = state
    turn = store.begin(owner, deck, "Research", 0)
    store.claim(owner, deck)
    store.append(owner, deck, turn["turn_id"], progress("planning"))
    store.release_claim(owner, deck, turn["turn_id"])
    activity = projection(path, turn["turn_id"])["entities"]["activity"]
    assert projection(path, turn["turn_id"])["run"]["lifecycle"] == "interrupted"
    assert [a["state"] for a in activity] == ["unavailable"]
