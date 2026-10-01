"""Re-simulate an edited deck and show the goldfish delta."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.cedh.candidate import (
    CandidateCard,
    CandidateProvenance,
    DeckCandidate,
)
from sabermetrics.cedh.domain import CommanderIdentity
from sabermetrics.cedh.simulator import AssemblyPoint, SimulationResult
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.deck_simulation import (
    DeckCandidate as HashOwner,
)
from sabermetrics.deck_simulation import (
    candidate_from_document,
    map_builder_role,
    user_edited_provenance,
)
from sabermetrics.ui import builder_routes
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
SIM_JS = ROOT / "src/sabermetrics/ui/static/deck-lab-simulate.js"
HARNESS = Path(__file__).with_name("resimulate_harness.mjs")

COMMANDER = "cmd-oracle-1"
ISLAND = "island-oracle-1"
MOUNTAIN = "mountain-oracle-1"


class ImmediateExecutor:
    """Run the queued simulation before the response returns."""

    def submit(self, function, *args, **kwargs):
        function(*args, **kwargs)
        return object()


class RecordingExecutor:
    """Remember the job without starting it."""

    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    def submit(self, function, *args, **kwargs):
        self.jobs.append((function, args, kwargs))
        return object()


class FakeSimulator:
    """SimulatorClient stand-in injected in place of the factory."""

    def __init__(
        self,
        keys: frozenset[str],
        *,
        probability: float = 0.5,
        unseen: int = 4,
        block: threading.Event | None = None,
        entered: threading.Event | None = None,
    ) -> None:
        self.keys = keys
        self.probability = probability
        self.unseen = unseen
        self.block = block
        self.entered = entered
        self.calls: list[DeckCandidate] = []
        self.thread_id: int | None = None

    def supported_commander_keys(self) -> frozenset[str]:
        return self.keys

    def simulate(self, candidate: DeckCandidate) -> SimulationResult:
        self.thread_id = threading.get_ident()
        self.calls.append(candidate)
        if self.entered is not None:
            self.entered.set()
        if self.block is not None:
            assert self.block.wait(5)
        return _result(candidate, self.probability, self.unseen)


def _result(
    candidate: DeckCandidate, probability: float, unseen: int
) -> SimulationResult:
    return SimulationResult(
        candidate_id=candidate.candidate_id,
        deck_sha256=candidate.deck_sha256,
        simulation_input_sha256="ab" * 32,
        simulator_version="test",
        games=20,
        objective_turn=3,
        metric="goldfish_turns_to_assembly",
        measures=(
            "turns until a declared pattern is assembled, playing alone"
        ),
        does_not_measure="deck strength, win rate, or card quality",
        assembly=(AssemblyPoint(turn=3, probability=probability),),
        censored_fraction=0.0,
        inert_card_count=unseen,
    )


def _stored_result(probability: float, unseen: int) -> str:
    """A successful result in the shape the lab stores on a candidate."""
    result = SimulationResult(
        candidate_id="baseline",
        deck_sha256="c" * 64,
        simulation_input_sha256="ab" * 32,
        simulator_version="test",
        games=20,
        objective_turn=3,
        metric="goldfish_turns_to_assembly",
        measures="turns until a declared pattern is assembled, playing alone",
        does_not_measure="deck strength, win rate, or card quality",
        assembly=(AssemblyPoint(turn=3, probability=probability),),
        censored_fraction=0.0,
        inert_card_count=unseen,
    )
    return result.model_dump_json()


def _client(tmp_path: Path):
    path = tmp_path / "resim.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="resim@example.test",
        display_name="Resim",
        role="user",
        status="active",
    )
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_REDESIGN_ENABLED=True,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    return client, path, owner


def _install(monkeypatch, simulator: FakeSimulator, executor) -> None:
    monkeypatch.setattr(
        "sabermetrics.deck_simulation.build_simulator_client",
        lambda _settings: (simulator, "fake"),
    )
    monkeypatch.setattr(builder_routes, "_SIM_EXECUTOR", executor)


def _add_card(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    oracle_id: str,
    name: str,
    type_line: str,
    commander: bool,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO cards (id, oracle_id, name, type_line, "
        "color_identity, is_legal_commander, is_legal_in_99) "
        "VALUES (?, ?, ?, ?, '[]', ?, 1)",
        (card_id, oracle_id, name, type_line, 1 if commander else 0),
    )


def _seed_deck(
    path: Path,
    owner: str,
    *,
    library_quantity: int = 99,
    library_color: str = "[]",
    source_kind: str | None = None,
    source_id: str | None = None,
) -> str:
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(
        owner,
        title="Edited list",
        source_kind=source_kind,
        source_id=source_id,
    )
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        _add_card(
            conn,
            card_id="card-cmd",
            oracle_id=COMMANDER,
            name="Kinnan",
            type_line="Legendary Creature — Human",
            commander=True,
        )
        _add_card(
            conn,
            card_id="card-island",
            oracle_id=ISLAND,
            name="Island",
            type_line="Basic Land — Island",
            commander=False,
        )
        _add_card(
            conn,
            card_id="card-mountain",
            oracle_id=MOUNTAIN,
            name="Mountain",
            type_line="Basic Land — Mountain",
            commander=False,
        )
        zone_id = conn.execute(
            "SELECT id FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
            (deck_id,),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO deck_entries "
            "(id, deck_id, zone_id, card_id, oracle_id, name, quantity, "
            "is_commander, sort_order, role, type_line, color_identity) "
            "VALUES (?, ?, NULL, 'card-cmd', ?, 'Kinnan', 1, 1, 0, NULL, "
            "'Legendary Creature — Human', '[]')",
            (db.new_id(), deck_id, COMMANDER),
        )
        conn.execute(
            "INSERT INTO deck_entries "
            "(id, deck_id, zone_id, card_id, oracle_id, name, quantity, "
            "is_commander, sort_order, role, type_line, color_identity) "
            "VALUES (?, ?, ?, 'card-island', ?, 'Island', ?, 0, 1, 'land', "
            "'Basic Land — Island', ?)",
            (db.new_id(), deck_id, zone_id, ISLAND, library_quantity, library_color),
        )
        conn.commit()
    return deck_id


def _save_candidate(path: Path, owner: str, probability: float, unseen: int) -> str:
    candidate_id = "cand-baseline"
    provenance = CandidateProvenance(pack_id="kinnan_basalt", pack_version="1")
    db.CedhCandidatesRepo(path).save(
        candidate_id=candidate_id,
        owner_id=owner,
        pack_id="kinnan_basalt",
        commander_key=COMMANDER,
        commander_name="Kinnan",
        deck_sha256="c" * 64,
        candidate_json=json.dumps(
            {"provenance": provenance.model_dump(mode="json")}
        ),
        simulation_status="simulated",
        simulation_json=_stored_result(probability, unseen),
    )
    return candidate_id


def _store_previous(
    path: Path, owner: str, deck_id: str, probability: float, unseen: int
) -> None:
    repo = db.DeckDocumentSimulationsRepo(path)
    simulation_id = repo.insert(deck_id=deck_id, owner_id=owner)
    repo.update(
        simulation_id,
        status="done",
        deck_sha256="d" * 64,
        revision=0,
        reason="",
        result_json=json.dumps(
            {"outcome": json.loads(_stored_result(probability, unseen))}
        ),
    )


def _post(client, deck_id: str):
    response = client.post(f"/api/decks/{deck_id}/simulate", json={})
    assert response.status_code == 202, response.get_data(as_text=True)
    return response


def _latest(client, deck_id: str) -> dict:
    response = client.get(f"/api/decks/{deck_id}/simulations/latest")
    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, dict)
    return body


def _harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the resimulate harness")
    result = subprocess.run(
        [node, str(HARNESS), str(SIM_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_conversion_uses_library_zones_and_existing_hash() -> None:
    provenance = user_edited_provenance()
    document = {
        "id": "deck-hash",
        "entries": [
            {
                "is_commander": 1,
                "oracle_id": "cmd",
                "name": "Kinnan",
                "quantity": 1,
                "color_identity": ["U", "G"],
                "zone_name": "Command",
            },
            {
                "is_commander": 0,
                "oracle_id": "sol",
                "name": "Sol Ring",
                "quantity": 1,
                "role": "ramp",
                "zone_name": "Unsorted",
            },
            {
                "is_commander": 0,
                "oracle_id": "island",
                "name": "Island",
                "quantity": 96,
                "role": "land",
                "zone_name": "Lands",
            },
            {
                "is_commander": 0,
                "oracle_id": "ponder",
                "name": "Ponder",
                "quantity": 1,
                "role": "other",
                "zone_name": "Unsorted",
            },
            {
                "is_commander": 0,
                "oracle_id": "preordain",
                "name": "Preordain",
                "quantity": 1,
                "role": None,
                "zone_name": "Unsorted",
            },
            {
                "is_commander": 0,
                "oracle_id": "maybe",
                "name": "Brainstorm",
                "quantity": 1,
                "role": "tutor",
                "zone_name": "maybeboard",
            },
        ],
    }
    converted = candidate_from_document(document, provenance)
    by_id = {card.oracle_id: card for card in converted.cards}
    assert set(by_id) == {"sol", "island", "ponder", "preordain"}
    assert by_id["sol"].role == "acceleration"
    assert by_id["island"].role == "land"
    assert by_id["ponder"].role == "flex"
    assert by_id["preordain"].role == "flex"
    assert map_builder_role("counter") == "flex"
    assert map_builder_role("tutor") == "tutor"
    assert map_builder_role("") == "flex"
    assert converted.notes == ("user-edited deck",)
    assert converted.provenance.pack_source == "user-edited deck"
    expected = DeckCandidate(
        candidate_id="independent",
        generated_at=datetime.now(UTC),
        commander=CommanderIdentity(
            oracle_ids=("cmd",),
            names=("Kinnan",),
            color_identity=("U", "G"),
        ),
        cards=(
            CandidateCard(
                oracle_id="sol", name="Sol Ring", role="acceleration", quantity=1
            ),
            CandidateCard(
                oracle_id="island", name="Island", role="land", quantity=96
            ),
            CandidateCard(
                oracle_id="ponder", name="Ponder", role="flex", quantity=1
            ),
            CandidateCard(
                oracle_id="preordain", name="Preordain", role="flex", quantity=1
            ),
        ),
        provenance=provenance,
    )
    assert type(converted) is DeckCandidate
    assert HashOwner is DeckCandidate
    assert DeckCandidate.deck_sha256.fget is type(converted).deck_sha256.fget
    assert converted.deck_sha256 == expected.deck_sha256


def test_illegal_or_wrong_size_deck_not_simulated_with_reason(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    fake = FakeSimulator(frozenset({COMMANDER}))
    _install(monkeypatch, fake, ImmediateExecutor())
    short_id = _seed_deck(path, owner, library_quantity=1)
    short = DeckDocumentRepo(path).get(owner, short_id)
    issue = short["validation"]["issues"][0]
    _post(client, short_id)
    body = _latest(client, short_id)
    assert body["status"] == "not_simulated"
    assert body["reason"] == (
        f"Not simulated: deck must be a legal 100-card list ({issue})"
    )
    assert fake.calls == []

    illegal_id = _seed_deck(
        path, owner, library_quantity=99, library_color='["R"]'
    )
    illegal = DeckDocumentRepo(path).get(owner, illegal_id)
    assert illegal["validation"]["legal"] is False
    assert illegal["validation"]["total_count"] == 100
    illegal_issue = illegal["validation"]["issues"][0]
    _post(client, illegal_id)
    illegal_body = _latest(client, illegal_id)
    assert illegal_body["reason"] == (
        "Not simulated: deck must be a legal 100-card list "
        f"({illegal_issue})"
    )
    assert fake.calls == []


def test_unsupported_commander_not_simulated_with_reason(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    fake = FakeSimulator(frozenset({"some-other-commander"}))
    _install(monkeypatch, fake, ImmediateExecutor())
    deck_id = _seed_deck(path, owner)
    _post(client, deck_id)
    body = _latest(client, deck_id)
    assert body["status"] == "not_simulated"
    assert body["reason"].startswith("Not simulated:")
    assert "commander_unsupported" in body["reason"]
    assert fake.calls == []


def test_run_job_stores_result_and_latest_endpoint_returns_it(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.4, unseen=2)
    _install(monkeypatch, fake, ImmediateExecutor())
    deck_id = _seed_deck(path, owner)
    response = _post(client, deck_id)
    payload = response.get_json()
    assert payload["status"] == "queued"
    assert payload["status_url"].endswith(f"/api/decks/{deck_id}/simulations/latest")
    body = _latest(client, deck_id)
    assert body["status"] == "done"
    assert body["result"]["metric"] == "goldfish_turns_to_assembly"
    assert body["result"]["probability"] == pytest.approx(0.4)
    assert body["result"]["unseen_card_count"] == 2
    assert "goldfish_turns_to_assembly" in body["result"]["text"]
    stored = db.DeckDocumentSimulationsRepo(path).latest(deck_id)
    assert stored is not None
    assert stored["status"] == "done"
    assert stored["deck_sha256"] == fake.calls[0].deck_sha256
    assert int(stored["revision"]) == int(
        DeckDocumentRepo(path).get(owner, deck_id)["revision"]
    )


def test_delta_against_source_candidate_baseline(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    candidate_id = _save_candidate(path, owner, 0.2, 7)
    deck_id = _seed_deck(
        path, owner, source_kind="candidate", source_id=candidate_id
    )
    _store_previous(path, owner, deck_id, 0.9, 1)
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.5, unseen=4)
    _install(monkeypatch, fake, ImmediateExecutor())
    _post(client, deck_id)
    body = _latest(client, deck_id)
    assert body["baseline_message"] == ""
    assert body["delta"]["metric"] == "goldfish_turns_to_assembly"
    assert body["delta"]["unit"] == "probability"
    assert body["delta"]["before"] == pytest.approx(0.2)
    assert body["delta"]["after"] == pytest.approx(0.5)
    assert "→" in body["delta"]["text"]
    assert "goldfish_turns_to_assembly" in body["delta"]["text"]
    assert "probability" in body["delta"]["text"]
    assert body["delta"]["unseen_card_count"]["before"] == 7
    assert body["delta"]["unseen_card_count"]["after"] == 4
    assert "unseen_card_count" in body["delta"]["unseen_card_count"]["text"]
    assert "→" in body["delta"]["unseen_card_count"]["text"]


def test_delta_against_previous_run_when_no_candidate(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    _store_previous(path, owner, deck_id, 0.25, 3)
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.8, unseen=1)
    _install(monkeypatch, fake, ImmediateExecutor())
    _post(client, deck_id)
    body = _latest(client, deck_id)
    assert body["delta"]["before"] == pytest.approx(0.25)
    assert body["delta"]["after"] == pytest.approx(0.8)
    assert body["delta"]["metric"] == "goldfish_turns_to_assembly"
    assert body["delta"]["unseen_card_count"]["before"] == 3
    assert body["delta"]["unseen_card_count"]["after"] == 1
    assert body["baseline_message"] == ""


def test_no_baseline_message(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    fake = FakeSimulator(frozenset({COMMANDER}))
    _install(monkeypatch, fake, ImmediateExecutor())
    _post(client, deck_id)
    body = _latest(client, deck_id)
    assert body["status"] == "done"
    assert body["delta"] is None
    assert body["baseline_message"] == "No earlier simulation to compare"


def test_stale_result_detected_after_edit(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    fake = FakeSimulator(frozenset({COMMANDER}))
    _install(monkeypatch, fake, ImmediateExecutor())
    _post(client, deck_id)
    fresh = _latest(client, deck_id)
    assert fresh["stale"] is False
    assert fresh["stale_message"] == ""
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE deck_entries SET oracle_id=?, name='Mountain', "
            "card_id='card-mountain', type_line='Basic Land — Mountain' "
            "WHERE deck_id=? AND is_commander=0",
            (MOUNTAIN, deck_id),
        )
        conn.commit()
    stale = _latest(client, deck_id)
    assert stale["stale"] is True
    assert stale["stale_message"] == (
        "Out of date: the list changed since this run"
    )
    assert stale["deck_sha256"] == fresh["deck_sha256"]
    current = candidate_from_document(
        DeckDocumentRepo(path).get(owner, deck_id),
        user_edited_provenance(),
    )
    assert stale["deck_sha256"] != current.deck_sha256


def test_simulation_does_not_run_on_request_thread(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    fake = FakeSimulator(
        frozenset({COMMANDER}),
        block=release,
        entered=entered,
    )
    executor = RecordingExecutor()
    _install(monkeypatch, fake, executor)
    deck_id = _seed_deck(path, owner)
    request_thread = threading.get_ident()
    response = client.post(f"/api/decks/{deck_id}/simulate", json={})
    assert response.status_code == 202
    assert not entered.is_set()
    assert fake.calls == []
    assert executor.jobs
    function, args, kwargs = executor.jobs[0]

    def _run() -> None:
        function(*args, **kwargs)

    worker = threading.Thread(target=_run, name="deck-sim-test")
    worker.start()
    assert entered.wait(2)
    assert fake.thread_id is not None
    assert fake.thread_id != request_thread
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    body = _latest(client, deck_id)
    assert body["status"] == "done"
    assert body["result"]["metric"] == "goldfish_turns_to_assembly"


def test_ui_states_running_result_delta_stale_and_absence() -> None:
    states = _harness("states")
    assert states["sectionId"] == "simulation"
    assert states["title"] == "Goldfish simulation"
    assert states["statusRole"] == "status"
    assert states["running"] == "Running"
    assert states["run"] == "Run simulation"
    assert states["buttonType"] == "button"
    assert states["csrf"] == "csrf-test"
    assert states["postUrl"] == "/api/decks/deck-1/simulate"
    assert states["delays"] == [1000, 2000]
    assert states["polledStatus"] == "Running"
    assert states["result"] == (
        "goldfish_turns_to_assembly: 0.400 probability by turn 3"
    )
    assert states["delta"] == (
        "goldfish_turns_to_assembly: 0.250 → 0.400 probability"
    )
    assert states["unseen"] == "unseen_card_count: 7 → 4"
    assert states["rerunAfterResult"] == ""

    stale = _harness("stale")
    assert stale["stale"] == "Out of date: the list changed since this run"
    assert stale["rerun"] == "Re-run"
    assert stale["run"] == "Run simulation"
    assert stale["result"]
    assert stale["delta"]

    absence = _harness("absence")
    assert absence["status"] == (
        "Not simulated: commander_unsupported: no simulator model exists for Kinnan"
    )
    assert absence["baseline"] == "No earlier simulation to compare"
    assert absence["result"] == ""
    assert absence["delta"] == ""


def test_shared_view_read_only(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.4, unseen=2)
    _install(monkeypatch, fake, ImmediateExecutor())
    deck_id = _seed_deck(path, owner)
    _post(client, deck_id)
    token = DeckDocumentRepo(path).create_share(owner, deck_id)
    page = client.get(f"/shared/deck/{token}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-shared="true"' in html
    assert "deck-lab-simulate.js" in html
    assert html.index("deck-lab-builder.js") < html.index("deck-lab-simulate.js")
    assert "Run simulation" not in html
    shared = _harness("shared")
    assert shared["run"] == ""
    assert shared["rerun"] == ""
    assert shared["runHidden"] is True
    assert shared["result"] == (
        "goldfish_turns_to_assembly: 0.400 probability by turn 3"
    )
    assert shared["statusRole"] == "status"
