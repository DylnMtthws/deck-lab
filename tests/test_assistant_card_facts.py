import json
import sqlite3

import pytest

from sabermetrics.assistant_card_facts import card_facts, import_snapshot


@pytest.fixture
def evidence(tmp_path):
    db = tmp_path / "cards.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE cards (id TEXT PRIMARY KEY,oracle_id TEXT,name TEXT,oracle_text TEXT)"
        )
        con.execute(
            "INSERT INTO cards VALUES ('printing','oracle','Front // Back','old')"
        )
    snapshot = tmp_path / "snapshot.json"
    card = {
        "object": "card",
        "id": "printing",
        "oracle_id": "oracle",
        "name": "Front // Back",
        "card_faces": [
            {"name": "Front", "oracle_text": "front"},
            {"name": "Back", "oracle_text": "back"},
        ],
        "legalities": {"commander": "legal"},
        "prices": {"usd": None},
        "rulings": [
            {
                "oracle_id": "oracle",
                "source": "wotc",
                "published_at": "2020-01-01",
                "comment": "A ruling",
            }
        ],
    }
    snapshot.write_text(json.dumps([card]))
    return db, snapshot, card


def test_faces_rulings_and_distinct_dates(evidence):
    db, snapshot, _ = evidence
    imported = import_snapshot(
        db, snapshot, observed_at="2025-01-01", retrieved_at="2026-01-01", version="v1"
    )
    facts = card_facts(db, "printing")
    assert len(facts["card"]["card_faces"]) == 2
    assert facts["provenance"]["content_hash"] == imported["content_hash"]
    assert facts["provenance"]["observed_at"] == "2025-01-01"
    assert facts["provenance"]["retrieved_at"] == "2026-01-01"
    assert facts["provenance"]["freshness"] == "not_verified_current"
    assert facts["rulings"][0]["oracle_id"] == "oracle"
    assert facts["rulings"][0]["source"] == "wotc"
    assert facts["prices"]["values"]["usd"] is None


def test_unknown_date_and_missing_prices(evidence):
    db, snapshot, card = evidence
    del card["prices"]
    snapshot.write_text(json.dumps([card]))
    assert card_facts(db, "printing")["status"] == "catalog_only"
    import_snapshot(db, snapshot, retrieved_at="2026-01-01")
    facts = card_facts(db, "printing")
    assert facts["provenance"]["date_status"] == "unknown"
    assert facts["prices"] is None
    assert facts["legalities"]["observed_at"] is None


def test_mismatch_is_atomic_and_catalog_change_rejects(evidence):
    db, snapshot, card = evidence
    import_snapshot(db, snapshot)
    before = card_facts(db, "printing")["provenance"]["content_hash"]
    bad = dict(card, id="missing", oracle_id="different")
    snapshot.write_text(json.dumps([dict(card, oracle_text="changed"), bad]))
    with pytest.raises(ValueError):
        import_snapshot(db, snapshot)
    assert card_facts(db, "printing")["provenance"]["content_hash"] == before
    with sqlite3.connect(db) as con:
        con.execute("UPDATE cards SET oracle_id='other'")
    assert card_facts(db, "printing")["status"] == "identity_mismatch"


def test_ruling_identity_and_provenance_rewrite_rejected(evidence):
    db, snapshot, card = evidence
    import_snapshot(db, snapshot)
    with pytest.raises(ValueError, match="provenance"):
        import_snapshot(db, snapshot, observed_at="2025-01-01")
    card["rulings"][0]["oracle_id"] = "other"
    snapshot.write_text(json.dumps([card]))
    with pytest.raises(ValueError, match="ruling"):
        import_snapshot(db, snapshot)


@pytest.mark.parametrize("face", [None, "invalid", 1])
def test_invalid_face_is_clear_error(evidence, face):
    db, snapshot, card = evidence
    card["card_faces"] = [face]
    snapshot.write_text(json.dumps([card]))
    with pytest.raises(ValueError, match="objects"):
        import_snapshot(db, snapshot)


def test_nonstring_date_rejected(evidence):
    db, snapshot, _ = evidence
    with pytest.raises(ValueError, match="Dates"):
        import_snapshot(db, snapshot, observed_at=123)


def test_older_snapshot_requires_explicit_override(evidence):
    db, snapshot, card = evidence
    import_snapshot(db, snapshot, observed_at="2026-01-01")
    card["oracle_text"] = "older text"
    snapshot.write_text(json.dumps([card]))
    with pytest.raises(ValueError, match="Older"):
        import_snapshot(db, snapshot, observed_at="2025-01-01")
    assert card_facts(db, "printing")["provenance"]["observed_at"] == "2026-01-01"
    import_snapshot(db, snapshot, observed_at="2025-01-01", allow_older=True)
    assert card_facts(db, "printing")["provenance"]["observed_at"] == "2025-01-01"


def test_output_budget_notices_and_legacy_fields(evidence):
    db, snapshot, card = evidence
    card["oracle_text"] = "x" * 100000
    card["rulings"] *= 100
    snapshot.write_text(json.dumps([card]))
    import_snapshot(db, snapshot)
    facts = card_facts(db, "printing")
    assert len(json.dumps(facts)) < 24000
    assert facts["truncation"]["characters"] > 0
    assert facts["truncation"]["notice"]
    assert facts["rulings_total"] == 100
    assert all(key in facts["card"] for key in ("image_uri", "is_legal_in_99", "roles"))


def test_failed_initial_import_leaves_no_sidecar(evidence):
    db, snapshot, card = evidence
    card["oracle_id"] = "wrong"
    snapshot.write_text(json.dumps([card]))
    with pytest.raises(ValueError, match="identity"):
        import_snapshot(db, snapshot)
    with sqlite3.connect(db) as con:
        assert not con.execute(
            "SELECT name FROM sqlite_master WHERE name='assistant_fact_snapshots'"
        ).fetchone()


