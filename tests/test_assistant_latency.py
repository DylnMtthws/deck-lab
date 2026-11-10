"""Offline checks for honest milestones and existing provider-phase adaptation."""

import json

import pytest
from pydantic import ValidationError

from sabermetrics.assistant_latency import (
    MAX_INTERVALS,
    MAX_PROVIDER_CALLS,
    ClientLatencyObservation,
    LatencyTrace,
)


def test_missing_milestones_are_unknown_and_durations_have_independent_boundaries():
    trace = LatencyTrace(
        "run", "exact_fact", origin_scope="request", origin_monotonic=100
    )
    assert trace.snapshot().durations_seconds["acknowledgment"] is None
    assert trace.mark("request_received", at=100)
    assert trace.mark("accepted", at=100.3)
    assert trace.mark("worker_started", at=102)
    assert trace.mark("first_useful_persisted", at=104)
    assert trace.mark("final_persisted", at=109)
    assert trace.snapshot().durations_seconds == {
        "acknowledgment": 0.3,
        "queue": 1.7,
        "first_useful": 3.7,
        "completion": 8.7,
        "worker_to_first_useful": 2.0,
        "worker_to_final": 7.0,
    }
    assert not trace.mark("first_useful_persisted", at=108)
    assert trace.snapshot().durations_seconds["first_useful"] == 3.7


def test_out_of_order_callbacks_cannot_fabricate_negative_or_earlier_timings():
    trace = LatencyTrace("run", origin_monotonic=100)
    assert trace.mark("final_persisted", at=110)
    assert not trace.mark("worker_started", at=111)
    assert not trace.mark("accepted", at=99)
    assert not trace.mark("accepted", at=float("nan"))
    assert not trace.mark("accepted", at=True)
    assert trace.mark("accepted", at=101)
    with pytest.raises(ValueError):
        trace.mark("rendered", at=105)


def test_provider_phase_intervals_union_nested_and_concurrent_wrappers():
    trace = LatencyTrace("run", origin_monotonic=100)
    for phase, a, b in [
        ("transport", 102, 106),
        ("transport", 102, 106),
        ("transport", 105, 109),
        ("ledger_wait", 100, 103),
        ("provider_permit_wait", 101, 102),
    ]:
        assert trace.phase(phase, a, b)
    report = trace.snapshot()
    assert report.phase_wall_seconds == {
        "transport": 7.0,
        "ledger_wait": 3.0,
        "provider_permit_wait": 1.0,
    }
    assert "never add" in report.scope
    assert not trace.phase("unbounded-user-content", 101, 102)
    assert not trace.phase("transport", 90, 101)
    assert not trace.phase("transport", 103, 102)


def test_existing_provider_checkpoint_shape_counts_attempts_once_and_discards_private_state():
    trace = LatencyTrace("run", "discovery", origin_monotonic=100)
    started = {"type": "provider_checkpoint", "call_id": "call", "state": "started"}
    trace.provider_checkpoint(started)
    event = {
        **started,
        "state": "finished",
        "prompt": "PRIVATE REQUEST",
        "phase_timings": [
            {
                "phase": "transport",
                "started_monotonic": 101,
                "finished_monotonic": 103,
                "attempt": 0,
            },
            {
                "phase": "transport",
                "started_monotonic": 104,
                "finished_monotonic": 108,
                "attempt": 1,
            },
            {
                "phase": "ledger_wait",
                "started_monotonic": 100,
                "finished_monotonic": 101,
            },
        ],
    }
    trace.provider_checkpoint(event)
    trace.provider_checkpoint(event)
    trace.cache_hit()
    report = trace.snapshot()
    assert report.provider_calls == 1
    assert report.provider_attempts_observed == 2
    assert report.cache_hits == 1
    assert report.phase_wall_seconds["transport"] == 6
    assert "PRIVATE" not in json.dumps(report.model_dump())
    assert "prompt" not in report.model_dump()


def test_telemetry_is_bounded_and_reports_omitted_samples():
    trace = LatencyTrace("run", origin_monotonic=100)
    for _ in range(MAX_INTERVALS + 3):
        trace.phase("ledger_wait", 100, 101)
    for n in range(MAX_PROVIDER_CALLS + 2):
        trace.provider_checkpoint(
            {"type": "provider_checkpoint", "call_id": str(n), "state": "started"}
        )
    report = trace.snapshot()
    assert report.dropped_intervals == 3
    assert report.provider_calls == MAX_PROVIDER_CALLS
    assert report.dropped_provider_calls == 2
    assert report.phase_wall_seconds["ledger_wait"] == 1


def test_client_measurements_have_a_separate_clock_and_cannot_claim_server_latency():
    observation = ClientLatencyObservation(
        run_id="run", received_to_rendered_seconds=0.2, first_useful=True
    )
    assert observation.clock_domain == "client_monotonic"
    with pytest.raises(ValidationError):
        ClientLatencyObservation(
            run_id="run",
            received_to_rendered_seconds=0.2,
            first_useful=True,
            accepted_at=100,
        )
    with pytest.raises(ValidationError):
        ClientLatencyObservation(
            run_id="run", received_to_rendered_seconds=float("inf"), first_useful=True
        )


def test_detached_report_and_injected_clock_do_not_change_earlier_report():
    clock = iter([10, 11, 12]).__next__
    trace = LatencyTrace("run", clock=clock)
    trace.mark("worker_started")
    prior = trace.snapshot()
    trace.mark("final_persisted")
    assert "final_persisted" not in prior.milestones_seconds
    assert trace.snapshot().durations_seconds["worker_to_final"] == 1


def test_usage_unknown_is_not_zero_and_reported_usage_is_partial_and_deduplicated():
    trace = LatencyTrace(
        "run", origin_monotonic=100, version_hashes={"model": "a" * 64}
    )
    assert trace.snapshot().usage_tokens_observed is None
    assert trace.usage(
        "known", input_tokens=100, cached_input_tokens=25, output_tokens=10
    )
    assert not trace.usage("known", input_tokens=999, output_tokens=99)
    assert not trace.usage(
        "invalid", input_tokens=1, cached_input_tokens=2, output_tokens=0
    )
    assert not trace.usage("bool", input_tokens=True, output_tokens=0)
    trace.provider_checkpoint(
        {"type": "provider_checkpoint", "call_id": "unknown-usage", "state": "started"}
    )
    report = trace.snapshot()
    assert report.usage_tokens_observed == {
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 10,
        "reasoning_tokens": 0,
    }
    assert report.usage_calls_observed == 1
    assert report.version_hashes == {"model": "a" * 64}
    with pytest.raises(ValueError):
        LatencyTrace("run", version_hashes={"prompt": "PRIVATE PROMPT"})


def test_nested_provider_wrappers_do_not_count_same_transport_interval_as_two_attempts():
    trace = LatencyTrace("run", origin_monotonic=100)
    event = {
        "type": "provider_checkpoint",
        "state": "finished",
        "phase_timings": [
            {"phase": "transport", "started_monotonic": 101, "finished_monotonic": 102}
        ],
    }
    trace.provider_checkpoint({**event, "call_id": "outer"})
    trace.provider_checkpoint({**event, "call_id": "inner"})
    assert trace.snapshot().provider_calls == 2
    assert trace.snapshot().provider_attempts_observed == 1
