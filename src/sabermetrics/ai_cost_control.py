"""Shared fail-closed spending authority. Money is integer microdollars."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass


class BudgetDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetConfig:
    # (model, input microdollars/token, output microdollars/token). Rates round UP.
    prices: tuple[tuple[str, int, int], ...]
    # (environment, provider, credential environment variable)
    credentials: tuple[tuple[str, str, str], ...]
    authorized: bool = False
    monthly: int = 15_000_000
    environments: tuple[tuple[str, int, int, int], ...] = (
        ("main", 12_000_000, 500_000, 10),
        ("preview", 3_000_000, 200_000, 5),
    )
    turn_cost: int = 100_000
    attempts: int = 12
    tools: int = 8
    input_tokens: int = 32_000
    output_tokens: int = 2_000
    deadline: int = 180

    def __post_init__(self):
        if type(self.authorized) is not bool:
            raise TypeError("authorization must be explicit boolean")
        if (
            not isinstance(self.prices, tuple)
            or not isinstance(self.credentials, tuple)
            or not isinstance(self.environments, tuple)
        ):
            raise TypeError("configuration must be immutable tuples")
        if not self.prices or len({p[0] for p in self.prices}) != len(self.prices):
            raise ValueError("explicit unique model prices required")
        for row in (*self.prices, *self.credentials, *self.environments):
            if not isinstance(row, tuple):
                raise TypeError("immutable rows required")
        values = [
            self.monthly,
            self.turn_cost,
            self.attempts,
            self.tools,
            self.input_tokens,
            self.output_tokens,
            self.deadline,
        ]
        values += [v for _, i, o in self.prices for v in (i, o)]
        values += [v for _, m, d, t in self.environments for v in (m, d, t)]
        if any(type(v) is not int or v <= 0 for v in values):
            raise ValueError("positive integer limits and conservative prices required")
        if {e[0] for e in self.environments} != {"main", "preview"} or len(
            self.environments
        ) != 2:
            raise ValueError("main and preview limits required")
        names = [r[2] for r in self.credentials]
        if len(set(names)) != len(names) or any(
            not n or n in {"HF_TOKEN", "ANTHROPIC_API_KEY", "TYPESAFE_API_KEY"}
            for n in names
        ):
            raise ValueError("distinct environment-specific credential names required")


class Authority:
    """Single locked state row provides serializable cross-worker admission.

    JSON state favors auditability over throughput at this small spending ceiling.
    Back up this database; deleting it resets financial history.
    """

    def __init__(self, dsn: str, config: BudgetConfig, *, local: bool = False):
        self.config = config
        self.dsn = dsn
        self.local = local
        if not local and not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("shared PostgreSQL required")
        if local and (not dsn or dsn == ":memory:"):
            raise ValueError("shared local file required")
        self.fingerprint = hashlib.sha256(
            json.dumps(asdict(config), sort_keys=True).encode()
        ).hexdigest()
        with self._transaction(initialize=True):
            pass

    @contextmanager
    def _transaction(self, initialize=False, readonly=False):
        if self.local:
            conn = sqlite3.connect(self.dsn, timeout=30)
        else:
            import psycopg

            conn = psycopg.connect(self.dsn, connect_timeout=5)
        try:
            if self.local:
                conn.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
            if not self.local:
                conn.execute("SET LOCAL lock_timeout = '5s'")
                conn.execute("SET LOCAL statement_timeout = '10s'")
            if initialize:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS ai_budget_authority (id INTEGER PRIMARY KEY, state TEXT NOT NULL)"
                )
                initial = json.dumps(
                    dict(
                        config=self.fingerprint,
                        killed=False,
                        killed_environments=[],
                        turns={},
                        attempts={},
                        alerts=[],
                    )
                )
                if self.local:
                    conn.execute(
                        "INSERT OR IGNORE INTO ai_budget_authority VALUES (1, ?)",
                        (initial,),
                    )
                else:
                    conn.execute(
                        "INSERT INTO ai_budget_authority VALUES (1, %s) ON CONFLICT DO NOTHING",
                        (initial,),
                    )
            row = conn.execute(
                "SELECT state FROM ai_budget_authority WHERE id=1"
                + ("" if self.local or readonly else " FOR UPDATE")
            ).fetchone()
            state = json.loads(row[0])
            if state["config"] != self.fingerprint:
                raise BudgetDenied("authority config mismatch")
            yield state
            if not readonly:
                conn.execute(
                    "UPDATE ai_budget_authority SET state="
                    + ("?" if self.local else "%s")
                    + " WHERE id=1",
                    (json.dumps(state),),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _live(self, s, turn_id, owner):
        t = s["turns"][turn_id]
        if (
            not self.config.authorized
            or s["killed"]
            or t["env"] in s.get("killed_environments", [])
            or t["owner"] != owner
            or t["closed"]
            or time.time() >= t["deadline"]
        ):
            raise BudgetDenied("turn unavailable")
        return t

    def begin_turn(self, environment, user_id, idempotency_key, owner):
        if not all(
            isinstance(v, str) and v
            for v in (environment, user_id, idempotency_key, owner)
        ):
            raise BudgetDenied("trusted identities required")
        with self._transaction() as s:
            env = next(
                (r for r in self.config.environments if r[0] == environment), None
            )
            if (
                not env
                or not self.config.authorized
                or s["killed"]
                or environment in s.get("killed_environments", [])
            ):
                raise BudgetDenied("spending disabled")
            for tid, t in s["turns"].items():
                if (t["env"], t["user"], t["key"]) == (
                    environment,
                    user_id,
                    idempotency_key,
                ):
                    if t["owner"] != owner:
                        raise BudgetDenied("owner excluded")
                    return tid
            if any(
                t["user"] == user_id and not t["closed"] and time.time() < t["deadline"]
                for t in s["turns"].values()
            ):
                raise BudgetDenied("another research turn is active")
            day = time.strftime("%Y-%m-%d", time.gmtime())
            if (
                sum(
                    t["env"] == environment and t["user"] == user_id and t["day"] == day
                    for t in s["turns"].values()
                )
                >= env[3]
            ):
                raise BudgetDenied("daily turns exhausted")
            tid = uuid.uuid4().hex
            s["turns"][tid] = dict(
                env=environment,
                user=user_id,
                key=idempotency_key,
                owner=owner,
                day=day,
                closed=False,
                deadline=time.time() + self.config.deadline,
                tools=0,
            )
            return tid

    def finish_turn(self, turn_id, owner):
        with self._transaction() as s:
            t = s["turns"][turn_id]
            if t["owner"] != owner:
                raise BudgetDenied("owner excluded")
            t["closed"] = True

    cancel_turn = finish_turn

    def tool_iteration(self, turn_id, owner):
        with self._transaction() as s:
            t = self._live(s, turn_id, owner)
            if t["tools"] >= self.config.tools:
                raise BudgetDenied("tool limit")
            t["tools"] += 1

    def admit_attempt(self, turn_id, owner, model, input_tokens, output_tokens):
        with self._transaction() as s:
            t = self._live(s, turn_id, owner)
            price = next((r for r in self.config.prices if r[0] == model), None)
            if (
                not price
                or type(input_tokens) is not int
                or type(output_tokens) is not int
                or not 0 <= input_tokens <= self.config.input_tokens
                or not 0 < output_tokens <= self.config.output_tokens
            ):
                raise BudgetDenied("model/token limit")
            bound = input_tokens * price[1] + output_tokens * price[2]
            rows = list(s["attempts"].values())
            own = [a for a in rows if a["turn"] == turn_id]
            month = time.strftime("%Y-%m", time.gmtime())
            env = next(e for e in self.config.environments if e[0] == t["env"])
            checks = [
                (rows, self.config.monthly, lambda a: a["month"] == month),
                (rows, env[1], lambda a: a["month"] == month and a["env"] == t["env"]),
                (
                    rows,
                    env[2],
                    lambda a: (
                        a["day"] == time.strftime("%Y-%m-%d", time.gmtime())
                        and a["env"] == t["env"]
                        and a["user"] == t["user"]
                    ),
                ),
                (own, self.config.turn_cost, lambda a: True),
            ]
            if len(own) >= self.config.attempts or any(
                sum(a["charge"] for a in rs if pred(a)) + bound > cap
                for rs, cap, pred in checks
            ):
                raise BudgetDenied("budget exhausted")
            aid = uuid.uuid4().hex
            s["attempts"][aid] = dict(
                turn=turn_id,
                env=t["env"],
                user=t["user"],
                day=time.strftime("%Y-%m-%d", time.gmtime()),
                month=month,
                bound=bound,
                charge=bound,
                settled=False,
            )
            scopes = [
                ("combined", month),
                (f"environment:{t['env']}", month),
                (
                    f"user:{t['env']}:{t['user']}",
                    time.strftime("%Y-%m-%d", time.gmtime()),
                ),
                (f"turn:{turn_id}", turn_id),
            ]
            for (scope, period), (rs, cap, pred) in zip(scopes, checks):
                exposure = sum(a["charge"] for a in rs if pred(a)) + bound
                for threshold in (50, 80, 95):
                    key = f"{scope}:{period}:{threshold}"
                    if exposure * 100 >= cap * threshold and not any(
                        alert.get("key") == key for alert in s["alerts"]
                    ):
                        s["alerts"].append(
                            dict(
                                id=uuid.uuid4().hex,
                                key=key,
                                scope=scope,
                                period=period,
                                threshold=threshold,
                                exposure_micros=exposure,
                                limit_micros=cap,
                                acknowledged=False,
                            )
                        )
            return aid

    def reconcile(self, attempt_id, cost_micros=None):
        exceeded = False
        with self._transaction() as s:
            a = s["attempts"][attempt_id]
            if cost_micros is None:
                return  # unknown remains reserved, including after expiry
            if type(cost_micros) is not int or cost_micros < 0:
                raise BudgetDenied("invalid usage")
            if a["settled"]:
                if a["charge"] != cost_micros:
                    raise BudgetDenied("conflicting reconciliation")
                return
            a.update(charge=cost_micros, settled=True)
            if cost_micros > a["bound"]:
                s["killed"] = True
                s["alerts"].append(
                    dict(
                        id=uuid.uuid4().hex,
                        reason="provider exceeded reservation",
                        attempt=attempt_id,
                        acknowledged=False,
                    )
                )
                exceeded = True
        if exceeded:
            raise BudgetDenied("provider exceeded reservation; spending disabled")

    def status(self):
        with self._transaction(readonly=True) as s:
            return s

    def set_kill_switch(self, enabled=True, environment=None):
        if type(enabled) is not bool:
            raise BudgetDenied("kill switch requires a boolean")
        if environment is not None and environment not in {
            e[0] for e in self.config.environments
        }:
            raise BudgetDenied("unknown environment")
        with self._transaction() as s:
            if environment is None:
                s["killed"] = enabled
            else:
                killed = set(s.get("killed_environments", []))
                if enabled:
                    killed.add(environment)
                else:
                    killed.discard(environment)
                s["killed_environments"] = sorted(killed)

    def reset(self):
        self.set_kill_switch(False)

    def alerts(self):
        return [a for a in self.status()["alerts"] if not a["acknowledged"]]

    def ack_alert(self, alert_id):
        with self._transaction() as s:
            next(a for a in s["alerts"] if a["id"] == alert_id)["acknowledged"] = True

    def claim_alert_email(self, alert_id, sender, recipient):
        """Persist a stable payload and claim before performing external IO."""
        with self._transaction() as state:
            alert = next(a for a in state["alerts"] if a["id"] == alert_id)
            delivery = alert.setdefault("email_delivery", {})
            now = time.time()
            if (
                alert["acknowledged"]
                or delivery.get("needs_review")
                or delivery.get("accepted_id")
            ):
                return None
            # Resend deduplicates for 24h. Stop before that window expires;
            # an ambiguous old send requires operator reconciliation, not resend.
            if (
                "first_attempt_at" in delivery
                and now - delivery["first_attempt_at"] >= 23 * 3600
            ):
                delivery["needs_review"] = True
                return None
            if now < delivery.get("lease_until", 0) or now < delivery.get(
                "retry_at", 0
            ):
                return None
            if "payload" not in delivery:
                scope = alert.get("scope", "combined")
                if "threshold" in alert:
                    subject = f"Deck Lab AI budget: {scope} at {alert['threshold']}%"
                    text = (
                        f"Budget scope: {scope}\nPeriod: {alert['period']}\n"
                        f"Alert threshold: {alert['threshold']}%\n"
                        f"Spent or reserved: ${alert['exposure_micros'] / 1_000_000:.6f}\n"
                        f"Budget limit: ${alert['limit_micros'] / 1_000_000:.6f}\n\n"
                        "Paid calls stop when a reservation would exceed a limit. "
                        "Review usage or disable paid research in the admin cost controls. "
                        "Browsing and deck editing remain available."
                    )
                else:
                    subject = "Deck Lab AI spending automatically disabled"
                    text = "A provider exceeded its reserved cost. Paid AI has been disabled. Review the admin cost controls before re-enabling it."
                delivery["payload"] = {
                    "from": sender,
                    "to": [recipient],
                    "subject": subject,
                    "text": text,
                }
                delivery["first_attempt_at"] = now
            delivery.update(
                lease=uuid.uuid4().hex,
                lease_until=now + 120,
                attempts=delivery.get("attempts", 0) + 1,
            )
            return {"lease": delivery["lease"], "payload": delivery["payload"]}

    def finish_alert_email(self, alert_id, lease, provider_id=None):
        with self._transaction() as state:
            alert = next(a for a in state["alerts"] if a["id"] == alert_id)
            delivery = alert["email_delivery"]
            if delivery.get("lease") != lease:
                raise BudgetDenied("stale email claim")
            if provider_id:
                delivery.update(accepted_id=provider_id, accepted_at=time.time())
                alert["acknowledged"] = True
            else:
                delivery["retry_at"] = time.time() + min(
                    3600, 60 * 2 ** min(delivery["attempts"] - 1, 6)
                )
            delivery["lease_until"] = 0


_context = contextvars.ContextVar("ai_budget_turn", default=None)


def bounded_output_tokens(requested):
    """Apply the shared output limit to the actual outbound provider request."""
    if type(requested) is not int or requested <= 0:
        raise BudgetDenied("model/token limit")
    context = _context.get()
    return min(requested, context[0].config.output_tokens) if context else requested


def consume_tool_iteration():
    """Meter actual tool execution, including nested runtime-owned work."""
    context = _context.get()
    if context:
        authority, turn_id, owner = context
        authority.tool_iteration(turn_id, owner)


@contextmanager
def bind_turn(authority, turn_id, owner):
    token = _context.set((authority, turn_id, owner))
    try:
        yield
    finally:
        _context.reset(token)


def credential_name(provider, legacy):
    ctx = _context.get()
    if ctx:
        authority, tid, _ = ctx
        env = authority.status()["turns"][tid]["env"]
        names = [
            n for e, p, n in authority.config.credentials if e == env and p == provider
        ]
        if len(names) != 1:
            raise BudgetDenied("credential mapping missing")
        return names[0]
    if os.environ.get("SABER_AI_ENV") not in {"local", "test"}:
        raise BudgetDenied("paid calls require bound authority context")
    return legacy


@contextmanager
def paid_attempt(model, payload, output_tokens):
    ctx = _context.get()
    if not ctx:
        if os.environ.get("SABER_AI_ENV") not in {"local", "test"}:
            raise BudgetDenied("paid calls require bound authority context")
        yield
        return
    a, tid, owner = ctx
    # Reserve full upper bound and retain it: usage reconciliation is an explicit
    # trusted operation, never inferred from missing/partial provider metadata.
    if model.startswith("jev-"):
        raise BudgetDenied(
            "Jev transport has no enforceable output bound; protected spending disabled"
        )
    aid = a.admit_attempt(
        tid,
        owner,
        model,
        len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        output_tokens,
    )
    yield AttemptReceipt(a, aid, model)


@dataclass(frozen=True)
class AttemptReceipt:
    authority: Authority
    attempt_id: str
    model: str

    @property
    def remaining_seconds(self):
        state = self.authority.status()
        turn_id = state["attempts"][self.attempt_id]["turn"]
        turn = state["turns"][turn_id]
        self.authority._live(state, turn_id, turn["owner"])
        remaining = turn["deadline"] - time.time()
        if remaining <= 0:
            raise BudgetDenied("turn unavailable")
        return remaining

    def usage(self, input_tokens, output_tokens):
        """Conservative rates must cover cached writes and reasoning too."""
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            return  # retain upper bound for unusable metadata
        _, i, o = next(p for p in self.authority.config.prices if p[0] == self.model)
        self.authority.reconcile(self.attempt_id, input_tokens * i + output_tokens * o)
