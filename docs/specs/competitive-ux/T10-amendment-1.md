# T10 — Amendment 1 (from the orchestrator, after review)

Review result for commit 3ffc16f: **TWO SMALL FIXES.** Everything else is accepted. The `cards`/`commander.oracle_ids` fix to `import_candidate` is a good catch: imports on main were producing decks without their 99.

1. Remove `_recorded_steps` and the `steps` addition in `_job_payload`.
   - `build_jobs` has no `steps` or `status_history` column (your own docstring says so).
   - The spec said not to invent it, and code reading columns that don't exist is dead code.
   - `_job_payload` must be byte-identical to BASE.
2. Add the acceptance criterion that was added to the spec after you launched:
   - AC-13 `test_generate_disabled_with_message_when_no_pack_matches` (harness): with the packs endpoint returning `{packs: [], message: ...}`, the pack select and the Generate button are disabled, and the message text is visible. With one supported pack, both are enabled.

## Completion
COMMON.md definition of done. The base has ZERO failing tests; run the FULL suite. `git commit --amend` into the single commit, including this file, and update `reports/T10.md`.
