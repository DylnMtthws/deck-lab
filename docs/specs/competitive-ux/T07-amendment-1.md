# T07 — Amendment 1 (from the orchestrator, after review of 32d0bba)

Accepted: the routes, owner checks, card_key validation, the repo, the JS, and the admin counts.

## Fix 1: SPEC ERROR (the orchestrator's): feedback must survive deck deletion
The spec told you to declare `deck_id ... REFERENCES deck_documents(id) ON DELETE CASCADE`. That was wrong.
- `DeckDocumentRepo._connect` sets `PRAGMA foreign_keys=ON`, so deleting a deck would delete all of its testers' feedback.
- CLAUDE.md records that feedback rows are deliberately KEPT on deck delete, because they are research data that admin analytics aggregate by card name.
- A plain `REFERENCES` without a cascade would be worse: deck deletion would fail with an FK error.

Change both new tables so `deck_id` is `TEXT NOT NULL` with NO foreign-key clause. Keep the indexes and the `user_id` reference. These tables have never shipped, so edit the `CREATE TABLE` statements directly; no migration is needed.

## Fix 2: revert your edit to `tests/test_builder_selection_and_preview.py`
- Restore that file exactly to BASE (`git checkout 8ef2751 -- tests/test_builder_selection_and_preview.py`).
- The integration branch keeps the card-image dialog AFTER the last `{% endif %}` (at the end of the scripts block), so the original test holds without changes.
- In YOUR branch, satisfy the original test by placing the card-image `<dialog>` at the END of `{% block scripts %}`, after your `{% if not shared %}...{% endif %}` script tag. Your rewritten parser could be fooled by nested non-`shared` ifs.

## Additional acceptance criterion (add to `tests/test_builder_feedback.py`)
- AC-15 `test_feedback_survives_deck_deletion`: create a deck, save one card vote and one verdict, then delete the deck through `DeckDocumentRepo.delete` (FKs ON). The deletion succeeds, and both feedback rows still exist with `card_name` intact.

## Completion
COMMON.md definition of done; FULL suite; `git commit --amend`; include this file; update the report.
