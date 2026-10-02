"""Card panel follows playmat hover, resets on view switch, and shows partner pairs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-card-panel.js"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
RAIL_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-rail.css"
HARNESS = Path(__file__).with_name("card_panel_i03_harness.mjs")


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


def test_hovering_playmat_card_updates_panel() -> None:
    payload = _payload("playmat_hover")
    assert payload["foundLotus"] is True
    assert "dl-mat-card" in payload["lotusClass"]
    assert payload["inPlaymat"] is True
    assert payload["during"] == "Kinnan Test"
    assert payload["after"] == "Lotus Petal"
    assert payload["focusDuring"] == "Lotus Petal"
    assert payload["focused"] == "Sol Ring"
    commander = _payload("commander_mat")
    assert commander["found"] is True
    assert commander["zone"] == "command"
    assert commander["name"] == "Tymna the Weaver"
    assert commander["pair"] is True


def test_view_switch_resets_panel_to_commander_unless_pinned() -> None:
    payload = _payload("view_switch")
    assert payload["hovered"] == "Lotus Petal"
    assert payload["afterDisplay"] == "Lotus Petal"
    assert payload["reset"] == "Kinnan Test"
    assert payload["hint"] is False
    assert payload["pinnedPressed"] == "true"
    assert payload["pinnedName"] == "Sol Ring"
    assert payload["frame"] is True


def test_rehover_same_table_row_after_view_switch_dispatches() -> None:
    payload = _payload("rehover")
    assert payload["beforeSwitch"] == ["entry-lotus"]
    assert payload["beforeAdvance"] == "Kinnan Test"
    assert payload["afterSwitch"] == "Kinnan Test"
    assert payload["afterDispatch"] == ["entry-lotus", "entry-lotus"]
    assert payload["during"] == "Kinnan Test"
    assert payload["after"] == "Lotus Petal"


def test_partner_pair_strip_renders_both_commanders_and_switches_active() -> None:
    css = RAIL_CSS.read_text()
    pair = css.split(".dl-workspace-body .dl-card-panel-pair {", 1)[1]
    assert "minmax(0, 1fr)" in pair
    assert "var(--t-12)" in pair
    assert "488 / 680" in pair
    assert "var(--brand)" in pair
    assert "var(--focus)" in pair
    payload = _payload("partners")
    initial = payload["initial"]
    assert initial["frame"] is False
    assert initial["name"] == "Thrasios, Triton Hero"
    assert initial["type"] == "Legendary Creature — Merfolk Wizard"
    assert "Partner" in initial["oracle"]
    assert initial["buttons"] == [
        {
            "label": "Show Thrasios, Triton Hero",
            "pressed": "true",
            "name": "Thrasios, Triton Hero",
            "id": "entry-thrasios",
        },
        {
            "label": "Show Tymna the Weaver",
            "pressed": "false",
            "name": "Tymna the Weaver",
            "id": "entry-tymna",
        },
    ]
    preview = payload["preview"]
    assert preview["frame"] is False
    assert preview["name"] == "Tymna the Weaver"
    assert [button["pressed"] for button in preview["buttons"]] == ["true", "false"]
    clicked = payload["clicked"]
    assert clicked["name"] == "Tymna the Weaver"
    assert clicked["current"] == "entry-tymna"
    assert [button["pressed"] for button in clicked["buttons"]] == ["false", "true"]
    assert payload["previewBack"] == "Thrasios, Triton Hero"
    assert payload["pressedWhilePreview"] == ["false", "true"]
    pinned = payload["pinned"]
    assert pinned["pressed"] == "true"
    assert pinned["name"] == "Tymna the Weaver"
    assert pinned["current"] == "entry-tymna"
    assert pinned["afterHover"] == "Tymna the Weaver"


def test_single_commander_and_noncommander_render_unchanged() -> None:
    payload = _payload("unchanged")
    single = payload["singleCommander"]
    assert single["name"] == "Kinnan Test"
    assert single["frame"] is True
    assert single["pair"] is False
    assert single["buttons"] == 0
    expected = {
        "name": "Sol Ring",
        "frame": True,
        "pair": False,
        "cost": "{1}",
        "type": "Artifact",
        "oracle": "Add {C}{C}.",
        "qty": "2",
        "zone": "Ramp",
        "role": "Ramp",
    }
    assert payload["nonCommander"] == expected
    assert payload["partnerNonCommander"] == expected


def test_late_load_partner_default_state() -> None:
    payload = _payload("late_partner")
    assert payload["current"] == "entry-thrasios"
    assert payload["name"] == "Thrasios, Triton Hero"
    assert payload["frame"] is False
    assert [button["name"] for button in payload["buttons"]] == [
        "Thrasios, Triton Hero",
        "Tymna the Weaver",
    ]
    assert [button["pressed"] for button in payload["buttons"]] == ["true", "false"]
