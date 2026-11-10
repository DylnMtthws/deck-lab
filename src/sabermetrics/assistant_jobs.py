"""Durable queue and lease primitives, used inside the session transaction."""

from __future__ import annotations

import os
import time
import uuid

PROCESS_ID = f"{os.getpid()}:{uuid.uuid4().hex}"
LEASE_SECONDS = 90
ACTIVE_STATES = ("queued", "running")


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS build_assistant_jobs (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, deck_id TEXT NOT NULL
        REFERENCES deck_documents(id) ON DELETE CASCADE,
        generation INTEGER NOT NULL, state TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        lease_owner TEXT, lease_deadline REAL, checkpoint_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS assistant_job_queue ON build_assistant_jobs(state,created_at)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS build_assistant_steering (
        owner_id TEXT NOT NULL, deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, request_json TEXT NOT NULL, response_json TEXT NOT NULL,
        PRIMARY KEY(owner_id,deck_id,request_id))""")


def view(row):
    import json

    if row is None:
        return {"state": "idle", "checkpoint": {}, "retry_allowed": False}
    columns = set(row.keys())
    public = (
        json.loads(row["public_projection_json"])
        if "public_projection_json" in columns and row["public_projection_json"]
        else {}
    )
    complete = public.get("run", {}).get("fulfillment") == "complete"
    return {
        "id": row["id"],
        "state": row["state"],
        "checkpoint": json.loads(row["checkpoint_json"]),
        "retry_allowed": row["state"] in {"interrupted", "failed", "stopped"}
        or (row["state"] == "completed" and not complete),
    }


def owner_alive(identity):
    if not identity:
        return False
    try:
        pid = int(identity.split(":", 1)[0])
        if pid == os.getpid():
            return identity == PROCESS_ID
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def expired(row):
    return row["state"] == "running" and (
        not owner_alive(row["lease_owner"])
        or (row["lease_deadline"] or 0) < time.time()
    )
