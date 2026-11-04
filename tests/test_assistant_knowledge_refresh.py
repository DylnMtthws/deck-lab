import json
import sqlite3

from sabermetrics.assistant_card_facts import import_snapshot
from sabermetrics.assistant_card_views import build_card_views
from sabermetrics.assistant_knowledge_refresh import (
    iter_catalog_cards,
    refresh_knowledge,
)
from sabermetrics.card_search import (
    _catalog_faces,
    ensure_card_search_schema,
    reset_search_catalog_cache,
)
from tests.test_assistant_card_views import Encoder


def catalog(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE cards(id TEXT PRIMARY KEY,oracle_id TEXT,name TEXT,type_line TEXT,mana_cost TEXT,cmc REAL,oracle_text TEXT,color_identity TEXT,image_uri TEXT,rarity TEXT,is_legal_commander INTEGER,is_legal_in_99 INTEGER)"
        )
        db.executemany(
            "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "p1",
                    "o1",
                    "Example",
                    "Artifact",
                    "{1}",
                    1,
                    "Sacrifice a creature: Draw a card.",
                    "[]",
                    None,
                    "rare",
                    0,
                    1,
                ),
                (
                    "p2",
                    "o2",
                    "Other",
                    "Instant",
                    "{U}",
                    1,
                    "Draw a card.",
                    '["U"]',
                    None,
                    "rare",
                    0,
                    1,
                ),
                (
                    "p3",
                    "o3",
                    "Excluded",
                    "Instant",
                    "{U}",
                    1,
                    "Draw a card.",
                    '["U"]',
                    None,
                    "rare",
                    0,
                    0,
                ),
            ],
        )
        ensure_card_search_schema(db)


def test_projection_is_identical_to_current_caller_and_receipt_is_persisted(tmp_path):
    source = tmp_path / "catalog.sqlite"
    output = tmp_path / "assistant-card-view-vectors.sqlite"
    catalog(source)
    encoder = Encoder()
    with sqlite3.connect(source) as db:
        db.row_factory = sqlite3.Row
        projected = list(iter_catalog_cards(db))
        reset_search_catalog_cache(str(source))
        caller = _catalog_faces(db, str(source))
    assert [build_card_views(c) for c in projected] == [
        build_card_views(c) for c in caller
    ]
    receipt = refresh_knowledge(
        source,
        output,
        encoder=encoder,
        expected_cards=4,
        source_publication_at="2020-01-01",
        fetched_at="2026-01-01",
    )
    assert receipt["counts"] == {
        "expected": 4,
        "present": 2,
        "eligible": 2,
        "indexed": 2,
        "stale": 0,
        "unsupported": 0,
        "failed": 0,
        "excluded": 1,
        "reused_rows": 0,
        "encoded_rows": 6,
    }
    assert receipt["source"]["coverage"] == "partial_cached_projection"
    assert receipt["source"]["source_publication_at"] == "2020-01-01"
    assert receipt["source"]["fetched_at"] == "2026-01-01"
    assert receipt["refresh_success_at"].startswith("20")
    with sqlite3.connect(output) as db:
        assert (
            json.loads(db.execute("SELECT payload FROM metadata").fetchone()[0])
            == receipt
        )
    encoder.calls.clear()
    second = refresh_knowledge(source, output, encoder=encoder)
    assert second["unchanged"]
    assert second["source"]["coverage"] == "partial_cached_projection"
    assert second["source"]["source_publication_at"] == "2020-01-01"
    assert second["source"]["fetched_at"] == "2026-01-01"
    assert second["counts"]["reused_rows"] == 6
    assert not encoder.calls


