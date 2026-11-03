# Final adversarial cost-control review — 2026-11-03

Read-only production review of the current working tree and the prior core/web
reports. Only this report and tests/test_ai_cost_review_closure.py were created
by this review. Concurrent edits and unrelated documents were preserved.

## Remaining finding: P2 / Medium — nested tool executions escape durable allowance

Locations: src/sabermetrics/ui/assistant_routes.py:231–233;
src/sabermetrics/assistant_tools.py:1012–1050.

The durable tool counter is incremented by the web worker's `tool_started` event
handler. Runtime emits this event for an outer tool invocation, but
`prepare_improvement` directly calls `self.execute` for
`retrieve_strategy_evidence`, `assess_deck_requirements`, `find_replacements`,
and `retrieve_rules`. Those nested calls emit no corresponding event and do not
consume the authority's shared iteration allowance. The fallback strategy query
can add another uncounted execution. This violates the explicit requirement that
tool loops, including nested work, consume the durable allowance.

Exact offline reproduction:

```
PYTHONPATH=src /Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python -m pytest -q tests/test_ai_cost_review_closure.py
```

`test_nested_prepare_improvement_escapes_shared_tool_counter` constructs real
AssistantTools and a real local authority, consumes the outer event's one unit,
and executes the real prepare_improvement dispatch under bind_turn. It stubs only
nested tool bodies and deck-fact rendering, recording the four nested dispatches.
The authority still reports tools=1. This is an evidence test asserting the defect,
not a passing assertion of the intended policy. No provider is called. Nested
operations can exceed eight executions while the durable counter remains below
eight. Separate provider attempt/dollar reservations remain enforced; this test
does not demonstrate a monetary cap bypass.

Recommendation: enforce counting at the actual tool execution boundary, including
recursive/runtime-owned operations; avoid double charging the outer event. Ensure
an exhausted allowance prevents entering the next nested body. Add a regression
that exhausts the counter between nested invocations.

## Prior findings and requested fixes

The prior core and web evidence files now pass (nine tests total). Inspected fixes:
raw Anthropic categories require exact nonnegative integers before reconciliation;
DeepSeek rejects n other than absent/exact integer 1 before transport; session-save
failure closes admission and identical retry returns 409/retry_new_submission;
message replay is resolved before new-work provider checks; steering provider
checks occur inside its admission callback; sidebar status is always visible and
clears terminal pending request IDs; environment kills appear in user status.
Active-user exclusion spans environments/decks, threshold events are durable, and
PostgreSQL lock/connect/statement timeouts are configured. Unknown charges remain
reserved. Protected Jev remains disabled.

Cancellation inspection: stop closes the authority binding and then stops the
session; settings changes cancel superseded work and close its binding. Attempt
admission checks closed/deadline/kill state. Worker publication checks current
session generation/revision, so cancellation does not refund dispatched charges.
Browsing/status paths remain available on the exercised accounting-status outage.
No additional reproducible cancellation or per-user disclosure defect was established.
The worker explicitly binds its turn in its background execution; absent context
fails closed at protected adapters. This is not exhaustive verification of all
background/nested runtime workflows or races.

## Validation and limitations

Focused command used the existing app/.venv interpreter and PYTHONPATH=src:
`python -m pytest -q tests/test_ai_cost_review_closure.py tests/test_ai_cost_adversarial_core.py tests/test_ai_cost_adversarial_web.py tests/test_assistant_cost_web.py tests/test_ai_cost_control.py tests/test_ai_cost_process_admission.py tests/test_ai_cost_postgres.py`

Result: **20 passed, 1 skipped**, 1.40 seconds. The skipped real PostgreSQL test
is the known missing DSN/executable limitation, not a newly fixed issue. SQLite
independent-process admission evidence does not certify PostgreSQL deployment
contention or cross-deployment behavior. No full suite, browser/e2e, paid calls,
installs, deployment, commits, or subagents. No servers/watchers were started;
final listener/Playwright checks returned no matching processes. Production
provider billing contracts and live credentials were not exercised.
