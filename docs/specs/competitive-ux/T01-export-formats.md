# T01 — Paste-ready export formats

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.

## Why
Players move decks between Deck Lab, Moxfield and Archidekt. "Copy list" already produces a clean
list on the client (`deck-lab-export.js`). The downloadable `export.txt` still has zone headers and
private zones, and there is no Archidekt-specific format.

## Allowed files
- `src/sabermetrics/deck_documents.py`: only `DeckDocumentRepo.export_text` and new private module-level helpers next to it.
- `src/sabermetrics/ui/builder_routes.py`: only the `export` view (`/build/deck/<deck_id>/export.txt`).
- `src/sabermetrics/ui/static/deck-lab-export.js`
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: only inside `<div id="deck-export-actions" role="menu">`.
- `tests/test_competitive_export.py` (new)
- `tests/export_format_harness.mjs` (new, if you need Node for the parity test)
- `docs/specs/competitive-ux/reports/T01.md` (new)

## Requirements
1. `export_text(document, fmt="sections")`: keep the existing output byte-for-byte when `fmt == "sections"` (the default). Add:
   - `fmt == "plain"`:
     - Commander entries first, in document order.
     - Then every non-commander entry whose zone is a public library zone (use `_is_public_library_zone` on the zone name; entries with a missing zone count as "Unsorted"). Merge quantities by card name, sort names case-insensitively, one line each as `"{qty} {name}"`.
     - If a commander's name also appears in the library, merge it into the commander line, exactly as `deck-lab-export.js` `exportLines` does.
     - No title, no headers, no blank lines. The output ends with a single `\n`.
   - `fmt == "archidekt"`: the same set and order as `plain`, but each line is `"{qty}x {name}"`, and commander lines end with `" [Commander]"`.
   - Any other `fmt`: raise `ValueError`.
2. Route: accept the query parameter `format` with values `sections` (default when absent), `plain` and `archidekt`. Any other value returns HTTP 400. Keep the `attachment` filename behaviour.
3. `deck-lab-export.js`: add an Archidekt line builder with the same rules as requirement 1, exposed next to the existing exported function(s) on the same global object the file already uses. Do not change the existing "Copy list" output.
4. `builder.html` export menu, inside `#deck-export-actions`, keeping the existing items:
   - A `<button type="button" role="menuitem" data-export-copy-archidekt>Copy for Archidekt</button>` that copies the Archidekt text. Reuse the existing copy, clipboard-fallback and status behaviour in `deck-lab-export.js`; the status text on success is `Copied for Archidekt`.
   - An `<a role="menuitem" data-export-download href="/build/deck/<id>/export.txt?format=plain" download>Download .txt</a>`. Build the URL from the deck id already available to the template (`document.id`).
   - Both items follow the same blocked/disabled rules as "Copy list" while a save is pending (see `exportBlocked()` / `syncExport()` in `deck-lab-builder.js`). If that requires changing `deck-lab-builder.js`, STOP and report BLOCKED. It is not an allowed file.

## Acceptance criteria → required tests (all in `tests/test_competitive_export.py`)
- AC-1 `test_sections_format_unchanged`: the default output equals the output of the code before your change, for a fixture document with two zones plus a "Maybeboard" zone. Hard-code the expected string.
- AC-2 `test_plain_format_commander_first_sorted_merged_no_private_zones`: covers a commander, duplicate names across two zones (merged), a "Considering" zone entry (excluded), mixed-case sorting, no blank lines, and a single trailing newline.
- AC-3 `test_archidekt_format_marks_commanders`: commander lines end with `[Commander]`, and quantities use the `Nx` form.
- AC-4 `test_route_format_param_plain_and_archidekt` (Flask test client, logged-in owner): status 200, `text/plain`, and the body matches `export_text` for that format.
- AC-5 `test_route_rejects_unknown_format`: returns 400.
- AC-6 `test_plain_matches_client_copy_list`: for the same document, the server `plain` output equals the client "Copy list" lines joined with `\n` plus a trailing `\n`. Run `deck-lab-export.js` in Node through the new harness.
- AC-7 `test_builder_menu_has_archidekt_copy_and_download`: the rendered builder page contains `data-export-copy-archidekt` and an `href` ending in `export.txt?format=plain`.
- AC-8 `test_client_archidekt_lines`: the Node harness shows that the JS Archidekt builder output equals the server `archidekt` output.

## Out of scope
Prices, buy links, Arena set codes, and import changes.
