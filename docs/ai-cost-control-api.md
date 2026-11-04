# AI cost authority API (integration contract)

Module: `sabermetrics.ai_cost_control`. All money is integer microdollars.
`BudgetConfig` is immutable; `Authority(dsn, config, local=False)` requires PostgreSQL
unless explicit local SQLite mode is selected. Every deployment must use the SAME
PostgreSQL database and identical config. SQLite is only for local tests/dev sharing
one file, never a combined deployment budget. Configuration mismatch fails closed.
No default config enables spending: trusted server setup must supply model prices
and environment-specific credential variable names, plus explicit authorization.

`begin_turn(environment, user_id, idempotency_key, owner)` returns a turn ID.
Replays by the same owner return the same ID; another owner is excluded. Never use
client-controlled owners or environment names. `finish_turn(id, owner)` and
`cancel_turn(id, owner)` close admission, retaining all existing charges/reservations.
`bind_turn(authority, id, owner)` propagates via ContextVar; copy context explicitly
when dispatching threads. Missing context denies paid transport in main/preview.
`admit_attempt(id, owner, model, input_tokens, output_tokens)` atomically reserves
worst-case cost and counts each HTTP attempt including retries and repairs.
`reconcile(attempt_id, cost_micros=None)` retains full reservation for unknown usage;
known nonnegative cost releases only proven unused reservation. Above-bound cost
records actual liability and trips shared kill switch. Reconciliation is idempotent.
Never expire/release unknown billed calls. `status()` exposes counters/kill state;
`set_kill_switch(bool)` is operator-only. `reset()` clears kill only, never spend.
`alerts()` exposes durable outbox; consumer delivers and `ack_alert(id)` acknowledges.
`tool_iteration(id, owner)` enforces the shared eight-iteration limit.

Proposed defaults: calendar UTC monthly combined $15, main $12 / preview $3;
UTC user daily main $0.50 and 10 turns, preview $0.20 and 5 turns; each turn $0.10,
12 attempts, 8 tool iterations, 32,000 input / 2,000 output per attempt, 180 seconds.
These defaults are ceilings, not authorization. Prices must conservatively cover
all provider billing categories (cache writes/reasoning included). Transport guard
uses serialized UTF-8 bytes as a conservative input-token upper bound. Unsupported
billing semantics must remain disabled. Server config must select credential names;
no generic or cross-environment fallback is permitted in protected environments.

Transport details: trusted environment selector is `SABER_AI_ENV`; only explicit
`local` or `test` permits legacy transport without context. Missing selector also
denies, so deployment metadata mistakes cannot silently enable paid calls.
Credential provider mapping keys are `deepseek`, `anthropic`, `jev`. Bind before
constructing provider clients. Anthropic SDK internal retries are disabled; each
explicit retry reserves separately. Protected Jev calls currently DENY because its
wire API here exposes no enforceable output-token limit. Do not enable until a
provider-enforced upper bound can be established. DeepSeek and Anthropic reconcile
complete usage using configured conservative rates; missing/invalid usage retains
reservation. Configure Anthropic input price to cover the highest cache-write rate.
No automatic expiry, history deletion, spend reset, or reservation refund exists.

Implemented lifecycle details:
- `begin_turn` excludes another unexpired active turn for the same user across
  environments. Completed exact submissions replay from the local durable store.
- `set_kill_switch(enabled, environment=None)` supports global or environment
  shutdown. Reset clears the global kill only, without refunding anything.
- Alerts include 50/80/95 percent exposure (charges plus outstanding reservations)
  for combined month, environment month, user day and turn. They remain in the
  durable outbox until acknowledged; no email delivery is implied.
- `consume_tool_iteration()` meters actual tool bodies, recursively nested calls,
  runtime package research, answer checking and cached tool iterations. UI progress
  events do not charge a second tool unit.
- `status()` reads a consistent snapshot without rewriting financial state.
- If the session save fails after admission, its authority turn is closed without
  refund. An identical unsaved retry returns 409 with `retry_new_submission=true`,
  rather than executing against closed admission. The UI preserves the message
  and gives its next deliberate retry a new submission ID.

QA rollout policy (2026-11-03): the input byte ceiling is 128,000 and the
provider output ceiling is 4,096. The static tool schema and system prompt alone
exceed the initial 32,000-byte default. Dollar, turn, attempt, tool and time limits
are unchanged. `price_scale` (default 1) divides the configured integer rates;
both reservations and usage charges round upward to whole microdollars. The
rollout uses scale 1,000 with input/output rates 600/900, equivalent to conservative
$0.60/$0.90 per million tokens. A maximum call reserves $0.080487.
Published rates checked on 2026-11-03:
https://deepinfra.com/deepseek-ai/DeepSeek-V4.1-Flash and
https://deepinfra.com/deepseek-ai/DeepSeek-V4-Flash . The bounds cover undiscounted
priority output and input including two-times cache writes. Policy migration
changes only the configuration fingerprint and appends an audit event; existing
charges, reservations and daily turn counters are retained.

Anonymous preview exception (owner requested 2026-11-03): a signed temporary
browser session may use paid research without registering. Main still requires
its normal authenticated account. The trusted preview adapter installs
`ai_cost_guest_network`, which maps the private owner to an HMAC of the canonical
client network (IPv4 address or IPv6 /64). The raw address is not in the budget
ledger. Deck ownership remains the independent random session user, never the
shared network identity. Forwarded headers are not read directly; the private
Caddy proxy overwrites them before Waitress establishes remote_addr.

`begin_turn(..., guest_session=...)` is preview-only. The existing preview user
allowance becomes the network allowance ($0.20 / 5 turns per UTC day). An extra
session allowance ($0.10 / 2 turns per UTC day), a 30-second network cooldown,
and one active turn per network/session are enforced under the same PostgreSQL
row lock. Every provider reservation checks the session's charges plus unknown
reservations as well as the network, environment and combined caps. Exact
submission replays bypass neither ownership checks nor budgets and consume no
new turn. The status endpoint shows the smaller remaining session/network
allowance without disclosing either identity. Cookies, restarts and expired demo
workspace cleanup do not delete the financial history.

No anonymous identifier proves a unique person. Cookie resets plus changing
networks can evade visitor limits; the $3 monthly preview cap remains the final
spending boundary. Shared networks can share an allowance. A bot challenge such
as Turnstile is an additional option, not currently configured or required by
this deployment. Guest browsing remains available when research is exhausted
or accounting/identity configuration is unavailable.
