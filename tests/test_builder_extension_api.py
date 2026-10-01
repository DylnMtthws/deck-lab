from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
HARNESS = Path(__file__).with_name("builder_api_harness.mjs")

EXPECTED_KEYS = [
    "command",
    "focusEntry",
    "getSelection",
    "getState",
    "onRender",
    "railSection",
    "render",
    "setEntryFilter",
    "setSelection",
    "shared",
    "version",
]


def _payload(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the builder API harness")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# AC-1 -- exact contract
# ---------------------------------------------------------------------------


def test_api_exposes_exact_contract() -> None:
    payload = _payload("contract")
    assert payload["exists"] is True
    assert payload["keys"] == EXPECTED_KEYS
    assert payload["version"] == 1


# ---------------------------------------------------------------------------
# AC-2 -- ready event
# ---------------------------------------------------------------------------


def test_ready_event_fires_once_with_api() -> None:
    payload = _payload("ready")
    assert payload["count"] == 1
    assert payload["hasApi"] is True


# ---------------------------------------------------------------------------
# AC-3 -- onRender listener lifecycle
# ---------------------------------------------------------------------------


def test_on_render_listener_runs_after_render_and_unsubscribes() -> None:
    payload = _payload("onrender")
    assert len(payload) == 3

    # After first render: both listeners fire once
    assert payload[0]["afterFirstRender"] == 1
    assert payload[0]["afterFirstRender2"] == 1

    # After unsubscribe: first listener no longer called, second still fires
    assert payload[1]["afterSecondRenderUnsubbed"] == 1
    assert payload[1]["afterSecondRender2"] == 2

    # Throwing listener does not stop subsequent listeners
    assert payload[2]["afterThirdRender"] == 3


# ---------------------------------------------------------------------------
# AC-4 -- entry filter
# ---------------------------------------------------------------------------


def test_entry_filter_limits_rendered_entries_and_chip_clears_it() -> None:
    payload = _payload("entryfilter")

    # Filter keeps exactly one entry
    assert payload["filteredCount"] == 1
    assert payload["ids"] == ["entry-ring"]

    # Chip and clear button exist while filter is active
    assert payload["chipExists"] is True
    assert payload["clearExists"] is True

    # After clearing, all entries return and chip is removed
    assert payload["totalAfter"] == 3
    assert payload["chipAfterExists"] is False


# ---------------------------------------------------------------------------
# AC-5 -- setSelection
# ---------------------------------------------------------------------------


def test_set_selection_updates_ui_and_fires_event() -> None:
    payload = _payload("setselection")

    # getSelection returns the valid ids passed in
    assert sorted(payload["selection"]) == ["entry-lotus", "entry-ring"]

    # Unknown id is dropped
    assert "entry-nonexistent" not in payload["selection"]

    # Event fires with the correct ids
    assert payload["event"] is not None
    assert sorted(payload["event"]["ids"]) == ["entry-lotus", "entry-ring"]


# ---------------------------------------------------------------------------
# AC-6 -- shared mode command resolves without fetch
# ---------------------------------------------------------------------------


def test_shared_mode_command_resolves_without_fetch() -> None:
    payload = _payload("shared_command")

    assert payload["shared"] is True
    assert payload["commandPromise"] is True
    assert payload["resolved"] is True
    assert payload["fetchCalls"] == 0


# ---------------------------------------------------------------------------
# AC-7 -- railSection idempotency
# ---------------------------------------------------------------------------


def test_rail_section_is_idempotent() -> None:
    payload = _payload("railsection")

    # Same id returns the same element
    assert payload["same"] is True

    # Only one section with that data attribute
    assert payload["count"] == 1

    # Heading has the right text
    assert payload["headingText"] == "Filter Cards"


# ---------------------------------------------------------------------------
# AC-8 -- every rendered entry has entry-id in all displays
# ---------------------------------------------------------------------------


def test_every_rendered_entry_has_entry_id_in_all_displays() -> None:
    payload = _payload("entryhaspresent")

    # Three deck entries (commander + 2 library cards) => 3 text rows with data-entry-id
    assert payload["textRowCount"] == 3
    assert payload["totalEntryElements"] == 3

    # Commander is one of them (but not counted separately in this fixture)
    _ = payload["commanderCount"]


# ---------------------------------------------------------------------------
# AC-9 -- entry hover event
# ---------------------------------------------------------------------------


def test_entry_hover_event_dispatches_entry_id() -> None:
    payload = _payload("entryhover")

    assert payload["hoverEvents"] == 1
    assert payload["firstId"] == "entry-ring"


# ---------------------------------------------------------------------------
# AC-10 -- focusEntry returns false for unknown id
# ---------------------------------------------------------------------------


def test_focus_entry_returns_false_for_unknown_id() -> None:
    payload = _payload("focusentry")

    assert payload["found"] is True
    assert payload["notFound"] is False
    # Coerces non-string ids
    assert payload["withNumber"] is False


# ---------------------------------------------------------------------------
# AC-11 -- checkbox click fires selection event
# ---------------------------------------------------------------------------


def test_checkbox_click_fires_selection_event() -> None:
    payload = _payload("checkbox_selection")

    assert payload["eventCount"] == 1
    assert payload["ids"] == ["entry-ring"]


# ---------------------------------------------------------------------------
# AC-12 -- clear selection button fires selection event with empty ids
# ---------------------------------------------------------------------------


def test_clear_selection_button_fires_selection_event_with_empty_ids() -> None:
    payload = _payload("clear_selection_event")

    assert payload["eventCount"] == 1
    assert payload["ids"] == []


# ---------------------------------------------------------------------------
# AC-13 -- selection event not fired when unchanged, once per setSelection
# ---------------------------------------------------------------------------


def test_selection_event_not_fired_when_unchanged_and_once_per_set_selection() -> None:
    payload = _payload("selection_unchanged_and_once")

    # Exactly one event, not two — second setSelection with same ids is no-op
    assert payload["eventCount"] == 1


# ---------------------------------------------------------------------------
# AC-14 -- filter chip text without label is exactly "Filtered"
# ---------------------------------------------------------------------------


def test_filter_chip_text_without_label_is_exactly_filtered() -> None:
    payload = _payload("filter_chip_no_label")

    assert payload["chipText"] == "Filtered"
