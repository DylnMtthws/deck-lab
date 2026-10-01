"""T11 — Group the decklist by role.

Tests for the server-side command validation, the client-side JavaScript
grouping logic, and the HTML template.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
HARNESS = Path(__file__).with_name("group_by_role_harness.mjs")


def _run_harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the harness")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "harness failed")
    lines = result.stdout.strip().splitlines()
    return json.loads(lines[-1])


# --------------------------------------------------------------------------
# AC-1: Server accepts "role" and rejects unknown group_mode values.
# --------------------------------------------------------------------------


def test_update_view_accepts_role_and_rejects_unknown() -> None:
    """Validate the DeckDocumentRepo command validator."""
    # We assert the option_sets in deck_documents.py by reading the source.
    src = (
        ROOT / "src" / "sabermetrics" / "deck_documents.py"
    ).read_text()
    match = re.search(
        r"\"group_mode\":\s*\{(.+?)\}", src, re.MULTILINE | re.DOTALL
    )
    assert match, "group_mode option set not found"
    valid = set(
        re.findall(r'"([^"]+)"', match.group(1))
    )
    assert "role" in valid, '"role" must be in group_mode valid values'
    assert "zone" in valid
    assert "type" in valid
    # Unknown values are not in the set.
    assert "bogus" not in valid
    assert "invalid" not in valid


# --------------------------------------------------------------------------
# AC-2: Role groups order, labels, and no-role last.
# --------------------------------------------------------------------------


def test_role_groups_order_labels_and_no_role_last() -> None:
    payload = _run_harness("role_groups")
    groups = payload  # list of {id, name, count, entryIds}
    group_ids = [g["id"] for g in groups]

    # Commander is first
    assert group_ids[0] == "commander"

    # Role groups appear in roleOptions order (ramp before draw)
    ramp_idx = group_ids.index("role-ramp")
    draw_idx = group_ids.index("role-draw")
    assert ramp_idx < draw_idx, "ramp should appear before draw"

    # No-role group exists after all role groups
    no_role_idx = group_ids.index("role-none")
    role_ids = [gid for gid in group_ids if gid.startswith("role-") and gid not in ("role-none", "role-private")]
    last_role_idx = max(group_ids.index(rid) for rid in role_ids)
    assert no_role_idx > last_role_idx, "No role group should be after all role groups"

    # Labels are human-readable from roleOptions
    ramp_group = groups[ramp_idx]
    assert ramp_group["name"] == "Ramp"
    draw_group = groups[draw_idx]
    assert draw_group["name"] == "Draw"


# --------------------------------------------------------------------------
# AC-3: Private zone entries grouped last.
# --------------------------------------------------------------------------


def test_private_zone_entries_grouped_last() -> None:
    payload = _run_harness("role_groups")
    groups = payload
    group_ids = [g["id"] for g in groups]

    # Private group name
    private_idx = group_ids.index("role-private")
    assert groups[private_idx]["name"] == "Considering & other private zones"
    assert groups[private_idx]["count"] == 1

    # Private group is last
    assert private_idx == len(group_ids) - 1


# --------------------------------------------------------------------------
# AC-4: Sort applies within role groups.
# --------------------------------------------------------------------------


def test_sort_applies_within_role_groups() -> None:
    payload = _run_harness("sort_mana_value")
    entry_ids = payload["rampEntryIds"]
    # Both have mana_value=1, so tie goes to alphabetical: "Birds of Paradise" < "Sol Ring"
    assert len(entry_ids) == 2
    assert entry_ids[0] == "entry-arbor", "Birds of Paradise should come first alphabetically"
    assert entry_ids[1] == "entry-sol"


# --------------------------------------------------------------------------
# AC-5: Empty role groups omitted.
# --------------------------------------------------------------------------


def test_empty_role_groups_omitted() -> None:
    """Only roles with entries produce a group."""
    payload = _run_harness("role_groups")
    present = {g["id"] for g in payload if g["id"].startswith("role-")}
    # Only ramp and draw have entries; all others are omitted.
    assert "role-ramp" in present
    assert "role-draw" in present
    assert "role-removal" not in present
    assert "role-protection" not in present
    assert "role-tutor" not in present
    # The fixture has no "land" role entries
    assert "role-land" not in present


# --------------------------------------------------------------------------
# AC-6: Group select has Role option and reflects saved preference.
# --------------------------------------------------------------------------


def test_group_select_has_role_option_and_reflects_saved_preference() -> None:
    # Template check: the HTML includes a role option.
    html = BUILDER_TEMPLATE.read_text()
    assert (
        '<option value="role">Group: Role</option>' in html
    ), "Role option must be in the group select"

    # Harness check: the select value reflects the saved group_mode.
    payload = _run_harness("select_value")
    assert payload["selectValue"] == "role"


# --------------------------------------------------------------------------
# AC-7: Zone and type grouping unchanged.
# --------------------------------------------------------------------------


def test_zone_and_type_grouping_unchanged() -> None:
    zone_payload = _run_harness("zone_group_snapshot")
    type_payload = _run_harness("type_group_snapshot")

    zone_names = [g["name"] for g in zone_payload]
    type_names = [g["name"] for g in type_payload]

    # Zone grouping: commander + zones by their name
    assert zone_names == ["Commander", "Unsorted", "Considering"]

    # Type grouping: commander + types, sorted
    assert type_names == [
        "Commander", "Artifact", "Basic Land", "Creature", "Enchantment"
    ]


# --------------------------------------------------------------------------
# AC-8: Unknown roles grouped under "Other".
# --------------------------------------------------------------------------


def test_unknown_roles_grouped_under_other() -> None:
    """Entries with unknown roles (card_draw, finisher) appear in the Other group."""
    payload = _run_harness("unknown_roles")
    groups = payload
    group_ids = [g["id"] for g in groups]

    other_idx = group_ids.index("role-other")
    assert groups[other_idx]["name"] == "Other"

    other_entry_ids = groups[other_idx]["entryIds"]
    assert "entry-carddraw" in other_entry_ids
    assert "entry-finisher" in other_entry_ids

    # The no-role entry should be in the No role group, not Other
    no_role_idx = group_ids.index("role-none")
    assert "entry-saproling" in groups[no_role_idx]["entryIds"]
    assert "entry-saproling" not in other_entry_ids


# --------------------------------------------------------------------------
# AC-9: Every entry appears exactly once in role grouping.
# --------------------------------------------------------------------------


def test_role_grouping_shows_every_entry_exactly_once() -> None:
    """On a mixed fixture, every rendered entry appears exactly once."""
    payload = _run_harness("all_entries_visible")
    all_ids = payload["allEntryIds"]
    expected = [
        "entry-cmd", "entry-ramp", "entry-draw",
        "entry-carddraw", "entry-finisher", "entry-norole",
        "entry-private",
    ]
    assert sorted(all_ids) == sorted(expected)
    # Verify no duplicates
    assert len(all_ids) == len(set(all_ids))


# --------------------------------------------------------------------------
# AC-10: Single Other group when "other" and unknown roles coexist.
# --------------------------------------------------------------------------


def test_single_other_group_when_other_and_unknown_roles_coexist() -> None:
    """A deck with one 'other' entry and one card_draw entry produces one role-other group."""
    payload = _run_harness("other_and_unknown")
    groups = payload
    group_ids = [g["id"] for g in groups]

    # Count role-other groups: must be exactly 1
    other_groups = [g for g in groups if g["id"] == "role-other"]
    assert len(other_groups) == 1, f"Expected exactly one role-other group, got {len(other_groups)}"

    other_group = other_groups[0]
    assert other_group["name"] == "Other"

    # Both entries are in it
    assert "entry-other-real" in other_group["entryIds"]
    assert "entry-carddraw" in other_group["entryIds"]

    # The group is at the position of "other" in roleOptions (after utility, before role-none and private)
    other_idx = group_ids.index("role-other")
    # There should be no other role-* group after this one besides role-none and role-private
    remaining = group_ids[other_idx + 1:]
    for rid in remaining:
        assert rid in ("role-none", "role-private"), f"Unexpected group {rid} after role-other"
