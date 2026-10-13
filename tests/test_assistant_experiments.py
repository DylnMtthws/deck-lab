"""Experiments freeze alternatives; only a selected adoption edits the source."""

import copy
import json

import pytest

from sabermetrics import db
from sabermetrics.assistant_experiments import ExperimentService
from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound, RevisionConflict
from tests.test_resimulate_delta import _client, _seed_deck


@pytest.fixture
def prepared(tmp_path):
    _browser, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    with db.connect(path) as conn:
        for index in range(4):
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,color_identity,is_legal_in_99) VALUES(?,?,?,?,?,?)",
                (
                    f"incoming-{index}",
                    f"incoming-oracle-{index}",
                    f"Incoming {index}",
                    "Artifact",
                    "[]",
                    1,
                ),
            )
        conn.commit()
    document = DeckDocumentRepo(path).get(owner, deck_id)
    entry = next(item for item in document["entries"] if not item["is_commander"])
    alternatives = [
        {
            "name": f"Plan {index}",
            "rationale": "Unverified theme fit",
            "replacements": [
                {
                    "outgoing_entry_id": entry["id"],
                    "incoming_card_id": f"incoming-{index}",
                }
            ],
        }
        for index in range(3)
    ]
    return ExperimentService(path), path, owner, deck_id, document, alternatives


def create(prepared, constraints=None):
    service, _path, owner, deck, document, alternatives = prepared
    return service.create(
        owner, deck, document["revision"], alternatives, constraints or {}
    )


def report(seed, value=0.5):
    return {
        "schema_version": "native",
        "settings": {
            "games": 2000,
            "seed": seed,
            "engine_version": "v1",
            "settings_fingerprint": f"profile-{seed}",
        },
        "coverage": {"modeled_cards": 100, "inert_cards": 0, "unauthored_cards": 0},
        "tests": {
            "opening": {
                "status": "supported",
                "metrics": {
                    "kept": {
                        "value": value,
                        "unit": "proportion",
                        "denominator": 2000,
                        "ci95": {"low": 0.4, "high": 0.6},
                    }
                },
            }
        },
    }


def complete(prepared, monkeypatch):
    service, _path, owner, _deck, _document, _alternatives = prepared
    payload = create(prepared)
    monkeypatch.setattr(
        service, "_simulate", lambda document, seed, settings: report(seed)
    )
    service.execute(owner, payload["id"])
    return service.get(owner, payload["id"])


def test_three_distinct_frozen_copies_leave_original_byte_equivalent(prepared):
    service, path, owner, deck, original, alternatives = prepared
    before = json.dumps(original, sort_keys=True)
    experiment = create(prepared, {"theme": "Wizard tokens", "power_level": "focused"})
    assert len(experiment["alternatives"]) == 3
    assert len({item["deck_sha256"] for item in experiment["alternatives"]}) == 3
    assert all(
        item["document"]["revision"] == original["revision"]
        for item in experiment["alternatives"]
    )
    assert all(
        item["document"]["validation"]["legal"] for item in experiment["alternatives"]
    )
    assert json.dumps(DeckDocumentRepo(path).get(owner, deck), sort_keys=True) == before
    alternatives[0]["name"] = "mutated caller"
    experiment["baseline"]["document"]["title"] = "mutated result"
    frozen = service.get(owner, experiment["id"])
    assert frozen["alternatives"][0]["name"] == "Plan 0"
    assert frozen["baseline"]["document"]["title"] == original["title"]
    assert frozen["constraints"]["semantic_verification"] == "unverified"
    with db.connect(path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS count FROM deck_documents").fetchone()[
                "count"
            ]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM deck_document_simulations"
            ).fetchone()["count"]
            == 0
        )


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "four_swaps",
        "protected",
        "illegal",
        "identity",
        "copies",
        "sideboard",
        "commander",
    ],
)
def test_invalid_alternative_rejects_entire_creation(prepared, defect):
    service, path, owner, deck, document, alternatives = prepared
    constraints = {}
    if defect == "duplicate":
        alternatives[2] = copy.deepcopy(alternatives[0])
    elif defect == "four_swaps":
        alternatives[2]["replacements"] *= 4
    elif defect == "protected":
        constraints["protected_cards"] = ["card-island"]
    elif defect in {"illegal", "identity"}:
        with db.connect(path) as conn:
            conn.execute(
                "UPDATE cards SET is_legal_in_99=?,color_identity=? WHERE id=?",
                (
                    0 if defect == "illegal" else 1,
                    '["G"]' if defect == "identity" else "[]",
                    "incoming-2",
                ),
            )
            conn.commit()
    elif defect == "copies":
        alternatives[2]["replacements"][0]["incoming_card_id"] = "card-cmd"
    elif defect == "commander":
        alternatives[2]["replacements"][0]["outgoing_entry_id"] = next(
            entry["id"] for entry in document["entries"] if entry["is_commander"]
        )
    else:
        alternatives[2]["replacements"][0][
            "outgoing_entry_id"
        ] = "sideboard-not-library"
    with pytest.raises(ValueError):
        create(prepared, constraints)
    assert service.list_for_deck(owner, deck) == []
    assert DeckDocumentRepo(path).get(owner, deck) == document


