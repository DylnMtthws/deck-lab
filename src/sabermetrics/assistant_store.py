"""Per-owner, per-deck conversations and cancellable assistant turns."""

from __future__ import annotations

import copy
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

from sabermetrics import db
from sabermetrics.deck_documents import DeckNotFound


def validate_constraints(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Constraints must be an object.")
    budget = value.get("budget")
    if budget is not None and (
        isinstance(budget, bool)
        or not isinstance(budget, (int, float))
        or not math.isfinite(budget)
        or budget <= 0
        or budget > 1000000
    ):
        raise ValueError("Budget must be a positive USD amount, or empty.")
    result: dict[str, Any] = {"budget": budget}
    for key in ("power_level", "theme"):
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError(f"{key} must be text of at most 500 characters.")
        result[key] = text.strip()
    from sabermetrics.swap_evaluation import OBJECTIVES

    objective = value.get("objective", "")
    if objective is None:
        objective = ""
    if not isinstance(objective, str) or objective not in {"", *OBJECTIVES}:
        raise ValueError("Choose a supported improvement objective, or leave it empty.")
    result["objective"] = objective
    cards = value.get("protected_cards", [])
    if (
        not isinstance(cards, list)
        or len(cards) > 100
        or any(not isinstance(card, str) or not 1 <= len(card) <= 100 for card in cards)
    ):
        raise ValueError("Protected cards must be a list of card IDs.")
    result["protected_cards"] = list(dict.fromkeys(cards))
    for key in ("excluded_candidates", "prioritized_candidates"):
        if key in value:
            cards = value[key]
            if (
                not isinstance(cards, list)
                or len(cards) > 100
                or any(
                    not isinstance(card, str) or not 1 <= len(card) <= 100
                    for card in cards
                )
            ):
                raise ValueError(f"{key} must be a list of at most 100 card IDs.")
            result[key] = list(dict.fromkeys(cards))
    return result


class RunConflict(ValueError):
    """A mutation targets stale run identity or deck context."""


class QueueFull(ValueError):
    pass


class AssistantStore:
    def __init__(self, db_path: str | Path):
        from sabermetrics import assistant_jobs as jobs

        self.db_path = Path(db_path)
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS build_assistant_sessions ("
                "deck_id TEXT PRIMARY KEY REFERENCES deck_documents(id) ON DELETE CASCADE, "
                "owner_id TEXT NOT NULL, payload_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assistant_submissions (owner TEXT, request_id TEXT, fingerprint TEXT NOT NULL, PRIMARY KEY(owner, request_id))"
            )
            columns = {
                r[1] for r in conn.execute("PRAGMA table_info(assistant_submissions)")
            }
            if "turn_id" not in columns:
                conn.execute(
                    "ALTER TABLE assistant_submissions ADD COLUMN turn_id TEXT"
                )
            jobs.initialize(conn)
            from sabermetrics import assistant_event_store

            assistant_event_store.initialize(conn)
            conn.commit()

    def _load(self, conn, owner, deck):
        if (
            conn.execute(
                "SELECT id FROM deck_documents WHERE id=? AND owner_id=?", (deck, owner)
            ).fetchone()
            is None
        ):
            raise DeckNotFound("Deck not found.")
        initial = dict(
            mode="suggest",
            constraints=validate_constraints({}),
            messages=[],
            busy=False,
            turn_id=None,
            updated_at=time.time(),
        )
        conn.execute(
            "INSERT INTO build_assistant_sessions(deck_id,owner_id,payload_json) VALUES (?,?,?) "
            "ON CONFLICT(deck_id) DO NOTHING",
            (deck, owner, json.dumps(initial)),
        )
        p = json.loads(
            conn.execute(
                "SELECT payload_json FROM build_assistant_sessions WHERE deck_id=? AND owner_id=?",
                (deck, owner),
            ).fetchone()[0]
        )
        # A pre-v1 writer can mutate legacy session state after a rollback.
        # Rebaseline at a NEW sequence watermark; never reuse old event IDs.
        if p.get("turn_id"):
            previous = self._job(conn, owner, deck, p["turn_id"])
            if previous and previous["public_domain_hash"]:
                from sabermetrics.assistant_events import digest

                if previous["public_domain_hash"] != digest(p):
                    self._rebaseline_nonjournaled(conn, previous, p)
        if p.get("busy") and self._job(conn, owner, deck, p.get("turn_id")) is None:
            # Upgrade old busy-only sessions without replaying an unknown paid call.
            from sabermetrics.assistant_workspace import create_workspace

            token = p.get("turn_id") or uuid.uuid4().hex
            request_text = next(
                (
                    m.get("content", "")
                    for m in reversed(p["messages"])
                    if m.get("role") == "user"
                ),
                "Interrupted research",
            )
            p.update(busy=False, turn_id=token)
            p["research_workspace"] = create_workspace(
                request_text, p.get("revision", 0), 1
            )
            conn.execute(
                "INSERT INTO build_assistant_jobs(id,owner_id,deck_id,generation,state,snapshot_json,created_at,updated_at) "
                "VALUES (?,?,?,1,'interrupted',?,?,?)",
                (token, owner, deck, json.dumps(p), time.time(), time.time()),
            )
            self._sync(conn, owner, deck, p)
            self._save(conn, owner, deck, p)
        return p

    def _rebaseline_nonjournaled(self, conn, row, payload):
        from sabermetrics import assistant_event_store as journal
        from sabermetrics.assistant_events import digest, serialized

        watermark = int(row["last_sequence"]) + 1
        conn.execute(
            "UPDATE build_assistant_jobs SET snapshot_json=?,public_projection_json=NULL,public_baseline_json=NULL,public_domain_hash=? WHERE id=?",
            (json.dumps(payload), digest(payload), row["id"]),
        )
        baseline = journal.ensure_baseline(conn, journal.row_for(conn, row["id"]))
        baseline["run"]["last_sequence"] = watermark
        text = serialized(baseline)
        conn.execute(
            "UPDATE build_assistant_jobs SET last_sequence=?,baseline_sequence=?,public_projection_json=?,public_baseline_json=? WHERE id=?",
            (watermark, watermark, text, text, row["id"]),
        )
        removed = conn.execute(
            "SELECT COUNT(*) FROM build_assistant_events WHERE run_id=?", (row["id"],)
        ).fetchone()[0]
        conn.execute(
            "UPDATE build_assistant_jobs SET compacted_event_count=compacted_event_count+? WHERE id=?",
            (removed, row["id"]),
        )
        conn.execute("DELETE FROM build_assistant_events WHERE run_id=?", (row["id"],))
        conn.execute(
            "DELETE FROM build_assistant_public_snapshots WHERE run_id=?", (row["id"],)
        )

    def _save(self, conn, owner, deck, p):
        p["updated_at"] = time.time()
        p["messages"] = p["messages"][-100:]
        conn.execute(
            "UPDATE build_assistant_sessions SET payload_json=?,version=version+1 WHERE deck_id=? AND owner_id=?",
            (json.dumps(p), deck, owner),
        )
        if p.get("turn_id"):
            conn.execute(
                "UPDATE build_assistant_jobs SET snapshot_json=? WHERE id=? AND owner_id=? AND deck_id=?",
                (json.dumps(p), p["turn_id"], owner, deck),
            )
            from sabermetrics.assistant_events import digest

            conn.execute(
                "UPDATE build_assistant_jobs SET public_domain_hash=? WHERE id=? AND owner_id=? AND deck_id=?",
                (digest(p), p["turn_id"], owner, deck),
            )

    def _job(self, conn, owner, deck, token):
        return conn.execute(
            "SELECT * FROM build_assistant_jobs WHERE id=? AND owner_id=? AND deck_id=?",
            (token, owner, deck),
        ).fetchone()

    def _sync(self, conn, owner, deck, p):
        from sabermetrics.assistant_jobs import view

        if p.get("research_workspace"):
            job = view(self._job(conn, owner, deck, p.get("turn_id")))
            p["research_workspace"]["job"] = job
            if job["state"] in {
                "queued",
                "running",
                "interrupted",
                "stopped",
                "superseded",
            }:
                p["research_workspace"]["status"] = job["state"]

    def get(self, owner_id, deck_id):
        # Reads do not infer interruption from session age. Recovery uses leases.
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            p = self._load(conn, owner_id, deck_id)
            self._sync(conn, owner_id, deck_id, p)
            conn.commit()
            return p

    def _update(self, owner_id, deck_id, change):
        """Preserve the original one-argument session mutation callback."""

        def apply(payload, _conn):
            change(payload)

        return self._update_transaction(owner_id, deck_id, apply)

    def _update_transaction(self, owner_id, deck_id, change):
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            p = self._load(conn, owner_id, deck_id)
            replay = change(p, conn)
            if replay is not None:
                conn.commit()
                return replay
            self._sync(conn, owner_id, deck_id, p)
            self._save(conn, owner_id, deck_id, p)
            conn.commit()
            return p

    def _cancel(self, conn, p, state, **public_changes):
        row = conn.execute(
            "SELECT * FROM build_assistant_jobs WHERE id=?", (p.get("turn_id"),)
        ).fetchone()
        if row and row["state"] in {"queued", "running"}:
            self._end_job(conn, row, state, **public_changes)
        p["busy"] = False
        if row:
            conn.execute(
                "UPDATE build_assistant_jobs SET snapshot_json=?,public_domain_hash=? WHERE id=?",
                (
                    json.dumps(p),
                    __import__(
                        "sabermetrics.assistant_events", fromlist=["digest"]
                    ).digest(p),
                    row["id"],
                ),
            )

    def _end_job(self, conn, row, state, **changes):
        from sabermetrics import assistant_event_store as journal
        from sabermetrics.assistant_events import TERMINAL, timestamp

        if not row or row["state"] in TERMINAL:
            return False
        projection = journal.ensure_baseline(conn, journal.row_for(conn, row["id"]))
        for operation in projection["entities"]["activity"]:
            if operation.get("state") != "started":
                continue
            stage = operation.get("category") == "stage" or operation.get(
                "operation_key"
            ) in {"planning", "comparison", "verification"}
            outcome = (
                "completed"
                if state == "completed" and stage
                else "failed" if state in {"failed", "interrupted"} else "unavailable"
            )
            journal.upsert(
                conn,
                row["id"],
                "activity.finished",
                "activity",
                dict(operation, state=outcome, ended_at=timestamp(time.time())),
            )
        if state == "superseded":
            for finding in projection["entities"]["findings"]:
                journal.upsert(
                    conn,
                    row["id"],
                    "finding.withdrawn",
                    "findings",
                    dict(
                        finding,
                        status="superseded",
                        version=int(finding.get("version", 1)) + 1,
                    ),
                )
            for claim in projection["entities"]["claims"]:
                journal.upsert(
                    conn,
                    row["id"],
                    "claim.updated",
                    "claims",
                    dict(claim, status="superseded"),
                )
        journal.update_run(
            conn,
            row["id"],
            "run.ended",
            lifecycle=state,
            ended_at=timestamp(time.time()),
            **changes,
        )
        conn.execute(
            "UPDATE build_assistant_jobs SET state=?,updated_at=? WHERE id=? AND state IN ('queued','running')",
            (state, time.time(), row["id"]),
        )
        return True

    def _expect(self, conn, p, owner, deck, expected, *, stop=False):
        if expected is None:
            return
        generation = (p.get("research_workspace") or {}).get("generation", 0)
        revision = p.get("revision")
        if (
            expected.get("expected_run_id") != p.get("turn_id")
            or type(expected.get("expected_generation")) is not int
            or expected.get("expected_generation") != generation
            or type(expected.get("expected_revision")) is not int
            or (stop and expected.get("expected_revision") != revision)
        ):
            raise RunConflict("Research changed. Refresh before changing this run.")
        if not stop:
            current = conn.execute(
                "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
                (deck, owner),
            ).fetchone()
            if not current or current[0] != expected.get("expected_revision"):
                raise RunConflict(
                    "Deck changed. Start a new request for this revision."
                )

    def _bound(self, body):
        return body if "expected_run_id" in body else None

    def settings(self, owner_id, deck_id, mode, constraints, *, expected=None):
        if mode not in {"suggest", "automate"}:
            raise ValueError("Choose Suggest or Automate.")
        validated = validate_constraints(constraints)

        def change(p, conn):
            self._expect(conn, p, owner_id, deck_id, expected)
            for key in ("excluded_candidates", "prioritized_candidates"):
                if key in validated and validated[key] != p["constraints"].get(key, []):
                    raise ValueError(
                        "Use research steering to change candidate constraints."
                    )
                if key in p["constraints"]:
                    validated[key] = p["constraints"][key]
            if p["mode"] != mode or p["constraints"] != validated:
                self._cancel(conn, p, "superseded")
            p.update(mode=mode, constraints=validated)

        return self._update_transaction(owner_id, deck_id, change)

    def _begin(
        self,
        conn,
        owner,
        deck,
        p,
        message,
        revision,
        previous=None,
        admission=None,
        request_id=None,
        steering_id=None,
    ):
        from sabermetrics.assistant_workspace import create_workspace

        if not isinstance(message, str) or not 1 <= len(message) <= 16000:
            raise ValueError(
                "Research input, including restart instructions, must be at most 16,000 characters."
            )
        from sabermetrics.assistant_obligations import build_obligations

        obligations = build_obligations(message, revision, constraints=p["constraints"])
        # Admission and invalidation share one SQLite transaction.
        count = conn.execute(
            "SELECT COUNT(*) FROM build_assistant_jobs WHERE state IN ('queued','running') AND id != ?",
            (p.get("turn_id") or "",),
        ).fetchone()[0]
        if count >= 4:
            raise QueueFull("The assistant is busy. Please try shortly.")
        binding = admission() if admission else None
        origin = p.get("turn_id")
        token = uuid.uuid4().hex
        if steering_id and origin:
            from sabermetrics import assistant_event_store as journal

            journal.append(
                conn,
                origin,
                "steering.received",
                {
                    "collection": "steering",
                    "entity": {
                        "id": steering_id,
                        "run_id": origin,
                        "state": "received",
                        "target_run_id": token,
                    },
                },
            )
        self._cancel(conn, p, "superseded", superseded_by_run_id=token)
        p["cost_turn"] = binding
        generation = (p.get("research_workspace") or {}).get("generation", 0) + 1
        p.update(busy=True, turn_id=token, started_at=time.time(), revision=revision)
        p["public_findings_count"] = 0
        p["client_request_id"] = request_id
        p["answer_obligations"] = obligations.model_dump()
        p["messages"].append(
            dict(
                run_id=token,
                id=uuid.uuid4().hex,
                role="user",
                content=message,
                created_at=time.time(),
            )
        )
        p["research_workspace"] = create_workspace(
            message, revision, generation, previous=previous
        )
        conn.execute(
            "INSERT INTO build_assistant_jobs(id,owner_id,deck_id,generation,state,snapshot_json,created_at,updated_at) "
            "VALUES (?,?,?,?,'queued',?,?,?)",
            (token, owner, deck, generation, json.dumps(p), time.time(), time.time()),
        )
        from sabermetrics import assistant_event_store as journal
        from sabermetrics.assistant_events import PublicRun, digest, timestamp

        budget = (binding or {}).get("public_budget", {})
        run = PublicRun(
            run_id=token,
            client_request_id=request_id,
            generation=generation,
            deck_revision=revision,
            lifecycle="queued",
            accepted_at=timestamp(p["started_at"]),
            budget_group_id=digest(binding["id"]) if binding else None,
            budget_started_at=budget.get("started_at"),
            deadline_at=budget.get("deadline_at"),
            steered_from_run_id=origin if steering_id else None,
        )
        journal.create_run(conn, run, owner=owner, deck=deck)
        journal.update_run(
            conn,
            token,
            "run.accepted",
            obligations=[{"id": x.id} for x in obligations.items],
        )
        for obligation in obligations.items:
            journal.upsert(
                conn,
                token,
                "obligation.upserted",
                "obligations",
                dict(obligation.model_dump(), run_id=token),
            )
        if steering_id:
            journal.append(
                conn,
                token,
                "steering.applied",
                {
                    "collection": "steering",
                    "entity": {
                        "id": steering_id,
                        "run_id": token,
                        "origin_run_id": origin,
                        "state": "applied",
                    },
                },
            )

    def begin(
        self,
        owner_id,
        deck_id,
        message,
        revision,
        *,
        request_id=None,
        admission=None,
        expected=None,
    ):
        def change(p, conn):
            fingerprint = json.dumps(
                [deck_id, message, revision, p["mode"], p["constraints"]],
                sort_keys=True,
            )
            if request_id:
                prior = conn.execute(
                    "SELECT fingerprint, turn_id FROM assistant_submissions WHERE owner=? AND request_id=?",
                    (owner_id, request_id),
                ).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise ValueError(
                            "Submission ID was already used with different deck, revision or settings."
                        )
                    if prior[1] == p.get("turn_id"):
                        return p
                    job = self._job(conn, owner_id, deck_id, prior[1])
                    if job:
                        result = json.loads(job["snapshot_json"])
                        result["busy"] = job["state"] in {"queued", "running"}
                        return result
                    raise ValueError(
                        "Original submission is no longer available; use a new submission ID."
                    )
            self._expect(conn, p, owner_id, deck_id, expected)
            if p["busy"]:
                raise ValueError(
                    "The assistant is already working. Stop it before sending another request."
                )
            self._begin(
                conn,
                owner_id,
                deck_id,
                p,
                message,
                revision,
                previous=p.get("research_workspace"),
                admission=admission,
                request_id=request_id,
            )
            if request_id:
                conn.execute(
                    "INSERT INTO assistant_submissions(owner,request_id,fingerprint,turn_id) VALUES (?,?,?,?)",
                    (owner_id, request_id, fingerprint, p["turn_id"]),
                )

        return self._update_transaction(owner_id, deck_id, change)

    def active(self, owner_id, deck_id, turn_id):
        from sabermetrics.assistant_jobs import PROCESS_ID, expired

        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT s.payload_json, j.state, j.lease_owner, j.lease_deadline "
                "FROM deck_documents d "
                "LEFT JOIN build_assistant_sessions s "
                "ON s.deck_id=d.id AND s.owner_id=d.owner_id "
                "LEFT JOIN build_assistant_jobs j "
                "ON j.deck_id=d.id AND j.owner_id=d.owner_id AND j.id=? "
                "WHERE d.id=? AND d.owner_id=?",
                (turn_id, deck_id, owner_id),
            ).fetchone()
            if row is None:
                raise DeckNotFound("Deck not found.")
            if row["payload_json"] is None:
                return False
            p = json.loads(row["payload_json"])
            return bool(
                p["busy"]
                and p["turn_id"] == turn_id
                and row["state"] in {"queued", "running"}
                and not expired(row)
                and (row["state"] == "queued" or row["lease_owner"] == PROCESS_ID)
            )

    def _writable(self, conn, p, row):
        from sabermetrics.assistant_jobs import PROCESS_ID, expired

        if not row or row["state"] not in {"queued", "running"} or expired(row):
            return False
        if row["state"] == "running" and row["lease_owner"] != PROCESS_ID:
            return False
        document = conn.execute(
            "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
            (row["deck_id"], row["owner_id"]),
        ).fetchone()
        return bool(
            document
            and document[0] == p.get("revision")
            and row["generation"]
            == (p.get("research_workspace") or {}).get("generation")
        )

    def append(self, owner_id, deck_id, turn_id, message):
        from sabermetrics.assistant_workspace import apply_workspace_event

        def change(p, conn):
            if p["busy"] and p["turn_id"] == turn_id:
                row = self._job(conn, owner_id, deck_id, turn_id)
                if not self._writable(conn, p, row):
                    return
                item = copy.deepcopy(message)
                event = item.get("action") or {}
                event["generation"] = row["generation"]
                if isinstance(event.get("data"), dict):
                    event["data"]["generation"] = row["generation"]
                if event:
                    p["research_workspace"] = apply_workspace_event(
                        p["research_workspace"], event
                    )
                if event.get("name") == "research_card_update":
                    item["action"] = {
                        "type": "partial_finding",
                        "name": "research_card_update",
                        "ok": event.get("ok"),
                        "generation": row["generation"],
                    }
                    item["content"] = "Research workspace updated."
                item.update(id=uuid.uuid4().hex, created_at=time.time(), run_id=turn_id)
                p["last_publication_nonce"] = message.get("publication_nonce")
                p["messages"].append(item)
                if event.get("type") == "obligations_refined":
                    from sabermetrics import assistant_event_store as journal
                    from sabermetrics.assistant_obligations import AnswerObligations

                    contract = AnswerObligations.model_validate(event["data"])
                    p["answer_obligations"] = contract.model_dump()
                    journal.update_run(
                        conn,
                        turn_id,
                        "obligations.refined",
                        obligations=[{"id": x.id} for x in contract.items],
                    )
                    for obligation in contract.items:
                        journal.upsert(
                            conn,
                            turn_id,
                            "obligation.upserted",
                            "obligations",
                            dict(obligation.model_dump(), run_id=turn_id),
                        )
                self._publish_activity(conn, row, event)
                for finding in message.get("public_findings", []):
                    if self._publish_finding(conn, row, finding):
                        p["public_findings_count"] = (
                            p.get("public_findings_count", 0) + 1
                        )

        return self._update_transaction(owner_id, deck_id, change)

    def finish(self, owner_id, deck_id, turn_id, message):
        from sabermetrics.assistant_workspace import apply_workspace_event

        def change(p, conn):
            row = self._job(conn, owner_id, deck_id, turn_id)
            if not (p["busy"] and p["turn_id"] == turn_id) or not self._writable(
                conn, p, row
            ):
                self._invalidate_job(conn, p, row)
                return
            item = copy.deepcopy(message)
            research = item.get("research") or {}
            if research.get("memory_capture") and research.get("reviewed_episode"):
                research["memory_capture"]["persistence_status"] = "persisted"
            p["messages"].append(
                dict(item, id=uuid.uuid4().hex, created_at=time.time(), run_id=turn_id)
            )
            p["research_workspace"] = apply_workspace_event(
                p["research_workspace"],
                {
                    "type": "workspace_finished",
                    "generation": row["generation"],
                    "result": item,
                },
            )
            fulfillment = item.get("answer_review") or {}
            fulfilled = fulfillment.get("fulfillment") in {"complete", "fulfilled"}
            p["research_workspace"]["status"] = (
                "completed"
                if item.get("status") in {"done", "completed"} and fulfilled
                else "partial"
            )
            p["busy"] = False
            from sabermetrics import assistant_event_store as journal
            from sabermetrics.assistant_events import (
                final_entities,
                review_outcome,
                terminal_result,
            )

            state, runtime_code = terminal_result(item)
            if state == "completed":
                for kind, payload in final_entities(turn_id, item):
                    journal.append(conn, turn_id, kind, payload)
            changes = review_outcome(item, p.get("answer_obligations"))
            if state == "failed":
                changes["fulfillment"] = "partial"
                changes["fulfillment_reason"] = (
                    "Research failed before a complete answer was available."
                )
                changes["missing_obligations"] = list(
                    dict.fromkeys(["answer", *changes["missing_obligations"]])
                )
                code = item.get("error_code") or runtime_code
                if code not in {
                    "turn_expired",
                    "spending_disabled",
                    "budget_exhausted",
                    "tool_limit",
                    "provider_unavailable",
                    "research_failed",
                }:
                    code = "research_failed"
                changes["error"] = {
                    "code": code,
                    "message": "Research could not complete this request.",
                    "retryable": code not in {"spending_disabled", "budget_exhausted"},
                }
            self._end_job(conn, row, state, **changes)
            conn.execute(
                "UPDATE build_assistant_jobs SET snapshot_json=? WHERE id=?",
                (json.dumps(p), turn_id),
            )

        return self._update_transaction(owner_id, deck_id, change)

    def stop(self, owner_id, deck_id, *, expected=None):
        def change(p, conn):
            self._expect(conn, p, owner_id, deck_id, expected, stop=True)
            if p["busy"]:
                p["messages"].append(
                    {
                        "role": "assistant",
                        "content": "Stopped. Requests already in flight may finish; their late results will be discarded.",
                    }
                )
            from sabermetrics import assistant_event_store as journal
            from sabermetrics.assistant_events import timestamp

            journal.update_run(
                conn,
                p.get("turn_id"),
                "stop.requested",
                stop_requested_at=timestamp(time.time()),
            )
            self._cancel(conn, p, "stopped")

        return self._update_transaction(owner_id, deck_id, change)

    def heartbeat(self, owner_id, deck_id, turn_id):
        from sabermetrics.assistant_jobs import LEASE_SECONDS, PROCESS_ID

        with db.connect(self.db_path) as conn:
            changed = conn.execute(
                "UPDATE build_assistant_jobs SET lease_deadline=?,updated_at=? WHERE id=? AND owner_id=? AND deck_id=? AND state='running' AND lease_owner=? AND lease_deadline>=?",
                (
                    time.time() + LEASE_SECONDS,
                    time.time(),
                    turn_id,
                    owner_id,
                    deck_id,
                    PROCESS_ID,
                    time.time(),
                ),
            )
            conn.commit()
            return changed.rowcount == 1

    def checkpoint(self, owner_id, deck_id, turn_id, event):
        def change(p, conn):
            row = self._job(conn, owner_id, deck_id, turn_id)
            if (
                row
                and p["busy"]
                and p["turn_id"] == turn_id
                and self._writable(conn, p, row)
            ):
                checkpoint = json.loads(row["checkpoint_json"])
                calls = checkpoint.setdefault("calls", {})
                call_id = str(event.get("call_id") or "unknown")
                calls[call_id] = event.get("state")
                checkpoint.update(state=event.get("state"), call_id=call_id)
                conn.execute(
                    "UPDATE build_assistant_jobs SET checkpoint_json=?,updated_at=? WHERE id=?",
                    (json.dumps(checkpoint), time.time(), turn_id),
                )

        return self._update_transaction(owner_id, deck_id, change)

    def recover(self, owner_id, deck_id):
        from sabermetrics.assistant_jobs import expired

        def change(p, conn):
            row = self._job(conn, owner_id, deck_id, p.get("turn_id"))
            if row and expired(row):
                # Any executed runtime may have non-provider effects; never replay it implicitly.
                self._end_job(conn, row, "interrupted")
                p["busy"] = False

        return self._update_transaction(owner_id, deck_id, change)

    def claim(self, owner_id, deck_id):
        from sabermetrics.assistant_jobs import LEASE_SECONDS, PROCESS_ID, expired

        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Dead claims on other decks must not monopolize both worker leases.
            for old in conn.execute(
                "SELECT * FROM build_assistant_jobs WHERE state='running'"
            ).fetchall():
                if expired(old):
                    try:
                        old_p = self._load(conn, old["owner_id"], old["deck_id"])
                    except DeckNotFound:
                        self._end_job(conn, old, "superseded")
                        continue
                    self._end_job(conn, old, "interrupted")
                    if old_p.get("turn_id") == old["id"]:
                        old_p["busy"] = False
                        self._sync(conn, old["owner_id"], old["deck_id"], old_p)
                        self._save(conn, old["owner_id"], old["deck_id"], old_p)
            p = self._load(conn, owner_id, deck_id)
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM build_assistant_jobs WHERE state='running'"
                ).fetchone()[0]
                >= 2
            ):
                conn.commit()
                return None
            row = self._job(conn, owner_id, deck_id, p.get("turn_id"))
            if row and row["state"] == "queued" and not self._writable(conn, p, row):
                self._cancel(conn, p, "superseded")
                self._sync(conn, owner_id, deck_id, p)
                self._save(conn, owner_id, deck_id, p)
                conn.commit()
                return None
            if not row or row["state"] != "queued":
                conn.commit()
                return None
            conn.execute(
                "UPDATE build_assistant_jobs SET state='running',lease_owner=?,lease_deadline=?,updated_at=? WHERE id=?",
                (PROCESS_ID, time.time() + LEASE_SECONDS, time.time(), row["id"]),
            )
            from sabermetrics import assistant_event_store as journal
            from sabermetrics.assistant_events import timestamp

            journal.update_run(
                conn,
                row["id"],
                "run.started",
                lifecycle="running",
                worker_started_at=timestamp(time.time()),
            )
            self._sync(conn, owner_id, deck_id, p)
            self._save(conn, owner_id, deck_id, p)
            conn.commit()
            snapshot = json.loads(row["snapshot_json"])
            snapshot["research_workspace"] = p["research_workspace"]
            return snapshot

    def steer(self, owner_id, deck_id, body, revision, *, admission=None):
        request_id = body.get("client_request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ValueError("A client request ID is required.")
        request_json = json.dumps(body, sort_keys=True)

        def change(p, conn):
            prior = conn.execute(
                "SELECT * FROM build_assistant_steering WHERE owner_id=? AND deck_id=? AND request_id=?",
                (owner_id, deck_id, request_id),
            ).fetchone()
            if prior:
                if prior["request_json"] != request_json:
                    raise RunConflict(
                        "Client request ID was already used for another action."
                    )
                previous = json.loads(prior["response_json"])
                original = self._job(conn, owner_id, deck_id, previous.get("turn_id"))
                if original and original["id"] != p.get("turn_id"):
                    result = json.loads(original["snapshot_json"])
                    result["busy"] = original["state"] in {"queued", "running"}
                    return result
                return p
            self._expect(conn, p, owner_id, deck_id, self._bound(body))
            current_revision = conn.execute(
                "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
                (deck_id, owner_id),
            ).fetchone()[0]
            if current_revision != revision:
                raise RunConflict(
                    "Deck changed. Start a request for the current revision."
                )
            workspace = copy.deepcopy(p.get("research_workspace"))
            if (
                not workspace
                or body.get("expected_generation") != workspace["generation"]
                or type(body.get("expected_generation")) is not int
            ):
                raise ValueError("Research generation changed. Reload before steering.")
            action = body.get("action")
            if action not in {
                "pin",
                "unpin",
                "exclude",
                "prioritize",
                "message",
                "retry",
            }:
                raise ValueError("Unknown steering action.")
            origin_row = self._job(conn, owner_id, deck_id, p.get("turn_id"))
            if action in {"message", "exclude", "prioritize"} and (
                not p.get("busy")
                or not origin_row
                or origin_row["state"] not in {"queued", "running"}
            ):
                raise RunConflict(
                    "This research already ended. Start an explicit new attempt before changing its instructions."
                )
            card = None
            if action in {"pin", "unpin", "exclude", "prioritize"}:
                card = next(
                    (
                        c
                        for c in workspace["cards"]
                        if c["card_id"] == body.get("card_id")
                    ),
                    None,
                )
                if card is None:
                    raise ValueError("Choose a card from this research workspace.")
            if action in {"pin", "unpin"}:
                if (
                    action == "pin"
                    and not card.get("pinned")
                    and sum(bool(c.get("pinned")) for c in workspace.get("cards", []))
                    >= 24
                ):
                    raise ValueError(
                        "Keep at most 24 pinned research cards; unpin one first."
                    )
                card["pinned"] = action == "pin"
                p["research_workspace"] = workspace
            else:
                if action == "retry":
                    row = self._job(conn, owner_id, deck_id, p.get("turn_id"))
                    if not row or row["state"] not in {
                        "interrupted",
                        "failed",
                        "stopped",
                        "completed",
                    }:
                        raise ValueError("Only finished work can be retried.")
                steering = workspace.setdefault(
                    "steering", {"excluded_card_ids": [], "prioritized_card_ids": []}
                )
                if action in {"exclude", "prioritize"}:
                    key = (
                        "excluded_card_ids"
                        if action == "exclude"
                        else "prioritized_card_ids"
                    )
                    steering[key] = list(
                        dict.fromkeys([*steering.get(key, []), card["card_id"]])
                    )
                    if len(steering[key]) > 100:
                        raise ValueError("At most 100 cards may be steered.")
                    card["excluded"] = action == "exclude" or card.get(
                        "excluded", False
                    )
                instruction = body.get("message", "")
                if action == "message" and (
                    not isinstance(instruction, str)
                    or not 1 <= len(instruction.strip()) <= 4000
                ):
                    raise ValueError(
                        "Enter an instruction between 1 and 4,000 characters."
                    )
                history = workspace.setdefault("steering_history", [])
                if action == "message":
                    history.append(instruction.strip())
                original = workspace.get(
                    "original_request_text", workspace["request_text"]
                )
                text = original + "".join(
                    "\nAdditional instruction: " + value for value in history
                )
                workspace["original_request_text"] = original
                for item in workspace["cards"]:
                    item["status"] = "superseded"
                constraints = copy.deepcopy(p["constraints"])
                constraints.update(
                    excluded_candidates=steering.get("excluded_card_ids", []),
                    prioritized_candidates=steering.get("prioritized_card_ids", []),
                )
                p["constraints"] = validate_constraints(constraints)
                self._begin(
                    conn,
                    owner_id,
                    deck_id,
                    p,
                    text,
                    revision,
                    previous=workspace,
                    admission=admission,
                    request_id=request_id,
                    steering_id=request_id if action != "retry" else None,
                )
                # Preserve steering metadata even if the reducer only copies cards.
                p["research_workspace"].update(
                    steering=steering,
                    steering_history=history,
                    original_request_text=original,
                )
                conn.execute(
                    "UPDATE build_assistant_jobs SET snapshot_json=? WHERE id=?",
                    (json.dumps(p), p["turn_id"]),
                )
            self._sync(conn, owner_id, deck_id, p)
            conn.execute(
                "INSERT INTO build_assistant_steering VALUES (?,?,?,?,?)",
                (owner_id, deck_id, request_id, request_json, json.dumps(p)),
            )

        return self._update_transaction(owner_id, deck_id, change)

    def release_claim(self, owner_id, deck_id, turn_id):
        """A claimed but unstarted executor failure requires an explicit retry."""
        from sabermetrics.assistant_jobs import PROCESS_ID

        def change(p, conn):
            row = self._job(conn, owner_id, deck_id, turn_id)
            if row and row["state"] == "running" and row["lease_owner"] == PROCESS_ID:
                self._end_job(
                    conn,
                    row,
                    "interrupted",
                    error={
                        "code": "worker_unavailable",
                        "message": "Research could not start. Retry this request.",
                        "retryable": True,
                    },
                )
                if p.get("turn_id") == turn_id:
                    p["busy"] = False

        return self._update_transaction(owner_id, deck_id, change)

    def queued(self):
        with db.connect(self.db_path) as conn:
            return [
                (r["owner_id"], r["deck_id"])
                for r in conn.execute(
                    "SELECT owner_id,deck_id FROM build_assistant_jobs WHERE state='queued' ORDER BY created_at LIMIT 4"
                )
            ]

    def _invalidate_job(self, conn, p, row):
        from sabermetrics.assistant_jobs import PROCESS_ID, expired

        if not row or row["state"] not in {"queued", "running"}:
            return
        interrupted = expired(row)
        if (
            row["state"] == "running"
            and row["lease_owner"] != PROCESS_ID
            and not interrupted
        ):
            return
        self._end_job(conn, row, "interrupted" if interrupted else "superseded")
        # A stale worker can retire only its own job, never a replacement turn.
        if p.get("turn_id") == row["id"]:
            p["busy"] = False

    def invalidate(self, owner_id, deck_id, turn_id):
        def change(p, conn):
            self._invalidate_job(conn, p, self._job(conn, owner_id, deck_id, turn_id))

        return self._update_transaction(owner_id, deck_id, change)

    def _publish_activity(self, conn, row, event):
        """Project approved operation labels; never expose private model reasoning."""
        from sabermetrics import assistant_event_store as journal
        from sabermetrics.assistant_events import digest, timestamp

        kind = event.get("type")
        if kind == "partial_finding" and event.get("name") == "research_progress":
            kind = "research_progress"
        if kind not in {"tool_started", "tool_result", "research_progress"}:
            return
        name = str(event.get("name") or "")
        data = event.get("data") or {}
        label_key = str(data.get("stage") or name)
        labels = {
            "planning": "Reading the request",
            "comparison": "Comparing options",
            "verification": "Checking claims against evidence",
            "research_plan": "Reading the request",
            "search_cards": "Retrieving candidate cards",
            "card_details": "Reading printed card facts",
            "retrieve_rules": "Retrieving rules evidence",
            "check_answer": "Checking claims against evidence",
            "inspect_deck": "Reading deck context",
        }
        projection = journal.ensure_baseline(conn, journal.row_for(conn, row["id"]))
        operations = projection["entities"]["activity"]
        if kind == "research_progress":
            for previous in operations:
                is_stage = previous.get("category") == "stage" or previous.get(
                    "operation_key"
                ) in {"planning", "comparison", "verification"}
                if (
                    is_stage
                    and previous.get("state") == "started"
                    and previous.get("operation_key") != label_key
                ):
                    journal.upsert(
                        conn,
                        row["id"],
                        "activity.finished",
                        "activity",
                        dict(
                            previous, state="completed", ended_at=timestamp(time.time())
                        ),
                    )
        current = next(
            (
                x
                for x in reversed(operations)
                if x.get("operation_key") == label_key and x.get("state") == "started"
            ),
            None,
        )
        if kind == "research_progress" and current:
            return
        if not current and len(operations) >= 256:
            return  # bounded history; no fabricated completion counters
        operation_id = (
            current["id"]
            if current
            else digest([row["id"], label_key, len(operations)])
        )
        entity = dict(
            current or {},
            id=operation_id,
            operation_id=operation_id,
            operation_key=label_key,
            category="stage" if kind == "research_progress" else "tool",
            run_id=row["id"],
            label_key=label_key if label_key in labels else "working",
            label=labels.get(label_key, "Working on your request"),
            state=(
                "started"
                if kind in {"tool_started", "research_progress"}
                else ("unavailable" if event.get("ok") is False else "completed")
            ),
        )
        if kind in {"tool_started", "research_progress"}:
            entity["started_at"] = timestamp(time.time())
        else:
            entity["ended_at"] = timestamp(time.time())
        journal.upsert(
            conn,
            row["id"],
            "activity.started" if entity["state"] == "started" else "activity.finished",
            "activity",
            entity,
        )

    def _publish_finding(self, conn, row, finding):
        from sabermetrics import assistant_event_store as journal
        from sabermetrics.assistant_events import ProjectionTooLarge

        entity = copy.deepcopy(finding)
        entity.update(
            id=entity.pop("finding_id", entity.get("id", "")), run_id=row["id"]
        )
        if not entity["id"] or entity.get("deck_revision") != json.loads(
            row["snapshot_json"]
        ).get("revision"):
            return
        projection = journal.ensure_baseline(conn, journal.row_for(conn, row["id"]))
        if len(projection["entities"]["findings"]) >= 24:
            return
        try:
            return journal.upsert(
                conn, row["id"], "finding.upserted", "findings", entity
            )
        except ProjectionTooLarge:
            placeholder = {
                "id": entity["id"],
                "run_id": row["id"],
                "deck_revision": entity["deck_revision"],
                "text": "Finding unavailable: its complete source exceeds the public display limit. No source conditions were removed.",
                "type": "unavailable",
                "review_state": "unavailable",
                "sources": [],
                "limitations": ["Oversized source artifact withheld."],
            }
            journal.upsert(conn, row["id"], "finding.upserted", "findings", placeholder)
            return False

    def public_events(
        self,
        owner_id,
        deck_id,
        *,
        run_id=None,
        after_sequence=None,
        snapshot_token=None,
        page_index=0,
    ):
        """Return one authorized bounded transport mode; reconnect never admits work."""
        from sabermetrics import assistant_event_store as journal
        from sabermetrics.assistant_events import (
            PAGE_BYTES,
            VERSION,
            serialized,
            timestamp,
        )

        now = time.time()
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            p = self._load(conn, owner_id, deck_id)  # owner/deck authorization
            journal.cleanup(conn, now)
            token_run = None
            if snapshot_token:
                bundle = conn.execute(
                    "SELECT * FROM build_assistant_public_snapshots WHERE token=? AND owner_id=? AND deck_id=?",
                    (snapshot_token, owner_id, deck_id),
                ).fetchone()
                token_run = bundle["run_id"] if bundle else None
            selected = run_id or token_run or p.get("turn_id")
            row = self._job(conn, owner_id, deck_id, selected)
            if row is None:
                conn.commit()
                if selected:
                    raise DeckNotFound("Research run not found.")
                return {
                    "contract_version": VERSION,
                    "server_time": timestamp(now),
                    "run": None,
                    "operational": {
                        "background_work_pending": False,
                        "cost_pending": None,
                        "provider_outcome_unknown": False,
                        "observed_at": timestamp(now),
                    },
                    "mode": "snapshot",
                    "snapshot_page": {
                        "token": None,
                        "page_index": 0,
                        "page_count": 1,
                        "through_sequence": 0,
                        "entities": {},
                    },
                    "cursor": {
                        "through_sequence": 0,
                        "last_sequence": 0,
                        "has_more": False,
                    },
                }
            from sabermetrics.assistant_events import digest

            if row["public_projection_json"]:
                existing_projection = json.loads(row["public_projection_json"])
                mismatch = existing_projection["run"]["lifecycle"] != row["state"]
                mismatch = mismatch or bool(
                    row["public_domain_hash"]
                    and digest(json.loads(row["snapshot_json"]))
                    != row["public_domain_hash"]
                )
                if mismatch:
                    self._rebaseline_nonjournaled(
                        conn, row, json.loads(row["snapshot_json"])
                    )
                    row = self._job(conn, owner_id, deck_id, selected)
            projection = journal.ensure_baseline(conn, row)
            calls = json.loads(row["checkpoint_json"]).get("calls", {})
            unknown = "started" in calls.values()
            operational = {
                "background_work_pending": (
                    True if row["state"] == "running" else (None if unknown else False)
                ),
                "cost_pending": True if unknown else None,
                "provider_outcome_unknown": unknown,
                "observed_at": timestamp(now),
            }
            base = journal.response_base(projection, operational)
            base["history"] = journal.history_metadata(row)
            head = row["last_sequence"]
            baseline = row["baseline_sequence"]
            event_rows = None
            existing_bundle = None
            reason = None
            if snapshot_token:
                existing_bundle = conn.execute(
                    "SELECT * FROM build_assistant_public_snapshots WHERE token=? AND owner_id=? AND deck_id=? AND run_id=? AND expires_at>?",
                    (snapshot_token, owner_id, deck_id, row["id"], now),
                ).fetchone()
                if not existing_bundle:
                    reason = "snapshot_expired"
            elif after_sequence is not None:
                if (
                    type(after_sequence) is not int
                    or after_sequence < 0
                    or after_sequence > head
                ):
                    reason = "invalid_cursor"
                elif after_sequence < baseline:
                    reason = "cursor_compacted"
                else:
                    event_rows = [
                        r[0]
                        for r in conn.execute(
                            "SELECT event_json FROM build_assistant_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                            (row["id"], after_sequence),
                        )
                    ]
            else:
                existing_bundle = conn.execute(
                    "SELECT * FROM build_assistant_public_snapshots WHERE owner_id=? AND deck_id=? AND run_id=? AND expires_at>? AND watermark>=? ORDER BY created_at DESC LIMIT 1",
                    (owner_id, deck_id, row["id"], now, baseline),
                ).fetchone()
            conn.commit()
        # JSON construction/serialization is intentionally outside the writer lock.
        if reason:
            return dict(
                base,
                mode="reset_required",
                cursor={
                    "through_sequence": 0,
                    "last_sequence": head,
                    "has_more": False,
                    "reset_reason": reason,
                },
            )
        if event_rows is not None:
            events = []
            through = after_sequence
            for text in event_rows:
                item = json.loads(text)
                candidate = dict(
                    base,
                    mode="delta",
                    events=[*events, item],
                    cursor={
                        "after_sequence": after_sequence,
                        "through_sequence": item["sequence"],
                        "last_sequence": head,
                        "has_more": item["sequence"] < head,
                    },
                )
                if len(serialized(candidate).encode()) > PAGE_BYTES:
                    break
                events.append(item)
                through = item["sequence"]
            return dict(
                base,
                mode="delta",
                events=events,
                cursor={
                    "after_sequence": after_sequence,
                    "through_sequence": through,
                    "last_sequence": head,
                    "has_more": through < head,
                },
            )
        if existing_bundle:
            pages = json.loads(existing_bundle["pages_json"])
            token = existing_bundle["token"]
            watermark = existing_bundle["watermark"]
        else:
            pages = journal.pages_for(projection)
            token = journal.new_token()
            watermark = head
            with db.connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._load(conn, owner_id, deck_id)
                current_row = journal.row_for(conn, row["id"])
                current_projection = journal.ensure_baseline(conn, current_row)
                head = current_row["last_sequence"]
                baseline = current_row["baseline_sequence"]
                base = journal.response_base(current_projection, operational)
                base["history"] = journal.history_metadata(current_row)
                if watermark < baseline:
                    conn.commit()
                    return dict(
                        base,
                        mode="reset_required",
                        cursor={
                            "through_sequence": 0,
                            "last_sequence": head,
                            "has_more": False,
                            "reset_reason": "snapshot_compacted_during_freeze",
                        },
                    )
                # Concurrent tabs reuse a compatible frozen bundle.
                present = conn.execute(
                    "SELECT * FROM build_assistant_public_snapshots WHERE owner_id=? AND deck_id=? AND run_id=? AND expires_at>? AND watermark>=? ORDER BY created_at DESC LIMIT 1",
                    (owner_id, deck_id, row["id"], now, baseline),
                ).fetchone()
                if present:
                    pages = json.loads(present["pages_json"])
                    token = present["token"]
                    watermark = present["watermark"]
                else:
                    conn.execute(
                        "DELETE FROM build_assistant_public_snapshots WHERE owner_id=? AND deck_id=? AND run_id=?",
                        (owner_id, deck_id, row["id"]),
                    )
                    conn.execute(
                        "INSERT INTO build_assistant_public_snapshots VALUES (?,?,?,?,?,?,?,?)",
                        (
                            token,
                            owner_id,
                            deck_id,
                            row["id"],
                            watermark,
                            now,
                            now + journal.SNAPSHOT_TTL,
                            serialized(pages),
                        ),
                    )
                    stale = conn.execute(
                        "SELECT token FROM build_assistant_public_snapshots WHERE owner_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                        (owner_id, journal.MAX_OWNER_SNAPSHOTS),
                    ).fetchall()
                    for old in stale:
                        conn.execute(
                            "DELETE FROM build_assistant_public_snapshots WHERE token=?",
                            (old[0],),
                        )
                conn.commit()
        if type(page_index) is not int or not 0 <= page_index < len(pages):
            return dict(
                base,
                mode="reset_required",
                cursor={
                    "through_sequence": 0,
                    "last_sequence": head,
                    "has_more": False,
                    "reset_reason": "invalid_page",
                },
            )
        return dict(
            base,
            mode="snapshot",
            snapshot_page={
                "token": token,
                "page_index": page_index,
                "page_count": len(pages),
                "through_sequence": watermark,
                "entities": pages[page_index],
            },
            cursor={
                "through_sequence": watermark,
                "last_sequence": head,
                "has_more": page_index + 1 < len(pages),
            },
        )

    def record_latency(self, owner_id, deck_id, turn_id, report):
        """Persist content-free observations independently of terminal domain events."""
        from sabermetrics.assistant_latency import LatencyReport

        value = LatencyReport.model_validate(report).model_dump(mode="json")
        if value["run_id"] != turn_id:
            raise ValueError("Latency report run mismatch.")
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._job(conn, owner_id, deck_id, turn_id)
            if not row:
                raise DeckNotFound("Research run not found.")
            checkpoint = json.loads(row["checkpoint_json"])
            checkpoint["latency"] = value
            conn.execute(
                "UPDATE build_assistant_jobs SET checkpoint_json=? WHERE id=?",
                (json.dumps(checkpoint), turn_id),
            )
            conn.commit()

    def record_client_latency(self, owner_id, deck_id, body):
        """Store one untrusted client render duration per run and kind.

        Args:
            owner_id: Authenticated owner; the run must belong to this owner/deck.
            deck_id: Deck scope of the run.
            body: Observation fields plus expected run/generation/revision.

        Returns:
            True when stored, False when this kind was already recorded.

        Raises:
            DeckNotFound: The run is not visible to this owner/deck.
            RunConflict: The expected identity no longer matches the run.
            ValueError: The observation is malformed.
        """
        from sabermetrics.assistant_latency import ClientLatencyObservation

        if not isinstance(body, dict):
            raise TypeError("A latency observation object is required.")
        expected = {
            key: body.get(key)
            for key in ("expected_run_id", "expected_generation", "expected_revision")
        }
        fields = {k: v for k, v in body.items() if k not in expected}
        observation = ClientLatencyObservation.model_validate(fields)
        if expected["expected_run_id"] != observation.run_id:
            raise RunConflict("Latency observation run mismatch.")
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._job(conn, owner_id, deck_id, observation.run_id)
            if row is None or not row["public_projection_json"]:
                raise DeckNotFound("Research run not found.")
            run = json.loads(row["public_projection_json"])["run"]
            if (
                type(expected["expected_generation"]) is not int
                or type(expected["expected_revision"]) is not int
                or expected["expected_generation"] != run.get("generation")
                or expected["expected_revision"] != run.get("deck_revision")
            ):
                raise RunConflict("Latency observation targets stale run identity.")
            checkpoint = json.loads(row["checkpoint_json"])
            observed = checkpoint.setdefault("client_latency", {})
            if observation.kind in observed:
                conn.commit()
                return False
            observed[observation.kind] = observation.model_dump(mode="json")
            conn.execute(
                "UPDATE build_assistant_jobs SET checkpoint_json=? WHERE id=?",
                (json.dumps(checkpoint), row["id"]),
            )
            conn.commit()
            return True

    def retire_missing_context(self, owner_id, deck_id, turn_id):
        """Fence an original worker after deletion/ownership loss, without loading a new owner's session."""
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            document = conn.execute(
                "SELECT owner_id FROM deck_documents WHERE id=?", (deck_id,)
            ).fetchone()
            if document is None or document[0] != owner_id:
                row = self._job(conn, owner_id, deck_id, turn_id)
                self._end_job(conn, row, "superseded")
            conn.commit()
