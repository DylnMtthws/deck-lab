"""Compare a deck to recorded lists and replace a card with a same-role alternative."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.deck_evidence import DeckEvidenceService
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name("meta_compare_harness.mjs")
SCRIPT = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-meta-compare.js"
GREEN = '["G"]'
RED = '["R"]'
NONE = "[]"


def _harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the meta compare harness")
    result = subprocess.run(
        [node, str(HARNESS), scenario, str(SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _prepare(tmp_path: Path, *, event_size: bool = False) -> Path:
    path = tmp_path / "meta.db"
    setup_database(path)
    with db.connect(path) as conn:
        if event_size:
            conn.execute("ALTER TABLE tournament_results ADD COLUMN player_count INTEGER")
        conn.commit()
    return path


def _card(
    card_id: str,
    oracle_id: str,
    name: str,
    *,
    colors: str = NONE,
    legal: int = 1,
    commander: int = 0,
    oracle: str = "",
    type_line: str = "Artifact",
) -> tuple:
    return (card_id, oracle_id, name, 1, type_line, oracle, colors, commander, legal)


def _insert_cards(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """INSERT INTO cards
        (id,oracle_id,name,cmc,type_line,oracle_text,color_identity,
         is_legal_commander,is_legal_in_99)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def _result(
    conn: sqlite3.Connection,
    row_id: str,
    deck_id: str,
    commander_id: str,
    when: str,
    *,
    size: int | None = None,
) -> None:
    if size is None:
        conn.execute(
            """INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,tournament_date)
            VALUES(?,?,?,?,?)""",
            (row_id, row_id, deck_id, commander_id, when),
        )
        return
    conn.execute(
        """INSERT INTO tournament_results
        (id,tournament_id,deck_id,commander_id,tournament_date,player_count)
        VALUES(?,?,?,?,?,?)""",
        (row_id, row_id, deck_id, commander_id, when, size),
    )


def _cards(conn: sqlite3.Connection, deck_id: str, rows: list[tuple[str, int]]) -> None:
    conn.executemany(
        "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES(?,?,?,?)",
        [(deck_id, card_id, 1, flag) for card_id, flag in rows],
    )


def _entry(
    card_id: str,
    oracle_id: str,
    name: str,
    *,
    commander: bool = False,
    zone_id: str = "zone-main",
    role: str | None = None,
) -> dict:
    entry = {
        "id": f"entry-{card_id}",
        "card_id": card_id,
        "oracle_id": oracle_id,
        "name": name,
        "is_commander": commander,
        "zone_id": None if commander else zone_id,
        "color_identity": ["W", "U", "B", "R", "G"],
    }
    if role is not None:
        entry["role"] = role
    return entry


def _document(*entries: dict, zones: list[dict] | None = None) -> dict:
    return {
        "id": "deck-1",
        "source_kind": "text_import",
        "zones": zones
        or [
            {"id": "zone-main", "name": "Main"},
            {"id": "zone-considering", "name": "Considering"},
        ],
        "entries": list(entries),
    }