def test_budget_unknown_and_total_ceiling_are_enforced(prepared):
    with pytest.raises(ValueError, match="price"):
        create(prepared, {"budget": 200})
    _service, path, _owner, _deck, _document, _alternatives = prepared
    with db.connect(path) as conn:
        cards = conn.execute("SELECT id FROM cards").fetchall()
        for card in cards:
            conn.execute(
                "INSERT INTO card_prices(card_id,snapshot_date,price_usd) VALUES(?,?,?)",
                (card["id"], "2026-10-13", 2),
            )
        conn.commit()
    with pytest.raises(ValueError, match="total deck ceiling"):
        create(prepared, {"budget": 199})
    payload = create(prepared, {"budget": 200})
    assert all(item["price_total_usd"] == 200 for item in payload["alternatives"])


def test_ownership_and_stale_revision(prepared):
    service, _path, owner, deck, document, alternatives = prepared
    payload = create(prepared)
    with pytest.raises(DeckNotFound):
        service.get("foreign", payload["id"])
    with pytest.raises(DeckNotFound):
        service.list_for_deck("foreign", deck)
    with pytest.raises(RevisionConflict):
        service.create(owner, deck, document["revision"] + 1, alternatives, {})


def test_execution_compares_same_baseline_and_independent_profile(
    prepared, monkeypatch
):
    service, path, owner, deck, document, _alternatives = prepared
    payload = create(prepared)
    runs = []

    def simulate(snapshot, seed, settings):
        runs.append((copy.deepcopy(snapshot), seed, copy.deepcopy(settings)))
        return report(seed)

    monkeypatch.setattr(service, "_simulate", simulate)
    service.execute(owner, payload["id"])
    result = service.get(owner, payload["id"])
    assert result["status"] == "done"
    assert len(runs) == 8
    train, heldout = (
        result["settings"]["training_seed"],
        result["settings"]["validation_seed"],
    )
    assert train != heldout
    assert [seed for _, seed, _ in runs] == [train, heldout] * 4
    assert all(settings == runs[0][2] for _, _, settings in runs)
    assert all(item["comparison"]["comparable"] for item in result["alternatives"])
    assert all(
        item["validation"]["status"] == "descriptive" for item in result["alternatives"]
    )
    assert DeckDocumentRepo(path).get(owner, deck) == document
    service.execute(owner, payload["id"])
    assert len(runs) == 8


def test_stop_prevents_next_alternative(prepared, monkeypatch):
    service, _path, owner, _deck, _document, _alternatives = prepared
    payload = create(prepared)
    calls = []
    monkeypatch.setattr(
        service,
        "_simulate",
        lambda document, seed, settings: calls.append(seed) or report(seed),
    )
    service.execute(owner, payload["id"], should_stop=lambda: len(calls) >= 4)
    stopped = service.get(owner, payload["id"])
    assert len(calls) == 4
    assert stopped["status"] == "stopped"
    assert stopped["alternatives"][1]["status"] == "queued"


def test_adoption_is_explicit_atomic_and_exact(prepared, monkeypatch):
    service, path, owner, deck, original, _alternatives = prepared
    payload = complete(prepared, monkeypatch)
    assert DeckDocumentRepo(path).get(owner, deck) == original
    selected = payload["alternatives"][1]
    adopted = service.adopt(owner, payload["id"], selected["id"], original["revision"])
    assert adopted["revision"] == original["revision"] + 1
    assert adopted["validation"]["legal"]
    assert any(entry["card_id"] == "incoming-1" for entry in adopted["entries"])
    assert not any(entry["card_id"] == "incoming-0" for entry in adopted["entries"])
    assert service.get(owner, payload["id"])["adopted_alternative_id"] == selected["id"]
    with pytest.raises(ValueError, match="already adopted"):
        service.adopt(owner, payload["id"], selected["id"], adopted["revision"])


