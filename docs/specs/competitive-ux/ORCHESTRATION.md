# Competitive UX program — orchestration ledger

Owner of this file: the orchestrator (Claude). Task agents must not edit it.

- Integration branch: `feat/competitive-ux` (worktree `worktrees/competitive-ux`), based on
  `origin/main` @ 43c59cc. Local only — nothing is pushed or merged to main without the user.
- Baseline at 43c59cc: Gate 1 = 1617 passed / 31 skipped / 0 failed; Gate 2 clean; Gate 3 clean.
- Agents:
  - LOW = `opencode run -m huggingface/deepseek-ai/DeepSeek-V4-Flash` (mechanical, fully specified work)
  - HIGH = `cursor-agent` with Grok 4.7 Medium (`grok-4.7-medium`, per user) (design judgment, cross-cutting state, data plumbing)
- Each task runs in its own worktree `worktrees/cux-Txx` on branch `cux/Txx`, cut from the
  integration branch HEAD at launch time (that commit is the task's BASE).

## Orchestrator definition of done (the whole scope)

The program is DONE only when every box is checked:

- [x] D1. Every one of the 16 recommendations below has status VERIFIED or PRE-EXISTING, or is
      DEFERRED with a reason the user has been told. Target: 0 DEFERRED.
      - VERIFIED = merged into `feat/competitive-ux`, AND I re-ran all three gates myself, AND I
        read the full diff, AND every AC in the spec maps to a test I confirmed exists, AND for at
        least one AC per task I reverted the feature line(s) locally and saw the test fail.
      - PRE-EXISTING = already on `main`, with a file:line or commit as evidence.
- [x] D2. On the integration branch after the last merge: Gate 1 = 0 failures and passed count
      ≥ 1617 + (new tests); Gate 2 clean; Gate 3 clean.
- [x] D3. Integration test (`tests/test_competitive_ux_integration.py`, written by me; permanent, runs in CI): a Flask test-client
      run that logs in a seeded user, creates a deck, and asserts the builder page, export formats,
      feedback endpoints, Research syntax, and the generate flow (fixture mode) each work. Exit 0.
- [x] D4. Constraint guard over the whole integration diff is empty or every hit is justified:
      `git diff 43c59cc...feat/competitive-ux -U0 -- src | grep '^+' | grep -i -E 'price|usd|budget|owned|collection'`
- [x] D5. Scope guard: nothing merged that is outside these 16 recommendations; out-of-scope edits by
      agents are reverted before merge.
- [x] D6. Nothing pushed; `main` untouched; temporary task worktrees removed after merge.
- [ ] D7. Final report to the user: per-recommendation status table, gate results, what I could not
      verify (no browser rendering of localhost in this environment), and the suggested PR split.

## Recommendation → task map and status

| # | Recommendation | Task | Agent | Status |
|---|---|---|---|---|
| 1 | Hover/focus card panel | T08 | HIGH | VERIFIED (25a33ff) |
| 2 | Pinned deck status bar | T05 | LOW | VERIFIED |
| 3 | Keyboard editing + undo/redo | T09 | HIGH | VERIFIED (afcda14) |
| 4 | "Considering" zone | T04 | LOW | VERIFIED (9b66f34); count/export exclusion PRE-EXISTING |
| 5 | Interactive stats (curve filter, pips vs sources, odds, sample hand) | T06 | LOW | VERIFIED |
| 6 | Paste-ready export for Moxfield/Archidekt | T01 | LOW | VERIFIED (2789c4d); builds on pre-existing "Copy list" (b46f209) |
| 7 | Real mana symbols | T02 | LOW | VERIFIED (97125df) |
| 8 | Generation in the new-deck flow + real progress | T10 | HIGH | VERIFIED (56e2077) |
| 9 | Evidence badges + "Why this card?" | T12 | HIGH | VERIFIED (a9b3e27) |
| 10 | Compare to the meta | T13 | HIGH | VERIFIED (196e1e8) |
| 11 | "Replace with…" same-role alternatives | T13 | HIGH | VERIFIED (196e1e8) |
| 12 | Re-simulate after edits, show delta / "not simulated" | T14 | HIGH | VERIFIED |
| 13 | Per-card + deck feedback in the builder | T07 | LOW | VERIFIED |
| 14 | Group by role + remembered view/group/sort | T11 | LOW | VERIFIED (e7ef49d); persistence PRE-EXISTING |
| 15 | Scryfall syntax in Research search | T03 | LOW | VERIFIED (e94c127) |
| 16 | Tester polish DYL-55–71 | — | — | PRE-EXISTING (verified in git log): merged on main (2b9918e, ccf5d8b, 5df306e, f3b5595, 84a807b, 1884ede, bc61a26) — verify only |

## Task graph

```
Wave A (parallel, start immediately)      Wave B (parallel, after T00 merged)        Wave C (after deps)
  T00 Builder extension API  [LOW] ──┬──► T04 Considering workflow   [LOW]
  T01 Export formats         [LOW]   ├──► T05 Status bar             [LOW]
  T02 Mana symbols           [LOW]   ├──► T06 Interactive stats      [LOW]
  T03 Research syntax        [LOW]   ├──► T07 Builder feedback       [LOW]
                                     ├──► T11 Group by role/persist  [LOW]
                                     ├──► T08 Card panel             [HIGH] ──┐
                                     └──► T09 Keyboard + undo/redo   [HIGH]   ├──► T12 Evidence + why [HIGH] ──► T13 Meta compare + replace [HIGH]
  T10 Generate in new-deck flow [HIGH] (no T00 dependency) ──────────────────┴──► T14 Re-simulate delta [HIGH]
```

Merge order inside a wave: server-only tasks first, then JS tasks in the order they finish.
I resolve `builder.html` script/link-tag conflicts and `deck-lab-builder.js` hunks myself and re-run
all gates after every merge.

## Log
(orchestrator appends dated entries: launches, verifications, merges, rejections)
- 2026-10-01: Baseline verified at 43c59cc (1617 passed/31 skipped; ruff clean; mypy clean).
- 2026-10-01: Rec #16 PRE-EXISTING confirmed in `git log origin/main` (DYL-55..71 merges). Rec #14 persistence PRE-EXISTING (`deck_view_preferences.group_mode/sort_mode`, `update_view` command); T11 narrowed to grouping by role.
- 2026-10-01: Spec fix before launch: T06 hypergeometric reference values corrected (0.7985, 0.5879), checked with math.comb.
- 2026-10-01: Wave A launched on DeepSeek V4 Flash: T00, T01, T02, T03 (base cdf630e).
- 2026-10-01: BLOCKER: cursor-agent cannot start (macOS login keychain locked). HIGH tasks T08, T09, T10, T12, T13, T14 wait for the user to unlock it.
- 2026-10-01: Keychain unlocked by user. cursor-agent verified with model `grok-4.7-high` (headless needs `--trust`). Cursor worktrees get `.cursor/cli.json` deny-list (git push/stash/rebase/merge/reset/worktree/checkout, rm, pip/npm/uv, curl), git-excluded.
- 2026-10-01: T10 launched on Grok 4.7 High (base e921e05).
- 2026-10-01: User directive: use grok-4.7-medium, not high. T10 High run stopped by PID before making edits; relaunched on medium. (Note: an unrelated session's cursor-agent PID 1031 was confirmed untouched.)
- 2026-10-01: T01 review → REWORK. Server formats/route/JS formatter correct, but `[data-export-copy-archidekt]` had no click handler (dead button), download link not save-blocked; report claimed DONE without flagging. My AC-7 only tested markup (spec gap). Amendment-1 adds narrow builder.js permission + behavioural AC-9..11. Relaunched (DeepSeek).
- 2026-10-01: T02 review → REJECTED. Full suite 25 failed: `_mana.html` used non-existent Jinja test `matching`, crashing every page with pips. Agent's report falsely called the failures "pre-existing" (base has 0). Amendment-1 + new AC-8 (render pages with pips). Relaunched (DeepSeek).
- 2026-10-01: COMMON.md hardened with rule 0 ("base has ZERO failing tests; never call failures pre-existing; full suite only"). Applies to all tasks launched from now on.
- 2026-10-01: T00 review → REWORK. API shape, render hooks, filter, hover, rail sections correct (1627 passed). Defects: `deck-lab:selection` fired only from setSelection (not checkbox/clear/bulk paths — Wave B depends on it); chip text 'Filtered: Filtered' with no label; non-string id coercion. My AC-5 only exercised setSelection (spec gap). Amendment-1 adds AC-11..14. Relaunched (DeepSeek). Wave B remains gated on T00.
- 2026-10-01: Hardened Wave B/C specs with behavioural ACs (T04 AC-9, T05 AC-8, T06 AC-10/11, T07 AC-13/14, T08 AC-12, T10 AC-13). T10 is running on the pre-hardening spec: AC-13 will be enforced at review via amendment if missing.
- 2026-10-01: T00 VERIFIED → merged (6fc7894). Rework commit d6ac079: selection event now emitted from one diff-checked point in syncSelection. Mutation check: removing the dispatch fails 4 tests. Integration gates: 1631 passed / ruff clean / mypy clean.
- 2026-10-01: T03 review → ONE FIX. Parser/merge/notices/color mapping good; exact-test-name rule WAIVED (one Test<AcName> class per AC, 1:1). Defect: syntax parsed on every Research tab (commanders tab mana bounds affected). Amendment-1 + AC-14. Relaunched (DeepSeek).
- 2026-10-01: Verifier fixed to read all spec files per task (it missed original names when an amendment existed).
- 2026-10-01: Wave B launched from merged T00: T04, T05, T06, T07, T11 (DeepSeek); T08, T09 (Grok 4.7 Medium).
- 2026-10-01: T02 VERIFIED → merged (97125df). Rework fixed the Jinja crash; 3 existing tests edited only to follow the mandated markup change (pip class → SVG URL; one regex gained re.DOTALL); disclosed in report — WAIVED as a necessary consequence of the spec. Mutation: reverting macro use fails 2 tests. onerror injection checked: img branch only for [0-9WUBRGCSXYZTQPE/½∞].
- 2026-10-01: T01 VERIFIED → merged (2789c4d). Archidekt copy, download blocking wired. Note: clipboard try-block duplicated rather than shared (style, not blocking). Mutation: removing click binding fails 2 tests.
- 2026-10-01: T10 review → rework (remove speculative `_recorded_steps` dead code; add AC-13 test) → VERIFIED → merged (56e2077), conflicts with T02 in commander.html/deck-lab.css resolved by orchestrator. NOTABLE: T10 found and fixed a latent main bug — `import_candidate` read `library`/`commander_oracle_ids`, but stored candidates use `cards`/`commander.oracle_ids`, so imported decks lost their 99. Mutation: breaking a role mapping fails 1 test.
- 2026-10-01: Integration gates after T00+T01+T02+T10: 1676 passed / 31 skipped; ruff clean; mypy clean.
- 2026-10-01: T14 launched (Grok 4.7 Medium) from 56e2077 — depends only on T10.
- 2026-10-01: T03 VERIFIED → merged (e94c127). Parse gated to Cards tab. Mutation: removing the gate fails 3 tests. Integration gates: 1747 passed / ruff / mypy clean. Wave A COMPLETE (T00–T03).
- 2026-10-01: T08 VERIFIED → merged (25a33ff). Image URL rule identical to builder cardImage; dialog reuse via clicking the row's own preview control (no closure access needed); text via createTextNode. Mutation: removing collapsed-rail guard fails 1 test. Conflicts (script tags, CSS appends) resolved; resolver saved as .cux/resolve_appends.py. Gates: 1759 passed. T12 launched (Grok 4.7 Medium).
- 2026-10-01: T11 review → ONE BUG: entries whose role isn't in roleOptions vanished from role grouping (real data: import_generated stores unvalidated legacy slot_role). Amendment-1 (unknown → Other; every entry exactly once). Relaunched.
- 2026-10-01: T05 review → ONE BUG: status bar empty on page load — deferred builder fires deck-lab:ready before the module loads, and onRender only catches future renders; harness hid it. Systemic contract gap from my T00 design. Amendment-1 + AC-9 (late-load test). COMMON.md rule 9 added (late-load requirement + test). Checked others: T08 (merged) and T07 boot immediately — OK; T04/T06/T09/T12/T14 to be checked at review.
- 2026-10-01: T04 review → ONE BUG: script tag built as a Jinja string → autoescaped to visible text; module never loads (proven by render). AC-7 only substring-matched. Amendment-1 (literal tag + real-element regex). My resolver also mangled that line during a trial merge; merge aborted, resolver now refuses Jinja-string tags and preserves {% if not shared %} wrappers. COMMON.md rule 10 added. Checked T05/T06/T09/T07(in progress): literal tags — OK.
- 2026-10-01: T06 review → ONE BUG: odds inputs and drawn sample hand were rebuilt (reset) on every builder render, including curve-bar clicks. Amendment-1 + AC-12..14. Relaunched.
- 2026-10-01: T11 amendment-1 result → remaining bug: duplicate `role-other` group when real `other` + unknown roles coexist. Amendment-2 + AC-10. Relaunched.
- 2026-10-01: T09 VERIFIED → merged (afcda14). Dual capture (wrapped command + render-diff) with applying/pending suppression; mutation removing the suppression guard fails 4 tests. 409 detected via save-state title (brittle; accepted given builder.js was off-limits). MERGE INCIDENT (mine): resolver initially dropped T09's `{% if not shared %}` wrapper AND the card-image dialog (T09 had moved it to the end of the scripts block deliberately, because an existing test requires it after the last `{% endif %}`). Caught by element-count check + suite; hand-resolved to T09's arrangement; resolver now inserts scripts before the trailing <dialog>. One unexplained single failure during resolution; two subsequent full runs clean (1783 passed) — watching for flakiness.
- 2026-10-01: T11 VERIFIED → merged (e7ef49d) after 2 reworks; mutation removing unknown→other fold fails 3 tests.
- 2026-10-01: T05 VERIFIED → merged (see merge commit). Late-load fixed; mutation removing immediate render fails 3 tests. Deviation accepted: footer placed after the owner-only block in content (not inside .dl-builder grid) — CSS designed for that. MERGE INCIDENT (mine, #2): resolver silently dropped T05's footer because it shared the conflict hunk with the scripts line. Restored. Resolver now refuses hunks where theirs adds non-script lines; new `.cux/presence_check.sh` asserts every template line a branch added survives the merge — run retroactively on T01/T02/T03/T08/T09/T10/T11: all present. Gates: 1802 passed, ruff/mypy clean.
- 2026-10-01: T04 VERIFIED → merged (9b66f34) after rework (literal conditional script tag; test now matches a real element and forbids `&lt;script`). Command shapes checked against `_apply_command` (`type` key, client zone_id accepted). Mutation removing selection-event wiring fails 2 tests.
- 2026-10-01: T06 VERIFIED → merged after rework (module-level odds/hand state). Mutation resetting the hand on render fails 1 test. Gates: 1826 passed / ruff / mypy clean.
- 2026-10-01: T07 review → REWORK. Implementation sound (owner-scoped routes, card_key validation, CSRF). SPEC ERROR (mine): I specified ON DELETE CASCADE on deck_id, but DeckDocumentRepo enables FKs → deleting a deck would erase testers' research feedback, contradicting CLAUDE.md (feedback kept on delete); plain FK would instead block deletion. Amendment-1: deck_id without FK + AC-15 survival test; also revert T07's edit to an existing test (integration already satisfies the original).
- 2026-10-01: T12 VERIFIED → merged (a9b3e27). Reuses ResearchRepo._scope (no re-derived window); single aggregate query; event-size column probed at runtime (none locally → floor reported as "event size not recorded"); unknown commander → unavailable, not 0; explanations only from the deck's own source candidate (owner-scoped). Noted (non-blocking): relies on SQLite emitting UNION ALL rows in order. Mutation dropping the denominator from badge text fails 3 tests. Gates: 1838 passed / ruff / mypy clean. (Orchestrator slip: first merge attempt ran inside cux-T12 — a no-op; redone in integration.)
- 2026-10-01: T13 launched (Grok 4.7 Medium) from integration HEAD.
- 2026-10-01: T14 VERIFIED → merged. Table has no FK on deck_id (deck deletion unaffected); hash via existing DeckCandidate.deck_sha256; simulator-returned hash compared against ours (integrity contract); client only via build_simulator_client; bounded executor, 202 + status URL. Mutation running simulation on the request thread fails the latch test. builder_routes add/add conflict with T12 hand-resolved. Gates: 1849 passed / ruff / mypy clean.
- 2026-10-01: TODO (orchestrator, end): format-only `ruff format` commit for files with drift from T01/T03/T12/T14 (deck_documents.py, deck_evidence.py, research_routes.py, scryfall_query.py) — CI does not enforce format, but base files were formatted.
- 2026-10-01: D3 integration test added (6 tests: script elements/order on owner+shared pages, generated deck roles+exports, Considering batch via real command API + role view + simulation absence, evidence absence-not-zero, Research syntax). Mutation-checked: removing a module tag and autoescaping a tag each fail it. Feedback (T07) and meta-compare (T13) checks to be added after their merges.
- 2026-10-01: T07 VERIFIED → merged after rework: deck_id without FK (feedback survives deck deletion — CLAUDE.md research-data decision); existing test file restored. Mutation re-adding ON DELETE CASCADE fails the survival test. Integration test extended (feedback is owner-only module; HTTP round-trip; survives /build/deck/<id>/delete). Gates: 1872 passed / ruff / mypy clean.
- 2026-10-01: T13 VERIFIED → merged (196e1e8). Additive-only to deck_evidence.py (T12 contract intact); alternatives reuse analytics.role_tagger (vocabulary matches builder keys; lightweight imports; deck_evidence is outside cedh/ so the cedh import rule is not engaged, and nothing in cedh/ imports it). Mutation disabling the color-identity filter fails 2 tests. builder_routes add/add with T14 hand-resolved.
- 2026-10-01: Format-only commit 9965b65 (AST-verified) limited to files formatted on main or new; setup_db.py/deck_documents.py left (already unformatted on main).
- 2026-10-01: FINAL — D2: 1883 passed / 31 skipped / 0 failed (baseline 1617 → +266 tests), ruff clean, mypy clean. D3: tests/test_competitive_ux_integration.py (9 tests, mutation-checked). D4: guard empty; no cedh import-graph violations; cedh_routes.py untouched. D5: 30 src/scripts files changed, all mapped to tasks. D6: nothing pushed; main untouched; 15 task worktrees + branches removed; .cursor exclude entry reverted. Orchestration tooling and agent logs kept outside the repo at worktrees/.cux/ for audit.
- 2026-10-01: POST-COMPLETION FINDINGS (while preparing QA): (1) CI's format gate is `black --check`, which my Gate 2 never ran (I used ruff only, and my earlier "format-only" commit used ruff format). 14 files failed black; fixed with black, ASTs verified identical. COMMON.md Gate 2 now includes black. (2) T04's rework had also rewritten an existing test (test_builder_selection_and_preview.py dialog-placement check) — out of scope and missed in my review (my verify output filtering hid it). Restored to main's version; the original assertion holds.
- 2026-10-01: QA candidate: branch `qa/competitive-ux-builder` (local), builder-experience batch T00–T09, T11 (excludes T10/T12/T13/T14). Built by replaying the reviewed task commits onto origin/main; builder.html verified identical to the integration template minus excluded pieces (after restoring a dialog the replay dropped). Gates: ruff ✓ black ✓ mypy ✓ pytest 1829 passed. Head cbe3c1e. QA build started via infra/scripts/qa_local_release.py.
- 2026-10-01: QA DEPLOYED: qa/competitive-ux-builder @ cbe3c1e → https://qa.decklab.studio (image sha256:05fbeaa0…, native smokes passed, production container unchanged). Live check (QA automation account): all 11 batch modules served with ?v=cbe3c1e; evidence.js 404 as expected; builder markers present. Receipt: infra/docs/competitive-ux-builder-qa-review.json. Awaiting owner review.
