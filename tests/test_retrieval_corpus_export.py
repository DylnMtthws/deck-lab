"""Atomic export tests for the R2 retrieval corpus."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest

from sabermetrics.cedh import adapters_postgres as pg
from sabermetrics.cedh import repositories
from sabermetrics.substrate.corpus import (
    CorpusExport,
    CorpusSource,
    JsonCorpusSource,
    PostgresCorpusSource,
)

CARD_ROWS: list[dict[str, Any]] = [
    {
        "oracle_id": "b",
        "name": "Beta // Beta Back",
        "layout": "modal_dfc",
        "mana_value": 2.0,
        "type_line": "Instant",
        "oracle_text": None,
        "colors": ["U", "W"],
        "color_identity": ["U", "W"],
        "keywords": ["Flash", "Flying"],
        "all_types": ["Instant"],
        "castable_cmcs": [2.0],
    },
    {
        "oracle_id": "a",
        "name": "Alpha",
        "layout": "normal",
        "mana_cost": "{G}",
        "mana_value": 1.0,
        "type_line": "Creature",
        "oracle_text": "Alpha text.",
        "colors": ["G"],
        "color_identity": ["G"],
        "keywords": [],
        "all_types": ["Creature"],
        "castable_cmcs": [1.0],
    },
]

FACE_ROWS: list[dict[str, Any]] = [
    {
        "oracle_id": "b",
        "face_index": 1,
        "name": "Beta Back",
        "mana_cost": None,
        "type_line": "Land",
        "oracle_text": "{T}: Add {U}.",
    },
    {
        "oracle_id": "b",
        "face_index": 0,
        "name": "Beta",
        "mana_cost": "{1}{U}",
        "type_line": "Instant",
        "oracle_text": "Draw a card.",
    },
]

LEGALITY_ROWS: list[dict[str, Any]] = [
    {"oracle_id": "b", "status": "banned"},
    {"oracle_id": "a", "status": "legal"},
]


class _FakeConnection:
    """Context-managed connection recording transaction usage."""

    def __init__(self) -> None:
        self.connection_entries = 0
        self.transaction_calls = 0
        self.transaction_entries = 0

    def __enter__(self) -> Self:
        self.connection_entries += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Record one transaction context."""
        self.transaction_calls += 1
        self.transaction_entries += 1
        yield


class _AdapterProbe:
    """Calls observed through a mocked cEDH Postgres adapter."""

    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.connect_calls: list[str | None] = []
        self.queries: list[tuple[str, dict[str, Any] | None]] = []
        self.guarded: list[str] = []


def _statement_kind(sql: str) -> str:
    if sql.startswith("SET TRANSACTION"):
        return "transaction"
    if "COUNT(*) AS n" in sql:
        return "identity"
    if f"FROM {pg.CARD_VIEW}" in sql:
        return "cards"
    if f"FROM {pg.FACE_VIEW}" in sql:
        return "faces"
    if f"FROM {pg.LEGALITY_VIEW}" in sql:
        return "legality"
    raise AssertionError(f"unexpected SQL: {sql}")


def _install_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    card_rows: list[dict[str, Any]] | None = None,
    face_rows: list[dict[str, Any]] | None = None,
    legality_rows: list[dict[str, Any]] | None = None,
) -> _AdapterProbe:
    probe = _AdapterProbe()
    cards = copy.deepcopy(CARD_ROWS if card_rows is None else card_rows)
    faces = copy.deepcopy(FACE_ROWS if face_rows is None else face_rows)
    legalities = copy.deepcopy(
        LEGALITY_ROWS if legality_rows is None else legality_rows
    )
    rows_by_kind = {
        "transaction": [],
        "identity": [
            {
                "n": len(cards),
                "latest": datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
            }
        ],
        "cards": cards,
        "faces": faces,
        "legality": legalities,
    }
    original_guard = repositories.assert_v1_only

    def fake_connect(dsn: str | None = None) -> _FakeConnection:
        probe.connect_calls.append(dsn)
        return probe.connection

    def fake_query(
        conn: Any,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        assert conn is probe.connection
        probe.queries.append((sql, params))
        return copy.deepcopy(rows_by_kind[_statement_kind(sql)])

    def recording_guard(sql: str) -> None:
        probe.guarded.append(sql)
        original_guard(sql)

    monkeypatch.setattr(pg, "_connect", fake_connect)
    monkeypatch.setattr(pg, "_query", fake_query)
    monkeypatch.setattr(repositories, "assert_v1_only", recording_guard)
    return probe


def _digest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    card_rows: list[dict[str, Any]],
    face_rows: list[dict[str, Any]],
    legality_rows: list[dict[str, Any]],
) -> str:
    _install_adapter(
        monkeypatch,
        card_rows=card_rows,
        face_rows=face_rows,
        legality_rows=legality_rows,
    )
    return PostgresCorpusSource("postgresql://consumer/test").export().content_sha256


