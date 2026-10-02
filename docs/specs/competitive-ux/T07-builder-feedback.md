# T07 — Per-card and deck feedback inside the builder

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00 (`window.DeckLabBuilder`; contract in `T00-builder-extension-api.md`).

## Why
The Phase 1 product goal (ADR-018) is per-card 👍/👎 plus comments, and a per-deck verdict. Today
that UI exists only on the legacy `/deck/<id>` page. The `card_feedback` and `deck_feedback` tables
have foreign keys to `generated_decks`, so they cannot hold feedback on the Deck Lab builder's
`deck_documents`. Testers in the new builder have no way to rate cards.

## Allowed files
- `scripts/setup_db.py`: add two tables and their indexes, using the same idempotent `CREATE TABLE IF NOT EXISTS` style.
- `src/sabermetrics/db.py`: add a new `DeckDocumentFeedbackRepo` class next to `FeedbackRepo`, and extend ONLY the admin count dictionary that includes `"card_feedback"`/`"deck_feedback"` counts (near `db.py:1010`) with two new keys.
- `src/sabermetrics/ui/builder_routes.py`: three new routes, listed below.
- `src/sabermetrics/ui/static/deck-lab-feedback.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: only the script tag, after `deck-lab-builder.js`, inside `{% if not shared %}`.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_builder_feedback.py` (new), `tests/builder_feedback_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T07.md` (new)

## Requirements
1. Tables:
   ```sql
   CREATE TABLE IF NOT EXISTS deck_document_card_feedback (
     id TEXT PRIMARY KEY,
     user_id TEXT NOT NULL REFERENCES users(id),
     deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
     card_key TEXT NOT NULL,          -- oracle_id, or "card:<card_id>" when the entry has no oracle_id
     card_name TEXT NOT NULL,
     vote TEXT CHECK (vote IN ('up','down') OR vote IS NULL),
     comment TEXT,
     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     UNIQUE (user_id, deck_id, card_key));
   CREATE TABLE IF NOT EXISTS deck_document_feedback (
     id TEXT PRIMARY KEY,
     user_id TEXT NOT NULL REFERENCES users(id),
     deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
     verdict TEXT CHECK (verdict IN ('good','mixed','bad') OR verdict IS NULL),
     comment TEXT,
     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     UNIQUE (user_id, deck_id));
   ```
   Add indexes on `(deck_id)` for both tables and `(card_name)` for the card table. Find where `setup_db.py` keeps its statement list and add these there, so new and existing databases both get them. Check how the tables near `deck_documents` are created.
2. `DeckDocumentFeedbackRepo(db_path)` methods:
   - `get(user_id, deck_id) -> {"cards": {card_key: {"vote", "comment"}}, "deck": {"verdict", "comment"} | None}`
   - `upsert_card(user_id, deck_id, card_key, card_name, vote, comment)`: normalize empty strings to `None`. When both vote and comment are `None`, DELETE the row instead.
   - `upsert_deck(user_id, deck_id, verdict, comment)`: same normalization and delete rule.
   - Comments are stripped and truncated to 2000 characters. Invalid vote or verdict values raise `ValueError`.
3. Routes. All are owner-only: use the same ownership check as the other `/api/decks/<deck_id>/...` routes, which return 404 for non-owners. They are JSON and CSRF-protected like the existing command route.
   - `GET /api/decks/<deck_id>/feedback` → the `get` payload.
   - `PUT /api/decks/<deck_id>/feedback/cards/<card_key>` with body `{"card_name", "vote", "comment"}`. The route rejects with 400 a `card_key` that does not match an entry in the deck: compare against the entries' `oracle_id`, or `card:<card_id>`. Returns the updated `get` payload.
   - `PUT /api/decks/<deck_id>/feedback/deck` with body `{"verdict", "comment"}`. Returns the updated `get` payload. Invalid values → 400.
4. `deck-lab-feedback.js`. Not in shared mode. It loads feedback once at `deck-lab:ready`, then:
   - On every render, for each List-display row with `data-entry-id`, it adds a small group `[data-card-feedback]` with three controls:
     - `<button data-vote="up" aria-pressed>` labeled `Good pick` (visual 👍)
     - `<button data-vote="down" aria-pressed>` labeled `Bad pick` (visual 👎)
     - `<button data-card-comment aria-label="Comment on <name>">` (visual 💬; it shows a dot when a comment exists)
   - Clicking the active vote again clears it.
   - The comment button opens one shared `<dialog data-card-comment-dialog>` with a textarea and Save/Cancel.
   - Saves are PUT requests. The UI updates optimistically and reverts on error, and the save error appears in `[data-feedback-status]` (`role="status"`).
   - Grid and Spoiler displays are NOT required.
5. Deck verdict, in `DeckLabBuilder.railSection("feedback", "Your verdict")`:
   - Three toggle buttons `[data-verdict="good|mixed|bad"]` labeled Good, Mixed and Bad.
   - A textarea `[data-verdict-comment]` saved on blur or after a 1500 ms idle (debounced).
   - A muted line `Your feedback is private to you and the Deck Lab owner.`

## Acceptance criteria → required tests (`tests/test_builder_feedback.py`)
- AC-1 `test_tables_created_on_fresh_and_existing_db` (run `setup_database` twice; both tables exist)
- AC-2 `test_repo_upsert_get_and_delete_when_empty`
- AC-3 `test_repo_rejects_invalid_vote_and_verdict_and_truncates_comment`
- AC-4 `test_routes_owner_only_404_for_other_user`
- AC-5 `test_put_card_rejects_key_not_in_deck`
- AC-6 `test_put_card_and_deck_roundtrip_via_routes`
- AC-7 `test_routes_require_csrf_when_enabled`. Mirror how existing tests check CSRF on `/commands`; if no such test exists, assert that a POST/PUT without a token is rejected with CSRF enabled.
- AC-8 `test_admin_counts_include_builder_feedback`
- AC-9 `test_rows_get_vote_controls_and_toggle_clears` (Node harness)
- AC-10 `test_vote_reverts_on_failed_save_and_shows_status` (Node harness)
- AC-11 `test_verdict_section_saves_and_debounces_comment` (Node harness, fake timers)
- AC-12 `test_script_not_loaded_on_shared_page` (Flask test client)
- AC-13 `test_comment_dialog_saves_comment_and_marks_button` (harness: open the dialog from a row, type, Save → PUT with the comment; the row's comment button shows the has-comment marker; Cancel sends nothing)
- AC-14 `test_feedback_controls_have_accessible_names`