def test_partial_new_canonical_evidence_never_masquerades_as_view_source(tmp_path):
    source = tmp_path / "catalog.sqlite"
    output = tmp_path / "assistant-card-view-vectors.sqlite"
    catalog(source)
    snapshot = tmp_path / "canonical.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "object": "card",
                    "id": "p1",
                    "oracle_id": "o1",
                    "name": "Example",
                    "oracle_text": "NEW enriched Oracle evidence.",
                }
            ]
        )
    )
    import_snapshot(
        source,
        snapshot,
        observed_at="2026-01-01",
        retrieved_at="2026-10-01",
        version="approved",
    )
    receipt = refresh_knowledge(source, output, encoder=Encoder())
    inventory = receipt["source"]["canonical_inventory"]
    assert inventory["expected"] == 3
    assert inventory["present"] == 1
    assert inventory["missing"] == 2
    assert inventory["source_publication_times"] == ["2026-01-01"] * 2
    assert inventory["fetch_times"] == ["2026-10-01"] * 2
    assert inventory["freshness"] == "partial_or_unknown_not_live_verified"
    assert receipt["source"]["kind"] == "cached_legacy_catalog"
    assert receipt["source"]["live_freshness"] == "not_verified"
    with sqlite3.connect(source) as db:
        assert (
            next(iter_catalog_cards(db))["oracle_text"]
            != "NEW enriched Oracle evidence."
        )


def test_cli_refresh_and_failed_encoder_keep_published_generation(
    tmp_path, monkeypatch, capsys
):
    import sys

    import pytest

    import sabermetrics.reference_layer.strategy_corpus as corpus
    from scripts.refresh_assistant_knowledge import main

    source = tmp_path / "catalog.sqlite"
    output = tmp_path / "assistant-card-view-vectors.sqlite"
    catalog(source)
    encoder = Encoder()
    monkeypatch.setattr(corpus, "local_embedding_service", lambda: encoder)
    monkeypatch.setattr(
        sys, "argv", ["refresh", "--db", str(source), "--expected-cards", "4"]
    )
    main()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "success"
    assert receipt["source"]["coverage"] == "partial_cached_projection"
    original = output.read_bytes()
    with sqlite3.connect(source) as db:
        db.execute("UPDATE cards SET oracle_text='New text' WHERE id='p1'")

    def fail(text):
        raise RuntimeError("simulated encoder failure")

    encoder.embed = fail
    with pytest.raises(SystemExit):
        main()
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "failed"
    assert failure["refresh_success_at"] is None
    assert failure["counts"]["failed"] == 1
    assert failure["previous_generation"] == receipt["generation"]
    assert output.read_bytes() == original


def test_cli_imports_approved_snapshot_and_hashed_rulings_offline(
    tmp_path, monkeypatch, capsys
):
    import hashlib
    import sys

    import sabermetrics.reference_layer.strategy_corpus as corpus
    from scripts.refresh_assistant_knowledge import main

    source = tmp_path / "catalog.sqlite"
    catalog(source)
    snapshot = tmp_path / "cards.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "object": "card",
                    "id": "p1",
                    "oracle_id": "o1",
                    "name": "Example",
                    "oracle_text": "Enriched evidence, separate from index.",
                }
            ]
        )
    )
    rulings = tmp_path / "rulings.json"
    rulings.write_text(
        json.dumps(
            [
                {
                    "object": "ruling",
                    "oracle_id": "00000000-0000-4000-8000-000000000001",
                    "source": "wotc",
                    "published_at": "2020-01-01",
                    "comment": "An approved ruling.",
                }
            ]
        )
    )
    digest = hashlib.sha256(rulings.read_bytes()).hexdigest()
    monkeypatch.setattr(corpus, "local_embedding_service", lambda: Encoder())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "refresh",
            "--db",
            str(source),
            "--snapshot",
            str(snapshot),
            "--snapshot-observed-at",
            "2020-01-01",
            "--snapshot-retrieved-at",
            "2026-01-01",
            "--rulings",
            str(rulings),
            "--rulings-sha256",
            digest,
            "--rulings-source-url",
            "https://data.scryfall.io/rulings/rulings-20261020000000.json",
            "--rulings-version",
            "approved",
        ],
    )
    main()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["imports"]["canonical"]["cards"] == 1
    assert receipt["imports"]["rulings"]["rows_count"] == 1
    inventory = receipt["source"]["canonical_inventory"]
    assert inventory["present"] == 1
    assert inventory["rulings"][0]["imported_at"]
    assert inventory["rulings"][0]["source_publication_at"] is None
    assert inventory["rulings"][0]["fetched_at"] is None
