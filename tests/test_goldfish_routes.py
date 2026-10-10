"""Exercise authenticated diagnostics and immutable submission boundaries."""

import sqlite3
import threading
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.goldfish_experiments import GoldfishExperimentsRepo
from sabermetrics.ui import goldfish_routes
from tests.test_resimulate_delta import _client, _seed_deck


class Recorder:
    def __init__(self):
        self.jobs = []

    def submit(self, *args):
        self.jobs.append(args)


def setup(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    client.application.config["DECK_LAB_GOLDFISH_ENABLED"] = True
    deck = _seed_deck(path, owner)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE cards SET name='Vivi Ornitier' WHERE id='card-cmd'")
        conn.execute(
            "UPDATE deck_entries SET name='Vivi Ornitier' WHERE is_commander=1"
        )
    recorder = Recorder()
    monkeypatch.setattr(goldfish_routes, "_EXECUTOR", recorder)
    monkeypatch.setattr(goldfish_routes, "_CAPACITY", threading.BoundedSemaphore(2))
    monkeypatch.setattr(
        "sabermetrics.goldfish_service.engine_identity", lambda: "a" * 64
    )
    document = DeckDocumentRepo(path).get(owner, deck)
    request = {
        "expected_revision": document["revision"],
        "plan_id": "vivi-affordable",
        "games": 20,
        "seed": 1,
    }
    return client, path, owner, deck, request, recorder


def test_queue_snapshots_before_edit_and_limits_concurrency(tmp_path, monkeypatch):
    client, path, owner, deck, request, recorder = setup(tmp_path, monkeypatch)
    response = client.post(f"/api/decks/{deck}/diagnostics", json=request)
    assert response.status_code == 202, response.json
    row = GoldfishExperimentsRepo(path).get(owner, response.json["id"])
    before = row["document"]["entries"]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE deck_entries SET quantity=90 WHERE is_commander=0 AND deck_id=?",
            (deck,),
        )
    assert (
        GoldfishExperimentsRepo(path).get(owner, row["id"])["document"]["entries"]
        == before
    )
    assert len(recorder.jobs) == 1
    # Invalid decks release capacity rather than starving later requests.
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 400
    )


def test_stale_wrong_plan_and_bad_settings_are_refused(tmp_path, monkeypatch):
    client, path, owner, deck, request, _ = setup(tmp_path, monkeypatch)
    for changed, status in [
        ({"expected_revision": -1}, 409),
        ({"plan_id": "sauron-affordable"}, 400),
        ({"games": 1001}, 400),
        ({"games": True}, 400),
        ({"games": 20.1}, 400),
        ({"seed": -1}, 400),
        ({"hypothesis": "x" * 1001}, 400),
        ({"parent_id": ["bad"]}, 400),
    ]:
        response = client.post(
            f"/api/decks/{deck}/diagnostics", json={**request, **changed}
        )
        assert response.status_code == status, response.json
    assert GoldfishExperimentsRepo(path).history(owner, deck) == []


def test_cross_owner_history_and_queue_are_private(tmp_path, monkeypatch):
    client, path, _owner, deck, request, _ = setup(tmp_path, monkeypatch)
    other = db.UsersRepo(path).create(
        email="other@example.test", display_name="Other", role="user", status="active"
    )
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.get(f"/api/decks/{deck}/diagnostics").status_code == 404
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 404
    )


def test_bounded_queue_and_failed_submission_release_slot(tmp_path, monkeypatch):
    client, _path, _owner, deck, request, _ = setup(tmp_path, monkeypatch)
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 202
    )
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 202
    )
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 429
    )


def test_csrf_is_enforced(tmp_path, monkeypatch):
    client, _, _, deck, request, _ = setup(tmp_path, monkeypatch)
    client.application.config["WTF_CSRF_ENABLED"] = True
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 400
    )


def test_reference_hub_and_all_five_panels_render(tmp_path, monkeypatch):
    client, _, _, deck, _, _ = setup(tmp_path, monkeypatch)
    home = client.get("/goldfish")
    assert home.status_code == 200
    assert home.data.count(b"Import a copy") == 4
    page = client.get(f"/build/deck/{deck}/diagnostics")
    assert page.status_code == 200
    assert b"Goldfish" in page.data
    assert b"csrf" in page.data


def test_starting_new_baseline_cannot_bypass_three_swap_limit(tmp_path, monkeypatch):
    client, path, owner, deck, request, _ = setup(tmp_path, monkeypatch)
    response = client.post(f"/api/decks/{deck}/diagnostics", json=request)
    repo = GoldfishExperimentsRepo(path)
    repo.finish(owner, response.json["id"], {"tests": {}})
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE deck_entries SET quantity=95 WHERE deck_id=? AND is_commander=0",
            (deck,),
        )
        conn.execute(
            "INSERT INTO deck_entries (id,deck_id,zone_id,card_id,oracle_id,name,quantity,is_commander,sort_order,type_line,color_identity) "
            "SELECT ?,deck_id,zone_id,'card-mountain','mountain-oracle-1','Mountain',4,0,2,'Basic Land — Mountain','[]' "
            "FROM deck_entries WHERE deck_id=? AND is_commander=0 LIMIT 1",
            (db.new_id(), deck),
        )
    response = client.post(f"/api/decks/{deck}/diagnostics", json=request)
    assert response.status_code == 400
    assert "replacements" in response.json["error"]
    response = client.post(
        f"/api/decks/{deck}/diagnostics", json={**request, "seed": 5}
    )
    assert response.status_code == 400
    assert "previous settings" in response.json["error"]


def test_goldfish_flag_off_hides_routes_and_navigation(tmp_path, monkeypatch):
    client, _, _, deck, request, _ = setup(tmp_path, monkeypatch)
    client.application.config["DECK_LAB_GOLDFISH_ENABLED"] = False
    for url in [
        "/goldfish",
        f"/build/deck/{deck}/diagnostics",
        f"/api/decks/{deck}/diagnostics",
    ]:
        assert client.get(url).status_code == 404
    assert (
        client.post(f"/api/decks/{deck}/diagnostics", json=request).status_code == 404
    )
    assert b">Diagnostics</a>" not in client.get(f"/build/deck/{deck}").data


def test_database_exception_releases_queue_capacity(tmp_path, monkeypatch):
    client, _, _, deck, request, _ = setup(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(GoldfishExperimentsRepo, "create", fail)
    with pytest.raises(sqlite3.OperationalError):
        client.post(f"/api/decks/{deck}/diagnostics", json=request)
    assert goldfish_routes._CAPACITY.acquire(blocking=False)
    assert goldfish_routes._CAPACITY.acquire(blocking=False)
    assert not goldfish_routes._CAPACITY.acquire(blocking=False)


def test_deleting_deck_deletes_private_experiment_snapshot(tmp_path, monkeypatch):
    client, path, owner, deck, request, _ = setup(tmp_path, monkeypatch)
    result = client.post(f"/api/decks/{deck}/diagnostics", json=request)
    experiment_id = result.json["id"]
    repo = GoldfishExperimentsRepo(path)
    assert repo.get(owner, experiment_id)
    DeckDocumentRepo(path).delete(owner, deck)
    assert repo.get(owner, experiment_id) is None


def test_browser_revision_payload_is_numeric():
    script = Path("src/sabermetrics/ui/static/deck-lab-goldfish.js").read_text()
    assert "expected_revision: Number(state.revision)" in script
