"""Right rail Card / Deck / Tools tabs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name("design_rail_harness.mjs")
NATIVE = Path(__file__).with_name("design_rail_native.mjs")
LAYOUT = Path(__file__).with_name("design_rail_layout.mjs")
BUILDER = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)


def _payload(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the rail harness")
    result = subprocess.run(
        [node, str(HARNESS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_tabs_switch_panes_with_click_and_arrow_keys_and_persist() -> None:
    markup = BUILDER.read_text()
    assert "data-rail-tabs" in markup
    assert 'data-rail-tab="card"' in markup
    assert 'data-rail-tab="deck"' in markup
    assert 'data-rail-tab="tools"' in markup
    assert 'data-rail-pane="card"' in markup
    assert 'data-rail-pane="deck"' in markup
    assert 'data-rail-pane="tools"' in markup
    data = _payload("tabs")
    assert data["afterClick"] == "true"
    assert data["deckHidden"] is False
    assert data["cardHidden"] is True
    assert data["saved"] == "deck"
    assert data["afterArrow"] == "true"
    assert data["focused"] == "tools"
    assert data["toolsHidden"] is False
    assert data["restored"] == "true"
    assert data["restoredDeckHidden"] is True


def test_rail_sections_routed_to_correct_tabs() -> None:
    data = _payload("routing")
    assert data == {
        "pips": "deck",
        "odds": "tools",
        "sample": "tools",
        "feedback": "tools",
        "evidence": "deck",
        "simulation": "deck",
        "bar": True,
        "src": True,
    }


def test_card_tab_order_title_cost_type_rules_meta_feedback_issues_evidence() -> None:
    data = _payload("card-order")
    assert data["rising"] is True
    assert data["order"][0] >= 0
    assert "dl-select" in data["roleControl"]
    assert data["feedbackLabels"] == [
        "Good pick",
        "Bad pick",
        "Comment on Sol Ring",
    ]


def test_card_tab_role_select_sends_set_role() -> None:
    data = _payload("role")
    assert data["commands"] == [
        [{"type": "set_role", "entry_id": "entry-ring", "role": "draw"}]
    ]


def test_curve_shows_counts_avg_and_filter_click_still_works() -> None:
    data = _payload("curve")
    assert re.fullmatch(r"Mana curve · avg \d+\.\d", data["heading"])
    assert data["counts"] == ["0", "2", "0", "0", "0", "0"]
    assert data["labels"] == ["0", "1", "2", "3", "4", "5+"]
    assert data["filters"] == ["Mana value 1", None]
    assert data["activeAfterFirst"] == 1
    assert data["activeAfterSecond"] == 0


def test_odds_card_sentence_controls_and_big_result() -> None:
    data = _payload("odds")
    assert re.fullmatch(r"\d+\.\d%", data["resultText"])
    assert "dl-odds-result" in data["resultClass"]
    assert data["sentence"].startswith("to see at least")
    assert data["sentence"].endswith("cards")
    assert data["needInside"] is True
    assert data["seenInside"] is True
    assert data["categoryInside"] is True
    assert "in" in data["detail"] and "cards" in data["detail"]


def test_sample_hand_renders_thumbnails_and_focuses_entry() -> None:
    data = _payload("hand")
    assert data["count"] == 3
    assert "dl-hand-card" in data["className"]
    assert data["image"] is True
    assert data["name"]
    assert data["focused"] == [data["entryId"]]
    assert "land" in data["summary"]


def test_verdict_segmented_and_note_debounce_still_saves() -> None:
    data = _payload("verdict")
    assert data["segmented"] is True
    assert data["label"] == "Your verdict"
    assert data["buttons"] == ["Good", "Mixed", "Bad"]
    assert len(data["saves"]) == 1
    body = json.loads(data["saves"][0]["body"])
    assert body["comment"] == "Needs more interaction"
    assert "/feedback/deck" in data["saves"][0]["url"]


_LAYOUT: dict | None = None


def _layout() -> dict:
    global _LAYOUT
    if _LAYOUT is not None:
        return _LAYOUT
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to measure rail layout")
    result = subprocess.run(
        [node, str(LAYOUT)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "layout probe failed")
    _LAYOUT = json.loads(result.stdout.strip().splitlines()[-1])
    return _LAYOUT


def test_verdict_section_stacks_vertically_full_width() -> None:
    data = _layout()
    assert data["textareaRatio"] >= 0.9
    assert data["stacked"] is True
    assert data["rows"] == 3


def test_evidence_window_select_is_compact() -> None:
    data = _layout()
    assert data["windowFont"] == "13px"
    assert data["windowRatio"] < 0.9
    assert data["windowLabel"] == "Window"


def test_no_native_controls_in_rail() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the native-control probe")
    result = subprocess.run(
        [node, str(NATIVE)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "native probe failed")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["native"] == []


def test_rail_modules_populate_when_loaded_after_ready() -> None:
    data = _payload("late")
    assert data["name"] == "Kinnan Test"
    assert data["bins"] == 6
    assert data["count"] == "2"
