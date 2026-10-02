# T12 — Evidence badges and "Why this card?"

Agent: HIGH (Cursor, Grok 4.7). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T08 (card panel with `[data-card-panel-slot="evidence"]` and `window.DeckLabCardPanel`)
and T10 (decks imported from candidates carry `source_kind="candidate"` and roles).

## Why
Moxfield and Archidekt show what a card IS. Deck Lab can show WHY it is in the deck: tournament
inclusion with a stated sample, its role, and the generator's reasoning. Today none of that reaches
the builder.

## Constraints that apply with extra force
- `popularity_is_not_quality`: an inclusion rate is ALWAYS shown with its denominator, window, and event-size floor, or not at all.
- `absence_is_visible`: when there is no data, show `No tournament evidence for this commander in the last N days`. Never show an empty badge or 0%.
- No price and no collection data.

## Allowed files
- `src/sabermetrics/deck_evidence.py` (new): pure query/service module over the local research tables (`research_results`, `deck_cards`, and the card tables). No Flask imports. It must not import anything under `sabermetrics.cedh` that would violate the cEDH import rules; it is outside `cedh/`, but keep it independent anyway.
- `src/sabermetrics/ui/builder_routes.py`: ONE new route, `GET /api/decks/<deck_id>/evidence?window=<days>`.
- `src/sabermetrics/ui/static/deck-lab-evidence.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: only the script tag (after `deck-lab-card-panel.js`).
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_deck_evidence.py` (new), `tests/deck_evidence_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T12.md` (new)

## Requirements
1. `DeckEvidenceService(db_path).for_deck(document, window_days, min_event_size)` returns:
   ```
   {"commander_ids": [...], "window_days": N, "min_event_size": M,
    "denominator": D,                 # distinct recorded lists for this commander (pair) in window, from events with >= M players
    "available": bool,                # False when D == 0 or the commander is unknown to research tables
    "cards": {oracle_id: {"lists": L, "rate": L / D}},   # only for entries in the deck; omit cards with L == 0
    "explanations": {oracle_id: {"role": str, "source": str, "priority": float, "reason": str | None}}}
   ```
   - Use the same window semantics as `ResearchRepo` (`window_days` 30/60/90/180, or 0 for all). Read how `research.py` scopes dates and counts `inclusion_denominator`, and reuse that logic. Do not re-derive it differently.
   - `min_event_size` defaults to the cEDH settings value (`load_cedh_settings().meta.min_event_size`). If the research tables have no event-size column, apply no floor, set `"min_event_size": None`, and the UI must then say `event size not recorded`.
   - For partner commanders, use lists that contain both commanders.
   - `explanations` is filled only when `document["source_kind"] == "candidate"`. It comes from the stored candidate: `role`, `source`, `priority` per card, and a per-card `reason` from `explanation_json` if that structure contains per-card text (inspect `cedh/responses.py` `DeckExplanation`), otherwise `None`. Never generate text here.
2. The route is owner-only, or read-only for shared tokens if trivial; otherwise owner-only. It returns the dict as JSON with a 30-day default window. The query must be bounded: a single aggregate query per deck, not one per card. A test asserts this with a query counter or `sqlite3` trace callback.
3. `deck-lab-evidence.js` fetches once on `deck-lab:ready`, then:
   - **Row badge.** In List rows, a small badge `[data-evidence-badge]` with text like `71% · 143 lists`. Its `title` is the full statement: `In 102 of 143 recorded <Commander> lists, last 30 days, events with ≥ 16 players`.
   - **Panel.** In the T08 card panel evidence slot, a section `Why this card?` with:
     - The inclusion statement (full sentence, as above), or the explicit absence message.
     - The role.
     - For candidate decks: `Added by the generator as <source> (priority <p>)`, plus the reason when present.
   - **Window control.** A window selector (30/60/90/180/All) in `railSection("evidence", "Tournament evidence")`. It refetches, and also shows `Based on D recorded lists`.
   - Re-render badges on every `deck-lab:render` without refetching, unless the set of oracle ids changed. Then refetch, debounced 500 ms.

## Acceptance criteria → required tests (`tests/test_deck_evidence.py`)
- AC-1 `test_for_deck_counts_lists_with_window_and_event_floor` (seeded research rows inside and outside the window and above and below the floor)
- AC-2 `test_partner_commanders_require_both_in_list`
- AC-3 `test_unknown_commander_reports_unavailable_not_zero`
- AC-4 `test_cards_with_zero_lists_omitted`
- AC-5 `test_candidate_deck_includes_explanations_from_stored_candidate`
- AC-6 `test_non_candidate_deck_has_no_explanations`
- AC-7 `test_route_owner_only_and_default_window`
- AC-8 `test_single_aggregate_query_for_deck` (query count is bounded regardless of deck size)
- AC-9 `test_badge_text_and_full_statement_title` (harness)
- AC-10 `test_panel_slot_shows_absence_message_when_unavailable` (harness)
- AC-11 `test_window_selector_refetches_and_shows_denominator` (harness)
- AC-12 `test_rate_never_rendered_without_denominator` (harness: scan all rendered evidence text; every `%` occurrence is accompanied by a denominator in the same element or its title)
