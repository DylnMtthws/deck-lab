"""Transactional public journal and bounded snapshot transport for assistant runs."""

from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any, cast

from sabermetrics.assistant_events import (
    COLLECTION_LIMITS,
    MAX_EVENT_BYTES,
    PAGE_BYTES,
    TERMINAL,
    VERSION,
    ProjectionTooLarge,
    PublicEvent,
    PublicRun,
    empty_projection,
    reduce_event,
    serialized,
    timestamp,
)

MAX_RUN_EVENTS = 4096
MAX_DECK_EVENTS = 16384
MAX_DECK_BYTES = 8 * 1024 * 1024
SNAPSHOT_TTL = 300
MAX_OWNER_SNAPSHOTS = 4


def initialize(conn: Any) -> None:
    """Apply additive migration once, safely on both new and existing databases."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(build_assistant_jobs)")}
    for name, declaration in {
        "last_sequence": "INTEGER NOT NULL DEFAULT 0",
        "baseline_sequence": "INTEGER NOT NULL DEFAULT 0",
        "public_projection_json": "TEXT",
        "public_baseline_json": "TEXT",
        "public_writer_version": "TEXT",
        "public_domain_hash": "TEXT",
    }.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE build_assistant_jobs ADD COLUMN {name} {declaration}"
            )
    statements = """
        CREATE TABLE IF NOT EXISTS build_assistant_events (
          run_id TEXT NOT NULL REFERENCES build_assistant_jobs(id) ON DELETE CASCADE,
          owner_id TEXT NOT NULL, deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
          sequence INTEGER NOT NULL, kind TEXT NOT NULL, event_json TEXT NOT NULL,
          PRIMARY KEY(run_id,sequence));
        CREATE INDEX IF NOT EXISTS assistant_public_events_deck
          ON build_assistant_events(deck_id,run_id,sequence);
        CREATE TABLE IF NOT EXISTS build_assistant_public_snapshots (
          token TEXT PRIMARY KEY, owner_id TEXT NOT NULL, deck_id TEXT NOT NULL
            REFERENCES deck_documents(id) ON DELETE CASCADE,
          run_id TEXT NOT NULL REFERENCES build_assistant_jobs(id) ON DELETE CASCADE,
          watermark INTEGER NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
          pages_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS assistant_public_snapshots_owner
          ON build_assistant_public_snapshots(owner_id,created_at);
        CREATE TABLE IF NOT EXISTS assistant_public_schema_versions (
          version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        """
    for statement in statements.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute(
        "INSERT OR IGNORE INTO assistant_public_schema_versions VALUES (1,?)",
        (time.time(),),
    )


def row_for(conn: Any, run_id: str) -> Any:
    return conn.execute(
        "SELECT * FROM build_assistant_jobs WHERE id=?", (run_id,)
    ).fetchone()


def ensure_baseline(conn: Any, row: Any) -> dict[str, Any]:
    """Legacy runs begin at S=0 without invented events or lifecycle timestamps."""
    if row["public_projection_json"]:
        return cast(dict[str, Any], json.loads(row["public_projection_json"]))
    state = row["state"]
    if state not in {*TERMINAL, "running", "queued"}:
        state = "interrupted"
    legacy = json.loads(row["snapshot_json"])
    run = PublicRun(
        run_id=row["id"],
        generation=int(row["generation"]),
        deck_revision=int(legacy.get("revision", 0)),
        lifecycle=state,
        legacy_outcome_uncertain=True,
        evidence_status="unavailable" if state in TERMINAL else "not_started",
    )
    projection = empty_projection(run)
    text = serialized(projection)
    conn.execute(
        "UPDATE build_assistant_jobs SET public_projection_json=?,public_baseline_json=?,public_writer_version=? WHERE id=?",
        (text, text, VERSION, row["id"]),
    )
    return projection


def create_run(conn: Any, run: PublicRun, *, owner: str, deck: str) -> None:
    """Initialize a new queued projection before its first domain event."""
    projection = empty_projection(run)
    text = serialized(projection)
    conn.execute(
        "UPDATE build_assistant_jobs SET public_projection_json=?,public_baseline_json=?,public_writer_version=? WHERE id=? AND owner_id=? AND deck_id=?",
        (text, text, VERSION, run.run_id, owner, deck),
    )


def append(conn: Any, run_id: str, kind: str, payload: dict[str, Any]) -> bool:
    """Publish exactly one bounded event in the caller's domain transaction."""
    row = row_for(conn, run_id)
    if row is None:
        return False
    projection = ensure_baseline(conn, row)
    if projection["run"]["lifecycle"] in TERMINAL:
        return False
    sequence = row["last_sequence"] + 1
    item = PublicEvent.model_validate(
        {
            "run_id": run_id,
            "generation": row["generation"],
            "sequence": sequence,
            "event_id": f"{run_id}:{sequence}",
            "occurred_at": timestamp(time.time()),
            "kind": kind,
            "payload": payload,
        }
    ).model_dump()
    text = serialized(item)
    if len(text.encode()) > MAX_EVENT_BYTES:
        raise ProjectionTooLarge("Public event exceeds 32 KiB.")
    updated = reduce_event(projection, item)
    conn.execute(
        "INSERT INTO build_assistant_events VALUES (?,?,?,?,?,?)",
        (run_id, row["owner_id"], row["deck_id"], sequence, kind, text),
    )
    conn.execute(
        "UPDATE build_assistant_jobs SET last_sequence=?,public_projection_json=? WHERE id=?",
        (sequence, serialized(updated), run_id),
    )
    compact(conn, row["deck_id"], run_id)
    return True


def update_run(conn: Any, run_id: str, kind: str, **changes: Any) -> bool:
    """Patch public lifecycle metadata, validated before journaling."""
    row = row_for(conn, run_id)
    if row is None:
        return False
    projection = ensure_baseline(conn, row)
    if projection["run"]["lifecycle"] in TERMINAL:
        return False
    run = dict(projection["run"], **changes)
    return append(
        conn, run_id, kind, {"run": PublicRun.model_validate(run).model_dump()}
    )


def upsert(
    conn: Any, run_id: str, kind: str, collection: str, entity: dict[str, Any]
) -> bool:
    """Do not emit identical records on repeated callbacks/polls."""
    row = row_for(conn, run_id)
    if row is None:
        return False
    projection = ensure_baseline(conn, row)
    if any(v == entity for v in projection["entities"][collection]):
        return False
    return append(conn, run_id, kind, {"collection": collection, "entity": entity})


def compact(conn: Any, deck_id: str, run_id: str) -> None:
    """Checkpoint retained public state before dropping replay history."""
    run_count = conn.execute(
        "SELECT COUNT(*) FROM build_assistant_events WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    if run_count > MAX_RUN_EVENTS:
        _compact_run(conn, run_id)
    count, size = conn.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(CAST(event_json AS BLOB))),0) FROM build_assistant_events WHERE deck_id=?",
        (deck_id,),
    ).fetchone()
    while count > MAX_DECK_EVENTS or size > MAX_DECK_BYTES:
        oldest = conn.execute(
            "SELECT e.run_id FROM build_assistant_events e JOIN build_assistant_jobs j ON j.id=e.run_id WHERE e.deck_id=? ORDER BY j.created_at,e.sequence LIMIT 1",
            (deck_id,),
        ).fetchone()
        if not oldest:
            break
        _compact_run(conn, oldest[0])
        count, size = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(event_json AS BLOB))),0) FROM build_assistant_events WHERE deck_id=?",
            (deck_id,),
        ).fetchone()


