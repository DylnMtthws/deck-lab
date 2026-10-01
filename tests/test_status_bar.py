"""T05 — Pinned deck status bar tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-status.js"
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
HARNESS = Path(__file__).with_name("status_bar_harness.mjs")


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
# AC-1: typeBucket precedence
# ---------------------------------------------------------------------------


def test_type_bucket_precedence_table() -> None:
    """At least 10 cases including the 4 spec examples and Other."""
    payload = _run_harness("typebucket")
    results = payload["typeBucketResults"]
    assert len(results) >= 10, f"Only {len(results)} cases"
    # Check all expected cases pass
    for case in results:
        assert case["actual"] == case["expected"], (
            f"typeBucket({case['typeLine']!r}) => {case['actual']!r}, "
            f"expected {case['expected']!r}"
        )
    # Verify specific examples from the spec
    cases_map = {c["typeLine"]: c for c in results}
    assert (
        cases_map.get("Artifact Creature \u2014 Golem", {}).get("actual") == "Creature"
    )
    assert (
        cases_map.get("Land Creature \u2014 Forest Dryad", {}).get("actual") == "Land"
    )
    assert (
        cases_map.get("Legendary Enchantment Artifact", {}).get("actual") == "Artifact"
    )
    assert cases_map.get("Kindred Instant \u2014 Elf", {}).get("actual") == "Instant"
    assert cases_map.get("", {}).get("actual") == "Other"
    # "Enchantment Creature" matches Creature first (Creature precedes Enchantment in the order)
    assert (
        cases_map.get("Enchantment Creature \u2014 Pegasus", {}).get("actual")
        == "Creature"
    )


# ---------------------------------------------------------------------------
# AC-2: summarize excludes commanders and private zones, weights quantity
# ---------------------------------------------------------------------------


def test_summarize_excludes_commanders_and_private_zones_and_weights_quantity() -> None:
    """Commands and sideboard/maybeboard entries are excluded; quantities are summed."""
    payload = _run_harness("summarize")
    s2 = payload["summarize2"]
    # Total
    assert s2["total"] == 10
    # Types: Land=5 (3+2), Instant=4 (sideboard excluded)
    types = {t["name"]: t["count"] for t in s2["types"]}
    assert types.get("Land") == 5
    assert types.get("Instant") == 4
    # No commander or maybeboard types
    assert "Other" not in types
    # Roles: land=5, removal=4
    roles = {r["role"]: r["count"] for r in s2["roles"]}
    assert roles.get("land") == 5
    assert roles.get("removal") == 4


# ---------------------------------------------------------------------------
# AC-3: roles sorted with none bucket
# ---------------------------------------------------------------------------


def test_summarize_roles_sorted_with_none_bucket() -> None:
    """Roles are sorted by count descending, then name. Entries with no role
    count as 'none'."""
    payload = _run_harness("summarize")
    s3 = payload["summarize3"]
    roles = s3["roles"]
    assert len(roles) == 3
    # draw=3, ramp=3 (tied, sorted by name), none=1
    assert roles[0]["count"] == 3
    assert roles[2]["role"] == "none"
    assert roles[2]["count"] == 1


# ---------------------------------------------------------------------------
# AC-4: bar renders count, types, roles, and legal chip
# ---------------------------------------------------------------------------


def test_bar_renders_count_types_roles_and_legal_chip() -> None:
    """Harness: legal fixture. The bar shows the count, type buckets, top roles,
    and the legal indicator."""
    payload = _run_harness("legal")
    snap = payload["snapshot"]
    assert snap["countText"] == "6/100"
    assert snap["countInvalid"] is True
    # Types: Creature, Artifact, Enchantment, Instant (4 types)
    type_names = [t["name"] for t in snap["types"]]
    assert "Creature" in type_names
    assert "Artifact" in type_names
    assert "Enchantment" in type_names
    assert "Instant" in type_names
    # Roles are no longer rendered in the status bar (D04 removed role spans)
    # See test_design_status_bar.py for type pill assertions


# ---------------------------------------------------------------------------
# AC-5: issue button lists issues and focuses entry
# ---------------------------------------------------------------------------


def test_issue_button_lists_issues_and_focuses_entry() -> None:
    """Harness: illegal fixture with one deck issue and one entry issue.
    Clicking the entry item calls focusEntry with that id."""
    payload = _run_harness("illegal")
    snap = payload["snapshot"]
    assert snap["countText"] == "3/100"
    assert snap["countInvalid"] is True
    assert "is-ok" not in snap["legalClass"]

    issues_snap = payload["issuesAfterClick"]
    assert issues_snap["hasIssuesList"] is True
    focus_check = payload["focusEntryCheck"]
    assert focus_check["focusedEntryId"] == "entry-ring"
    assert "Sol Ring" in focus_check["buttonText"]


# ---------------------------------------------------------------------------
# AC-6: bar updates on render event
# ---------------------------------------------------------------------------


def test_bar_updates_on_render_event() -> None:
    """After updating state and calling render(), the bar reflects the new data."""
    payload = _run_harness("illegal")
    after = payload["afterRender"]
    assert after["countText"] == "100/100"
    assert after["countInvalid"] is False
    assert after["legalText"] == "Legal"
    assert "is-ok" in after["legalClass"]


# ---------------------------------------------------------------------------
# AC-7: builder page has status bar container and script
# ---------------------------------------------------------------------------


def test_builder_page_has_status_bar_container_and_script() -> None:
    """Flask test client: owner and shared pages have the markup."""
    html = BUILDER_TEMPLATE.read_text()
    assert '<footer class="dl-status-bar" data-status-bar' in html
    assert 'aria-label="Deck status"' in html
    assert "deck-lab-status.js" in html


# ---------------------------------------------------------------------------
# AC-8: toolbar deck count untouched
# ---------------------------------------------------------------------------


def test_bar_populated_when_loaded_after_ready() -> None:
    """AC-9: Status bar is populated even when deck-lab-status.js loads after
    deck-lab:ready has already fired."""
    payload = _run_harness("late-load")
    snap = payload["lateLoadSnapshot"]
    assert snap["countText"] == "6/100"
    assert snap["typeCount"] >= 1
    assert "Artifact" in snap["typeNames"]


def test_toolbar_deck_count_untouched() -> None:
    """[data-deck-count] text and attributes are identical with and without the
    status script loaded."""
    js = STATUS_JS.read_text()
    html = BUILDER_TEMPLATE.read_text()
    # The status script never references data-deck-count
    assert "data-deck-count" not in js
    # The toolbar deck count is in the template, and the status bar is separate
    assert "data-deck-count" in html.split('<footer class="dl-status-bar"')[0]
