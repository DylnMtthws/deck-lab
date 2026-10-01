# T06 — Amendment 1 (from the orchestrator, after review of 6f4ddd8)

Accepted: the curve filter, pips and sources counting, the hypergeometric math, the literal script tag, and late-load init.

**Bug: user state is wiped on every builder render.** `renderOddsSection` and `renderSampleHandSection` rebuild their sections from scratch, including the local `_hand`, on EVERY render. Any render resets the odds inputs to Lands/7/1 and erases the drawn hand. Renders include clicking a curve bar, which calls `setEntryFilter` and re-renders.

## Fix
Keep user state in module-level variables that survive renders:
- **Odds:** the selected category, `seen` and `need` persist across renders. On render, recompute the result with the current deck. If the selected category no longer exists in the deck, fall back to `Lands`. Clamp `seen` to the new N.
- **Sample hand:** the drawn hand (entry ids, in draw order) persists across renders. On render, drop ids whose entry no longer exists in a library zone, keep the rest, and re-render the list. Reset the hand only on `Draw 7`.

## Additional acceptance criteria (add to `tests/test_interactive_stats.py`)
- AC-12 `test_odds_inputs_survive_builder_render`: set category=a role, seen=10, need=2; call `DeckLabBuilder.render()`; the inputs keep those values, and the result text is recomputed.
- AC-13 `test_sample_hand_survives_curve_filter_click`: draw 7, click a curve bin; the same 7 names are still listed.
- AC-14 `test_sample_hand_drops_removed_entries_on_render`

## Completion
COMMON.md definition of done; FULL suite; `git commit --amend`; include this file; update the report.
