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

- [ ] D1. Every one of the 16 recommendations below has status VERIFIED or PRE-EXISTING, or is
      DEFERRED with a reason the user has been told. Target: 0 DEFERRED.
      - VERIFIED = merged into `feat/competitive-ux`, AND I re-ran all three gates myself, AND I
        read the full diff, AND every AC in the spec maps to a test I confirmed exists, AND for at
        least one AC per task I reverted the feature line(s) locally and saw the test fail.
      - PRE-EXISTING = already on `main`, with a file:line or commit as evidence.
- [ ] D2. On the integration branch after the last merge: Gate 1 = 0 failures and passed count
      ≥ 1617 + (new tests); Gate 2 clean; Gate 3 clean.
- [ ] D3. Integration smoke (`scripts/competitive_ux_smoke.py`, written by me): a Flask test-client
      run that logs in a seeded user, creates a deck, and asserts the builder page, export formats,
      feedback endpoints, Research syntax, and the generate flow (fixture mode) each work. Exit 0.
- [ ] D4. Constraint guard over the whole integration diff is empty or every hit is justified:
      `git diff 43c59cc...feat/competitive-ux -U0 -- src | grep '^+' | grep -i -E 'price|usd|budget|owned|collection'`
- [ ] D5. Scope guard: nothing merged that is outside these 16 recommendations; out-of-scope edits by
      agents are reverted before merge.
- [ ] D6. Nothing pushed; `main` untouched; temporary task worktrees removed after merge.
- [ ] D7. Final report to the user: per-recommendation status table, gate results, what I could not
      verify (no browser rendering of localhost in this environment), and the suggested PR split.

## Recommendation → task map and status

| # | Recommendation | Task | Agent | Status |
|---|---|---|---|---|
| 1 | Hover/focus card panel | T08 | HIGH | VERIFIED (25a33ff) |
| 2 | Pinned deck status bar | T05 | LOW | TODO |
| 3 | Keyboard editing + undo/redo | T09 | HIGH | TODO |
| 4 | "Considering" zone | T04 | LOW | PARTIAL PRE-EXISTING: private zone names excluded from count/copy (`deck_documents.py:64-71`, `deck-lab-export.js` PRIVATE_ZONE_NAMES) — T04 adds the workflow |
| 5 | Interactive stats (curve filter, pips vs sources, odds, sample hand) | T06 | LOW | TODO |
| 6 | Paste-ready export for Moxfield/Archidekt | T01 | LOW | VERIFIED (2789c4d); builds on pre-existing "Copy list" (b46f209) |
| 7 | Real mana symbols | T02 | LOW | VERIFIED (97125df) |
| 8 | Generation in the new-deck flow + real progress | T10 | HIGH | VERIFIED (56e2077) |
| 9 | Evidence badges + "Why this card?" | T12 | HIGH | TODO |
| 10 | Compare to the meta | T13 | HIGH | TODO |
| 11 | "Replace with…" same-role alternatives | T13 | HIGH | TODO |
| 12 | Re-simulate after edits, show delta / "not simulated" | T14 | HIGH | TODO |
| 13 | Per-card + deck feedback in the builder | T07 | LOW | TODO |
| 14 | Group by role + remembered view/group/sort | T11 | LOW | TODO |
| 15 | Scryfall syntax in Research search | T03 | LOW | VERIFIED (e94c127) |
| 16 | Tester polish DYL-55–71 | — | — | PRE-EXISTING: merged on main (2b9918e, ccf5d8b, 5df306e, f3b5595, 84a807b, 1884ede, bc61a26) — verify only |

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
