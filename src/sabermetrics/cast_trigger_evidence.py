"""Conservative cast-trigger evidence, independent of simulator coverage."""

from __future__ import annotations

import re
from typing import Any

from sabermetrics.card_eligibility import supplemental_object_reason


def cast_trigger_compatibility(
    commander: dict[str, Any], card: dict[str, Any]
) -> dict[str, Any]:
    """Check a single, unqualified controller cast trigger against card types.

    This establishes only trigger eligibility when cast, never payoff magnitude,
    timing, affordability, resolution, or native simulation support.
    """
    text = str(commander.get("oracle_text") or "")
    kind = str(card.get("type_line") or "")
    # Requiring the comma excludes qualified triggers such as "your second ...".
    triggers = list(
        re.finditer(
            r"Whenever you cast (?:a|an) (noncreature|instant or sorcery|artifact|creature|enchantment|instant|sorcery)?\s*spell,",
            text,
            re.IGNORECASE,
        )
    )
    result: dict[str, Any] = {
        "commander": commander.get("name"),
        "card": card.get("name"),
        "source_ids": {
            "commander": commander.get("card_id") or commander.get("id"),
            "card": card.get("card_id") or card.get("id"),
        },
        "status": "unknown",
        "matches_trigger": None,
        "trigger_category": None,
        "source_excerpts": {
            "commander_oracle": text[:400],
            "card_type_line": kind[:160],
        },
        "assumptions": [
            "You cast this card as a spell; commander is on the battlefield.",
            "Eligibility only; independent of native simulation coverage.",
        ],
        "reason": "No single supported, unqualified cast trigger in supplied Oracle text.",
    }
    if len(triggers) != 1 or any(mark in text for mark in ('"', "“", "”")):
        return result
    trigger = triggers[0]
    category = (trigger.group(1) or "any").casefold()
    result["trigger_category"] = category
    result["source_excerpts"]["commander_oracle"] = trigger.group(0)
    if re.match(r"\s*if\b", text[trigger.end() :], re.IGNORECASE):
        result["reason"] = "Intervening-if trigger requires additional state."
        return result
    if (
        not kind.strip()
        or "//" in kind
        or card.get("card_faces")
        or "//" in text
        or commander.get("card_faces")
    ):
        result["reason"] = (
            "Missing types or multiple faces require an explicit casting face."
        )
        return result
    if supplemental_object_reason(card):
        result["reason"] = (
            "Supplemental card type does not establish a normally castable spell."
        )
        return result
    types = set(re.findall(r"[A-Za-z]+", kind.split("—", 1)[0].casefold()))
    spell_types = {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "planeswalker",
        "sorcery",
    }
    if "land" in types:
        matches = False
        reason = "Playing a land is not casting a spell."
    elif not types & spell_types:
        result["reason"] = (
            "Supplied type line does not establish a supported spell type."
        )
        return result
    else:
        matches = (
            "creature" not in types
            if category == "noncreature"
            else (
                bool(types & {"instant", "sorcery"})
                if category == "instant or sorcery"
                else True if category == "any" else category in types
            )
        )
        reason = (
            "Printed spell types match the cast trigger."
            if matches
            else "Printed spell types do not match the cast trigger."
        )
    result.update(
        status="compatible" if matches else "incompatible",
        matches_trigger=matches,
        reason=reason,
    )
    return result


def document_cast_trigger_evidence(
    document: dict[str, Any], card: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return separate evidence for each document commander, including partners."""
    return [
        cast_trigger_compatibility(entry, card)
        for entry in document.get("entries", [])
        if isinstance(entry, dict) and entry.get("is_commander")
    ]
