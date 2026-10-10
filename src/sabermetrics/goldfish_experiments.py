"""Durable, owner-scoped experiment log for the Goldfish Diagnostics workflow.

Each row is an immutable snapshot of a deck's commander and library state at
the moment a diagnostics run was requested, plus the settings that run used.
No network and no model calls happen here: this module only freezes inputs,
tracks run status, and stores whatever result a caller later reports. Rows
are private to their owner — every read is scoped by ``owner_id`` and a
foreign owner's query simply returns nothing.

List identity reuses :func:`sabermetrics.deck_simulation.candidate_from_document`
with :func:`sabermetrics.deck_simulation.user_edited_provenance`, the same
hash the simulator and the rest of Deck Lab already agree on.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabermetrics import db
from sabermetrics.deck_simulation import (
    _commander_entries,
    _library_entries,
    _merge_library,
    _oracle_id,
    candidate_from_document,
    illegal_list_reason,
    user_edited_provenance,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS goldfish_experiments (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    deck_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deck_revision INTEGER NOT NULL,
    deck_sha256 TEXT NOT NULL,
    settings_sha256 TEXT NOT NULL,
    deck_snapshot_json TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    parent_id TEXT,
    baseline_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued', 'running', 'done', 'failed')),
    result_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_goldfish_experiments_owner_deck
    ON goldfish_experiments(owner_id, deck_id, created_at DESC);
"""

_MAX_REPLACEMENTS = 3


def _hash_settings(settings: dict[str, Any]) -> str:
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _library_quantities(document: dict[str, Any]) -> dict[str, int]:
    """Oracle id -> quantity for the public 99, same scope the hash uses."""
    return {
        card.oracle_id: card.quantity
        for card in _merge_library(_library_entries(document))
    }


def _commander_ids(document: dict[str, Any]) -> set[str]:
    return {_oracle_id(entry) for entry in _commander_entries(document)}


def _check_lineage(parent_snapshot: dict[str, Any], document: dict[str, Any]) -> None:
    """Enforce the allowed distance between a parent run and a new one.

    No commander changes are allowed, and quantity-aware library edits are
    capped at three net replacements. Every card, basic lands included,
    counts the same toward that cap — there is no exemption that would let a
    land-count edit sidestep the limit.
    """
    if _commander_ids(parent_snapshot) != _commander_ids(document):
        raise ValueError(
            "Commander changes are not allowed between a parent experiment "
            "and its child; start a new baseline instead."
        )
    parent_library = _library_quantities(parent_snapshot)
    new_library = _library_quantities(document)
    removed = sum(
        max(0, quantity - new_library.get(oracle_id, 0))
        for oracle_id, quantity in parent_library.items()
    )
    added = sum(
        max(0, quantity - parent_library.get(oracle_id, 0))
        for oracle_id, quantity in new_library.items()
    )
    replacements = max(removed, added)
    if replacements > _MAX_REPLACEMENTS:
        raise ValueError(
            f"Too many card replacements since the parent experiment: "
            f"{replacements} (limit {_MAX_REPLACEMENTS})."
        )


