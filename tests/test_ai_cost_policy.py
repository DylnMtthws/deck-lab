"""Financial policy regressions use independent authorities and fake prices."""

import pytest

from sabermetrics.ai_cost_control import Authority, BudgetConfig, BudgetDenied


def make_control(tmp_path, **overrides):
    policy = dict(
        prices=(("model", 1, 1),),
        credentials=(),
        authorized=True,
        monthly=1000,
        environments=(("main", 800, 500, 10), ("preview", 200, 100, 5)),
        turn_cost=500,
    )
    policy.update(overrides)
    return Authority(
        str(tmp_path / "policy.sqlite"), BudgetConfig(**policy), local=True
    )


def test_preview_cannot_consume_main_allocation(tmp_path):
    control = make_control(tmp_path)
    for user in ("a", "b"):
        turn = control.begin_turn("preview", user, "one", "owner")
        control.admit_attempt(turn, "owner", "model", 50, 50)
        control.finish_turn(turn, "owner")
    turn = control.begin_turn("preview", "c", "one", "owner")
    with pytest.raises(BudgetDenied, match="budget"):
        control.admit_attempt(turn, "owner", "model", 1, 1)
    main = control.begin_turn("main", "d", "one", "owner")
    control.admit_attempt(main, "owner", "model", 200, 200)


def test_daily_dollars_do_not_reset_with_new_turn(tmp_path):
    control = make_control(tmp_path)
    first = control.begin_turn("main", "user", "one", "owner")
    control.admit_attempt(first, "owner", "model", 200, 200)
    control.finish_turn(first, "owner")
    second = control.begin_turn("main", "user", "two", "owner")
    with pytest.raises(BudgetDenied, match="budget"):
        control.admit_attempt(second, "owner", "model", 100, 100)


def test_alerts_are_durable_deduplicated_and_include_reservations(tmp_path):
    control = make_control(tmp_path)
    turn = control.begin_turn("main", "user", "one", "owner")
    attempt = control.admit_attempt(turn, "owner", "model", 240, 240)
    alerts = control.alerts()
    assert {a["threshold"] for a in alerts if a["scope"].startswith("user:")} == {
        50,
        80,
        95,
    }
    control.reconcile(attempt, 100)
    control.admit_attempt(turn, "owner", "model", 190, 190)
    assert len(control.alerts()) == len(alerts)
    control.ack_alert(alerts[0]["id"])
    other = make_control(tmp_path)
    assert len(other.alerts()) == len(alerts) - 1


def test_environment_shutdown_is_shared_but_does_not_disable_other_env(tmp_path):
    first, other = make_control(tmp_path), make_control(tmp_path)
    main = first.begin_turn("main", "a", "one", "owner")
    preview = first.begin_turn("preview", "b", "one", "owner")
    other.set_kill_switch(True, environment="main")
    with pytest.raises(BudgetDenied):
        first.admit_attempt(main, "owner", "model", 1, 1)
    first.admit_attempt(preview, "owner", "model", 1, 1)
    other.set_kill_switch(True)
    with pytest.raises(BudgetDenied):
        first.admit_attempt(preview, "owner", "model", 1, 1)


def test_accounting_failure_prevents_transport(tmp_path, monkeypatch):
    from sabermetrics.ai_cost_control import bind_turn, paid_attempt

    control = make_control(tmp_path)
    turn = control.begin_turn("main", "user", "one", "owner")

    def unavailable(*args, **kwargs):
        raise OSError("accounting unavailable")

    monkeypatch.setattr(control, "_transaction", unavailable)
    with (
        bind_turn(control, turn, "owner"),
        pytest.raises(OSError),
        paid_attempt("model", {"messages": []}, 100),
    ):
        pytest.fail("transport must never run without durable admission")


def test_fractional_rates_round_up_and_admit_full_schema(tmp_path):
    from dataclasses import replace

    from sabermetrics.ai_cost_control import AttemptReceipt

    base = BudgetConfig(
        prices=(("model", 600, 900),),
        credentials=(),
        authorized=True,
        price_scale=1000,
        input_tokens=128000,
        output_tokens=4096,
    )
    control = Authority(str(tmp_path / "fractional.sqlite"), base, local=True)
    tid = control.begin_turn("preview", "user", "fractional", "owner")
    aid = control.admit_attempt(tid, "owner", "model", 128000, 4096)
    assert control.status()["attempts"][aid]["bound"] == 80487
    AttemptReceipt(control, aid, "model").usage(1, 1)
    assert control.status()["attempts"][aid]["charge"] == 2
    assert control.status()["attempts"][aid]["settled"]
    with pytest.raises(ValueError):
        replace(base, price_scale=0)
    with pytest.raises(ValueError):
        replace(base, price_scale=True)
