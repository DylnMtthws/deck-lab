from concurrent.futures import ThreadPoolExecutor

import pytest

from sabermetrics.ai_cost_control import (
    Authority,
    BudgetConfig,
    BudgetDenied,
    bind_turn,
    paid_attempt,
)


def authority(tmp_path, **kwargs):
    return Authority(
        str(tmp_path / "budget.db"),
        BudgetConfig(
            prices=(("model", 1, 2),), credentials=(), authorized=True, **kwargs
        ),
        local=True,
    )


def test_owner_idempotency_and_closed(tmp_path):
    a = authority(tmp_path)
    t = a.begin_turn("main", "user", "key", "owner")
    assert a.begin_turn("main", "user", "key", "owner") == t
    with pytest.raises(BudgetDenied):
        a.begin_turn("main", "user", "key", "other")
    a.cancel_turn(t, "owner")
    with pytest.raises(BudgetDenied):
        a.admit_attempt(t, "owner", "model", 100, 100)


def test_concurrent_combined_reservations(tmp_path):
    a = authority(tmp_path, monthly=600, turn_cost=600)
    ids = [a.begin_turn(e, "u-" + e, e, "o") for e in ("main", "preview")]

    def attempt(t):
        try:
            return a.admit_attempt(t, "o", "model", 100, 200)
        except BudgetDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ids))
    assert sum(r is not None for r in results) == 1
    aid = next(r for r in results if r)
    a.reconcile(aid)
    assert a.status()["attempts"][aid]["charge"] == 500
    a.reconcile(aid, 200)
    assert a.status()["attempts"][aid]["charge"] == 200


def test_overrun_kills_and_outbox(tmp_path):
    a = authority(tmp_path)
    t = a.begin_turn("main", "u", "k", "o")
    aid = a.admit_attempt(t, "o", "model", 10, 10)
    with pytest.raises(BudgetDenied):
        a.reconcile(aid, 31)
    assert a.status()["killed"]
    assert a.alerts()
    a.reset()
    assert a.status()["attempts"][aid]["charge"] == 31


def test_config_and_context_fail_closed(tmp_path, monkeypatch):
    a = authority(tmp_path)
    with pytest.raises(BudgetDenied):
        Authority(
            str(tmp_path / "budget.db"),
            BudgetConfig(prices=(("other", 1, 1),), credentials=()),
            local=True,
        )
    monkeypatch.delenv("SABER_AI_ENV", raising=False)
    with pytest.raises(BudgetDenied), paid_attempt("model", {}, 1):
        pytest.fail("unreachable")
    t = a.begin_turn("main", "u", "k", "o")
    with bind_turn(a, t, "o"), paid_attempt("model", {}, 1):
        pass
    assert len(a.status()["attempts"]) == 1


def test_limits_expiry_and_no_refunds(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr("sabermetrics.ai_cost_control.time.time", lambda: clock[0])
    a = authority(tmp_path, attempts=1, tools=1)
    t = a.begin_turn("main", "u", "k", "o")
    a.tool_iteration(t, "o")
    with pytest.raises(BudgetDenied):
        a.tool_iteration(t, "o")
    with pytest.raises(BudgetDenied):
        a.admit_attempt(t, "o", "unknown", 1, 1)
    aid = a.admit_attempt(t, "o", "model", 1, 1)
    with pytest.raises(BudgetDenied):
        a.admit_attempt(t, "o", "model", 1, 1)
    clock[0] += 181
    a.cancel_turn(t, "o")
    a.reconcile(aid)
    assert a.status()["attempts"][aid]["charge"] == 3


def test_daily_turns_and_explicit_local_only(tmp_path):
    with pytest.raises(ValueError):
        Authority(
            str(tmp_path / "x"), BudgetConfig(prices=(("m", 1, 1),), credentials=())
        )
    a = authority(tmp_path)
    for i in range(5):
        turn = a.begin_turn("preview", "u", str(i), "o")
        a.finish_turn(turn, "o")
    with pytest.raises(BudgetDenied):
        a.begin_turn("preview", "u", "sixth", "o")
