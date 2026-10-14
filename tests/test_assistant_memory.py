"""Research memory is owner-scoped, read-only, fresh-checked and bounded."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from sabermetrics import db
from sabermetrics.assistant_memory import (
    MAX_MEMORY_CHARS,
    MAX_SUMMARY_CHARS,
    research_memory,
)
from sabermetrics.assistant_store import AssistantStore
from sabermetrics.deck_documents import DeckDocumentRepo
from tests.test_assistant_experiments import complete, create, report

pytest_plugins = ["tests.test_assistant_experiments"]

_FORBIDDEN_OUTCOMES = {"improved", "improvement", "win", "winner", "better"}


def _other_owner(path):
    return db.UsersRepo(path).create(
        email="other@example.test",
        display_name="Other",
        role="user",
        status="active",
    )


def _outcomes(memory):
    return [
        alternative["measured_outcome"]
        for item in memory["experiments"]["items"]
        for alternative in item["alternatives"]
    ]


def test_foreign_owner_reads_not_found_and_no_history(prepared, monkeypatch):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    complete(prepared, monkeypatch)
    AssistantStore(path).settings(owner, deck_id, "suggest", {"theme": "Wizards"})
    other = _other_owner(path)
    memory = research_memory(path, other, deck_id)
    assert memory["status"] == "not_found"
    assert "experiments" not in memory and "preferences" not in memory
    # Same response for a deck that does not exist at all: no existence leak.
    missing = research_memory(path, other, "no-such-deck")
    assert {k: v for k, v in memory.items() if k != "deck_id"} == {
        k: v for k, v in missing.items() if k != "deck_id"
    }


def test_foreign_experiment_rows_on_same_deck_are_never_recalled(prepared):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    other = _other_owner(path)
    payload = create(prepared)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE assistant_experiments SET owner_id=? WHERE id=?",
            (other, payload["id"]),
        )
        conn.commit()
    memory = research_memory(path, owner, deck_id)
    assert memory["status"] == "no_history"
    assert memory["experiments"]["items"] == []
    assert memory["experiments"]["total_for_deck"] == 0


def test_no_history_and_default_preferences_are_explicit(prepared):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    assert research_memory(path, owner, deck_id)["preferences"]["status"] == (
        "no_history"
    )
    AssistantStore(path).get(owner, deck_id)
    memory = research_memory(path, owner, deck_id)
    assert memory["status"] == "no_history"
    assert memory["preferences"]["status"] == "default"
    assert memory["experiments"] == {
        "status": "no_history",
        "returned": 0,
        "total_for_deck": 0,
        "items": [],
    }


def test_saved_preferences_are_recalled_as_unverified(prepared):
    _service, path, owner, deck_id, document, _alternatives = prepared
    protected = next(e for e in document["entries"] if not e["is_commander"])
    AssistantStore(path).settings(
        owner,
        deck_id,
        "automate",
        {
            "theme": "Artifact ramp",
            "power_level": "cEDH",
            "protected_cards": [protected["card_id"], "card-not-in-deck"],
        },
    )
    memory = research_memory(path, owner, deck_id)
    preferences = memory["preferences"]
    assert memory["status"] == "available"
    assert preferences["status"] == "saved"
    assert preferences["mode"] == "automate"
    assert preferences["verification"] == "user_stated_unverified"
    assert preferences["constraints"]["theme"] == "Artifact ramp"
    assert preferences["constraints"]["protected_cards"] == [
        {
            "card_id": protected["card_id"],
            "name": protected["name"],
            "in_current_deck": True,
        },
        {"card_id": "card-not-in-deck", "name": None, "in_current_deck": False},
    ]


def test_current_completed_experiment_is_descriptive_never_a_win(prepared, monkeypatch):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    completed = complete(prepared, monkeypatch)
    memory = research_memory(path, owner, deck_id)
    item = memory["experiments"]["items"][0]
    assert memory["status"] == "available"
    assert memory["improvement_claims"] == "none"
    assert item["id"] == completed["id"]
    assert item["freshness"] == "current"
    assert item["applies_to_current_deck"] is True
    assert len(item["alternatives"]) == 3
    # Fixture reports lack effects/strategy provenance, so held-out checks fail.
    assert set(_outcomes(memory)) == {"descriptive_unvalidated"}
    alternative = item["alternatives"][0]
    assert alternative["verdict"] == "none"
    assert alternative["metrics"] == [
        {
            "test": "opening",
            "metric": "kept",
            "unit": "proportion",
            "before": 0.5,
            "after": 0.5,
            "delta": 0.0,
            "comparable": True,
            "held_out_validated": False,
            "reason": alternative["metrics"][0]["reason"],
        }
    ]
    assert "provenance" in alternative["metrics"][0]["reason"]
    assert alternative["replacements"][0]["incoming_name"] == "Incoming 0"
    assert alternative["replacements"][0]["outgoing_name"] == "Island"
    assert not set(_outcomes(memory)) & _FORBIDDEN_OUTCOMES


def test_adopted_alternative_makes_baseline_stale_and_is_not_improvement(
    prepared, monkeypatch
):
    service, path, owner, deck_id, document, _alternatives = prepared
    completed = complete(prepared, monkeypatch)
    chosen = completed["alternatives"][1]
    service.adopt(owner, completed["id"], chosen["id"], document["revision"])
    memory = research_memory(path, owner, deck_id)
    item = memory["experiments"]["items"][0]
    assert item["freshness"] == "stale"
    assert item["applies_to_current_deck"] is False
    adopted = [a for a in item["alternatives"] if a["adopted"]]
    assert [a["id"] for a in adopted] == [chosen["id"]]
    assert adopted[0]["matches_current_deck"] is True
    assert adopted[0]["measured_outcome"] == "descriptive_unvalidated"
    assert any("not evidence of improvement" in w for w in memory["warnings"])
    assert any("do not describe the current list" in w for w in memory["warnings"])


def test_content_edit_marks_experiment_stale(prepared, monkeypatch):
    _service, path, owner, deck_id, document, _alternatives = prepared
    complete(prepared, monkeypatch)
    entry = next(e for e in document["entries"] if not e["is_commander"])
    DeckDocumentRepo(path).apply_commands(
        owner,
        deck_id,
        expected_revision=document["revision"],
        mutation_id="memory-stale-edit",
        commands=[{"type": "set_quantity", "entry_id": entry["id"], "quantity": 98}],
    )
    memory = research_memory(path, owner, deck_id)
    item = memory["experiments"]["items"][0]
    assert memory["deck"]["deck_sha256"] is None
    assert item["freshness"] == "unverifiable"
    assert item["applies_to_current_deck"] is False


def test_noncomparable_experiment_reports_no_difference(prepared, monkeypatch):
    service, path, owner, deck_id, _document, _alternatives = prepared
    payload = create(prepared)
    calls = {"count": 0}

    def simulate(document, seed, settings):
        calls["count"] += 1
        result = report(seed)
        if calls["count"] > 2:  # every alternative uses a different engine
            result["settings"]["engine_version"] = "v2"
        return result

    monkeypatch.setattr(service, "_simulate", simulate)
    service.execute(owner, payload["id"])
    memory = research_memory(path, owner, deck_id)
    alternatives = memory["experiments"]["items"][0]["alternatives"]
    assert {a["measured_outcome"] for a in alternatives} == {"noncomparable"}
    assert all(a["metrics"] == [] and a["metrics_total"] == 0 for a in alternatives)
    assert all("engine" in a["comparison_reason"] for a in alternatives)
    assert any("not comparable" in w for w in memory["warnings"])


def test_unfinished_and_expired_experiments_are_not_measured_or_rewritten(
    prepared,
):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    payload = create(prepared)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE assistant_experiments SET state='running' WHERE id=?",
            (payload["id"],),
        )
        conn.commit()
        before = conn.execute(
            "SELECT state,payload_json FROM assistant_experiments WHERE id=?",
            (payload["id"],),
        ).fetchone()
    later = datetime.now(UTC) + timedelta(hours=2)
    memory = research_memory(path, owner, deck_id, now=later)
    item = memory["experiments"]["items"][0]
    assert item["status"] == "interrupted_expired"
    assert set(_outcomes(memory)) == {"not_measured"}
    with db.connect(path) as conn:
        after = conn.execute(
            "SELECT state,payload_json FROM assistant_experiments WHERE id=?",
            (payload["id"],),
        ).fetchone()
    assert tuple(after) == tuple(before)


def test_large_payloads_are_bounded_and_oversized_summaries_skipped(
    prepared, monkeypatch
):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    for _ in range(5):
        completed = complete(prepared, monkeypatch)
    with db.connect(path) as conn:
        for row in conn.execute("SELECT id,payload_json FROM assistant_experiments"):
            payload = json.loads(row["payload_json"])
            payload["baseline"]["document"]["padding"] = "x" * 1_000_000
            payload["alternatives"][0]["comparison"]["padding"] = "y" * (
                MAX_SUMMARY_CHARS + 1
            )
            payload["alternatives"][1]["name"] = "n" * 10_000
            conn.execute(
                "UPDATE assistant_experiments SET payload_json=? WHERE id=?",
                (json.dumps(payload), row["id"]),
            )
        conn.commit()
    memory = research_memory(path, owner, deck_id, limit=5)
    encoded = json.dumps(memory)
    assert len(encoded) <= MAX_MEMORY_CHARS
    assert "xxxxxxxxxx" not in encoded and "yyyyyyyyyy" not in encoded
    assert memory["experiments"]["total_for_deck"] == 5
    item = memory["experiments"]["items"][0]
    assert item["payload_chars"] > 1_000_000
    assert item["alternatives"][0]["measured_outcome"] == "unavailable"
    assert "oversized" in item["alternatives"][0]["comparison_reason"]
    assert len(item["alternatives"][1]["name"]) == 200
    assert completed["id"] in {i["id"] for i in memory["experiments"]["items"]}


def test_limit_is_validated_and_respected(prepared, monkeypatch):
    _service, path, owner, deck_id, _document, _alternatives = prepared
    for _ in range(2):
        complete(prepared, monkeypatch)
    assert (
        research_memory(path, owner, deck_id, limit=1)["experiments"]["returned"] == 1
    )
    for bad in (0, 6, True, "3"):
        with pytest.raises(ValueError):
            research_memory(path, owner, deck_id, limit=bad)


def test_unavailable_database_is_reported_and_never_created(tmp_path):
    missing = tmp_path / "nope" / "missing.db"
    memory = research_memory(missing, "owner", "deck")
    assert memory["status"] == "unavailable"
    assert memory["improvement_claims"] == "none"
    assert not missing.exists() and not missing.parent.exists()
