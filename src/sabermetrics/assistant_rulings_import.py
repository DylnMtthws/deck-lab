"""Offline import of an explicit Scryfall bulk rulings snapshot into card_rulings.

No download, API or model call happens here. The operator supplies a file that
was fetched elsewhere, its SHA-256, its HTTPS Scryfall source URL and a version
label. Rows this importer owns carry ``source`` values in the ``scryfall-bulk``
namespace (``scryfall-bulk:wotc`` / ``scryfall-bulk:scryfall``, preserving the
ruling's original source as Scryfall labels it); all other ``card_rulings`` rows
are left alone. Snapshot identity lives in ``card_rulings_snapshots``.

Scryfall's ``wotc`` label is recorded, never independently authenticated.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import sqlite3
import zlib
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

NAMESPACE = "scryfall-bulk"
NAMESPACE_SOURCES = (f"{NAMESPACE}:scryfall", f"{NAMESPACE}:wotc")
SCRYFALL_HOSTS = frozenset({"api.scryfall.com", "data.scryfall.io"})
MAX_RAW_BYTES = 64 * 1024 * 1024
# Bounds decompression (gzip bombs) and the one whole-text JSON-list parse.
MAX_DECOMPRESSED_BYTES = 96 * 1024 * 1024
MAX_LINE_BYTES = 32 * 1024
MAX_ROWS = 400_000
MAX_COMMENT_CHARS = 8_000
# More blank comments than this is a broken export, not upstream noise.
MAX_EMPTY_COMMENTS = 50
_CHUNK = 1024 * 1024
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_URL_STAMP = re.compile(r"-(\d{14})\.[a-z.]+$")
_FIRST_RULING = date(1993, 1, 1)
_REQUIRED_COLUMNS = {"card_oracle_id", "ruling_date", "ruling_text", "source"}
# Same shape as scripts/setup_db.py, so a fresh throwaway database works.
_CARD_RULINGS_DDL = (
    "CREATE TABLE IF NOT EXISTS card_rulings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "card_oracle_id TEXT NOT NULL, ruling_date DATE, ruling_text TEXT NOT NULL, "
    "source TEXT DEFAULT 'mtgapi', fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)
_SNAPSHOTS_DDL = (
    "CREATE TABLE IF NOT EXISTS card_rulings_snapshots (namespace TEXT PRIMARY KEY, "
    "source_url TEXT NOT NULL, source_version TEXT NOT NULL, content_sha256 TEXT NOT NULL, "
    "raw_bytes INTEGER NOT NULL, input_format TEXT NOT NULL, rows_count INTEGER NOT NULL, "
    "wotc_count INTEGER NOT NULL, scryfall_count INTEGER NOT NULL, "
    "duplicates_dropped INTEGER NOT NULL, empty_comments_skipped INTEGER NOT NULL, descriptor_uri TEXT, descriptor_updated_at TEXT, "
    "imported_at TEXT NOT NULL)"
)


class ScryfallRuling(BaseModel):
    """One Scryfall ruling object; unknown fields are a contract change and fail."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    object: Literal["ruling"]
    oracle_id: str
    source: Literal["wotc", "scryfall"]
    published_at: str
    comment: str

    @field_validator("oracle_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        if not _UUID.fullmatch(value):
            raise ValueError("oracle_id must be a lowercase canonical UUID")
        return value

    @field_validator("published_at")
    @classmethod
    def _date(cls, value: str) -> str:
        if not _DATE.fullmatch(value):
            raise ValueError("published_at must be YYYY-MM-DD")
        parsed = date.fromisoformat(value)
        latest = datetime.now(UTC).date() + timedelta(days=2)
        if not _FIRST_RULING <= parsed <= latest:
            raise ValueError("published_at is outside the plausible range")
        return value

    @field_validator("comment")
    @classmethod
    def _comment(cls, value: str) -> str:
        # Whitespace-only comments occur upstream (two in 2026-10-17); they
        # validate as "" and the importer counts, bounds and skips them.
        value = value.strip()
        if len(value) > MAX_COMMENT_CHARS or _CONTROL.search(value):
            raise ValueError("comment is oversized or contains control characters")
        return value


class BulkDescriptor(BaseModel):
    """The Scryfall ``/bulk-data`` object the file was downloaded from."""

    model_config = ConfigDict(extra="ignore", strict=True)
    object: Literal["bulk_data"]
    type: Literal["rulings"]
    uri: str
    updated_at: datetime
    # Current API publishes jsonl_download_uri; older objects had download_uri.
    jsonl_download_uri: str | None = None
    download_uri: str | None = None
    compressed_size: int | None = Field(default=None, ge=0)
    size: int | None = Field(default=None, ge=0)


class RulingsImportResult(BaseModel):
    """What was stored (or found already stored) for the namespace."""

    model_config = ConfigDict(extra="forbid", strict=True)
    namespace: Literal["scryfall-bulk"] = "scryfall-bulk"
    source_url: str
    source_version: str
    content_sha256: str
    raw_bytes: int
    input_format: Literal["jsonl.gz", "jsonl", "json.gz", "json"]
    rows_count: int
    wotc_count: int
    scryfall_count: int
    duplicates_dropped: int
    empty_comments_skipped: int
    descriptor_uri: str | None
    descriptor_updated_at: str | None
    imported_at: str
    unchanged: bool
    rows_replaced: int
    other_source_rows: int
    oracle_ids: int
    oracle_ids_matching_cards: int | None = None


_RULINGS_LIST = TypeAdapter(list[ScryfallRuling])


class _HashingReader(io.RawIOBase):
    """Read-through that hashes and bounds every raw byte it passes on."""

    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        data = self.handle.read(len(buffer))
        self.count += len(data)
        if self.count > MAX_RAW_BYTES:
            raise ValueError("Rulings file exceeds the raw size bound")
        self.digest.update(data)
        buffer[: len(data)] = data
        return len(data)

    def drain(self) -> None:
        while self.read(_CHUNK):
            pass


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in SCRYFALL_HOSTS
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "Rulings require an HTTPS Scryfall URL (api.scryfall.com or data.scryfall.io)"
        )
    return url


