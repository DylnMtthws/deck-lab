"""Opt-in real PostgreSQL test; use a disposable writable database DSN."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import pytest

from sabermetrics.ai_cost_control import Authority, BudgetConfig, BudgetDenied


@pytest.mark.postgres
def test_postgres_independent_authorities_serialize_reservations():
    dsn = os.environ.get("SABER_AI_COST_TEST_DSN")
    if not dsn:
        pytest.skip("SABER_AI_COST_TEST_DSN must name a disposable writable database")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = "budget_test_" + uuid.uuid4().hex
    config = BudgetConfig(
        prices=(("test-model", 1, 1),),
        credentials=(),
        authorized=True,
        monthly=600,
        environments=(("main", 500, 500, 10), ("preview", 100, 100, 5)),
        turn_cost=400,
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            parts = urlsplit(dsn)
            if parts.scheme not in {"postgres", "postgresql"}:
                pytest.fail("test DSN must be a PostgreSQL URL")
            query = [(k, v) for k, v in parse_qsl(parts.query) if k != "options"]
            query.append(("options", f"-c search_path={schema}"))
            isolated = urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))
            controls = [Authority(isolated, config), Authority(isolated, config)]
            turns = [
                control.begin_turn("main", f"user-{i}", f"request-{i}", "owner")
                for i, control in enumerate(controls)
            ]

            def attempt(pair):
                control, turn = pair
                try:
                    control.admit_attempt(turn, "owner", "test-model", 200, 200)
                except BudgetDenied:
                    return "denied"
                return "admitted"

            with ThreadPoolExecutor(max_workers=2) as pool:
                assert sorted(pool.map(attempt, zip(controls, turns))) == [
                    "admitted",
                    "denied",
                ]
            assert (
                sum(row["charge"] for row in controls[0].status()["attempts"].values())
                == 400
            )
        finally:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
