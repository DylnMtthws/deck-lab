"""Process-wide admission for synchronous provider operations.

Wrap every planner, selector, reviewer and corrective gateway with
``budget_gateway(gateway, check_current=thread_safe_cancellation_check)``.
The original gateway still owns cost/output/deadline accounting. Never acquire
a permit around a review job: only generate holds one. Nested wrappers on the
same thread share its permit; different turns share the process-wide limit.
This is a process limit, not an interprocess/distributed semaphore.
Cost admission belongs to the original gateway: DeepSeek reserves its shared
ledger through retries and accounting, serializing only calls to that database
because its ceiling has no projected-cost bound. Permits do not reserve dollars.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from sabermetrics.cedh.cost_ledger import operation_check


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
                while not _PERMITS.acquire(timeout=0.025):
                    check_active(check_current)
                acquired = True
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
        with (
            operation_check(lambda: check_active(self.check_current)),
            self.budget.operation(self.check_current),
        ):
            event = {
                "type": "provider_checkpoint",
                "call_id": uuid.uuid4().hex,
                "call_type": getattr(request, "call_type", ""),
                "prompt_version": getattr(request, "prompt_version", ""),
                "provider": getattr(self.gateway, "provider", ""),
                "model_id": getattr(self.gateway, "model_id", ""),
            }
            if self.on_checkpoint:
                self.on_checkpoint({**event, "state": "started"})
            try:
                check_active(self.check_current)
                result = self.gateway.generate(request)
                check_active(self.check_current)
                return result
            finally:
                if self.on_checkpoint:
                    self.on_checkpoint({**event, "state": "finished"})


def budget_gateway(
    gateway: Any, *, check_current=None, on_checkpoint=None, budget=None
) -> BudgetedGateway:
    """Safe to wrap an already wrapped gateway; both checks remain active."""
    return BudgetedGateway(
        gateway, check_current=check_current, on_checkpoint=on_checkpoint, budget=budget
    )
