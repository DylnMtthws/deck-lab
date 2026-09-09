"""Corpus sources: where the tag builder gets card text, and how it names it.

The tag builder needs something ``mtg_v1``'s repository interface deliberately
does not offer — a **full scan**. ``CardRepository`` is built for "fetch these
oracle ids", because that is what deterministic deck construction needs; a
mechanic tag rebuild reads every card exactly once. That is a different access
pattern, so it gets a different interface rather than a fifth method on the
existing one.

Three sources implement it, and the difference between them is provenance, not
behaviour:

* :class:`PostgresCorpusSource` — production. ``mtg_v1.card_any_medium``, never
  ``mtg_v1.card`` (ADR-020), read-only, ``assert_v1_only`` on every statement.
* :class:`JsonCorpusSource` — a materialised snapshot on disk. Used by the test
  suite and for local development, so the tag library can be measured with no
  database. It carries the provenance of whatever produced it and refuses to
  pretend to be the production view.
* Any object satisfying :class:`CorpusSource`, for tests that construct cards
  in memory.

Every source yields the same :class:`~sabermetrics.mechanics.tags.predicates.CardView`,
which is a strict subset of ``CardFacts``. A predicate therefore cannot read a
field that exists offline and not in production — the failure mode where a tag
passes every fixture and tags nothing against the real corpus.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sabermetrics.mechanics.tags.predicates import (
    CardView as PredicateCardView,
)
from sabermetrics.mechanics.tags.predicates import (
    FaceView,
)

#: The on-disk snapshot format this module reads and ``scripts/`` writes.
CORPUS_SCHEMA_VERSION = "mechanic-corpus-snapshot.v1"
#: Domain separator for hashes of canonical retrieval-corpus facts.
CORPUS_CONTENT_HASH_VERSION = "retrieval-corpus-content.v1"


@dataclass(frozen=True)
class CardView(PredicateCardView):
    """A predicate card plus its Commander legality when the source provides it.

    The pure predicate algebra deliberately does not need legality. Retrieval
    does, so the substrate extends that strict subset without making mechanics
    depend on the cEDH package. ``None`` means the source did not provide a
    Commander legality fact; it must not be interpreted as illegal.
    """

    commander_legal: str | None = None


@dataclass(frozen=True)
class SnapshotIdentity:
    """Which corpus a tag build read, in a form two builds can be compared on.

    Mirrors :class:`sabermetrics.cedh.repositories.CorpusSnapshot`, plus the
    hash the tag rows carry. ``source_view`` is the honest label: a snapshot
    materialised from a development export says so, so a row built from it can
    never be mistaken for one built from ``mtg_v1``.
    """

    source_view: str
    row_count: int | None = None
    max_content_updated_at: str | None = None
    captured_at: str | None = None

    def sha256(self) -> str:
        """Stable hash over the identifying fields, not the card rows.

        Hashing the rows would be more precise and would also mean reading the
        whole corpus twice; these four fields are what ``mtg_v1`` already
        publishes to distinguish one nightly from the next, and the tag build's
        own content hash covers the output.
        """
        payload = "|".join(
            (
                self.source_view,
                "" if self.row_count is None else str(self.row_count),
                self.max_content_updated_at or "",
                self.captured_at or "",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CorpusExport:
    """One immutable, snapshot-consistent corpus materialization.

    ``content_sha256`` covers canonical card, face, and Commander-legality
    facts only. Capture time and other provenance metadata are intentionally
    excluded, so exporting unchanged facts twice produces the same digest.
    """

    identity: SnapshotIdentity
    cards: tuple[CardView, ...]
    content_sha256: str

    def iter_cards(self) -> Iterator[CardView]:
        """Yield the materialized cards in canonical order."""
        yield from self.cards


@runtime_checkable
class CorpusSource(Protocol):
    """A full scan over card text, plus the identity of what was scanned."""

    def identity(self) -> SnapshotIdentity:
        """Name the snapshot. Called before ``iter_cards``."""

    def iter_cards(self) -> Iterator[PredicateCardView]:
        """Yield every card exactly once, in a deterministic order."""


# --- in-memory -------------------------------------------------------------


@dataclass(frozen=True)
class InMemoryCorpusSource:
    """A fixed list of cards. For unit tests that build their own text."""

    cards: tuple[PredicateCardView, ...]
    source_view: str = "memory"

    def identity(self) -> SnapshotIdentity:
        return SnapshotIdentity(source_view=self.source_view, row_count=len(self.cards))

    def iter_cards(self) -> Iterator[PredicateCardView]:
        yield from sorted(self.cards, key=lambda c: (c.oracle_id, c.name))


# --- on disk ---------------------------------------------------------------


def card_view_from_mapping(raw: dict[str, Any]) -> CardView:
    """Build a :class:`CardView` from one snapshot record.

    Args:
        raw: A mapping with ``mtg_v1.card_any_medium`` column names.

    Returns:
        The card. Missing optional fields take their documented defaults;
        ``oracle_id`` and ``name`` are required, because a card that cannot be
        identified cannot carry a tag row.

    Raises:
        KeyError: If ``oracle_id`` or ``name`` is absent.
    """
    faces = tuple(
        FaceView(
            name=str(face.get("name") or ""),
            mana_cost=face.get("mana_cost"),
            type_line=face.get("type_line"),
            oracle_text=face.get("oracle_text"),
        )
        for face in (raw.get("faces") or ())
    )
    commander_legal = raw.get("commander_legal", raw.get("commander_legality"))
    return CardView(
        oracle_id=str(raw["oracle_id"]),
        name=str(raw["name"]),
        layout=str(raw.get("layout") or ""),
        mana_cost=raw.get("mana_cost"),
        mana_value=float(raw.get("mana_value") or 0.0),
        type_line=str(raw.get("type_line") or ""),
        oracle_text=raw.get("oracle_text"),
        colors=tuple(raw.get("colors") or ()),
        color_identity=tuple(raw.get("color_identity") or ()),
        keywords=tuple(raw.get("keywords") or ()),
        all_types=tuple(raw.get("all_types") or ()),
        castable_cmcs=tuple(float(c) for c in (raw.get("castable_cmcs") or ())),
        faces=faces,
        commander_legal=(str(commander_legal) if commander_legal is not None else None),
    )


def _canonical_card(card: CardView) -> dict[str, Any]:
    return {
        "oracle_id": card.oracle_id,
        "name": card.name,
        "layout": card.layout,
        "mana_cost": card.mana_cost,
        "mana_value": card.mana_value,
        "type_line": card.type_line,
        "oracle_text": card.oracle_text,
        "colors": sorted(card.colors),
        "color_identity": sorted(card.color_identity),
        "keywords": sorted(card.keywords),
        "all_types": sorted(card.all_types),
        "castable_cmcs": sorted(card.castable_cmcs),
        "faces": [
            {
                "name": face.name,
                "mana_cost": face.mana_cost,
                "type_line": face.type_line,
                "oracle_text": face.oracle_text,
            }
            for face in card.faces
        ],
        "commander_legal": card.commander_legal,
    }


def corpus_content_sha256(cards: Sequence[CardView]) -> str:
    """Hash canonical card, face, and legality facts independent of row order."""
    digest = hashlib.sha256()
    digest.update(f"{CORPUS_CONTENT_HASH_VERSION}\n".encode())
    for card in sorted(cards, key=lambda item: (item.oracle_id, item.name)):
        line = json.dumps(
            _canonical_card(card),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    formatter = getattr(value, "isoformat", None)
    return str(formatter()) if callable(formatter) else str(value)


class JsonCorpusSource:
    """A materialised snapshot: ``.json`` with a ``cards`` array, or ``.jsonl``.

    ``.jsonl`` takes its provenance from a sibling ``<name>.meta.json``. A
    snapshot with no provenance is readable but reports
    ``source_view="unattributed:<filename>"`` rather than inventing one, because
    a tag row whose snapshot cannot be named is a row nobody can reproduce.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"corpus snapshot missing: {self._path}")
        self._identity = self._read_identity()

    @property
    def path(self) -> Path:
        return self._path

    def _read_identity(self) -> SnapshotIdentity:
        if self._path.suffix == ".jsonl":
            meta_path = self._path.with_suffix(".meta.json")
            raw = (
                json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists()
                else {}
            )
        else:
            with self._path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
        snapshot = raw.get("snapshot") or {}
        return SnapshotIdentity(
            source_view=str(
                snapshot.get("source_view") or f"unattributed:{self._path.name}"
            ),
            row_count=snapshot.get("row_count"),
            max_content_updated_at=snapshot.get("max_content_updated_at"),
            captured_at=snapshot.get("captured_at"),
        )

    def identity(self) -> SnapshotIdentity:
        return self._identity

    def iter_cards(self) -> Iterator[CardView]:
        if self._path.suffix == ".jsonl":
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield card_view_from_mapping(json.loads(line))
            return
        with self._path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        for raw in payload.get("cards", []):
            yield card_view_from_mapping(raw)

    def by_name(self) -> dict[str, CardView]:
        """Index the snapshot by card name, and by front-face name for DFCs.

        Used by the fixture harness, which labels cards the way a person does.
        The joined ``A // B`` name wins over a bare front-face key, so a fixture
        naming ``Fire`` still resolves while ``Fire // Ice`` resolves exactly.
        """
        index: dict[str, CardView] = {}
        for card in self.iter_cards():
            front = card.name.split(" // ", 1)[0]
            index.setdefault(front, card)
        for card in self.iter_cards():
            index[card.name] = card
        return index