def test_stale_or_now_illegal_adoption_leaves_source_unchanged(prepared, monkeypatch):
    service, path, owner, deck, original, _alternatives = prepared
    payload = complete(prepared, monkeypatch)
    with db.connect(path) as conn:
        conn.execute("UPDATE cards SET is_legal_in_99=0 WHERE id='incoming-0'")
        conn.commit()
    with pytest.raises(ValueError, match="Commander-legal"):
        service.adopt(
            owner, payload["id"], payload["alternatives"][0]["id"], original["revision"]
        )
    assert DeckDocumentRepo(path).get(owner, deck) == original
    changed = DeckDocumentRepo(path).apply_commands(
        owner,
        deck,
        expected_revision=original["revision"],
        mutation_id="manual-edit",
        commands=[{"type": "rename_deck", "title": "Changed"}],
    )
    with pytest.raises(RevisionConflict):
        service.adopt(
            owner, payload["id"], payload["alternatives"][1]["id"], changed["revision"]
        )


def test_prior_source_run_cap_is_not_reset_by_new_request(prepared):
    from sabermetrics.deck_simulation import (
        candidate_from_document,
        user_edited_provenance,
    )
    from tests.test_resimulate_delta import _result

    service, path, owner, deck, original, alternatives = prepared
    candidate = candidate_from_document(original, user_edited_provenance())
    outcome = _result(candidate, 0.5, 0)
    simulations = db.DeckDocumentSimulationsRepo(path)
    run_id = simulations.insert(
        deck_id=deck,
        owner_id=owner,
        deck_sha256=candidate.deck_sha256,
        revision=original["revision"],
        result_json=json.dumps(
            {
                "snapshot": original,
                "provenance": user_edited_provenance().model_dump(mode="json"),
                "outcome": outcome.model_dump(mode="json"),
            }
        ),
    )
    simulations.update(run_id, status="done")
    outgoing = alternatives[0]["replacements"][0]["outgoing_entry_id"]
    zone_id = next(
        entry["zone_id"] for entry in original["entries"] if entry["id"] == outgoing
    )
    commands = [{"type": "set_quantity", "entry_id": outgoing, "quantity": 96}]
    commands += [
        {
            "type": "add_card",
            "card_id": f"incoming-{index}",
            "quantity": 1,
            "zone_id": zone_id,
        }
        for index in range(3)
    ]
    edited = DeckDocumentRepo(path).apply_commands(
        owner,
        deck,
        expected_revision=original["revision"],
        mutation_id="three-manual-swaps",
        commands=commands,
    )
    with pytest.raises(ValueError, match="three"):
        service.validate_replacements(
            owner,
            deck,
            edited["revision"],
            [{"outgoing_entry_id": outgoing, "incoming_card_id": "incoming-3"}],
            {},
        )


def test_stop_before_execute_never_calls_native(prepared, monkeypatch):
    service, _path, owner, _deck, _document, _alternatives = prepared
    payload = create(prepared)
    monkeypatch.setattr(
        service,
        "_simulate",
        lambda *args: pytest.fail("stopped experiment must not run"),
    )
    service.execute(owner, payload["id"], should_stop=lambda: True)
    assert service.get(owner, payload["id"])["status"] == "stopped"


def test_protected_other_printing_preserves_same_oracle(prepared):
    service, path, owner, deck, document, alternatives = prepared
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,is_legal_in_99) VALUES(?,?,?,?)",
            ("protected-island-print", "island-oracle-1", "Island", 1),
        )
        conn.commit()
    with pytest.raises(ValueError, match="Protected"):
        service.validate_replacements(
            owner,
            deck,
            document["revision"],
            alternatives[0]["replacements"],
            {"protected_cards": ["protected-island-print"]},
        )


