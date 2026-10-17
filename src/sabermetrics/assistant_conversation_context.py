"""Bounded, quote-backed research preferences, scoped to the supplied session.

These are interpretations of user statements, never action authorization or card
facts. The existing planning call interprets them; code validates provenance.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal[
        "card_type",
        "draw_control",
        "goal",
        "multifunctionality",
        "mana",
        "excluded_card",
    ]
    value: str = Field(
        min_length=1,
        max_length=100,
        description="For card_type use creature or any; draw_control use optional or any; excluded_card use printed card name. Other dimensions describe the user preference without inventing a hard numeric mana ceiling.",
    )
    strength: Literal["required", "preferred", "rejected"]
    quote: str = Field(min_length=8, max_length=300)


def user_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Filter BEFORE limiting: tool messages must not erase the conversation.
    return [m for m in messages if m.get("role") == "user"][-8:]


def load_preferences(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        research = (
            message.get("research")
            or (message.get("presentation") or {}).get("research", {})
            or {}
        )
        memory = research.get("conversation_preferences")
        if isinstance(memory, list):
            valid = []
            for row in memory[:8]:
                try:
                    checked = PreferenceUpdate.model_validate(
                        {k: row[k] for k in PreferenceUpdate.model_fields}
                    )
                except (KeyError, TypeError, ValidationError):
                    continue
                valid.append(
                    {**checked.model_dump(), "source_turn": row.get("source_turn")}
                )
            return valid
    return []


def merge_preferences(
    previous: list[dict[str, Any]],
    updates: list[PreferenceUpdate],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    retained = previous[:]
    turns = user_turns(messages)
    sourced = []
    for update in updates:
        source = next(
            (m for m in reversed(turns) if update.quote in str(m.get("content") or "")),
            None,
        )
        if source is None:
            continue
        sourced.append(
            (
                turns.index(source),
                str(source.get("content") or "").rfind(update.quote),
                update,
                source,
            )
        )
    for _, _, update, source in sorted(sourced, key=lambda row: (row[0], row[1])):
        newer_existing = next(
            (
                row
                for row in retained
                if row["dimension"] == update.dimension
                and row.get("source_turn")
                in {
                    m.get("id") for m in turns[turns.index(source) + 1 :] if m.get("id")
                }
            ),
            None,
        )
        if newer_existing is not None:
            continue
        # Distinct rejected cards can coexist; scalar preferences supersede.
        retained = [
            row
            for row in retained
            if row["dimension"] != update.dimension
            or (update.dimension == "excluded_card" and row["value"] != update.value)
        ]
        retained.append({**update.model_dump(), "source_turn": source.get("id")})
    return retained[-8:]


def preference_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "preferences": load_preferences(messages),
        "user_statements": [
            {"id": m.get("id"), "text": str(m.get("content") or "")[:900]}
            for m in user_turns(messages)[-4:]
        ],
        "scope": "User quotes with model-interpreted preferences. Later user statements supersede earlier ones; not authorization to change a deck. A rejected expensive card does not establish a numeric mana ceiling.",
    }
