# T03 — Amendment 1 (from the orchestrator, after review)

Review result for commit 586e29d: **ONE FIX REQUIRED.** Everything else is accepted.
- The parser, the merge rule, the notices and the color mapping table are good.
- **Waiver:** grouping each AC into a `Test<AcName>` class instead of one `test_<ac_name>` function is accepted, because the mapping is one-to-one. Keep it.

## Fix
`_load_index_state` parses `q` for EVERY tab, and rewrites `query` plus `card_filters` (including the mana bounds that the Commanders tab also reads). The spec scoped syntax to the **Cards tab only**.
- Parse and merge only when `tab == "cards"`.
- On the commanders, metagame and decks tabs, `q` must behave exactly as it does at BASE.

## Additional acceptance criterion (add to `tests/test_research_query_syntax.py`)
- AC-14 `test_syntax_not_applied_on_other_tabs`: for `tab=commanders` and `tab=metagame` with `q=mv<=2`, the rendered results equal the BASE behaviour for that q. Compare against a run with `parse_query` monkeypatched to raise; it must never be called on those tabs. No `data-query-applied` or `data-query-unsupported` notice appears.

## Completion
COMMON.md definition of done. The base has ZERO failing tests; run the FULL suite. `git commit --amend` into the single commit, including this file, and update the report.
