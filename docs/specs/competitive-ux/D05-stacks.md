# D05 — Stacks display mode

Agent: HIGH. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.4 and §3. Mock-ups B1 and B2.

## Allowed files
- `static/deck-lab-stacks.js` (new). Render through a small, explicit hook: in `deck-lab-builder.js`'s `renderTable`, delegate `display_mode === "stacks"` to `window.DeckLabStacks.render(groups, container, api)`. This hook is the ONLY builder change.
- `static/deck-lab-stacks.css` (yours).
- `src/sabermetrics/deck_documents.py`: ONLY adding `"stacks"` to the `display_mode` option set.
- `templates/deck_lab/builder.html`: the stacks script tag (after the builder, literal tag), plus a `data-display="stacks"` option. If D01's View popover owns the display control, add the option to the existing display segmented control and D01 will reconcile; record this in your report.
- New tests.

## Acceptance → tests (`tests/test_design_stacks.py`, plus a harness)
- AC-1 `test_update_view_accepts_stacks`
- AC-2 `test_columns_per_group_with_swatch_name_count`
- AC-3 `test_cards_overlap_showing_title_and_last_card_full`
- AC-4 `test_qty_and_feedback_badges`
- AC-5 `test_hover_and_focus_lift_and_update_card_panel` (fires T00 entry-hover / focus-entry)
- AC-6 `test_drag_between_columns_moves_zone_or_sets_role_and_type_disallowed` (correct command batches; no command for type)
- AC-7 `test_cmd_click_toggles_selection_and_dblclick_opens_dialog`
- AC-8 `test_arrow_key_navigation_across_columns`
- AC-9 `test_reduced_motion_disables_lift_transform`
- Visual DoD: scenario `stacks` must be clean at 1440 and 1280. Columns wrap; there is no horizontal page overflow.

## Review server
Use port **5315** for your seeded review server (`--port 5315`). Kill it by PID when done; never kill processes you did not start.

Note: `static/deck-lab-shell.js` enhances every `<select>` on the page into a custom dropdown (`enhanceSelects`). Your `.dl-select`/dropdown styling must work with that enhancement, not fight it.
