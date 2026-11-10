# Implementation adversarial review log

No deployment or paid benchmark is authorized by this log.

## Codex quality/speed checkpoint

Two material findings fixed with regressions: early multi-face card facts now
abstain instead of omitting a face; hand-plan failure/model-plan success is no
longer labeled a corpus gap. Admitted request bound aligned at 16000 cumulative
characters without clipping. Reviewer found no speed utility/cache blocker.

## Codex UX checkpoint

Fixed fresh-session (null run, generation 0) target validation, preserved same-ID
uncertain card steering/retry, made withdrawal status override source-rendered
labels, and hid timer/pulse on activity collapse. Action acknowledgments separate
pinning, restart and deliberate retry. Added behavioral Node-backed regressions.

## Backend checkpoint and coordinator counterexamples

Fixed real partial_finding/research_progress event routing, unknown-tool operation
identity, and journaled executor failure terminalization. Added real HTTP/store/
local authority tests for shared steering allowance, stale-stop fencing, current
run discovery from an old tab, and cancellation after a deck revision change.
No claim is made that static or deterministic tests establish live answer quality.

## Codex integrated review of d3c811b

Three concrete findings addressed with regressions: actual runtime terminal
statuses now map exhaustively, including provider_unavailable and budget_exhausted;
explicit historical run reads are honored while follow_current=1 serves live
polling; compaction counts persist transactionally and render as transport-history
disclosures without mutating terminal domain state. Historical findings reload
through bounded sequential authorized reads, not an in-memory-only cache.

The full offline suite passed: 5232 passed, 28 skipped, 12 deliberately excluded
live cases. The old completed-on-failure and auto-readmitted steering tests were
updated to the approved failure/retry semantics without weakening no-provider-
without-admission assertions. Follow-up testing caught ownership-loss worker
cleanup; it now retires only the original job without reading a new owner's
session. Executor startup rejection now closes only its own allowance, preserving
a concurrent shared-allowance replacement. The final focused suite passed 84
checks after those refinements. Full static checks and existing hermetic
regression scripts pass. Claude integrated review remains pending.

## Claude integrated review of d3c811b — follow-up

Codex began these fixes and ran out of usage partway through. Claude
Opus 5.5 verified the partial edits and finished the rest.

- P1 runtime failures labeled completed: already fixed in 3061fde.
- P2 open activities: a new stage closes the prior stage as completed, and the
  terminal transaction closes every open activity: completed for a stage at
  completed, failed at failed/interrupted, unavailable otherwise.
- P2 frozen elapsed time: a terminal summary keeps the frozen elapsed text
  visible while collapsed; only the live clock hides when collapsed.
- P2 evidence partial with zero claims: evidence_status is now unavailable until
  claim/citation mappings are published.
- P2 unassessed obligations: legacy complete with obligations but no coverage
  receipts maps to fulfillment unknown with a stated reason (QUALITY item 6).
  The UI shows "Answer ready · fulfillment not assessed" and uses
  "incomplete" only for partial.
- P3 rollback detection: explicit historical reads rebaseline when the job
  state disagrees with the projection lifecycle or the domain hash changed.
  Archive writes now update the domain hash.
- P3 settings binding: closes the binding captured with the mutation result.
- Steering against a terminal origin is rejected before any journal write.
  Superseded runs withdraw published findings and claims.
- Telemetry: provider usage receipts are deduplicated into the trace; the task
  class is set from the exact source-rendered final receipt; a POST /latency
  route stores one untrusted client render observation per run and kind
  without changing lifecycle or sequence.
- Journal cost: running per-run/per-deck totals maintained by triggers replace
  the deck-wide COUNT/SUM per append. Cleanup is indexed and capped at 64 runs
  per call. Synthetic measurements are in `validation/journal-overhead.json`.
  At 2048 preloaded events, write p50 is 0.14 ms versus 0.34 ms for d3c811b.
  Measured on one connection, so this does not cover concurrent lock wait.

Still not implemented (disclosed, not hidden): claim/citation entity
publication, a steering.rejected event producer, planner-typed task class
(the runtime does not pass a typed ResearchTask to the worker), and
final_persisted for non-completed terminals. New regressions are in
tests/test_assistant_events_followup.py. All 2538 assistant tests pass, with 2
skipped.

## Claude adversarial review of 7734e9c

Confirmed P2: when obligations were present, the assessment replaced the
reviewer's named missing obligations with an empty list. Assessment results
are now merged with the reviewer's list instead. Accepted P3: interrupted
runs close open activities as unavailable, not failed, because their outcome
is unknown. Deferred P3s with reasons:
- Branch-database hash migration: this branch has never been deployed, so
  no existing database needs it.
- Claim supersession without a replacement_id: no claims are published yet.
  Revisit when claim publication lands.
- Exclude/Prioritize controls stay enabled on finished runs: these already
  returned 409 before this branch.
Both changes have regression tests. All 2540 assistant tests pass, with 2
skipped.