def _compact_run(conn: Any, run_id: str) -> None:
    row = row_for(conn, run_id)
    projection = json.loads(row["public_projection_json"])
    # This metadata is part of the baseline, never a fabricated historical event.
    text = serialized(projection)
    conn.execute(
        "UPDATE build_assistant_jobs SET public_baseline_json=?,public_projection_json=?,baseline_sequence=last_sequence WHERE id=?",
        (text, text, run_id),
    )
    conn.execute("DELETE FROM build_assistant_events WHERE run_id=?", (run_id,))


def cleanup(conn: Any, now: float) -> None:
    """Lazy bounded housekeeping, without a background process."""
    conn.execute(
        "DELETE FROM build_assistant_public_snapshots WHERE expires_at<=?", (now,)
    )
    expired = conn.execute(
        "SELECT DISTINCT e.run_id FROM build_assistant_events e JOIN build_assistant_jobs j ON e.run_id=j.id WHERE j.state NOT IN ('queued','running') AND j.updated_at<?",
        (now - 86400,),
    ).fetchall()
    for row in expired:
        _compact_run(conn, row[0])


def pages_for(projection: dict[str, Any]) -> list[dict[str, Any]]:
    """Page immutable entities; leave envelope overhead below the wire cap."""
    pages: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for collection in COLLECTION_LIMITS:
        for entity in projection["entities"][collection]:
            candidate = copy.deepcopy(current)
            candidate.setdefault(collection, []).append(entity)
            if len(serialized(candidate).encode()) > PAGE_BYTES - 16 * 1024:
                if not current:
                    raise ProjectionTooLarge("Entity cannot fit snapshot page.")
                pages.append(current)
                current = {collection: [entity]}
            else:
                current = candidate
    pages.append(current)
    if len(pages) > 64 or len(serialized(pages).encode()) > 8 * 1024 * 1024:
        raise ProjectionTooLarge("Snapshot bundle exceeds the wire limit.")
    return pages


def response_base(
    projection: dict[str, Any], operational: dict[str, Any]
) -> dict[str, Any]:
    """Build head/advisory metadata; callers attach one transport mode."""
    return {
        "contract_version": VERSION,
        "server_time": timestamp(time.time()),
        "run": copy.deepcopy(projection["run"]),
        "operational": operational,
    }


def new_token() -> str:
    """Opaque, non-authorizing snapshot handle."""
    return uuid.uuid4().hex
