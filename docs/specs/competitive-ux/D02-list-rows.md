# D02 — List rows, group headers, Grid tokens, Spoiler symbol fix

Agent: HIGH. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.3, §2.5 and §3. Mock-ups A1 and A2.

## Allowed files
- `static/deck-lab-builder.js`: `renderText`, `renderGrid`, `renderSpoiler`, the group-header rendering in `renderTable`, `manaToken`/`appendManaText` sizing hooks.
- `static/deck-lab-feedback.js`: row controls only; move them into the actions cell as icon buttons.
- `static/deck-lab-list.css` (yours).
- New tests, plus the updates allowed by DESIGN-SPEC §3.3.

## Must preserve
- T00 `data-entry-id` on every entry element, plus the hover and focus events.
- Selection checkboxes (their accessible names), quantity steppers, the image dialog, and T07 feedback behaviour (votes, comment dialog, revert on error).
- The role dropdown is removed from rows. Role changes must remain possible through the card panel (D03), the selection bar (D01) and hotkeys. Until D03 lands, tests may use the command API.

## Acceptance → tests (`tests/test_design_list.py`, plus a Node harness)
- AC-1 `test_row_columns_and_fixed_height_compact_and_comfy`
- AC-2 `test_hover_reveals_checkbox_steppers_feedback_and_hides_at_rest`
- AC-3 `test_set_feedback_stays_visible_and_comment_dot`
- AC-4 `test_zone_chip_only_for_private_zones_when_grouped_by_role`
- AC-5 `test_role_chip_when_grouped_by_zone`
- AC-6 `test_group_header_count_share_bar_and_collapse_persists`
- AC-7 `test_spoiler_inline_symbols_sized_to_text`: computed height ≤ 1.2 × font-size.
- AC-8 `test_row_click_focuses_panel_and_dblclick_opens_dialog`
- AC-9 `test_feedback_controls_are_icon_buttons_with_names_no_emoji`
- Visual DoD: scenarios `list`, `list-selection` and `spoiler` must be clean at 1440 and 1280, with `bigSym` 0 and no `native:`.
