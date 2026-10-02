"""Card panel that follows hover and keyboard focus."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-card-panel.js"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
HARNESS = Path(__file__).with_name("card_panel_harness.mjs")
ISLAND_IMAGE = (
    "https://api.scryfall.com/cards/named?format=image&version=normal&exact=Island"
)


def _payload(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the card panel harness")
    result = subprocess.run(
        [node, str(HARNESS), str(PANEL_JS), str(BUILDER_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_initial_panel_shows_commander_or_empty_hint() -> None:
    payload = _payload("initial")
    shown = payload["shown"]
    assert shown["name"] == "Kinnan Test"
    assert shown["current"] == "entry-cmd"
    assert shown["hint"] is False
    assert shown["active"] == "sentinel"
    empty = payload["empty"]
    assert empty["name"] is None
    assert empty["current"] is None
    assert empty["hint"] == "Hover or focus a card to preview it"
    assert empty["active"] == "sentinel"


def test_hover_updates_panel_after_debounce_and_ignores_same_entry() -> None:
    payload = _payload("hover")
    assert payload["before"] == "Kinnan Test"
    assert payload["during"] == "Kinnan Test"
    assert payload["after"] == "Lotus Petal"
    assert payload["sameRender"] == payload["afterRender"]
    assert payload["stableSame"] == "Lotus Petal"
    assert payload["midSwitch"] == "Kinnan Test"
    assert payload["switched"] == "Sol Ring"
    assert payload["active"] == payload["activeStarted"] == "sentinel"


def test_focus_entry_updates_panel_immediately() -> None:
    payload = _payload("focus")
    assert payload["immediate"] == "Sol Ring"
    assert payload["current"] == "entry-ring"
    assert payload["afterWait"] == "Sol Ring"
    assert payload["active"] == "sentinel"


def test_panel_renders_name_cost_type_oracle_qty_zone_role() -> None:
    payload = _payload("fields")
    ring = payload["ring"]
    assert ring["name"] == "Sol Ring"
    assert ring["cost"] == "{1}"
    assert ring["type"] == "Artifact"
    assert ring["oracle"] == "Add {C}{C}."
    assert ring["qty"] == "2"
    assert ring["zone"] == "Ramp"
    assert ring["role"] == "Ramp"
    assert ring["alt"] == "Sol Ring"
    assert ring["loading"] == "lazy"
    assert ring["src"] == "https://cards.test/sol-ring.png"
    assert ring["costSymbols"] == 0
    assert payload["islandSrc"] == ISLAND_IMAGE

    symbols = _payload("fields_symbols")
    assert symbols["costSymbols"] == 1
    assert symbols["oracleSymbols"] == 2
    assert "{1}" not in symbols["cost"]
    assert "{C}" not in symbols["oracle"]
    assert symbols["name"] == "Sol Ring"
    assert symbols["qty"] == "2"
    assert symbols["zone"] == "Ramp"
    assert symbols["role"] == "Ramp"


def test_panel_lists_entry_validation_issues() -> None:
    payload = _payload("issues")
    assert payload["role"] == "note"
    assert payload["items"] == ["Not legal in Commander", "Too many copies"]


def test_pin_freezes_panel() -> None:
    payload = _payload("pin")
    assert payload["pressed"] == "true"
    assert payload["frozen"] == "Sol Ring"
    assert payload["currentFrozen"] == "entry-ring"
    assert payload["pressedAfter"] == "false"
    assert payload["after"] == "Lotus Petal"
    assert payload["label"] == "Pin card preview"


def test_image_error_shows_name_not_broken_image() -> None:
    css = CSS_PATH.read_text()
    assert "aspect-ratio:488 / 680" in css
    assert ".dl-card-panel-skeleton" in css
    payload = _payload("image_error")
    assert payload["before"]["alt"] == "Sol Ring"
    assert payload["before"]["loading"] == "lazy"
    assert payload["before"]["skeleton"] is True
    assert payload["before"]["loadingClass"] is True
    assert payload["imgLeft"] is False
    assert payload["fallback"] == "Sol Ring"
    assert payload["loadingClass"] is False
    assert payload["errorClass"] is True


def test_collapsed_rail_or_narrow_viewport_skips_work() -> None:
    payload = _payload("inactive")
    for key in ("collapsed", "narrow"):
        skipped = payload[key]
        assert skipped["children"] == 0
        assert skipped["text"] == ""
        assert skipped["preloads"] == []
        assert skipped["current"] is None
    assert payload["live"]["started"] == "Kinnan Test"
    assert payload["live"]["name"] == "Kinnan Test"
    assert payload["live"]["preloads"] == []


def test_evidence_slot_exists_and_is_empty() -> None:
    payload = _payload("evidence")
    assert payload["exists"] is True
    assert payload["sameNode"] is True
    assert payload["children"] == 0
    assert payload["text"] == ""


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _database(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "card-panel.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="panel@example.test",
        display_name="Panel",
        role="admin",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','Mana text','["G","U"]',1,1,
             'https://images.example.test/kinnan.jpg')""")
        conn.commit()
    return path, owner


def _assert_panel_markup(html: str) -> None:
    head = html.index("dl-builder-rail-head")
    panel = html.index("data-card-panel")
    curve = html.index('id="mana-curve"')
    assert head < panel < curve
    assert 'class="dl-card-panel"' in html
    assert 'aria-label="Card preview"' in html
    assert 'aria-live="polite"' in html
    builder = html.index("deck-lab-builder.js")
    script = html.index("deck-lab-card-panel.js")
    assert builder < script


def test_panel_container_on_owner_and_shared_pages(tmp_path, monkeypatch) -> None:
    path, owner = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, commander_card_id="kinnan")
    token = repo.create_share(owner, deck_id)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, owner)
    owner_page = client.get(f"/build/deck/{deck_id}")
    assert owner_page.status_code == 200
    _assert_panel_markup(owner_page.get_data(as_text=True))
    shared_page = client.get(f"/shared/deck/{token}")
    assert shared_page.status_code == 200
    shared_html = shared_page.get_data(as_text=True)
    assert 'data-shared="true"' in shared_html
    _assert_panel_markup(shared_html)


def test_panel_click_opens_existing_image_dialog() -> None:
    payload = _payload("dialog")
    assert payload["before"] == 0
    assert payload["after"] == 1
    assert payload["title"] == "Kinnan Test"
    assert payload["src"] == "https://cards.test/kinnan.png"
    assert payload["alt"] == "Kinnan Test"
    assert payload["panelSrc"] == payload["src"]
    assert payload["active"] == "sentinel"


def test_hover_preloads_only_hovered_image() -> None:
    payload = _payload("prefetch")
    assert payload["before"] == []
    assert payload["one"] == [payload["ring"]]
    assert payload["repeat"] == [payload["ring"]]
    assert payload["two"] == [payload["ring"], payload["lotus"]]
    assert payload["island"] not in payload["two"]
    assert payload["commander"] not in payload["two"]
