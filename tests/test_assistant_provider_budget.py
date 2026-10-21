import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_provider_budget import (
    ProviderBudget,
    ProviderOperationCancelled,
    budget_gateway,
)


def test_process_global_limit_across_turns_and_nested_wrappers():
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()
    active = peak = count = 0

    class Gateway:
        provider = "fake"
        model_id = "model"
        ledger = object()

        def close(self):
            return "closed"

        def generate(self, request):
            nonlocal active, peak, count
            with lock:
                active += 1
                count += 1
                peak = max(peak, active)
                if active == 2:
                    entered.set()
            assert release.wait(3)
            with lock:
                active -= 1
            return request

    raw = Gateway()
    wrappers = [
        budget_gateway(budget_gateway(raw), budget=ProviderBudget()) for _ in range(6)
    ]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(w.generate, i) for i, w in enumerate(wrappers)]
        try:
            assert entered.wait(3)
            with lock:
                assert count == peak == 2
        finally:
            release.set()
        assert [f.result(timeout=3) for f in futures] == list(range(6))
    assert peak == 2
    assert wrappers[0].provider == "fake"
    assert wrappers[0].model_id == "model"
    assert wrappers[0].ledger is raw.ledger
    assert wrappers[0].close() == "closed"


def test_queued_cancel_never_calls_provider_and_releases_permits():
    budget = ProviderBudget()
    cancelled = threading.Event()
    checked = threading.Event()
    called = []

    def current():
        checked.set()
        return not cancelled.is_set()

    gateway = budget_gateway(
        SimpleNamespace(generate=lambda r: called.append(r)), check_current=current
    )
    # Hold both permits on different threads (same-thread admission is reentrant).
    release = threading.Event()
    ready = [threading.Event(), threading.Event()]

    def holder(index):
        with budget.operation():
            ready[index].set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=3) as executor:
        holders = [executor.submit(holder, i) for i in range(2)]
        try:
            assert all(event.wait(3) for event in ready)
            future = executor.submit(gateway.generate, "queued")
            assert checked.wait(3)
            cancelled.set()
            with pytest.raises(ProviderOperationCancelled):
                future.result(timeout=3)
            assert not called
        finally:
            release.set()
        for future in holders:
            future.result(timeout=3)
    assert (
        budget_gateway(SimpleNamespace(generate=lambda r: r)).generate("next") == "next"
    )


def test_late_cancellation_discards_result_and_emits_paired_internal_checkpoints():
    events = []
    active = True

    def generate(request):
        nonlocal active
        active = False
        return "stale result"

    wrapper = budget_gateway(
        SimpleNamespace(generate=generate),
        check_current=lambda: active,
        on_checkpoint=events.append,
    )
    with pytest.raises(ProviderOperationCancelled):
        wrapper.generate(SimpleNamespace(call_type="review", prompt_version="v1"))
    assert [e["state"] for e in events] == ["started", "finished"]
    assert {e["type"] for e in events} == {"provider_checkpoint"}
    assert events[0]["call_id"] == events[1]["call_id"]
    assert events[0]["call_type"] == "review"


def test_provider_failure_releases_permit_and_emits_finished():
    events = []

    def fail(_):
        raise ValueError("provider error")

    with pytest.raises(ValueError, match="provider error"):
        budget_gateway(
            SimpleNamespace(generate=fail), on_checkpoint=events.append
        ).generate(None)
    assert [e["state"] for e in events] == ["started", "finished"]
    assert budget_gateway(SimpleNamespace(generate=lambda r: r)).generate(7) == 7


