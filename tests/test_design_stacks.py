"""D05 — Stacks display mode."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo, InvalidCommand
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
STACKS_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-stacks.js"
STACKS_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-stacks.css"
HARNESS = Path(__file__).with_name("design_stacks_harness.mjs")


@pytest.fixture
def deck_db(tmp_path):
    path = tmp_path / "stacks.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    return path, owner


def _run(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the harness")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS), str(STACKS_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _column(payload: dict, group_id: str) -> dict:
    match = next(col for col in payload["columns"] if col["id"] == group_id)
    return match


def test_update_view_accepts_stacks(deck_db, monkeypatch) -> None:
    path, owner = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="Stacks")
    updated = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="stacks-view",
        commands=[{"type": "update_view", "display_mode": "stacks"}],
    )
    assert updated["preferences"]["display_mode"] == "stacks"
    with pytest.raises(InvalidCommand):
        repo.apply_commands(
            owner,
            deck_id,
            expected_revision=updated["revision"],
            mutation_id="bad-display",
            commands=[{"type": "update_view", "display_mode": "gallery"}],
        )
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    html = client.get(f"/build/deck/{deck_id}").get_data(as_text=True)
    assert re.search(r'<script src="[^"]*/deck-lab-stacks\.js" defer></script>', html)
    assert html.index("deck-lab-builder.js") < html.index("deck-lab-stacks.js")
    positions = [
        html.index('data-display="text"'),
        html.index('data-display="stacks"'),
        html.index('data-display="grid"'),
        html.index('data-display="spoiler"'),
    ]
    assert positions == sorted(positions)


def test_columns_per_group_with_swatch_name_count() -> None:
    payload = _run("columns")
    assert [col["id"] for col in payload["columns"]] == [
        "commander",
        "zone-main",
        "zone-ramp",
        "zone-cons",
    ]
    assert payload["rows"] == 0
    commander = _column(payload, "commander")
    assert commander["name"] == "Commander"
    assert commander["count"] == "1"
    assert "background:#e94560" in commander["swatch"]
    unsorted = _column(payload, "zone-main")
    assert unsorted["name"] == "Unsorted"
    assert unsorted["count"] == "3"
    assert unsorted["swatch"].startswith("background:#")
    assert _column(payload, "zone-ramp")["name"] == "Ramp"
    assert _column(payload, "zone-ramp")["count"] == "1"


def test_cards_overlap_showing_title_and_last_card_full() -> None:
    css = STACKS_CSS.read_text()
    assert "aspect-ratio: 488 / 680" in css
    assert "width: 172px" in css
    assert "calc(28px - (172px * 680 / 488))" in css
    assert re.search(r"\.dl-stack-card:last-child\s*\{[^}]*margin-bottom:\s*0", css)
    unsorted = _column(_run("columns"), "zone-main")
    assert [card["face"] for card in unsorted["cards"]] == ["peek", "full"]
    assert [card["id"] for card in unsorted["cards"]] == ["entry-ring", "entry-birds"]
    assert _column(_run("columns"), "zone-ramp")["cards"][0]["face"] == "full"


def test_qty_and_feedback_badges() -> None:
    payload = _run("badges")
    cards = {card["id"]: card for col in payload["columns"] for card in col["cards"]}
    assert cards["entry-ring"]["qty"] == ""
    assert cards["entry-ring"]["feedback"] == ""
    assert cards["entry-birds"]["qty"] == "2×"
    assert cards["entry-birds"]["feedback"] == "up"
    assert cards["entry-study"]["qty"] == ""
    assert cards["entry-study"]["feedback"] == "comment"
    assert "2 copies" in cards["entry-birds"]["label"]
    assert "thumbs up" in cards["entry-birds"]["label"]


def test_hover_and_focus_lift_and_update_card_panel() -> None:
    css = STACKS_CSS.read_text()
    assert "translateX(10px)" in css
    assert "var(--shadow-card-lift)" in css
    assert "var(--dur)" in css
    payload = _run("hover")
    assert payload["lifted"] is True
    assert payload["afterLeave"] is False
    assert payload["focusedLift"] is True
    assert payload["events"] == [
        {"type": "deck-lab:entry-hover", "entryId": "entry-ring"},
        {"type": "deck-lab:focus-entry", "entryId": "entry-ring"},
    ]


def test_drag_between_columns_moves_zone_or_sets_role_and_type_disallowed() -> None:
    zone = _run("drag-zone")
    assert zone["during"]["dragSource"] is True
    assert zone["during"]["preview"] is True
    assert zone["during"]["dropTarget"] is True
    assert zone["during"]["overPrevented"] is True
    assert zone["during"]["dropEffect"] == "move"
    assert zone["commands"] == [
        [
            {
                "type": "move_entry",
                "entry_id": "entry-ring",
                "zone_id": "zone-ramp",
                "sort_order": 999,
            }
        ]
    ]
    role = _run("drag-role")
    assert role["commands"] == [
        [{"type": "set_role", "entry_id": "entry-ring", "role": "draw"}]
    ]
    typed = _run("drag-type")
    assert typed["during"]["disallowed"] is True
    assert typed["during"]["dropTarget"] is False
    assert typed["during"]["overPrevented"] is False
    assert typed["during"]["dropEffect"] == "none"
    assert typed["commands"] == []


def test_cmd_click_toggles_selection_and_dblclick_opens_dialog() -> None:
    payload = _run("pointer")
    assert payload["selected"] == ["entry-ring"]
    assert payload["pressed"] == "true"
    assert payload["cleared"] == []
    assert payload["dialogOpen"] is True
    assert payload["title"] == "Rhystic Study"
    assert payload["src"] == "https://cards.test/study.png"


def test_arrow_key_navigation_across_columns() -> None:
    payload = _run("keys")
    assert payload["down"] == "entry-birds"
    assert payload["right"] == "entry-study"
    assert payload["left"] == "entry-ring"
    assert payload["up"] == "entry-ring"
    assert payload["enter"] == [
        {"type": "deck-lab:focus-entry", "entryId": "entry-study"}
    ]


def test_reduced_motion_disables_lift_transform() -> None:
    css = STACKS_CSS.read_text()
    assert "translateX(10px)" in css.split("prefers-reduced-motion")[0]
    reduced = css.split("prefers-reduced-motion", 1)[1]
    assert "transform: none" in reduced
    assert "transition: none" in reduced
    assert "animation: none" in reduced
    assert ".dl-stack-card:hover" in reduced
    assert ".dl-stack-card.is-lifted" in reduced


def test_stacks_populates_when_loaded_after_ready() -> None:
    payload = _run("late")
    assert payload["readyBeforeStacks"] == 1
    assert payload["before"]["columns"] == []
    assert payload["after"]["rows"] == 0
    assert [col["id"] for col in payload["after"]["columns"]] == [
        "commander",
        "zone-main",
        "zone-ramp",
        "zone-cons",
    ]
