"""Anonymous preview limits remain durable across cookie resets and workers."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from sabermetrics.ai_cost_control import Authority, BudgetConfig, BudgetDenied


@pytest.fixture
def guests(tmp_path, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr("sabermetrics.ai_cost_control.time.time", lambda: now[0])
    policy = BudgetConfig(
        prices=(("model", 1, 1),), credentials=(), authorized=True, input_tokens=100000
    )
    first = Authority(str(tmp_path / "guests.sqlite"), policy, local=True)
    other = Authority(first.dsn, policy, local=True)
    return first, other, now


def test_cookie_reset_does_not_reset_network_allowance(guests):
    first, other, now = guests
    for i in range(5):
        t = first.begin_turn(
            "preview", "guest-network:a", str(i), "owner", guest_session=f"session-{i}"
        )
        other.finish_turn(t, "owner")
        now[0] += 31
    with pytest.raises(BudgetDenied, match="daily turns"):
        other.begin_turn(
            "preview", "guest-network:a", "six", "owner", guest_session="new-cookie"
        )
    # Other networks and main retain independent allowances.
    other.begin_turn(
        "preview", "guest-network:b", "one", "owner", guest_session="other"
    )
    first.begin_turn("main", "member", "one", "member-owner")


def test_network_change_does_not_reset_session_allowance(guests):
    first, other, now = guests
    for i in range(2):
        t = first.begin_turn(
            "preview",
            f"guest-network:{i}",
            str(i),
            "owner",
            guest_session="same-session",
        )
        other.finish_turn(t, "owner")
        now[0] += 31
    with pytest.raises(BudgetDenied, match="session turns"):
        other.begin_turn(
            "preview",
            "guest-network:new",
            "three",
            "owner",
            guest_session="same-session",
        )


def test_session_unknown_reservation_survives_close_and_network_change(guests):
    first, other, now = guests
    t = first.begin_turn(
        "preview", "guest-network:a", "one", "owner", guest_session="session"
    )
    aid = first.admit_attempt(t, "owner", "model", 59900, 100)
    first.finish_turn(t, "owner")
    now[0] += 31
    t2 = other.begin_turn(
        "preview", "guest-network:b", "two", "owner", guest_session="session"
    )
    with pytest.raises(BudgetDenied, match="budget"):
        other.admit_attempt(t2, "owner", "model", 49900, 100)
    assert first.status()["attempts"][aid]["charge"] == 60000
    # Proven lower usage makes only the reconciled remainder available.
    first.reconcile(aid, 10000)
    other.admit_attempt(t2, "owner", "model", 49900, 100)


def test_replay_survives_cooldown_but_new_submission_waits(guests):
    first, other, now = guests
    t = first.begin_turn(
        "preview", "guest-network:a", "one", "owner", guest_session="session"
    )
    first.finish_turn(t, "owner")
    assert (
        other.begin_turn(
            "preview", "guest-network:a", "one", "owner", guest_session="session"
        )
        == t
    )
    with pytest.raises(BudgetDenied, match="cooldown"):
        other.begin_turn(
            "preview", "guest-network:a", "two", "owner", guest_session="new-cookie"
        )
    now[0] += 30
    other.begin_turn(
        "preview", "guest-network:a", "two", "owner", guest_session="new-cookie"
    )


def test_two_workers_admit_only_one_active_network_turn(guests):
    first, other, _ = guests

    def start(pair):
        control, cookie = pair
        try:
            return control.begin_turn(
                "preview", "guest-network:a", cookie, "owner", guest_session=cookie
            )
        except BudgetDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, [(first, "one"), (other, "two")]))
    assert sum(r is not None for r in results) == 1


def test_guest_mode_cannot_be_enabled_on_main(guests):
    first, _, _ = guests
    with pytest.raises(BudgetDenied, match="guest"):
        first.begin_turn(
            "main", "guest-network:a", "one", "owner", guest_session="session"
        )
    assert not first.status()["turns"]
