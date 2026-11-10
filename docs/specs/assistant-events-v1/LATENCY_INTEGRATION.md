# Observed telemetry completion handoff

This change adds no provider calls, retries, model changes, financial ledger or
billing conclusions. All verification below is offline.

## Provider receipts

`BudgetedGateway` adds `usage_observed` and `usage_observation_id` only to finished
checkpoints with a real `GenerationResult.usage: UsageRecord`. All four token
fields must be explicitly present, bounded nonnegative integers; cached input
must not exceed input. The provider, model and nonempty provider request ID bind
the SHA-256 observation identity. Nested wrappers returning the same provider
receipt share that identity. It contains no prompt or provider request ID text.

`LatencyTrace.provider_checkpoint` passes these receipts to its existing bounded
`usage()` accumulator. Identity deduplication prevents repeated or nested wrapper
checkpoints from counting the same usage twice. It operates even when phase
intervals are absent. Transport attempt counts remain deduplicated interval
observations, separate from aggregate generation usage.

A material limitation: the existing DeepSeek adapter turns absent raw provider
usage into an all-zero UsageRecord. Without changing that adapter, an actual zero
and absent usage are indistinguishable. This adapter therefore conservatively
omits all-zero totals, default-only fields, missing provider identities, failed
calls without a returned result, and malformed receipts. Missing usage stays
null; these aggregates describe observed samples and cannot certify complete
billing. Valid observed validation-retry totals are counted once, as returned by
the existing provider adapter. Partial successful usage is never extrapolated to
unknown failed attempts.

## Task class hooks (coordinator-owned runtime)

Use `task_class_from_metadata(task=validated_research_task)` after an actual typed
`ResearchTask` planner receipt. Discover/fill/swap map to discovery; compare to
comparison; explain with current_deck scope to deck_review. Other combinations
remain unknown. Raw dictionaries and tool-operation names are not planner proof.
A request for empirical evidence does not establish simulation execution.

On a final exact direct-fact response, use
`task_class_from_metadata(answer_review=result.get('answer_review'))`;
`status=source_rendered` establishes exact_fact. Pass only actual internal final
review metadata, never a client-supplied status. Retain an earlier known class
when a later mapping returns unknown. Current planner metadata has no distinct
rules/simulation task discriminator; those remain unknown until a typed source
exists. No lexical inference or added classifier/model call is introduced.

## Client observation hooks (coordinator-owned route, UI-owned producer)

`ClientLatencyObservation` strictly validates run_id, client_monotonic clock,
kind first_useful|terminal, received_to_rendered_seconds in [0,86400], and the
first_useful boolean. kind=first_useful requires true; kind=terminal requires
false. The default kind is first_useful for compatibility. Nonfinite values,
extra fields, wrong types and mismatched booleans are rejected. Omit missing or
over-limit observations rather than clamping.

The route must authorize the existing run and compare the submitted expected
run/generation/revision before storing a separate telemetry checkpoint. Dedupe
one observation per kind/run. Do not start jobs, alter lifecycle, advance event
sequence or settle financial usage. A client duration is untrusted and uses only
the browser's clock; never subtract it from a server timestamp or infer grounding
from its first_useful flag.

## Offline verification

Targeted latency/provider tests cover nested identity dedupe, repeated checkpoint
replay, phase-free usage, missing/default/zero/unidentified usage abstention,
real deferred gateway validation-retry totals with mocked HTTP, typed task-class
mapping, and client kind/duration validation. Existing cancellation, ledger and
provider-permit tests remain in the same targeted suite. No live speed/quality or
cost improvement is established by these tests.
