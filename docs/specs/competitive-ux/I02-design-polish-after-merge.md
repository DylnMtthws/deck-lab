# I02 — Integration polish found in the orchestrator's full-capture review

Agent: LOW. Read COMMON.md and DESIGN-SPEC.md §3.
The orchestrator found these defects by capturing the integrated builder in real Chromium. Each one has a single, exact fix.

## 1. Stacks mode hides the toolbar "…" button and restyles the rail tabs
`src/sabermetrics/ui/static/deck-lab-stacks.css` still has D05's stopgap overrides scoped to `body.dl-has-stacks`. They predate the shared foundation and now break the chrome in Stacks mode:
- `.dl-decklist-more` is hidden (the "…" overflow button disappears);
- every rail `button` is restyled, so the Card/Deck/Tools tabs lose their selected state.

**Fix:** delete every `body.dl-has-stacks …` rule in that file EXCEPT `body.dl-has-stacks .dl-bulk-controls.is-empty { display: none; }`. Keep the JS class toggle.

**AC-1** `test_stacks_css_has_no_global_chrome_overrides`: parse the CSS. The only selector starting with `body.dl-has-stacks` is the bulk-controls rule.

## 2. Playmat: the default Unsorted zone sits under the Commander box
`deck_documents.py` creates Unsorted at `(80, 120)`, in both the new-deck and text-import paths. The builder draws the Commander box at left 18, top 18, width ≥ 170 (`deck-lab-builder.js`, `dl-mat-command`), so the two overlap on every new deck.

**Fix (both parts):**
- a. Server: new decks create Unsorted at `x=220, y=18` (both INSERTs).
- b. Client, for existing decks: when rendering mat zones, if commanders exist and a zone's stored top-left point lies inside the Commander box rectangle (left 18, top 18, the box's computed width, height 260), render that zone at `left = 18 + boxWidth + 24` and its stored y. The result is display-only; do NOT send a command. `freeZonePosition` must treat the Commander box rectangle as occupied.

**Tests:**
- **AC-2a** `test_new_deck_unsorted_zone_clear_of_commander_box`: repo test, Unsorted x/y equals 220/18 for both creation paths.
- **AC-2b** `test_mat_zone_inside_commander_box_renders_beside_it` (Node harness): a zone at (80, 120) with a commander present renders with `left >= 18 + 170 + 24`; a zone at (600, 400) is unchanged.
- **AC-2c** `test_free_zone_position_avoids_commander_box`.

## 3. Off-scale type (lint `offScaleSamples`)
Type scale is {11, 12, 13, 14, 16, 20, 28}.
- Playmat zoom −/+ buttons (`.dl-icon-button`, 15px): use 14px.
- Dialog `h2` titles (24px, e.g. "Keyboard shortcuts"): use 20px.
- Research page `h1` "Research" (24px): use 28px.
- Research keycap `kbd` and the colour filter `span.mana` pips (10px): use 11px.
- Account avatar emoji `span` (22px): use 20px.

**AC-3** `test_offscale_sizes_fixed`: CSS assertions on the selectors you changed.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-stacks.css`
- `src/sabermetrics/ui/static/deck-lab-builder.js`
- `src/sabermetrics/deck_documents.py`
- `src/sabermetrics/ui/static/deck-lab-playmat.css`, `deck-lab-dialogs.css`, `deck-lab.css`, `avatar.css`
- research and avatar CSS/templates ONLY where they own the selectors above
- New test files: `tests/test_design_polish_i02.py` and `tests/design_polish_i02_harness.mjs`
- Do not modify existing tests. If an existing test asserts the old `(80, 120)` default, STOP and report BLOCKED with its name.

## Machine-checked visual proof (required)
From your worktree run:
`PORT=5392 zsh /Users/dylan/Projects/mtg/deck-lab/worktrees/.cux/visual/review.sh I02 stacks,playmat,hotkeys,research-syntax`
- Every line must print `clean`, except that `research-syntax` may still list a `10px` entry if it comes from a slider tick.
- Paste the printed lines into the report.
- Open `/tmp/rv-I02/stacks-1440.png` and `/tmp/rv-I02/playmat-1440.png`. Confirm in the report that the "…" button is present in Stacks mode and that no zone overlaps the Commander box.

## Completion
COMMON.md definition of done; FULL Gate 1, ruff + black, mypy. One commit: `competitive-ux(I02): design polish after integration review`.
