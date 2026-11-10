"""Conversation ownership, cancellation, provider failures and persistence."""

import threading
import time
from contextlib import contextmanager

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
    from uuid import uuid4

    from sabermetrics.ai_cost_control import Authority, BudgetConfig

    control = Authority(
        str(tmp_path / "cost.sqlite"),
        BudgetConfig(
            prices=(("fake", 1, 1),),
            credentials=(("main", "deepseek", "TEST_MAIN"),),
            authorized=True,
        ),
        local=True,
    )
    client.application.extensions["ai_cost_authority"] = control
    client.application.config["AI_COST_ENVIRONMENT"] = "main"
    client.application.config["DECK_LAB_ASSISTANT_AI_ENABLED"] = True
    post = client.post

    def paid_post(url, **kwargs):
        if url.endswith("/messages") and isinstance(kwargs.get("json"), dict):
            kwargs["json"].setdefault("client_request_id", uuid4().hex)
        return post(url, **kwargs)

    monkeypatch.setattr(client, "post", paid_post)
    executor = RecordingExecutor()
    monkeypatch.setattr(assistant_routes, "_EXECUTOR", executor)
    monkeypatch.setattr(
        assistant_routes, "_WORKER_SLOTS", threading.BoundedSemaphore(2)
    )
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
        "objective": "qualified_opening_rate",
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


def test_polling_does_not_expire_a_queued_job(setup, monkeypatch):
    from sabermetrics import assistant_store

    _client, path, owner, deck, _ = setup
    store = AssistantStore(path)
    started = store.begin(owner, deck, "Inspect", 0)
    monkeypatch.setattr(
        assistant_store.time, "time", lambda: started["started_at"] + 1201
    )
    assert store.get(owner, deck)["busy"]
    assert store.recover(owner, deck)["research_workspace"]["job"]["state"] == "queued"


def test_steering_ack_and_provider_checkpoints_stay_out_of_chat(setup, monkeypatch):
    from sabermetrics import assistant_runtime
    from sabermetrics.assistant_workspace import catalog_card_record, workspace_event

    client, _path, _owner, deck, executor = setup
    url = f"/api/decks/{deck}/assistant"

    def run(*args, **kwargs):
        for status in ("started", "finished"):
            kwargs["on_event"](
                {"type": "provider_checkpoint", "state": status, "call_id": "paid"}
            )
        card = catalog_card_record(
            {"id": "candidate", "name": "Candidate", "oracle_text": "Draw a card."}
        )
        kwargs["on_event"](workspace_event([card]))
        return {"content": "Partial findings", "status": "done"}

    monkeypatch.setattr(assistant_runtime, "run_turn", run)
    assert (
        client.post(
            url + "/messages", json={"message": "Find draw", "expected_revision": 0}
        ).status_code
        == 202
    )
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    session = client.get(url).json
    assert session["research_workspace"]["job"]["checkpoint"]["calls"] == {
        "paid": "finished"
    }
    assert len(session["messages"]) == 3
    body = {
        "action": "prioritize",
        "card_id": "candidate",
        "expected_generation": 1,
        "expected_revision": 0,
        "client_request_id": "once",
    }
    # Completed work closes its original allowance: steering must not silently
    # open a fresh paid group. A deliberate retry admits the new attempt.
    assert client.post(url + "/steer", json=body).status_code == 409
    assert not executor.jobs
    retry = client.post(
        url + "/steer",
        json={
            "action": "retry",
            "expected_generation": 1,
            "expected_revision": 0,
            "client_request_id": "explicit-new-attempt",
        },
    )
    assert retry.status_code == 200
    body["expected_generation"] = 2
    ack = client.post(url + "/steer", json=body)
    assert ack.status_code == 200
    assert ack.json["research_workspace"]["generation"] == 3
    assert ack.json["research_workspace"]["cards"][0]["status"] == "superseded"
    assert ack.json["constraints"]["prioritized_candidates"] == ["candidate"]
    replay = client.post(url + "/steer", json=body)
    assert replay.json["turn_id"] == ack.json["turn_id"]
    assert len(executor.jobs) == 2  # retry predecessor plus its replacement
    assert (
        client.post(url + "/steer", json=dict(body, expected_revision=9)).status_code
        == 409
    )


