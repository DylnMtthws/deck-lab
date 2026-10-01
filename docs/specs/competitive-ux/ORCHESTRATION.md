# Competitive UX program — orchestration ledger

Owner of this file: the orchestrator (Claude). Task agents must not edit it.

- Integration branch: `feat/competitive-ux` (worktree `worktrees/competitive-ux`), based on
  `origin/main` @ 43c59cc. Local only — nothing is pushed or merged to main without the user.
- Baseline at 43c59cc: Gate 1 = 1617 passed / 31 skipped / 0 failed; Gate 2 clean; Gate 3 clean.
- Agents:
  - LOW = `opencode run -m huggingface/deepseek-ai/DeepSeek-V4-Flash` (mechanical, fully specified work)
  - HIGH = `cursor-agent` with Grok 4.7 (design judgment, cross-cutting state, data plumbing)
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
| 1 | Hover/focus card panel | T08 | HIGH | TODO |
| 2 | Pinned deck status bar | T05 | LOW | TODO |
| 3 | Keyboard editing + undo/redo | T09 | HIGH | TODO |
| 4 | "Considering" zone | T04 | LOW | PARTIAL PRE-EXISTING: private zone names excluded from count/copy (`deck_documents.py:64-71`, `deck-lab-export.js` PRIVATE_ZONE_NAMES) — T04 adds the workflow |
| 5 | Interactive stats (curve filter, pips vs sources, odds, sample hand) | T06 | LOW | TODO |
| 6 | Paste-ready export for Moxfield/Archidekt | T01 | LOW | PARTIAL PRE-EXISTING: "Copy list" (b46f209) — T01 adds formats + download |
| 7 | Real mana symbols | T02 | LOW | TODO |
| 8 | Generation in the new-deck flow + real progress | T10 | HIGH | TODO |
| 9 | Evidence badges + "Why this card?" | T12 | HIGH | TODO |
| 10 | Compare to the meta | T13 | HIGH | TODO |
| 11 | "Replace with…" same-role alternatives | T13 | HIGH | TODO |
| 12 | Re-simulate after edits, show delta / "not simulated" | T14 | HIGH | TODO |
| 13 | Per-card + deck feedback in the builder | T07 | LOW | TODO |
| 14 | Group by role + remembered view/group/sort | T11 | LOW | TODO |
| 15 | Scryfall syntax in Research search | T03 | LOW | TODO |
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
