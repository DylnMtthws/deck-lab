# D01 — Header, toolbar, View popover, selection bar, export and deck-options menus

Agent: HIGH. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.1, §2.2, §2.9 (export menu only) and §3. Look at mock-up screenshots A1, A2 (selection bar) and A3 (View popover).

## Allowed files
- `templates/deck_lab/builder.html`: header and toolbar markup only.
- `static/deck-lab-builder.js`: only the toolbar, selection-bar, export-menu and header wiring functions (`renderSelectionBar`, `syncSelection` UI parts, `syncControls`, `bindExportMenu`, `syncExport`, add-destination functions), plus a new View-popover function.
- `static/deck-lab-considering.js`: its button moves into the selection bar.
- `static/deck-lab-history.js`: icon undo/redo buttons only.
- `static/deck-lab-chrome.css` (yours).
- New tests, plus the existing-test updates allowed by DESIGN-SPEC §3.3.

## Must preserve
- Every behaviour T00–T11 tests cover: export formats and blocking, Considering logic, undo/redo, the `/` focus, Shift+Enter, selection events, `update_view`/`update_presentation` persistence, and the playmat picker/surface functionality (now inside the View popover in Playmat view).

## Acceptance → tests (`tests/test_design_toolbar.py`, plus a Node harness)
- AC-1 `test_view_popover_controls_persist_via_update_view`: changing display, group, sort or density sends `update_view`, and the button label reads "View · <Group> · <Sort>".
- AC-2 `test_view_popover_in_playmat_shows_surface_outlines_dim_fit`
- AC-3 `test_popover_closes_on_escape_and_outside_click_and_restores_focus`
- AC-4 `test_selection_bar_replaces_toolbar_only_when_selected`: 0 selected → no selection bar; ≥1 → bar shown and normal controls hidden; Clear restores.
- AC-5 `test_selection_bar_actions_move_considering_set_role_remove`: each sends the correct command batch.
- AC-6 `test_destination_pill_menu_sets_add_destination`
- AC-7 `test_deck_options_menu_has_tags_new_zone_commanders_share_delete`
- AC-8 `test_undo_redo_icon_buttons_with_tooltips_and_disabled_state`
- AC-9 `test_export_menu_items_have_icons_labels_descriptions`: and copy/download behaviour is unchanged.
- AC-10 `test_no_count_or_bulk_controls_visible_in_toolbar`
- Visual DoD: scenarios `list`, `list-selection`, `view-options`, `export-menu` and `playmat` must be clean at 1440 and 1280 (no `bar:`, `covered:` or `native:`).

## Review server
Use port **5311** for your seeded review server (`--port 5311`). Kill it by PID when done; never kill processes you did not start.

Note: `static/deck-lab-shell.js` enhances every `<select>` on the page into a custom dropdown (`enhanceSelects`). Your `.dl-select`/dropdown styling must work with that enhancement, not fight it.