def test_superseding_inflight_workers_leaves_new_generation_durably_queued(setup):
    client, _path, _owner, deck, executor = setup
    url = f"/api/decks/{deck}/assistant"
    client.post(url + "/messages", json={"message": "Original", "expected_revision": 0})
    for generation in (1, 2):
        response = client.post(
            url + "/steer",
            json={
                "action": "message",
                "message": "Change focus",
                "expected_revision": 0,
                "expected_generation": generation,
                "client_request_id": str(generation),
            },
        )
        assert response.status_code == 200
    assert len(executor.jobs) == 2
    session = client.get(url).json
    assert session["research_workspace"]["generation"] == 3
    assert session["research_workspace"]["job"]["state"] == "queued"
    # The stale worker exits before entering the runtime, freeing a physical slot.
    function, args, kwargs = executor.jobs.pop(0)
    function(*args, **kwargs)
    assert client.get(url).json["research_workspace"]["job"]["state"] == "running"
    assert len(executor.jobs) == 2


@pytest.mark.parametrize("entry", ["session", "messages"])
def test_unrelated_request_drains_legacy_jobs_without_paid_admission(
    setup, monkeypatch, entry
):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    store = AssistantStore(path)
    decks = [_seed_deck(path, owner) for _ in range(4)]
    for queued in decks:
        store.begin(owner, queued, "Persisted research", 0)
    # New store/request, no physical workers and no request to any queued deck.
    assert not executor.jobs
    calls = []
    monkeypatch.setattr(
        assistant_runtime,
        "run_turn",
        lambda *a, **kw: calls.append(a[2]) or {"content": "Done"},
    )
    url = f"/api/decks/{deck}/assistant"
    if entry == "session":
        assert client.get(url).status_code == 200
    else:
        assert (
            client.post(
                url + "/messages", json={"message": "New", "expected_revision": 0}
            ).status_code
            == 429
        )
    assert len(executor.jobs) == 2
    with pytest.raises(ValueError, match="busy"):
        AssistantStore(path).begin(owner, deck, "Fifth job", 0)
    while executor.jobs:
        assert len(executor.jobs) <= 2
        function, args, kwargs = executor.jobs.pop(0)
        function(*args, **kwargs)
    # Legacy queued work has no durable budget admission. Drain it visibly,
    # without executing a provider or silently creating a fresh allowance.
    assert not calls
    for queued in decks:
        finished = AssistantStore(path).get(owner, queued)
        assert not finished["busy"]
        assert finished["research_workspace"]["job"]["state"] == "failed"
        public = AssistantStore(path).public_events(owner, queued)
        assert public["run"]["lifecycle"] == "failed"
        assert "answer" in public["run"]["missing_obligations"]
        assert finished["messages"][-1]["status"] == "failed"
    assert (
        client.post(
            url + "/messages", json={"message": "New", "expected_revision": 0}
        ).status_code
        == 202
    )


def test_global_drain_respects_provider_and_never_replays_expired_running(
    setup, monkeypatch
):
    import time

    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    store = AssistantStore(path)
    expired_deck, queued_deck, stale_deck = [_seed_deck(path, owner) for _ in range(3)]
    old = store.begin(owner, expired_deck, "Unknown paid call", 0)
    store.claim(owner, expired_deck)
    store.checkpoint(
        owner, expired_deck, old["turn_id"], {"call_id": "paid", "state": "started"}
    )
    store.begin(owner, queued_deck, "Safe queued", 0)
    store.begin(owner, stale_deck, "Old revision", 0)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE build_assistant_jobs SET lease_deadline=? WHERE id=?",
            (time.time() - 1, old["turn_id"]),
        )
        conn.execute(
            "UPDATE deck_documents SET revision=revision+1 WHERE id=?", (stale_deck,)
        )
        conn.commit()
    url = f"/api/decks/{deck}/assistant"
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": False})
    assert client.get(url).status_code == 200
    assert not executor.jobs
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": True})
    assert client.get(url).status_code == 200
    assert len(executor.jobs) == 1
    interrupted = store.get(owner, expired_deck)
    assert not interrupted["busy"]
    assert interrupted["research_workspace"]["job"]["state"] == "interrupted"
    assert interrupted["research_workspace"]["job"]["retry_allowed"]
    assert (
        store.get(owner, stale_deck)["research_workspace"]["job"]["state"]
        == "superseded"
    )
    calls = []
    monkeypatch.setattr(
        assistant_runtime,
        "run_turn",
        lambda *a, **kw: calls.append(a[2]) or {"content": "Done"},
    )
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    assert not calls and not executor.jobs
    assert store.get(owner, queued_deck)["messages"][-1]["status"] == "failed"


