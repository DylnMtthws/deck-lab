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
    _result_payload,
    candidate_from_document,
    load_provenance,
    map_builder_role,
    prepare_simulation,
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
        measures=("turns until a declared pattern is assembled, playing alone"),
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
        candidate_json=json.dumps({"provenance": provenance.model_dump(mode="json")}),
        simulation_status="simulated",
        simulation_json=_stored_result(probability, unseen),
    )
    return candidate_id


def _store_previous(
    path: Path, owner: str, deck_id: str, probability: float, unseen: int
) -> None:
    repo = db.DeckDocumentSimulationsRepo(path)
    document = DeckDocumentRepo(path).get(owner, deck_id)
    digest, frozen = prepare_simulation(path, document)
    candidate = candidate_from_document(document, load_provenance(path, document))
    payload = json.loads(frozen)
    payload["outcome"] = _result(candidate, probability, unseen).model_dump(mode="json")
    simulation_id = repo.insert(deck_id=deck_id, owner_id=owner, result_json=frozen)
    repo.update(
        simulation_id,
        status="done",
        deck_sha256=digest,
        revision=int(document["revision"]),
        reason="",
        result_json=json.dumps(payload),
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
            CandidateCard(oracle_id="island", name="Island", role="land", quantity=96),
            CandidateCard(oracle_id="ponder", name="Ponder", role="flex", quantity=1),
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

    illegal_id = _seed_deck(path, owner, library_quantity=99, library_color='["R"]')
    illegal = DeckDocumentRepo(path).get(owner, illegal_id)
    assert illegal["validation"]["legal"] is False
    assert illegal["validation"]["total_count"] == 100
    illegal_issue = illegal["validation"]["issues"][0]
    _post(client, illegal_id)
    illegal_body = _latest(client, illegal_id)
    assert illegal_body["reason"] == (
        "Not simulated: deck must be a legal 100-card list " f"({illegal_issue})"
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
    deck_id = _seed_deck(path, owner, source_kind="candidate", source_id=candidate_id)
    document = DeckDocumentRepo(path).get(owner, deck_id)
    candidate = candidate_from_document(document, load_provenance(path, document))
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cedh_candidates SET simulation_json=?, deck_sha256=? WHERE candidate_id=?",
            (
                _result(candidate, 0.2, 7).model_dump_json(),
                candidate.deck_sha256,
                candidate_id,
            ),
        )
        conn.commit()
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


def _replace_lands(path: Path, deck_id: str, count: int) -> None:
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_entries SET quantity=? WHERE deck_id=? AND is_commander=0",
            (99 - count, deck_id),
        )
        conn.execute(
            "INSERT INTO deck_entries (id, deck_id, zone_id, card_id, oracle_id, "
            "name, quantity, is_commander, sort_order, role, type_line, color_identity) "
            "SELECT ?, deck_id, zone_id, 'card-mountain', ?, 'Mountain', ?, "
            "0, 2, 'land', 'Basic Land — Mountain', '[]' FROM deck_entries "
            "WHERE deck_id=? AND is_commander=0 LIMIT 1",
            (db.new_id(), MOUNTAIN, count, deck_id),
        )
        conn.execute(
            "UPDATE deck_documents SET revision=revision+1 WHERE id=?", (deck_id,)
        )
        conn.commit()


def test_queued_run_executes_frozen_document_after_edit(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    original = DeckDocumentRepo(path).get(owner, deck_id)
    fake = FakeSimulator(frozenset({COMMANDER}))
    executor = RecordingExecutor()
    _install(monkeypatch, fake, executor)
    response = _post(client, deck_id)
    run_id = response.get_json()["id"]
    queued = db.DeckDocumentSimulationsRepo(path).get(run_id)
    assert json.loads(queued["result_json"])["snapshot"] == original
    _replace_lands(path, deck_id, 4)
    function, args, kwargs = executor.jobs[0]
    function(*args, **kwargs)
    assert (
        fake.calls[0].deck_sha256
        == candidate_from_document(original, user_edited_provenance()).deck_sha256
    )
    done = db.DeckDocumentSimulationsRepo(path).get(run_id)
    payload = json.loads(done["result_json"])
    assert payload["snapshot"] == original
    assert payload["outcome"]["deck_sha256"] == queued["deck_sha256"]
    assert done["revision"] == original["revision"]
    assert _latest(client, deck_id)["stale"] is True


@pytest.mark.parametrize("count", [1, 3, 4])
def test_native_run_quantity_aware_max_three(tmp_path, monkeypatch, count):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.25)
    _install(monkeypatch, fake, ImmediateExecutor())
    first_id = _post(client, deck_id).get_json()["id"]
    _replace_lands(path, deck_id, count)
    fake.probability = 0.8
    response = client.post(
        f"/api/decks/{deck_id}/simulate", json={"new_baseline": True}
    )
    if count > 3:
        assert response.status_code == 409
        assert "limit 3" in response.get_json()["message"]
        assert "separate deck history" in response.get_json()["message"]
        assert len(fake.calls) == 1
        assert db.DeckDocumentSimulationsRepo(path).latest(deck_id)["id"] == first_id
    else:
        assert response.status_code == 202
        latest = _latest(client, deck_id)
        assert latest["delta"]["before"] == pytest.approx(0.25)
        assert latest["delta"]["after"] == pytest.approx(0.8)
        row = db.DeckDocumentSimulationsRepo(path).latest(deck_id)
        assert json.loads(row["result_json"])["comparison_id"] == first_id


