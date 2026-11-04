"""Adversarial review checks; all transports are local fakes."""

import copy
import json

import httpx
import pytest

from sabermetrics.ai_cost_control import BudgetDenied
from sabermetrics.ai_cost_email import drain

pytest_plugins = ["tests.test_ai_cost_email"]


def test_stale_finish_cannot_overwrite_new_claim(mail):
    authority, config, clock = mail
    first = authority.claim_alert_email("event", config.sender, "a@example.com")
    clock[0] += 120
    second = authority.claim_alert_email("event", config.sender, "b@example.com")
    before = authority.status()
    with pytest.raises(BudgetDenied, match="stale"):
        authority.finish_alert_email("event", first["lease"], "old-accepted")
    assert authority.status() == before
    authority.finish_alert_email("event", second["lease"], "accepted")
    assert not authority.alerts()


def test_backoff_cap_and_exact_review_boundary(mail):
    authority, config, clock = mail
    start = clock[0]
    for expected in (60, 120, 240, 480, 960, 1920, 3600, 3600):
        claim = authority.claim_alert_email("event", config.sender, "a@example.com")
        assert claim
        authority.finish_alert_email("event", claim["lease"])
        delivery = authority.alerts()[0]["email_delivery"]
        assert delivery["retry_at"] - clock[0] == expected
        assert (
            authority.claim_alert_email("event", config.sender, "a@example.com") is None
        )
        clock[0] += expected
    clock[0] = start + 23 * 3600
    assert authority.claim_alert_email("event", config.sender, "a@example.com") is None
    assert authority.alerts()[0]["email_delivery"]["needs_review"]


@pytest.mark.parametrize("body", [[], None, {}, {"id": 123}, {"id": ""}])
def test_malformed_acceptance_is_retryable_and_spend_unchanged(mail, body):
    authority, config, _ = mail
    before = copy.deepcopy(authority.status())
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        assert drain(authority, config, "a@example.com", client=client) == 0
    after = authority.status()
    delivery = after["alerts"][0].pop("email_delivery")
    assert "accepted_id" not in delivery
    assert after == before


def test_scope_filter_keeps_user_turn_outbox_and_sends_shutdown(mail):
    authority, config, _ = mail
    with authority._transaction() as state:
        template = state["alerts"][0]
        for scope in ("user:alice", "turn:one", "environment:preview"):
            state["alerts"].append(dict(template, id=scope, scope=scope))
        state["alerts"].append(
            dict(
                id="shutdown",
                reason="provider exceeded reservation",
                acknowledged=False,
            )
        )
    sent = []

    def send(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={"id": "accepted"})

    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        assert drain(authority, config, "a@example.com", client=client) == 3
    assert len(sent) == 3
    assert {a["id"] for a in authority.alerts()} == {"user:alice", "turn:one"}


def test_transport_failure_has_no_sensitive_logs(mail, caplog):
    authority, config, _ = mail

    def send(request):
        raise httpx.ConnectError("fake-secret and provider response", request=request)

    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        assert drain(authority, config, "a@example.com", client=client) == 0
    assert "fake-secret" not in caplog.text
    assert "provider response" not in caplog.text
    assert authority.alerts()[0]["email_delivery"]["retry_at"]
