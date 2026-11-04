"""Admission must coordinate independent processes, not just Python locks."""

import multiprocessing

from sabermetrics.ai_cost_control import Authority, BudgetConfig, BudgetDenied


def _config():
    return BudgetConfig(
        prices=(("test-model", 1, 1),),
        credentials=(),
        authorized=True,
        monthly=600,
        environments=(("main", 500, 500, 10), ("preview", 100, 100, 5)),
        turn_cost=400,
    )


def _attempt(path, turn, ready, start, results):
    authority = Authority(path, _config(), local=True)
    ready.put(True)
    if not start.wait(10):
        raise RuntimeError("parent did not release admission race")
    try:
        authority.admit_attempt(turn, "owner", "test-model", 200, 200)
    except BudgetDenied:
        results.put("denied")
    else:
        results.put("admitted")


def test_independent_processes_cannot_oversubscribe(tmp_path):
    path = str(tmp_path / "shared-budget.sqlite")
    authority = Authority(path, _config(), local=True)
    turns = [
        authority.begin_turn("main", f"user-{i}", f"request-{i}", "owner")
        for i in range(2)
    ]
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    workers = [
        ctx.Process(target=_attempt, args=(path, turn, ready, start, results))
        for turn in turns
    ]
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            assert ready.get(timeout=15)
        start.set()
        assert sorted(results.get(timeout=15) for _ in workers) == [
            "admitted",
            "denied",
        ]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert sum(a["charge"] for a in authority.status()["attempts"].values()) == 400
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        ready.close()
        results.close()