def test_adoption_rolls_back_every_swap_if_later_command_fails(prepared, monkeypatch):
    from sabermetrics.deck_documents import InvalidCommand

    service, path, owner, deck, source, proposals = prepared
    outgoing = proposals[0]["replacements"][0]["outgoing_entry_id"]
    zone_id = next(
        entry["zone_id"] for entry in source["entries"] if entry["id"] == outgoing
    )
    original = DeckDocumentRepo(path).apply_commands(
        owner,
        deck,
        expected_revision=source["revision"],
        mutation_id="seed-two-targets",
        commands=[
            {"type": "set_quantity", "entry_id": outgoing, "quantity": 98},
            {
                "type": "add_card",
                "card_id": "incoming-3",
                "quantity": 1,
                "zone_id": zone_id,
            },
        ],
    )
    second = next(
        entry["id"] for entry in original["entries"] if entry["card_id"] == "incoming-3"
    )
    alternatives = [
        {
            "name": str(index),
            "replacements": [
                {
                    "outgoing_entry_id": outgoing,
                    "incoming_card_id": f"incoming-{index}",
                },
                {
                    "outgoing_entry_id": second,
                    "incoming_card_id": f"incoming-{(index + 1) % 3}",
                },
            ],
        }
        for index in range(3)
    ]
    payload = service.create(owner, deck, original["revision"], alternatives, {})
    monkeypatch.setattr(
        service, "_simulate", lambda document, seed, settings: report(seed)
    )
    service.execute(owner, payload["id"])
    validator = service._candidate

    def race_legality(*args):
        result = validator(*args)
        with db.connect(path) as conn:
            conn.execute("UPDATE cards SET is_legal_in_99=0 WHERE id='incoming-1'")
            conn.commit()
        return result

    monkeypatch.setattr(service, "_candidate", race_legality)
    with pytest.raises(InvalidCommand):
        service.adopt(
            owner, payload["id"], payload["alternatives"][0]["id"], original["revision"]
        )
    assert DeckDocumentRepo(path).get(owner, deck) == original
    assert service.get(owner, payload["id"])["adopted_alternative_id"] is None


def age_experiment(service, path, owner, payload, status="running"):
    from datetime import UTC, datetime, timedelta

    aged = copy.deepcopy(payload)
    aged["created_at"] = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    aged["status"] = status
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE assistant_experiments SET created_at=?,payload_json=?,state=? WHERE id=? AND owner_id=?",
            (aged["created_at"], json.dumps(aged), status, aged["id"], owner),
        )
        conn.commit()
    return aged


@pytest.mark.parametrize("status", ["queued", "running"])
def test_old_unfinished_experiment_expires_on_get_preserving_partial_reports(
    prepared, status
):
    service, path, owner, _deck, _document, _alternatives = prepared
    payload = create(prepared)
    payload["baseline"]["training_report"] = report(
        payload["settings"]["training_seed"]
    )
    payload["alternatives"][0].update(status="done", training_report=report(17))
    payload["alternatives"][1].update(status="running", training_report=report(17))
    aged = age_experiment(service, path, owner, payload, status)
    expired = service.get(owner, payload["id"])
    assert expired["status"] == "failed"
    assert expired["expiry_reason"] == "interrupted_expired"
    assert "Partial measurements are preserved" in expired["error"]
    assert expired["baseline"]["training_report"] == aged["baseline"]["training_report"]
    assert expired["alternatives"][0] == aged["alternatives"][0]
    assert (
        expired["alternatives"][1]["training_report"]
        == aged["alternatives"][1]["training_report"]
    )
    assert expired["alternatives"][1]["status"] == "failed"
    with pytest.raises(ValueError, match="completed"):
        service.adopt(
            owner,
            payload["id"],
            expired["alternatives"][0]["id"],
            expired["baseline"]["revision"],
        )
    service._save(owner, aged)  # A late worker cannot resurrect an expired run.
    assert service.get(owner, payload["id"])["status"] == "failed"


def test_list_expires_only_old_unfinished_experiments(prepared):
    service, path, owner, deck, _document, _alternatives = prepared
    old = create(prepared)
    age_experiment(service, path, owner, old)
    fresh = create(prepared)
    fresh["status"] = "running"
    service._save(owner, fresh)
    done = create(prepared)
    age_experiment(service, path, owner, done, status="done")
    states = {item["id"]: item["status"] for item in service.list_for_deck(owner, deck)}
    assert states == {old["id"]: "failed", fresh["id"]: "running", done["id"]: "done"}


def test_expired_execution_never_resumes_native_trials(prepared, monkeypatch):
    service, path, owner, _deck, _document, _alternatives = prepared
    payload = create(prepared)
    age_experiment(service, path, owner, payload, status="queued")
    monkeypatch.setattr(
        service, "_simulate", lambda *args: pytest.fail("expired run must not resume")
    )
    service.execute(owner, payload["id"])
    assert service.get(owner, payload["id"])["status"] == "failed"


def test_read_side_expiry_stops_late_worker_before_next_trial(prepared, monkeypatch):
    service, path, owner, _deck, _document, _alternatives = prepared
    payload = create(prepared)
    calls = []

    def expire_during_native(document, seed, settings):
        calls.append(seed)
        current = service.get(owner, payload["id"])
        age_experiment(service, path, owner, current)
        assert service.get(owner, payload["id"])["status"] == "failed"
        return report(seed)

    monkeypatch.setattr(service, "_simulate", expire_during_native)
    service.execute(owner, payload["id"])
    assert len(calls) == 1
    assert service.get(owner, payload["id"])["status"] == "failed"
