"""R2's immutable SQLite catalog: exact filters before learned retrieval."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from sabermetrics.mechanics.tags.predicates import FaceView
from sabermetrics.substrate import catalog
from sabermetrics.substrate.catalog import (
    CatalogBuildError,
    CatalogHit,
    CatalogNotFoundError,
    CatalogRecord,
    build_catalog,
    compile_fts_query,
    open_catalog,
)
from sabermetrics.substrate.corpus import CardView, SnapshotIdentity
from sabermetrics.substrate.models import CardFilters, Color, ColorMode
from sabermetrics.substrate.tagging import CoverageReport, TagBuild, TagRow


def _card(
    oracle_id: str,
    name: str,
    *,
    color_identity: tuple[str, ...] = (),
    mana_value: float = 0.0,
    type_line: str = "Instant",
    oracle_text: str | None = None,
    all_types: tuple[str, ...] = (),
    faces: tuple[FaceView, ...] = (),
    commander_legal: str | None = None,
) -> CardView:
    return CardView(
        oracle_id=oracle_id,
        name=name,
        mana_value=mana_value,
        type_line=type_line,
        oracle_text=oracle_text,
        color_identity=color_identity,
        all_types=all_types,
        faces=faces,
        commander_legal=commander_legal,
    )


def _tag_build(*pairs: tuple[str, str]) -> TagBuild:
    snapshot = SnapshotIdentity(source_view="test", row_count=5)
    snapshot_hash = snapshot.sha256()
    rows = tuple(
        TagRow(
            oracle_id=oracle_id,
            tag_id=tag_id,
            tag_version="1.0",
            confidence=1.0,
            matched_span='{"field":"oracle_text","start":0,"end":4,"text":"test"}',
            snapshot_hash=snapshot_hash,
        )
        for oracle_id, tag_id in sorted(pairs)
    )
    return TagBuild(
        rows=rows,
        coverage=CoverageReport(
            total_cards=5,
            tagged_cards=len({row.oracle_id for row in rows}),
            untagged_by_type=(),
            tag_counts=(),
            family_counts=(),
        ),
        snapshot=snapshot,
        library_sha256="library-hash",
        content_sha256="content-hash",
        tag_ids=tuple(sorted({row.tag_id for row in rows})),
    )


@pytest.fixture
def cards() -> tuple[CardView, ...]:
    return (
        _card(
            "z-colorless",
            "Mana Vault",
            mana_value=1,
            type_line="Artifact",
            oracle_text="Mana Vault doesn't untap during your untap step.",
            all_types=("Artifact",),
            commander_legal="legal",
        ),
        _card(
            "b-blue",
            "Counterspell",
            color_identity=("U",),
            mana_value=2,
            oracle_text="Counter target spell.",
            all_types=("Instant",),
            commander_legal="legal",
        ),
        _card(
            "c-izzet",
            "Engine Adept",
            color_identity=("R", "U"),
            mana_value=3,
            type_line="Artifact Creature — Wizard",
            oracle_text="Whenever you cast a spell, draw a card.",
            all_types=("Creature", "Artifact"),
            commander_legal="legal",
        ),
        _card(
            "d-green",
            "Counter Growth",
            color_identity=("G",),
            mana_value=4,
            type_line="Creature — Plant",
            oracle_text="Trample",
            all_types=("Creature",),
            commander_legal="banned",
        ),
        _card(
            "a-white",
            "Pathway Adept",
            color_identity=("W",),
            mana_value=2,
            type_line="Creature",
            all_types=("Creature",),
            faces=(
                FaceView(
                    name="Forgotten Path",
                    type_line="Land",
                    oracle_text="Exile target card from a graveyard.",
                ),
            ),
        ),
    )


@pytest.fixture
def tags() -> TagBuild:
    return _tag_build(
        ("z-colorless", "mana:mana_rock"),
        ("b-blue", "interact:hard_counter"),
        ("c-izzet", "draw:engine"),
        ("c-izzet", "mana:mana_rock"),
    )


@pytest.fixture
def catalog_path(
    tmp_path: Path,
    cards: tuple[CardView, ...],
    tags: TagBuild,
) -> Path:
    path = tmp_path / "cards.sqlite"
    build_catalog(path, cards, tags)
    return path


def _ids(rows: Sequence[CatalogRecord | CatalogHit]) -> list[str]:
    return [row.oracle_id for row in rows]


class TestCatalogSchema:
    def test_schema_is_normalized_indexed_and_has_fts(self, catalog_path: Path) -> None:
        connection = sqlite3.connect(catalog_path)
        try:
            objects = {
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT type, name FROM sqlite_master ORDER BY type, name"
                )
            }
            assert ("table", "card_document") in objects
            assert ("table", "card_type") in objects
            assert ("table", "card_tag") in objects
            assert ("table", "card_fts") in objects
            assert ("index", "idx_card_document_structured") in objects
            assert ("index", "idx_card_type_lookup") in objects
            assert ("index", "idx_card_tag_lookup") in objects

            document_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(card_document)")
            }
            assert {
                "oracle_id",
                "row_offset",
                "vector_offset",
                "canonical_document",
            } <= document_columns
            all_schema = "\n".join(
                str(row[0] or "")
                for row in connection.execute("SELECT sql FROM sqlite_master")
            )
            assert "price" not in all_schema.casefold()
            assert "price" not in CatalogHit.__dataclass_fields__
        finally:
            connection.close()

    def test_offsets_are_zero_based_and_deterministic_by_oracle_id(
        self,
        tmp_path: Path,
        cards: tuple[CardView, ...],
        tags: TagBuild,
    ) -> None:
        first = tmp_path / "first.sqlite"
        second = tmp_path / "second.sqlite"
        build_catalog(first, cards, tags)
        build_catalog(second, tuple(reversed(cards)), tags)

        with open_catalog(first) as left, open_catalog(second) as right:
            left_offsets = [
                (row.oracle_id, row.row_offset, row.vector_offset)
                for row in left.records()
            ]
            right_offsets = [
                (row.oracle_id, row.row_offset, row.vector_offset)
                for row in right.records()
            ]
        expected_ids = sorted(card.oracle_id for card in cards)
        assert left_offsets == right_offsets
        assert left_offsets == [
            (oracle_id, offset, offset) for offset, oracle_id in enumerate(expected_ids)
        ]

    def test_missing_legality_remains_unknown(self, catalog_path: Path) -> None:
        with open_catalog(catalog_path) as reader:
            white = reader.records(
                CardFilters(
                    allowed_oracle_ids=("a-white",),
                    commander_legal=None,
                )
            )[0]
            assert white.commander_legal is None
            assert reader.records(CardFilters(commander_legal=True))
            assert "a-white" not in _ids(
                reader.records(CardFilters(commander_legal=True))
            )
            assert "a-white" not in _ids(
                reader.records(CardFilters(commander_legal=False))
            )


class TestStructuredFiltering:
    @pytest.mark.parametrize(
        ("mode", "colors", "expected"),
        [
            (
                "subset",
                ("R", "U"),
                ["b-blue", "c-izzet", "z-colorless"],
            ),
            ("exact", ("U",), ["b-blue"]),
            (
                "intersects",
                ("U",),
                ["b-blue", "c-izzet"],
            ),
            ("exact", (), ["z-colorless"]),
            ("intersects", (), []),
        ],
    )
    def test_color_identity_modes(
        self,
        catalog_path: Path,
        mode: ColorMode,
        colors: tuple[Color, ...],
        expected: list[str],
    ) -> None:
        with open_catalog(catalog_path) as reader:
            actual = reader.records(CardFilters(color_identity=colors, color_mode=mode))
        assert _ids(actual) == expected

    def test_required_types_and_tags_are_all_of_and_exclusions_are_any_of(
        self, catalog_path: Path
    ) -> None:
        with open_catalog(catalog_path) as reader:
            assert _ids(
                reader.records(CardFilters(required_types=("ARTIFACT", "creature")))
            ) == ["c-izzet"]
            assert _ids(
                reader.records(
                    CardFilters(
                        required_tags=("mana:mana_rock",),
                        excluded_types=("creature",),
                    )
                )
            ) == ["z-colorless"]
            izzet = reader.records(
                CardFilters(required_tags=("draw:engine", "mana:mana_rock"))
            )
            either = reader.records(
                CardFilters(any_tags=("draw:engine", "interact:hard_counter"))
            )
        assert _ids(izzet) == ["c-izzet"]
        assert _ids(either) == ["b-blue", "c-izzet"]
        assert izzet[0].types == ("artifact", "creature")
        assert izzet[0].tags == ("draw:engine", "mana:mana_rock")

    def test_mana_legality_and_allowed_ids_compose(self, catalog_path: Path) -> None:
        filters = CardFilters(
            mana_value_min=2,
            mana_value_max=4,
            commander_legal=False,
            allowed_oracle_ids=("b-blue", "d-green", "not-present"),
        )
        with open_catalog(catalog_path) as reader:
            assert _ids(reader.records(filters)) == ["d-green"]
            assert _ids(reader.records(CardFilters(allowed_oracle_ids=()))) == [
                "b-blue",
                "c-izzet",
                "z-colorless",
            ]


class TestLexicalRetrieval:
    def test_name_hits_outrank_oracle_text_hits(self, catalog_path: Path) -> None:
        with open_catalog(catalog_path) as reader:
            hits = reader.search("counter")
        assert _ids(hits)[:2] == ["d-green", "b-blue"]
        assert hits[0].bm25_score < hits[1].bm25_score

    def test_equal_bm25_scores_tie_break_by_oracle_id(self, tmp_path: Path) -> None:
        tied = (
            _card("z", "Twin", oracle_text="Seek the answer."),
            _card("a", "Twin", oracle_text="Seek the answer."),
        )
        path = tmp_path / "ties.sqlite"
        build_catalog(path, tied, _tag_build())
        with open_catalog(path) as reader:
            assert _ids(reader.search("twin")) == ["a", "z"]

    def test_face_only_text_is_in_the_canonical_document_and_fts(
        self, catalog_path: Path
    ) -> None:
        with open_catalog(catalog_path) as reader:
            hits = reader.search("graveyard")
            record = reader.records(
                CardFilters(
                    allowed_oracle_ids=("a-white",),
                    commander_legal=None,
                )
            )[0]
        assert _ids(hits) == ["a-white"]
        assert "Forgotten Path" in record.canonical_document
        assert "graveyard" in record.canonical_document

    def test_plain_text_compiler_drops_raw_fts_syntax_and_punctuation(
        self, catalog_path: Path
    ) -> None:
        compiled = compile_fts_query('counter") OR card_fts:* --')
        assert compiled == '"counter" OR "fts"'
        with open_catalog(catalog_path) as reader:
            assert _ids(reader.search('counter"); DROP TABLE card_document; --')) == [
                "d-green",
                "b-blue",
            ]
            assert reader.search('")(*:^') == ()
            assert len(reader.records()) == 5


class TestAtomicArtifact:
    def test_failed_replace_leaves_previous_catalog_and_no_partial_file(
        self,
        tmp_path: Path,
        cards: tuple[CardView, ...],
        tags: TagBuild,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "cards.sqlite"
        build_catalog(path, cards[:1], _tag_build())

        def fail_replace(source: Path, destination: Path) -> None:
            raise OSError(f"refusing {source} -> {destination}")

        monkeypatch.setattr(catalog.os, "replace", fail_replace)
        with pytest.raises(CatalogBuildError, match="catalog build failed"):
            build_catalog(path, cards, tags)

        with open_catalog(path) as reader:
            assert _ids(reader.records()) == ["z-colorless"]
        assert not list(tmp_path.glob(".cards.sqlite.*.tmp*"))

    def test_open_reader_keeps_its_generation_across_atomic_replacement(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "cards.sqlite"
        old_card = _card("old", "Old Generation")
        new_card = _card("new", "New Generation")
        build_catalog(path, (old_card,), _tag_build())
        with open_catalog(path) as old_reader:
            build_catalog(path, (new_card,), _tag_build())
            assert _ids(old_reader.records()) == ["old"]
            with open_catalog(path) as new_reader:
                assert _ids(new_reader.records()) == ["new"]

    def test_failed_first_build_leaves_no_catalog(self, tmp_path: Path) -> None:
        path = tmp_path / "cards.sqlite"
        duplicate = (
            _card("same", "One"),
            _card("same", "Two"),
        )
        with pytest.raises(CatalogBuildError, match="unique"):
            build_catalog(path, duplicate, _tag_build())
        assert not path.exists()

    def test_absent_database_raises_visible_custom_exception(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "not-built.sqlite"
        with pytest.raises(CatalogNotFoundError, match="has not been built"):
            open_catalog(missing)
        assert not missing.exists()
