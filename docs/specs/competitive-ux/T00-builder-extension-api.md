# T00 — Builder extension API

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.

## Why
Many later tasks add features to the deck builder. `src/sabermetrics/ui/static/deck-lab-builder.js`
is one closed IIFE, so every task would edit the same 77 KB file and conflict. This task exposes a
small, stable API so later features can live in their own JS files.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-builder.js` (modify)
- `tests/builder_api_harness.mjs` (new)
- `tests/test_builder_extension_api.py` (new)
- `docs/specs/competitive-ux/reports/T00.md` (new, your report)

## Requirements
Inside the existing IIFE, after the first `render()` call near the end of the file, define
`window.DeckLabBuilder` with EXACTLY these members (no others):

| Member | Behaviour |
|---|---|
| `version` | The number `1`. |
| `shared` | Boolean, same value as the IIFE's existing `shared` variable. |
| `getState()` | Returns the builder's current `state` object (the live variable, which the builder reassigns after each save; always return the current one). |
| `onRender(fn)` | Registers `fn`. After EVERY call of the internal `render()` finishes, call each registered fn with `(state)`. Returns a function that unregisters `fn`. If a listener throws, catch it, `console.error` it, and still call the remaining listeners. |
| `command(commands)` | Calls the existing internal `command(commands)` and returns its promise unchanged (resolves `true` on success, `false` on failure; resolves immediately when `shared`). |
| `getSelection()` | Returns a NEW array of the entry ids in the internal `selectedEntries` set. |
| `setSelection(ids)` | Replaces the contents of `selectedEntries` with `ids` (ignore ids not present in `state.entries`), then updates the UI the same way user selection does (`syncSelection()` and `renderSelectionBar()` or whatever the existing selection change path calls), then dispatches `deck-lab:selection`. |
| `setEntryFilter(fn, label)` | `fn` is `null` or a predicate `(entry) => boolean`. When set, the List, Grid and Spoiler table views render only entries where `fn(entry)` is truthy (commanders included). Re-renders immediately. While a filter is active, show a chip in `.dl-builder-toolbar` with attribute `data-entry-filter-chip`, text `Filtered: <label>` (or `Filtered` if no label), and a button inside it with `aria-label="Clear filter"` and attribute `data-entry-filter-clear` that calls `setEntryFilter(null)`. With `null`, remove the chip and render everything. The playmat view is unaffected. |
| `focusEntry(entryId)` | Finds the first element in `#table-view` with `data-entry-id` equal to `entryId`, sets `tabindex="-1"` if it has no tabindex, calls `scrollIntoView({block: "nearest"})` and `focus()`. Dispatches `deck-lab:focus-entry`. Returns `true` if found, `false` otherwise. |
| `railSection(id, title)` | Returns a `<section>` inside `.dl-stats-rail` with attribute `data-ext-section="<id>"`. If one already exists, return it unchanged. Otherwise create it with an `<h2>` containing `title`, append it as the last child of `.dl-stats-rail`, and return it. |
| `render()` | Calls the internal `render()`. |

Events, all dispatched on `document` as `CustomEvent`:
- `deck-lab:ready`, detail `{ api: window.DeckLabBuilder }`, dispatched exactly once, right after `window.DeckLabBuilder` is assigned.
- `deck-lab:render`, detail `{ state }`, after every `render()` (after the `onRender` listeners run).
- `deck-lab:selection`, detail `{ ids: [...] }`, whenever the selection changes, whether through `setSelection` or the existing UI paths (checkbox clicks, clear selection, bulk move).
- `deck-lab:entry-hover`, detail `{ entryId }`, on `mouseover` and `focusin` of any element inside `#table-view` that has a `data-entry-id` attribute, or a descendant of one. Use ONE delegated listener on `#table-view`. Do not dispatch twice in a row for the same id.
- `deck-lab:focus-entry`, detail `{ entryId }`, from `focusEntry`.

Every element that represents one deck entry in the List, Grid and Spoiler views must carry
`data-entry-id="<entry.id>"`. Some already do (`dataset.entryId`); add it where it is missing.

Nothing else may change. Every existing test must still pass without modification.

## Acceptance criteria → required tests (all in `tests/test_builder_extension_api.py`)
Drive `deck-lab-builder.js` through a new harness `tests/builder_api_harness.mjs`. Base it on
`tests/builder_selection_harness.mjs`: copy its fake-DOM approach and extend it as needed. Pass the
scenario name on argv and print a JSON result for pytest to assert on.

- AC-1 `test_api_exposes_exact_contract`: `window.DeckLabBuilder` has exactly the listed keys, and `version === 1`.
- AC-2 `test_ready_event_fires_once_with_api`.
- AC-3 `test_on_render_listener_runs_after_render_and_unsubscribes`: the listener is called after `api.render()`; after calling the returned unsubscribe function it is not called again; a throwing listener does not stop a second listener.
- AC-4 `test_entry_filter_limits_rendered_entries_and_chip_clears_it`: with a filter keeping one entry, exactly one `[data-entry-id]` element is rendered in `#table-view`, and a `[data-entry-filter-chip]` exists containing the label. Clicking `[data-entry-filter-clear]` brings back all entries and removes the chip.
- AC-5 `test_set_selection_updates_ui_and_fires_event`: `getSelection()` returns the ids passed in, unknown ids are dropped, and a `deck-lab:selection` event fires with those ids.
- AC-6 `test_shared_mode_command_resolves_without_fetch`: with `data-shared="true"`, `command([...])` resolves and `fetch` is never called.
- AC-7 `test_rail_section_is_idempotent`: calling it twice with the same id returns the same element, and `.dl-stats-rail` contains exactly one `[data-ext-section="x"]`.
- AC-8 `test_every_rendered_entry_has_entry_id_in_all_displays`: in each of the text, grid and spoiler displays, the number of `[data-entry-id]` entry elements equals the number of entries in the fixture document.
- AC-9 `test_entry_hover_event_dispatches_entry_id`: a `mouseover` on a row's child element dispatches `deck-lab:entry-hover` with that row's entry id.
- AC-10 `test_focus_entry_returns_false_for_unknown_id`.

Tests must skip with `pytest.skip("node is required ...")` when `node` is not available, matching the existing harness tests.

## Out of scope
New UI features, CSS changes, and refactoring of existing functions beyond the hooks described above.
