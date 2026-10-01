# T06 — Interactive stats: curve filter, pips vs sources, odds, sample hand

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00 (`window.DeckLabBuilder`; contract in `T00-builder-extension-api.md`).

## Why
Moxfield's mana curve is clickable and filters the list to those cards. It also shows color
requirements against mana production, a draw-odds calculator and a sample hand. Deck Lab's rail has
a static curve and per-card color counts.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-stats.js` (new)
- `src/sabermetrics/ui/static/deck-lab-builder.js`: ONLY inside `renderStats()`. On each curve bin element (`.dl-curve-bin`), add `data-mana-bucket="<0..5>"`, `role="button"`, `tabindex="0"`, and `aria-label="<count> cards at mana value <label>"`. No other change.
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: only the script tag, after `deck-lab-builder.js`.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_interactive_stats.py` (new), `tests/interactive_stats_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T06.md` (new)

## Requirements
`window.DeckLabStats` exposes the pure functions below for tests. "Library entries" means
non-commander entries in library zones (exclude private zones, same list as `deck-lab-export.js`),
quantity-weighted.

1. **Curve filter.** Clicking a `.dl-curve-bin`, or pressing Enter/Space on it, calls `DeckLabBuilder.setEntryFilter(fn, "Mana value <label>")`.
   - `fn` keeps non-land library entries whose bucket matches. The bucket is `min(5, floor(mana_value))`; label `5+` for bucket 5.
   - Clicking the same bin again clears the filter.
   - Clicking a different bin replaces the filter.
   - The active bin gets class `is-active`. Re-apply it after each render, because `renderStats` rebuilds the bins.
2. **Pips vs sources** (`pipsAndSources(state)` → `{W:{pips,pipShare,sources}, U:..., B:..., R:..., G:...}`). Render it in `DeckLabBuilder.railSection("pips", "Color requirements")`.
   - Pips: count `{X}` symbols in `mana_cost` of non-land library entries.
     - `{W}` counts 1 for W.
     - Hybrid `{W/U}` counts 1 for each color.
     - `{2/W}` and `{W/P}` count 1 for W.
     - Generic, `{C}` and `{X}` are not counted.
   - `pipShare` = pips for the color ÷ total colored pips, as an integer percent (0 when the total is 0).
   - Sources: library entries whose type line contains `Land`, or whose `oracle_text` contains `Add {` and that are artifacts or creatures, counted quantity-weighted.
     - A source counts for a color if its oracle text contains `{<C>}` within an "Add" clause.
     - It counts for ALL colors of the commanders' combined `color_identity` if it says `any color` or `mana of any`.
   - Render one row per color in the commander identity: `<div data-pips-color="W">` with the symbol letter, `Pips 34%` and `Sources 12`.
   - When there are no commanders, render all 5 colors.
   - The rail section must state the rule in one muted line: `Sources: lands and mana producers that can add the color.`
3. **Odds calculator** (`hypergeomAtLeast(N, K, n, k)`: the probability of at least k successes when drawing n from N with K successes; exact, computed with log-factorials or iterative products; returns 0..1). Render it in `railSection("odds", "Draw odds")`.
   - A `<select data-odds-category>`: `Lands`, then every role present in library entries (the human label rules from the role list in `deck-lab-builder.js`).
   - Number inputs `[data-odds-seen]` (default 7, min 1, max N) and `[data-odds-need]` (default 1, min 0).
   - Output `[data-odds-result]` reads `XX.X% to see at least k <category> in n cards (K in N)`.
   - N = library entry count. K = count in the category, where Lands means type contains Land.
   - Recompute on input and on render.
4. **Sample hand** (`sampleHand(state, count, rng)` returns entries drawn without replacement from the library expanded by quantity; `rng` is a function returning [0,1), defaulting to `Math.random`).
   - Render in `railSection("sample-hand", "Sample hand")`: a `Draw 7` button `[data-sample-draw]`, a `Draw a card` button `[data-sample-next]` (disabled until a hand exists), and a list `[data-sample-cards]` of the drawn names.
   - Each name is a button that calls `DeckLabBuilder.focusEntry(entry.id)`.
   - Show `Lands in hand: L` under the list.
   - If the library has fewer than 7 cards, draw what is available and say so.

## Acceptance criteria → required tests (`tests/test_interactive_stats.py`)
- AC-1 `test_curve_bins_have_bucket_attrs_and_are_keyboard_reachable`
- AC-2 `test_curve_click_sets_and_clears_filter_with_label`: the second click on the same bin calls `setEntryFilter(null)`; the active class survives a re-render.
- AC-3 `test_pips_counting_rules_table`: covers mono, hybrid, two-brid (`2/W`), phyrexian, generic/colorless/X ignored, lands excluded from pips.
- AC-4 `test_sources_counting_rules`: covers a basic land, a dual, an "any color" land in a 3-color identity, a mana dork, a non-producing land, and a private-zone card that is ignored.
- AC-5 `test_hypergeom_known_values`, checked to 4 decimals:
  - N=99, K=36, n=7, k=2 → 0.7985 (±0.0005)
  - N=99, K=36, n=7, k=0 → 1.0
  - N=99, K=0, n=7, k=1 → 0.0
  - N=60, K=24, n=7, k=3 → 0.5879 (±0.0005)

  Verify these numbers in Python with `math.comb` inside the test itself, and assert the JS matches the Python value.
- AC-6 `test_odds_ui_updates_result_text`
- AC-7 `test_sample_hand_deterministic_with_seeded_rng_and_no_replacement` (a 4-of entry may appear up to 4 times; total equals 7)
- AC-8 `test_sample_hand_short_library_message`
- AC-9 `test_builder_page_includes_stats_script` (Flask test client)
- AC-10 `test_curve_bin_keyboard_enter_and_space_toggle_filter`
- AC-11 `test_pips_section_limited_to_commander_identity_and_states_rule`
