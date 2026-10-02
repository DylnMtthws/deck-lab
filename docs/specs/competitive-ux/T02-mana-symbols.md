# T02 — Real mana symbols

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.

## Why
Mana costs and color pips render as colored CSS circles with letters (`.mana-W` and `.dl-mana-generic`).
Moxfield and Archidekt use the real symbols, which players read instantly.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-mana.js` (new)
- `src/sabermetrics/ui/static/deck-lab-builder.js`: only the body of `manaToken(symbol)`.
- `src/sabermetrics/ui/templates/deck_lab/_mana.html` (new Jinja macro file)
- `src/sabermetrics/ui/templates/deck_lab/*.html`: only to swap existing mana or color pip markup for the macro, and to add the `deck-lab-mana.js` script tag to `builder.html`'s `{% block scripts %}`. It must come BEFORE `deck-lab-builder.js`.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_mana_symbols.py` (new), `tests/mana_symbols_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T02.md` (new)

## Requirements
1. `deck-lab-mana.js` defines `window.DeckLabMana = { fileName(symbol), symbol(symbol, opts) }`. Use the same IIFE style as the other static files.
   - `fileName(symbol)` takes the inside of a brace, such as `W`, `2`, `10`, `X`, `W/U`, `2/W`, `W/P`, `G/U/P`, `C`, `S`, `T` or `Q`. It returns the Scryfall SVG file name: uppercase, slashes removed, plus `.svg`. Examples: `W/U` → `WU.svg`, `2/W` → `2W.svg`, `w/p` → `WP.svg`, `10` → `10.svg`, `G/U/P` → `GUP.svg`. For anything not matching `^[0-9WUBRGCSXYZTQPE/½∞]+$` (case-insensitive), it returns `null`.
   - `symbol(symbol, opts)` returns an element:
     - If `fileName` is `null`, return a `<span class="dl-mana-symbol dl-mana-generic">` containing the text (the current fallback look).
     - Otherwise return `<img class="dl-ms" src="https://svgs.scryfall.io/card-symbols/<FILE>" alt="{<SYMBOL>}" width="16" height="16" loading="lazy" decoding="async">`.
     - If `opts && opts.decorative`, set `alt=""` and `aria-hidden="true"`.
     - On the img `error` event, replace it in the DOM with the span fallback, so a missing network never shows broken images.
2. `manaToken(symbol)` in `deck-lab-builder.js` returns `window.DeckLabMana.symbol(symbol, {decorative: true})` when `window.DeckLabMana` exists. Otherwise it keeps the current implementation. This keeps existing harness tests valid when they do not load the mana file.
3. Jinja macro file `_mana.html` defines `mana_symbol(sym, decorative=True)`, which renders the same `<img>` markup with an `onerror` fallback. Inline handlers are acceptable only if no CSP forbids them; there is no global CSP on these pages. If you prefer, add a delegated `error` listener in `deck-lab-mana.js` instead, and load that script on the pages that use the macro. Replace these with the macro:
   - Every server-rendered color pip that currently outputs `class="mana mana-{{ ... }}"` with a letter inside, in templates under `templates/deck_lab/`, EXCEPT inside `<label>` chips for form checkboxes (the Research color filter). Those keep the CSS circles because they are toggle controls.
4. CSS appended to `deck-lab.css`: `.dl-ms { width: 1em; height: 1em; vertical-align: -0.125em; display: inline-block; }` plus a variant for card-row mana cost alignment if needed. Nothing else in that file may change.

## Acceptance criteria → required tests (all in `tests/test_mana_symbols.py`)
- AC-1 `test_file_name_mapping_table`: Node harness, a parametrized table of at least 12 inputs covering every example above plus at least two invalid inputs that return `null`.
- AC-2 `test_symbol_returns_img_with_scryfall_src_and_alt`: covers both the decorative and the non-decorative variant.
- AC-3 `test_symbol_falls_back_to_span_on_error`: dispatch `error` on the img; the parent now contains the span with the symbol text.
- AC-4 `test_builder_mana_cost_uses_svg_when_mana_js_loaded`: load `deck-lab-mana.js`, then `deck-lab-builder.js`, in the harness. A rendered entry with mana cost `{1}{U}{U}` produces three `img.dl-ms` elements.
- AC-5 `test_builder_falls_back_without_mana_js`: without the mana file, the existing `.dl-mana-symbol` elements still render. The existing builder tests already cover this, but assert it explicitly too.
- AC-6 `test_server_templates_use_macro`: render the commander page and the library page with a fixture deck through the Flask test client. Assert `svgs.scryfall.io/card-symbols/` appears, and that no `class="mana mana-` pip remains outside form checkbox labels.
- AC-7 `test_builder_page_loads_mana_js_before_builder_js`: in the rendered builder HTML, the index of `deck-lab-mana.js` is less than the index of `deck-lab-builder.js`.

## Out of scope
Changing color filter chips, chart colors, or any non-symbol styling.
