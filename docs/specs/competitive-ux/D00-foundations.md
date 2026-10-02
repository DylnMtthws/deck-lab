# D00 — Design foundations (sequential; blocks D01–D07)

Agent: HIGH. Read COMMON.md, then `docs/design/DESIGN-SPEC.md` §1 and §3 in full.

## Deliver
1. **Tokens.** Add the §1.1 tokens to `:root` in `src/sabermetrics/ui/static/deck-lab.css`. This task is the ONLY one allowed to edit `deck-lab.css`, and only `:root`.
2. **Primitives.** Create `src/sabermetrics/ui/static/deck-lab-foundation.css` implementing the §1.4 primitives as refinements of the existing `dl-` classes, plus focus rings (§1.7), motion and reduced motion (§1.6), and the eyebrow and key-cap styles.
   - Where an existing primitive class already exists (`.dl-button`, `.dl-icon-button`, `.dl-field`, `.dl-segments`, `.dl-eyebrow`), restyle it to the spec here, through a later stylesheet, rather than adding a parallel class.
   - Add `.dl-select` (an appearance-none select with a chevron), the `.dl-chip` variants, `.dl-kbd`, `.dl-popover`, `.dl-tabs`/`.dl-tab`, and `.dl-dot`.
3. **Area stylesheets.** Create EMPTY area stylesheets, each with a one-line header comment naming its owner task:
   - `deck-lab-chrome.css` (D01), `deck-lab-list.css` (D02), `deck-lab-rail.css` (D03), `deck-lab-statusbar.css` (D04), `deck-lab-stacks.css` (D05), `deck-lab-playmat.css` (D06), `deck-lab-dialogs.css` (D07).
   - Link foundation plus all area stylesheets from `templates/deck_lab/builder.html` `{% block head %}`, in that order. Link foundation and `deck-lab-dialogs.css` from `templates/deck_lab/research.html` too, so D07 can style Research chips.
4. **Icons.** Create `src/sabermetrics/ui/static/deck-lab-icons.js` with exactly the §1.5 names, as an IIFE exposing `window.DeckLabIcons = { svg(name, opts), names }`. Load it on the builder page BEFORE `deck-lab-builder.js`.
5. **Extension API.** Extend `DeckLabBuilder.railSection(id, title, opts)` in `deck-lab-builder.js`:
   - `opts.tab` is one of `card|deck|tools` and defaults to `deck`.
   - If `.dl-stats-rail [data-rail-pane="<tab>"]` exists, append the section there. Otherwise keep today's behaviour (append to the rail).
   - Idempotency by id is unchanged.
   - Do NOT change any caller. D03 does that.
6. **Lint markers.** Add the `data-lint-bar` attribute to the builder toolbar container and to `[data-status-bar]`.

## Acceptance → tests (`tests/test_design_foundations.py`)
- AC-1 `test_tokens_present_in_root`: every §1.1 token name is in `deck-lab.css` `:root`.
- AC-2 `test_area_stylesheets_linked_in_order`: the rendered builder page links `deck-lab.css`, then foundation, chrome, list, rail, statusbar, stacks, playmat and dialogs as real `<link>` elements, in that order.
- AC-3 `test_icons_module_exposes_all_names_and_valid_svg`: Node harness. Every name returns an `<svg>` with `viewBox="0 0 24 24"`; unknown names throw.
- AC-4 `test_icons_loaded_before_builder`.
- AC-5 `test_rail_section_tab_option_routes_into_pane_and_falls_back`: harness, with and without panes.
- AC-6 `test_no_text_below_11px_in_foundation_css`: parse `deck-lab-foundation.css`; no `font-size` below 11 px.
- Visual DoD (DESIGN-SPEC §3): scenarios `list` and `playmat` at 1440 and 1280 must not REGRESS against the orchestrator baseline. Foundations restyle existing primitives, so some native-control counts may drop; none may rise.
