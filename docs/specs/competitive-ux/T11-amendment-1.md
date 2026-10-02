# T11 — Amendment 1 (from the orchestrator, after review)

Review result for commit 48ec380: **ONE BUG.** Everything else is accepted.

**Bug: entries whose role is not a key in `roleOptions` vanish from the decklist** in role grouping. They go into `roles[role]`, but only known keys are emitted. This happens in real data: `import_generated` stores the legacy generator's unvalidated `slot_role` (`deck_documents.py`, the `role=item.get("slot_role")` line), so imported decks carry roles such as `card_draw` or `finisher`.

## Fix
Every library-zone entry must appear in exactly one group. Entries with an unknown role go into the `Other` group (`role-other`, label from `roleOptions`), created if needed, in `Other`'s normal position. Do not change the stored role.

## Additional acceptance criteria (add to `tests/test_group_by_role.py`)
- AC-8 `test_unknown_roles_grouped_under_other`: an entry with role `card_draw` and one with `finisher` appear in the `Other` group.
- AC-9 `test_role_grouping_shows_every_entry_exactly_once`: on a fixture with known roles, unknown roles, no role, a private zone and a commander, the multiset of rendered `[data-entry-id]` equals all entries.

## Completion
COMMON.md definition of done. The base has ZERO failing tests; run the FULL suite. `git commit --amend` into the single commit, including this file, and update the report.
