"""Real outbox transactions with a fake email transport; never sends mail."""

from types import SimpleNamespace

import httpx
import pytest

from sabermetrics.ai_cost_control import Authority, BudgetConfig
from sabermetrics.ai_cost_email import drain, main


@pytest.fixture
def mail(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr("sabermetrics.ai_cost_control.time.time", lambda: clock[0])
    authority = Authority(
        str(tmp_path / "budget.sqlite"),
        BudgetConfig(prices=(("fake", 1, 1),), credentials=(), authorized=True),
        local=True,
    )
    with authority._transaction() as state:
        state["alerts"].append(
            dict(
                id="event",
                scope="combined",
                period="2026-11",
                threshold=80,
                exposure_micros=800000,
                limit_micros=1000000,
                acknowledged=False,
            )
        )
    config = SimpleNamespace(
        sender="Deck Lab <accounts@decklab.studio>", api_key="fake-secret"
    )
    return authority, config, clock


def test_success_is_durable_and_not_resent(mail):
    authority, config, _clock = mail
    sent = []

    def send(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "accepted"})

    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        assert drain(authority, config, "djmatthews93@gmail.com", client=client) == 1
        assert drain(authority, config, "djmatthews93@gmail.com", client=client) == 0
    assert len(sent) == 1
    assert sent[0].headers["Idempotency-Key"] == "decklab-ai-budget/event"
    import json

    assert json.loads(sent[0].content)["to"] == ["djmatthews93@gmail.com"]
    assert not authority.alerts()
    assert (
        authority.status()["alerts"][0]["email_delivery"]["accepted_id"] == "accepted"
    )


def test_failed_send_retries_with_identical_payload(mail):
    authority, config, clock = mail
    sent = []

    def send(request):
        sent.append(request)
        return httpx.Response(429 if len(sent) == 1 else 200, json={"id": "accepted"})

    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        assert drain(authority, config, "djmatthews93@gmail.com", client=client) == 0
        assert drain(authority, config, "djmatthews93@gmail.com", client=client) == 0
        assert len(sent) == 1
        clock[0] += 61
        assert drain(authority, config, "changed@example.com", client=client) == 1
    assert sent[0].content == sent[1].content
    assert sent[0].headers["Idempotency-Key"] == sent[1].headers["Idempotency-Key"]


def test_claim_excludes_second_worker_and_recovers_after_crash(mail):
    authority, config, clock = mail
    first = authority.claim_alert_email(
        "event", config.sender, "djmatthews93@gmail.com"
    )
    assert (
        authority.claim_alert_email("event", config.sender, "djmatthews93@gmail.com")
        is None
    )
    clock[0] += 121
    second = authority.claim_alert_email(
        "event", "changed@example.com", "changed@example.com"
    )
    assert second["payload"] == first["payload"]
    assert second["lease"] != first["lease"]
    clock[0] += 23 * 3600
    assert (
        authority.claim_alert_email("event", config.sender, "djmatthews93@gmail.com")
        is None
    )
    assert authority.alerts()[0]["email_delivery"]["needs_review"]


def test_delivery_commit_failure_recovers_same_idempotency_key(mail, monkeypatch):
    authority, config, clock = mail
    sent = []
    original = authority.finish_alert_email

    def unavailable(*args):
        raise OSError("accounting offline")

    monkeypatch.setattr(authority, "finish_alert_email", unavailable)

    def send(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "accepted"})

    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        with pytest.raises(OSError):
            drain(authority, config, "djmatthews93@gmail.com", client=client)
        clock[0] += 121
        monkeypatch.setattr(authority, "finish_alert_email", original)
        assert drain(authority, config, "djmatthews93@gmail.com", client=client) == 1
    assert sent[0].content == sent[1].content
    assert sent[0].headers["Idempotency-Key"] == sent[1].headers["Idempotency-Key"]


def test_preview_cannot_run_central_sender(monkeypatch):
    monkeypatch.setenv("SABER_AI_ENV", "preview")
    with pytest.raises(ValueError, match="main only"):
        main()
