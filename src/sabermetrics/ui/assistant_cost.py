"""Fail-closed web adapter for the shared paid-turn authority.

Trusted application setup installs an Authority at extensions['ai_cost_authority']
and selects AI_COST_ENVIRONMENT. Neither value comes from request input.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SETUP_LOCK = threading.Lock()
import logging

from flask import current_app


class CostUnavailable(RuntimeError):
    def __init__(self, message, status=429, *, retry_new_submission=False):
        super().__init__(message)
        self.status = status
        self.retry_new_submission = retry_new_submission


def authority(app=None):
    app = app or current_app
    value = app.extensions.get("ai_cost_authority")
    if value is None and os.environ.get("SABER_AI_COST_CONFIG"):
        with _SETUP_LOCK:
            value = app.extensions.get("ai_cost_authority")
            if value is None:
                try:
                    from sabermetrics.ai_cost_control import Authority, BudgetConfig

                    data = json.loads(
                        Path(os.environ["SABER_AI_COST_CONFIG"]).read_text()
                    )
                    for field in ("prices", "credentials", "environments"):
                        if field in data:
                            data[field] = tuple(tuple(row) for row in data[field])
                    policy = BudgetConfig(**data)
                    if not policy.authorized:
                        raise CostUnavailable("Paid research is disabled by policy.")
                    value = Authority(os.environ["SABER_AI_COST_DSN"], policy)
                    app.extensions["ai_cost_authority"] = value
                except Exception as exc:
                    raise CostUnavailable(
                        "Shared cost controls are unavailable."
                    ) from exc
    if value is None:
        raise CostUnavailable(
            "Paid research is unavailable until shared cost controls are configured."
        )
    return value


def quota_identity(user):
    """A trusted preview adapter may group private owners under a network quota."""
    resolver = current_app.extensions.get("ai_cost_guest_network")
    if resolver is None:
        return user, None
    environment = current_app.config.get("AI_COST_ENVIRONMENT") or os.environ.get(
        "SABER_AI_ENV"
    )
    if environment != "preview" or not callable(resolver):
        raise CostUnavailable("Anonymous research is not configured safely.")
    subject = resolver(user)
    if not isinstance(subject, str) or not subject.startswith("guest-network:"):
        raise CostUnavailable("Anonymous research identity is unavailable.")
    return subject, "guest-session:" + str(user)


def admission(user, deck, request_id):
    environment = current_app.config.get("AI_COST_ENVIRONMENT") or os.environ.get(
        "SABER_AI_ENV"
    )
    if environment not in {"main", "preview"}:
        raise CostUnavailable("Paid research environment is not configured.")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
        raise ValueError("A client request ID is required.")
    # Stable server-derived lease owner allows exact retries across workers.
    key = hashlib.sha256(f"{user}:{deck}:{request_id}".encode()).hexdigest()
    owner = "web-" + key
    try:
        subject, guest_session = quota_identity(user)
        extra = {"guest_session": guest_session} if guest_session is not None else {}
        turn = authority().begin_turn(environment, subject, key, owner, **extra)
        state = authority().status()["turns"][turn]
        import time

        if state["closed"] or time.time() >= state["deadline"]:
            raise CostUnavailable(
                "This submission ended before it could be saved. Send a new message to retry.",
                409,
                retry_new_submission=True,
            )
    except Exception as exc:
        if isinstance(exc, CostUnavailable):
            raise
        from sabermetrics.ai_cost_control import BudgetDenied

        if isinstance(exc, BudgetDenied):
            reasons = {
                "spending disabled": "Paid research is disabled.",
                "another research turn is active": "Another research turn is active for your account.",
                "daily turns exhausted": "Your daily research turn allowance is exhausted.",
                "daily session turns exhausted": "This preview session has used its daily research allowance.",
                "preview research cooldown": "Please wait 30 seconds between preview research submissions.",
                "budget exhausted": "The research budget is exhausted.",
            }
            raise CostUnavailable(
                reasons.get(str(exc), "Research admission denied: " + str(exc))
                + " Allowances reset at 00:00 UTC."
            ) from exc
        raise CostUnavailable(
            "Shared accounting is temporarily unavailable. Browsing and deck editing remain available."
        ) from exc
    return {"id": turn, "owner": owner}


def close_turn(binding, *, app=None, cancel=False):
    if not binding:
        return
    try:
        control = authority(app)
        method = control.cancel_turn if cancel else control.finish_turn
        method(binding["id"], binding["owner"])
    except Exception:
        # Fail closed: never release unknown reservations locally.
        logging.getLogger(__name__).exception("Could not close paid research admission")


def cost_status(user):
    try:
        status = authority().status()
        environment = current_app.config.get("AI_COST_ENVIRONMENT") or os.environ.get(
            "SABER_AI_ENV"
        )
        config = authority().config
        policy = next(e for e in config.environments if e[0] == environment)
        subject, guest_session = quota_identity(user)
        now = datetime.now(UTC)
        day = now.strftime("%Y-%m-%d")
        exposure = sum(
            a["charge"]
            for a in status["attempts"].values()
            if a["user"] == subject and a["env"] == environment and a["day"] == day
        )
        turns = sum(
            t["user"] == subject and t["env"] == environment and t["day"] == day
            for t in status["turns"].values()
        )
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_reset = (midnight.replace(day=28) + timedelta(days=4)).replace(day=1)
        remaining_micros = max(0, policy[2] - exposure)
        remaining_turns = max(0, policy[3] - turns)
        if guest_session is not None:
            from sabermetrics.ai_cost_control import (
                GUEST_SESSION_MICROS,
                GUEST_SESSION_TURNS,
            )

            session_exposure = sum(
                a["charge"]
                for a in status["attempts"].values()
                if a.get("guest_session") == guest_session and a["day"] == day
            )
            session_turns = sum(
                t.get("guest_session") == guest_session and t["day"] == day
                for t in status["turns"].values()
            )
            remaining_micros = min(
                remaining_micros, max(0, GUEST_SESSION_MICROS - session_exposure)
            )
            remaining_turns = min(
                remaining_turns, max(0, GUEST_SESSION_TURNS - session_turns)
            )
        return {
            "configured": True,
            "kill_switch": bool(
                status.get("killed", False)
                or environment in status.get("killed_environments", [])
            ),
            "remaining_micros": remaining_micros,
            "remaining_turns": remaining_turns,
            "daily_reset_at": (midnight + timedelta(days=1)).isoformat(),
            "monthly_reset_at": month_reset.isoformat(),
        }
    except Exception:
        return {
            "configured": False,
            "message": "Paid research unavailable. Browsing and deck editing remain available.",
        }