def test_oracle_keyed_catalog_preserves_source_printing(evidence):
    db, snapshot, _card = evidence
    with sqlite3.connect(db) as con:
        con.execute("UPDATE cards SET id='oracle'")
    imported = import_snapshot(db, snapshot)
    facts = card_facts(db, "oracle")
    assert facts["card"]["id"] == "oracle"
    assert facts["provenance"]["printing_id"] == "printing"
    assert facts["prices"]["printing_id"] == "printing"
    with sqlite3.connect(db) as con:
        payload = con.execute("SELECT payload FROM assistant_card_facts").fetchone()[0]
        assert json.loads(payload)["id"] == "printing"
        assert con.execute(
            "SELECT snapshot_hash,catalog_id,printing_id FROM assistant_card_fact_printings"
        ).fetchone() == (imported["content_hash"], "oracle", "printing")


def test_ambiguous_oracle_printings_and_names_rejected(evidence):
    db, snapshot, card = evidence
    with sqlite3.connect(db) as con:
        con.execute("UPDATE cards SET id='oracle'")
    snapshot.write_text(json.dumps([card, dict(card, id="printing2")]))
    with pytest.raises(ValueError, match="Ambiguous"):
        import_snapshot(db, snapshot)
    card["name"] = "Conflicting name"
    snapshot.write_text(json.dumps([card]))
    with pytest.raises(ValueError, match="identity"):
        import_snapshot(db, snapshot)


def test_multiple_exact_printing_rows_allowed(evidence):
    db, snapshot, card = evidence
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO cards VALUES ('printing2','oracle','Front // Back','old')"
        )
    snapshot.write_text(json.dumps([card, dict(card, id="printing2")]))
    assert import_snapshot(db, snapshot)["cards"] == 2


def test_canonical_text_difference_is_reported_not_rejected(evidence):
    db, snapshot, card = evidence
    card["oracle_text"] = "New authoritative snapshot text."
    snapshot.write_text(json.dumps([card]))
    import_snapshot(db, snapshot)
    facts = card_facts(db, "printing")
    assert facts["status"] == "canonical_snapshot"
    assert facts["card"]["oracle_text"] == card["oracle_text"]
    assert facts["provenance"]["catalog_differences"] == ["oracle_text"]
    assert any(
        "recommendations" in note and "stale" in note for note in facts["limitations"]
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT oracle_text FROM cards").fetchone()[0] == "old"
        conn.execute("UPDATE cards SET name='Changed identity'")
    assert card_facts(db, "printing")["status"] == "identity_mismatch"


def test_normalized_representations_do_not_report_false_difference(evidence):
    db, snapshot, card = evidence
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE cards ADD COLUMN color_identity TEXT")
        conn.execute("ALTER TABLE cards ADD COLUMN cmc TEXT")
        conn.execute("ALTER TABLE cards ADD COLUMN mana_cost TEXT")
        conn.execute("ALTER TABLE cards ADD COLUMN type_line TEXT")
        conn.execute(
            "UPDATE cards SET oracle_text='same  text',color_identity='[\"R\",\"U\"]',cmc='3',mana_cost='{1}{U}{R}',type_line='Legendary  Creature'"
        )
    card.update(
        oracle_text="same\ntext",
        color_identity=["U", "R"],
        cmc=3.0,
        mana_cost="{1}{U}{R}",
        type_line="Legendary Creature",
    )
    snapshot.write_text(json.dumps([card]))
    import_snapshot(db, snapshot)
    assert card_facts(db, "printing")["provenance"]["catalog_differences"] == []


def test_snapshot_streams_wrapped_array_without_whole_file_read(evidence, monkeypatch):
    from pathlib import Path

    db, snapshot, card = evidence
    snapshot.write_text(
        json.dumps(
            {"description": "approved snapshot", "data": [card], "version": "v1"}
        )
    )
    monkeypatch.setattr(
        Path, "read_bytes", lambda *a, **k: pytest.fail("whole snapshot read")
    )
    assert import_snapshot(db, snapshot)["cards"] == 1
    assert card_facts(db, "printing")["status"] == "canonical_snapshot"


def test_malformed_stream_suffix_never_changes_current_snapshot(evidence):
    db, snapshot, card = evidence
    previous = import_snapshot(db, snapshot)["content_hash"]
    snapshot.write_text(json.dumps([{**card, "oracle_text": "changed"}]) + " malformed")
    with pytest.raises(ValueError):
        import_snapshot(db, snapshot)
    assert card_facts(db, "printing")["provenance"]["content_hash"] == previous


@pytest.mark.parametrize(
    "factory",
    [
        None,
        sqlite3.Row,
        lambda cursor, row: dict(zip([c[0] for c in cursor.description], row)),
    ],
)
@pytest.mark.parametrize("kind", ["canonical", "catalog", "missing", "mismatch"])
def test_connection_reader_preserves_api_factory_and_transaction(
    evidence, factory, kind
):
    from sabermetrics.assistant_card_facts import card_facts_from_connection

    db, snapshot, _ = evidence
    if kind != "catalog":
        import_snapshot(db, snapshot)
    if kind == "mismatch":
        with sqlite3.connect(db) as writer:
            writer.execute("UPDATE cards SET name='changed'")
    card_id = "absent" if kind == "missing" else "printing"
    expected = card_facts(db, card_id)
    with sqlite3.connect(db) as connection:
        connection.row_factory = factory
        connection.execute("BEGIN")
        assert card_facts_from_connection(connection, card_id) == expected
        assert connection.row_factory is factory
        assert connection.in_transaction
        assert card_facts_from_connection(connection, card_id) == expected