def test_revision_change_after_final_stop_check_frees_slots_and_drains_queue(
    setup, monkeypatch
):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    store = AssistantStore(path)
    decks = [deck, _seed_deck(path, owner), _seed_deck(path, owner)]
    for queued in decks:
        store.begin(owner, queued, "Research", 0)
    monkeypatch.setattr(
        assistant_runtime, "run_turn", lambda *a, **kw: {"content": "Stale output"}
    )
    finish = AssistantStore.finish

    def race(self, owner_id, deck_id, turn_id, message):
        # _work has already passed its final stopped() check.
        with db.connect(path) as conn:
            conn.execute(
                "UPDATE deck_documents SET revision=revision+1 WHERE id=?", (deck_id,)
            )
            conn.commit()
        return finish(self, owner_id, deck_id, turn_id, message)

    monkeypatch.setattr(AssistantStore, "finish", race)
    assert client.get(f"/api/decks/{deck}/assistant").status_code == 200
    assert len(executor.jobs) == 2
    executed = 0
    while executor.jobs:
        function, args, kwargs = executor.jobs.pop(0)
        function(*args, **kwargs)
        executed += 1
    assert executed == 3
    for item in decks:
        session = store.get(owner, item)
        assert not session["busy"]
        assert session["research_workspace"]["job"]["state"] == "superseded"
        assert [m["content"] for m in session["messages"]] == ["Research"]
    with db.connect(path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM build_assistant_jobs WHERE state IN ('queued','running')"
            ).fetchone()[0]
            == 0
        )


def test_replacement_between_final_stop_check_and_finish_stays_current(
    setup, monkeypatch
):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    url = f"/api/decks/{deck}/assistant"
    monkeypatch.setattr(
        assistant_runtime, "run_turn", lambda *a, **kw: {"content": "Old output"}
    )
    client.post(url + "/messages", json={"message": "Original", "expected_revision": 0})
    finish = AssistantStore.finish
    replacement = {}

    def race(self, owner_id, deck_id, turn_id, message):
        replacement.update(
            self.steer(
                owner_id,
                deck_id,
                {
                    "action": "message",
                    "message": "Replacement",
                    "expected_generation": 1,
                    "client_request_id": "finish-race",
                },
                0,
            )
        )
        return finish(self, owner_id, deck_id, turn_id, message)

    monkeypatch.setattr(AssistantStore, "finish", race)
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    session = AssistantStore(path).get(owner, deck)
    assert session["busy"] and session["turn_id"] == replacement["turn_id"]
    assert session["research_workspace"]["generation"] == 2
    assert session["research_workspace"]["job"]["state"] == "running"
    assert session["messages"] == replacement["messages"]
    assert all(m["role"] == "user" for m in session["messages"])
    # Finish the replacement worker without another replacement race.
    monkeypatch.setattr(AssistantStore, "finish", finish)
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    assert not AssistantStore(path).get(owner, deck)["busy"]


def test_worker_completion_does_not_drain_when_provider_becomes_unavailable(
    setup, monkeypatch
):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    store = AssistantStore(path)
    decks = [deck, _seed_deck(path, owner), _seed_deck(path, owner)]
    for item in decks:
        store.begin(owner, item, "Research", 0)
    client.get(f"/api/decks/{deck}/assistant")
    assert len(executor.jobs) == 2
    monkeypatch.setattr(
        assistant_runtime, "run_turn", lambda *a, **kw: {"content": "Done"}
    )
    monkeypatch.setattr(assistant_routes, "provider_status", lambda: {"enabled": False})
    while executor.jobs:
        function, args, kwargs = executor.jobs.pop(0)
        function(*args, **kwargs)
    assert store.get(owner, decks[2])["research_workspace"]["job"]["state"] == "queued"


@pytest.mark.parametrize("review_kind", ["partial", "not_checked", "abstention_only"])
def test_partial_evidence_review_is_not_reported_as_failed_action(
    setup, monkeypatch, review_kind
):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    revision = DeckDocumentRepo(path).get(owner, deck)["revision"]

    def run(*_args, **kwargs):
        receipt = {
            "status": "unverified",
            "fulfillment": "partial",
            "unresolved": ["support unknown"],
            "claim_judgments": [{"verdict": "supported"}],
        }
        if review_kind == "not_checked":
            receipt = {"status": "not_checked", "reason": "cost_ceiling"}
        elif review_kind == "abstention_only":
            receipt["claim_judgments"] = [
                {"verdict": "unverified", "method": "unreviewed_abstention"}
            ]
        kwargs["on_event"](
            {
                "type": "tool_result",
                "name": "check_answer",
                "ok": False,
                "data": receipt,
            }
        )
        return {
            "content": "Three candidates; suitability remains conditional.",
            "status": "incomplete",
            "reason": "answer_partially_fulfilled",
            "answer_review": receipt,
            "proposals": [],
        }

    monkeypatch.setattr(assistant_runtime, "run_turn", run)
    client.post(
        f"/api/decks/{deck}/assistant/messages",
        json={"message": "Find three candidates", "expected_revision": revision},
    )
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    session = client.get(f"/api/decks/{deck}/assistant").json
    review = next(
        m
        for m in session["messages"]
        if (m.get("action") or {}).get("name") == "check_answer"
    )
    if review_kind == "partial":
        assert "Supported findings remain available" in review["content"]
        assert "Request fulfillment: partial" in review["content"]
    else:
        assert "review unavailable" in review["content"]
        assert "findings remain available" not in review["content"]
        assert "review finished" not in review["content"]
    assert "Could not complete this action" not in review["content"]
    assert session["messages"][-1]["status"] == "incomplete"
    assert session["research_workspace"]["status"] == "partial"


