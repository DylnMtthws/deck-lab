"""Sourced static commander links for retrieval and cut ranking, not execution."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant_strategy import strategy_capabilities
from sabermetrics.cast_trigger_evidence import cast_trigger_compatibility
from sabermetrics.deck_evidence import _library_entries


class EngineLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_oracle_id: str
    provider: str
    commander_oracle_id: str
    commander: str
    relationship: str
    source_quote: str
    commander_quote: str
    conditions: list[str]
    retention_weight: int = Field(ge=1, le=4)
    status: Literal["inferred_static_relationship"] = "inferred_static_relationship"


class EngineGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["assistant-engine-graph.v1"] = "assistant-engine-graph.v1"
    revision: int
    links: list[EngineLink]
    scope: str = (
        "Bounded printed relationships, not a rules interpreter, complete synergy "
        "graph, battlefield observation or measured quality. Retention weights "
        "only order candidate cuts; they do not prohibit a strategic tradeoff."
    )


def card_engine_links(
    document: dict[str, Any], card: dict[str, Any]
) -> list[EngineLink]:
    """Recognize high-value relationship shapes using both printed endpoints."""
    if card.get("card_faces") or "//" in str(card.get("type_line") or ""):
        return []
    raw = str(card.get("oracle_text") or "")
    text = raw.lower()
    links = []
    for commander in document.get("entries", []):
        if not commander.get("is_commander") or commander.get("card_faces"):
            continue
        cmd = str(commander.get("oracle_text") or "")
        cmd_text = cmd.lower()
        shapes: list[tuple[str, str, list[str], int]] = []
        cast = cast_trigger_compatibility(commander, card)
        if cast.get("matches_trigger") is True:
            shapes.append(
                (
                    "cast_trigger_ingredient",
                    str(card.get("type_line") or ""),
                    ["commander on battlefield when the spell is cast"],
                    1,
                )
            )
        # Recognize tribe-qualified multipliers only when the commander has that
        # printed subtype. A produced Wizard token does not make its source one.
        tribes = strategy_capabilities(commander)
        if "additional time" in text and "trigger" in text:
            tribe_match = any(
                "tribe:" + tribe in tribes and re.search(r"\b" + tribe + r"\b", text)
                for tribe in ("wizard", "shaman", "elf", "dragon", "vampire")
            )
            conditional_cast = (
                "casting or copying an instant or sorcery spell" in text
                and re.search(
                    r"whenever you cast (?:an? )?(?:noncreature|instant|sorcery)",
                    cmd_text,
                )
            )
            commander_has_trigger = bool(
                re.search(r"\b(?:when|whenever|at the beginning)\b", cmd_text)
            )
            if (tribe_match and commander_has_trigger) or conditional_cast:
                shapes.append(
                    (
                        "trigger_multiplier",
                        raw,
                        [
                            "both permanents on battlefield",
                            "only triggers satisfying the printed tribe/cast condition",
                            "copies are not casts; use the actual triggering event",
                        ],
                        4,
                    )
                )
        damage = bool(
            re.search(
                r"(?:it|\b"
                + re.escape(str(commander.get("name") or "").lower())
                + r") deals? \d+ damage to each opponent",
                cmd_text,
            )
        )
        if damage and re.search(r"deals damage to an opponent[^.\n]*draw", text):
            attached = "enchanted creature deals damage" in text
            paired = "paired with another creature" in text
            if attached or paired:
                shapes.append(
                    (
                        "commander_damage_to_draw",
                        raw,
                        [
                            (
                                "Aura attached to commander"
                                if attached
                                else "creature paired with commander"
                            ),
                            "damage to an opponent actually occurs",
                            "enough cards in library",
                            "draw replacement effects unmodeled",
                        ],
                        4,
                    )
                )
        if (
            damage
            and "a source you control deals noncombat damage to an opponent" in text
            and "draw" in text
        ):
            shapes.append(
                (
                    "commander_damage_to_draw",
                    raw,
                    [
                        "commander damage is noncombat",
                        "both permanents on battlefield",
                        "damage actually occurs",
                        "enough cards in library",
                    ],
                    4,
                )
            )
        if (
            damage
            and "red source you control" in text
            and "damage" in text
            and "instead" in text
        ):
            shapes.append(
                (
                    "conditional_damage_modifier",
                    raw,
                    [
                        "commander must actually be a red source",
                        "source and recipient must satisfy the printed replacement condition",
                        "replacement interactions unmodeled",
                    ],
                    3,
                )
            )
        if (
            "where x is" in cmd_text
            and "power" in cmd_text
            and "add x mana" in cmd_text
        ):
            if re.search(
                r"(?:equipped|enchanted|target) creature(?: you control)? gets \+[1-9]\d*/\+[0-9]+",
                text,
            ):
                shapes.append(
                    (
                        "power_to_mana_support",
                        raw,
                        [
                            "pump actually applies to commander",
                            "pump before mana activation",
                            "respect commander activation timing and per-turn limit",
                            "pay setup costs; not net mana profit",
                        ],
                        3,
                    )
                )
        if (
            "whenever you tap a nonland permanent for mana" in cmd_text
            and "nonland_tap_mana" in strategy_capabilities(card)
        ):
            shapes.append(
                (
                    "nonland_tap_mana_ingredient",
                    raw,
                    [
                        "both permanents on battlefield",
                        "activate the source's own tap-symbol mana ability",
                        "respect summoning sickness and mana restrictions",
                    ],
                    2,
                )
            )
        if re.search(
            r"whenever (?:another|a|one or more) creatures? you control die(?:s)?",
            cmd_text,
        ):
            if re.search(r"sacrifice (?:another |a |an )creature[^:\n]*:", text):
                shapes.append(
                    (
                        "creature_death_support",
                        raw,
                        [
                            "legal expendable creature under your control",
                            "pay the printed sacrifice activation cost",
                            "commander present for the death event",
                        ],
                        2,
                    )
                )
        if "creature card in your graveyard" in cmd_text:
            from sabermetrics.assistant_deck_facts import graveyard_stock_quote

            stock_quote = graveyard_stock_quote(card)
            if stock_quote:
                shapes.append(
                    (
                        "graveyard_setup",
                        stock_quote,
                        [
                            "creature cards must actually reach your graveyard",
                            "respect commander end-step timing and return conditions",
                            "mill does not guarantee a particular card",
                        ],
                        2,
                    )
                )
        if "tap ten untapped elves you control" in cmd_text:
            if "tribe:elf" in strategy_capabilities(card):
                shapes.append(
                    (
                        "elf_body_for_activation",
                        str(card.get("type_line") or ""),
                        [
                            "Elf on battlefield and untapped",
                            "Lathril plus ten other untapped Elves",
                            "other Elves may be summoning sick; Lathril's own tap symbol requires control since turn began or haste",
                        ],
                        1,
                    )
                )
            if re.search(
                r"untap (?:all creatures you control|all elves you control|target creature)",
                text,
            ):
                shapes.append(
                    (
                        "conditional_commander_untap",
                        raw,
                        [
                            "effect must legally untap commander",
                            "pay printed costs and respect timing",
                            "untapping only Lathril does not untap the ten other Elves",
                        ],
                        2,
                    )
                )
        for relation, quote, conditions, weight in shapes:
            links.append(
                EngineLink(
                    provider_oracle_id=str(
                        card.get("oracle_id")
                        or card.get("card_id")
                        or card.get("id")
                        or ""
                    ),
                    provider=str(card.get("name") or ""),
                    commander_oracle_id=str(
                        commander.get("oracle_id") or commander.get("card_id") or ""
                    ),
                    commander=str(commander.get("name") or ""),
                    relationship=relation,
                    source_quote=quote,
                    commander_quote=cmd,
                    conditions=conditions,
                    retention_weight=weight,
                )
            )
    return links


def build_engine_graph(document: dict[str, Any]) -> EngineGraph:
    """Build links from every library card using current canonical text only."""
    return EngineGraph(
        revision=int(document.get("revision", 0)),
        links=sorted(
            [
                link
                for card in _library_entries(document)
                for link in card_engine_links(document, card)
            ],
            key=lambda e: (
                -e.retention_weight,
                e.provider,
                e.relationship,
                e.commander,
            ),
        ),
    )


def engine_loss_score(
    graph: EngineGraph, outgoing: dict[str, Any], incoming: dict[str, Any] | None = None
) -> int:
    """Rank loss conservatively; replacement fit still requires strategic review."""
    identity = str(
        outgoing.get("oracle_id") or outgoing.get("card_id") or outgoing.get("id") or ""
    )
    # Do not infer equivalent output merely from a matching broad role. The
    # semantic reviewer can justify trading a known relationship for a benefit.
    incoming_text = str((incoming or {}).get("oracle_text") or "")
    identical_source = (
        bool(incoming_text)
        and incoming_text == str(outgoing.get("oracle_text") or "")
        and (incoming or {}).get("type_line") == outgoing.get("type_line")
    )
    return sum(
        link.retention_weight
        for link in graph.links
        if link.provider_oracle_id == identity
        and link.relationship != "cast_trigger_ingredient"
        and not identical_source
    )


def summarize_engine_graph(graph: EngineGraph) -> dict[str, Any]:
    """Keep core links ahead of generic cast ingredients in the model context."""
    return {
        "version": graph.version,
        "revision": graph.revision,
        "links": [
            {
                "provider": link.provider,
                "commander": link.commander,
                "relationship": link.relationship,
                "conditions": link.conditions[:4],
            }
            for link in graph.links[:8]
        ],
        "links_omitted": max(0, len(graph.links) - 8),
        "scope": graph.scope,
    }