@pytest.fixture
def cost_gateway(tmp_path, monkeypatch):
    """Real gateway/ledger with exact $0.002 responses and no network."""
    import sqlite3

    import httpx
    from pydantic import BaseModel

    from sabermetrics.cedh.cost_ledger import CostLedger
    from sabermetrics.cedh.model_gateway import StructuredRequest
    from sabermetrics.cedh.provider_deepseek import DeepSeekGateway
    from sabermetrics.cedh.settings import ModelPricing, load_cedh_settings

    class Answer(BaseModel):
        ok: bool

    monkeypatch.setenv("HF_TOKEN", "test-token")
    settings = load_cedh_settings()
    # Production ceiling is a property reading legacy settings, not a model field.
    settings = SimpleNamespace(
        monthly_cost_ceiling_usd=0.010,
        model=settings.model.model_copy(
            update={
                "pricing": ModelPricing(input=0, cached_input=0, output=1),
                "max_retries": 0,
                "max_validation_retries": 1,
            }
        ),
    )
    clients = []

    def make(name, handler, spend=0.009):
        path = tmp_path / name
        if not path.exists():
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE cost_log (
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    call_type TEXT, model TEXT, input_tokens INTEGER,
                    cached_input_tokens INTEGER, output_tokens INTEGER,
                    cost_usd REAL, request_id TEXT, user_id TEXT,
                    deck_id TEXT, metadata TEXT)""")
                conn.execute("INSERT INTO cost_log(cost_usd) VALUES (?)", (spend,))
        client = httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        )
        clients.append(client)
        raw = DeepSeekGateway(settings, db_path=str(path), client=client)
        return budget_gateway(budget_gateway(raw)), CostLedger(path)

    def response(content='{"ok":true}'):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"completion_tokens": 2000},
            },
        )

    yield make, response, StructuredRequest(
        prompt_version="budget-v1", system="system", user="user", schema=Answer
    )
    for client in clients:
        client.close()


def test_shared_actual_ledger_admits_only_one_call_near_ceiling(cost_gateway):
    from sabermetrics.errors import LLMCostCeilingExceeded

    make, response, request = cost_gateway
    first_entered = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()

    def first_handler(_):
        first_entered.set()
        assert release.wait(3)
        return response()

    def second_handler(_):
        second_entered.set()
        return response()

    first, ledger = make("shared.db", first_handler)
    # Distinct gateway, ledger and wrapper instances, same real database.
    second, _ = make("./shared.db", second_handler)

    def run_second():
        second_started.set()
        return second.generate(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(first.generate, request)
        try:
            assert first_entered.wait(3)
            two = executor.submit(run_second)
            assert second_started.wait(3)
            assert not second_entered.wait(0.1)
        finally:
            release.set()
        assert one.result(timeout=3).usage.cost_usd == 0.002
        with pytest.raises(LLMCostCeilingExceeded):
            two.result(timeout=3)
    assert not second_entered.is_set()
    # Existing sequential contract: one call can cross the stop threshold.
    assert ledger.monthly_spend() == pytest.approx(0.011)


def test_independent_ledgers_still_allow_two_provider_operations(cost_gateway):
    make, response, request = cost_gateway
    both = threading.Barrier(2)

    def handler(_):
        both.wait(timeout=3)
        return response()

    first, _ = make("one.db", handler)
    second, _ = make("two.db", handler)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(g.generate, request) for g in (first, second)]
        assert all(f.result(timeout=3).value.ok for f in futures)


@pytest.mark.parametrize("failure", ["validation", "transport"])
def test_ledger_admission_releases_on_failure_and_preserves_accounting(
    cost_gateway, failure
):
    import httpx

    from sabermetrics.cedh.errors import ModelProviderError, ModelValidationError

    make, response, request = cost_gateway
    attempts = []

    def handler(_):
        attempts.append(1)
        if failure == "transport":
            return httpx.Response(400, text="bad request")
        return response("invalid json")

    failed, ledger = make("failure.db", handler, spend=0)
    with pytest.raises(
        ModelProviderError if failure == "transport" else ModelValidationError
    ):
        failed.generate(request)
    assert len(attempts) == (1 if failure == "transport" else 2)
    assert ledger.monthly_spend() == pytest.approx(
        0 if failure == "transport" else 0.004
    )
    # A different thread must acquire the reservation after the failure.
    succeeding, _ = make("failure.db", lambda _: response())
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(succeeding.generate, request).result(timeout=3).value.ok
    assert ledger.monthly_spend() == pytest.approx(
        0.002 if failure == "transport" else 0.006
    )


@pytest.mark.parametrize("signal", ["cancel", "deadline"])
@pytest.mark.parametrize("release_first", [False, True])
def test_actual_gateway_ledger_wait_checks_current_before_paid_transport(
    cost_gateway, monkeypatch, signal, release_first
):
    from sabermetrics.cedh import cost_ledger

    make, response, request = cost_gateway
    entered = threading.Event()
    release = threading.Event()
    queued = threading.Event()
    stale = threading.Event()
    paid = []
    checkpoints = []

    def handler(_):
        entered.set()
        assert release.wait(3)
        return response()

    first, ledger = make("cancel-shared.db", handler, spend=0)
    second, _ = make(
        "cancel-shared.db", lambda _: paid.append(1) or response(), spend=0
    )

    def check():
        if stale.is_set() and signal == "deadline":
            raise TimeoutError("operation deadline")
        return not stale.is_set()

    # The outer hook must survive two nested wrappers with no checks of their own.
    second = budget_gateway(
        second, check_current=check, on_checkpoint=checkpoints.append
    )
    error = TimeoutError if signal == "deadline" else ProviderOperationCancelled
    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(first.generate, request)
        try:
            assert entered.wait(3)
            key = str(ledger.db_path.resolve())
            underlying = cost_ledger._ADMISSION_LOCKS[key]

            class ObservedLock:
                def acquire(self, **kwargs):
                    acquired = underlying.acquire(**kwargs)
                    if not acquired:
                        queued.set()
                    return acquired

                def release(self):
                    underlying.release()

            # Keep a strong reference: the production registry uses weak values.
            observed = ObservedLock()
            monkeypatch.setitem(cost_ledger._ADMISSION_LOCKS, key, observed)
            two = executor.submit(second.generate, request)
            assert queued.wait(3)
            stale.set()
            if release_first:
                release.set()
            with pytest.raises(error):
                two.result(timeout=3)
            assert paid == []
        finally:
            release.set()
        one.result(timeout=3)
    assert ledger.monthly_spend() == pytest.approx(0.002)
    assert [e["state"] for e in checkpoints] == ["started", "finished"]
    assert checkpoints[0]["call_id"] == checkpoints[1]["call_id"]
    # Neither the failed admission nor its hook leaks into a subsequent call.
    assert second.gateway.generate(request).value.ok
    assert paid == [1]


def test_actual_gateway_rechecks_after_ledger_acquisition(cost_gateway, monkeypatch):
    make, response, request = cost_gateway
    stale = threading.Event()
    paid = []
    wrapped, ledger = make(
        "acquired.db", lambda _: paid.append(1) or response(), spend=0
    )
    raw = wrapped.gateway.gateway
    original = raw._ledger.check_ceiling

    def ceiling(limit):
        original(limit)
        stale.set()

    monkeypatch.setattr(raw._ledger, "check_ceiling", ceiling)
    with pytest.raises(ProviderOperationCancelled):
        budget_gateway(wrapped, check_current=lambda: not stale.is_set()).generate(
            request
        )
    assert not paid
    assert ledger.monthly_spend() == 0
    monkeypatch.setattr(raw._ledger, "check_ceiling", original)
    assert wrapped.generate(request).value.ok


@pytest.mark.parametrize("retry", ["validation", "transport"])
def test_actual_gateway_rechecks_before_retry_and_accounts_paid_response(
    cost_gateway, monkeypatch, retry
):
    import httpx

    from sabermetrics.cedh import provider_deepseek

    make, response, request = cost_gateway
    stale = threading.Event()
    attempts = []

    def handler(_):
        attempts.append(1)
        stale.set()
        return (
            response("invalid json") if retry == "validation" else httpx.Response(503)
        )

    wrapped, ledger = make("retry-cancel.db", handler, spend=0)
    raw = wrapped.gateway.gateway
    raw._settings.model.max_retries = 1
    monkeypatch.setattr(provider_deepseek.time, "sleep", lambda _: None)
    with pytest.raises(ProviderOperationCancelled):
        budget_gateway(wrapped, check_current=lambda: not stale.is_set()).generate(
            request
        )
    assert attempts == [1]
    assert ledger.monthly_spend() == pytest.approx(
        0.002 if retry == "validation" else 0
    )
