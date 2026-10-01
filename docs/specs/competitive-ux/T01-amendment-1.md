# T01 — Amendment 1 (from the orchestrator, after review)

Review result for commit 107e81e: **NOT DONE.**
- The server formats, the route, and the JS formatter are accepted. Keep them.
- The new menu items are DEAD. Nothing binds `[data-export-copy-archidekt]` to a click, so it never copies.
- `[data-export-download]` does not follow the pending-save blocking that "Copy list" uses.
- AC-7 only checked the markup, so it passed with a non-working button.
- Your report said DONE without flagging this. COMMON.md required `Status: BLOCKED` when the work needed a non-allowed file. Next time, report it.

## Additional allowed file (narrow)
- `src/sabermetrics/ui/static/deck-lab-builder.js`: ONLY inside `bindExportMenu()`, `syncExport()` and `exportBlocked()`, and any code they directly call for copying (`copyExportList()` and `showExportFallback()`). Nothing else in that file.
- This amendment file, which is included in your commit.

## Additional requirements
1. Clicking `[data-export-copy-archidekt]` copies `DeckLabExport.exportArchidektText(state)` plus a trailing `"\n"`, using the SAME clipboard path and fallback dialog as "Copy list".
   - On success, `[data-export-status]` reads exactly `Copied for Archidekt`.
   - On clipboard failure, the existing fallback dialog opens with the Archidekt text.
2. While a save is pending (the same condition that blocks "Copy list"):
   - `[data-export-copy-archidekt]` is `disabled`.
   - `[data-export-download]` gets `aria-disabled="true"`, and its click is prevented.

   When the save finishes, both are re-enabled.
3. Remove `exportArchidektLines` if nothing uses it. Keep one Archidekt function.

## Additional acceptance criteria → tests (add to `tests/test_competitive_export.py`)
Drive the real `deck-lab-builder.js` together with `deck-lab-export.js` in a Node harness. Extend the existing builder harness approach (see `tests/builder_selection_harness.mjs`).
- AC-9 `test_archidekt_button_copies_text_and_sets_status`: a fake `navigator.clipboard.writeText` receives exactly the server `archidekt` output, and the status text equals `Copied for Archidekt`.
- AC-10 `test_archidekt_button_falls_back_when_clipboard_fails`: `writeText` rejects; the fallback textarea contains the Archidekt text and the dialog is opened.
- AC-11 `test_export_items_blocked_while_save_pending`: with a save in flight, the Archidekt button is disabled and the download link has `aria-disabled="true"` with its click default-prevented. After the save resolves, neither is blocked.

## Completion
Same definition of done as COMMON.md, with these specifics:
- Fold everything into the SINGLE existing commit (`git commit --amend`; keep the message).
- Update `reports/T01.md`: list AC-9, AC-10 and AC-11 with their tests, and list the `deck-lab-builder.js` functions you touched under Deviations.
