# AI cost email adversarial review

Reviewed `src/sabermetrics/ai_cost_email.py`, `claim_alert_email` and
`finish_alert_email` in `ai_cost_control.py`, and `tests/test_ai_cost_email.py`.

## Findings

No reproducible correctness or security findings in the reviewed scope. No
production changes recommended on the evidence from this review.

## Evidence

- Payload (including sender and recipient) is persisted before external IO.
  Existing retry/crash tests verify identical request content and idempotency key
  even when the configured recipient changes.
- Claims use the authority transaction and a 120-second lease. A reclaimed alert
  receives a new token; a stale completion raises and rolls back without modifying
  the new claim. The review test exercises the exact lease expiration boundary.
- Retry delays are 60, 120, 240, 480, 960, 1920, then 3600 seconds; the new test
  verifies the cap and suppression at exactly 23 hours from the first claim.
- Missing, empty, numeric, and malformed-shape acceptance responses leave the
  alert unacknowledged and scheduled for retry. Tests compare the remaining
  authority state before/after to verify financial state is unchanged.
- The worker sends combined and environment alerts plus provider-overrun shutdown
  alerts, while user/turn alerts remain in the outbox. Success is provider
  acceptance, not a claim of inbox delivery.
- Simulated connection exceptions containing a fake credential produce only the
  generic warning; exception content and credentials do not appear in captured
  logs. The transport disables redirects and environment proxy inheritance.
- `main()` checks the main environment before reading email configuration or
  constructing the authority. No admission, reservation, or reconciliation paths
  are changed by the email worker.

## Validation and limits

Command (serial execution, no parallel workers):

```sh
PYTHONPATH=src /Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python -m pytest -q tests/test_ai_cost_email.py tests/test_ai_cost_email_review.py
```

Result: **14 passed**. Review checks are in
`tests/test_ai_cost_email_review.py`. Tests use local SQLite transactions and fake
HTTP transports. PostgreSQL contention, live Resend idempotency behavior, inbox
delivery, and scheduler installation were not exercised. Claim interleavings were
tested deterministically rather than with concurrent processes. Existing shared
cap and paid-call enforcement were inspected for interaction, not revalidated by
running their broader suites.

No real mail, secret inspection, deployment, servers, installations, or production
code edits were performed. Unrelated workspace changes were preserved.
