"""Immutable local card catalog for deterministic structured and lexical retrieval.

The catalog is a derived artifact, not application state.  A build is written to
a temporary sibling and moved into place only after SQLite reports a healthy
database.  Readers use SQLite's read-only immutable URI mode, so retrieval can
never mutate the artifact it is consulting.

The substrate ``CardView`` extends the pure mechanic-predicate view with a
Commander-legality status.  Missing facts are stored as ``NULL``.  That
distinction is load-bearing: unknown legality must not become legal merely
because a source omitted it.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from sabermetrics.substrate.corpus import CardView
from sabermetrics.substrate.models import CardFilters
from sabermetrics.substrate.tagging import CARD_TYPES, TagBuild

CATALOG_SCHEMA_VERSION = "retrieval-catalog.v1"
_COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_MAX_QUERY_TOKENS = 32
_MAX_TOKEN_LENGTH = 64
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "card",
        "cards",
        "find",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "legal",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "whose",
        "with",
    }
)


@dataclass(frozen=True)
class Bm25Weights:
    """Positive FTS5 column weights supplied by retrieval configuration."""

    name: float = 10.0
    type_line: float = 3.0
    oracle_text: float = 1.0

    def __post_init__(self) -> None:
        """Reject values that cannot produce a stable lexical ordering."""
        for label, value in (
            ("name", self.name),
            ("type_line", self.type_line),
            ("oracle_text", self.oracle_text),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"BM25 {label} weight must be positive and finite")


_SCHEMA = (
    """
    CREATE TABLE catalog_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        tag_library_sha256 TEXT NOT NULL,
        tag_content_sha256 TEXT NOT NULL,
        card_count INTEGER NOT NULL,
        tag_row_count INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE card_document (
        oracle_id TEXT PRIMARY KEY,
        row_offset INTEGER NOT NULL UNIQUE CHECK (row_offset >= 0),
        vector_offset INTEGER NOT NULL UNIQUE CHECK (vector_offset >= 0),
        name TEXT NOT NULL,
        type_line TEXT NOT NULL,
        oracle_text TEXT,
        mana_cost TEXT,
        mana_value REAL NOT NULL,
        color_identity TEXT NOT NULL,
        color_identity_mask INTEGER NOT NULL,
        commander_legal INTEGER CHECK (commander_legal IN (0, 1)),
        canonical_name TEXT NOT NULL,
        canonical_type_line TEXT NOT NULL,
        canonical_oracle_text TEXT NOT NULL,
        canonical_document TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE card_type (
        oracle_id TEXT NOT NULL REFERENCES card_document(oracle_id) ON DELETE CASCADE,
        card_type TEXT NOT NULL,
        PRIMARY KEY (oracle_id, card_type)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE card_tag (
        oracle_id TEXT NOT NULL REFERENCES card_document(oracle_id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL,
        tag_version TEXT NOT NULL,
        confidence REAL NOT NULL,
        matched_span TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        PRIMARY KEY (oracle_id, tag_id)
    ) WITHOUT ROWID
    """,
    """
    CREATE VIRTUAL TABLE card_fts USING fts5(
        name,
        type_line,
        oracle_text,
        oracle_id UNINDEXED,
        tokenize = 'unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE INDEX idx_card_document_structured
    ON card_document(commander_legal, mana_value, color_identity_mask, oracle_id)
    """,
    """
    CREATE INDEX idx_card_document_mana_value
    ON card_document(mana_value, oracle_id)
    """,
    """
    CREATE INDEX idx_card_document_color_identity
    ON card_document(color_identity_mask, oracle_id)
    """,
    """
    CREATE INDEX idx_card_type_lookup
    ON card_type(card_type, oracle_id)
    """,
    """
    CREATE INDEX idx_card_tag_lookup
    ON card_tag(tag_id, oracle_id)
    """,
)


class CatalogNotFoundError(FileNotFoundError):
    """Raised when a caller tries to open a catalog that has not been built."""


class CatalogBuildError(RuntimeError):
    """Raised when a catalog cannot be completely built and installed."""


@dataclass(frozen=True)
class CatalogRecord:
    """One card returned by structured retrieval."""

    oracle_id: str
    row_offset: int
    vector_offset: int
    name: str
    type_line: str
    oracle_text: str | None
    mana_cost: str | None
    mana_value: float
    color_identity: tuple[str, ...]
    commander_legal: bool | None
    canonical_document: str
    types: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class CatalogHit:
    """A lexical result and its weighted FTS5 BM25 score.

    SQLite BM25 scores are lower-is-better.  Equal scores are ordered by
    ``oracle_id``.
    """

    oracle_id: str
    row_offset: int
    vector_offset: int
    name: str
    type_line: str
    oracle_text: str | None
    mana_cost: str | None
    mana_value: float
    color_identity: tuple[str, ...]
    commander_legal: bool | None
    canonical_document: str
    types: tuple[str, ...]
    tags: tuple[str, ...]
    bm25_score: float


@dataclass(frozen=True)
class CatalogBuildResult:
    """Counts and provenance for an atomically installed catalog."""

    path: Path
    card_count: int
    tag_row_count: int
    snapshot_hash: str
    tag_content_sha256: str


def compile_fts_query(text: str) -> str | None:
    """Compile plain user text into a safe FTS5 conjunction.

    Only normalized Unicode word tokens survive.  Operators, quotes,
    parentheses, column selectors, wildcards, and punctuation are never copied
    into the FTS expression, so callers cannot inject raw FTS syntax.

    Args:
        text: Untrusted plain-text search input.

    Returns:
        A quoted ``OR`` expression over informative tokens, or ``None`` when no
        informative word token exists. Disjunction is intentional for recall:
        clarified natural-language asks are not phrases copied from Oracle text.

    Raises:
        ValueError: If the query exceeds the bounded token or token-length
            limits.
    """
    normalized = unicodedata.normalize("NFKC", text)
    tokens = _TOKEN_RE.findall(normalized)
    if len(tokens) > _MAX_QUERY_TOKENS:
        raise ValueError(f"search query has more than {_MAX_QUERY_TOKENS} tokens")
    if any(len(token) > _MAX_TOKEN_LENGTH for token in tokens):
        raise ValueError(
            f"search query contains a token longer than {_MAX_TOKEN_LENGTH} characters"
        )
    unique_tokens = tuple(
        dict.fromkeys(
            token.casefold() for token in tokens if token.casefold() not in _STOPWORDS
        )
    )
    if not unique_tokens:
        return None
    return " OR ".join(f'"{token}"' for token in unique_tokens)


def build_catalog(
    path: str | Path,
    cards: Sequence[CardView],
    tag_build: TagBuild,
) -> CatalogBuildResult:
    """Build a standalone catalog and atomically move it to ``path``.

    Args:
        path: Final SQLite artifact path.  Its parent must already exist.
        cards: Card views to index.  Oracle ids must be non-empty and unique.
        tag_build: Existing mechanic-tag build whose rows populate ``card_tag``.

    Returns:
        Build counts and source hashes.

    Raises:
        CatalogBuildError: If validation, SQLite construction, integrity
            checking, or atomic replacement fails.  The previous catalog, when
            one exists, remains untouched.
    """
    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise CatalogBuildError(f"catalog parent does not exist: {parent}")

    ordered_cards = tuple(sorted(cards, key=lambda card: card.oracle_id))
    temporary: Path | None = None
    try:
        _validate_inputs(ordered_cards, tag_build)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=parent
        )
        os.close(descriptor)
        temporary = Path(temp_name)
        _populate_catalog(temporary, ordered_cards, tag_build)
        with temporary.open("rb") as catalog_file:
            os.fsync(catalog_file.fileno())
        os.replace(temporary, target)
        temporary = None
    except Exception as exc:
        if isinstance(exc, CatalogBuildError):
            raise
        raise CatalogBuildError(f"catalog build failed for {target}: {exc}") from exc
    finally:
        if temporary is not None:
            _remove_sqlite_artifacts(temporary)

    return CatalogBuildResult(
        path=target,
        card_count=len(ordered_cards),
        tag_row_count=len(tag_build.rows),
        snapshot_hash=tag_build.snapshot.sha256(),
        tag_content_sha256=tag_build.content_sha256,
    )


class Catalog:
    """Read-only handle to one immutable catalog generation."""

    def __init__(self, path: str | Path) -> None:
        """Open ``path`` using SQLite read-only immutable mode.

        Args:
            path: A catalog previously produced by :func:`build_catalog`.

        Raises:
            CatalogNotFoundError: If the path does not name an existing file.
            sqlite3.DatabaseError: If the file is not a compatible catalog.
        """
        self.path = Path(path)
        if not self.path.is_file():
            raise CatalogNotFoundError(f"catalog has not been built: {self.path}")
        uri = f"{self.path.resolve().as_uri()}?mode=ro&immutable=1"
        self._connection = sqlite3.connect(uri, uri=True)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only = ON")
        row = self._connection.execute(
            "SELECT schema_version FROM catalog_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None or row["schema_version"] != CATALOG_SCHEMA_VERSION:
            self.close()
            actual = None if row is None else row["schema_version"]
            raise sqlite3.DatabaseError(
                f"unsupported catalog schema {actual!r}; "
                f"expected {CATALOG_SCHEMA_VERSION!r}"
            )

    def __enter__(self) -> Self:
        """Return this open read-only handle."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the handle when leaving a context manager."""
        self.close()

    def close(self) -> None:
        """Close the underlying read-only SQLite connection."""
        self._connection.close()

    def records(
        self,
        filters: CardFilters | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[CatalogRecord, ...]:
        """Return cards satisfying structured predicates in oracle-id order.

        Args:
            filters: Predicates to push into SQL.  Defaults to no constraints.
            limit: Optional maximum number of rows.  Must be non-negative.

        Returns:
            Matching records with normalized types and tags.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        where, parameters = _compile_filters(
            filters or CardFilters(commander_legal=None)
        )
        sql = f"SELECT d.* FROM card_document AS d WHERE {' AND '.join(where)}"
        sql += " ORDER BY d.oracle_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        rows = self._connection.execute(sql, parameters).fetchall()
        relations = self._relations(tuple(str(row["oracle_id"]) for row in rows))
        return tuple(
            _record_from_row(row, relations[str(row["oracle_id"])]) for row in rows
        )

    def search(
        self,
        text: str,
        filters: CardFilters | None = None,
        *,
        limit: int = 50,
        weights: Bm25Weights | None = None,
    ) -> tuple[CatalogHit, ...]:
        """Run weighted lexical retrieval with structured SQL predicates.

        Args:
            text: Untrusted plain-language query.  Raw FTS5 syntax is not
                accepted.
            filters: Predicates applied before result ordering.
            limit: Maximum hits, between zero and 5001 inclusive. The extra
                row lets a configured 5000-row pool report truncation.
            weights: Configured name, type-line, and Oracle-text weights.

        Returns:
            Hits ordered by weighted BM25 score, then ``oracle_id``.  Empty or
            punctuation-only input returns no hits.
        """
        if limit < 0 or limit > 5001:
            raise ValueError("limit must be between 0 and 5001")
        compiled = compile_fts_query(text)
        if compiled is None or limit == 0:
            return ()
        where, parameters = _compile_filters(
            filters or CardFilters(commander_legal=None)
        )
        bm25 = weights or Bm25Weights()
        where.insert(0, "card_fts MATCH ?")
        parameters.insert(0, compiled)
        sql = f"""
            SELECT d.*, bm25(
                card_fts, {bm25.name!r}, {bm25.type_line!r},
                {bm25.oracle_text!r}, 0.0
            ) AS bm25_score
            FROM card_fts
            JOIN card_document AS d ON d.oracle_id = card_fts.oracle_id
            WHERE {" AND ".join(where)}
            ORDER BY bm25_score ASC, d.oracle_id ASC
            LIMIT ?
        """
        parameters.append(limit)
        rows = self._connection.execute(sql, parameters).fetchall()
        relations = self._relations(tuple(str(row["oracle_id"]) for row in rows))
        return tuple(
            _hit_from_row(row, relations[str(row["oracle_id"])]) for row in rows
        )

    def _relations(
        self, oracle_ids: tuple[str, ...]
    ) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
        if not oracle_ids:
            return {}
        encoded = json.dumps(oracle_ids)
        types: dict[str, list[str]] = defaultdict(list)
        tags: dict[str, list[str]] = defaultdict(list)
        for row in self._connection.execute(
            """
            SELECT oracle_id, card_type FROM card_type
            WHERE oracle_id IN (SELECT value FROM json_each(?))
            ORDER BY oracle_id, card_type
            """,
            (encoded,),
        ):
            types[str(row["oracle_id"])].append(str(row["card_type"]))
        for row in self._connection.execute(
            """
            SELECT oracle_id, tag_id FROM card_tag
            WHERE oracle_id IN (SELECT value FROM json_each(?))
            ORDER BY oracle_id, tag_id
            """,
            (encoded,),
        ):
            tags[str(row["oracle_id"])].append(str(row["tag_id"]))
        return {
            oracle_id: (
                tuple(types.get(oracle_id, ())),
                tuple(tags.get(oracle_id, ())),
            )
            for oracle_id in oracle_ids
        }


def open_catalog(path: str | Path) -> Catalog:
    """Open an immutable read-only catalog.

    Args:
        path: Catalog artifact path.

    Returns:
        A context-manager-compatible :class:`Catalog`.

    Raises:
        CatalogNotFoundError: If no catalog exists at ``path``.
    """
    return Catalog(path)


def _normalize_colors(colors: Sequence[str]) -> tuple[str, ...]:
    normalized = {color.upper() for color in colors}
    unknown = normalized.difference(_COLOR_BITS)
    if unknown:
        raise ValueError(f"unknown color identity symbols: {sorted(unknown)}")
    return tuple(color for color in _COLOR_BITS if color in normalized)


def _normalize_terms(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized = tuple(sorted({" ".join(value.split()).casefold() for value in values}))
    if any(not value for value in normalized):
        raise ValueError(f"{label} cannot contain an empty value")
    return normalized


def _color_mask(colors: Sequence[str]) -> int:
    return sum(_COLOR_BITS[color] for color in _normalize_colors(colors))


def _card_types(card: CardView) -> tuple[str, ...]:
    values = {value.casefold() for value in card.all_types if value}
    for line in (card.type_line, *(face.type_line or "" for face in card.faces)):
        head = line.split("—", 1)[0]
        words = set(head.replace("//", " ").split())
        values.update(
            card_type.casefold() for card_type in CARD_TYPES if card_type in words
        )
    return tuple(sorted(values))


def _join_unique(values: Sequence[str | None]) -> str:
    return "\n".join(
        dict.fromkeys(value.strip() for value in values if value and value.strip())
    )


def _canonical_fields(card: CardView) -> tuple[str, str, str, str]:
    name = _join_unique((card.name, *(face.name for face in card.faces)))
    type_line = _join_unique((card.type_line, *(face.type_line for face in card.faces)))
    oracle_text = _join_unique(
        (card.oracle_text, *(face.oracle_text for face in card.faces))
    )
    mana_cost = _join_unique((card.mana_cost, *(face.mana_cost for face in card.faces)))
    document = "\n".join(
        (
            f"Name: {name}",
            f"Type: {type_line}",
            f"Mana cost: {mana_cost}",
            f"Mana value: {card.mana_value:g}",
            f"Oracle text: {oracle_text}",
        )
    )
    return name, type_line, oracle_text, document


def _stored_legality(status: str | None) -> int | None:
    if status is None or status == "unknown":
        return None
    return int(status == "legal")


def _validate_inputs(
    cards: Sequence[CardView],
    tag_build: TagBuild,
) -> None:
    oracle_ids = [card.oracle_id for card in cards]
    if any(not oracle_id for oracle_id in oracle_ids):
        raise CatalogBuildError("every card needs a non-empty oracle_id")
    if len(oracle_ids) != len(set(oracle_ids)):
        raise CatalogBuildError("card oracle_ids must be unique")
    card_ids = set(oracle_ids)
    orphan_tags = sorted({row.oracle_id for row in tag_build.rows} - card_ids)
    if orphan_tags:
        raise CatalogBuildError(
            f"tag rows refer to {len(orphan_tags)} absent card(s): {orphan_tags[:3]}"
        )
    valid_legality = {None, "legal", "not_legal", "restricted", "banned", "unknown"}
    invalid_legality = sorted(
        card.oracle_id for card in cards if card.commander_legal not in valid_legality
    )
    if invalid_legality:
        raise CatalogBuildError(
            f"unrecognized Commander legality facts: {invalid_legality[:3]}"
        )


def _populate_catalog(
    path: Path,
    cards: Sequence[CardView],
    tag_build: TagBuild,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA:
            connection.execute(statement)

        document_rows: list[tuple[object, ...]] = []
        fts_rows: list[tuple[object, ...]] = []
        type_rows: list[tuple[str, str]] = []
        for offset, card in enumerate(cards):
            canonical_name, canonical_type, canonical_oracle, document = (
                _canonical_fields(card)
            )
            colors = _normalize_colors(card.color_identity)
            stored_status = _stored_legality(card.commander_legal)
            document_rows.append(
                (
                    card.oracle_id,
                    offset,
                    offset,
                    card.name,
                    card.type_line,
                    card.oracle_text,
                    card.mana_cost,
                    card.mana_value,
                    json.dumps(colors),
                    _color_mask(colors),
                    stored_status,
                    canonical_name,
                    canonical_type,
                    canonical_oracle,
                    document,
                )
            )
            fts_rows.append(
                (
                    offset + 1,
                    canonical_name,
                    canonical_type,
                    canonical_oracle,
                    card.oracle_id,
                )
            )
            type_rows.extend(
                (card.oracle_id, card_type) for card_type in _card_types(card)
            )

        connection.executemany(
            """
            INSERT INTO card_document (
                oracle_id, row_offset, vector_offset, name, type_line, oracle_text,
                mana_cost, mana_value, color_identity, color_identity_mask,
                commander_legal, canonical_name, canonical_type_line,
                canonical_oracle_text, canonical_document
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            document_rows,
        )
        connection.executemany(
            """
            INSERT INTO card_fts(
                rowid, name, type_line, oracle_text, oracle_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            fts_rows,
        )
        connection.executemany(
            "INSERT INTO card_type(oracle_id, card_type) VALUES (?, ?)", type_rows
        )
        connection.executemany(
            """
            INSERT INTO card_tag(
                oracle_id, tag_id, tag_version, confidence, matched_span,
                snapshot_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    row.oracle_id,
                    row.tag_id.casefold(),
                    row.tag_version,
                    row.confidence,
                    row.matched_span,
                    row.snapshot_hash,
                )
                for row in tag_build.rows
            ),
        )
        connection.execute(
            """
            INSERT INTO catalog_metadata(
                singleton, schema_version, snapshot_hash, tag_library_sha256,
                tag_content_sha256, card_count, tag_row_count
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                CATALOG_SCHEMA_VERSION,
                tag_build.snapshot.sha256(),
                tag_build.library_sha256,
                tag_build.content_sha256,
                len(cards),
                len(tag_build.rows),
            ),
        )
        connection.execute("INSERT INTO card_fts(card_fts) VALUES ('optimize')")
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise CatalogBuildError(f"SQLite integrity check failed: {integrity}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _compile_filters(filters: CardFilters) -> tuple[list[str], list[object]]:
    clauses = ["1 = 1"]
    parameters: list[object] = []
    if filters.color_identity is not None:
        mask = _color_mask(filters.color_identity)
        if filters.color_mode == "subset":
            clauses.append("(d.color_identity_mask | ?) = ?")
            parameters.extend((mask, mask))
        elif filters.color_mode == "exact":
            clauses.append("d.color_identity_mask = ?")
            parameters.append(mask)
        else:
            clauses.append("(d.color_identity_mask & ?) != 0")
            parameters.append(mask)
    for card_type in _normalize_terms(filters.required_types, "required_types"):
        clauses.append(
            "EXISTS (SELECT 1 FROM card_type AS required_type "
            "WHERE required_type.oracle_id = d.oracle_id "
            "AND required_type.card_type = ?)"
        )
        parameters.append(card_type)
    for card_type in _normalize_terms(filters.excluded_types, "excluded_types"):
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM card_type AS excluded_type "
            "WHERE excluded_type.oracle_id = d.oracle_id "
            "AND excluded_type.card_type = ?)"
        )
        parameters.append(card_type)
    for tag_id in _normalize_terms(filters.required_tags, "required_tags"):
        clauses.append(
            "EXISTS (SELECT 1 FROM card_tag AS required_tag "
            "WHERE required_tag.oracle_id = d.oracle_id "
            "AND required_tag.tag_id = ?)"
        )
        parameters.append(tag_id)
    any_tags = _normalize_terms(filters.any_tags, "any_tags")
    if any_tags:
        placeholders = ",".join("?" for _ in any_tags)
        clauses.append(
            "EXISTS (SELECT 1 FROM card_tag AS any_tag "
            "WHERE any_tag.oracle_id = d.oracle_id "
            f"AND any_tag.tag_id IN ({placeholders}))"
        )
        parameters.extend(any_tags)
    for tag_id in _normalize_terms(filters.excluded_tags, "excluded_tags"):
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM card_tag AS excluded_tag "
            "WHERE excluded_tag.oracle_id = d.oracle_id "
            "AND excluded_tag.tag_id = ?)"
        )
        parameters.append(tag_id)
    if filters.mana_value_min is not None:
        clauses.append("d.mana_value >= ?")
        parameters.append(filters.mana_value_min)
    if filters.mana_value_max is not None:
        clauses.append("d.mana_value <= ?")
        parameters.append(filters.mana_value_max)
    if filters.commander_legal is not None:
        clauses.append("d.commander_legal = ?")
        parameters.append(int(filters.commander_legal))
    if filters.allowed_oracle_ids:
        clauses.append("d.oracle_id IN (SELECT value FROM json_each(?))")
        parameters.append(json.dumps(filters.allowed_oracle_ids))
    return clauses, parameters


def _record_from_row(
    row: sqlite3.Row, relations: tuple[tuple[str, ...], tuple[str, ...]]
) -> CatalogRecord:
    status = row["commander_legal"]
    return CatalogRecord(
        oracle_id=str(row["oracle_id"]),
        row_offset=int(row["row_offset"]),
        vector_offset=int(row["vector_offset"]),
        name=str(row["name"]),
        type_line=str(row["type_line"]),
        oracle_text=row["oracle_text"],
        mana_cost=row["mana_cost"],
        mana_value=float(row["mana_value"]),
        color_identity=tuple(json.loads(str(row["color_identity"]))),
        commander_legal=None if status is None else bool(status),
        canonical_document=str(row["canonical_document"]),
        types=relations[0],
        tags=relations[1],
    )


def _hit_from_row(
    row: sqlite3.Row, relations: tuple[tuple[str, ...], tuple[str, ...]]
) -> CatalogHit:
    record = _record_from_row(row, relations)
    return CatalogHit(
        oracle_id=record.oracle_id,
        row_offset=record.row_offset,
        vector_offset=record.vector_offset,
        name=record.name,
        type_line=record.type_line,
        oracle_text=record.oracle_text,
        mana_cost=record.mana_cost,
        mana_value=record.mana_value,
        color_identity=record.color_identity,
        commander_legal=record.commander_legal,
        canonical_document=record.canonical_document,
        types=record.types,
        tags=record.tags,
        bm25_score=float(row["bm25_score"]),
    )


def _remove_sqlite_artifacts(path: Path) -> None:
    for artifact in (
        path,
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    ):
        try:
            artifact.unlink()
        except FileNotFoundError:
            pass