class GoldfishExperimentsRepo:
    """Owner-scoped, append-mostly log of goldfish diagnostics runs.

    The backing table is created lazily on first use of a given instance and
    never altered destructively; later instances against the same database
    see the same additive ``CREATE TABLE IF NOT EXISTS``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._schema_ready = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        if not self._schema_ready:
            conn.executescript(_SCHEMA_SQL)
            self._schema_ready = True
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _fetch(
        conn: sqlite3.Connection, owner_id: str, experiment_id: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM goldfish_experiments WHERE id=? AND owner_id=?",
            (experiment_id, owner_id),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _to_view(row: dict[str, Any]) -> dict[str, Any]:
        view = dict(row)
        view["document"] = json.loads(view["deck_snapshot_json"])
        view["settings"] = json.loads(view["settings_json"])
        raw_result = view.get("result_json")
        view["result"] = json.loads(raw_result) if raw_result else None
        return view

    def create(
        self,
        owner_id: str,
        document: dict[str, Any],
        settings: dict[str, Any],
        hypothesis: str = "",
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Freeze ``document`` and ``settings`` as a new queued experiment.

        Raises:
            ValueError: ``document`` is not a legal, simulatable 100-card
                list; ``settings`` is not a mapping; or ``parent_id`` is set
                and the parent is missing, owned by someone else, tied to a
                different deck, not yet ``done``, run under different
                settings, or too far from ``document`` (a commander change,
                or more than three net card replacements).
        """
        if not owner_id:
            raise ValueError("An owner id is required.")
        if not isinstance(document, dict):
            raise ValueError("document must be a mapping.")  # noqa: TRY004
        deck_id = str(document.get("id") or "")
        if not deck_id:
            raise ValueError("document is missing its deck id.")
        reason = illegal_list_reason(document)
        if reason:
            raise ValueError(reason)
        # Also validates the list is hashable/simulatable (commander count,
        # oracle ids present); candidate_from_document raises ValueError
        # with the specific defect when it is not.
        deck_sha256 = candidate_from_document(
            document, user_edited_provenance()
        ).deck_sha256
        if not isinstance(settings, dict):
            raise ValueError("settings must be a mapping.")  # noqa: TRY004
        settings_sha256 = _hash_settings(settings)
        try:
            deck_revision = int(document.get("revision") or 0)
        except (TypeError, ValueError):
            deck_revision = 0

        snapshot = copy.deepcopy(document)
        settings_copy = copy.deepcopy(settings)

        with self._connect() as conn:
            baseline_id: str | None = None
            if parent_id:
                parent = self._fetch(conn, owner_id, str(parent_id))
                if parent is None:
                    raise ValueError("Parent experiment was not found for this owner.")
                if parent["deck_id"] != deck_id:
                    raise ValueError("Parent experiment belongs to a different deck.")
                if parent["status"] != "done":
                    raise ValueError("Parent experiment has not finished running yet.")
                if parent["settings_sha256"] != settings_sha256:
                    raise ValueError(
                        "Settings must match the parent experiment exactly "
                        "(seed, trial count, and pilot versions included)."
                    )
                parent_snapshot = json.loads(parent["deck_snapshot_json"])
                _check_lineage(parent_snapshot, document)
                baseline_id = str(parent["baseline_id"])

            experiment_id = db.new_id()
            if baseline_id is None:
                baseline_id = experiment_id
            created_at = datetime.now(UTC).isoformat(timespec="microseconds")
            conn.execute(
                "INSERT INTO goldfish_experiments "
                "(id, owner_id, deck_id, created_at, deck_revision, "
                "deck_sha256, settings_sha256, deck_snapshot_json, "
                "settings_json, parent_id, baseline_id, hypothesis, "
                "status, result_json, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'queued',NULL,NULL)",
                (
                    experiment_id,
                    owner_id,
                    deck_id,
                    created_at,
                    deck_revision,
                    deck_sha256,
                    settings_sha256,
                    json.dumps(snapshot),
                    json.dumps(settings_copy),
                    str(parent_id) if parent_id else None,
                    baseline_id,
                    str(hypothesis or ""),
                ),
            )
            row = self._fetch(conn, owner_id, experiment_id)
        assert row is not None
        return self._to_view(row)

    def get(self, owner_id: str, id: str) -> dict[str, Any] | None:
        """Return one experiment owned by ``owner_id``, or None.

        A foreign owner's id never resolves, even if the row exists.
        """
        with self._connect() as conn:
            row = self._fetch(conn, owner_id, id)
        return self._to_view(row) if row else None

    def history(self, owner_id: str, deck_id: str) -> list[dict[str, Any]]:
        """Return every experiment for ``deck_id`` owned by ``owner_id``, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goldfish_experiments WHERE owner_id=? AND deck_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 50",
                (owner_id, deck_id),
            ).fetchall()
        return [self._to_view(dict(row)) for row in rows]

    def finish(self, owner_id: str, id: str, result: dict[str, Any]) -> None:
        """Record a successful run's result, verbatim.

        ``result`` is stored exactly as given — unknown or missing metrics
        are never filled in with zeroes.

        Raises:
            ValueError: The row does not exist for this owner, or it already
                finished (results are immutable once ``done``).
        """
        if not isinstance(result, dict):
            raise ValueError("result must be a mapping.")  # noqa: TRY004
        with self._connect() as conn:
            row = self._fetch(conn, owner_id, id)
            if row is None:
                raise ValueError("Experiment was not found for this owner.")
            if row["status"] == "done":
                raise ValueError(
                    "This experiment already finished; results are immutable."
                )
            conn.execute(
                "UPDATE goldfish_experiments "
                "SET status='done', result_json=?, error=NULL "
                "WHERE id=? AND owner_id=?",
                (json.dumps(result), id, owner_id),
            )

    def fail(self, owner_id: str, id: str, error: str) -> None:
        """Record a failed run.

        Raises:
            ValueError: The row does not exist for this owner, or it already
                finished (a completed run is never overwritten).
        """
        with self._connect() as conn:
            row = self._fetch(conn, owner_id, id)
            if row is None:
                raise ValueError("Experiment was not found for this owner.")
            if row["status"] == "done":
                raise ValueError("Cannot fail an experiment that already finished.")
            conn.execute(
                "UPDATE goldfish_experiments SET status='failed', error=? "
                "WHERE id=? AND owner_id=?",
                (str(error), id, owner_id),
            )
