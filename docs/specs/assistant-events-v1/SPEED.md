# Speed utilities and integration handoff

Status: utility implementation complete; coordinator owns runtime/route/store hooks.
Baseline: 454614ff. This document is not a production benchmark or a paid-evaluation receipt.

## Existing call graph

`ui.assistant_routes.messages` performs external cost admission from inside
`AssistantStore.begin`, persists the queued run, then calls `_dispatch`.
`_dispatch` acquires a physical `_WORKER_SLOTS` permit, calls `store.claim`, and
submits `_work`. `_work` owns final persistence and releases the physical worker.
GET maintenance can dispatch already-admitted queued jobs; queue progress is not
independent of polling.

`assistant_runtime.run_turn` creates a monotonic origin and the existing
180-second runtime guard. Its `bounded_gateway` wraps planner, selector and review
gateways with `assistant_provider_budget.budget_gateway`. The wrapper emits
`provider_checkpoint` with `call_id`, started/finished state and mutable
`phase_timings`. `cedh.cost_ledger.operation_phase` records absolute process
monotonic interval bounds for ledger wait, provider permits, transport and
callbacks. Nested wrappers observe overlapping intervals: summing them overstates
wall time. The new adapter unions intervals per category and deduplicates exact
transport spans for observed attempt counts.

`cedh.provider_deepseek._post` already uses `paid_attempt` and caps each HTTP
operation to the authority receipt's remaining time. It checks cancellation before
retrying, but the existing exponential backoff is not an independent deadline
reservation. Do not add another admission or dollar ledger. Root may integrate a
remaining-deadline/backoff check at that existing boundary if necessary; no
unintegrated alternate retry system was introduced here.

The runtime already has per-turn exact tool-read caching and strategy snapshot
caching. Selection evidence and review evidence share the latter. Selection,
claim review and corrective review remain distinct responsibilities; apparent
similarity is not enough to reuse a verdict.

## Telemetry API

`LatencyTrace(run_id, task_class='unknown', origin_scope='runtime',
origin_monotonic=started, version_hashes=None)` uses one server monotonic origin.
Accepted task classes are exact_fact, rules, discovery, comparison, deck_review,
simulation and unknown. `version_hashes` accepts only SHA-256 values keyed by
prompt/schema/model/policy/evidence. It cannot contain prompts or conversation.

- `mark(name, at=...)`: first observed request_received, accepted, worker_started,
  first_useful_persisted or final_persisted boundary. Missing is unknown, never zero.
  Rejects inverted observed order and invalid/nonfinite values.
- `provider_checkpoint(event)`: consumes existing gateway checkpoints without
  copying prompt, source text, IDs or private metadata into the receipt. Event
  call IDs deduplicate checkpoints; exact transport intervals deduplicate nested
  wrapper observations. Uses at most 128 calls and 512 phase intervals.
- `usage(call_id, input_tokens=..., output_tokens=...,
  cached_input_tokens=0, reasoning_tokens=0)`: records known usage once. No usage
  samples produces null, not zero. Aggregates are explicitly observed samples,
  not complete billing. It never reconciles authority reservations.
- `cache_hit()`: count actual committed receipt reuse only.
- `snapshot().model_dump(mode='json')`: bounded typed report with milestone
  offsets, separately computed acknowledgment/queue/first-useful/completion
  durations and provider interval unions. Report scope warns not to sum categories.

Root integration responsibilities:

1. Capture request receipt/acceptance in the route only where observed. Do not
   infer acknowledgment from `_work`, which starts later. If persisting monotonic
   origins, bind to the current process identity and never compare across a
   process restart. Legacy/recovered runs lacking this origin have unknown timing.
2. Create a worker/runtime scoped trace when no valid request origin is available.
   Mark worker_started at actual claim/start observation. A runtime-only report
   cannot claim queue or acknowledgment latency.
3. Forward actual provider checkpoints via a wrapper that calls both the original
   store callback and `trace.provider_checkpoint(event)`; replacing the original
   checkpoint callback would disable existing cancellation/recovery evidence.
4. Mark first_useful_persisted AFTER a publishable finding has actually committed.
   The quality policy must certify request relevance and deterministic source
   support. Stage announcements and generic commander facts do not qualify.
5. Mark final_persisted only after `store.finish` successfully committed this exact
   run. A stale/terminal rejection does not establish final publication. `run_turn`
   alone cannot measure this final boundary.
6. Client `received_to_rendered_seconds` has its own
   `ClientLatencyObservation` clock domain. Never subtract it from server epoch or
   monotonic timestamps. Do not trust it for billing, grounding or gate decisions.

## Exact judgment reuse API

`ExactJudgmentCache(run_id, owner_id)` is a bounded same-run LRU: max 64 entries,
32 KiB/receipt and 256 KiB of serialized receipts total. There is no shared global
cache, network service, request coalescing or semaphore.

`JudgmentContext` requires owner, run, deck revision/hash, task scope hash,
constraints hash, steering hash, evidence manifest hash, nonempty source hashes,
freshness hash, prompt/schema/model/policy versions and judgment kind. No missing
or malformed validity input can hit. Callers must compute freshness from known
source version policy; hashing an unknown value does not make it known.

`JudgmentReceipt` requires the context fingerprint, exact reviewed text hash,
evidence manifest hash, review receipt ID, `committed=True` and an already
schema-validated receipt value. The cache does not semantically validate that
value and must be called only after existing review validation/gates. `put`
rejects mismatched hashes, wrong owner/run, uncommitted, oversized, non-JSON and
nonfinite values. Stored/get values are JSON-detached so later nested mutation
cannot poison another consumer.

`get(context, text_hash=...)` returns the original exact review receipt or None.
The caller still applies its existing review policy to that receipt. A subclaim
verdict is never an answer approval. Corrective prose, new source sets, freshness
changes or a new run miss. A steering replacement cannot borrow an unfinished
predecessor verdict; a different run ID necessarily misses this initial cache.

Integration must identify an actual repeated review of identical text and
identical evidence/policy. Do not wire this to a broad fuzzy selection or raw
whole-answer similarity cache. When full validity data is unavailable, bypass
reuse; utilities alone do not establish fewer paid calls or faster responses.

## Verification and limitations

48 targeted tests passed for these two modules (0.12s); Ruff and Black checks pass,
and targeted mypy reports no source errors. Tests cover independent milestones,
missing clocks, skew/order rejection, nested/duplicate provider phases, bounded
receipts, unknown usage, client clock separation, all cache validity dimensions,
missing keys, mutation isolation, explicit noncoercible commit markers and byte/count LRU limits.

No tests call a model, database service or browser. No dev server or child process
was started and left running. No source outside this stream's ownership changed.
No observed production improvement is claimed. Persisted integration and request
publication/cancellation tests remain coordinator work; paid latency/quality
benchmarks remain outside current authorization.

## Coordinator integration checkpoint

Production worker routes now capture request/acceptance when observed and use a
worker-scoped origin after recovery. Provider checkpoints still persist through
the original callback. A useful-finding milestone requires a matching committed
publication nonce; timing is stored once on worker exit, outside domain events.
The final timing receipt is content-free and cannot reconcile cost.

Runtime review reuse is connected at the existing review seam. It requires a
complete key, a current local snapshot watcher, exact source and input hashes,
and a matching durable callback acknowledgment before retention. Mutated
corrective receipts are not reused as the original review. Legacy/injected
callbacks without commit acknowledgments miss. This adds no review calls and
does not change the existing one-review budget. Actual cache hit frequency and
latency benefit are unmeasured; default execution may have no repeated review.
