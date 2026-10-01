# T11 — Group the decklist by role

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00 being merged; this task touches `deck-lab-builder.js`.

## Why
Archidekt's default grouping is by function, not card type. cEDH players think in roles: ramp, tutors,
interaction, win conditions. Deck Lab already stores a role per entry and remembers per-deck view
preferences (`deck_view_preferences.group_mode`). But only `zone` and `type` grouping exist.

## Allowed files
- `src/sabermetrics/deck_documents.py`: ONLY the `option_sets["group_mode"]` set in the `update_view` command, adding `"role"`.
- `src/sabermetrics/ui/static/deck-lab-builder.js`: ONLY the `groups()` function, and the code that handles the `[data-group]` select change, if that is needed to send `group_mode: "role"`.
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: ONLY the `[data-group]` select, to add `<option value="role">Group: Role</option>`.
- `tests/test_group_by_role.py` (new), `tests/group_by_role_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T11.md` (new)

## Requirements
1. `group_mode` accepts `"role"` on the server. Invalid values are still rejected.
2. In `groups()`, when `preference("group_mode") === "role"`:
   - The Commander group stays first, unchanged.
   - Non-commander entries in LIBRARY zones are grouped by `entry.role`, ordered as in the existing `roleOptions` list (skip the empty "Add role" option). Group names are the human labels from `roleOptions`, and group ids are `"role-<key>"`.
   - Entries with no role go into a final group `No role` (id `role-none`), placed after all role groups.
   - Entries in private zones (Considering, Maybeboard and so on, using `isLibraryZone`) are grouped into ONE group named `Considering & other private zones` (id `role-private`), placed last.
   - Empty groups are omitted.
   - The existing sort modes apply inside each group.
   - Groups are `permanent: true`, like type groups, so they are not draggable or renamable as zones.
3. The select shows the persisted value on load. Persistence already works through `update_view`; verify it, don't re-implement it.

## Acceptance criteria → required tests (`tests/test_group_by_role.py`)
- AC-1 `test_update_view_accepts_role_and_rejects_unknown` (`DeckDocumentRepo` command)
- AC-2 `test_role_groups_order_labels_and_no_role_last` (harness)
- AC-3 `test_private_zone_entries_grouped_last` (harness)
- AC-4 `test_sort_applies_within_role_groups` (harness: `sort_mode` `mana_value`)
- AC-5 `test_empty_role_groups_omitted` (harness)
- AC-6 `test_group_select_has_role_option_and_reflects_saved_preference` (Flask test client: set `group_mode` `role` through the command API, reload the page, and the embedded document preferences have `group_mode == "role"`. Harness: the select's value is `role` after the first render.)
- AC-7 `test_zone_and_type_grouping_unchanged`: harness snapshot of group names for both existing modes, before and after your change, on the same fixture.
