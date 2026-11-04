"""Process-wide admission for synchronous provider operations.

Wrap every planner, selector, reviewer and corrective gateway with
``budget_gateway(gateway, check_current=thread_safe_cancellation_check)``.
The original gateway still owns cost/output/deadline accounting. Never acquire
a permit around a review job: only generate holds one. Nested wrappers on the
same thread share its permit; different turns share the process-wide limit.
This is a process limit, not an interprocess/distributed semaphore.
Cost admission belongs to the original gateway: DeepSeek reserves its shared
ledger before acquiring a permit, through retries and accounting, serializing
only calls to that database because its ceiling has no projected-cost bound.
Permits do not reserve dollars.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from typing import Any

from sabermetrics.cedh.cost_ledger import (
    check_operation_current,
    operation_check,
    operation_phase,
    operation_timings,
)


class ProviderOperationCancelled(RuntimeError):
    """A queued operation or returned result is no longer current."""


_PERMITS = threading.BoundedSemaphore(2)
_LOCAL = threading.local()


def check_active(check_current: Callable[[], Any] | None) -> None:
    """Callbacks may raise their own cancellation exception or return False."""
    if check_current is not None and check_current() is False:
        raise ProviderOperationCancelled("Provider operation is no longer current")


class ProviderBudget:
    """All instances use the same two permits, including nested adapters."""

    max_concurrent = 2

    @contextmanager
    def operation(self, check_current: Callable[[], Any] | None = None):
        check_active(check_current)
        nested = bool(getattr(_LOCAL, "depth", 0))
        acquired = False
        try:
            if not nested:
                with operation_phase("provider_permit_wait"):
                    while not _PERMITS.acquire(timeout=0.025):
                        check_operation_current()
                        check_active(check_current)
                    acquired = True
            check_operation_current()
            check_active(check_current)
            _LOCAL.depth = getattr(_LOCAL, "depth", 0) + 1
            try:
                yield
                check_active(check_current)
            finally:
                _LOCAL.depth -= 1
        finally:
            if acquired:
                _PERMITS.release()


shared_budget = ProviderBudget()


@contextmanager
def admitted_provider_operation():
    """Acquire only after exclusive cost admission, including unwrapped calls."""
    budget = getattr(_LOCAL, "admitted_budget", shared_budget)
    with budget.operation():
        yield


class BudgetedGateway:
    """Transparent adapter; no copied ledger, retry policy or provider state.

    Cancellation checks here run on the generate caller's thread. A check that
    touches coordinator-owned SQLite must instead stay in progressive_review;
    supply only thread-safe cancellation/deadline signals to this adapter.
    """

    def __init__(
        self, gateway: Any, *, check_current=None, on_checkpoint=None, budget=None
    ):
        self.gateway = gateway
        self.check_current = check_current
        self.on_checkpoint = on_checkpoint
        self.budget = budget if budget is not None else shared_budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self.gateway, name)

    def generate(self, request):
        # Explicit capability: the gateway promises to acquire at its admitted
        # execution boundary. Forwarded by nested wrappers, so none hold a
        # global permit while the innermost gateway waits for its ledger.
        deferred = getattr(self.gateway, "defers_provider_permit", False) is True
        previous = getattr(_LOCAL, "admitted_budget", None)
        if deferred and previous is None:
            _LOCAL.admitted_budget = self.budget
        try:
            with (
                operation_check(lambda: check_active(self.check_current)),
                operation_timings() as phases,
            ):
                event = {
                    "type": "provider_checkpoint",
                    "call_id": uuid.uuid4().hex,
                    "call_type": getattr(request, "call_type", ""),
                    "prompt_version": getattr(request, "prompt_version", ""),
                    "provider": getattr(self.gateway, "provider", ""),
                    "model_id": getattr(self.gateway, "model_id", ""),
                    "phase_timings": phases,
                }
                try:
                    if self.on_checkpoint:
                        with operation_phase("checkpoint_callback", state="started"):
                            self.on_checkpoint({**event, "state": "started"})
                    with (
                        nullcontext()
                        if deferred
                        else self.budget.operation(self.check_current)
                    ):
                        check_operation_current()
                        if deferred:
                            result = self.gateway.generate(request)
                        else:
                            # A generic gateway may include validation, nested
                            # wrappers and callbacks. Only the real provider
                            # knows its HTTP transport boundary.
                            with operation_phase("gateway_execution"):
                                result = self.gateway.generate(request)
                        check_operation_current()
                        return result
                finally:
                    if self.on_checkpoint:
                        with operation_phase("checkpoint_callback", state="finished"):
                            self.on_checkpoint({**event, "state": "finished"})
        finally:
            if deferred and previous is None:
                del _LOCAL.admitted_budget


def budget_gateway(
    gateway: Any, *, check_current=None, on_checkpoint=None, budget=None
) -> BudgetedGateway:
    """Safe to wrap an already wrapped gateway; both checks remain active."""
    return BudgetedGateway(
        gateway, check_current=check_current, on_checkpoint=on_checkpoint, budget=budget
    )
