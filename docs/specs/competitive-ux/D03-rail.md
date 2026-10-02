# D03 — Right rail: Card / Deck / Tools tabs

Agent: HIGH. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.6 and §3. Mock-ups A1 (Card), A3 (Deck) and A2 (Tools).

## Allowed files
- `templates/deck_lab/builder.html`: rail markup only (`.dl-stats-rail`).
- These modules, each limited to rendering and markup changes, keeping their logic and state:
  - `static/deck-lab-card-panel.js`
  - `static/deck-lab-stats.js`
  - `static/deck-lab-feedback.js` (verdict section only)
  - `static/deck-lab-evidence.js`
  - `static/deck-lab-simulate.js`
  - `static/deck-lab-meta-compare.js` (its rail button only)
- `static/deck-lab-builder.js`: only `renderStats` (the curve, color and zone markup).
- `static/deck-lab-rail.css` (yours).
- New tests, plus the updates allowed by DESIGN-SPEC §3.3.

## Requirements
- Tabs (`[data-rail-tabs]`, `data-rail-tab=card|deck|tools`, `data-rail-pane=...`), keyboard operable with arrow keys between tabs (ARIA tabs pattern). The last tab is remembered per deck in `localStorage`.
- Update every `railSection` caller to pass the `tab` mapping in §2.6.
- Card tab content order, exactly as §2.6. Role change via a `.dl-select` in the meta row, using the `set_role` command.
- Deck tab: curve with count labels and average, color requirements, zones, tags, evidence, simulation.
- Tools tab:
  - the draw-odds card with a 28 px result and the inline sentence controls;
  - the sample hand as image thumbnails;
  - the verdict segmented control and its note;
  - Compare to meta.

## Acceptance → tests (`tests/test_design_rail.py`, plus a Node harness)
- AC-1 `test_tabs_switch_panes_with_click_and_arrow_keys_and_persist`
- AC-2 `test_rail_sections_routed_to_correct_tabs`
- AC-3 `test_card_tab_order_title_cost_type_rules_meta_feedback_issues_evidence`
- AC-4 `test_card_tab_role_select_sends_set_role`
- AC-5 `test_curve_shows_counts_avg_and_filter_click_still_works`
- AC-6 `test_odds_card_sentence_controls_and_big_result`
- AC-7 `test_sample_hand_renders_thumbnails_and_focuses_entry`
- AC-8 `test_verdict_segmented_and_note_debounce_still_saves`
- AC-9 `test_no_native_controls_in_rail` (harness: computed style differs from an unstyled probe)
- Visual DoD: scenarios `list` (Card tab), `rail-deck` and `rail-tools` must be clean at 1440 and 1280.

## Review server
Use port **5313** for your seeded review server (`--port 5313`). Kill it by PID when done; never kill processes you did not start.

Note: `static/deck-lab-shell.js` enhances every `<select>` on the page into a custom dropdown (`enhanceSelects`). Your `.dl-select`/dropdown styling must work with that enhancement, not fight it.
