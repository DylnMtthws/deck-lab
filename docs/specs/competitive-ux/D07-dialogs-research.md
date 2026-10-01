# D07 — Shortcuts and comment dialogs; Research query chips

Agent: LOW. Depends on D00. Read COMMON.md, `DESIGN-SPEC.md` §1, §2.9 (shortcuts and comment dialogs only), §2.10 and §3.

## Allowed files
- `templates/deck_lab/builder.html`: ONLY the `[data-hotkeys-help]` dialog markup.
- `static/deck-lab-hotkeys.js`: the help-dialog rendering only.
- `static/deck-lab-feedback.js`: the comment dialog markup and text only.
- `static/deck-lab-dialogs.css` (yours).
- `templates/deck_lab/research_fragment.html`: the cards-tab notices only.
- `src/sabermetrics/ui/research_routes.py`: keep `query` (the user's full `q`) for the search field, and pass a SEPARATE `name_query` to the search.
- `static/deck-lab-research.js`: ONLY to remove a chip term from `q` and reload.
- New tests.

## Acceptance → tests (`tests/test_design_dialogs_research.py`)
- AC-1 `test_shortcuts_dialog_two_column_table_with_grouped_keycaps` (every T09 key still listed)
- AC-2 `test_comment_dialog_title_names_card_and_styled_controls`
- AC-3 `test_research_search_field_keeps_full_syntax_query`
- AC-4 `test_applied_terms_render_as_removable_chips_and_removal_updates_q`
- AC-5 `test_unsupported_terms_render_as_warn_chips_with_title`
- AC-6 `test_plain_query_unchanged` (T03's behaviour kept)
- Visual DoD: scenarios `hotkeys` and `research-syntax` must be clean at 1440 and 1280 (no `native:`; no text below 11 px).

## Review server
Use port **5317** for your seeded review server (`--port 5317`). Kill it by PID when done; never kill processes you did not start.

Note: `static/deck-lab-shell.js` enhances every `<select>` on the page into a custom dropdown (`enhanceSelects`). Your `.dl-select`/dropdown styling must work with that enhancement, not fight it.
