"""Bounded, quote-backed research preferences, scoped to the supplied session.

These are interpretations of user statements, never action authorization or card
facts. The existing planning call interprets them; code validates provenance.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal[
        "card_type",
        "draw_control",
        "goal",
        "multifunctionality",
        "mana",
        "excluded_card",
        "core_card",
        "card_type_exception",
        "constraint",
    ]
    value: str = Field(
        min_length=1,
        max_length=100,
        description="For card_type use creature or any; draw_control use optional or any; excluded_card use printed card name. Other dimensions describe the user preference without inventing a hard numeric mana ceiling.",
    )
    polarity: Literal["include", "exclude"] = "include"
    scope: str = Field(default="recommendations", min_length=1, max_length=120)
    operation: Literal["set", "relax"] = "set"
    strength: Literal["required", "preferred", "rejected"]
    quote: str = Field(min_length=8, max_length=300)

    @model_validator(mode="after")
    def canonical_exclusion_polarity(self) -> PreferenceUpdate:
        """Keep legacy excluded-card rows explicitly negative in the new ledger."""
        if self.dimension == "excluded_card":
            self.polarity = "exclude"
        return self


class QuotedPreference(BaseModel):
    """One current-turn interpretation; exact quote provenance is checked later."""

    model_config = ConfigDict(extra="forbid", strict=True)
    value: str = Field(min_length=1, max_length=100)
    strength: Literal["required", "preferred", "rejected"]
    quote: str = Field(min_length=8, max_length=300)


class CardTypePreference(QuotedPreference):
    value: Literal[
        "creature",
        "noncreature",
        "permanent",
        "artifact",
        "instant",
        "sorcery",
        "enchantment",
        "land",
        "planeswalker",
        "battle",
        "any",
    ]


class DrawControlPreference(QuotedPreference):
    value: Literal["optional", "any"] = Field(
        description="optional means the user wants control over whether to start drawing, including voluntary activations; any means this preference was explicitly relaxed."
    )


class PreferenceAssessment(BaseModel):
    """Fixed required slots prevent duplicated dimensions hiding omissions.

    Null means no newly expressed preference, not a negative judgment and not
    permission to clear inherited memory. No extra model call is involved.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    goal: QuotedPreference | None = Field(
        description="Newly expressed strategic goal, such as late-game draw density; null when not expressed in the current request."
    )
    multifunctionality: QuotedPreference | None = Field(
        description="Newly expressed preference about multiple useful roles; null when absent."
    )
    card_type: CardTypePreference | None = Field(
        description="New card type requirement or explicit relaxation; null when absent."
    )
    draw_control: DrawControlPreference | None = Field(
        description="New optionality preference or explicit relaxation; null when absent. Assess separately from the goal and multifunctionality."
    )
    mana: QuotedPreference | None = Field(
        description="New mana preference with its actual scope; rejecting one expensive card does not establish a numeric ceiling. Null when absent."
    )
    excluded_cards: list[QuotedPreference] | None = Field(
        min_length=1,
        max_length=3,
        description="New explicit individual-card exclusions, with printed names; null when absent.",
    )

    scoped_preferences: list[PreferenceUpdate] | None = Field(
        default=None,
        max_length=120,
        description="Explicit scoped constraints, type include/exclude polarity, role-specific mana preferences, core_card membership, and card_type_exception relaxations. Keep core cards separate from excluded_card (excluded from new recommendations). Null when absent. Quote the actual relevant user words; never infer defaults. Use operation=relax only for explicit relaxation and preserve its narrow scope.",
    )

    def updates(self, current_request: str) -> list[PreferenceUpdate]:
        """Flatten only evidence actually quoted from this latest user turn."""
        updates = []
        for dimension in (
            "goal",
            "multifunctionality",
            "card_type",
            "draw_control",
            "mana",
        ):
            evidence = getattr(self, dimension)
            if evidence is not None and evidence.quote in current_request:
                updates.append(
                    PreferenceUpdate(dimension=dimension, **evidence.model_dump())
                )
        for evidence in self.excluded_cards or []:
            if evidence.quote in current_request:
                updates.append(
                    PreferenceUpdate(dimension="excluded_card", **evidence.model_dump())
                )
        updates.extend(self.scoped_preferences or [])
        updates.extend(explicit_preferences(current_request))
        return [
            row
            for row in updates
            if row.quote in current_request and _supported_preference(row)
        ]


