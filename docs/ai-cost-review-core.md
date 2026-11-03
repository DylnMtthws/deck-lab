# Adversarial core cost review — 2026-11-03

Scope: ai_cost_control.py, cedh/provider_deepseek.py, reasoning/client.py,
assistant_jev.py against the two cost-control specifications. No production edits,
paid calls, deployments, commits, or PostgreSQL integration runs were performed.
The implementation was being edited concurrently; findings below refer to the
last inspected working tree. Web/store/runtime/UI work was excluded.

## 1. High: malformed Anthropic usage can release an uncertain reservation

Location: src/sabermetrics/reasoning/client.py:299–309.

The adapter adds input, cache-read, and cache-write counts before passing the
result to AttemptReceipt.usage. The receipt validates only the aggregate. Thus
input_tokens=100, cache_read_input_tokens=-100, cache_creation_input_tokens=0,
output_tokens=0 becomes valid aggregate usage (0, 0). Reconciliation settles the
attempt at zero and releases the entire reservation. This violates the explicit
requirement that malformed usage retain the full bound. Repeated malformed
responses can free admission capacity despite dispatched, potentially billed work.
This requires malformed upstream/SDK metadata; ordinary user control of that
metadata has not been demonstrated.

Reproduction: test_anthropic_negative_cache_metadata_refunds in
 tests/test_ai_cost_adversarial_core.py uses an injected SDK-shaped response and
calls the real call_with_cache under an admitted turn. It verifies a positive
reservation becomes settled with charge zero. No network call occurs.

Fix: validate each raw usage category independently as an exact nonnegative int
before aggregation; reject bools, negatives, fractional values and invalid/missing
required fields. On any invalid category, keep the reservation. Do not coerce
invalid falsey values to zero. Only aggregate after validation, with documented
rules for optional categories. Add negative cache-write and boolean cases too.

## 2. Medium: DeepSeek accepts output-multiplying parameters without reserving them

Location: src/sabermetrics/cedh/provider_deepseek.py:360 and :368–382.

extra_body passes through arbitrary parameters. The transport rejects model and
max_completion_tokens overrides, but accepts n=10 while reserving max_tokens only
once. The local reproduction sends n=10/max_tokens=100 and proves the total
reservation is less than 1,000 output units. An OpenAI-compatible endpoint that
honors n can produce ten separately bounded completions. Missing usage retains
only the undersized reservation; complete usage trips the kill switch after the
overspend has happened. Server-controlled extra_body is the demonstrated entry
point, not an established untrusted request exploit.

Reproduction: test_deepseek_multiple_choices_not_in_reservation exercises _post
with httpx.MockTransport and an actual authority. Acceptance and underreservation
are reproduced; whether the configured live endpoint honors n was not tested.
Do not describe actual provider overspend as proven by this test.

Fix: enforce n absent or exactly integer 1 at the final transport boundary, and
allowlist supported body options with established billing semantics. Alternatively,
reserve all completions and validate the provider contract before enabling them.
Audit other output-expansion options against the same rule.

## Verification and withdrawn observations

Command: PYTHONPATH=src /Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python
-m pytest -q tests/test_ai_cost_adversarial_core.py

Latest result: 4 passed in 0.50s. Two tests assert the current defects as evidence;
two assert the newly implemented active-turn exclusion and threshold alerts.
They are evidence tests, not a declaration that the cost contract passes.

Initial inspection reproduced missing active-turn exclusion and threshold alerts.
A concurrent implementation update fixed both before the final run; they are not
outstanding findings. The latest PostgreSQL path also configures connect, lock,
and statement timeouts. PostgreSQL execution, transaction contention and outage
behavior remain unvalidated here, rather than established defects.

The inspected receipt rejects negative/noninteger aggregate counts; unknown usage
retains charge; above-bound reconciliation retains actual liability and kills
admission; cancellation closes subsequent admission without refunds. Missing
protected context denies transport, and protected Jev remains explicitly disabled.
No reproducible bypass of those paths was established in this review. Context
propagation by runtime callers and user-facing dedup bindings belong to the ongoing
integration work and were not treated as completed core defects.

No background processes were started. Heavy-job process check found none before
the targeted run; final listener/Playwright checks found none to clean up.
