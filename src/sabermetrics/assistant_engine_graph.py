"""Sourced static commander links for retrieval and cut ranking, not execution."""

from __future__ import annotations

import json
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
    local_edges: list[dict[str, Any]] = Field(default_factory=list)
    local_edges_omitted: int = Field(default=0, ge=0)
    # None identifies older graphs whose display edges were also scoring input.
    # Counts include each incident edge once, before any display truncation.
    local_retention_counts: dict[str, int] | None = None
    scope: str = (
        "Bounded printed relationships, not a rules interpreter, complete synergy "
        "graph, battlefield observation or measured quality. Retention weights "
        "only order candidate cuts; they do not prohibit a strategic tradeoff."
    )

    @property
    def local_retention_status(self) -> str:
        if self.local_retention_counts is not None:
            return "complete_aggregates"
        if self.local_edges_omitted:
            return "incomplete_conservative_fallback"
        return "complete_legacy_edges"


def card_engine_links(
    document: dict[str, Any], card: dict[str, Any]
) -> list[EngineLink]:
    """Recognize high-value relationship shapes using both printed endpoints."""
    if (
        card.get("card_faces")
        or card.get("oracle_text_truncated")
        or "//" in str(card.get("type_line") or "")
    ):
        return []
    raw = str(card.get("oracle_text") or "")
    text = raw.lower()
    links = []
    for commander in document.get("entries", []):
        if (
            not commander.get("is_commander")
            or commander.get("card_faces")
            or commander.get("oracle_text_truncated")
            or "//" in str(commander.get("type_line") or "")
        ):
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
    from sabermetrics.assistant_ability_clauses import ability_clauses

    entries = [
        *list(_library_entries(document)),
        *[c for c in document.get("entries", []) if c.get("is_commander")],
    ]
    # Build-local reuse only: no cross-owner/revision cache or retained input refs.
    parsed_entries = [(card, ability_clauses(card)) for card in entries]
    edges = [
        edge
        for card, parsed in parsed_entries
        for edge in _local_support_edges(card, entries, None, parsed, parsed_entries)
    ]
    counts: dict[str, int] = {}
    for edge in edges:
        for identity in {edge["provider_id"], edge["recipient_id"]}:
            if identity:
                counts[identity] = counts.get(identity, 0) + 1
    # A total content order makes the displayed subset stable even on ties.
    edges.sort(
        key=lambda edge: (
            edge["recipient_id"],
            edge["provider_id"],
            json.dumps(edge, sort_keys=True),
        )
    )
    return EngineGraph(
        local_edges=edges[:128],
        local_edges_omitted=max(0, len(edges) - 128),
        local_retention_counts=dict(sorted(counts.items())),
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
    from sabermetrics.assistant_ability_clauses import card_identity

    identity = card_identity(outgoing)
    # Do not infer equivalent output merely from a matching broad role. The
    # semantic reviewer can justify trading a known relationship for a benefit.
    incoming_text = str((incoming or {}).get("oracle_text") or "")
    identical_source = (
        bool(incoming_text)
        and incoming_text == str(outgoing.get("oracle_text") or "")
        and (incoming or {}).get("type_line") == outgoing.get("type_line")
    )
    if identical_source:
        return 0
    if graph.local_retention_counts is not None:
        local_loss = min(4, graph.local_retention_counts.get(identity, 0))
    elif graph.local_edges_omitted:
        # Unknown endpoints may include this identity. Preserve the existing
        # local cap rather than treating absence from a truncated display as zero.
        local_loss = 4
    else:
        local_loss = min(
            4,
            sum(
                e.get("provider_id") == identity or e.get("recipient_id") == identity
                for e in graph.local_edges
            ),
        )
    return local_loss + sum(
        link.retention_weight
        for link in graph.links
        if link.provider_oracle_id == identity
        and link.relationship != "cast_trigger_ingredient"
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
        "local_edges": [
            {
                key: edge[key]
                for key in (
                    "provider_id",
                    "provider",
                    "recipient_id",
                    "recipient",
                    "relationship",
                    "status",
                )
            }
            for edge in graph.local_edges[:8]
        ],
        "local_edges_omitted": graph.local_edges_omitted
        + max(0, len(graph.local_edges) - 8),
        "local_retention_status": graph.local_retention_status,
        "scope": graph.scope,
    }


def local_support_edges(
    card: dict[str, Any],
    entries: list[dict[str, Any]],
    compatibility: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Directed printed support, with exact endpoints; no transitive/loop inference."""
    from sabermetrics.assistant_ability_clauses import ability_clauses

    parsed = ability_clauses(card)
    if parsed["coverage"] == "unknown":
        return []
    return _local_support_edges(
        card,
        entries,
        compatibility,
        parsed,
        [(provider, ability_clauses(provider)) for provider in entries],
    )


def _local_support_edges(
    card: dict[str, Any],
    entries: list[dict[str, Any]],
    compatibility: dict[str, Any] | None,
    parsed: dict[str, Any],
    parsed_entries: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    from sabermetrics.assistant_ability_clauses import (
        bounded_compatibility,
        card_identity,
    )

    if parsed["coverage"] == "unknown":
        return []
    compatibility = (
        compatibility
        if compatibility is not None
        else bounded_compatibility(card, entries)
    )
    edges = []
    for demand in compatibility["requirements"]:
        for provider in demand["providers"]:
            edges.append(
                {
                    "provider_oracle_id": provider.get("oracle_id"),
                    "provider_id": next(
                        (
                            card_identity(p)
                            for p in entries
                            if p.get("name") == provider.get("name")
                            and p.get("oracle_id") == provider.get("oracle_id")
                        ),
                        "",
                    ),
                    "provider": provider["name"],
                    "recipient_id": card_identity(card),
                    "recipient": card.get("name"),
                    "relationship": demand["dimension"],
                    "required": demand["required"],
                    "source_quote": provider["source_quote"],
                    "recipient_quote": demand["candidate_quote"],
                    "status": "compatible_printed_ingredient",
                    "conditions": [demand["conditions"]],
                }
            )
    # Protection is a potential defensive link, never evidence of actual survival.
    types = str(card.get("type_line") or "").lower().split("—")[0]
    for provider, source in parsed_entries:
        if card_identity(provider) == card_identity(card):
            continue
        if source["coverage"] == "unknown":
            continue
        for clause in source["clauses"]:
            if clause["coverage"] == "unknown" or clause["triggers"]:
                continue
            effect = clause["effects"][0]
            protection = re.search(
                r"\btarget (creature|artifact)(?: you control)? gains? (hexproof|indestructible) until end of turn\.",
                effect,
                re.IGNORECASE,
            )
            if not protection or protection[1].lower() not in types:
                continue
            # Full effect must be the recognized shape: qualifiers and opponent
            # targets are not silently stripped by a substring match.
            if effect.strip() != protection[0]:
                continue
            edges.append(
                {
                    "provider_oracle_id": provider.get("oracle_id"),
                    "provider_id": card_identity(provider),
                    "provider": provider.get("name"),
                    "recipient_id": card_identity(card),
                    "recipient": card.get("name"),
                    "relationship": "potential_protection",
                    "required": protection[2].lower(),
                    "source_quote": clause["source_quote"],
                    "recipient_quote": str(card.get("type_line") or ""),
                    "status": "compatible_printed_ingredient",
                    "conditions": [
                        "Legal target and provider availability required; pay all printed costs.",
                        "Timing, response windows and actual survival remain unverified.",
                    ],
                }
            )
    return edges
