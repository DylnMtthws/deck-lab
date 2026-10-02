# T13 — Compare to the meta, and "Replace with…"

Agent: HIGH (Cursor, Grok 4.7). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T12 (`src/sabermetrics/deck_evidence.py`, `/api/decks/<id>/evidence`, `deck-lab-evidence.js`).

## Why
Moxfield and Archidekt can only tell you what is popular. Deck Lab can show where YOUR list departs
from recorded tournament lists for the same commander:
- Staples you lack.
- Cards nobody plays.
- Same-role swaps, each with its sample stated.

## Allowed files
- `src/sabermetrics/deck_evidence.py`: add methods; do not change T12 method signatures or outputs.
- `src/sabermetrics/ui/builder_routes.py`: two new GET routes.
- `src/sabermetrics/ui/static/deck-lab-meta-compare.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: the script tag, plus one toolbar button `[data-meta-compare-open]` labeled `Compare to meta` and a `<dialog data-meta-compare>`.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_meta_compare.py` (new), `tests/meta_compare_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T13.md` (new)

## Requirements
1. `meta_diff(document, window_days, min_event_size, staple_threshold=0.5, limit=30)` returns:
   - `missing_staples`: cards in ≥ `staple_threshold` of recorded lists that are NOT in the deck's library zones. Each has `{oracle_id, name, lists, rate, role_guess}`, sorted by rate descending. Cards outside the commander's color identity are never suggested; check with the card table's `color_identity`, as legality checks do elsewhere. Banned or not legal-in-99 cards are never suggested.
   - `unplayed`: deck library entries with 0 recorded lists while the denominator D ≥ 10. Below D = 10, return `[]` and `"too_few_lists": true`.
   - It also carries `denominator`, `window_days` and `min_event_size`, exactly as T12 does.
2. `alternatives(document, oracle_id, window_days, min_event_size, limit=8)` returns cards not in the deck, legal and inside the color identity, that share the target entry's role:
   - Use the entry's builder role.
   - For cards without a stored role, use the same oracle-keyword role classification the repo already has. Search for an existing role tagger (`role_tagger`, `oracle_keywords`); do not write a new classifier. If no reusable classifier exists, restrict alternatives to cards that appear in recorded lists for this commander, use the role the deck assigns, and state this in the report.
   - Sort by inclusion rate descending, each with `lists`/`rate`.
   - If the entry has no role, return `{"needs_role": true}`.
3. Routes, both owner-only, with the same window defaults as T12:
   - `GET /api/decks/<id>/meta-diff`
   - `GET /api/decks/<id>/alternatives/<oracle_id>`
4. UI:
   - `Compare to meta` opens the dialog. It has two sections: "Staples you're missing" (each row: name, full inclusion statement, and an `Add` button that adds the card to the current Add-to destination through `DeckLabBuilder.command`) and "In your list, in no recorded list" (each row: name and a `Find replacement` button). It also shows the denominator sentence and the absence states.
   - `Replace with…`: in the T08 card panel evidence slot area, add a `Replace with…` button for the shown entry. It lists alternatives. Choosing one sends ONE command batch that removes the entry and adds the alternative into the same zone with the same role. That makes it a single undo step under T09.
   - Every rate is shown with its denominator, window and floor (same rule and same test as T12 AC-12).

## Acceptance criteria → required tests (`tests/test_meta_compare.py`)
- AC-1 `test_missing_staples_threshold_identity_and_legality_filters`
- AC-2 `test_unplayed_requires_min_denominator`
- AC-3 `test_alternatives_share_role_and_exclude_deck_cards_and_off_identity`
- AC-4 `test_alternatives_needs_role_when_entry_has_no_role`
- AC-5 `test_routes_owner_only`
- AC-6 `test_dialog_add_staple_sends_add_command_to_destination` (harness)
- AC-7 `test_replace_with_sends_single_batch_remove_and_add_same_zone_role` (harness)
- AC-8 `test_meta_compare_never_shows_rate_without_denominator` (harness)
- AC-9 `test_bounded_queries_for_meta_diff`
