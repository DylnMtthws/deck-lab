"""D01 header, toolbar, view popover, selection bar, and menus."""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CONSIDERING_JS = (
    ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-considering.js"
)
HISTORY_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-history.js"
CHROME_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-chrome.css"
BUILDER_HTML = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
HARNESS = Path(__file__).with_name("design_toolbar_harness.mjs")
LAYOUT_HARNESS = Path(__file__).with_name("design_toolbar_layout.mjs")
STATIC = ROOT / "src" / "sabermetrics" / "ui" / "static"
ICONS_JS = STATIC / "deck-lab-icons.js"


def _payload() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the toolbar harness")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS), str(CONSIDERING_JS), str(ICONS_JS)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "toolbar harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_view_popover_controls_persist_via_update_view() -> None:
    payload = _payload()
    commands = payload["viewCommands"]
    assert {"type": "update_view", "group_mode": "role"} in commands
    assert {"type": "update_view", "sort_mode": "name"} in commands
    assert {"type": "update_view", "display_mode": "stacks"} in commands
    assert {"type": "update_view", "density": "comfortable"} in commands
    assert payload["label"] == "View · Role · Name"
    assert payload["displayPressed"] == "true"
    assert payload["densityPressed"] == "true"


def test_view_popover_in_playmat_shows_surface_outlines_dim_fit() -> None:
    shown = _payload()["playmatShown"]
    assert shown["paneHidden"] is False
    assert shown["decklistHidden"] is True
    assert shown["label"] == "Mat · Slate Grid"
    assert shown["surfaces"] >= 3
    assert shown["outlines"] is True
    assert shown["dim"] is True
    assert shown["fit"] is True


def test_popover_closes_on_escape_and_outside_click_and_restores_focus() -> None:
    payload = _payload()
    assert payload["opened"] is True
    assert payload["afterEscape"] == {"hidden": True, "focus": True}
    assert payload["afterOutside"] == {"hidden": True, "focus": True}


def test_selection_bar_replaces_toolbar_only_when_selected() -> None:
    payload = _payload()
    assert payload["atRest"] == {"barHidden": True, "mainHidden": False}
    assert payload["one"]["barHidden"] is False
    assert payload["one"]["mainHidden"] is True
    assert payload["one"]["count"] == "1 selected"
    assert payload["cleared"]["barHidden"] is True
    assert payload["cleared"]["mainHidden"] is False


def test_selection_bar_actions_move_considering_set_role_remove() -> None:
    payload = _payload()
    assert payload["moved"]
    assert all(item["type"] == "move_entry" for item in payload["moved"])
    assert {item["zone_id"] for item in payload["moved"]} == {"zone-ramp"}
    assert payload["roles"]
    assert all(
        item["type"] == "set_role" and item["role"] == "draw"
        for item in payload["roles"]
    )
    assert payload["removed"]
    assert all(item["type"] == "remove_entry" for item in payload["removed"])
    assert any(
        item["type"] == "create_zone" and item["name"] == "Considering"
        for item in payload["considered"]
    )
    assert any(item["type"] == "move_entry" for item in payload["considered"])
    assert payload["considerLabel"] in {"Move to Considering", "Move to deck"}


def test_destination_pill_menu_sets_add_destination() -> None:
    destination = _payload()["destination"]
    assert (
        destination["items"] == ["Unsorted", "Ramp"] or "Ramp" in destination["items"]
    )
    assert destination["value"] == "zone-ramp"
    assert destination["pill"] == "Ramp"
    assert "Ramp" in destination["hint"]


def test_deck_options_menu_has_tags_new_zone_commanders_share_delete() -> None:
    html = BUILDER_HTML.read_text()
    menu = re.search(
        r'class="dl-decklist-more dl-deck-options"[\s\S]*?</details>',
        html,
    )
    assert menu, "missing deck options menu"
    body = menu.group(0)
    for label in (
        "Tags…",
        "New zone",
        "Commanders…",
        "Compare to meta",
        "Share link",
        "Delete deck",
    ):
        assert label in body
    assert "data-tags-open" in body
    assert "data-new-zone" in body
    assert "data-commanders-open" in body
    assert "data-meta-compare-open" in body
    assert "data-share-deck" in body
    assert "builder.delete_deck" in body
    assert "dl-toolbar-action" not in html


def test_undo_redo_icon_buttons_with_tooltips_and_disabled_state() -> None:
    html = BUILDER_HTML.read_text()
    history = HISTORY_JS.read_text()
    for name in ("data-undo", "data-redo"):
        button = re.search(
            rf"<button\b[^>]*\b{name}\b[^>]*>[\s\S]*?</button>",
            html,
        )
        assert button, name
        markup = button.group(0)
        assert "<svg" in markup
        assert ">Undo<" not in markup and ">Redo<" not in markup
        assert "disabled" in markup
        assert "data-dl-tip=" in markup
        assert "aria-label=" in markup
    assert "undoBtn.disabled" in history
    assert 'undoBtn.setAttribute("data-dl-tip", undoTip)' in history
    assert "redoBtn.disabled" in history
    assert 'redoBtn.setAttribute("data-dl-tip", redoTip)' in history
    assert (
        "opacity: .4" in CHROME_CSS.read_text()
        or "opacity:.4" in CHROME_CSS.read_text()
    )


def test_export_menu_items_have_icons_labels_descriptions() -> None:
    html = BUILDER_HTML.read_text()
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    menu = header.split('id="deck-export-actions"', 1)[1].split("</details>", 1)[0]
    for label, description in (
        ("Copy list", "Moxfield, MTGO, plain text"),
        ("Copy for Archidekt", "With [Commander] tags"),
        ("Download .txt", "Plain text file"),
        ("Buy deck", "Opens Mana Pool"),
    ):
        assert label in menu
        assert description in menu
    assert menu.count("<svg") >= 4
    assert 'role="menu"' in header
    payload = _payload()
    assert payload["copied"] == ["1 Sol Ring"]
    assert payload["downloadPrevented"] is False
    assert "copyExportList" in BUILDER_JS.read_text()
    assert "data-export-download" in BUILDER_JS.read_text()


@functools.cache
def _layout() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to measure toolbar layout")
    result = subprocess.run(
        [node, str(LAYOUT_HARNESS), str(BUILDER_HTML), str(STATIC)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "toolbar layout harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_search_icon_does_not_overlap_input_text() -> None:
    for width, measured in _layout().items():
        assert measured["iconRight"] <= measured["textStart"] + 0.5, (
            width,
            measured,
        )


def test_view_button_immediately_follows_search_field() -> None:
    for width, measured in _layout().items():
        assert measured["nextIsView"] is True, (width, measured)
        assert -0.5 <= measured["gap"] <= 16, (width, measured)


def test_no_count_or_bulk_controls_visible_in_toolbar() -> None:
    html = BUILDER_HTML.read_text()
    toolbar = html.split('class="dl-builder-toolbar"', 1)[1].split(
        "</div>\n    </section>", 1
    )[0]
    assert "data-bulk-controls" not in html
    assert "data-bulk-move" not in html
    assert "dl-toolbar-action" not in html
    assert "0 cards selected" not in toolbar
    count = re.search(r"<span\b[^>]*data-deck-count[^>]*>", toolbar)
    assert count, "deck count stays in the toolbar for existing tests"
    css = CHROME_CSS.read_text()
    assert ".dl-builder-toolbar > .dl-deck-count" in css
    assert "width: 0 !important" in css
    assert "height: 0 !important" in css
