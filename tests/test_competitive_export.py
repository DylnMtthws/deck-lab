"""T01 — paste-ready export formats."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
EXPORT_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-export.js"
HARNESS = ROOT / "tests" / "export_format_harness.mjs"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)


# ---------------------------------------------------------------------------
# Fixture documents
# ---------------------------------------------------------------------------

def _fixture_document() -> dict[str, Any]:
    return {
        "id": "fixture-1",
        "title": "Test Deck",
        "revision": 0,
        "zones": [
            {"id": "z-main", "name": "Main", "sort_order": 0},
            {"id": "z-ramp", "name": "Ramp", "sort_order": 1},
            {"id": "z-maybe", "name": "Maybeboard", "sort_order": 2},
            {"id": "z-side", "name": "Sideboard", "sort_order": 3},
            {"id": "z-consider", "name": "Considering", "sort_order": 4},
        ],
        "entries": [
            {
                "id": "e-cmd", "name": "Kinnan, Bonder Prodigy",
                "is_commander": True, "quantity": 1, "zone_id": None,
                "type_line": "Legendary Creature", "color_identity": ["G", "U"],
            },
            {
                "id": "e-sol", "name": "Sol Ring",
                "is_commander": False, "quantity": 1, "zone_id": "z-main",
                "type_line": "Artifact", "color_identity": [],
            },
            {
                "id": "e-ring2", "name": "Sol Ring",
                "is_commander": False, "quantity": 1, "zone_id": "z-ramp",
                "type_line": "Artifact", "color_identity": [],
            },
            {
                "id": "e-isochron", "name": "Isochron Scepter",
                "is_commander": False, "quantity": 1, "zone_id": "z-main",
                "type_line": "Artifact", "color_identity": [],
            },
            {
                "id": "e-abrade", "name": "Abrade",
                "is_commander": False, "quantity": 1, "zone_id": "z-main",
                "type_line": "Instant", "color_identity": ["R"],
            },
            {
                "id": "e-maybe1", "name": "Jace, Wielder of Mysteries",
                "is_commander": False, "quantity": 1, "zone_id": "z-maybe",
                "type_line": "Legendary Planeswalker", "color_identity": ["U"],
            },
            {
                "id": "e-side1", "name": "Leyline of Anticipation",
                "is_commander": False, "quantity": 1, "zone_id": "z-side",
                "type_line": "Enchantment", "color_identity": ["U"],
            },
            {
                "id": "e-cons1", "name": "Questing Beast",
                "is_commander": False, "quantity": 1, "zone_id": "z-consider",
                "type_line": "Legendary Creature", "color_identity": ["G"],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Flask fixtures for route tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "export.db"
    setup_database(p)
    return p


@pytest.fixture
def export_app(db_path):
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    return app


@pytest.fixture
def export_client(export_app):
    return export_app.test_client()


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


# ---------------------------------------------------------------------------
# AC-1: sections format unchanged
# ---------------------------------------------------------------------------

def test_sections_format_unchanged() -> None:
    doc = _fixture_document()
    expected = (
        "// Test Deck\n"
        "\n"
        "Commander\n"
        "1 Kinnan, Bonder Prodigy\n"
        "\n"
        "Main\n"
        "1 Abrade\n"
        "1 Isochron Scepter\n"
        "1 Sol Ring\n"
        "\n"
        "Ramp\n"
        "1 Sol Ring\n"
        "\n"
        "Maybeboard\n"
        "1 Jace, Wielder of Mysteries\n"
        "\n"
        "Sideboard\n"
        "1 Leyline of Anticipation\n"
        "\n"
        "Considering\n"
        "1 Questing Beast\n"
    )
    result = DeckDocumentRepo.export_text(doc, fmt="sections")
    assert result == expected


# ---------------------------------------------------------------------------
# AC-2: plain format
# ---------------------------------------------------------------------------

def test_plain_format_commander_first_sorted_merged_no_private_zones() -> None:
    doc = _fixture_document()
    result = DeckDocumentRepo.export_text(doc, fmt="plain")
    lines = result.rstrip("\n").split("\n")
    # Single trailing newline
    assert result.endswith("\n")
    # No blank lines
    assert "\n\n" not in result.rstrip("\n")
    # No title, no headers
    assert "// Test Deck" not in result
    assert "Commander" not in result
    # Commander first
    assert lines[0] == "1 Kinnan, Bonder Prodigy"
    # Private zones excluded
    assert "Jace" not in result
    assert "Leyline" not in result
    assert "Questing" not in result
    # Sol Ring duplicated across two library zones — merged to quantity 2
    assert "2 Sol Ring" in result
    # Case-insensitive sort (Abrade < Isochron < Kinnan < Sol)
    names = [line.split(" ", 1)[1] for line in lines]
    assert names == [
        "Kinnan, Bonder Prodigy",
        "Abrade",
        "Isochron Scepter",
        "Sol Ring",
    ]


# ---------------------------------------------------------------------------
# AC-3: Archidekt format
# ---------------------------------------------------------------------------

def test_archidekt_format_marks_commanders() -> None:
    doc = _fixture_document()
    result = DeckDocumentRepo.export_text(doc, fmt="archidekt")
    assert "1x Kinnan, Bonder Prodigy [Commander]" in result
    assert "1x Abrade" in result
    assert "1x Isochron Scepter" in result
    assert "2x Sol Ring" in result
    assert "1x Abrade [Commander]" not in result
    assert "Jace" not in result


# ---------------------------------------------------------------------------
# AC-4/AC-5: route tests (Flask test client)
# ---------------------------------------------------------------------------

def test_route_format_param_plain_and_archidekt(
    export_app, export_client, db_path
) -> None:
    from sabermetrics import db
    uid = db.UsersRepo(db_path).create(
        email="export@local",
        display_name="export",
        role="user",
        status="active",
        password_hash=db.hash_password("password123"),
    )
    _login(export_client, uid)
    resp = export_client.post("/build/new", json={"title": "Export Test"})
    assert resp.status_code == 201
    deck_id = resp.get_json()["id"]
    for fmt in ("plain", "archidekt"):
        resp = export_client.get(
            f"/build/deck/{deck_id}/export.txt?format={fmt}"
        )
        assert resp.status_code == 200, f"format={fmt} failed"
        assert resp.mimetype == "text/plain"


def test_route_rejects_unknown_format(
    export_app, export_client, db_path
) -> None:
    from sabermetrics import db
    uid = db.UsersRepo(db_path).create(
        email="badfmt@local",
        display_name="badfmt",
        role="user",
        status="active",
        password_hash=db.hash_password("password123"),
    )
    _login(export_client, uid)
    resp = export_client.post("/build/new", json={"title": "Bad Format"})
    assert resp.status_code == 201
    deck_id = resp.get_json()["id"]
    resp = export_client.get(f"/build/deck/{deck_id}/export.txt?format=csv")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# AC-6: plain matches client copy-list (Node harness)
# ---------------------------------------------------------------------------

def _harness_payload() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the export harness")
    result = subprocess.run(
        [node, str(HARNESS), str(EXPORT_JS)],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "export harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_plain_matches_client_copy_list() -> None:
    payload = _harness_payload()
    doc = _fixture_document()
    server_plain = DeckDocumentRepo.export_text(doc, fmt="plain")
    assert payload["clientPlain"] == server_plain, (
        f"Client plain differs from server plain\n"
        f"Client: {payload['clientPlain']!r}\n"
        f"Server: {server_plain!r}"
    )


# ---------------------------------------------------------------------------
# AC-8: client Archidekt output matches server
# ---------------------------------------------------------------------------

def test_client_archidekt_lines() -> None:
    payload = _harness_payload()
    doc = _fixture_document()
    server_archidekt = DeckDocumentRepo.export_text(doc, fmt="archidekt")
    assert payload["clientArchidekt"] == server_archidekt, (
        f"Client archidekt differs from server archidekt\n"
        f"Client: {payload['clientArchidekt']!r}\n"
        f"Server: {server_archidekt!r}"
    )


# ---------------------------------------------------------------------------
# AC-7: builder menu has archidekt copy and download
# ---------------------------------------------------------------------------

def test_builder_menu_has_archidekt_copy_and_download() -> None:
    html = BUILDER_TEMPLATE.read_text()
    assert "data-export-copy-archidekt" in html
    assert "export.txt?format=plain" in html


# ---------------------------------------------------------------------------
# AC-9: Archidekt button copies text and sets status
# ---------------------------------------------------------------------------

def _builder_harness_payload(scenario: str = "boot") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the builder export harness")
    BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
    HARNESS = ROOT / "tests" / "export_builder_harness.mjs"
    result = subprocess.run(
        [node, str(HARNESS), str(EXPORT_JS), str(BUILDER_JS), scenario],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness {scenario} failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_archidekt_button_copies_text_and_sets_status() -> None:
    payload = _builder_harness_payload("ac9")
    doc = _fixture_document()
    expected = DeckDocumentRepo.export_text(doc, fmt="archidekt")
    assert payload["clipboardWrites"] == [expected]
    assert payload["statusText"] == "Copied for Archidekt"
    assert payload["statusNotice"] == "copied"


# ---------------------------------------------------------------------------
# AC-10: Archidekt button fallback when clipboard fails
# ---------------------------------------------------------------------------

def test_archidekt_button_falls_back_when_clipboard_fails() -> None:
    payload = _builder_harness_payload("ac10")
    doc = _fixture_document()
    expected = DeckDocumentRepo.export_text(doc, fmt="archidekt")
    assert payload["clipboardWrites"] == [expected]
    assert payload["statusText"] == "Copy unavailable. Select the list to copy."
    assert payload["fallbackOpened"] is True
    assert payload["fallbackText"] == expected


# ---------------------------------------------------------------------------
# AC-11: Export items blocked while save pending
# ---------------------------------------------------------------------------

def test_export_items_blocked_while_save_pending() -> None:
    payload = _builder_harness_payload("ac11")
    # Before save: neither blocked
    before = payload["beforeSave"]
    assert before["archButtonDisabled"] is False
    assert before["downloadAriaDisabled"] is None
    # During save: both blocked
    during = payload["duringSave"]
    assert during["archButtonDisabled"] is True
    assert during["downloadAriaDisabled"] == "true"
    assert during["statusText"] == "Saving…"
    # After save: both unblocked
    after = payload["afterSave"]
    assert after["archButtonDisabled"] is False
    assert after["downloadAriaDisabled"] is None
