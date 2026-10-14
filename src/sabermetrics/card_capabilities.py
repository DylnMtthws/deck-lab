"""Bounded Oracle predicates adapted from generator intelligence/strategy.py.

Evidence status records a scoped text match; inferred mechanics are never a rules-engine
proof. Relationships describe ingredients; inferred links require play validation.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Relationship:
    kind: Literal["produces", "consumes", "triggers", "payoffs", "prerequisites"]
    resource: str
    status: Literal["verified", "inferred", "unknown"]
    evidence: str
    conditions: tuple[str, ...] = ()
    evidence_status: str = "oracle_text_match"


def oracle_text(card: dict[str, Any]) -> str:
    return re.sub(r"\([^)]*\)", "", str(card.get("oracle_text") or "")).lower()


def own_draw_text(text: str) -> bool:
    """Exclude explicitly opposing recipients; target-player draw remains usable."""
    remaining = re.sub(
        r"\b(?:(?:target|each|an?|that) opponents?|opponents?(?: you choose)?) "
        r"(?:may )?draws? (?:a|an?|one|two|three|four|five|seven|x|[0-9]+|that many) cards?",
        "",
        text,
    )
    return bool(re.search(r"\bdraws?\b", remaining))


def relationships(card: dict[str, Any]) -> list[dict[str, Any]]:
    text = oracle_text(card)
    result: list[Relationship] = []
    previous_conditions: list[str] = []
    for clause in re.split(r"[.\n]", text):
        conditions = list(previous_conditions) if "if you do" in clause else []
        if "if you do" in clause:
            conditions.append("optional_action_required")
        if re.search(r"whenever .*?(?:attacks|combat damage)", clause):
            conditions.append("combat_required")
        if "equipped creature" in clause:
            conditions.append("equipped_creature_required")
        for phrase, prerequisite in [
            ("an opponent casts a spell", "opponent_cast_required"),
            ("cast a noncreature spell", "noncreature_cast_required"),
            ("whenever the ring tempts you", "ring_tempt_required"),
        ]:
            if phrase in clause:
                conditions.append(prerequisite)
        for trigger, resource in [
            ("cast a noncreature spell", "noncreature_cast"),
            ("cast an instant or sorcery", "instant_sorcery_cast"),
            ("whenever a land", "land_entry"),
            ("an opponent casts a spell", "opponent_cast"),
            ("whenever the ring tempts you", "ring_tempt"),
        ]:
            if trigger in clause:
                result.append(
                    Relationship(
                        "triggers",
                        resource,
                        "inferred",
                        clause.strip(),
                        tuple(conditions),
                    )
                )
        for predicate, resource in [
            ("amass orcs", "army"),
            ("create a treasure", "treasure"),
            ("draw a card", "cards"),
            ("draw four cards", "cards"),
            ("discard your hand", "graveyard"),
            ("put a +1/+1 counter", "counters"),
        ]:
            if predicate in clause and (resource != "cards" or own_draw_text(clause)):
                result.append(
                    Relationship(
                        "produces",
                        resource,
                        "inferred",
                        clause.strip(),
                        tuple(conditions),
                    )
                )
        if (
            re.search(r"(?:sacrifice|exile)[^.]*from your graveyard", clause)
            or "delve" in clause
        ):
            result.append(
                Relationship(
                    "consumes",
                    "graveyard",
                    "inferred",
                    clause.strip(),
                    ("graveyard_stock_required",),
                )
            )
        if (
            "power" in clause
            and "add" in clause
            and ("equal to" in clause or re.search(r"where x is [^.,]*power", clause))
        ):
            result.append(
                Relationship(
                    "prerequisites",
                    "power",
                    "inferred",
                    clause.strip(),
                    ("power_required",),
                )
            )
        if (
            "the ring tempts you" in clause
            and "whenever the ring tempts you" not in clause
        ):
            result.append(
                Relationship(
                    "produces",
                    "ring_tempt",
                    "inferred",
                    clause.strip(),
                    tuple(conditions),
                )
            )
        if "damage to each opponent" in clause:
            result.append(
                Relationship(
                    "payoffs",
                    "opponent_damage",
                    "inferred",
                    clause.strip(),
                    tuple(conditions),
                )
            )
        previous_conditions = conditions
    if not result:
        result.append(
            Relationship(
                "prerequisites",
                "unsupported_mechanics",
                "unknown",
                "No supported relationship predicate matched.",
            )
        )
    return [asdict(r) for r in result]


def engine_features(card: dict[str, Any]) -> set[str]:
    text = oracle_text(card)
    types = str(card.get("type_line") or "").split(" // ")[0].lower()
    result = set()
    if "instant" in types:
        result.add("instant_spell")
    if "instant" in types or "sorcery" in types:
        result.add("instant_sorcery_spell")
    if "amass" in text:
        result.add("army_production")
    if any(
        "the ring tempts you" in clause and "whenever the ring tempts you" not in clause
        for clause in re.split(r"[.\n]", text)
    ):
        result.add("ring_tempt")
    if re.search(r"(?:you |^|, )mill (?:a|one|two|three|four|five|[0-9]+|x)", text) or (
        "put the top" in text and "your graveyard" in text
    ):
        result.add("self_mill")
    if (
        any(t in types for t in ("creature", "artifact", "enchantment", "planeswalker"))
        and ("counter" in text or "planeswalker" in types)
        and "counter target" not in text
    ):
        result.add("counter_permanent")
    if re.search(
        r"\breturn\b[^.\n]*\bland cards? from your graveyard to (?:your hand|the battlefield)",
        text,
    ) or re.search(r"\bplay\b[^.\n]*\blands?\b[^.\n]*\bfrom your graveyard\b", text):
        result.add("graveyard_land_access")
    if "discard" in text and "draw" in text:
        result.add("discard_draw")
    return result


def supported_plan(commanders: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    text = "\n".join(oracle_text(c) for c in commanders)
    if "amass orcs" in text and "the ring tempts you" in text:
        return "army_ring_discard", {
            "army_production": 3,
            "ring_tempt": 3,
            "discard_draw": 3,
            "recursion": 3,
        }
    if "land card from your graveyard to the battlefield" in text:
        return "graveyard_lands", {"graveyard_land_access": 4, "self_mill": 3}
    if "proliferate" in text:
        return "counters", {"counter_permanent": 10}
    if "while" in text and "attacking" in text and "copy that spell" in text:
        return "combat_cast", {"instant_spell": 12}
    for tribe in ("dragon", "vampire", "goblin", "elf", "zombie", "merfolk"):
        if re.search(rf"\b{tribe}s?\b", text) and (
            "spells you cast" in text or "number of" in text
        ):
            return "typal", {"creature_type:" + tribe: 16}
    return "general", {}


def casting_cost_basis(card: dict[str, Any]) -> tuple[float, str]:
    text = oracle_text(card)
    raw = card.get("cmc")
    if raw is None:
        raw = card.get("mana_value")
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
        or raw < 0
    ):
        return 3.0, "unknown_effective_cost"
    printed = float(raw)
    if (
        "delve" in text
        or "rather than pay" in text
        or "without paying its mana cost" in text
    ):
        return 3.0, "unknown_effective_cost"
    return printed, "printed_mana_value"