def test_export_is_one_guarded_ordered_snapshot_and_preserves_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity-then-iteration shares one atomic materialization."""
    probe = _install_adapter(monkeypatch)
    source = PostgresCorpusSource("postgresql://consumer/test")

    identity = source.identity()
    iterated = tuple(source.iter_cards())
    exported = source.export()

    assert isinstance(source, CorpusSource)
    assert isinstance(exported, CorpusExport)
    assert identity is exported.identity
    assert iterated == exported.cards == tuple(exported.iter_cards())
    assert [card.oracle_id for card in iterated] == ["a", "b"]
    assert [face.name for face in iterated[1].faces] == ["Beta", "Beta Back"]
    assert [card.commander_legal for card in iterated] == ["legal", "banned"]

    assert probe.connect_calls == ["postgresql://consumer/test"]
    assert probe.connection.connection_entries == 1
    assert probe.connection.transaction_calls == 1
    assert probe.connection.transaction_entries == 1
    assert [_statement_kind(sql) for sql, _ in probe.queries] == [
        "transaction",
        "identity",
        "cards",
        "faces",
        "legality",
    ]
    assert probe.queries[-1][1] == {"format": "commander"}

    queried_sql = [sql for sql, _ in probe.queries]
    assert probe.guarded == queried_sql
    assert f"FROM {pg.CARD_VIEW}" in queried_sql[2]
    default_card_view = re.compile(r"\bFROM\s+mtg_v1\.card(?:\s|$)", re.IGNORECASE)
    assert not any(default_card_view.search(sql) for sql in queried_sql)


def test_export_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers cannot mutate the identity or card collection after hashing."""
    _install_adapter(monkeypatch)
    exported = PostgresCorpusSource().export()

    with pytest.raises(FrozenInstanceError):
        exported.content_sha256 = "changed"  # type: ignore[misc]
    assert isinstance(exported.cards, tuple)


def test_content_hash_is_stable_across_capture_and_row_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database delivery order and capture time are not corpus content."""
    first = _digest(
        monkeypatch,
        card_rows=copy.deepcopy(CARD_ROWS),
        face_rows=copy.deepcopy(FACE_ROWS),
        legality_rows=copy.deepcopy(LEGALITY_ROWS),
    )
    reordered_cards = list(reversed(copy.deepcopy(CARD_ROWS)))
    for key in ("colors", "color_identity", "keywords", "all_types"):
        reordered_cards[0][key] = list(reversed(reordered_cards[0][key]))
    second = _digest(
        monkeypatch,
        card_rows=reordered_cards,
        face_rows=list(reversed(copy.deepcopy(FACE_ROWS))),
        legality_rows=list(reversed(copy.deepcopy(LEGALITY_ROWS))),
    )

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_content_hash_moves_when_canonical_facts_or_legality_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Printed text and Commander legality are both retrieval facts."""
    original = _digest(
        monkeypatch,
        card_rows=copy.deepcopy(CARD_ROWS),
        face_rows=copy.deepcopy(FACE_ROWS),
        legality_rows=copy.deepcopy(LEGALITY_ROWS),
    )
    changed_cards = copy.deepcopy(CARD_ROWS)
    changed_cards[1]["oracle_text"] = "Different Alpha text."
    changed_text = _digest(
        monkeypatch,
        card_rows=changed_cards,
        face_rows=copy.deepcopy(FACE_ROWS),
        legality_rows=copy.deepcopy(LEGALITY_ROWS),
    )
    changed_legalities = copy.deepcopy(LEGALITY_ROWS)
    changed_legalities[1]["status"] = "not_legal"
    changed_legality = _digest(
        monkeypatch,
        card_rows=copy.deepcopy(CARD_ROWS),
        face_rows=copy.deepcopy(FACE_ROWS),
        legality_rows=changed_legalities,
    )

    assert changed_text != original
    assert changed_legality != original


def test_json_snapshot_carries_commander_legality_and_iterates(
    tmp_path: Path,
) -> None:
    """Existing snapshots expose legality when their records contain it."""
    path = tmp_path / "cards.json"
    path.write_text(
        json.dumps(
            {
                "snapshot": {"source_view": "fixture"},
                "cards": [
                    {
                        "oracle_id": "a",
                        "name": "Alpha",
                        "commander_legal": "legal",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    source = JsonCorpusSource(path)
    assert source.identity().source_view == "fixture"
    assert next(source.iter_cards()).commander_legal == "legal"