def test_missing_staples_threshold_identity_and_legality_filters(tmp_path: Path) -> None:
    path = _prepare(tmp_path, event_size=True)
    floor = load_cedh_settings().meta.min_event_size
    inside = date.today().isoformat()
    outside = (date.today() - timedelta(days=40)).isoformat()
    with db.connect(path) as conn:
        _insert_cards(
            conn,
            [
                _card("kinnan", "oracle-kinnan", "Kinnan", colors=GREEN, commander=1, legal=1, type_line="Legendary Creature"),
                _card("alpha", "oracle-alpha", "Alpha Staple", oracle="Add {C}."),
                _card("mid", "oracle-mid", "Mid Staple", oracle="Add {C}."),
                _card("low", "oracle-low", "Low Staple", oracle="Add {C}."),
                _card("banned", "oracle-banned", "Banned Card", legal=0, oracle="Add {C}."),
                _card("red", "oracle-red", "Red Card", colors=RED, oracle="Add {R}."),
                _card("library", "oracle-library", "Library Card", oracle="Add {C}."),
                _card("considering", "oracle-considering", "Considering Card", oracle="Add {C}."),
                _card("old", "oracle-old", "Old Card", oracle="Add {C}."),
                _card("floor", "oracle-floor", "Floor Card", oracle="Add {C}."),
            ],
        )
        shared = [
            ("kinnan", 1),
            ("alpha", 0),
            ("banned", 0),
            ("red", 0),
            ("library", 0),
            ("considering", 0),
        ]
        _cards(conn, "d1", shared + [("mid", 0), ("low", 0)])
        _cards(conn, "d2", shared + [("mid", 0)])
        _cards(conn, "d3", shared)
        _cards(conn, "d4", shared)
        _cards(conn, "d-old", [("kinnan", 1), ("old", 0)])
        _cards(conn, "d-small", [("kinnan", 1), ("floor", 0)])
        for index, deck_id in enumerate(("d1", "d2", "d3", "d4"), start=1):
            _result(conn, f"r{index}", deck_id, "kinnan", inside, size=floor)
        _result(conn, "old", "d-old", "kinnan", outside, size=floor)
        _result(conn, "small", "d-small", "kinnan", inside, size=floor - 1)
        conn.commit()
    document = _document(
        _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True),
        _entry("library", "oracle-library", "Library Card", role="ramp"),
        _entry(
            "considering",
            "oracle-considering",
            "Considering Card",
            zone_id="zone-considering",
            role="ramp",
        ),
    )
    result = DeckEvidenceService(path).meta_diff(document, 30)
    assert result["window_days"] == 30
    assert result["min_event_size"] == floor
    assert result["denominator"] == 4
    assert result["too_few_lists"] is True
    names = [item["name"] for item in result["missing_staples"]]
    assert names == ["Alpha Staple", "Considering Card", "Mid Staple"]
    alpha = result["missing_staples"][0]
    assert alpha["oracle_id"] == "oracle-alpha"
    assert alpha["lists"] == 4
    assert alpha["rate"] == 1
    assert alpha["role_guess"] == "ramp"
    assert result["missing_staples"][2]["rate"] == 0.5
    assert "Low Staple" not in names
    assert "Banned Card" not in names
    assert "Red Card" not in names
    assert "Library Card" not in names
    assert "Old Card" not in names
    assert "Floor Card" not in names
    assert "Kinnan" not in names


def test_unplayed_requires_min_denominator(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    with db.connect(path) as conn:
        _insert_cards(
            conn,
            [
                _card("kinnan", "oracle-kinnan", "Kinnan", commander=1, type_line="Legendary Creature"),
                _card("played", "oracle-played", "Played Card"),
                _card("lonely", "oracle-lonely", "Lonely Card"),
                _card("side", "oracle-side", "Side Card"),
            ],
        )
        for index in range(1, 10):
            deck_id = f"d{index}"
            _cards(conn, deck_id, [("kinnan", 1), ("played", 0)])
            _result(conn, f"r{index}", deck_id, "kinnan", inside)
        conn.commit()
    document = _document(
        _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True),
        _entry("played", "oracle-played", "Played Card", role="ramp"),
        _entry("lonely", "oracle-lonely", "Lonely Card", role="draw"),
        _entry("side", "oracle-side", "Side Card", zone_id="zone-considering"),
    )
    service = DeckEvidenceService(path)
    early = service.meta_diff(document, 30)
    assert early["denominator"] == 9
    assert early["too_few_lists"] is True
    assert early["unplayed"] == []

    with db.connect(path) as conn:
        _cards(conn, "d10", [("kinnan", 1), ("played", 0)])
        _result(conn, "r10", "d10", "kinnan", inside)
        conn.commit()
    later = service.meta_diff(document, 30)
    assert later["denominator"] == 10
    assert later["too_few_lists"] is False
    assert [item["name"] for item in later["unplayed"]] == ["Lonely Card"]
    assert later["unplayed"][0]["oracle_id"] == "oracle-lonely"
    assert "Played Card" not in [item["name"] for item in later["unplayed"]]
    assert "Side Card" not in [item["name"] for item in later["unplayed"]]
    assert "Kinnan" not in [item["name"] for item in later["unplayed"]]