def test_commander_change_rejected_between_native_runs(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    fake = FakeSimulator(frozenset({COMMANDER}))
    _install(monkeypatch, fake, ImmediateExecutor())
    _post(client, deck_id)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_entries SET oracle_id=? WHERE deck_id=? AND is_commander=1",
            (MOUNTAIN, deck_id),
        )
        conn.commit()
    response = client.post(f"/api/decks/{deck_id}/simulate", json={})
    assert response.status_code == 409
    assert "Commander changes" in response.get_json()["message"]
    assert len(fake.calls) == 1


def test_expected_revision_conflict_does_not_enqueue(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    executor = RecordingExecutor()
    _install(monkeypatch, FakeSimulator(frozenset({COMMANDER})), executor)
    revision = DeckDocumentRepo(path).get(owner, deck_id)["revision"]
    response = client.post(
        f"/api/decks/{deck_id}/simulate", json={"expected_revision": revision + 1}
    )
    assert response.status_code == 409
    assert executor.jobs == []
    assert db.DeckDocumentSimulationsRepo(path).latest(deck_id) is None
    assert (
        client.post(
            f"/api/decks/{deck_id}/simulate", json={"expected_revision": revision}
        ).status_code
        == 202
    )


def test_legacy_snapshotless_result_has_no_comparison(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    repo = db.DeckDocumentSimulationsRepo(path)
    run_id = repo.insert(deck_id=deck_id, owner_id=owner)
    repo.update(
        run_id,
        status="done",
        result_json=json.dumps({"outcome": json.loads(_stored_result(0.9, 1))}),
    )
    _install(monkeypatch, FakeSimulator(frozenset({COMMANDER})), ImmediateExecutor())
    _post(client, deck_id)
    assert _latest(client, deck_id)["delta"] is None
    assert (
        _latest(client, deck_id)["baseline_message"]
        == "No earlier simulation to compare"
    )


def test_mismatched_source_candidate_is_not_a_baseline(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    source_id = _save_candidate(path, owner, 0.9, 1)
    deck_id = _seed_deck(path, owner, source_kind="candidate", source_id=source_id)
    _install(monkeypatch, FakeSimulator(frozenset({COMMANDER})), ImmediateExecutor())
    _post(client, deck_id)
    assert _latest(client, deck_id)["delta"] is None


@pytest.mark.parametrize(
    "change", [None, "pack", "cards", "scenario", "games", "sweep", "ablations"]
)
def test_native_delta_requires_same_model_data_configuration(
    tmp_path, monkeypatch, change
):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    fake = FakeSimulator(frozenset({COMMANDER}))
    _install(monkeypatch, fake, ImmediateExecutor())
    original_simulate = fake.simulate

    def simulate(candidate):
        result = original_simulate(candidate)
        raw = {
            "strategy_pack": {
                "id": "derived-generic",
                "version": "1",
                "content_sha256": "pack",
            },
            "card_data": {"manifest_hash": "manifest", "cards_sha256": "cards"},
            "simulation": {
                "games": 20,
                "objective_turn": 3,
                "requested_scenario": {"id": "goldfish", "version": "1"},
                "seed": len(fake.calls),
            },
        }
        if len(fake.calls) > 1:
            if change == "pack":
                raw["strategy_pack"]["content_sha256"] = "changed"
            elif change == "cards":
                raw["card_data"]["manifest_hash"] = "changed"
                raw["card_data"]["cards_sha256"] = "changed"
            elif change == "scenario":
                raw["simulation"]["requested_scenario"]["version"] = "2"
            elif change in {"sweep", "ablations"}:
                raw["simulation"][change] = True if change == "sweep" else ["Sol Ring"]
            elif change == "games":
                result = result.model_copy(update={"games": 21})
        return result.model_copy(update={"raw_document": raw})

    monkeypatch.setattr(fake, "simulate", simulate)
    _post(client, deck_id)
    _replace_lands(path, deck_id, 1)
    _post(client, deck_id)
    latest = _latest(client, deck_id)
    assert (latest["delta"] is None) == (change not in {None, "cards"})
    if change == "cards":
        assert "Catalog changes are not isolated" in latest["delta"]["note"]


@pytest.mark.parametrize("empty_run", [1, 2])
def test_no_authored_assembly_objective_is_unknown_not_zero(
    tmp_path, monkeypatch, empty_run
):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.0)
    _install(monkeypatch, fake, ImmediateExecutor())
    original_simulate = fake.simulate

    def simulate(candidate):
        result = original_simulate(candidate)
        objectives = [] if len(fake.calls) == empty_run else ["authored-pattern"]
        return result.model_copy(
            update={
                "raw_document": {
                    "strategy_pack": {"assembly_objectives": objectives},
                    "card_data": {},
                    "simulation": {},
                }
            }
        )

    monkeypatch.setattr(fake, "simulate", simulate)
    _post(client, deck_id)
    first = _latest(client, deck_id)
    if empty_run == 1:
        assert first["result"]["probability"] is None
        assert "consistency is unknown" in first["result"]["text"]
        assert first["result"]["assembly_objectives"] == []
    else:
        assert first["result"]["probability"] == 0.0
        assert first["result"]["assembly_objectives"] == ["authored-pattern"]
    _post(client, deck_id)
    latest = _latest(client, deck_id)
    assert latest["delta"] is None
    assert "no authored assembly objective" in latest["baseline_message"].lower()
    if empty_run == 2:
        assert latest["result"]["probability"] is None
        assert latest["result"]["turn"] is None
        assert "consistency is unknown" in latest["result"]["text"]


def test_source_candidate_baseline_is_frozen_at_enqueue(tmp_path, monkeypatch):
    client, path, owner = _client(tmp_path)
    source_id = _save_candidate(path, owner, 0.2, 7)
    deck_id = _seed_deck(path, owner, source_kind="candidate", source_id=source_id)
    document = DeckDocumentRepo(path).get(owner, deck_id)
    candidate = candidate_from_document(document, load_provenance(path, document))
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cedh_candidates SET simulation_json=? WHERE candidate_id=?",
            (_result(candidate, 0.2, 7).model_dump_json(), source_id),
        )
        conn.commit()
    fake = FakeSimulator(frozenset({COMMANDER}), probability=0.5)
    executor = RecordingExecutor()
    _install(monkeypatch, fake, executor)
    _post(client, deck_id)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cedh_candidates SET simulation_json=? WHERE candidate_id=?",
            (_result(candidate, 0.8, 7).model_dump_json(), source_id),
        )
        conn.commit()
    function, args, kwargs = executor.jobs[0]
    function(*args, **kwargs)
    assert _latest(client, deck_id)["delta"]["before"] == pytest.approx(0.2)


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
    assert stale["stale_message"] == ("Out of date: the list changed since this run")
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
    assert states["sectionId"] == "goldfish-dialog"
    assert states["title"] == "Goldfish simulator"
    assert states["statusRole"] == "status"
    assert states["running"] == "Simulation queued…"
    assert states["run"] == "Run Goldfish"
    assert states["buttonType"] == "button"
    assert states["csrf"] == "csrf-test"
    assert states["postUrl"] == "/api/decks/deck-1/simulate"
    assert states["delays"] == [1000, 2000]
    assert states["polledStatus"] == "Running Goldfish…"
    assert json.loads(states["postBody"]) == {"expected_revision": 7}
    assert states["dialogOpen"] is True
    assert states["result"] == (
        "goldfish_turns_to_assembly: 0.400 probability by turn 3"
    )
    assert states["missingDiagnostics"] == (
        "Five-test diagnostics are not included in this run; this is the existing "
        "solo assembly check only."
    )
    assert states["delta"] == ("goldfish_turns_to_assembly: 0.250 → 0.400 probability")
    assert states["unseen"] == "unseen_card_count: 7 → 4"
    assert states["rerunAfterResult"] == ""
    assert states["resultAfterRender"] == states["result"]
    assert states["statusAfterRender"] == "Goldfish run complete."
    assert states["runningAfterRender"] == states["running"]
    assert states["disabledAfterRunningRender"] is True

    stale = _harness("stale")
    assert stale["stale"] == "Out of date: the list changed since this run"
    assert stale["rerun"] == "Run Goldfish again"
    assert stale["run"] == "Run Goldfish"
    assert stale["result"]
    assert stale["delta"]

    absence = _harness("absence")
    assert absence["status"] == (
        "Not simulated: commander_unsupported: no simulator model exists for Kinnan"
    )
    assert absence["baseline"] == "No earlier simulation to compare"
    assert absence["result"] == ""
    assert absence["delta"] == ""


