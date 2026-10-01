# Competitive UX program — rules every task agent follows

You are implementing ONE task (Txx) from `docs/specs/competitive-ux/Txx-*.md`.
Read `CLAUDE.md` (project constraints) and your task spec before editing anything.
These rules are part of your task's definition of done. A task that breaks any
rule here is NOT done, even if its own acceptance criteria pass.

## Environment (use these exact commands)

```
PY=/Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python
RUFF=/Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/ruff
MYPY=/Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/mypy

# Gate 1 — full test suite (baseline on the base commit: 1617 passed, 31 skipped, 0 failed)
PYTHONPATH=src:. $PY -m pytest -q -p no:cacheprovider
# Gate 2 — lint (baseline: "All checks passed!")
$RUFF check src tests scripts/release_control.py scripts/storage_control.py scripts/storage_remote.py
# Gate 3 — types (baseline: "Success: no issues found")
PYTHONPATH=src $MYPY src
# Your task's own tests only (fast loop)
PYTHONPATH=src:. $PY -m pytest -q -p no:cacheprovider tests/<your test file>
```

Always set `PYTHONPATH=src:.` — the venv's installed package points at a
different checkout, and without it you test the wrong code.

## Hard rules

0. **The base commit has ZERO failing tests.** Any failure in Gate 1 is caused by your change. Never report failures as "pre-existing". Fix them, or report BLOCKED with the failing test names. Run the FULL suite for Gate 1, not a subset.

1. Work ONLY in your assigned worktree directory, on your assigned branch. Never
   `cd` elsewhere, never touch another worktree, never `git push`, never merge,
   never rebase onto anything, never use `git stash`.
2. Edit ONLY the files listed under "Allowed files" in your spec, plus the new
   test/harness files your spec names. If you believe another file must change,
   STOP and record it as BLOCKED in your report (see below) instead of editing it.
3. Do not modify or delete existing tests unless your spec explicitly says so.
4. No new dependencies: no pip/npm installs, no new CDN scripts or stylesheets.
   (Image/SVG URLs on `cards.scryfall.io`, `svgs.scryfall.io`, `api.scryfall.com`
   are allowed where your spec says so.)
5. Project constraints from `CLAUDE.md` are absolute. In particular:
   - No price, budget, USD, or cost figure anywhere in new UI or engine code (ADR-025).
   - No owned-cards / collection concept (ADR-025).
   - An inclusion or popularity rate is never shown without its denominator,
     window, and event-size floor (popularity_is_not_quality).
   - Missing data renders as an explicit absence ("no tournament evidence",
     "not simulated"), never as an empty area or an invented number.
   - Nothing under `src/sabermetrics/cedh/` may import `sabermetrics.ingestion`,
     `.pipeline`, `.reasoning`, `.analytics`, or a vendor model SDK.
6. Match the surrounding code: vanilla JS IIFEs (no frameworks, no modules
   syntax in browser files), Flask blueprints, Jinja templates, `dl-` CSS class
   prefix, dark theme variables already defined in `deck-lab.css`.
7. Every new interactive control has an accessible name (`aria-label` or visible
   text) and is reachable by keyboard.
8. JavaScript behaviour is tested the way this repo already does it: a Node
   harness under `tests/` that loads the browser file into a fake DOM with
   `node:vm` (see `tests/builder_selection_harness.mjs` and
   `tests/test_builder_selection_and_preview.py`), driven from a pytest test.

9. **Extension modules must work when loaded late.** Builder scripts are `defer` and run in order, so `deck-lab-builder.js` has ALREADY rendered and dispatched `deck-lab:ready` before your module runs.
   - If `window.DeckLabBuilder` exists at load: initialize and render immediately from `getState()`, and register `onRender`.
   - Only listen for `deck-lab:ready` when the API is absent.
   - Every builder extension task must include a test that loads your module AFTER the builder has rendered and fired ready, with no further events, and asserts that your UI is populated.

10. **Script and style tags must be literal HTML.** Write `<script src="{{ url_for('static', filename='x.js') }}" defer></script>`, wrapped in `{% if not shared %}...{% endif %}` when the module is owner-only. Never build a tag inside a `{{ '...' }}` string: Flask autoescapes it into visible text and the script never loads. Page tests must match a real element with a regex such as `<script src="[^"]*/x\.js" defer></script>`, not merely the filename.

## Definition of done (every task)

A task is DONE only when ALL of these are true:

- [ ] Every acceptance criterion (AC-n) in the spec is implemented, and each has
      at least one named test that would FAIL if the criterion were reverted.
- [ ] Every test named in the spec exists with exactly that name and passes.
- [ ] Gate 1 passes with 0 failures and no fewer passing tests than baseline
      (new tests raise the count; skips may stay at 31).
- [ ] Gate 2 prints "All checks passed!".
- [ ] Gate 3 prints "Success: no issues found".
- [ ] `git diff --stat <BASE>...HEAD` lists only allowed files, new test files,
      and your report.
- [ ] This command prints nothing (constraint guard), or your report explains
      every line it prints:
      `git diff <BASE>...HEAD -U0 -- src | grep '^+' | grep -i -E 'price|usd|budget|owned|collection'`
- [ ] Exactly ONE commit on your branch on top of BASE, message
      `competitive-ux(Txx): <task title>` (use `git commit --amend` to fold fixes in).
- [ ] The report file exists and is part of that commit.

`<BASE>` is the commit your branch started from; it is written in your spec run header.

## Report file (required): `docs/specs/competitive-ux/reports/Txx.md`

```
# Txx report
Status: DONE | BLOCKED
## Acceptance criteria
- AC-1: <one line> — test: tests/<file>.py::<test_name>
- AC-2: ...
## Files changed
<output of git diff --stat BASE...HEAD>
## Gates (paste the LAST line of each)
Gate 1: ...
Gate 2: ...
Gate 3: ...
Constraint guard: <empty | explanation per line>
## Deviations / notes
<anything you did differently from the spec, and why; "none" if none>
```

If you cannot meet a criterion without breaking a rule, set `Status: BLOCKED`,
explain exactly which criterion and why, still commit whatever is complete and
passing, and stop. Never fake a test, skip a test, or weaken an assertion to
get to green. The orchestrator re-runs every gate independently and reads the diff.
