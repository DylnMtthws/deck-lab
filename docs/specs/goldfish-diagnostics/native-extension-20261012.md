# Native diagnostic extension — 2026-10-12

This is a QA-only foundation for the five-test workflow inside **Build → Goldfish simulator**. It extends the existing native C++ game loop and HTTP service. It does not use the retired Python simulator.

## What executes

The opt-in QA request uses `goldfish_diagnostics.v1`, 2,000 games per scenario, seed 20261012 and the fixed turn 5 / wipe turn 5 / finish turn 7 profile. The existing assembly response is retained separately. Production defaults remain the legacy request unless explicitly configured.

- Opening: bounded London mulligans, one free mulligan, a visible-hand-only generic keep policy (2–4 lands and an affordable authored action). Bottomed cards remain real library cards. This policy is not an archetype-trained pilot.
- Development: continue past assembly to observe missed land drops, untapped modeled source capacity, hand size and first authored action. Archetype engine activation remains unknown.
- Commander loss: remove the first cast commander, clean up permanent state, apply command-zone recast tax, observe the next available turn. Recasting the commander and playing a land do not count as independent recovery.
- Creature wipe: controlled creature removal after turn five, followed by one recovery turn. Empty wipes are not exercised disruptions. Indestructible, death triggers, replacements and protection are not comprehensively modeled, so this is a partial stress test.
- Finish: checkpoint at turn seven. Verified table-wide victory is **unmeasured**, not zero and not inferred from assembly.

Every result retains settings identity, coverage, unknown metrics, denominators, intervals where applicable, and bounded sample traces. Unsupported card names are visible in expandable evidence. Colors for Command Tower/Arcane Signet follow the actual commander; repeated basic lands receive effects per physical card slot.

## Comparison and user constraints

The existing queue freezes each submitted deck and provenance. At most three replacements are allowed from the prior successful frozen run. Reports show metric differences against up to five prior runs from that deck and owner. Settings/engine changes or unmeasured values suppress affected comparisons. Shared seeds provide reproducibility; descriptive differences are not statistical proof of improvement. Catalog changes are not isolated. History truncation is explicit.

Sideboards are excluded from the 100-card simulation; the four supplied fixtures remain intact. Prices and theme constraints are not inferred from “affordable.”

## Handoffs and evidence

| Role/model actually used | Bounded task and KPI | Evidence / unmet reason |
| --- | --- | --- |
| OpenCode Spark 1.3 free | Audit four fixture counts, commanders, sideboards and checksums | 3 unit tests pass; all four main decks are 100 cards; affordable Sauron sideboard is 35 cards. |
| Codex Sol 6.1 | Extend native core; visible-hand decisions, disruption state, tax, determinism | 12 diagnostic tests, 105 assertions passed. No verified finish authoring claimed. |
| Claude Code Sonnet 5 | Default-off service guardrails and additive contracts | Implemented scenario validation, bounded games, flag, health/capabilities and tests. Session was stopped after ~16 minutes without a final report; coordinator reconciled real schemas, added missing-output rejection, and ran validation. |
| Codex Luna 6 | Build panels plus opt-in HTTP adapter; unknown stays unmeasured | UI harness and 14 dedicated HTTP/profile tests pass; 37 existing HTTP/schema tests pass. Real native output validates against vendored schemas. |
| Codex Sol 6.1 | Immutable native comparison history and strict compatibility | 16 focused tests; 23 combined comparison/snapshot/max-three regression tests pass. |
| Claude Code Fable 5.1 | Skeptical review with evidence-backed acceptance criteria | Identified low effect coverage, disruption cleanup, assembly/finish confusion and pilot limits. Supplied no production authorization; unresolved mechanics remain explicit unknowns. |
| Claude Code Opus 5.5 | Senior integration challenge | Found schema mismatches, recast/land recovery inflation and empty/censored disruption risks; fixes validated in native and real-process tests. |

Full app validation: 2,073 passed, 31 skipped. Native final full suite: 145 cases / 89,153 assertions passed, including the duplicate-card loader regressions. Service boundary suite: 63 passed; real-process integration and legacy digest: 3 passed. Final build/release receipts add subsequent loader validation and QA acceptance.

## Follow-up work

The subsequent implementation and current limitations are tracked in [the QA review guide](qa-review-20261012.md). The list below describes the original foundation milestone.

### Original remaining product work

1. Author and verify actual Vivi/Sauron mechanics and finish resolution, including opponent-dependent limitations. Current coverage is insufficient for whole-deck resilience or win-conversion verdicts.
2. Add archetype engine predicates, richer mulligan pilots, and meaningful baselines; validate against manually traced games. Preserve interaction and theme value that solo play cannot measure.
3. Connect Build Research Assistant and generator to these structured findings. Existing deck evidence/alternatives endpoints are useful inputs, but no new chatbot or automatic swap optimizer is shipped here. QA does not call external AI under its environment policy.
4. Add held-out-seed validation before ranking proposed swaps as improvements. Maintain the three-replacement cap and immutable experiment history.

Primer references: [Sauron](https://moxfield.com/decks/WDJsYexc9EaHELI7vXeHjQ/primer), [Vivi](https://moxfield.com/decks/nBKRWuYyckqPxpCmWBjIBg/primer). Live pages returned unreadable shells in this session; supplied lists and previously captured primer notes were used, without claiming fresh primer verification.
