# I03 — Card panel: playmat hover, reset on view switch, partner commanders

Agent: HIGH. Read COMMON.md and DESIGN-SPEC.md (rail / Card tab, and §3).
These are owner-reported defects on QA. Each one reproduces in real Chromium with the capture scenarios below.

## Defect 1 — the last Decklist hover sticks on the playmat
- `deck-lab:entry-hover` is dispatched only from `#table-view` (delegated `mouseover`/`focusin` near the end of `deck-lab-builder.js`).
- Playmat cards (`.dl-mat-card[data-entry-id]` inside `#playmat-view`) never feed the panel.
- Nothing resets the panel when the view changes.

Measured: after hovering "Faerie Mastermind" in the Decklist and switching to Playmat, the panel still shows Faerie Mastermind. Hovering a mat card does not change it.

**Fix:**
- a. Delegate the same hover and focus dispatch on `#playmat-view`, so hovering or focusing any `.dl-mat-card[data-entry-id]` (including Commander-zone cards) updates the panel. Use the same 60 ms hover debounce and the same event.
- b. When the active view changes (Decklist ↔ Playmat, through the `[data-view]` buttons or any `update_view` that changes `view_mode`), reset the panel to its DEFAULT state unless it is pinned. The default state is the commander display (Defect 2), or the empty hint if there is no commander.
  - Implement this via the builder API (for example, track `view_mode` in an `onRender` listener inside `deck-lab-card-panel.js`), not by reaching into builder internals.
  - Reset `lastHoverId` so the next hover always renders.
  - The builder's own `_hoverLastId` must also reset on view change; otherwise re-hovering the same Decklist row will not dispatch.

## Defect 2 — partner commanders: the panel shows only one
`boot()` and the default state use `firstCommander()`. Decks with two commanders (Partner, Background, Friends forever, and so on) show only the first.

**Fix (design):** when the deck has two commanders and the panel shows either one (the default state, or a hover/focus on either commander):
- Render a **commander pair strip** at the top of the panel (`[data-card-panel-pair]`):
  - two card thumbnails side by side, each half the panel width minus the gap, at card aspect ratio, with the name under each (`--t-12`, truncated);
  - each thumbnail is a `button` with `aria-pressed`, labelled "Show <name>";
  - the active one has a brand outline;
  - hovering the inactive one previews it; clicking it makes it active.
- Below the strip, render the normal detail block (name, cost, type, oracle text, meta, feedback, evidence slot) for the active commander. Do not render the large card frame in pair mode, because the thumbnails replace it.
- With a single commander, or for any non-commander card, render exactly as today.
- Pinning in pair mode pins the active commander.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-card-panel.js`
- `src/sabermetrics/ui/static/deck-lab-builder.js` (hover delegation and the view-change reset of `_hoverLastId` ONLY)
- `src/sabermetrics/ui/static/deck-lab-rail.css` (the pair-strip styles; foundation tokens only)
- New tests: `tests/test_card_panel_i03.py` and `tests/card_panel_i03_harness.mjs` (you may copy and extend `tests/card_panel_harness.mjs` into the new file)
- Do NOT modify existing tests.

## Acceptance (named tests; each must fail if its fix is reverted)
- AC-1 `test_hovering_playmat_card_updates_panel`
- AC-2 `test_view_switch_resets_panel_to_commander_unless_pinned`, which also asserts that a pinned card survives the switch.
- AC-3 `test_rehover_same_table_row_after_view_switch_dispatches`
- AC-4 `test_partner_pair_strip_renders_both_commanders_and_switches_active`
- AC-5 `test_single_commander_and_noncommander_render_unchanged`
- AC-6 `test_late_load_partner_default_state` (COMMON rule 9)

## Machine-checked visual proof (required)
Run from your worktree:
`PORT=5396 zsh /Users/dylan/Projects/mtg/deck-lab/worktrees/.cux/visual/review.sh I03 hover-then-playmat,playmat-hover-card,partner-panel,list,playmat`

Then read `/tmp/rv-I03/lint.json`. At BOTH 1440 and 1280:
- `hover-then-playmat.panelNames` must be `["Kinnan, Bonder Prodigy"]`;
- `playmat-hover-card.panelNames` must be one name that is NOT Kinnan (the hovered mat card);
- `partner-panel.panelNames` must contain "Thrasios, Triton Hero", and `document.querySelectorAll('[data-card-panel-pair] button').length` must equal 2 (check it in the screenshot as well);
- every scenario's summary line prints `clean`, and `errors@…` is `[]`.

Paste the `panelNames` objects verbatim into your report. Open `/tmp/rv-I03/partner-panel-1440.png` and describe what you see.

## Completion
COMMON.md definition of done: FULL Gate 1, ruff + black, and mypy. One commit: `competitive-ux(I03): card panel follows playmat hover, resets on view switch, shows partner pairs`.
