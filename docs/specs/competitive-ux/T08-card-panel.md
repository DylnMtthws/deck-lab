# T08 — Card panel that follows hover and keyboard focus

Agent: HIGH (Cursor, Grok 4.7). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00 (`window.DeckLabBuilder`; contract in `T00-builder-extension-api.md`).

## Why
Moxfield's biggest efficiency win: a fixed card panel that updates as you move over the list, so
users read cards without opening anything. Deck Lab only has a click-to-open image modal.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-card-panel.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`:
  - Add a panel container `<aside class="dl-card-panel" data-card-panel aria-label="Card preview" aria-live="polite"></aside>`. Place it as the first child of `.dl-stats-rail`, after `.dl-builder-rail-head`.
  - Add the script tag after `deck-lab-builder.js`. Shared pages too: the panel is read-only.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_card_panel.py` (new), `tests/card_panel_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T08.md` (new)

## Requirements
1. The panel shows the entry most recently hovered or focused (`deck-lab:entry-hover`, `deck-lab:focus-entry`). Before any hover it shows the first commander, or a muted `Hover or focus a card to preview it` when the deck is empty.
2. Panel contents for an entry:
   - The card image (`entry.image_uri`; fall back to the same Scryfall named-image URL rule the builder uses for images), with `alt` = card name, loaded lazily. Reuse the builder's existing image-URL logic by reading it, not by duplicating different behaviour.
   - Name, mana cost (use `window.DeckLabMana.symbol` if present, otherwise text), type line, and oracle text with `{X}` symbols rendered the same way.
   - Quantity, zone name, and role label.
   - Validation issues for that entry, if any (from `state.validation.entry_issues`), in a `role="note"` list.
   - An extension slot `<div data-card-panel-slot="evidence"></div>`, left empty. T12 fills it.
3. Hover handling: update after 60 ms of hover stability; ignore moves across the same entry. The panel never steals focus. A click on the panel image opens the existing card image dialog (the builder's `openCardImage` behaviour; trigger it the same way the row image button does).
4. Image swaps do not shift layout: the image box has a fixed aspect ratio of 488:680, with a skeleton while loading. If the image fails, show the name in the box. Never show a broken image.
5. A pin toggle `<button data-card-panel-pin aria-pressed>` freezes the panel on the current card until it is toggled off.
6. When the right rail is collapsed, or the viewport is ≤ 767px, the panel does nothing: no network and no DOM work.
7. Prefetch: on hover, preload the image of the hovered entry only. No bulk prefetch.
8. Expose `window.DeckLabCardPanel = { show(entryId), current() }` for other modules and tests.

## Acceptance criteria → required tests (`tests/test_card_panel.py`)
- AC-1 `test_initial_panel_shows_commander_or_empty_hint`
- AC-2 `test_hover_updates_panel_after_debounce_and_ignores_same_entry` (fake timers)
- AC-3 `test_focus_entry_updates_panel_immediately`
- AC-4 `test_panel_renders_name_cost_type_oracle_qty_zone_role`
- AC-5 `test_panel_lists_entry_validation_issues`
- AC-6 `test_pin_freezes_panel`
- AC-7 `test_image_error_shows_name_not_broken_image`
- AC-8 `test_collapsed_rail_or_narrow_viewport_skips_work`
- AC-9 `test_evidence_slot_exists_and_is_empty`
- AC-10 `test_panel_container_on_owner_and_shared_pages` (Flask test client)
- AC-11 `test_panel_click_opens_existing_image_dialog`
