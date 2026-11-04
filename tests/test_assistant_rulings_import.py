"""Offline Scryfall bulk rulings import.

Every ruling here is a SYNTHETIC fixture shaped like Scryfall's bulk rulings
objects; nothing below asserts it is real ruling text.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import socket
import sqlite3
from pathlib import Path

import pytest

import sabermetrics.assistant_rulings_import as rulings_import
from sabermetrics.assistant_rules import retrieve_rules
from sabermetrics.assistant_rulings_import import (
    BulkDescriptor,
    import_rulings,
    read_snapshot,
)
from scripts.prepare_assistant_rulings import main as prepare_main

URL = "https://data.scryfall.io/rulings/rulings-20261017090034.jsonl.gz"
OID_A = "00000000-0000-4000-8000-00000000000a"
OID_B = "00000000-0000-4000-8000-00000000000b"
DESCRIPTOR = {
    "object": "bulk_data",
    "id": "06f54c0b-ab9c-452d-b35a-8297db5eb940",
    "type": "rulings",
    "updated_at": "2026-10-17T09:00:34.555+00:00",
    "uri": "https://api.scryfall.com/bulk-data/06f54c0b-ab9c-452d-b35a-8297db5eb940",
    "name": "Rulings",
    "jsonl_download_uri": URL,
    "compressed_size": 0,
}


def _ruling(oid: str = OID_A, **over) -> dict:
    base = {
        "object": "ruling",
        "oracle_id": oid,
        "source": "wotc",
        "published_at": "2024-01-02",
        "comment": f"Synthetic ruling for {oid[-1]}.",
    }
    return {**base, **over}


RULINGS = [
    _ruling(),
    _ruling(source="scryfall", comment="Synthetic Scryfall note."),
    _ruling(OID_B, published_at="2020-05-06"),
    _ruling(),  # exact duplicate
    _ruling(OID_B, comment=" "),  # whitespace-only, as seen upstream
]


def _jsonl(rows: list[dict]) -> bytes:
    return b"".join(json.dumps(r).encode() + b"\n" for r in rows)


def _write(
    tmp_path: Path, data: bytes, name: str = "rulings.jsonl.gz"
) -> tuple[Path, str]:
    path = tmp_path / name
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def _import(db: Path, path: Path, digest: str, **kw):
    return import_rulings(
        db,
        path,
        expected_sha256=digest,
        source_url=kw.pop("source_url", URL),
        version=kw.pop("version", "synthetic-1"),
        **kw,
    )


def _rows(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT card_oracle_id, ruling_date, ruling_text, source FROM card_rulings ORDER BY id"
        ).fetchall()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE card_rulings (id INTEGER PRIMARY KEY AUTOINCREMENT, card_oracle_id TEXT NOT NULL, ruling_date DATE, ruling_text TEXT NOT NULL, source TEXT DEFAULT 'mtgapi', fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO card_rulings (card_oracle_id, ruling_date, ruling_text, source) VALUES (?,?,?,?)",
            (OID_A, "2019-01-01", "Synthetic legacy mtgapi ruling.", "mtgapi"),
        )
    return path


def test_import_preserves_origin_date_oracle_and_other_sources(
    db: Path, tmp_path: Path
) -> None:
    path, digest = _write(tmp_path, gzip.compress(_jsonl(RULINGS)))
    result = _import(db, path, digest)
    assert (result.rows_count, result.wotc_count, result.scryfall_count) == (3, 2, 1)
    assert result.duplicates_dropped == 1 and result.empty_comments_skipped == 1
    assert result.input_format == "jsonl.gz" and result.content_sha256 == digest
    assert result.other_source_rows == 1 and result.oracle_ids == 2
    rows = _rows(db)
    assert rows[0][3] == "mtgapi"  # untouched legacy row
    assert set(rows[1:]) == {
        (OID_A, "2024-01-02", "Synthetic ruling for a.", "scryfall-bulk:wotc"),
        (OID_A, "2024-01-02", "Synthetic Scryfall note.", "scryfall-bulk:scryfall"),
        (OID_B, "2020-05-06", "Synthetic ruling for b.", "scryfall-bulk:wotc"),
    }
    with sqlite3.connect(db) as conn:
        snap = conn.execute(
            "SELECT source_url, source_version, content_sha256, rows_count FROM card_rulings_snapshots"
        ).fetchone()
    assert snap == (URL, "synthetic-1", digest, 3)


def test_repeat_import_is_idempotent_and_replacement_is_scoped(
    db: Path, tmp_path: Path
) -> None:
    path, digest = _write(tmp_path, gzip.compress(_jsonl(RULINGS)))
    first = _import(db, path, digest)
    before = _rows(db)
    with sqlite3.connect(db) as conn:
        ids = conn.execute("SELECT id FROM card_rulings ORDER BY id").fetchall()
    again = _import(db, path, digest)
    assert again.unchanged and again.imported_at == first.imported_at
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT id FROM card_rulings ORDER BY id").fetchall() == ids
    # A new snapshot replaces only the namespace, never the legacy row.
    path2, digest2 = _write(tmp_path, _jsonl([_ruling(OID_B)]), "next.jsonl")
    replaced = _import(db, path2, digest2, version="synthetic-2")
    assert not replaced.unchanged and replaced.rows_replaced == 3
    assert replaced.input_format == "jsonl"
    rows = _rows(db)
    assert rows[0] == before[0] and len(rows) == 2
    assert rows[1][3] == "scryfall-bulk:wotc"


def test_plain_json_list_snapshot(db: Path, tmp_path: Path) -> None:
    path, digest = _write(tmp_path, json.dumps(RULINGS).encode(), "rulings.json")
    assert _import(db, path, digest).input_format == "json"


@pytest.mark.parametrize(
    "bad",
    [
        _ruling(oracle_id="not-a-uuid"),
        _ruling(oracle_id=OID_A.upper()),
        _ruling(published_at="2024-13-01"),
        _ruling(published_at="2024-01-02T00:00:00"),
        _ruling(published_at="1980-01-01"),
        _ruling(published_at="2999-01-01"),
        _ruling(published_at=20240102),
        _ruling(source="mtgapi"),
        _ruling(comment="Synthetic\x00ruling"),
        _ruling(comment="x" * 8001),
        _ruling(comment=None),
        _ruling(object="card"),
        {**_ruling(), "extra": 1},
    ],
)
def test_malformed_record_rejects_whole_file_and_keeps_old(
    db: Path, tmp_path: Path, bad: dict
) -> None:
    good, good_digest = _write(tmp_path, gzip.compress(_jsonl(RULINGS)), "good.gz")
    _import(db, good, good_digest)
    before = _rows(db)
    path, digest = _write(tmp_path, gzip.compress(_jsonl([_ruling(OID_B), bad])))
    with pytest.raises(ValueError):
        _import(db, path, digest, version="synthetic-2")
    assert _rows(db) == before


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json\n",
        _jsonl(RULINGS)[:-20],  # truncated last record
        gzip.compress(_jsonl(RULINGS))[:-12],  # truncated gzip stream
        gzip.compress(_jsonl(RULINGS)) + b"garbage",  # trailing non-gzip bytes
        gzip.compress(b"\xef\xbb\xbf" + _jsonl(RULINGS)),  # BOM
        json.dumps([_ruling(), "x"]).encode(),
        json.dumps({"data": RULINGS}).encode(),
    ],
)
def test_malformed_files_are_rejected(db: Path, tmp_path: Path, data: bytes) -> None:
    path, digest = _write(tmp_path, data)
    before = _rows(db)
    with pytest.raises(ValueError):
        _import(db, path, digest)
    assert _rows(db) == before


def test_hash_url_and_version_are_required_and_checked(
    db: Path, tmp_path: Path
) -> None:
    path, digest = _write(tmp_path, gzip.compress(_jsonl(RULINGS)))
    with pytest.raises(ValueError, match="SHA-256"):
        _import(db, path, "0" * 64)
    with pytest.raises(ValueError, match="64 hex"):
        _import(db, path, "")
    for url in (
        "http://data.scryfall.io/rulings/x.jsonl.gz",
        "https://data.scryfall.io.evil.example/x.jsonl.gz",
        "https://u:p@data.scryfall.io/x.jsonl.gz",
        "https://data.scryfall.io:8443/x.jsonl.gz",
        "https://data.scryfall.io/x.jsonl.gz?y=1",
        "https://scryfall.com/x.jsonl.gz",
    ):
        with pytest.raises(ValueError, match="Scryfall"):
            _import(db, path, digest, source_url=url)
    for version in ("", " ", "x" * 121, "a\nb"):
        with pytest.raises(ValueError, match="version"):
            _import(db, path, digest, version=version)
    assert len(_rows(db)) == 1


def test_descriptor_must_match_url_size_and_timestamp(db: Path, tmp_path: Path) -> None:
    data = gzip.compress(_jsonl(RULINGS))
    path, digest = _write(tmp_path, data)
    good = BulkDescriptor.model_validate_json(
        json.dumps({**DESCRIPTOR, "compressed_size": len(data)})
    )
    assert (
        _import(db, path, digest, descriptor=good).descriptor_uri == DESCRIPTOR["uri"]
    )
    for change, match in (
        ({"compressed_size": len(data) + 1}, "size"),
        ({"updated_at": "2026-10-18T09:00:34+00:00"}, "timestamp"),
        ({"jsonl_download_uri": URL.replace("2026", "2025")}, "download URI"),
    ):
        bad = BulkDescriptor.model_validate_json(
            json.dumps({**DESCRIPTOR, "compressed_size": len(data), **change})
        )
        with pytest.raises(ValueError, match=match):
            _import(db, path, digest, version="other", descriptor=bad)
    legacy = BulkDescriptor.model_validate_json(
        json.dumps(
            {
                **{k: v for k, v in DESCRIPTOR.items() if k != "jsonl_download_uri"},
                "download_uri": URL,
                "compressed_size": len(data),
            }
        )
    )
    assert not _import(db, path, digest, version="legacy", descriptor=legacy).unchanged


def test_bounds_stop_bombs_long_lines_and_too_many_rows(
    db: Path, tmp_path: Path, monkeypatch
) -> None:
    bomb = gzip.compress(b"{" + b" " * (2 * 1024 * 1024))
    path, digest = _write(tmp_path, bomb)
    monkeypatch.setattr(rulings_import, "MAX_DECOMPRESSED_BYTES", 1024 * 1024)
    monkeypatch.setattr(rulings_import, "MAX_LINE_BYTES", 4 * 1024 * 1024)
    with pytest.raises(ValueError, match="size bound"):
        _import(db, path, digest)
    monkeypatch.setattr(rulings_import, "MAX_LINE_BYTES", 1024)
    long_line, ld = _write(tmp_path, _jsonl([_ruling(comment="y" * 2000)]), "l.jsonl")
    with pytest.raises(ValueError, match="line size"):
        _import(db, long_line, ld)
    monkeypatch.setattr(rulings_import, "MAX_ROWS", 2)
    many, md = _write(tmp_path, _jsonl(RULINGS), "m.jsonl")
    with pytest.raises(ValueError, match="row bound"):
        _import(db, many, md)
    monkeypatch.setattr(rulings_import, "MAX_RAW_BYTES", 10)
    with pytest.raises(ValueError, match="raw size"):
        _import(db, many, md)
    monkeypatch.setattr(rulings_import, "MAX_ROWS", 10_000)
    monkeypatch.setattr(rulings_import, "MAX_RAW_BYTES", 10**9)
    monkeypatch.setattr(rulings_import, "MAX_EMPTY_COMMENTS", 0)
    with pytest.raises(ValueError, match="empty comments"):
        _import(db, many, md)
    assert len(_rows(db)) == 1


def test_hostile_text_is_stored_as_data(db: Path, tmp_path: Path) -> None:
    text = "Synthetic'); DROP TABLE card_rulings; --"
    path, digest = _write(tmp_path, _jsonl([_ruling(comment=text)]), "x.jsonl")
    _import(db, path, digest)
    assert _rows(db)[-1][2] == text


def test_incompatible_table_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "odd.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE card_rulings (oracle TEXT, body TEXT)")
    file, digest = _write(tmp_path, _jsonl(RULINGS), "r.jsonl")
    with pytest.raises(ValueError, match="columns"):
        _import(path, file, digest)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM card_rulings").fetchone() == (0,)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "card_rulings_snapshots" not in tables  # rolled back


def test_retrieval_cites_snapshot_and_original_source(db: Path, tmp_path: Path) -> None:
    path, digest = _write(tmp_path, gzip.compress(_jsonl(RULINGS)))
    _import(db, path, digest)
    card = {"oracle_id": OID_A, "name": "Synthetic Card"}
    evidence = retrieve_rules(db, "Synthetic Card", cards=[card], limit=6)
    hits = [h for h in evidence.hits if h.kind == "card_ruling"]
    snapshot_hits = [h for h in hits if h.source_url == URL]
    assert len(snapshot_hits) == 2 and evidence.rulings_status == "available"
    assert {h.version for h in snapshot_hits} == {"2024-01-02"}
    labels = " ".join(h.source for h in snapshot_hits)
    assert "labeled 'wotc' by Scryfall" in labels and "synthetic-1" in labels
    assert all("not independently verified" in h.source for h in hits)
    # A namespace row count that no longer matches its snapshot is withheld.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM card_rulings WHERE source='scryfall-bulk:scryfall'")
    degraded = retrieve_rules(db, "Synthetic Card", cards=[card], limit=6)
    assert all(h.source_url != URL for h in degraded.hits)
    assert any(
        "inconsistent with its recorded row count" in x for x in degraded.limitations
    )


def test_cli_requires_hash_and_reports(db: Path, tmp_path: Path, capsys) -> None:
    data = gzip.compress(_jsonl(RULINGS))
    path, digest = _write(tmp_path, data)
    desc = tmp_path / "desc.json"
    desc.write_text(json.dumps({**DESCRIPTOR, "compressed_size": len(data)}))
    common = ["--db-path", str(db), "--rulings-file", str(path), "--source-url", URL]
    common += ["--version", "cli", "--descriptor", str(desc)]
    assert prepare_main([*common, "--sha256", "0" * 64]) == 1
    assert "previous rulings kept" in capsys.readouterr().err
    assert prepare_main([*common, "--sha256", digest]) == 0
    assert json.loads(capsys.readouterr().out)["rows_count"] == 3
    desc.write_text('{"object": "bulk_data"}')
    assert prepare_main([*common, "--sha256", digest]) == 1
    assert "invalid descriptor" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        prepare_main(common)  # --sha256 is mandatory


def test_import_is_offline(db: Path, tmp_path: Path, monkeypatch) -> None:
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    path, digest = _write(tmp_path, gzip.compress(_jsonl(RULINGS)))
    _import(db, path, digest)


@pytest.mark.skipif(
    not os.environ.get("SABER_RULINGS_PROBE_FILE"),
    reason="set SABER_RULINGS_PROBE_FILE (and _SHA256) to probe a real snapshot",
)
def test_probe_real_snapshot_parses() -> None:
    fmt, size, _, records = read_snapshot(
        Path(os.environ["SABER_RULINGS_PROBE_FILE"]),
        expected_sha256=os.environ["SABER_RULINGS_PROBE_SHA256"],
    )
    assert size > 0 and records and fmt.startswith("json")