def test_alternatives_share_role_and_exclude_deck_cards_and_off_identity(
    tmp_path: Path,
) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    with db.connect(path) as conn:
        _insert_cards(
            conn,
            [
                _card("kinnan", "oracle-kinnan", "Kinnan", colors=GREEN, commander=1, type_line="Legendary Creature"),
                _card("target", "oracle-target", "Target Card", oracle="Add {G}."),
                _card("high", "oracle-high", "High Ramp", oracle="Add {G}."),
                _card("low", "oracle-low", "Low Ramp", oracle="Add {G}{G}."),
                _card("draw", "oracle-draw", "Draw Card", oracle="Draw a card."),
                _card("red", "oracle-red", "Red Ramp", colors=RED, oracle="Add {R}."),
                _card("banned", "oracle-banned", "Banned Ramp", legal=0, oracle="Add {C}."),
                _card("held", "oracle-held", "Held Ramp", oracle="Add {W}."),
                _card("stored-draw", "oracle-stored-draw", "Stored Draw", oracle="Add {G}."),
                _card("stored-ramp", "oracle-stored-ramp", "Stored Ramp", oracle=""),
            ],
        )
        conn.execute("ALTER TABLE cards ADD COLUMN role_tags TEXT")
        conn.execute(
            "UPDATE cards SET role_tags=? WHERE id=?",
            (json.dumps(["draw"]), "stored-draw"),
        )
        conn.execute(
            "UPDATE cards SET role_tags=? WHERE id=?",
            (json.dumps(["ramp"]), "stored-ramp"),
        )
        present = [
            ("kinnan", 1),
            ("high", 0),
            ("draw", 0),
            ("red", 0),
            ("banned", 0),
            ("held", 0),
            ("stored-draw", 0),
        ]
        _cards(conn, "d1", present + [("low", 0), ("stored-ramp", 0)])
        _cards(conn, "d2", present + [("stored-ramp", 0)])
        _cards(conn, "d3", present)
        for index, deck_id in enumerate(("d1", "d2", "d3"), start=1):
            _result(conn, f"a{index}", deck_id, "kinnan", inside)
        conn.commit()
    document = _document(
        _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True),
        _entry("target", "oracle-target", "Target Card", role="Ramp"),
        _entry("held", "oracle-held", "Held Ramp", role="ramp"),
    )
    service = DeckEvidenceService(path)
    result = service.alternatives(document, "oracle-target", 30)
    assert result["role"] == "Ramp"
    assert result["denominator"] == 3
    assert [item["name"] for item in result["alternatives"]] == [
        "High Ramp",
        "Stored Ramp",
        "Low Ramp",
    ]
    assert result["alternatives"][0]["lists"] == 3
    assert result["alternatives"][0]["rate"] == 1
    assert result["alternatives"][2]["lists"] == 1
    assert result["alternatives"][2]["rate"] == pytest.approx(1 / 3)
    names = [item["name"] for item in result["alternatives"]]
    assert "Draw Card" not in names
    assert "Red Ramp" not in names
    assert "Banned Ramp" not in names
    assert "Held Ramp" not in names
    assert "Stored Draw" not in names
    assert "Target Card" not in names
    limited = service.alternatives(document, "oracle-target", 30, limit=1)
    assert [item["name"] for item in limited["alternatives"]] == ["High Ramp"]


