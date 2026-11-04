"""Offline refresh contracts: evidence inventory and caller-compatible views.

Canonical imports remain evidence alongside the legacy catalog. The index uses
exactly the production search projection; enriched Oracle text is never silently
substituted into an index whose caller still supplies legacy facts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from sabermetrics.assistant_view_index import MAX_TEXT_BYTES, refresh_view_index


def iter_card_file(
    path: str | Path, *, data_object: bool = False
) -> Iterator[dict[str, Any]]:
    """Stream JSON arrays/JSONL (optionally a snapshot ``{data: [...]}``).

    Records and non-data metadata values are bounded to 1 MiB; no artifact-sized
    read, even for malformed or unterminated input. Parsing continues to EOF so
    a malformed suffix aborts publication rather than accepting a partial array.
    """
    with Path(path).open(encoding="utf-8") as stream:
        if Path(path).suffix == ".jsonl":
            while line := stream.readline(MAX_TEXT_BYTES + 1):
                if len(line.encode()) > MAX_TEXT_BYTES:
                    raise ValueError("card record exceeds byte bound")
                if line.strip():
                    card = json.loads(line)
                    if not isinstance(card, dict):
                        raise ValueError("expected card object")
                    yield card
            return
        decoder = json.JSONDecoder()
        buffer = ""
        eof = False

        def fill() -> None:
            nonlocal buffer, eof
            chunk = stream.read(8192)
            eof = not chunk
            buffer += chunk

        def whitespace() -> None:
            nonlocal buffer
            buffer = buffer.lstrip()
            while not buffer and not eof:
                fill()
                buffer = buffer.lstrip()

        def take(token: str) -> None:
            nonlocal buffer
            whitespace()
            if not buffer.startswith(token):
                raise ValueError("invalid card JSON structure")
            buffer = buffer[len(token) :]

        def value() -> Any:
            nonlocal buffer
            whitespace()
            while True:
                try:
                    item, end = decoder.raw_decode(buffer)
                    break
                except json.JSONDecodeError:
                    if eof:
                        raise ValueError("invalid or incomplete card JSON") from None
                    if len(buffer.encode()) > MAX_TEXT_BYTES:
                        raise ValueError("card record exceeds byte bound") from None
                    fill()
            if len(buffer[:end].encode()) > MAX_TEXT_BYTES:
                raise ValueError("card record exceeds byte bound")
            buffer = buffer[end:]
            return item

        def array() -> Iterator[dict[str, Any]]:
            take("[")
            whitespace()
            if buffer.startswith("]"):
                take("]")
                return
            while True:
                card = value()
                if not isinstance(card, dict):
                    raise ValueError("expected card object")  # noqa: TRY004
                yield card
                whitespace()
                if buffer.startswith("]"):
                    take("]")
                    return
                take(",")

        whitespace()
        if data_object and buffer.startswith("{"):
            take("{")
            found = False
            while True:
                key = value()
                if not isinstance(key, str):
                    raise ValueError("expected snapshot property")  # noqa: TRY004
                take(":")
                if key == "data":
                    if found:
                        raise ValueError("duplicate snapshot data")
                    found = True
                    yield from array()
                else:
                    value()
                whitespace()
                if buffer.startswith("}"):
                    take("}")
                    break
                take(",")
            if not found:
                raise ValueError("snapshot has no data array")
        else:
            yield from array()
        whitespace()
        if buffer:
            raise ValueError("trailing data after card array")


def iter_catalog_cards(db: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """Streaming equivalent of card_search._load_catalog's source projection.

    Uses the same SQL, mana-value alias and color parsing. Search-only private
    fields do not participate in view hashes. No cache or catalog-wide list.
    """
    from sabermetrics.card_search import _parse_identity, catalog_load_sql

    cursor = db.execute(catalog_load_sql())
    names = [column[0] for column in cursor.description]
    for row in cursor:
        card = dict(zip(names, row))
        card["mana_value"] = card.pop("cmc", None)
        card["color_identity"], _ = _parse_identity(card.get("color_identity"))
        yield card


def evidence_inventory(db: sqlite3.Connection) -> dict[str, Any]:
    """Counts are explicit about printing vs Oracle vs eligible-view scope."""
    expected = db.execute("SELECT count(*) FROM cards").fetchone()[0]
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    result: dict[str, Any] = {
        "expected": expected,
        "present": 0,
        "missing": expected,
        "identity_mismatched": 0,
        "source_publication_times": [],
        "fetch_times": [],
        "freshness": "partial_or_unknown_not_live_verified",
        "scope": "catalog printing identities; canonical evidence is not the view source",
    }
    if "assistant_card_fact_current" in tables:
        present = db.execute(
            "SELECT count(*) FROM assistant_card_fact_current c JOIN cards k ON k.id=c.card_id JOIN assistant_card_facts f ON f.card_id=c.card_id AND f.snapshot_hash=c.snapshot_hash JOIN assistant_fact_snapshots s ON s.content_hash=c.snapshot_hash"
        ).fetchone()[0]
        mismatches = db.execute(
            "SELECT count(*) FROM assistant_card_fact_current c JOIN cards k ON k.id=c.card_id JOIN assistant_card_facts f ON f.card_id=c.card_id AND f.snapshot_hash=c.snapshot_hash WHERE json_extract(f.payload,'$.oracle_id') != k.oracle_id OR json_extract(f.payload,'$.name') != k.name"
        ).fetchone()[0]
        result.update(
            present=present,
            missing=max(0, expected - present),
            identity_mismatched=mismatches,
        )
        # Aggregate bounds avoid retaining every source date/version in memory.
        row = db.execute(
            "SELECT min(s.observed_at),max(s.observed_at),min(s.retrieved_at),max(s.retrieved_at),count(DISTINCT s.content_hash) FROM assistant_fact_snapshots s JOIN assistant_card_fact_current c ON c.snapshot_hash=s.content_hash"
        ).fetchone()
        result.update(
            source_publication_times=list(row[:2]),
            fetch_times=list(row[2:4]),
            current_snapshot_count=row[4],
        )
    if "card_rulings_snapshots" in tables:
        result["rulings"] = [
            dict(
                zip(
                    (
                        "source_url",
                        "source_version",
                        "content_sha256",
                        "rows_count",
                        "source_publication_at",
                        "imported_at",
                    ),
                    row,
                )
            )
            for row in db.execute(
                "SELECT source_url,source_version,content_sha256,rows_count,descriptor_updated_at,imported_at FROM card_rulings_snapshots LIMIT 16"
            )
        ]
        for ruling_source in result["rulings"]:
            ruling_source["fetched_at"] = None
    return result


def refresh_knowledge(
    db_path: str | Path,
    output: str | Path,
    *,
    encoder: Any = None,
    expected_cards: int | None = None,
    source_publication_at: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Refresh a cached catalog without claiming live or enriched Oracle freshness.

    Caller must prepare the standard card-search schema before using this API.
    A read transaction pins the catalog projection and inventory for this build.
    Canonical/rulings imports are separately atomic and explicitly operator-led.
    """
    if Path(output).resolve() == Path(db_path).resolve():
        raise ValueError("output must differ from catalog database")
    for date in (source_publication_at, fetched_at):
        if date is not None:
            datetime.fromisoformat(date)
    previous_source: dict[str, Any] = {}
    try:
        from sabermetrics.assistant_view_index import metadata

        with closing(
            sqlite3.connect(Path(output).resolve().as_uri() + "?mode=ro", uri=True)
        ) as previous:
            previous_source = metadata(previous).get("source", {})
    except (OSError, ValueError, TypeError, sqlite3.Error):
        pass
    with closing(
        sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("PRAGMA cache_size=-2048")
        db.execute("BEGIN")
        inventory = evidence_inventory(db)
        from sabermetrics.card_search import catalog_load_sql, catalog_revision

        projection_count = db.execute(
            "SELECT count(*) FROM (" + catalog_load_sql() + ")"
        ).fetchone()[0]
        expected = (
            max(projection_count, int(previous_source.get("expected", 0)))
            if expected_cards is None
            else expected_cards
        )
        revision = json.loads(
            json.dumps(catalog_revision(db, str(Path(db_path).resolve())))
        )
        if revision is not None and previous_source.get("catalog_revision") == revision:
            source_publication_at = source_publication_at or previous_source.get(
                "source_publication_at"
            )
            fetched_at = fetched_at or previous_source.get("fetched_at")
        if expected < projection_count:
            raise ValueError("expected cards is below present projection count")
        source = {
            "kind": "cached_legacy_catalog",
            "projection": "card_search.catalog_load_sql/mana_value/parsed_color_identity.v1",
            "catalog_revision": revision,
            "expected": expected,
            "present": projection_count,
            "excluded": inventory["expected"] - projection_count,
            "coverage": (
                "complete_cached_projection"
                if projection_count == expected
                else "partial_cached_projection"
            ),
            "source_publication_at": source_publication_at,
            "fetched_at": fetched_at,
            "canonical_inventory": inventory,
            "live_freshness": "not_verified",
            "notice": "Index embeds legacy caller facts, not enriched snapshot Oracle text. Refresh success is not source publication or fetch time.",
        }
        receipt = refresh_view_index(
            iter_catalog_cards(db), output, encoder=encoder, source=source
        )
        return receipt
