"""DYL-69: compact card search with an adjacent destination category selector.

The owner chose the slim variant over the batch wizard: keep the existing combobox and
put a category selector next to it that defaults to Unsorted. These checks drive the real
builder IIFE, so they assert where a searched card actually lands, not that markup exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "sabermetrics" / "ui" / "static"
BUILDER_JS = STATIC / "deck-lab-builder.js"
WORKSPACE_CSS = STATIC / "feedback-workspace.css"
BUILDER_HTML = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
HARNESS = Path(__file__).with_name("add_destination_harness.js")


@pytest.fixture(scope="module")
def behavior(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder add-destination behavior")
    completed = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path_factory.mktemp("add-destination"),
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout.splitlines()[-1])


def test_destination_defaults_to_unsorted_not_the_first_zone(behavior):
    booted = behavior["booted"]
    # The harness deck lists "Ramp" first, so an index-based default would pick it.
    assert booted["options"] == ["Ramp", "Unsorted"]
    assert booted["value"] == "z-unsorted"
    assert "Unsorted" in booted["hint"]


def test_results_announce_the_destination_they_will_add_into(behavior):
    default = behavior["defaultResults"]
    assert default["label"] == "Add Sol Ring to Unsorted"
    assert "Cards you add go to Unsorted." in default["status"]


def test_changing_the_destination_retargets_results_already_on_screen(behavior):
    switched = behavior["switched"]
    assert switched["value"] == "z-ramp"
    assert switched["label"] == "Add Sol Ring to Ramp"
    assert "Ramp" in switched["hint"]


def test_adding_a_result_uses_the_selected_destination(behavior):
    added = behavior["added"]
    assert added["commands"] == [
        {"type": "add_card", "card_id": "sol-ring", "zone_id": "z-ramp", "quantity": 1}
    ]
    assert added["status"] == "Added Sol Ring to Ramp."
    # The compact flow clears the query and closes the list so the next search is one keystroke.
    assert added["query"] == ""
    assert added["resultsHidden"] is True


def test_deleting_the_destination_falls_back_to_unsorted_and_says_so(behavior):
    after = behavior["afterDelete"]
    assert after["value"] == "z-unsorted"
    assert "Unsorted" in after["hint"]
    assert after["status"] == (
        "That category is gone. Cards you add now go to Unsorted."
    )


def test_adds_after_a_deletion_go_to_unsorted_not_the_dead_zone(behavior):
    commands = behavior["readded"]["commands"]
    assert commands == [
        {
            "type": "add_card",
            "card_id": "sol-ring",
            "zone_id": "z-unsorted",
            "quantity": 1,
        }
    ]


def test_destination_selector_survives_narrow_viewports():
    """It used to be display:none under 768px, which broke the flow on a phone."""
    css = WORKSPACE_CSS.read_text()
    narrow = css.split("@media (max-width: 767px) {")[1]
    assert ".dl-add-destination-control {\n    display: none;" not in narrow
    assert ".dl-workspace-body .dl-add-destination-control {" in narrow

    markup = BUILDER_HTML.read_text()
    destination = next(
        line for line in markup.splitlines() if "dl-add-destination-control" in line
    )
    assert "desktop-only" not in destination
    # The select names itself visibly and to assistive tech.
    assert ">Add to<" in destination
    assert 'aria-label="Add found cards to category"' in destination
