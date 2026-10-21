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


class QueueFull(ValueError):
    pass


class AssistantStore:
    def __init__(self, db_path: str | Path):
        from sabermetrics import assistant_jobs as jobs

        self.db_path = Path(db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS build_assistant_sessions ("
                "deck_id TEXT PRIMARY KEY REFERENCES deck_documents(id) ON DELETE CASCADE, "
                "owner_id TEXT NOT NULL, payload_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0)"
            )
            jobs.initialize(conn)
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

    def _save(self, conn, owner, deck, p):
        p["updated_at"] = time.time()
        p["messages"] = p["messages"][-100:]
        conn.execute(
            "UPDATE build_assistant_sessions SET payload_json=?,version=version+1 WHERE deck_id=? AND owner_id=?",
            (json.dumps(p), deck, owner),
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

    def _cancel(self, conn, p, state):
        conn.execute(
            "UPDATE build_assistant_jobs SET state=?,updated_at=? WHERE id=? AND state IN ('queued','running','interrupted')",
            (state, time.time(), p.get("turn_id")),
        )
        p["busy"] = False

    def settings(self, owner_id, deck_id, mode, constraints):
        if mode not in {"suggest", "automate"}:
            raise ValueError("Choose Suggest or Automate.")
        validated = validate_constraints(constraints)

        def change(p, conn):
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

    def _begin(self, conn, owner, deck, p, message, revision, previous=None):
        from sabermetrics.assistant_workspace import create_workspace

        # Admission and invalidation share one SQLite transaction.
        count = conn.execute(
            "SELECT COUNT(*) FROM build_assistant_jobs WHERE state IN ('queued','running') AND id != ?",
            (p.get("turn_id") or "",),
        ).fetchone()[0]
        if count >= 4:
            raise QueueFull("The assistant is busy. Please try shortly.")
        self._cancel(conn, p, "superseded")
        generation = (p.get("research_workspace") or {}).get("generation", 0) + 1
        token = uuid.uuid4().hex
        p.update(busy=True, turn_id=token, started_at=time.time(), revision=revision)
        p["messages"].append(
            dict(
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

    def begin(self, owner_id, deck_id, message, revision):
        def change(p, conn):
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
            )

        return self._update_transaction(owner_id, deck_id, change)

    def active(self, owner_id, deck_id, turn_id):
        from sabermetrics.assistant_jobs import PROCESS_ID, expired

        with db.connect(self.db_path) as conn:
            p = self._load(conn, owner_id, deck_id)
            row = self._job(conn, owner_id, deck_id, turn_id)
            return bool(
                p["busy"]
                and p["turn_id"] == turn_id
                and row
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
                item.update(id=uuid.uuid4().hex, created_at=time.time())
                p["messages"].append(item)

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
                dict(item, id=uuid.uuid4().hex, created_at=time.time())
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
            calls = json.loads(row["checkpoint_json"]).get("calls", {})
            state = "interrupted" if "started" in calls.values() else "completed"
            conn.execute(
                "UPDATE build_assistant_jobs SET state=?,updated_at=? WHERE id=?",
                (state, time.time(), turn_id),
            )

        return self._update_transaction(owner_id, deck_id, change)

    def stop(self, owner_id, deck_id):
        def change(p, conn):
            if p["busy"]:
                p["messages"].append(
                    {
                        "role": "assistant",
                        "content": "Stopped. Requests already in flight may finish; their late results will be discarded.",
                    }
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
                conn.execute(
                    "UPDATE build_assistant_jobs SET state='interrupted',updated_at=? WHERE id=?",
                    (time.time(), row["id"]),
                )
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
                    old_p = self._load(conn, old["owner_id"], old["deck_id"])
                    conn.execute(
                        "UPDATE build_assistant_jobs SET state='interrupted',updated_at=? WHERE id=?",
                        (time.time(), old["id"]),
                    )
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
            self._sync(conn, owner_id, deck_id, p)
            self._save(conn, owner_id, deck_id, p)
            conn.commit()
            snapshot = json.loads(row["snapshot_json"])
            snapshot["research_workspace"] = p["research_workspace"]
            return snapshot

    def steer(self, owner_id, deck_id, body, revision):
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
                    raise ValueError(
                        "Client request ID was already used for another action."
                    )
                return json.loads(prior["response_json"])
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
                    if not row or row["state"] != "interrupted":
                        raise ValueError("Only interrupted work can be retried.")
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
                    conn, owner_id, deck_id, p, text, revision, previous=workspace
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
        from sabermetrics.assistant_jobs import PROCESS_ID

        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE build_assistant_jobs SET state='queued',lease_owner=NULL,lease_deadline=NULL WHERE id=? AND owner_id=? AND deck_id=? AND state='running' AND lease_owner=?",
                (turn_id, owner_id, deck_id, PROCESS_ID),
            )
            conn.commit()

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
        conn.execute(
            "UPDATE build_assistant_jobs SET state=?,updated_at=? WHERE id=?",
            ("interrupted" if interrupted else "superseded", time.time(), row["id"]),
        )
        # A stale worker can retire only its own job, never a replacement turn.
        if p.get("turn_id") == row["id"]:
            p["busy"] = False

    def invalidate(self, owner_id, deck_id, turn_id):
        def change(p, conn):
            self._invalidate_job(conn, p, self._job(conn, owner_id, deck_id, turn_id))

        return self._update_transaction(owner_id, deck_id, change)
