# Research assistant spending controls

Status: implementation specification, 2026-11-03. Applies to paid AI on main
and preview, including planners, selectors, reviewers, repairs, background
research, and optional experimental providers. This supersedes the older
process-local recorded-spend threshold for protected deployments. It does not
introduce quotas on deterministic deck editing or browsing.

## Initial policy

These are configurable conservative rollout defaults, not a spend commitment.
Policy changes are trusted administrative actions, never request parameters.

| Scope | Main | Preview |
| --- | --- | --- |
| Monthly environment budget | $12 | $3 |
| Shared combined monthly budget | $15 across both environments | same authority |
| Daily allowance per authenticated user | $0.50 | $0.20 |
| Daily admitted research turns per user | 10 | 5 |
| Maximum cost per research turn | $0.10 | $0.10 |
| Provider attempts per turn, including retries and repairs | 12 | 12 |
| Tool iterations per turn | 8 | 8 |
| Input tokens per provider attempt | 32,000 | 32,000 |
| Output tokens per provider attempt | 2,000 | 2,000 |
| Turn deadline | 180 seconds | 180 seconds |
| Active research turns per user | 1 | 1 |

Budget periods use UTC calendar months and UTC calendar days. Status responses
include absolute reset timestamps. The legacy trailing-30-day ceiling may remain
as an additional conservative restriction; it cannot replace this policy.
Environment budgets sum to the combined budget, protecting each environment's
allocation. Global user exclusion should use stable account identity, not an
untrusted identifier or IP address. Quotas are not shared through client cookies.

## Authority and credentials

Both deployments and every worker use the same PostgreSQL budget authority,
with a dedicated writable application schema/role. Do not modify the read-only
`mtg_v1` data contract or use the `mtg_consumer` role for accounting writes.
SQLite is suitable only for explicit local/test operation against one shared
file, never for independent deployment volumes. Missing authority, unavailable
storage, invalid policy, and failed accounting all deny new paid requests.

Use separate provider credentials or provider projects for main and preview.
Select credential variable names from trusted environment configuration; never
fall back to another environment's credential. Keys stay server-side and are
provisioned through deployment secret storage, not source files. Runtime cannot
prove two opaque credentials are different: verify separation during provisioning.
Model IDs and conservative uncached input/output/reasoning prices are explicitly
allowed server-side. Unknown models or prices deny admission.

## Admission and reconciliation invariants

1. Authenticate and authorize deck access before creating a paid turn.
2. Atomically deduplicate the submission, enforce one active turn, and increment
   the user's daily turn count once. Bind the key to owner, environment, deck,
   revision, messages, mode, and constraints. Reusing a key for different input
   is a conflict. Duplicates return the original state, not another execution.
3. Before every outbound paid attempt, atomically check all relevant balances:
   `spent + outstanding reservations + maximum attempt cost <= limit`.
   Include combined month, environment month, user day, and turn scopes.
4. Derive the reservation from the actual outbound payload, including system
   prompts, tools, schemas, retry/repair context, and the output/reasoning limit.
   Use a documented conservative token upper bound where an exact tokenizer is
   unavailable. Reject input over the limit; do not silently undercount it.
5. Count transport retries as additional possible charges. Cap SDK-internal
   retries or wrap their actual transport boundary. Each attempt rechecks
   cancellation, deadline, kill switches, model allowlist, and remaining limits.
6. Record actual usage and release only a proven unused balance, atomically and
   idempotently. Missing/malformed usage, timeouts after dispatch, and ambiguous
   provider failures conservatively consume the reservation. Never assume an
   HTTP error means no charge. Reservations must survive process crashes.
7. Never refund an uncertain dispatched charge merely because a worker lease
   expires. Abandoned turns can become terminal without releasing their charges.
   If reported usage exceeds its bound, retain the liability and block further
   paid admission pending investigation; do not hide or clamp the overrun.

Use integer money units with conservative upward rounding. Admission must
serialize competing transactions across workers and deployments, with bounded
database lock waits. Tests must exercise independent connections/processes, not
only Python thread locks. Policy disagreement between deployments must fail
closed rather than letting a caller choose a higher global limit.

## Turn lifecycle, cancellation, and reusable work

Persist turn state and deadline. Propagate the same turn identity into all
background threads and provider adapters; a paid call without an admitted turn
is refused in protected environments. Tool loops must consume a shared durable
iteration allowance, including nested work. A new turn cannot reset the old
turn's accumulated provider count while its worker still runs.

Cancellation makes the turn ineligible for subsequent provider attempts. An
already dispatched request may still be charged; reconcile it without publishing
a stale result. Recheck validity after queued waits and before sending HTTP.
Deduplication remains effective after completion and across reloads. Reuse only
exact results with appropriate owner, revision, model/prompt version, and settings
scope; never reuse private context across users. Retain existing deterministic
retrieval caches where their freshness rules remain valid.

## Alerts and administrative shutdown

Persist deduplicated 50%, 80%, and 95% threshold events for each budget scope
and period. Include reservations when assessing exposure so alerts precede
settlement. Alert recording is part of admission/accounting transactions.
Provide an authenticated admin view or polling endpoint that reliably exposes
pending events; external delivery, if configured, uses durable retryable events
and never sends secrets. Do not describe an outbox as delivered email.

An admin can disable paid AI globally or per environment through a durable
shared kill switch. Every provider attempt checks it, including retries and
background work. Mutations require administrator authorization and CSRF.
The environment feature flag is an additional local emergency off switch.
At a budget cap, new paid attempts stop automatically. Browsing, login, deck
editing, and viewing previous research remain available.

Show users a clear reason for rejection, remaining daily allowance/turns where
appropriate, and reset time. Do not expose other users' usage or credentials.
Distinguish budget exhaustion, active work, disabled AI, and temporary accounting
unavailability. An exhausted budget must never trigger a fallback paid provider.

