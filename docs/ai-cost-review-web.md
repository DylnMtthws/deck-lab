# Adversarial web cost-control review

Reviewed 2026-11-03 against a concurrently changing worktree. Production files were not edited. Tests added only in `tests/test_ai_cost_adversarial_web.py`.

## P2 — Session persistence failure poisons the request ID

Locations: `ui/assistant_routes.py:381-406`, `assistant_store.py:245-246,268`, `ai_cost_control.py:186-195` (line numbers may shift with concurrent edits).

Reproduction: admit a message, inject an OSError from AssistantStore._save, restore storage, then retry the identical HTTP body and client_request_id. The first request rolls back the session/submission transaction and closes the independently committed authority turn. The retry finds no submission record, but authority.begin_turn returns the original closed turn for its deterministic key. HTTP responds 202 and persists a new job with that closed binding. _work.stopped immediately invalidates it; no research can execute under this request ID. The failure also consumes a daily turn despite no durable submission.

Automated reproduction: `test_failed_session_commit_poisoned_idempotency_retry` fails on the closed-admission assertion. This uses a fake executor and local SQLite authority, not a paid request.

Recommended fix: persist recoverable submission/admission intent and terminal error state across the two stores. Replay a durable failure rather than accepting a job bound to a closed turn. Do not reopen closed admission or reset accumulated counters; distinguish a legitimate replay from a new execution after a failed commit.

## P2 — Provider shutdown prevents exact submission replay

Location: `ui/assistant_routes.py:371-379` (provider check before store.begin replay lookup).

Reproduction: POST a valid message; stop it; disable provider status; POST the identical request ID/body. Response is 503 provider_unavailable rather than the original persisted submission. Existing replay state does not require paid transport. This breaks retry reconciliation during shutdown or lost credentials. GET history remains available, so this is an idempotency-contract failure, not total loss of history.

Automated reproduction: `test_exact_replay_still_readable_when_provider_disabled` fails (503 versus 202).

Recommended fix: resolve and validate exact persisted submissions before checking provider availability for new work; replay must not dispatch another execution. Apply equivalent ordering to steering replays.

## P2 — Sidebar hides accounting outage when provider remains configured

Location: `ui/static/deck-lab-assistant-sidebar.js:527-528`.

Reproduction from the rendering expression: with data.provider.enabled=true and data.cost_control={configured:false,message:"Paid research unavailable..."}, provider.hidden evaluates true. These values occur when a cached authority/config and credentials exist but authority.status fails. The next line writes the outage explanation into the hidden element. Thus the intended free-browsing/accounting-unavailable guidance is invisible.

Recommended fix: keep the status element visible for an unavailable cost authority; render both provider availability and budget state explicitly. Also avoid displaying only positive daily allowance when provider.enabled=false but quota.configured=true: the current text then hides the missing/disabled provider reason.

Static reproduction only; no browser/e2e executed.

## Verification and limits

Command: `PYTHONPATH=src /Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python -m pytest -q tests/test_ai_cost_adversarial_web.py`

Latest result: **2 failed, 3 passed**, 0.69 seconds. Failures intentionally assert desired behavior for the two reproduced bugs above. Passing cases cover cross-deck active-user exclusion, replay leaving the newer session intact, and per-environment kill visibility. Environment kill visibility was corrected concurrently and is not reported as an open bug.

Read both cost-control contracts, adapter/routes/store/runtime event hooks, sidebar, deployment examples, and existing focused web tests. Global Flask CSRF initialization and blueprint authentication/admin checks are present; this run does not establish complete authorization coverage. No live PostgreSQL, cross-instance deployment, provider billing/credentials, paid calls, real worker timing, full suite, e2e, installs, deployment, commits, dev servers, or subagents. Nested/background propagation and nested tool accounting were inspected only and are not exhaustively verified. No claim that untested paths are safe. No processes were started beyond short-lived targeted pytest commands.
