# T11 — Amendment 2 (from the orchestrator, after review of fb9e99a)

**One remaining bug.** When a deck has entries with the real role `other` AND entries with unknown roles, `groups()` emits TWO groups with id `role-other`:
- one at Other's normal position,
- one appended later with the unknown-role entries.

The result is two "Other" headings, and collapse state is keyed by group id, so the two collide.

## Fix
Merge unknown-role entries into `roles["other"]` BEFORE emitting groups, so exactly one `role-other` group exists, at Other's normal position in `roleOptions` order.

## Additional acceptance criterion
- AC-10 `test_single_other_group_when_other_and_unknown_roles_coexist`: a fixture with one `other` entry and one `card_draw` entry renders exactly one group with id `role-other`, containing both, at the position of `other` in `roleOptions` order.

## Completion
COMMON.md definition of done; FULL suite; `git commit --amend`; include this file; update the report.