# --- production ------------------------------------------------------------


class PostgresCorpusSource:
    """Full scan of ``mtg_v1.card_any_medium`` as ``mtg_consumer``.

    Reads through the existing cEDH Postgres adapter's connection helpers so
    there is one place that knows the DSN, the read-only transaction settings
    and the schema guard. The view is ``card_any_medium`` and not ``card``: the
    default view silently drops 254 Reserved List cards, and a tag corpus built
    from it would be missing Lotus Petal and Mox Diamond while reporting
    success.
    """

    def __init__(self, dsn: str | None = None, *, batch_size: int = 5000) -> None:
        self._dsn = dsn
        self._batch_size = batch_size
        self._export: CorpusExport | None = None

    def identity(self) -> SnapshotIdentity:
        """Return the identity captured with this source's atomic export."""
        return self.export().identity

    def iter_cards(self) -> Iterator[CardView]:
        """Yield cards from the cached atomic export in deterministic order."""
        yield from self.export().cards

    def export(self) -> CorpusExport:
        """Atomically export identity, card facts, faces, and Commander legality.

        Returns:
            A frozen export whose rows and metadata were read on one connection
            in one read-only, repeatable-read transaction.
        """
        if self._export is not None:
            return self._export

        from sabermetrics.cedh import adapters_postgres as pg
        from sabermetrics.cedh.repositories import assert_v1_only

        transaction_sql = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        identity_sql = (
            "SELECT COUNT(*) AS n, MAX(content_updated_at) AS latest "
            f"FROM {pg.CARD_VIEW}"
        )
        card_sql = f"SELECT {pg._CARD_COLUMNS} FROM {pg.CARD_VIEW} ORDER BY oracle_id"
        face_sql = (
            "SELECT oracle_id, face_index, name, mana_cost, type_line, oracle_text "
            f"FROM {pg.FACE_VIEW} ORDER BY oracle_id, face_index"
        )
        legality_sql = (
            "SELECT oracle_id, status FROM "
            f"{pg.LEGALITY_VIEW} WHERE format = %(format)s ORDER BY oracle_id"
        )
        statements = (
            transaction_sql,
            identity_sql,
            card_sql,
            face_sql,
            legality_sql,
        )
        # ``_query`` guards each statement too. Guarding where the SQL is
        # authored keeps that invariant testable even when the adapter is mocked.
        for statement in statements:
            assert_v1_only(statement)

        with pg._connect(self._dsn) as conn, conn.transaction():
            pg._query(conn, transaction_sql)
            captured_at = datetime.now(UTC).isoformat()
            identity_rows = pg._query(conn, identity_sql)
            card_rows = pg._query(conn, card_sql)
            face_rows = pg._query(conn, face_sql)
            legality_rows = pg._query(
                conn,
                legality_sql,
                {"format": "commander"},
            )

            faces: dict[str, list[dict[str, Any]]] = {}
            for row in sorted(
                face_rows,
                key=lambda item: (
                    str(item["oracle_id"]),
                    int(item.get("face_index") or 0),
                ),
            ):
                faces.setdefault(str(row["oracle_id"]), []).append(dict(row))
            legalities = {
                str(row["oracle_id"]): str(row["status"]) for row in legality_rows
            }
            cards: list[CardView] = []
            for row in sorted(
                card_rows,
                key=lambda item: (str(item["oracle_id"]), str(item["name"])),
            ):
                record = dict(row)
                oracle_id = str(record["oracle_id"])
                record["faces"] = faces.get(oracle_id, [])
                record["commander_legal"] = legalities.get(oracle_id)
                cards.append(card_view_from_mapping(record))

            identity_row = identity_rows[0] if identity_rows else {}
            latest = identity_row.get("latest")
            identity = SnapshotIdentity(
                source_view=pg.CARD_VIEW,
                row_count=identity_row.get("n"),
                max_content_updated_at=_isoformat(latest),
                captured_at=captured_at,
            )
            frozen_cards = tuple(cards)
            self._export = CorpusExport(
                identity=identity,
                cards=frozen_cards,
                content_sha256=corpus_content_sha256(frozen_cards),
            )
            return self._export


def resolve_source(
    snapshot: str | Path | None = None, *, dsn: str | None = None
) -> CorpusSource:
    """Pick a corpus source from what the caller supplied.

    Args:
        snapshot: Path to a materialised snapshot. Wins when present.
        dsn: Postgres DSN, or ``None`` to take it from the environment.

    Returns:
        A :class:`CorpusSource`.
    """
    if snapshot is not None:
        return JsonCorpusSource(snapshot)
    return PostgresCorpusSource(dsn)


def names_missing_from(source: JsonCorpusSource, names: Sequence[str]) -> list[str]:
    """Fixture names the snapshot cannot resolve, sorted.

    A missing fixture is an error rather than a skipped case: a precision figure
    computed over the fixtures that happened to resolve is a different number
    than the one the tag claims, and nothing in the output would say so.
    """
    index = source.by_name()
    return sorted({name for name in names if name not in index})
