"""D02 list rows, group headers, grid tokens, and spoiler symbol size."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIST_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-list.css"
DECK_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
HARNESS = Path(__file__).with_name("design_list_harness.mjs")

_EMOJI = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f1e6-\U0001f1ff" "]"
)


def _run(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the list harness")
    result = subprocess.run(
        [node, str(HARNESS), scenario, str(ROOT)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "list harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _rule_bodies(css: str, selector: str) -> list[str]:
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]+)\}")
    return [match.group(1) for match in pattern.finditer(css)]


def _decl(body: str, prop: str) -> str | None:
    match = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", body)
    return match.group(1).strip() if match else None


def test_row_columns_and_fixed_height_compact_and_comfy() -> None:
    payload = _run("columns")
    assert payload["headers"] == ["Qty", "Card", "Cost", "Type"]
    assert "dl-density-compact" in payload["compactClass"]
    assert "dl-density-comfortable" in payload["comfortableClass"]
    assert payload["entryId"] == "entry-sol"
    assert payload["children"][:6] == [
        "dl-row-select",
        "dl-qty",
        "dl-card-cell",
        "dl-mana-cost",
        "dl-card-type",
        "dl-row-actions",
    ]
    assert payload["selectLabel"] == "Select Sol Ring"
    assert "Remove one Sol Ring" in payload["steps"]
    assert "Add one Sol Ring" in payload["steps"]
    assert payload["roleSelects"] == 0
    css = LIST_CSS.read_text()
    grid = _rule_bodies(css, ".dl-decklist-columns,\n.dl-deck-row")
    assert grid, "list rows need an explicit column grid"
    assert "28px 52px minmax(200px, 1fr) 96px minmax(160px, 260px) 96px" in grid[0]
    compact = _rule_bodies(css, ".dl-deck-row")
    assert any(_decl(body, "height") == "var(--row-compact)" for body in compact)
    comfy = _rule_bodies(css, ".dl-density-comfortable .dl-deck-row")
    assert comfy and _decl(comfy[0], "height") == "var(--row-comfy)"
    header = _rule_bodies(css, ".dl-decklist-columns")
    assert any(_decl(body, "height") == "30px" for body in header)
    assert any("var(--t-11)" in body for body in header)


def test_hover_reveals_checkbox_steppers_feedback_and_hides_at_rest() -> None:
    payload = _run("feedback")
    assert payload["checkbox"] is True
    assert payload["steppers"] == 2
    assert payload["inActions"] is True
    css = LIST_CSS.read_text()
    hidden = _rule_bodies(css, ".dl-row-select")
    assert any(_decl(body, "visibility") == "hidden" for body in hidden)
    steps = _rule_bodies(css, ".dl-qty button,\n.dl-step")
    assert any(_decl(body, "visibility") == "hidden" for body in steps)
    assert any(_decl(body, "width") == "18px" for body in steps)
    feedback = _rule_bodies(
        css, ".dl-deck-row .dl-card-feedback .dl-icon-button:not(.is-on)"
    )
    assert feedback and _decl(feedback[0], "visibility") == "hidden"
    shown = "\n".join(
        _rule_bodies(
            css,
            ".dl-deck-row:hover .dl-card-feedback .dl-icon-button,\n"
            ".dl-deck-row:focus-within .dl-card-feedback .dl-icon-button,\n"
            ".dl-deck-row.selected .dl-card-feedback .dl-icon-button,\n"
            ".dl-deck-row .dl-card-feedback .dl-icon-button.is-on",
        )
    )
    assert "visibility: visible" in shown
    hover_box = "\n".join(
        _rule_bodies(
            css,
            ".dl-deck-row:hover .dl-row-select,\n"
            ".dl-deck-row:focus-within .dl-row-select,\n"
            ".dl-deck-row.selected .dl-row-select",
        )
    )
    assert "visibility: visible" in hover_box
    hover_steps = "\n".join(
        _rule_bodies(
            css,
            ".dl-deck-row:hover .dl-qty button,\n"
            ".dl-deck-row:focus-within .dl-qty button,\n"
            ".dl-deck-row.selected .dl-qty button",
        )
    )
    assert "visibility: visible" in hover_steps


def test_set_feedback_stays_visible_and_comment_dot() -> None:
    payload = _run("feedback")
    by_label = {button["label"]: button for button in payload["buttons"]}
    assert "is-on" in by_label["Good pick"]["className"]
    assert "is-on" not in by_label["Bad pick"]["className"]
    assert by_label["Comment on Sol Ring"]["hasComment"] is True
    assert "is-on" in by_label["Comment on Sol Ring"]["className"]
    assert payload["landOn"] is False
    css = LIST_CSS.read_text()
    dot = _rule_bodies(css, ".dl-deck-row .dl-fb-comment[data-has-comment]::after")
    assert dot
    assert _decl(dot[0], "width") == "6px"
    assert _decl(dot[0], "height") == "6px"
    assert "var(--brand)" in (_decl(dot[0], "background") or "")


def test_zone_chip_only_for_private_zones_when_grouped_by_role() -> None:
    payload = _run("zone-chip")
    assert payload["solChip"] is None
    assert payload["hulkChip"] == "Considering"
    assert payload["solRoleSelect"] == 0
    assert payload["hulkRoleSelect"] == 0


def test_role_chip_when_grouped_by_zone() -> None:
    payload = _run("role-chip")
    assert payload["solRole"] == "Ramp"
    assert payload["hulkRole"] == "Combo"
    assert payload["landRole"] is None
    assert payload["roleSelects"] == 0
    assert payload["solZoneChip"] is None


def test_group_header_count_share_bar_and_collapse_persists() -> None:
    payload = _run("header")
    by_id = {group["id"]: group for group in payload["before"]}
    assert by_id["zone-commander"]["name"] == "Commander"
    assert by_id["zone-commander"]["chip"] == "Commander"
    assert by_id["zone-commander"]["share"] == "20%"
    assert by_id["zone-role-ramp"]["count"] == "1"
    assert by_id["zone-role-ramp"]["share"] == "20%"
    assert by_id["zone-role-draw"]["count"] == "2"
    assert by_id["zone-role-draw"]["share"] == "40%"
    assert "· not counted" in by_id["zone-role-private"]["count"]
    assert by_id["zone-role-private"]["share"] is None
    assert payload["collapsed"]["collapsed"] is True
    assert payload["collapsed"]["hidden"] is True
    assert payload["restored"]["collapsed"] is False
    assert payload["restored"]["hidden"] is False
    css = LIST_CSS.read_text()
    share = _rule_bodies(css, ".dl-share")
    assert share
    assert _decl(share[0], "width") == "72px"
    assert _decl(share[0], "height") == "4px"
    heading = _rule_bodies(css, ".dl-zone-heading")
    assert any(_decl(body, "height") == "32px" for body in heading)


def _px(value: str, font_size: float) -> float:
    value = value.strip()
    if value.endswith("em"):
        return float(value[:-2]) * font_size
    if value.endswith("px"):
        return float(value[:-2])
    raise AssertionError(f"unsupported length {value}")


def _matching_symbol_length(css: str, prop: str) -> str:
    """Winning width/height for an inline spoiler mana image."""
    best_score = -1
    best = "68px"
    element_classes = {"dl-ms", "dl-mana-text"}
    ancestors = [
        {"dl-mana-inline"},
        {"dl-spoiler-card"},
    ]
    for match in re.finditer(r"([^{}]+)\{([^}]+)\}", css):
        selector = match.group(1).strip()
        body = match.group(2)
        declared = _decl(body, prop)
        if not declared:
            continue
        for part in selector.split(","):
            part = part.strip()
            if (
                "dl-spoiler" not in part
                and "dl-ms" not in part
                and "dl-mana" not in part
            ):
                continue
            simple = part.split(":")[0].strip()
            tokens = simple.split()
            if not tokens:
                continue
            last = tokens[-1]
            if (
                not last.startswith("img")
                and "dl-ms" not in last
                and "dl-mana" not in last
            ):
                continue
            classes = set(re.findall(r"\.([\w-]+)", last))
            if classes and not classes <= element_classes:
                continue
            needed = []
            for token in tokens[:-1]:
                bucket = set(re.findall(r"\.([\w-]+)", token))
                if bucket:
                    needed.append(bucket)
            ancestor_ok = True
            pool = [set(bucket) for bucket in ancestors]
            for bucket in needed:
                if not any(bucket <= have for have in pool):
                    ancestor_ok = False
                    break
            if not ancestor_ok:
                continue
            score = 10 * len(classes) + len(tokens)
            if score >= best_score:
                best_score = score
                best = declared
    return best


def test_spoiler_inline_symbols_sized_to_text() -> None:
    payload = _run("spoiler")
    assert payload["symbols"] >= 1
    assert payload["tag"] == "IMG"
    assert "dl-ms" in payload["className"]
    assert payload["inRules"] is True
    css = LIST_CSS.read_text() + "\n" + DECK_CSS.read_text()
    font_rule = _rule_bodies(LIST_CSS.read_text(), ".dl-spoiler-card p")
    assert font_rule
    font_token = _decl(font_rule[-1], "font-size")
    assert font_token == "var(--t-13)"
    assert _decl(font_rule[-1], "line-height") == "1.55"
    font_size = 13.0
    height = _px(_matching_symbol_length(css, "height"), font_size)
    assert height <= 1.2 * font_size
    assert height < 24


def test_row_click_focuses_panel_and_dblclick_opens_dialog() -> None:
    payload = _run("click")
    assert payload["before"] == "Kinnan"
    assert payload["afterClick"] == "Sol Ring"
    assert payload["focused"] == "entry-sol"
    assert payload["openBefore"] is False
    assert payload["dialogOpen"] is True
    assert payload["title"] == "Sol Ring"


def test_feedback_controls_are_icon_buttons_with_names_no_emoji() -> None:
    payload = _run("feedback")
    assert payload["loadedAfterReady"] is True
    assert payload["inActions"] is True
    assert len(payload["buttons"]) == 3
    labels = [button["label"] for button in payload["buttons"]]
    assert labels == ["Good pick", "Bad pick", "Comment on Sol Ring"]
    for button in payload["buttons"]:
        assert button["svg"] is True
        assert button["tip"]
        assert "dl-icon-button" in button["className"]
        assert _EMOJI.search(button["text"]) is None
        assert button["text"].strip() != ""