def test_worker_stale_revision_blocks_runtime_without_loading_document(
    setup, monkeypatch
):
    from sabermetrics import assistant_runtime

    client, path, _owner, deck, executor = setup
    client.post(
        f"/api/decks/{deck}/assistant/messages",
        json={"message": "Research", "expected_revision": 0},
    )
    with db.connect(path) as conn:
        conn.execute("UPDATE deck_documents SET revision=1 WHERE id=?", (deck,))
        conn.commit()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Stale work must not load the document or enter the model runtime")

    monkeypatch.setattr(DeckDocumentRepo, "get", forbidden)
    monkeypatch.setattr(assistant_runtime, "run_turn", forbidden)
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    with db.connect(path) as conn:
        assert conn.execute("SELECT state FROM build_assistant_jobs").fetchone()[0] == (
            "superseded"
        )


@pytest.mark.parametrize(
    "change", ["revision", "replacement", "lease", "owner", "deleted"]
)
def test_worker_stop_checks_are_read_only_bounded_and_recheck_authority(
    setup, monkeypatch, change
):
    from sabermetrics import assistant_runtime

    client, path, owner, deck, executor = setup
    client.post(
        f"/api/decks/{deck}/assistant/messages",
        json={"message": "Research", "expected_revision": 0},
    )
    connect = db.connect
    statements = []
    verified = []

    @contextmanager
    def read_only(*args, **kwargs):
        with connect(*args, **kwargs) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.set_trace_callback(statements.append)
            yield conn
            assert conn.total_changes == 0

    def forbidden(*_args, **_kwargs):
        pytest.fail("Stop checks must not hydrate a deck document")

    monkeypatch.setattr(DeckDocumentRepo, "get", forbidden)

    def run(*_args, **kwargs):
        stopped = kwargs["should_stop"]
        with connect(path) as observer:
            version = observer.execute("PRAGMA data_version").fetchone()[0]
            with monkeypatch.context() as patch:
                patch.setattr(db, "connect", read_only)
                for _ in range(5):
                    assert not stopped()
            assert observer.execute("PRAGMA data_version").fetchone()[0] == version
        assert len(statements) == 10
        assert all(sql.startswith("SELECT ") for sql in statements)
        assert not any(
            "snapshot_json" in sql or "deck_entries" in sql for sql in statements
        )
        if change == "replacement":
            AssistantStore(path).steer(
                owner,
                deck,
                {
                    "action": "message",
                    "message": "Replacement",
                    "expected_generation": 1,
                    "client_request_id": "stop-check-replacement",
                },
                0,
            )
        else:
            with connect(path) as conn:
                if change == "revision":
                    conn.execute(
                        "UPDATE deck_documents SET revision=1 WHERE id=?", (deck,)
                    )
                elif change == "lease":
                    conn.execute(
                        "UPDATE build_assistant_jobs SET lease_deadline=? WHERE deck_id=?",
                        (time.time() - 1, deck),
                    )
                elif change == "owner":
                    conn.execute(
                        "UPDATE deck_documents SET owner_id='someone-else' WHERE id=?",
                        (deck,),
                    )
                else:
                    conn.execute("DELETE FROM deck_documents WHERE id=?", (deck,))
                conn.commit()
        with monkeypatch.context() as patch:
            patch.setattr(db, "connect", read_only)
            assert stopped()
            with pytest.raises(RuntimeError, match="no longer active"):
                kwargs["on_event"](
                    {
                        "type": "provider_checkpoint",
                        "state": "started",
                        "call_id": "blocked",
                    }
                )
        verified.append(change)
        return {"content": "Stale output"}

    monkeypatch.setattr(assistant_runtime, "run_turn", run)
    if change in {"owner", "deleted"}:
        # Isolate stop checks from unrelated missing-deck terminalization.
        monkeypatch.setattr(AssistantStore, "invalidate", lambda *a, **kw: None)
    function, args, kwargs = executor.jobs.pop()
    function(*args, **kwargs)
    assert verified == [change]
    if change == "replacement":
        current = AssistantStore(path).get(owner, deck)
        assert current["busy"]
        assert current["research_workspace"]["generation"] == 2
        assert all(m["role"] == "user" for m in current["messages"])
