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


def test_ledger_waiter_does_not_starve_independent_ledger_or_exceed_two_transports(
    cost_gateway, monkeypatch
):
    from sabermetrics.cedh import cost_ledger

    make, response, request = cost_gateway
    release = threading.Event()
    entered_a = threading.Event()
    entered_c = threading.Event()
    entered_d = threading.Event()
    queued_b = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def handler(entered):
        def transport(_):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            entered.set()
            try:
                assert release.wait(3)
                return response()
            finally:
                with lock:
                    active -= 1

        return transport

    a, ledger = make("starvation.db", handler(entered_a), spend=0)
    b, _ = make("starvation.db", handler(threading.Event()), spend=0)
    c, _ = make("independent.db", handler(entered_c), spend=0)
    d, _ = make("third.db", handler(entered_d), spend=0)
    events = []
    b = budget_gateway(b, on_checkpoint=events.append)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(a.generate, request)]
        try:
            assert entered_a.wait(3)
            key = str(ledger.db_path.resolve())
            underlying = cost_ledger._ADMISSION_LOCKS[key]

            class ObservedLock:
                def acquire(self, **kwargs):
                    acquired = underlying.acquire(**kwargs)
                    if not acquired:
                        queued_b.set()
                    return acquired

                def release(self):
                    underlying.release()

            observed = ObservedLock()
            monkeypatch.setitem(cost_ledger._ADMISSION_LOCKS, key, observed)
            futures.append(executor.submit(b.generate, request))
            assert queued_b.wait(3)
            futures.append(executor.submit(c.generate, request))
            # A remains in transport while C enters despite B's same-DB wait.
            assert entered_c.wait(3)
            futures.append(executor.submit(d.generate, request))
            assert not entered_d.wait(0.1)
            assert peak == 2
        finally:
            release.set()
        assert all(f.result(timeout=3).value.ok for f in futures)
    assert peak == 2
    phases = events[-1]["phase_timings"]
    assert {p["phase"] for p in phases} >= {
        "ledger_wait",
        "provider_permit_wait",
        "transport",
        "checkpoint_callback",
    }
    ledger_wait = next(p for p in phases if p["phase"] == "ledger_wait")
    permit_wait = next(p for p in phases if p["phase"] == "provider_permit_wait")
    transport = next(p for p in phases if p["phase"] == "transport")
    assert ledger_wait["finished_monotonic"] <= permit_wait["started_monotonic"]
    assert permit_wait["finished_monotonic"] <= transport["started_monotonic"]
    assert all(p["duration_ms"] >= 0 for p in phases)


@pytest.mark.parametrize("cancel_after_acquire", [False, True])
def test_actual_gateway_permit_wait_and_acquisition_check_scoped_cancellation(
    cost_gateway, monkeypatch, cancel_after_acquire
):
    from sabermetrics import assistant_provider_budget as budget_module

    make, response, request = cost_gateway
    stale = threading.Event()
    waiting = threading.Event()
    release = threading.Event()
    ready = [threading.Event(), threading.Event()]
    paid = []
    wrapped, ledger = make(
        "permit-cancel.db", lambda _: paid.append(1) or response(), spend=0
    )
    wrapped = budget_gateway(wrapped, check_current=lambda: not stale.is_set())
    underlying = budget_module._PERMITS

    class ObservedPermits:
        def acquire(self, **kwargs):
            acquired = underlying.acquire(**kwargs)
            if threading.current_thread().name.startswith("permit-waiter"):
                if acquired and cancel_after_acquire:
                    stale.set()
                elif not acquired:
                    waiting.set()
            return acquired

        def release(self):
            underlying.release()

    monkeypatch.setattr(budget_module, "_PERMITS", ObservedPermits())

    def holder(index):
        with ProviderBudget().operation():
            ready[index].set()
            assert release.wait(3)

    with (
        ThreadPoolExecutor(max_workers=2) as holders,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="permit-waiter") as waiter,
    ):
        held = [holders.submit(holder, i) for i in range(2)]
        try:
            assert all(event.wait(3) for event in ready)
            pending = waiter.submit(wrapped.generate, request)
            assert waiting.wait(3)
            if cancel_after_acquire:
                release.set()
            else:
                stale.set()
            with pytest.raises(ProviderOperationCancelled):
                pending.result(timeout=3)
        finally:
            release.set()
        for future in held:
            future.result(timeout=3)
    assert not paid
    assert ledger.monthly_spend() == 0
    stale.clear()
    # The scoped cancellation hook and the cancelled permit both released.
    assert wrapped.gateway.generate(request).value.ok


def test_paid_success_is_recorded_before_stale_discard_with_internal_timings(
    cost_gateway,
):
    import json
    import sqlite3

    make, response, request = cost_gateway
    stale = threading.Event()

    def handler(_):
        stale.set()
        return response()

    wrapped, ledger = make("paid-stale.db", handler, spend=0)
    with pytest.raises(ProviderOperationCancelled):
        budget_gateway(wrapped, check_current=lambda: not stale.is_set()).generate(
            request
        )
    assert ledger.monthly_spend() == pytest.approx(0.002)
    with sqlite3.connect(ledger.db_path) as conn:
        metadata = json.loads(
            conn.execute(
                "SELECT metadata FROM cost_log WHERE metadata IS NOT NULL"
            ).fetchone()[0]
        )
    assert {row["phase"] for row in metadata["phase_timings"]} == {
        "ledger_wait",
        "provider_permit_wait",
        "transport",
    }


