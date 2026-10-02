# D04 — Status bar, issues popover, playmat zoom offset

Agent: LOW. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.7 and §3. Mock-ups A1 and A2 (issues popover above the bar).

## Allowed files
- `static/deck-lab-status.js`
- `static/deck-lab-statusbar.css` (yours)
- `static/deck-lab-playmat.css`: ONLY the zoom-control offset rule; append one clearly commented block at the top of the file.
- `templates/deck_lab/builder.html`: the status bar container only.
- New tests, plus the updates allowed by DESIGN-SPEC §3.3.

## Requirements
Implement §2.7 exactly:
- the count with a progress track, in the colour for each state;
- the legality chip or the issues button;
- the issues `.dl-popover` above the bar: closes on Escape and outside click; entry issues call `focusEntry`;
- type pills;
- the "+N considering" chip (move the T04 badge here, removing it from the toolbar);
- the verdict chip, which activates the Tools tab (`[data-rail-tab=tools]` click) when present.

Keep `window.DeckLabStatus.typeBucket` and `summarize` unchanged.

## Acceptance → tests (`tests/test_design_status_bar.py`, plus a harness)
- AC-1 `test_progress_track_color_states_below_at_above_100`
- AC-2 `test_issues_popover_opens_above_bar_lists_and_focuses_entries`
- AC-3 `test_popover_closes_on_escape_and_outside_click`
- AC-4 `test_considering_and_verdict_chips_render_and_verdict_opens_tools_tab`
- AC-5 `test_status_text_sizes_within_scale` (computed sizes are 11–14 px only)
- Visual DoD: scenarios `status-issues` and `playmat` must be clean at 1440 and 1280, with `underStatus` 0.

## Review server
Use port **5314** for your seeded review server (`--port 5314`). Kill it by PID when done; never kill processes you did not start.

Note: `static/deck-lab-shell.js` enhances every `<select>` on the page into a custom dropdown (`enhanceSelects`). Your `.dl-select`/dropdown styling must work with that enhancement, not fight it.
