"""Per-owner, per-deck conversations and cancellable assistant turns."""

from __future__ import annotations

import copy
import json
import math
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

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
    cards = value.get("protected_cards", [])
    if (
        not isinstance(cards, list)
        or len(cards) > 100
        or any(not isinstance(card, str) or not 1 <= len(card) <= 100 for card in cards)
    ):
        raise ValueError("Protected cards must be a list of card IDs.")
    result["protected_cards"] = list(dict.fromkeys(cards))
    return result


class AssistantStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS build_assistant_sessions ("
                "deck_id TEXT PRIMARY KEY REFERENCES deck_documents(id) ON DELETE CASCADE, owner_id TEXT NOT NULL, "
                "payload_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0)"
            )
            conn.commit()

    def get(self, owner_id: str, deck_id: str) -> dict[str, Any]:
        initial = {
            "mode": "suggest",
            "constraints": validate_constraints({}),
            "messages": [],
            "busy": False,
            "turn_id": None,
            "updated_at": time.time(),
        }
        with db.connect(self.db_path) as conn:
            if (
                conn.execute(
                    "SELECT id FROM deck_documents WHERE id=? AND owner_id=?",
                    (deck_id, owner_id),
                ).fetchone()
                is None
            ):
                raise DeckNotFound("Deck not found.")
            conn.execute(
                "INSERT INTO build_assistant_sessions(deck_id,owner_id,payload_json) "
                "VALUES (?,?,?) ON CONFLICT(deck_id) DO NOTHING",
                (deck_id, owner_id, json.dumps(initial)),
            )
            conn.commit()
            row = conn.execute(
                "SELECT payload_json,version FROM build_assistant_sessions "
                "WHERE deck_id=? AND owner_id=?",
                (deck_id, owner_id),
            ).fetchone()
        if row is None:
            raise ValueError("Conversation unavailable.")
        payload = cast(dict[str, Any], json.loads(row["payload_json"]))
        if payload.get("busy") and time.time() - payload.get("started_at", 0) > 1200:
            payload.update(busy=False, turn_id=None, updated_at=time.time())
            payload["messages"].append(
                {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "status": "failed",
                    "content": "This task was interrupted or expired. Its saved results remain available; send a new message to continue.",
                    "created_at": time.time(),
                }
            )
            payload["messages"] = payload["messages"][-100:]
            with db.connect(self.db_path) as conn:
                changed = conn.execute(
                    "UPDATE build_assistant_sessions SET payload_json=?,version=version+1 "
                    "WHERE deck_id=? AND owner_id=? AND version=?",
                    (json.dumps(payload), deck_id, owner_id, row["version"]),
                )
                conn.commit()
            if changed.rowcount != 1:
                return self.get(owner_id, deck_id)
        return payload

    def _update(
        self, owner_id: str, deck_id: str, change: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        self.get(owner_id, deck_id)
        for _ in range(10):
            with db.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT payload_json,version FROM build_assistant_sessions "
                    "WHERE deck_id=? AND owner_id=?",
                    (deck_id, owner_id),
                ).fetchone()
                if row is None:
                    raise ValueError("Conversation unavailable.")
                payload: dict[str, Any] = json.loads(row["payload_json"])
                change(payload)
                payload["updated_at"] = time.time()
                payload["messages"] = payload["messages"][-100:]
                changed = conn.execute(
                    "UPDATE build_assistant_sessions SET payload_json=?,version=version+1 "
                    "WHERE deck_id=? AND owner_id=? AND version=?",
                    (json.dumps(payload), deck_id, owner_id, row["version"]),
                )
                conn.commit()
                if changed.rowcount == 1:
                    return payload
        raise ValueError("Conversation changed. Please retry.")

    def settings(self, owner_id: str, deck_id: str, mode: str, constraints: Any):
        if mode not in {"suggest", "automate"}:
            raise ValueError("Choose Suggest or Automate.")
        validated = validate_constraints(constraints)

        def change(p):
            if p["mode"] != mode or p["constraints"] != validated:
                p.update(busy=False, turn_id=None)
            p.update(mode=mode, constraints=validated)

        return self._update(owner_id, deck_id, change)

    def begin(self, owner_id: str, deck_id: str, message: str, revision: int):
        turn_id = uuid.uuid4().hex

        def change(p):
            if p["busy"] and time.time() - p.get("started_at", 0) < 1800:
                raise ValueError(
                    "The assistant is already working. Stop it before sending another request."
                )
            p.update(
                busy=True, turn_id=turn_id, started_at=time.time(), revision=revision
            )
            p["messages"].append(
                {
                    "id": uuid.uuid4().hex,
                    "role": "user",
                    "content": message,
                    "created_at": time.time(),
                }
            )

        return self._update(owner_id, deck_id, change)

    def active(self, owner_id: str, deck_id: str, turn_id: str) -> bool:
        p = self.get(owner_id, deck_id)
        return bool(p["busy"] and p["turn_id"] == turn_id)

    def append(self, owner_id: str, deck_id: str, turn_id: str, message: dict):
        def change(p):
            if p["busy"] and p["turn_id"] == turn_id:
                item = copy.deepcopy(message)
                item.update(id=uuid.uuid4().hex, created_at=time.time())
                p["messages"].append(item)

        return self._update(owner_id, deck_id, change)

    def finish(self, owner_id: str, deck_id: str, turn_id: str, message: dict):
        def change(p):
            if p["turn_id"] == turn_id and p["busy"]:
                p["messages"].append(
                    dict(message, id=uuid.uuid4().hex, created_at=time.time())
                )
                p["busy"] = False

        return self._update(owner_id, deck_id, change)

    def stop(self, owner_id: str, deck_id: str):
        def change(p):
            if p["busy"]:
                p["messages"].append(
                    {
                        "role": "assistant",
                        "content": "Stopped. Any model or simulation request already in flight may finish, but no further experiment steps will start.",
                    }
                )
            p.update(busy=False, turn_id=None)

        return self._update(owner_id, deck_id, change)