def _supported_preference(row: PreferenceUpdate) -> bool:
    """Reject known polarity/default artifacts; lexical checks are not semantic proof."""
    quote = row.quote.casefold()
    if row.dimension == "card_type":
        # A substring match used to invert noncreature into creature.
        if row.value == "creature" and not re.search(r"\bcreatures?\b", quote):
            return False
        if row.value not in {"creature", "any"} and not re.search(
            rf"\b{re.escape(row.value)}s?\b", quote
        ):
            return False
        if row.value == "any" and not re.search(
            r"any|relax|no longer|lift|allow|open to", quote
        ):
            return False
    cues = {
        "draw_control": r"draw|drawing",
        "multifunctionality": r"multifunction|multiple|several|roles|versatil|flexib",
        "mana": r"mana|cost|expensive|cheap|curve|mv\b|cmc\b",
        "excluded_card": r"exclude|avoid|reject|skip|too much|too expensive|no more|do not|don't|already|core|keep",
        "core_card": r"core|keep|retain|already|include|protect|don't cut|do not cut",
        "card_type_exception": r"exception|except|allow|relax|one|single",
    }
    if row.dimension in cues and not re.search(cues[row.dimension], quote):
        return False
    if (
        row.dimension == "goal"
        and "draw" in row.value.casefold()
        and "draw" not in quote
    ):
        return False
    if row.dimension == "draw_control":
        if row.value == "optional" and not re.search(
            r"optional|choos|control|voluntar|may\b", quote
        ):
            return False
        if row.value == "any" and not re.search(
            r"any|relax|allow|no longer|don't care|do not care", quote
        ):
            return False
    if (
        row.dimension in {"mana", "multifunctionality", "goal"}
        and row.value.casefold() == "any"
        and row.operation != "relax"
    ):
        return False
    if row.dimension == "mana":
        for number in re.findall(r"\d+", row.value):
            if not re.search(rf"(?<!\d){number}(?!\d)", quote):
                return False
    if row.dimension == "core_card" and row.value.casefold() not in quote:
        return False
    if row.dimension == "excluded_card":
        # Core membership alone is not a request to exclude recommendations.
        if re.search(r"core|keep", quote) and not re.search(
            r"exclude|avoid|skip|recommend|suggest|add|no more|already", quote
        ):
            return False
    if row.dimension == "constraint" and row.value not in row.quote:
        return False
    return not (
        row.operation == "relax"
        and not re.search(
            r"relax|except|allow|no longer|lift|instead|open to|any", quote
        )
    )


def explicit_preferences(current_request: str) -> list[PreferenceUpdate]:
    """Capture clear list grammar and hard-rule text without guessing semantics.

    Compound rules remain verbatim instead of being split into a misleading
    conjunction. Narrow relaxations retain their exact quoted scope.
    """
    updates = []
    for match in re.finditer(
        r"(?:my\s+)?(?:\w+\s+)?core cards are:\s*([^\n.]+)",
        current_request,
        re.IGNORECASE,
    ):
        for name in match.group(1).split(";"):
            name = name.strip()
            if not name or len(name) > 100:
                continue
            # Include the marker when short enough; each name itself also occurs
            # verbatim in the quote and is retained as an independent member.
            quote = match.group(0)
            if len(quote) > 300:
                quote = name if len(name) >= 8 else "core cards are: " + name
                if quote not in current_request:
                    continue
            updates.append(
                PreferenceUpdate(
                    dimension="core_card", value=name, strength="required", quote=quote
                )
            )
    for match in re.finditer(r"Hard rule:\s*[^.\n]+", current_request, re.IGNORECASE):
        quote = match.group(0)
        if len(quote) <= 300:
            updates.append(
                PreferenceUpdate(
                    dimension="constraint",
                    value=quote[:100],
                    strength="required",
                    quote=quote,
                )
            )
    for match in re.finditer(
        r"[^.\n]*(?:exception|relax|allow)[^.\n]*", current_request, re.IGNORECASE
    ):
        quote = match.group(0).strip()
        if 8 <= len(quote) <= 300:
            updates.append(
                PreferenceUpdate(
                    dimension="constraint",
                    value=quote[:100],
                    strength="required",
                    scope="explicit_exception",
                    operation="relax",
                    quote=quote,
                )
            )
    return updates


