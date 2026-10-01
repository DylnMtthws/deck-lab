# I01 — Integration fix: D01 toolbar harness after D02 rows

Agent: LOW. Read COMMON.md.

## Context
On the integration branch (D00 + D05 + D02 + D01 merged), 7 tests in `tests/test_design_toolbar.py` fail with `TypeError: Cannot set properties of undefined (setting 'checked')` from `tests/design_toolbar_harness.mjs`.
- D02's rows now render icons with `window.DeckLabIcons` (`static/deck-lab-icons.js`, loaded before the builder on the real page).
- D01's harness was written before D02 and does not load that file, so row rendering throws and `.dl-row-select` checkboxes never exist.

## Allowed files
- `tests/design_toolbar_harness.mjs`
- `tests/test_design_toolbar.py`: ONLY if the harness invocation must pass an extra file path.

## Requirements
- Load `src/sabermetrics/ui/static/deck-lab-icons.js` into the harness before the builder, mirroring the page's script order.
- Fix any other harness-only gap that D02's row markup exposes, for example new row structure classes.
- Do NOT weaken any assertion, and do NOT change product code.
- If a failure turns out to be a PRODUCT bug, not a harness gap, stop and report BLOCKED with details.

## Acceptance
- All tests in `tests/test_design_toolbar.py` pass on this branch.
- FULL Gate 1 has 0 failures; Gate 2 (ruff + black) and Gate 3 are clean.
- Commit message: `competitive-ux(I01): toolbar harness loads icons after D02 rows`.
