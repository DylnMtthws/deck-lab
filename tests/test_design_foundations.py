"""D00 design foundations: tokens, stylesheets, icons, and rail panes."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
FOUNDATION = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-foundation.css"
ICONS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-icons.js"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
HARNESS = Path(__file__).with_name("design_foundations_harness.mjs")

TOKEN_NAMES = (
    "--panel-3",
    "--line-soft",
    "--faint",
    "--brand-soft",
    "--success-soft",
    "--warning-soft",
    "--t-11",
    "--t-12",
    "--t-13",
    "--t-14",
    "--t-16",
    "--t-20",
    "--t-28",
    "--s-1",
    "--s-2",
    "--s-3",
    "--s-4",
    "--s-5",
    "--s-6",
    "--r-sm",
    "--r-md",
    "--r-lg",
    "--r-pill",
    "--row-compact",
    "--row-comfy",
    "--shadow-pop",
    "--shadow-card",
    "--shadow-card-lift",
    "--focus",
    "--ease",
    "--dur-fast",
    "--dur",
)

ICON_NAMES = (
    "back",
    "undo",
    "redo",
    "search",
    "sliders",
    "more",
    "pin",
    "pin-off",
    "thumb-up",
    "thumb-down",
    "comment",
    "chevron-down",
    "x",
    "check",
    "plus",
    "minus",
    "grid",
    "stack",
    "spread",
    "layers",
    "alert",
    "filter",
    "copy",
    "download",
    "external",
    "image",
)

BUILDER_STYLESHEETS = (
    "deck-lab.css",
    "deck-lab-foundation.css",
    "deck-lab-chrome.css",
    "deck-lab-list.css",
    "deck-lab-rail.css",
    "deck-lab-statusbar.css",
    "deck-lab-stacks.css",
    "deck-lab-playmat.css",
    "deck-lab-dialogs.css",
)


def _root_block(css: str) -> str:
    match = re.search(r":root\s*\{", css)
    assert match, "deck-lab.css has no :root block"
    start = match.end()
    depth = 1
    index = start
    while index < len(css) and depth:
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
        index += 1
    return css[start : index - 1]


def _stylesheet_order(html: str) -> list[str]:
    return re.findall(
        r'<link rel="stylesheet" href="[^"]*/([\w.-]+\.css)"',
        html,
    )


def _harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the foundations harness")
    result = subprocess.run(
        [node, str(HARNESS), scenario, str(ICONS), str(BUILDER_JS)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _client(tmp_path: Path, monkeypatch):
    path = tmp_path / "foundations.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="foundations@example.test",
        display_name="Foundations",
        role="admin",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','','["G","U"]',1,1)""")
        conn.commit()
    deck_id = DeckDocumentRepo(path).create(owner, commander_card_id="kinnan")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    monkeypatch.setenv("SABER_DECK_LAB_RESEARCH", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, owner)
    return client, deck_id


def test_tokens_present_in_root() -> None:
    block = _root_block(CSS.read_text())
    missing = [name for name in TOKEN_NAMES if not re.search(rf"{name}\s*:", block)]
    assert missing == []


def test_area_stylesheets_linked_in_order(tmp_path, monkeypatch) -> None:
    client, deck_id = _client(tmp_path, monkeypatch)
    page = client.get(f"/build/deck/{deck_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    order = _stylesheet_order(html)
    positions = [order.index(name) for name in BUILDER_STYLESHEETS]
    assert positions == sorted(positions)
    assert [order[index] for index in positions] == list(BUILDER_STYLESHEETS)
    assert 'class="dl-builder-toolbar" data-lint-bar' in html
    assert "data-status-bar data-lint-bar" in html
    research = client.get("/research/")
    assert research.status_code == 200
    research_html = research.get_data(as_text=True)
    research_order = _stylesheet_order(research_html)
    assert research_order.index("deck-lab-foundation.css") < research_order.index(
        "deck-lab-dialogs.css"
    )
    assert re.search(
        r'<link rel="stylesheet" href="[^"]*/deck-lab-foundation\.css">',
        research_html,
    )
    assert re.search(
        r'<link rel="stylesheet" href="[^"]*/deck-lab-dialogs\.css">',
        research_html,
    )


def test_icons_module_exposes_all_names_and_valid_svg() -> None:
    payload = _harness("icons")
    assert payload["names"] == list(ICON_NAMES)
    assert payload["unknown"]
    assert "Unknown Deck Lab icon" in payload["unknown"]
    assert payload["defaultSize"] == "16"
    assert len(payload["drawn"]) == len(ICON_NAMES)
    for icon in payload["drawn"]:
        assert icon["tag"] == "svg"
        assert icon["viewBox"] == "0 0 24 24"
        assert icon["fill"] == "none"
        assert icon["stroke"] == "currentColor"
        assert icon["strokeWidth"] == "1.7"
        assert icon["linecap"] == "round"
        assert icon["width"] == "20"
        assert icon["height"] == "20"
        assert (
            "<path" in icon["markup"]
            or "<circle" in icon["markup"]
            or "<rect" in icon["markup"]
        )


def test_icons_loaded_before_builder(tmp_path, monkeypatch) -> None:
    client, deck_id = _client(tmp_path, monkeypatch)
    html = client.get(f"/build/deck/{deck_id}").get_data(as_text=True)
    icons = re.search(r'<script src="[^"]*/deck-lab-icons\.js" defer></script>', html)
    builder = re.search(
        r'<script src="[^"]*/deck-lab-builder\.js" defer></script>', html
    )
    assert icons and builder
    assert icons.start() < builder.start()


def test_rail_section_tab_option_routes_into_pane_and_falls_back() -> None:
    panes = _harness("rail-panes")
    assert panes["card"]["parentPane"] == "card"
    assert panes["card"]["heading"] == "Preview"
    assert panes["same"] is True
    assert panes["headingKept"] == "Preview"
    assert panes["count"] == 1
    assert panes["deck"]["parentPane"] == "deck"
    assert panes["deck"]["heading"] == "Mana curve"
    assert panes["tools"]["parentPane"] == "tools"
    assert panes["missingPane"]["parentPane"] is None
    assert panes["missingPane"]["parentClass"] == "dl-stats-rail"

    fallback = _harness("rail-fallback")
    assert fallback["same"] is True
    assert fallback["onRail"] is True
    assert fallback["pane"] is None
    assert fallback["first"]["heading"] == "Filter Cards"
    assert fallback["first"]["parentClass"] == "dl-stats-rail"


def test_no_text_below_11px_in_foundation_css() -> None:
    css = re.sub(r"/\*.*?\*/", "", FOUNDATION.read_text(), flags=re.DOTALL)
    sizes: list[float] = []
    sizes.extend(
        float(match) for match in re.findall(r"font-size\s*:\s*([0-9.]+)px", css)
    )
    for shorthand in re.findall(r"font\s*:\s*([^;}]*)", css):
        sizes.extend(
            float(match) for match in re.findall(r"(?<![\w.-])([0-9.]+)px", shorthand)
        )
    below = [size for size in sizes if size < 11]
    assert below == []
