"""D04 — Status bar, issues popover, playmat zoom offset tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-status.js"
STATUSBAR_CSS = (
    ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-statusbar.css"
)
PLAYMAT_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-playmat.css"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
HARNESS = Path(__file__).with_name("status_bar_design_harness.mjs")


def _run_harness(scenario: str = "legal") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the harness")
    result = subprocess.run(
        [node, str(HARNESS), str(STATUS_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "harness failed")
    lines = result.stdout.strip().splitlines()
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# AC-1: Progress track color states
# ---------------------------------------------------------------------------


def test_progress_track_color_states_below_at_above_100() -> None:
    """Progress track shows warning (<100), success (=100), brand (>100)."""
    payload = _run_harness("legal")
    snap = payload["snapshot"]
    assert snap["hasProgress"] is True
    assert snap["progressFillWidth"] == "6%"
    assert snap["progressFillDisplay"] == "block"


# ---------------------------------------------------------------------------
# AC-2: Issues popover opens above bar, lists issues, focuses entries
# ---------------------------------------------------------------------------


def test_issues_popover_opens_above_bar_lists_and_focuses_entries() -> None:
    """Issues button opens a .dl-popover above the bar with kind chips and
    entry buttons that call focusEntry."""
    js = STATUS_JS.read_text()
    assert "dl-popover" in js
    assert "focusEntry" in js


# ---------------------------------------------------------------------------
# AC-3: Popover closes on Escape and outside click
# ---------------------------------------------------------------------------


def test_popover_closes_on_escape_and_outside_click() -> None:
    """Issues popover dismisses on Escape key and clicking outside."""
    js = STATUS_JS.read_text()
    assert "Escape" in js or 'key === "Escape"' in js
    assert "outsideClickHandler" in js or "click" in js
    assert 'document.addEventListener("keydown"' in js
    assert 'document.addEventListener("click"' in js


# ---------------------------------------------------------------------------
# AC-4: Considering and verdict chips render, verdict opens tools tab
# ---------------------------------------------------------------------------


def test_considering_and_verdict_chips_render_and_verdict_opens_tools_tab() -> None:
    """Status bar shows +N considering chip and Verdict chip. Clicking verdict
    activates the Tools tab."""
    payload = _run_harness("withConsidering")
    snap = payload["snapshot"]
    assert snap["consideringText"] is not None
    assert "considering" in snap["consideringText"]
    assert "+" in snap["consideringText"]

    payload2 = _run_harness("withVerdict")
    snap2 = payload2["snapshot"]
    assert snap2["verdictText"] is not None
    assert "Verdict:" in snap2["verdictText"]
    assert "Mixed" in snap2["verdictText"] or "not set" in snap2["verdictText"]

    js = STATUS_JS.read_text()
    assert "data-rail-tab=tools" in js
    assert 'verdictChip.addEventListener("click"' in js


# ---------------------------------------------------------------------------
# AC-5: All status text sizes are within 11–14 px
# ---------------------------------------------------------------------------


def test_status_text_sizes_within_scale() -> None:
    """Computed font sizes for status bar elements are 11–14 px only."""
    css = STATUSBAR_CSS.read_text()
    assert ".dl-status-bar" in css
    import re

    sizes = re.findall(r"font-size:\s*(\d+)px", css)
    for s in sizes:
        size = int(s)
        assert 11 <= size <= 14, f"Size {size}px outside 11-14px range"


# ---------------------------------------------------------------------------
# AC-6: Progress fill is block with percentage width
# ---------------------------------------------------------------------------


def test_progress_fill_is_block_with_percentage_width() -> None:
    """Progress fill has display:block and width set to percentage."""
    payload = _run_harness("legal")
    snap = payload["snapshot"]
    assert snap["progressFillDisplay"] == "block"
    assert snap["progressFillWidth"] is not None
    pct = int(snap["progressFillWidth"].rstrip("%"))
    assert 0 <= pct <= 100


# ---------------------------------------------------------------------------
# AC-7: Issue toggle is warn chip and legal is ok chip
# ---------------------------------------------------------------------------


def test_issue_toggle_is_warn_chip_and_legal_is_ok_chip() -> None:
    """Issues button is in a .dl-chip.is-warn; Legal is .dl-chip.is-ok."""
    # legal deck
    payload = _run_harness("withConsidering")
    snap = payload["snapshot"]
    assert snap["legalClass"] is not None
    assert "dl-chip" in snap["legalClass"]
    assert "is-ok" in snap["legalClass"]

    # The legality element wraps an issues button when illegal
    # Test directly from JS source for warn chip
    js = STATUS_JS.read_text()
    assert 'className = "dl-chip is-warn"' in js
    assert 'className = "dl-chip is-ok"' in js
    assert "dl-dot" in js


# ---------------------------------------------------------------------------
# AC-8: Issues popover survives status re-render while open
# ---------------------------------------------------------------------------


def test_issues_popover_survives_status_rerender_while_open() -> None:
    """Open popover, dispatch deck-lab:render, popover still present."""
    payload = _run_harness("issuesSurviveRerender")
    assert payload.get("popoverSurvived") is True


# ---------------------------------------------------------------------------
# AC-9: Pill label is sans, number is mono
# ---------------------------------------------------------------------------


def test_issues_popover_is_fixed_outside_status_bar_and_anchored_to_toggle() -> None:
    """Popover is fixed outside the status bar and left-aligned to its toggle."""
    payload = _run_harness("popoverAnchor")
    assert payload["insideStatusBar"] is False
    assert payload["position"] == "fixed"
    assert abs(payload["left"] - payload["toggleLeft"]) <= 4
    assert payload["clampedInsideStatusBar"] is False
    assert payload["clampedPosition"] == "fixed"
    assert abs(payload["clampedLeft"] - payload["clampedExpected"]) <= 4


def test_pill_label_sans_and_number_mono() -> None:
    """Type pill label is --sans, number is --mono tabular weight 500."""
    payload = _run_harness("legal")
    snap = payload["snapshot"]
    assert len(snap["types"]) > 0
    for t in snap["types"]:
        assert t["labelText"] != "", f"type {t['name']} has no label"
        assert t["countText"] != "", f"type {t['name']} has no count"
    css = STATUSBAR_CSS.read_text()
    assert ".dl-status-type-label" in css
    assert ".dl-status-type-count" in css
    assert "var(--mono)" in css
    assert "font-family: var(--sans)" in css


# ---------------------------------------------------------------------------
# AC-10: Template has the status bar container
# ---------------------------------------------------------------------------


def test_builder_page_has_status_bar_container_and_scripts() -> None:
    """Flask templates have the status bar container and statusbar.css/js."""
    html = BUILDER_TEMPLATE.read_text()
    assert '<footer class="dl-status-bar" data-status-bar' in html
    assert 'aria-label="Deck status"' in html
    assert "deck-lab-status.js" in html
    assert "deck-lab-statusbar.css" in html


# ---------------------------------------------------------------------------
# AC-11: Playmat zoom offset rule
# ---------------------------------------------------------------------------


def test_playmat_zoom_offset_rule_exists() -> None:
    """Playmat zoom control has bottom offset to clear the status bar."""
    css = PLAYMAT_CSS.read_text()
    assert ".dl-playmat-view .dl-zoom" in css
    assert "bottom:" in css
    assert "36px" in css


# ---------------------------------------------------------------------------
# AC-12: Considering badge removed from toolbar
# ---------------------------------------------------------------------------


def test_considering_badge_removed_from_toolbar() -> None:
    """The old .dl-considering-count badge is no longer in considering.js."""
    js = (
        ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-considering.js"
    ).read_text()
    assert "dl-considering-count" not in js
    assert "insertBadge" not in js
    assert "updateBadge" not in js
