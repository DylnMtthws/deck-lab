"""D06 playmat restyle: zone chrome, card lift, stacks, peek, and FLIP."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
PLAYMAT_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-playmat.css"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CONSIDERING_JS = (
    ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-considering.js"
)
LAB_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
ICONS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-icons.js"
HARNESS = Path(__file__).with_name("design_playmat_harness.mjs")
NEW_ZONE = (360.0, 240.0)
COMMAND_BOTTOM = 258.0

EXISTING_PLAYMAT_SUITES = (
    "tests/test_deck_lab_drag_preview.py",
    "tests/test_feedback_playmat_review.py",
    "tests/test_feedback_workspace.py",
    "tests/test_feedback_workspace_integration.py",
    "tests/test_account_playmats.py",
    "tests/test_commander_zone.py",
    "tests/test_menu_dismissal.py",
    "tests/test_deck_validation_indicators.py",
    "tests/test_builder_feedback_chrome.py",
    "tests/test_builder_selection_and_preview.py",
    "tests/test_deck_lab_redesign.py",
)


def _css() -> str:
    return PLAYMAT_CSS.read_text()


def _reduced_motion_block(css: str) -> str:
    match = re.search(r"@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)\s*\{", css)
    assert match, "playmat css has no reduced-motion block"
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


def _harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the playmat harness")
    result = subprocess.run(
        [
            node,
            str(HARNESS),
            scenario,
            str(ICONS),
            str(BUILDER_JS),
            str(CONSIDERING_JS),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_zone_header_sans_title_count_chip_icon_buttons_with_tooltips() -> None:
    css = _css()
    assert "height: 36px" in css
    assert "font-family: var(--sans)" in css
    assert "font-size: var(--t-13)" in css
    assert "font-weight: 600" in css
    assert "text-transform: none" in css
    header = _harness("header")
    assert header["title"] == "Lands"
    assert "dl-chip" in header["countClass"]
    assert header["countText"] == "2"
    assert header["grid"]["viewBox"] == "0 0 24 24"
    assert 'width="6.5"' in header["grid"]["markup"]
    assert header["grid"]["label"] == "Grid cards in Lands"
    assert header["grid"]["tip"] == header["grid"]["label"]
    assert header["grid"]["pressed"] == "false"
    assert header["stackToggle"]["viewBox"] == "0 0 24 24"
    assert 'x="7" y="7"' in header["stackToggle"]["markup"]
    assert header["stackToggle"]["label"] == "Stack cards in Lands"
    assert header["stackToggle"]["tip"] == header["stackToggle"]["label"]
    assert header["spreadToggle"]["label"] == "Spread cards in Stack"
    assert header["spreadToggle"]["viewBox"] == "0 0 24 24"
    assert "m7 19" in header["spreadToggle"]["markup"]
    assert header["more"]["viewBox"] == "0 0 24 24"
    assert 'cx="18" cy="12"' in header["more"]["markup"]
    assert header["more"]["label"] == "Lands actions"
    assert header["more"]["tip"] == "Lands actions"
    assert "Rename zone" in header["menuItems"]
    assert "Sort by mana value" in header["menuItems"]
    assert any(item.startswith("Select all ") for item in header["menuItems"])
    assert "Delete zone" in header["menuItems"]


def test_card_hover_lift_and_reduced_motion_none() -> None:
    css = _css()
    assert "translateY(-4px)" in css
    assert "var(--shadow-card-lift)" in css
    assert "var(--dur)" in css
    assert "border-radius: 4.5% / 3.2%" in css
    assert "var(--shadow-card)" in css
    reduced = _reduced_motion_block(css)
    assert "transform: none" in reduced
    assert "transition: none" in reduced
    assert "animation: none" in reduced


def test_drag_source_dims_preview_tilted_drop_zone_glows() -> None:
    css = _css()
    assert re.search(r"\.dl-mat-card\.drag-source\s*\{[^}]*opacity:\s*\.4", css)
    assert "rotate(3deg)" in css
    preview = css.split(".dl-drag-preview", 1)[1].split("}", 1)[0]
    assert "var(--shadow-card-lift)" in preview
    drop = css.split(".dl-mat-zone.drop-target", 1)[1].split("}", 1)[0]
    assert "inset" in drop
    assert "var(--brand-soft)" in drop
    assert "var(--brand)" in drop


def test_stack_depth_offsets_up_to_6_and_plus_n_badge() -> None:
    stack = _harness("stack")
    assert stack["count"] == 10
    offsets = stack["offsets"]
    assert offsets[0]["x"] == "0px" and offsets[0]["y"] == "0px"
    assert offsets[6]["x"] == "18px" and offsets[6]["y"] == "18px"
    assert offsets[9]["x"] == "18px" and offsets[9]["y"] == "18px"
    assert [item["peek"] for item in offsets[:5]] == ["0", "1", "2", "3", "4"]
    assert all(item["peek"] is None for item in offsets[5:])
    assert stack["badge"] == "+3"
    assert stack["badgeLabel"] == "3 more cards in the stack"


def test_stack_peek_on_hover_300ms_and_focus_fans_top_5_without_command() -> None:
    css = _css()
    assert "translateX(calc(var(--peek-index) * 26px))" in css
    assert ".is-peek" in css
    assert "var(--dur)" in css
    peek = _harness("peek")
    assert peek["immediate"] is False
    assert peek["early"] is False
    assert peek["opened"] is True
    assert peek["closed"] is False
    assert peek["focused"] is True
    assert peek["peekedIndexes"][:5] == ["0", "1", "2", "3", "4"]
    assert all(index is None for index in peek["peekedIndexes"][5:])
    assert peek["layout"] == "fan"
    assert peek["fetches"] == 0
    assert peek["commands"] == []


def test_drag_card_out_of_stack_moves_entry_to_target_zone() -> None:
    drag = _harness("drag")
    assert drag["during"]["peek"] is True
    assert drag["during"]["dragSource"] is True
    assert drag["during"]["preview"] is True
    assert drag["over"]["dropTarget"] is True
    assert drag["over"]["dropEffect"] == "move"
    assert drag["entryId"] == "d2"
    assert drag["commands"] == [
        [
            {
                "type": "move_entry",
                "entry_id": "d2",
                "zone_id": "zone-lands",
                "sort_order": 999,
            }
        ]
    ]


def test_spread_stack_toggle_sends_set_zone_layout_and_animates_flip() -> None:
    css = _css()
    assert ".dl-flip" in css
    assert "dl-flip-run" in css
    flip_rule = css.split(".dl-mat-card.dl-flip.dl-flip-run", 1)[1].split("}", 1)[0]
    assert "var(--dur)" in flip_rule
    reduced = _reduced_motion_block(css)
    assert "transform: none" in reduced
    flip = _harness("flip")
    assert flip["commands"][0] == [
        {"type": "set_zone_layout", "zone_id": "zone-main", "layout": "fan"}
    ]
    assert flip["animated"]
    assert all(
        card["flip"] and card["run"] and card["dataFlip"] == "1"
        for card in flip["animated"]
    )
    assert flip["commands"][1] == [
        {"type": "set_zone_layout", "zone_id": "zone-main", "layout": "spread"}
    ]
    assert flip["reduced"]
    assert all(not card["flip"] and not card["run"] for card in flip["reduced"])
    assert flip["layout"] == "spread"


def test_existing_playmat_interactions_unchanged() -> None:
    """Suites that must stay green: test_deck_lab_drag_preview,
    test_feedback_playmat_review, test_feedback_workspace*,
    test_account_playmats, test_commander_zone, test_menu_dismissal,
    test_deck_validation_indicators, test_builder_feedback_chrome,
    test_builder_selection_and_preview, and test_deck_lab_redesign.
    """
    missing = [rel for rel in EXISTING_PLAYMAT_SUITES if not (ROOT / rel).is_file()]
    assert not missing
    js = BUILDER_JS.read_text()
    for needle in (
        "move_zone",
        "set_zone_layout",
        "move_entry",
        "text/deck-entry",
        "text/card-id",
        "metaKey",
        "Rename zone",
        "Select all ",
        "Sort by mana value",
        "Delete zone",
        "dl-mat-card-quantity",
        "applyCardValidity",
        "update_presentation",
        "playmat_id",
    ):
        assert needle in js, needle


def _zone_bottom(zone: dict) -> float:
    height = NEW_ZONE[1] if zone.get("height") is None else float(zone["height"])
    return float(zone["y"]) + height


def _clear_of(
    rect: tuple[float, float, float, float],
    other: tuple[float, float, float, float],
    gutter: float = 16.0,
) -> bool:
    ax, ay, aw, ah = rect
    bx, by, bw, bh = other
    return (
        ax + aw + gutter <= bx
        or bx + bw + gutter <= ax
        or ay + ah + gutter <= by
        or by + bh + gutter <= ay
    )


def _mat_rect(rect: dict, mat: dict, zoom: float) -> tuple[float, float, float, float]:
    return (
        (rect["left"] - mat["left"]) / zoom,
        (rect["top"] - mat["top"]) / zoom,
        rect["width"] / zoom,
        rect["height"] / zoom,
    )


def _assert_free_rendered_slot(result: dict) -> None:
    created = result["create"]
    assert created["type"] == "create_zone"
    assert result["zoom"] == 0.5
    assert result["mat"]["left"] == 36
    assert result["mat"]["top"] == 48
    placed = (float(created["x"]), float(created["y"]), NEW_ZONE[0], NEW_ZONE[1])
    rendered = [
        _mat_rect(zone, result["mat"], result["zoom"]) for zone in result["zones"]
    ]
    assert any(rect[2] >= 720 for rect in rendered)
    for rect in rendered:
        assert _clear_of(placed, rect), (placed, rect)


def _owner_deck(tmp_path: Path) -> tuple[DeckDocumentRepo, str, str]:
    path = tmp_path / "playmat-zones.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="playmat@example.test", display_name="Playmat", status="active"
    )
    repo = DeckDocumentRepo(path)
    return repo, owner, repo.create(owner, title="Free space")


def test_ui_new_zone_lands_in_free_rendered_space() -> None:
    result = _harness("new-zone")
    assert result["create"]["name"] == "Ramp"
    _assert_free_rendered_slot(result)
    assert result["create"]["x"] == result["spot"]["x"]
    assert result["create"]["y"] == result["spot"]["y"]


def test_considering_uses_free_zone_position_when_available() -> None:
    result = _harness("considering-free")
    assert result["create"]["name"] == "Considering"
    assert result["spot"] is not None
    _assert_free_rendered_slot(result)
    assert result["create"]["x"] == result["spot"]["x"]
    assert result["create"]["y"] == result["spot"]["y"]


def test_server_fallback_places_new_zone_below_all_and_grows_canvas(
    tmp_path: Path,
) -> None:
    repo, owner, deck_id = _owner_deck(tmp_path)
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="below-all",
        commands=[
            {"type": "create_zone", "name": "Alpha", "zone_id": "alpha"},
            {"type": "create_zone", "name": "Beta", "zone_id": "beta"},
            {"type": "create_zone", "name": "Gamma", "zone_id": "gamma"},
            {"type": "create_zone", "name": "Delta", "zone_id": "delta"},
        ],
    )
    zones = {zone["name"]: zone for zone in document["zones"]}
    assert set(zones) == {"Unsorted", "Alpha", "Beta", "Gamma", "Delta"}
    seen = [zones["Unsorted"]]
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        zone = zones[name]
        assert zone["x"] == 18
        lowest = max([COMMAND_BOTTOM, *(_zone_bottom(earlier) for earlier in seen)])
        assert zone["y"] == lowest + 24
        for earlier in seen:
            assert zone["y"] > _zone_bottom(earlier)
        assert zone["y"] > COMMAND_BOTTOM
        seen.append(zone)
    assert zones["Delta"]["y"] > 900
    assert document["presentation"]["canvas_height"] == zones["Delta"]["y"] + 264

    commanded = repo.create(owner, title="Command floor")
    with db.connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE deck_zones SET y=0, height=10, width=1400 WHERE deck_id=?",
            (commanded,),
        )
        conn.execute(
            "UPDATE deck_presentations SET canvas_height=100 WHERE deck_id=?",
            (commanded,),
        )
        conn.commit()
    grown = repo.apply_commands(
        owner,
        commanded,
        expected_revision=0,
        mutation_id="grow-canvas",
        commands=[{"type": "create_zone", "name": "Below", "zone_id": "below"}],
    )
    below = next(zone for zone in grown["zones"] if zone["name"] == "Below")
    assert below["x"] == 18
    assert below["y"] == COMMAND_BOTTOM + 24
    assert below["y"] > 0 + 10
    assert grown["presentation"]["canvas_height"] == below["y"] + 264


def test_create_zone_with_explicit_position_is_honoured(tmp_path: Path) -> None:
    repo, owner, deck_id = _owner_deck(tmp_path)
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="explicit-origin",
        commands=[
            {
                "type": "create_zone",
                "name": "Pinned",
                "zone_id": "pinned",
                "x": 733,
                "y": 411,
            },
            {
                "type": "create_zone",
                "name": "Origin",
                "zone_id": "origin",
                "x": 0,
                "y": 0,
            },
        ],
    )
    zones = {zone["name"]: zone for zone in document["zones"]}
    assert zones["Pinned"]["x"] == 733
    assert zones["Pinned"]["y"] == 411
    assert zones["Origin"]["x"] == 0
    assert zones["Origin"]["y"] == 0


def test_considering_zone_created_without_fixed_coordinates() -> None:
    created = _harness("considering")["create"]
    assert created["type"] == "create_zone"
    assert created["name"] == "Considering"
    assert "x" not in created
    assert "y" not in created


def _resolved_font_px(rule_body: str) -> float:
    tokens = {
        name: float(size)
        for name, size in re.findall(
            r"(--t-\d+)\s*:\s*([0-9.]+)px", LAB_CSS.read_text()
        )
    }
    declared = re.search(r"font-size\s*:\s*([^;]+)", rule_body)
    assert declared, rule_body
    raw = declared.group(1).strip()
    variable = re.fullmatch(r"var\((--t-\d+)\)", raw)
    if variable:
        return tokens[variable.group(1)]
    return float(raw.removesuffix("px"))


def test_playmat_text_sizes_within_scale() -> None:
    css = _css()
    zoom = css.split("#zoom-label", 1)[1].split("}", 1)[0]
    chips = css.split(".dl-chip.dl-mat-count", 1)[1].split("}", 1)[0]
    fit = css.split(".dl-playmat-view .dl-zoom .dl-button", 1)[1].split("}", 1)[0]
    assert "tabular-nums" in zoom
    assert "var(--mono)" in zoom
    assert _resolved_font_px(zoom) >= 11
    assert _resolved_font_px(chips) >= 11
    assert _resolved_font_px(zoom) == 12
    assert _resolved_font_px(chips) == 12
    assert _resolved_font_px(fit) >= 11