def _check_descriptor(
    descriptor: BulkDescriptor, source_url: str, raw_bytes: int, compressed: bool
) -> None:
    published = {
        u for u in (descriptor.jsonl_download_uri, descriptor.download_uri) if u
    }
    if source_url not in published:
        raise ValueError("Source URL is not the descriptor's download URI")
    _check_url(descriptor.uri)
    expected = descriptor.compressed_size if compressed else descriptor.size
    if expected is not None and expected != raw_bytes:
        raise ValueError("File size does not match the descriptor; not that snapshot")
    stamp = _URL_STAMP.search(urlsplit(source_url).path)
    if stamp:
        updated = descriptor.updated_at.astimezone(UTC)
        if stamp[1] != updated.strftime("%Y%m%d%H%M%S"):
            raise ValueError(
                "Download URL timestamp does not match descriptor updated_at"
            )


def _lines(stream: io.BufferedReader[Any] | gzip.GzipFile) -> Iterator[bytes]:
    total = 0
    while True:
        line = stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        total += len(line)
        if total > MAX_DECOMPRESSED_BYTES:
            raise ValueError("Decompressed rulings exceed the size bound")
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("A rulings line exceeds the line size bound")
        yield line


def _parse(
    stream: io.BufferedReader[Any] | gzip.GzipFile,
) -> tuple[str, list[ScryfallRuling]]:
    """Parse JSONL or a JSON list from a decompressed stream, bounded."""
    head = stream.peek(64)[:64]
    first = head.lstrip(b" \t\r\n")[:1]
    if first == b"[":
        data = stream.read(MAX_DECOMPRESSED_BYTES + 1)
        if len(data) > MAX_DECOMPRESSED_BYTES:
            raise ValueError("Decompressed rulings exceed the size bound")
        try:
            records = _RULINGS_LIST.validate_json(data)
        except ValidationError as exc:
            raise ValueError(
                f"Malformed rulings list: {exc.errors()[0]['msg']}"
            ) from None
        if len(records) > MAX_ROWS:
            raise ValueError("Rulings exceed the row bound")
        return "json", records
    if first != b"{":
        raise ValueError("Expected Scryfall rulings as JSONL or a JSON list")
    records = []
    for number, line in enumerate(_lines(stream), start=1):
        if not line.strip():
            continue
        if len(records) >= MAX_ROWS:
            raise ValueError("Rulings exceed the row bound")
        try:
            records.append(ScryfallRuling.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(
                f"Malformed ruling on line {number}: {exc.errors()[0]['msg']}"
            ) from None
    return "jsonl", records


def read_snapshot(
    path: Path, *, expected_sha256: str
) -> tuple[str, int, str, list[ScryfallRuling]]:
    """Hash, bound and strictly parse a gzip/plain JSONL or JSON-list snapshot.

    Returns:
        (input_format, raw_bytes, sha256, records). Records keep file order.

    Raises:
        ValueError: Size, gzip, JSON, schema or hash failure.
    """
    expected = expected_sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Expected SHA-256 must be 64 hex characters")
    with open(path, "rb") as handle:
        raw = _HashingReader(handle)
        buffered = io.BufferedReader(raw, buffer_size=_CHUNK)
        compressed = buffered.peek(2)[:2] == b"\x1f\x8b"
        try:
            if compressed:
                with gzip.GzipFile(fileobj=buffered, mode="rb") as unzipped:
                    fmt, records = _parse(unzipped)
                    if unzipped.read(1):
                        raise ValueError("Trailing data after the rulings payload")
            else:
                fmt, records = _parse(buffered)
                if buffered.read(1).strip():
                    raise ValueError("Trailing data after the rulings payload")
            buffered.read()  # hash every remaining raw byte
            raw.drain()
        except (OSError, EOFError, zlib.error) as exc:  # BadGzipFile is an OSError
            raise ValueError(f"Unreadable rulings file: {exc}") from None
    if raw.digest.hexdigest() != expected:
        raise ValueError("Rulings file does not match the expected SHA-256")
    return fmt + (".gz" if compressed else ""), raw.count, expected, records


def import_rulings(
    db_path: Path,
    snapshot_path: Path,
    *,
    expected_sha256: str,
    source_url: str,
    version: str,
    descriptor: BulkDescriptor | None = None,
) -> RulingsImportResult:
    """Validate a snapshot fully, then atomically replace this namespace's rows.

    Args:
        db_path: SQLite database holding (or to hold) ``card_rulings``.
        snapshot_path: Local Scryfall rulings file; gzip or plain JSONL/JSON list.
        expected_sha256: Hash of the file's raw bytes, as ``shasum -a 256`` reports.
        source_url: HTTPS Scryfall URL the file was obtained from.
        version: Operator label for the snapshot, e.g. the bulk ``updated_at``.
        descriptor: The bulk-data object; if given, URL, size and timestamp
            must agree with it.

    Returns:
        The stored snapshot record. Re-importing the stored hash/URL/version is
        a no-op (``unchanged=True``), so row IDs stay stable.

    Raises:
        ValueError: Any validation failure. The database is untouched.
    """
    _check_url(source_url)
    version = version.strip()
    if not version or len(version) > 120 or not version.isprintable():
        raise ValueError("Invalid rulings version")
    fmt, raw_bytes, digest, records = read_snapshot(
        snapshot_path, expected_sha256=expected_sha256
    )
    if descriptor is not None:
        _check_descriptor(descriptor, source_url, raw_bytes, fmt.endswith(".gz"))
    empty = sum(1 for r in records if not r.comment)
    if empty > MAX_EMPTY_COMMENTS:
        raise ValueError(f"{empty} rulings have empty comments; malformed export")
    unique = sorted(
        {
            (r.oracle_id, r.published_at, r.source, r.comment)
            for r in records
            if r.comment
        }
    )
    if not unique:
        raise ValueError("Rulings snapshot is empty")
    duplicates = len(records) - empty - len(unique)
    del records
    rows = [(oid, day, text, f"{NAMESPACE}:{src}") for oid, day, src, text in unique]
    wotc = sum(1 for r in unique if r[2] == "wotc")
    imported_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    meta: dict[str, Any] = {
        "source_url": source_url,
        "source_version": version,
        "content_sha256": digest,
        "raw_bytes": raw_bytes,
        "input_format": fmt,
        "rows_count": len(rows),
        "wotc_count": wotc,
        "scryfall_count": len(rows) - wotc,
        "duplicates_dropped": duplicates,
        "empty_comments_skipped": empty,
        "descriptor_uri": descriptor.uri if descriptor else None,
        "descriptor_updated_at": (
            descriptor.updated_at.isoformat() if descriptor else None
        ),
    }
    placeholders = ",".join("?" * len(NAMESPACE_SOURCES))
    with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_CARD_RULINGS_DDL)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(card_rulings)")}
            if not _REQUIRED_COLUMNS <= columns:
                raise ValueError("Existing card_rulings lacks the expected columns")
            conn.execute(_SNAPSHOTS_DDL)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rulings_oracle ON card_rulings(card_oracle_id)"
            )
            stored = conn.execute(
                "SELECT source_url, source_version, content_sha256, rows_count, imported_at FROM card_rulings_snapshots WHERE namespace=?",
                (NAMESPACE,),
            ).fetchone()
            present = conn.execute(
                f"SELECT COUNT(*) FROM card_rulings WHERE source IN ({placeholders})",
                NAMESPACE_SOURCES,
            ).fetchone()[0]
            unchanged = (
                stored is not None
                and stored[:4]
                == (
                    source_url,
                    version,
                    digest,
                    len(rows),
                )
                and present == len(rows)
            )
            replaced = 0
            if unchanged:
                imported_at = stored[4]
            else:
                replaced = conn.execute(
                    f"DELETE FROM card_rulings WHERE source IN ({placeholders})",
                    NAMESPACE_SOURCES,
                ).rowcount
                conn.executemany(
                    "INSERT INTO card_rulings (card_oracle_id, ruling_date, ruling_text, source) VALUES (?,?,?,?)",
                    rows,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO card_rulings_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (NAMESPACE, *meta.values(), imported_at),
                )
            others = conn.execute(
                f"SELECT COUNT(*) FROM card_rulings WHERE source IS NULL OR source NOT IN ({placeholders})",
                NAMESPACE_SOURCES,
            ).fetchone()[0]
            matching = _matching_cards(conn, {r[0] for r in rows})
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return RulingsImportResult(
        **meta,
        imported_at=imported_at,
        unchanged=unchanged,
        rows_replaced=replaced,
        other_source_rows=others,
        oracle_ids=len({r[0] for r in rows}),
        oracle_ids_matching_cards=matching,
    )


def _matching_cards(conn: sqlite3.Connection, oracle_ids: set[str]) -> int | None:
    """How many ruling Oracle IDs a local ``cards`` table knows; None if absent."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
    if "oracle_id" not in columns:
        return None
    known = {r[0] for r in conn.execute("SELECT DISTINCT oracle_id FROM cards")}
    return len(oracle_ids & known)
