"""Bounded central outbox drain. Run once per minute on main, never on preview."""

from __future__ import annotations

import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path

import httpx

from sabermetrics.ai_cost_control import Authority, BudgetConfig
from sabermetrics.ui.recovery import EmailConfig

logger = logging.getLogger(__name__)


def drain(control, config, recipient, *, client=None, limit=20):
    """Provider acceptance is persisted; it does not prove inbox delivery."""
    if not recipient or "@" not in recipient or any(c.isspace() for c in recipient):
        raise ValueError("A single alert recipient is required")
    accepted = attempted = 0
    with (
        httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        if client is None
        else nullcontext(client)
    ) as transport:
        for event in control.alerts():
            if attempted >= limit:
                break
            if not (
                event.get("scope") == "combined"
                or str(event.get("scope", "")).startswith("environment:")
                or event.get("reason") == "provider exceeded reservation"
            ):
                continue
            claim = control.claim_alert_email(event["id"], config.sender, recipient)
            if claim is None:
                continue
            attempted += 1
            provider_id = None
            try:
                response = transport.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": "Bearer " + config.api_key,
                        "Idempotency-Key": "decklab-ai-budget/" + event["id"],
                    },
                    json=claim["payload"],
                )
                if response.status_code in {200, 201}:
                    value = response.json().get("id")
                    if isinstance(value, str) and value:
                        provider_id = value
            except (httpx.HTTPError, ValueError, AttributeError):
                # Never log provider errors, credentials, or message content.
                pass
            control.finish_alert_email(event["id"], claim["lease"], provider_id)
            if provider_id:
                accepted += 1
            else:
                logger.warning(
                    "Budget alert email not confirmed; durable retry pending"
                )
    return accepted


def main():
    # Only a central worker sends email, including shared preview budget alerts.
    if os.environ.get("SABER_AI_ENV") != "main":
        raise ValueError("Budget email worker must run on main only")
    config = EmailConfig.from_env()
    if config is None:
        raise ValueError("Resend configuration is required")
    data = json.loads(Path(os.environ["SABER_AI_COST_CONFIG"]).read_text())
    for name in ("prices", "credentials", "environments"):
        if name in data:
            data[name] = tuple(tuple(row) for row in data[name])
    control = Authority(os.environ["SABER_AI_COST_DSN"], BudgetConfig(**data))
    drain(control, config, os.environ["SABER_AI_ALERT_EMAIL"])


if __name__ == "__main__":
    main()
