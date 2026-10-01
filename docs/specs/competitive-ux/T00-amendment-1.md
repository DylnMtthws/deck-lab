# T00 — Amendment 1 (from the orchestrator, after review)

Review result for commit 9d89014: **NOT DONE.** Everything else is accepted; keep it. Fix exactly these:

1. `deck-lab:selection` must fire whenever the selection changes through ANY path, not only `setSelection`. That includes:
   - the row checkbox in the List display
   - the clear-selection button `[data-clear-selection]`
   - the selection being emptied after a bulk move
   - entries being pruned from the selection because they no longer exist (`syncSelection`)

   Implement it in ONE place, by comparing the selection before and after in the shared selection-update path (e.g. at the end of `syncSelection()`/`renderSelectionBar()`, against a remembered previous set). Do not scatter dispatch calls. It must not fire when the set did not actually change, and `setSelection` must not fire it twice.
2. When `setEntryFilter(fn)` is called with no label, the chip text must be exactly `Filtered`. With a label, it is `Filtered: <label>`.
3. `focusEntry` and `railSection` must not throw for non-string ids. Coerce with `String(...)`.

## Additional acceptance criteria → tests (add to `tests/test_builder_extension_api.py`)
- AC-11 `test_checkbox_click_fires_selection_event`: click a row's selection checkbox in the harness; exactly one `deck-lab:selection` event fires, with that id.
- AC-12 `test_clear_selection_button_fires_selection_event_with_empty_ids`
- AC-13 `test_selection_event_not_fired_when_unchanged_and_once_per_set_selection`
- AC-14 `test_filter_chip_text_without_label_is_exactly_filtered`

## Completion
COMMON.md definition of done. The base has ZERO failing tests; run the FULL suite. Fold everything into the single existing commit (`git commit --amend`), and include this file. Update `reports/T00.md` with AC-11..14.