def test_native_diagnostics_adapter_preserves_additive_report() -> None:
    candidate = DeckCandidate(
        candidate_id="test-candidate",
        generated_at=datetime.now(UTC),
        commander=CommanderIdentity(
            oracle_ids=(COMMANDER,), names=("Test",), color_identity=("U",)
        ),
        cards=(
            CandidateCard(oracle_id=ISLAND, name="Island", quantity=99, role="land"),
        ),
        role_counts={"land": 99},
        provenance=CandidateProvenance(
            pack_id="fixture", pack_version="1", pack_source="test"
        ),
    )
    original = _result(candidate, 0.5, 0)
    diagnostics = {
        "schema_version": "decklab.goldfish-diagnostics.v1",
        "settings": {"games": 20, "seed": 9, "settings_fingerprint": "stable-settings"},
        "tests": {
            key: {
                "status": "unsupported",
                "metrics": {},
                "limitations": [],
                "evidence": [],
            }
            for key in (
                "opening",
                "development",
                "commander_loss",
                "creature_wipe",
                "finish",
            )
        },
    }
    enriched = original.model_copy(
        update={"raw_document": {"diagnostics": diagnostics}}
    )
    payload = _result_payload(enriched)
    assert payload["diagnostics"] == diagnostics
    assert payload["games"] == 20
    assert "diagnostics" not in _result_payload(original)


