"""Tournament evidence badges and stored candidate reasons."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.cedh.responses import DeckExplanation
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.deck_evidence import DeckEvidenceService
from sabermetrics.research import ResearchRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name("deck_evidence_harness.mjs")
EVIDENCE_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-evidence.js"
RING = "oracle-ring"
PETAL = "oracle-petal"
OTHER = "oracle-other"


def _harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the evidence harness")
    result = subprocess.run(
        [node, str(HARNESS), scenario, str(EVIDENCE_JS)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _prepare(tmp_path: Path, *, event_size: bool = False) -> Path:
    path = tmp_path / "evidence.db"
    setup_database(path)
    with db.connect(path) as conn:
        if event_size:
            conn.execute(
                "ALTER TABLE tournament_results ADD COLUMN player_count INTEGER"
            )
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("kinnan", "oracle-kinnan", "Kinnan", 2, "Legendary Creature", "[]", 1, 1),
                ("tymna", "oracle-tymna", "Tymna", 3, "Legendary Creature", "[]", 1, 1),
                ("kraum", "oracle-kraum", "Kraum", 3, "Legendary Creature", "[]", 1, 1),
                ("bogus", "oracle-bogus", "Bogus", 2, "Creature", "[]", 0, 1),
                ("ring", RING, "Sol Ring", 1, "Artifact", "[]", 0, 1),
                ("petal", PETAL, "Lotus Petal", 0, "Artifact", "[]", 0, 1),
                ("other", OTHER, "Other Card", 1, "Instant", "[]", 0, 1),
                ("island", "oracle-island", "Island", 0, "Basic Land", "[]", 0, 1),
            ],
        )
        conn.commit()
    return path


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


def _document(*entries: dict) -> dict:
    return {"id": "deck-1", "source_kind": "text_import", "entries": list(entries)}


def _entry(
    card_id: str,
    oracle_id: str,
    name: str,
    *,
    commander: bool = False,
) -> dict:
    return {
        "id": card_id,
        "card_id": card_id,
        "oracle_id": oracle_id,
        "name": name,
        "is_commander": commander,
    }


def test_for_deck_counts_lists_with_window_and_event_floor(tmp_path: Path) -> None:
    path = _prepare(tmp_path, event_size=True)
    floor = load_cedh_settings().meta.min_event_size
    assert floor >= 1
    inside = date.today().isoformat()
    outside = (date.today() - timedelta(days=40)).isoformat()
    with db.connect(path) as conn:
        _cards(conn, "in-floor", [("kinnan", 1), ("ring", 0)])
        _cards(conn, "below-floor", [("kinnan", 1), ("ring", 0), ("other", 0)])
        _cards(conn, "outside", [("kinnan", 1), ("ring", 0)])
        _cards(conn, "no-ring", [("kinnan", 1), ("island", 0)])
        _result(conn, "r1", "in-floor", "kinnan", inside, size=floor)
        _result(conn, "r2", "below-floor", "kinnan", inside, size=floor - 1)
        _result(conn, "r3", "outside", "kinnan", outside, size=floor)
        _result(conn, "r4", "no-ring", "kinnan", inside, size=floor)
        conn.commit()
    document = _document(
        _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True),
        _entry("ring", RING, "Sol Ring"),
        _entry("other", OTHER, "Other Card"),
    )
    service = DeckEvidenceService(path)
    counted = service.for_deck(document, 30)
    assert counted["window_days"] == 30
    assert counted["min_event_size"] == floor
    assert counted["available"] is True
    assert counted["denominator"] == 2
    assert counted["cards"][RING] == {"lists": 1, "rate": 0.5}
    assert OTHER not in counted["cards"]

    lowered = service.for_deck(document, 30, floor - 1)
    assert lowered["min_event_size"] == floor - 1
    assert lowered["denominator"] == 3
    assert lowered["cards"][RING]["lists"] == 2
    assert lowered["cards"][OTHER]["lists"] == 1
    assert lowered["cards"][RING]["rate"] == pytest.approx(2 / 3)


def test_partner_commanders_require_both_in_list(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    outside = (date.today() - timedelta(days=40)).isoformat()
    with db.connect(path) as conn:
        _cards(conn, "both", [("tymna", 1), ("kraum", 1), ("ring", 0)])
        _cards(conn, "only-tymna", [("tymna", 1), ("ring", 0)])
        _cards(conn, "both-old", [("tymna", 1), ("kraum", 1), ("ring", 0)])
        _result(conn, "p1", "both", "tymna", inside)
        _result(conn, "p2", "only-tymna", "tymna", inside)
        _result(conn, "p3", "both-old", "tymna", outside)
        conn.commit()
    document = _document(
        _entry("tymna", "oracle-tymna", "Tymna", commander=True),
        _entry("kraum", "oracle-kraum", "Kraum", commander=True),
        _entry("ring", RING, "Sol Ring"),
    )
    result = DeckEvidenceService(path).for_deck(document, 30)
    assert result["available"] is True
    assert result["denominator"] == 1
    assert result["commander_ids"] == ["tymna", "kraum"]
    assert result["cards"][RING] == {"lists": 1, "rate": 1.0}


def test_unknown_commander_reports_unavailable_not_zero(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    with db.connect(path) as conn:
        _cards(conn, "bogus-deck", [("bogus", 1), ("ring", 0)])
        _result(conn, "b1", "bogus-deck", "bogus", inside)
        conn.commit()
    document = _document(
        _entry("bogus", "oracle-bogus", "Bogus", commander=True),
        _entry("ring", RING, "Sol Ring"),
    )
    result = DeckEvidenceService(path).for_deck(document, 30)
    assert result["available"] is False
    assert result["denominator"] == 0
    assert result["cards"] == {}
    assert result["min_event_size"] is None
    assert "0%" not in json.dumps(result)


def test_cards_with_zero_lists_omitted(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    outside = (date.today() - timedelta(days=40)).isoformat()
    with db.connect(path) as conn:
        _cards(conn, "has-ring", [("kinnan", 1), ("ring", 0)])
        _cards(conn, "old-other", [("kinnan", 1), ("other", 0)])
        _result(conn, "z1", "has-ring", "kinnan", inside)
        _result(conn, "z2", "old-other", "kinnan", outside)
        conn.commit()
    document = _document(
        _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True),
        _entry("ring", RING, "Sol Ring"),
        _entry("other", OTHER, "Other Card"),
    )
    result = DeckEvidenceService(path).for_deck(document, 30)
    assert result["available"] is True
    assert RING in result["cards"]
    assert OTHER not in result["cards"]
    assert result["cards"][RING]["lists"] > 0
    assert all(card["lists"] > 0 for card in result["cards"].values())


def _store_candidate(path: Path, explanation: dict) -> None:
    candidate = {
        "cards": [
            {
                "oracle_id": RING,
                "name": "Sol Ring",
                "role": "ramp",
                "source": "pool",
                "quantity": 1,
                "priority": 2.5,
            },
            {
                "oracle_id": PETAL,
                "name": "Lotus Petal",
                "role": "combo",
                "source": "auto_include",
                "quantity": 1,
                "priority": 1,
            },
        ]
    }
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cedh_candidates
            (candidate_id,owner_id,pack_id,commander_key,commander_name,deck_sha256,
             candidate_json,explanation_json)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                "cand-1",
                "owner",
                "pack",
                "kinnan",
                "Kinnan",
                "abc",
                json.dumps(candidate),
                json.dumps(explanation),
            ),
        )
        conn.commit()


def _candidate_document(source_kind: str) -> dict:
    document = _document(
        _entry("kinnan", "oracle-kinnan", "Kinnan", commander=True),
        _entry("ring", RING, "Sol Ring"),
        _entry("petal", PETAL, "Lotus Petal"),
    )
    document["source_kind"] = source_kind
    document["source_id"] = "cand-1"
    return document


def test_candidate_deck_includes_explanations_from_stored_candidate(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    explanation = DeckExplanation(
        game_plan="Present a combo.",
        primary_line="Cast the commander.",
        weaknesses=["Slow"],
        key_oracle_ids=[RING],
    ).model_dump()
    explanation["card_reasons"] = {RING: "Fast mana"}
    _store_candidate(path, explanation)
    result = DeckEvidenceService(path).for_deck(_candidate_document("candidate"), 30)
    assert result["explanations"][RING] == {
        "role": "ramp",
        "source": "pool",
        "priority": 2.5,
        "reason": "Fast mana",
    }
    assert result["explanations"][PETAL]["role"] == "combo"
    assert result["explanations"][PETAL]["source"] == "auto_include"
    assert result["explanations"][PETAL]["priority"] == 1.0
    assert result["explanations"][PETAL]["reason"] is None
    assert "oracle-kinnan" not in result["explanations"]


def test_non_candidate_deck_has_no_explanations(tmp_path: Path) -> None:
    path = _prepare(tmp_path)
    _store_candidate(
        path,
        DeckExplanation(
            game_plan="Present a combo.",
            primary_line="Cast the commander.",
        ).model_dump(),
    )
    result = DeckEvidenceService(path).for_deck(_candidate_document("generated"), 30)
    assert result["explanations"] == {}


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def test_route_owner_only_and_default_window(tmp_path: Path, monkeypatch) -> None:
    path = _prepare(tmp_path)
    inside = date.today().isoformat()
    older = (date.today() - timedelta(days=40)).isoformat()
    with db.connect(path) as conn:
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
    anonymous = client.get(f"/api/decks/{deck_id}/evidence")
    assert anonymous.status_code != 200
    _login(client, other)
    refused = client.get(f"/api/decks/{deck_id}/evidence")
    assert refused.status_code == 404
    _login(client, owner)
    response = client.get(f"/api/decks/{deck_id}/evidence")
    assert response.status_code == 200
    body = response.get_json()
    assert body["window_days"] == 30
    assert body["min_event_size"] is None
    assert body["available"] is True
    assert body["denominator"] == 1
    widened = client.get(f"/api/decks/{deck_id}/evidence?window=90")
    assert widened.status_code == 200
    widened_body = widened.get_json()
    assert widened_body["window_days"] == 90
    assert widened_body["denominator"] == 2
    page = client.get(f"/build/deck/{deck_id}")
    html = page.get_data(as_text=True)
    assert html.index("deck-lab-card-panel.js") < html.index("deck-lab-evidence.js")
    start, _end, _prior = ResearchRepo._scope(30)
    assert older < start <= inside


def test_single_aggregate_query_for_deck(tmp_path: Path, monkeypatch) -> None:
    path = _prepare(tmp_path)
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
        service.for_deck(_document(*entries), 30)
        return list(captured)

    small = statements(1)
    large = statements(40)
    assert len(small) == len(large)
    assert len(large) <= 5
    aggregates = [sql for sql in large if "GROUP BY" in sql.upper()]
    assert len(aggregates) == 1
    assert aggregates[0].lower().count("oracle_id =") <= 2


def test_badge_text_and_full_statement_title() -> None:
    payload = _harness("badge")
    badge = payload["badges"][0]
    assert badge["text"] == "71% · 143 lists"
    assert badge["title"] == (
        "In 102 of 143 recorded Kinnan lists, last 30 days, "
        "events with ≥ 16 players"
    )
    assert payload["statement"] == badge["title"]
    assert payload["role"] == "Ramp"
    assert payload["added"] == "Added by the generator as pool (priority 2.5)"
    assert payload["reason"] == "Fast mana"
    assert payload["unrecorded"].endswith("event size not recorded")
    assert "≥" not in payload["unrecorded"]


def test_panel_slot_shows_absence_message_when_unavailable() -> None:
    payload = _harness("absence")
    message = "No tournament evidence for this commander in the last 30 days"
    assert message in payload["before"]
    assert message in payload["after"]
    assert payload["badges"] == []
    assert "0%" not in payload["before"]
    assert "%" not in payload["before"]
    assert "Why this card?" in payload["before"]


def test_window_selector_refetches_and_shows_denominator() -> None:
    payload = _harness("window")
    initial = payload["initial"]
    assert initial["heading"] == "Tournament evidence"
    assert initial["label"] == "Tournament window"
    assert [item["value"] for item in initial["options"]] == ["30", "60", "90", "180", "0"]
    assert initial["options"][-1]["label"] == "All time"
    assert initial["urls"] == ["/api/decks/deck-1/evidence?window=30"]
    assert initial["basis"] == "Based on 10 recorded lists"
    assert initial["badge"]["text"] == "50% · 10 lists"
    assert payload["sameRender"] == {"urls": 1, "timers": 0}
    assert payload["beforeTimer"]["urls"] == 1
    assert payload["beforeTimer"]["timers"] == [500]
    assert payload["afterOracle"]["urls"] == [
        "/api/decks/deck-1/evidence?window=30",
        "/api/decks/deck-1/evidence?window=30",
    ]
    assert payload["afterWindow"]["urls"][-1] == "/api/decks/deck-1/evidence?window=90"
    assert payload["afterWindow"]["basis"] == "Based on 4 recorded lists"
    assert payload["afterWindow"]["badge"]["text"] == "25% · 4 lists"


def test_rate_never_rendered_without_denominator() -> None:
    payload = _harness("rates")
    seen_percent = False
    for element in payload["elements"]:
        text = element["text"] or ""
        if "%" not in text:
            continue
        seen_percent = True
        title = element["title"] or ""
        assert payload["denominator"] in text or payload["denominator"] in title
    assert seen_percent