def user_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return user turns without allowing tool traffic to displace provenance."""
    return [m for m in messages if m.get("role") == "user"]


def _research(message: dict[str, Any]) -> dict[str, Any]:
    research = message.get("research") or (message.get("presentation") or {}).get(
        "research", {}
    )
    return research if isinstance(research, dict) else {}


def _preference_row(row: Any) -> PreferenceUpdate | None:
    if not isinstance(row, dict):
        return None
    try:
        checked = PreferenceUpdate.model_validate(
            {k: row[k] for k in PreferenceUpdate.model_fields if k in row}
        )
    except (TypeError, ValidationError):
        return None
    return checked if _supported_preference(checked) else None


def load_preferences(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load the latest structured ledger, rejecting unsupported legacy artifacts."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        memory = _research(message).get("conversation_preferences")
        if not isinstance(memory, list):
            continue
        valid = []
        for row in memory:
            checked = _preference_row(row)
            if checked is None:
                continue
            source = next(
                (
                    m
                    for m in messages
                    if m.get("role") == "user"
                    and m.get("id") == row.get("source_turn")
                    and row.get("source_turn") is not None
                ),
                None,
            )
            if source is not None and checked.quote not in str(
                source.get("content") or ""
            ):
                continue
            valid.append(
                {**checked.model_dump(), "source_turn": row.get("source_turn")}
            )
        return valid
    return []


def _preference_key(row: dict[str, Any]) -> tuple[str, ...]:
    dimension = row["dimension"]
    key: tuple[str, ...] = (dimension, row.get("scope", "recommendations"))
    if dimension not in {"core_card", "excluded_card"}:
        key += (row["strength"],)
    if dimension in {
        "excluded_card",
        "core_card",
        "card_type",
        "card_type_exception",
        "constraint",
    }:
        key += (row["value"].casefold(), row.get("polarity", "include"))
    return key


def merge_preferences(
    previous: list[dict[str, Any]],
    updates: list[PreferenceUpdate],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only quoted user intent; unrelated scopes and core lists coexist.

    Explicit relaxations remain in the ledger alongside the original rule so a
    narrow exception cannot erase a global rule. There is no shared row cap.
    """
    retained = [
        {**checked.model_dump(), "source_turn": row.get("source_turn")}
        for row in previous
        if (checked := _preference_row(row)) is not None
    ]
    turns = user_turns(messages)
    sourced = []
    for update in updates:
        if not _supported_preference(update):
            continue
        source_index = next(
            (
                i
                for i in range(len(turns) - 1, -1, -1)
                if update.quote in str(turns[i].get("content") or "")
            ),
            None,
        )
        if source_index is not None:
            sourced.append(
                (
                    source_index,
                    str(turns[source_index].get("content") or "").rfind(update.quote),
                    update,
                )
            )
    for index, _, update in sorted(sourced, key=lambda row: (row[0], row[1])):
        key = _preference_key(update.model_dump())
        later_ids = {m.get("id") for m in turns[index + 1 :] if m.get("id")}
        if any(
            _preference_key(row) == key and row.get("source_turn") in later_ids
            for row in retained
        ):
            continue
        retained = [row for row in retained if _preference_key(row) != key]
        retained.append({**update.model_dump(), "source_turn": turns[index].get("id")})
    return retained


class FindingEvidence(BaseModel):
    """Exact bounded support from a named tool or other evidence source."""

    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=8, max_length=600)


class SessionFinding(BaseModel):
    """A research conclusion carried with evidence, never action authority."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["rules", "empirical", "candidate", "limitation"]
    claim: str = Field(min_length=8, max_length=500)
    evidence: list[FindingEvidence] = Field(min_length=1, max_length=4)
    source_turn: str | None = Field(default=None, max_length=160)


SESSION_FINDINGS_LIMIT = 32


def load_session_findings(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load only structured, provenance-carrying findings, never assistant prose."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        rows = _research(message).get("session_findings")
        if not isinstance(rows, list):
            continue
        valid = []
        for row in rows:
            try:
                valid.append(SessionFinding.model_validate(row).model_dump())
            except (TypeError, ValidationError):
                continue
        return valid[-SESSION_FINDINGS_LIMIT:]
    return []


def merge_session_findings(
    previous: list[dict[str, Any]],
    updates: list[SessionFinding],
    *,
    evidence: dict[str, str],
    source_turn: str | None = None,
) -> list[dict[str, Any]]:
    """Retain at most 32 facts; admit new claims only with matched source quotes.

    Callers must supply actual tool/retrieval evidence, not generated assistant
    prose. Quote matching validates provenance, not truth or entailment; callers
    must preserve uncertainty in the claim and distinguish rules from measurements.
    """
    retained = []
    for row in previous:
        try:
            retained.append(SessionFinding.model_validate(row).model_dump())
        except (TypeError, ValidationError):
            continue
    for finding in updates:
        if not all(
            item.reference in evidence and item.quote in evidence[item.reference]
            for item in finding.evidence
        ):
            continue
        retained = [
            row
            for row in retained
            if (row["kind"], row["claim"]) != (finding.kind, finding.claim)
        ]
        retained.append(
            {**finding.model_dump(), "source_turn": source_turn or finding.source_turn}
        )
    return retained[-SESSION_FINDINGS_LIMIT:]


def preference_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble session interpretations and evidence without granting authority."""
    return {
        "preferences": load_preferences(messages),
        "session_findings": load_session_findings(messages),
        "user_statements": [
            {"id": m.get("id"), "text": str(m.get("content") or "")[:900]}
            for m in user_turns(messages)[-4:]
        ],
        "scope": "User quotes with model-interpreted preferences; findings retain evidence quotes, not verified truth. Preserve required versus preferred, polarity, scopes and narrow exceptions. Core cards are membership, not exclusions from new recommendations. Later explicit changes supersede only matching scopes; unexpressed dimensions stay unchanged. Not authorization to change a deck. A rejected expensive card does not establish a numeric mana ceiling.",
    }
