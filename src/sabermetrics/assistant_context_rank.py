"""Transparent request and engine relevance inside the canonical recall pool."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sabermetrics.assistant_deck_facts import feature_quotes
from sabermetrics.assistant_engine_graph import EngineGraph, card_engine_links


class ContextRank(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["assistant-context-rank.v1"] = "assistant-context-rank.v1"
    requested_functions: list[str]
    matched_functions: list[str]
    engine_relationships: list[str]
    conditions: list[str]
    priority: int
    scope: str = (
        "Discovery relevance only; not legality, quality, execution or measured improvement."
    )


def positive_focus(focus: str) -> str:
    """Keep need/add clauses while masking explicit unwanted-function spans."""
    text = focus.lower().replace("’", "'")
    text = re.sub(
        r"\b(?:don't have enough|do not have enough|not enough|too few|lack(?:ing)?(?: of)?)\b",
        "need more",
        text,
    )
    text = re.sub(
        r"\bhave no(?=\s+(?:draw|ramp|mana|protection|removal|recursion)\b)",
        "need more",
        text,
    )
    parts = re.split(r"([,;.!?]|\b(?:but|instead|and|to add|to get)\b)", text)
    kept = []
    negated = False
    for index, part in enumerate(parts):
        if index % 2:
            if part not in {"and", ",", ";"}:
                negated = False
            continue
        if re.match(
            r"\s*(?:(?:i|we|the deck)\s+)?(?:add|need|want|include|find|improve|compare|more)\b",
            part,
        ):
            negated = False
        if negated:
            continue
        match = re.search(
            r"\b(?:don't want|do not want|no|not|without|less|fewer|cut|remove|too much|too many)\b",
            part,
        )
        kept.append(part[: match.start()] if match else part)
        negated = match is not None
    return " ".join(kept)


def requested_functions(focus: str) -> set[str]:
    """Map bounded user vocabulary to functions; no inferred preference changes."""
    words = set(re.findall(r"\w+", positive_focus(focus)))
    result = set()
    for feature, terms in {
        "draw": {"draw", "drawing", "advantage", "flow", "cantrip"},
        "graveyard_stock": {"mill", "selfmill", "filling", "stock"},
        "graveyard_access": {"recursion", "reanimation", "recovery", "reanimate"},
        "sacrifice_outlet": {"sacrifice", "sacrificing", "outlet", "outlets"},
        "protection": {"protection", "protect", "hexproof", "survive"},
        "nonland_tap_mana": {"ramp", "mana", "acceleration"},
        "physical_land": {"land", "lands"},
        "interaction": {"removal", "interaction", "counterspell", "answers"},
    }.items():
        if words & terms:
            result.add(feature)
    if "mana" in words and not words & {"nonland", "rocks", "dorks"}:
        result.add("physical_land")
    return result


def rank_card_context(
    document: dict[str, Any],
    card: dict[str, Any],
    focus: str,
    graph: EngineGraph | None = None,
) -> ContextRank:
    """Rank source-backed function matches ahead of unrelated lexical neighbors."""
    functions = requested_functions(focus)
    features = set(feature_quotes(card))
    if "land" in str(card.get("type_line") or "").lower().split("—")[0]:
        features.add("physical_land")
    from sabermetrics.deck_evidence import _card_roles

    if "removal" in _card_roles(card):
        features.add("interaction")
    links = card_engine_links(document, card)
    matched = functions & features
    relevant_links = [
        link for link in links if link.relationship != "cast_trigger_ingredient"
    ]
    # A generic cast match should not drown a requested draw/land/protection
    # function. With no specific function, direct engine relationships lead.
    priority = 12 * len(matched) + (
        min(8, sum(link.retention_weight for link in relevant_links))
        if not functions or matched
        else 0
    )
    return ContextRank(
        requested_functions=sorted(functions),
        matched_functions=sorted(matched),
        engine_relationships=sorted({link.relationship for link in links}),
        conditions=sorted({c for link in links for c in link.conditions})[:8],
        priority=priority,
    )
