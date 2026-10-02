# T09 — Keyboard editing and undo/redo

Agent: HIGH (Cursor, Grok 4.7). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00 (`window.DeckLabBuilder`; contract in `T00-builder-extension-api.md`).

## Why
Moxfield users edit without the mouse (`/` to search, `Shift+Enter` to quick-add, and `Alt+1`/`Alt+2`
for quantity). Archidekt shows Undo/Redo. Deck Lab has neither, so every experiment feels permanent.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-hotkeys.js` (new)
- `src/sabermetrics/ui/static/deck-lab-history.js` (new)
- `src/sabermetrics/ui/static/deck-lab-builder.js`: ONLY to make the card search combobox support `Shift+Enter` = "add the first result". If that is already possible through a DOM event on `[data-card-search]` without editing this file, do NOT edit it.
- `src/sabermetrics/ui/templates/deck_lab/builder.html`:
  - Script tags after `deck-lab-builder.js`, inside `{% if not shared %}`.
  - An Undo button `[data-undo]` and a Redo button `[data-redo]` next to `#save-state`.
  - A keyboard-help `<dialog data-hotkeys-help>`.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_keyboard_and_undo.py` (new), `tests/keyboard_undo_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T09.md` (new)

## Requirements

### Undo/redo (`deck-lab-history.js`)
1. History is client-side and per page load.
   - Every user mutation that goes through `DeckLabBuilder.command(commands)` is recorded as `{forward: commands, inverse: <commands that restore the prior state>}`.
   - Compute the inverse from `DeckLabBuilder.getState()` BEFORE sending.
   - Wrap `DeckLabBuilder.command` so that all callers, including other extension modules, are captured. Builder-internal mutations that call the internal `command()` directly are NOT captured by wrapping; capture them by observing state diffs between renders instead.

   **You must decide and document in the report which approach you used.** Whichever you choose, the AC tests below are the contract.
2. Inverses are required for these command kinds (read `DeckDocumentRepo._apply_command` for their exact shapes): add card, remove entry, set quantity, move entry to zone, set role, rename zone, create zone, delete zone (restoring its entries' zones). If a kind has no safe inverse, that mutation clears the redo stack and is marked non-undoable. Show `Can't undo <action>` in the save-state title. It must never silently undo the wrong thing.
3. Undo sends the inverse batch as one `command` call; redo re-sends forward. Both go through the normal save path, so revision conflicts behave exactly as today (409 → reload). On 409, clear both stacks.
4. Buttons `[data-undo]` and `[data-redo]` are disabled when their stack is empty. Their tooltips (`data-dl-tip`) name the action, e.g. `Undo: remove Sol Ring`.
5. The stack is capped at 50; the oldest entries drop off.

### Hotkeys (`deck-lab-hotkeys.js`)
Ignore keys while focus is in an input, textarea, select or contenteditable, except where noted.
Never override browser shortcuts other than those listed.

| Key | Action |
|---|---|
| `/` | Focus `[data-card-search]` (works from anywhere outside inputs). |
| `Shift+Enter` in `[data-card-search]` | Add the first result to the current "Add to" destination. |
| `Cmd/Ctrl+Z` | Undo. |
| `Cmd/Ctrl+Shift+Z` and `Ctrl+Y` | Redo. |
| `j` / `k` or `ArrowDown` / `ArrowUp` | Move the "current entry" through visible List rows, using `DeckLabBuilder.focusEntry`. |
| `+` / `=` | Increase the current entry's quantity, never above the singleton rules the server enforces (let the server reject; surface its message). |
| `-` | Decrease the current entry's quantity; at 1, remove the entry (undoable). |
| `x` or `Space` | Toggle selection of the current entry (`setSelection`). |
| `m` | Open the existing per-row zone select for the current entry. |
| `Delete` / `Backspace` | Remove the current entry (undoable). |
| `?` | Open `[data-hotkeys-help]`, which lists every key above. |

The "current entry" is the last focused or hovered entry (`deck-lab:focus-entry` and `deck-lab:entry-hover`).
When `DeckLabBuilder.shared` is true, nothing is installed.

## Acceptance criteria → required tests (`tests/test_keyboard_and_undo.py`)
- AC-1 `test_inverse_for_each_supported_command_kind`: a table test; applying forward then inverse to a fixture state, using `DeckDocumentRepo` in Python for real semantics, yields the original entries and zones.
- AC-2 `test_undo_redo_roundtrip_through_command_api` (harness: forward, undo, redo; fetch bodies asserted)
- AC-3 `test_undo_buttons_disabled_and_tooltips_name_action`
- AC-4 `test_conflict_409_clears_history`
- AC-5 `test_non_undoable_command_clears_redo_and_says_so`
- AC-6 `test_history_capped_at_50`
- AC-7 `test_slash_focuses_search_but_not_inside_inputs`
- AC-8 `test_shift_enter_adds_first_result`
- AC-9 `test_jk_navigation_and_plus_minus_delete_on_current_entry`
- AC-10 `test_x_toggles_selection_and_m_opens_zone_select`
- AC-11 `test_help_dialog_lists_all_keys` (every key in the table appears in the dialog text)
- AC-12 `test_nothing_installed_in_shared_mode`
- AC-13 `test_builder_internal_mutation_is_undoable`: e.g. changing quantity with the row's +/− buttons, which are builder-internal, can be undone. This proves the chosen capture approach covers internal paths.
