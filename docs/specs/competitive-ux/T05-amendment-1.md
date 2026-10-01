# T05 — Amendment 1 (from the orchestrator, after review)

Review result for commit 892154d: **ONE BUG.** Everything else is accepted.

**Bug: the status bar is empty on page load.** In the real page, all scripts are `defer` and run in order:
1. `deck-lab-builder.js` runs first. It renders and dispatches `deck-lab:ready` before `deck-lab-status.js` has even executed.
2. Your `deck-lab:ready` listener therefore never fires.
3. Your `onRender` registration only catches FUTURE renders.

So the bar stays blank until the user changes something. Your harness dispatched `ready` after loading every script, which hid this.

## Fix
If `window.DeckLabBuilder` already exists when the script runs:
- register `onRender`, AND
- render immediately from `getState()`.

Only wait for `deck-lab:ready` when the API does not exist yet. Never render twice for one state on boot.

## Additional acceptance criterion (add to `tests/test_status_bar.py`)
- AC-9 `test_bar_populated_when_loaded_after_ready`:
  1. In the harness, load `deck-lab-builder.js`.
  2. Let it render and dispatch `deck-lab:ready`.
  3. THEN load `deck-lab-status.js`, with no further events or renders.
  4. `[data-status-bar]` must already contain the count and the type spans.

## Completion
COMMON.md definition of done. The base has ZERO failing tests; run the FULL suite. `git commit --amend` into the single commit, including this file, and update the report.