def test_ui_renders_five_native_diagnostic_panels_without_inventing_zero() -> None:
    rendered = _harness("diagnostics")
    assert rendered["diagnosticCardCount"] == 5
    text = rendered["diagnosticsText"]
    for title in (
        "Opening hand",
        "Development through the early turns",
        "Commander loss",
        "Creature wipe recovery",
        "Finish",
    ):
        assert title in text
    assert "75% · 15/20 · 95% interval 55–90%" in text
    assert "Not measured · 0/20 observed" in text
    assert "unknown is not zero" in text
    assert "settings-abc" in text
    assert "No comparable diagnostics baseline" not in text
    assert "Not measured · 0%" not in text
    assert "Development Turn 5, Wipe Turn 5, Finish Turn 7" in text
    assert "Partially measured" in text
    assert "Evidence: 20 sampled opening hands" in text
    assert "Limitation: Only authored cards are modeled" in text
    assert "Compare with revision 6" in text
    assert "0.5 → 0.75 probability (change 0.25)" in text
    assert "Not compared: Metric is unmeasured in one or both runs." in text
    assert "Compare with revision 5" in text
    assert "Engine settings changed." in text
    assert "Showing the five most recent prior runs." in text
    assert "no winner verdict is inferred" in text
    assert "(change null)" not in text


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


def test_heldout_comparison_renders_values_uncertainty_and_suppression():
    text = _harness("diagnostics")["diagnosticsText"]
    assert "Independent held-out comparison" in text
    assert "training seed 17" in text and "validation seed 456" in text
    assert "baseline: 50%" in text and "candidate: 60%" in text
    assert "95% interval 40–60%" in text
    assert "descriptive change 0.1" in text
    assert "Not compared: Metric is unmeasured" in text
    assert "No improvement ranking is inferred" in text
    assert "authored subset only" in text


def test_finish_positive_proof_is_primary_and_conditional_rate_hidden():
    text = _harness("diagnostics")["diagnosticsText"]
    assert "Finish positively proven across all trials (lower bound): 10%" in text
    assert "remaining trials are unverified" in text
    assert "Finish proven among evaluated runs (conditional)" not in text