def test_alternatives_needs_role_when_entry_has_no_role(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    with db.connect(path) as conn:
        _insert_cards(
            conn,
            [_card("kinnan", "oracle-kinnan", "Kinnan", commander=1, type_line="Legendary Creature")],
        )
        conn.commit()
    document = _document(_entry("kinnan", "oracle-kinnan", "Kinnan", commander=True))
    document["entries"].append(
        _entry("blank", "oracle-blank", "Blank Card", role="")
    )
    result = DeckEvidenceService(path).alternatives(document, "oracle-blank", 30)
    assert result == {"needs_role": True}


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def test_routes_owner_only(tmp_path: Path, monkeypatch) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    older = (date.today() - timedelta(days=40)).isoformat()
    with db.connect(path) as conn:
        _insert_cards(
            conn,
            [
                _card("kinnan", "oracle-kinnan", "Kinnan", commander=1, type_line="Legendary Creature"),
                _card("ring", "oracle-ring", "Sol Ring", oracle="Add {C}."),
            ],
        )
        _cards(conn, "recent", [("kinnan", 1), ("ring", 0)])
        _cards(conn, "aged", [("kinnan", 1), ("ring", 0)])
        _result(conn, "e1", "recent", "kinnan", inside)
        _result(conn, "e2", "aged", "kinnan", older)
        conn.commit()
    owner = db.UsersRepo(path).create(
        email="owner@example.test",
        display_name="Owner",
        role="admin",
        status="active",
    )
    other = db.UsersRepo(path).create(
        email="other@example.test",
        display_name="Other",
        role="admin",
        status="active",
    )
    deck_id = DeckDocumentRepo(path).create(owner, commander_card_id="kinnan")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    anonymous = client.get(f"/api/decks/{deck_id}/meta-diff")
    assert anonymous.status_code != 200
    _login(client, other)
    assert client.get(f"/api/decks/{deck_id}/meta-diff").status_code == 404
    assert client.get(f"/api/decks/{deck_id}/alternatives/oracle-ring").status_code == 404
    _login(client, owner)
    refused = client.get(f"/api/decks/{deck_id}/meta-diff?window=15")
    assert refused.status_code == 400
    response = client.get(f"/api/decks/{deck_id}/meta-diff")
    assert response.status_code == 200
    body = response.get_json()
    assert body["window_days"] == 30
    assert body["denominator"] == 1
    assert body["too_few_lists"] is True
    assert [item["name"] for item in body["missing_staples"]] == ["Sol Ring"]
    widened = client.get(f"/api/decks/{deck_id}/meta-diff?window=90")
    assert widened.status_code == 200
    assert widened.get_json()["denominator"] == 2
    needs_role = client.get(f"/api/decks/{deck_id}/alternatives/oracle-kinnan")
    assert needs_role.status_code == 200
    assert needs_role.get_json() == {"needs_role": True}
    page = client.get(f"/build/deck/{deck_id}")
    html = page.get_data(as_text=True)
    assert 'data-meta-compare-open' in html
    assert ">Compare to meta<" in html
    assert "data-meta-compare" in html
    assert html.index("deck-lab-builder.js") < html.index("deck-lab-meta-compare.js")
    assert re.search(
        r'<script src="[^"]*/deck-lab-meta-compare\.js" defer></script>',
        html,
    )


def test_dialog_add_staple_sends_add_command_to_destination() -> None:
    payload = _harness("add")
    assert "Staples you're missing" in payload["headings"]
    assert "In your list, in no recorded list" in payload["headings"]
    assert "In 8 of 10 recorded Kinnan lists, last 30 days" in payload["statement"]
    assert "≥ 16" in payload["statement"]
    assert payload["label"] == "Add Staple Card"
    assert payload["commands"] == [
        [{
            "type": "add_card",
            "card_id": "card-staple",
            "zone_id": "zone-side",
            "quantity": 1,
        }]
    ]
    assert payload["calls"] == ["/api/decks/deck-1/meta-diff?window=30"]


def test_replace_with_sends_single_batch_remove_and_add_same_zone_role() -> None:
    payload = _harness("replace")
    assert payload["label"] == "Replace Sol Ring with an alternative"
    assert payload["option"] == "Replace with Alt Card"
    assert "In 7 of 10 recorded Kinnan lists" in payload["statement"]
    assert payload["commands"] == [
        [
            {"type": "remove_entry", "entry_id": "entry-ring"},
            {
                "type": "add_card",
                "card_id": "card-alt",
                "zone_id": "zone-main",
                "quantity": 1,
                "role": "ramp",
            },
        ]
    ]
    assert len(payload["commands"]) == 1
    assert "/api/decks/deck-1/alternatives/oracle-ring?window=30" in payload["calls"]


def test_meta_compare_never_shows_rate_without_denominator() -> None:
    payload = _harness("rates")
    seen_percent = False
    for element in payload["elements"]:
        blob = f"{element['text']} {element['title']}"
        if "%" not in blob:
            continue
        seen_percent = True
        assert "10" in blob
        assert "last 30 days" in blob
        assert "≥ 16" in blob
    assert seen_percent
    assert payload["absencePercents"] == 0
    assert "No tournament evidence for this commander in the last 30 days" in payload["absence"]
    assert "%" not in payload["absence"]


def test_meta_compare_populates_when_loaded_after_ready() -> None:
    payload = _harness("late")
    assert payload["rendered"] is True
    assert payload["tag"] == "BUTTON"
    assert payload["text"] == "Replace with…"
    assert payload["label"] == "Replace Sol Ring with an alternative"
    assert payload["readyBound"] == 0


def test_bounded_queries_for_meta_diff(tmp_path: Path, monkeypatch) -> None:
    path = _prepare(tmp_path)
    with db.connect(path) as conn:
        _insert_cards(
            conn,
            [_card("kinnan", "oracle-kinnan", "Kinnan", commander=1, type_line="Legendary Creature")],
        )
        conn.commit()
    service = DeckEvidenceService(path)
    commander = _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True)
    real_connect = sqlite3.connect

    def statements(library: int) -> list[str]:
        entries = [commander]
        entries.extend(
            _entry(f"card-{index}", f"oracle-{index}", f"Card {index}")
            for index in range(library)
        )
        captured: list[str] = []

        def wrapped(target, *args, **kwargs):
            conn = real_connect(target, *args, **kwargs)
            conn.set_trace_callback(captured.append)
            return conn

        monkeypatch.setattr(sqlite3, "connect", wrapped)
        service.meta_diff(_document(*entries), 30)
        return list(captured)

    small = statements(1)
    large = statements(40)
    assert len(small) == len(large)
    assert len(large) <= 8
    aggregates = [sql for sql in large if "GROUP BY" in sql.upper()]
    assert len(aggregates) == 1
    assert aggregates[0].lower().count("oracle_id =") <= 2
    assert "oracle-39" not in aggregates[0]