## Verification and rollout

Required focused tests: competing reservations at the exact cap; different
environments sharing the combined cap; accounting outage and failed settlement;
unknown usage and worker crash; duplicate and conflicting idempotency keys;
concurrent same-user turns on different decks; UTC rollover with in-flight work;
retry/repair/nested review accounting; cancellation while queued; malicious model
selection; missing environment credential; deduplicated alert thresholds; admin
authorization; free browsing when the authority is unavailable.

Before enabling paid research: provision the dedicated shared writable authority,
apply schema, configure identical shared policy, provision separate provider
credentials, check model prices and bounded provider contracts, verify alert
visibility/delivery, and run a cross-deployment admission smoke test with fake
providers. No production deployment or secret provisioning is implied by this
implementation task. Actual provider charges depend on provider billing accuracy;
the application guarantee is strict admission against configured conservative
bounds, with conservative retention of uncertain charges.

## Implementation and operator handoff

Implemented in `ai_cost_control.py`, provider transports, `assistant_tools.py`,
research runtime/store/routes, and the assistant sidebar. Example trusted policy
and environment setup are in `deploy/ai-cost-config.example.json` and
`deploy/ai-cost.env.example`. The example is disabled and contains no real prices
or credentials. It is not a deployable pricing recommendation.

1. Provision a dedicated writable PostgreSQL budget database/schema. Do not reuse
   the read-only tournament consumer role. Both environments must point at the
   same authority table via `SABER_AI_COST_DSN` in private deployment secrets.
2. Copy and complete the example policy with approved model IDs, conservative
   integer microdollar-per-token prices (round upward), and separate credential
   variable names. Install corresponding distinct provider credentials privately.
3. Install the identical finalized policy in both deployments and select it with
   `SABER_AI_COST_CONFIG`. Select `SABER_AI_ENV=main` or `preview` respectively.
   Never select `local` or `test` in hosted environments. Unset environment also
   denies paid transport. Only explicitly authorized policy initializes accounting
   through the web adapter; the disabled example does not create financial state.
4. Before enabling users, run `tests/test_ai_cost_postgres.py` against a disposable
   writable PostgreSQL URL supplied as `SABER_AI_COST_TEST_DSN`. This test creates
   and removes a unique schema. Then exercise both deployments with fake providers
   to verify the shared scope and credential selection. Do not use paid prompts
   as a substitute for this test.
5. Budgets and prices are fingerprinted in the persisted authority. Changing a
   policy after initialization intentionally blocks mismatched workers. Coordinate
   an explicit administrative policy migration preserving ALL charges and unknown
   reservations; never delete/recreate the authority to change a limit. No automatic
   spend reset or policy migration is supplied by this change.

Authenticated users can read their remaining daily dollars/turns and absolute
UTC reset times at `/api/assistant/cost-status`. Administrators can inspect
`GET /api/admin/assistant/cost-control`. CSRF-protected POSTs to that same endpoint
accept `{"action":"kill","enabled":true}` globally, or add
`"environment":"preview"` for an environment shutdown. `enabled:false` reverses
that switch. `{"action":"reset"}` clears only the global shutdown, preserving
all spending. `{"action":"acknowledge","alert_id":"..."}` acknowledges an
outbox event. Read the outbox through this admin endpoint; email/Slack/webhook
notification delivery is not configured or claimed by this implementation.

Paid Jev experimentation is refused in protected environments because its current
wire integration does not expose an enforceable output-token bound. It cannot
serve as a fallback around a cap. Normal DeepSeek research remains the guarded
primary path; legacy Anthropic requires the same admitted context when protected.

Independent Sol-medium adversarial review reports are `ai-cost-review-core.md`,
`ai-cost-review-web.md`, and `ai-cost-review-closure.md`. Their reproduced defects
have corresponding regression tests; historical evidence assertions were converted
into assertions of the repaired behavior. The final nested-tool finding was fixed
by metering execution boundaries and testing exhaustion before the next nested
body. Live PostgreSQL/provider billing validation remains a rollout gate.

### Budget alert email

The configured recipient is `djmatthews93@gmail.com`, supplied to the central worker
as `SABER_AI_ALERT_EMAIL` (see the environment example). Run
`python -m sabermetrics.ai_cost_email` once per minute in the main application's
runtime with the shared budget DSN/policy and existing private `RESEND_API_KEY`,
`SABER_EMAIL_FROM` and `SABER_PUBLIC_URL`. A host scheduler should invoke this
one-shot command; no persistent polling thread is added to web requests. The worker
refuses preview execution. It reads combined/main/preview budget events from the
shared authority, so preview itself needs no outbound email configuration.

The sender emails combined/environment 50/80/95% threshold events and automatic
provider-overrun shutdowns. Individual user/turn events stay in the admin outbox.
Each send is claimed transactionally with a 120-second lease, stable persisted
recipient/content, and a provider idempotency key. Failures retry after exponential
backoff capped at one hour, with at most 20 send attempts per invocation. A confirmed
Resend acceptance is recorded and acknowledged atomically; acceptance does not
prove inbox delivery. Financial counters and reservations are never modified.

Resend retains idempotency keys for 24 hours. After 23 hours from the first attempt,
ambiguous sends stop automatic retries and remain unacknowledged with
`email_delivery.needs_review=true` in admin status. Inspect provider delivery records
and reconcile manually rather than risk sending a duplicate. Acknowledging such
an alert after confirming receipt suppresses further handling. Provider responses,
API keys, prompts and user research are never logged or included in email bodies.

This code/configuration has not been installed into a live scheduler or used to
send a real test email. Activating delivery remains part of the reviewed rollout.
