# D06 — Playmat restyle and real-card feel

Agent: HIGH. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.8 and §3. Baseline playmat screenshots: `/Users/dylan/Projects/mtg/deck-lab/worktrees/.cux/visual/baseline/playmat-1440.png` and `playmat-hover-stack-1440.png`.

## Allowed files
- `static/deck-lab-builder.js`: ONLY `renderPlaymat`, `makeCard`, `zoneLayoutButton`, `zoneGridButton`, `zoneMenu`, `beginCardDrag`/drag preview helpers, `clearDropState`.
- `static/deck-lab-playmat.css` (yours, apart from D04's zoom-offset block, which you must keep).
- New tests.

## Must preserve (non-negotiable)
Every §2.8 "must keep working" interaction. All existing playmat tests stay green, unmodified. If one must change, it falls under DESIGN-SPEC §3.3 and must be justified.

## Acceptance → tests (`tests/test_design_playmat.py`, plus a harness)
- AC-1 `test_zone_header_sans_title_count_chip_icon_buttons_with_tooltips`
- AC-2 `test_card_hover_lift_and_reduced_motion_none`
- AC-3 `test_drag_source_dims_preview_tilted_drop_zone_glows`
- AC-4 `test_stack_depth_offsets_up_to_6_and_plus_n_badge`
- AC-5 `test_stack_peek_on_hover_300ms_and_focus_fans_top_5_without_command`: no fetch is sent while peeking.
- AC-6 `test_drag_card_out_of_stack_moves_entry_to_target_zone`
- AC-7 `test_spread_stack_toggle_sends_set_zone_layout_and_animates_flip` (FLIP classes applied; none under reduced motion)
- AC-8 `test_existing_playmat_interactions_unchanged`: names the existing test modules you ran: `test_deck_lab_drag_preview`, `test_feedback_playmat_review`, `test_feedback_workspace*`, `test_account_playmats`, plus any others you find.
- Visual DoD: scenarios `playmat` and `playmat-hover-stack` must be clean at 1440 and 1280. The orchestrator judges the "real cards" feel from the screenshots.

## Review server
Use port **5316** for your seeded review server (`--port 5316`). Kill it by PID when done; never kill processes you did not start.

Note: `static/deck-lab-shell.js` enhances every `<select>` on the page into a custom dropdown (`enhanceSelects`). Your `.dl-select`/dropdown styling must work with that enhancement, not fight it.