def test_transport_and_validation_retries_keep_accounting_and_phase_metadata(
    cost_gateway, monkeypatch
):
    import json
    import sqlite3

    import httpx

    from sabermetrics.cedh import provider_deepseek

    make, response, request = cost_gateway
    responses = iter([httpx.Response(503), response("invalid json"), response()])
    wrapped, ledger = make("retries.db", lambda _: next(responses), spend=0)
    wrapped.gateway.gateway._settings.model.max_retries = 1
    monkeypatch.setattr(provider_deepseek.time, "sleep", lambda _: None)
    result = wrapped.generate(request)
    assert result.value.ok
    assert result.usage.retries == 1
    assert result.usage.validation_failures == 1
    assert ledger.monthly_spend() == pytest.approx(0.004)
    with sqlite3.connect(ledger.db_path) as conn:
        metadata = json.loads(
            conn.execute(
                "SELECT metadata FROM cost_log WHERE metadata IS NOT NULL"
            ).fetchone()[0]
        )
    assert len([p for p in metadata["phase_timings"] if p["phase"] == "transport"]) == 3
    # Recording outside a scoped operation must also work after scope cleanup.
    ledger.record(result.usage)
    assert ledger.monthly_spend() == pytest.approx(0.008)


def test_generic_nested_gateway_timings_never_claim_to_measure_transport():
    import copy

    events = []

    def checkpoint(event):
        # Serialize immediately, as persistence does; do not retain a mutable
        # phase-list reference that later completion could change.
        events.append(copy.deepcopy(event))

    gateway = budget_gateway(
        budget_gateway(
            SimpleNamespace(generate=lambda request: "done"), on_checkpoint=checkpoint
        ),
        on_checkpoint=checkpoint,
    )
    assert gateway.generate(SimpleNamespace()) == "done"
    phases = [p["phase"] for event in events for p in event["phase_timings"]]
    assert "gateway_execution" in phases
    assert "transport" not in phases


def test_actual_usage_is_normalized_and_nested_wrapper_identity_is_shared():
    from sabermetrics.assistant_latency import LatencyTrace
    from sabermetrics.cedh.model_gateway import GenerationResult, UsageRecord

    usage = UsageRecord(
        provider="fake",
        model_id="model",
        prompt_version="v1",
        call_type="test",
        mode="assistant",
        request_id="provider-response-1",
        input_tokens=20,
        cached_input_tokens=5,
        output_tokens=3,
        reasoning_tokens=2,
    )
    result = GenerationResult(value={"ok": True}, usage=usage)

    class Gateway:
        def generate(self, request):
            return result

    events = []
    wrapper = budget_gateway(
        budget_gateway(Gateway(), on_checkpoint=events.append),
        on_checkpoint=events.append,
    )
    assert wrapper.generate(SimpleNamespace()) is result
    finished = [e for e in events if e["state"] == "finished"]
    assert len(finished) == 2
    assert finished[0]["usage_observation_id"] == finished[1]["usage_observation_id"]
    assert "provider-response-1" not in str(finished)
    trace = LatencyTrace("run")
    for event in events + events:
        trace.provider_checkpoint(event)
    assert trace.snapshot().usage_calls_observed == 1
    assert trace.snapshot().usage_tokens_observed["input_tokens"] == 20


@pytest.mark.parametrize("tokens, request_id", [(None, "id"), (0, "id"), (3, "")])
def test_missing_default_or_unidentified_usage_remains_unknown(tokens, request_id):
    from sabermetrics.cedh.model_gateway import GenerationResult, UsageRecord

    fields = (
        {}
        if tokens is None
        else dict(
            input_tokens=tokens,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )
    )
    result = GenerationResult(
        value={},
        usage=UsageRecord(
            provider="fake",
            model_id="model",
            prompt_version="v1",
            call_type="test",
            mode="assistant",
            request_id=request_id,
            **fields,
        ),
    )

    class Gateway:
        def generate(self, request):
            return result

    events = []
    budget_gateway(Gateway(), on_checkpoint=events.append).generate(SimpleNamespace())
    assert "usage_observed" not in events[-1]


def test_deferred_real_gateway_usage_includes_observed_validation_attempts(
    cost_gateway,
):
    import httpx

    from sabermetrics.assistant_latency import LatencyTrace

    make, _, request = cost_gateway
    attempts = []

    def handler(_):
        attempts.append(1)
        return httpx.Response(
            200,
            json={
                "id": "actual-response-" + str(len(attempts)),
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"ok":true}' if len(attempts) == 2 else "bad json"
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )

    gateway, _ = make("observed-usage.db", handler, spend=0)
    events = []
    result = budget_gateway(gateway, on_checkpoint=events.append).generate(request)
    assert result.usage.validation_failures == 1
    assert len(attempts) == 2
    trace = LatencyTrace("run", origin_monotonic=0.0)
    for event in events:
        trace.provider_checkpoint(event)
    report = trace.snapshot()
    assert report.usage_calls_observed == 1
    assert report.usage_tokens_observed == {
        "input_tokens": 20,
        "cached_input_tokens": 4,
        "output_tokens": 34,
        "reasoning_tokens": 6,
    }
    assert report.provider_attempts_observed == 2
