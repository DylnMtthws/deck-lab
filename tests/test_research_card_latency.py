"""Research Cards default: paged SQL equivalence and corpus-revision caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from sabermetrics import db, research
from sabermetrics.card_discovery import format_legal_sql
from sabermetrics.card_search import (
    ensure_card_search_schema,
    unique_legal_faces_page_sql,
    unique_legal_faces_sql,
)
from sabermetrics.research import ResearchRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture(autouse=True)
def _fresh_cache():
    research.reset_card_results_cache()
    yield
    research.reset_card_results_cache()


def _row(card_id, name, *, legal=1, image="img", cmc=2, type_line="Instant"):
    return (
        card_id,
        f"o-{name}",
        name,
        cmc,
        type_line,
        "",
        '["R"]',
        0,
        legal,
        image,
        "common",
    )


def _seed(path: Path, rows) -> None:
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri,rarity)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


def _database(tmp_path: Path, *, revision: bool = True) -> Path:
    path = tmp_path / "cards.db"
    setup_database(path)
    rows = []
    for i in range(40):
        name = f"{'abcdefgh'[i % 8]}Card {i:02d}"
        if i % 3 == 0:
            name = name.upper()
        # Two printings: the imageless lower id loses to the imaged printing.
        rows.append(_row(f"a{i:02d}", name, image=None, cmc=i % 5))
        rows.append(_row(f"b{i:02d}", name, cmc=i % 5))
    rows.append(_row("z-illegal", "Illegal Card", legal=0))
    rows.append(_row("mixed-1", "Mixed Legality", legal=0))
    rows.append(_row("mixed-2", "Mixed Legality", image=None))
    _seed(path, rows)
    with db.connect(path) as conn:
        if revision:
            ensure_card_search_schema(conn)
        else:
            for event in ("insert", "update", "delete"):
                conn.execute(f"DROP TRIGGER IF EXISTS card_catalog_rev_{event}")
            conn.execute("DROP TABLE IF EXISTS card_catalog_revision")
        conn.commit()
    return path


def _ids(result):
    return [row["id"] for row in result]


@pytest.mark.parametrize(
    ("extra", "values"),
    [
        ("", []),
        (" AND c.name LIKE ?", ["%card 1%"]),
        (" AND c.cmc <= ?", [1]),
        (" AND c.image_uri IS NULL", []),
    ],
)
def test_paged_faces_match_windowed_faces(tmp_path, extra, values):
    path = _database(tmp_path)
    where = format_legal_sql("c") + extra
    with db.connect(path) as conn:
        for per_page, offset in ((24, 0), (24, 24), (7, 14), (100, 0)):
            old = conn.execute(
                unique_legal_faces_sql(where)
                + " ORDER BY c.name COLLATE NOCASE, c.name LIMIT ? OFFSET ?",
                [*values, per_page, offset],
            ).fetchall()
            new = conn.execute(
                unique_legal_faces_page_sql(where),
                [*values, *values, per_page, offset],
            ).fetchall()
            assert [dict(r) for r in new] == [dict(r) for r in old]


def test_cards_prefers_imaged_printing_and_legal_faces(tmp_path):
    repo = ResearchRepo(_database(tmp_path))
    first = repo.cards("", per_page=24)
    assert first["total"] == 41
    assert first["has_next"] is True
    assert all(card_id.startswith("b") for card_id in _ids(first["results"])[:24])
    everything = repo.cards("", per_page=100)["results"]
    assert "z-illegal" not in _ids(everything)
    assert "mixed-2" in _ids(everything)
    assert first["stat_coverage"]["legal_cards"] == 41


def test_results_are_reused_until_the_card_catalog_changes(tmp_path, monkeypatch):
    path = _database(tmp_path)
    repo = ResearchRepo(path)
    coverage_runs = []
    original = research.unique_legal_faces_sql
    monkeypatch.setattr(
        research,
        "unique_legal_faces_sql",
        lambda *a, **k: coverage_runs.append(1) or original(*a, **k),
    )
    first = repo.cards("")
    first["results"][0]["name"] = "mutated by caller"
    again = repo.cards("")
    assert again["results"][0]["name"] != "mutated by caller"
    filtered = repo.cards("card 1")
    assert filtered["total"] == 10
    assert len(coverage_runs) == 1

    _seed(path, [_row("new", "aaa New Card")])
    fresh = repo.cards("")
    assert fresh["results"][0]["id"] == "new"
    assert fresh["total"] == first["total"] + 1
    assert fresh["stat_coverage"]["legal_cards"] == 42
    assert len(coverage_runs) == 2

    with db.connect(path) as conn:
        conn.execute("UPDATE cards SET is_legal_in_99=0 WHERE id='new'")
        conn.commit()
    assert repo.cards("")["total"] == first["total"]


def test_without_catalog_revision_nothing_is_cached(tmp_path, monkeypatch):
    path = _database(tmp_path, revision=False)
    repo = ResearchRepo(path)
    assert repo.cards("")["total"] == 41
    _seed(path, [_row("new", "aaa New Card")])
    assert repo.cards("")["total"] == 42
    assert not research._CARD_RESULTS


def test_default_research_tab_is_still_cards_and_cached(tmp_path, monkeypatch):
    path = _database(tmp_path)
    user = db.UsersRepo(path).create(
        email="cards@example.test", display_name="Cards", status="active"
    )
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    first = client.get("/research/")
    assert first.status_code == 200
    assert b'data-tab="cards"' in first.data
    assert b"aCard 00" in first.data or b"ACARD 00" in first.data
    assert research._CARD_RESULTS
    second = client.get("/research/")
    assert second.status_code == 200 and b'data-tab="cards"' in second.data
