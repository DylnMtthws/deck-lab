# T04 — "Considering" workflow

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00: `window.DeckLabBuilder` exists. Read its contract in `docs/specs/competitive-ux/T00-builder-extension-api.md`.

## Why
Moxfield has "Considering" and Archidekt has a maybeboard: a place to park cards without counting
them. Deck Lab already treats zones named Considering, Maybeboard, Sideboard, Notes or Draft as
private. They are excluded from the 100-card count and from "Copy list" (`deck_documents.py`
`_PRIVATE_PUBLIC_ZONES`). But users have to discover this by naming a zone exactly right, and
nothing in the UI explains it.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-considering.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: only to add the script tag in `{% block scripts %}`, AFTER `deck-lab-builder.js`, inside `{% if not shared %}`.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_considering_workflow.py` (new), `tests/considering_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T04.md` (new)

Do NOT change the Python zone rules or `deck-lab-builder.js`. Use only the T00 API and the existing
command endpoint. Find the exact command shapes for creating a zone and moving entries by reading
`DeckDocumentRepo._apply_command` in `src/sabermetrics/deck_documents.py`. Use exactly those shapes.

## Requirements
1. A button `<button type="button" class="dl-button" data-move-considering>` is inserted into `[data-bulk-controls]`, after the existing Move button.
   - Its label is `Move to Considering`. When EVERY selected entry is already in the Considering zone, the label is `Move to deck`.
   - It is disabled when nothing is selected. Keep it in sync on every `deck-lab:selection` and `deck-lab:render`.
2. Clicking "Move to Considering":
   - If no zone named `Considering` (case-insensitive) exists, send ONE command batch that creates the zone and moves all selected non-commander entries into it.
   - If the zone exists, send one batch that moves them.
   - Use `DeckLabBuilder.command`, then clear the selection with `DeckLabBuilder.setSelection([])`.
   - Commander entries are never moved; they are ignored if selected.
3. Clicking "Move to deck" moves the selected entries to the zone named `Unsorted`, creating it if missing, in the same way.
4. A badge `<span class="dl-considering-count" data-considering-count>` is inserted right after `[data-deck-count]`.
   - Its text is `+N considering`, where N is the quantity-weighted number of cards in all private zones (same name set as `deck-lab-export.js` `PRIVATE_ZONE_NAMES`).
   - It is hidden (`hidden` attribute) when N is 0. Its `title` is `Not counted toward 100 or included in exports`.
   - It updates on every render.
5. Nothing is inserted in shared mode (`DeckLabBuilder.shared`).
6. Wait for `deck-lab:ready` if the API is not there yet when the script runs.

## Acceptance criteria → required tests (`tests/test_considering_workflow.py`, Node harness `tests/considering_harness.mjs`)
Load `deck-lab-builder.js`, then `deck-lab-considering.js`. Reuse the fake DOM approach from `tests/builder_api_harness.mjs` (T00).
- AC-1 `test_button_inserted_and_disabled_without_selection`
- AC-2 `test_creates_considering_zone_and_moves_in_one_batch`: exactly one fetch to `/commands`; its body contains a zone-creation command followed by move commands for the selected non-commander entries only.
- AC-3 `test_moves_into_existing_considering_zone_without_creating`
- AC-4 `test_label_switches_to_move_to_deck_and_moves_to_unsorted`
- AC-5 `test_badge_counts_private_zone_quantities_and_hides_at_zero`
- AC-6 `test_nothing_inserted_in_shared_mode`
- AC-7 `test_builder_page_includes_script_after_builder` (Flask test client: the owner's builder page includes `deck-lab-considering.js` after `deck-lab-builder.js`; a shared page does not include it)
- AC-8 `test_server_count_excludes_considering`: Python test through `DeckDocumentRepo`. A deck with 99 + commander + 3 cards in Considering has `total_count == 100`, and `export_text(..., "sections")` behaviour stays as it is today. This is a regression guard on existing behaviour; do not change server code.
- AC-9 `test_button_enables_after_checkbox_selection_via_ui`: select a row through its real checkbox (not `setSelection`); the button becomes enabled with the correct label, and becomes disabled again after the clear-selection button.
