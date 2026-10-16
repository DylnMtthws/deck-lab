"""Pure, revision-specific strategy evidence, adapted from generator contracts.

These bounded predicates describe ingredients and static support. They are not
an Oracle interpreter, a deck quality score, or proof an engine can execute.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.card_capabilities import oracle_text
from sabermetrics.card_economy import card_economy_profile
from sabermetrics.cast_trigger_evidence import cast_trigger_compatibility
from sabermetrics.deck_evidence import _library_entries


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategySupport(EvidenceModel):
    oracle_id: str
    name: str
    quantity: int = Field(ge=1, le=100)


class EngineContract(EvidenceModel):
    key: str
    commander: str
    capability: str
    source_quote: str
    support_count: int = Field(ge=0)
    support: list[StrategySupport]
    support_omitted: int = Field(ge=0)
    status: Literal["inferred_ingredients"] = "inferred_ingredients"


class DrawRoute(EvidenceModel):
    kind: Literal["spell", "activation", "trigger", "unclassified"]
    source_quote: str
    prerequisites: list[str]
    independent: bool = False


class DeckDependency(EvidenceModel):
    key: str
    provider: str
    source_quote: str
    kind: Literal["named_card", "mana_value_access"]
    support_count: int = Field(ge=0)


class StrategyContext(EvidenceModel):
    version: Literal["assistant-strategy.v1"] = "assistant-strategy.v1"
    fingerprint: str
    revision: int
    commanders: list[dict[str, str]]
    source_card_ids: list[str] = Field(default_factory=list)
    preferences: dict[str, Any]
    archetype: str
    contracts: list[EngineContract]
    dependencies: list[DeckDependency] = Field(default_factory=list)
    requirements: list[dict[str, Any]]
    draw_portfolio: dict[str, Any]
    limitations: list[str]


def _deck_dependencies(
    cards: list[dict[str, Any]], library: list[dict[str, Any]]
) -> list[DeckDependency]:
    dependencies = []
    search = re.compile(
        r"search your library for (?:a|an) (?:(artifact|creature|enchantment|instant|sorcery|land|planeswalker|instant or sorcery) )?card with mana value (\d+)( or less| or greater)?(?=[,.])",
        re.IGNORECASE,
    )
    for provider in cards:
        raw = str(provider.get("oracle_text") or "")
        provider_id = str(provider.get("oracle_id") or provider.get("id"))
        for reference in re.finditer(r"\bnamed\b[^.\n]*", raw, re.IGNORECASE):
            for name in sorted({str(c.get("name") or "") for c in library}):
                if name and re.search(
                    r"(?<!\w)" + re.escape(name) + r"(?!\w)",
                    reference[0],
                    re.IGNORECASE,
                ):
                    dependencies.append(
                        DeckDependency(
                            key=provider_id + ":named:" + name,
                            provider=str(provider.get("name") or ""),
                            source_quote=reference[0],
                            kind="named_card",
                            support_count=sum(
                                int(c.get("quantity", 1))
                                for c in library
                                if c.get("name") == name
                            ),
                        )
                    )
        for match in search.finditer(raw):
            wanted = set((match[1] or "").lower().split(" or ")) - {""}
            threshold = int(match[2])
            direction = (match[3] or "").lower()
            count = 0
            for card in library:
                if card.get("oracle_id") == provider.get("oracle_id"):
                    continue
                value = card.get("cmc")
                if value is None:
                    value = card.get("mana_value")
                types = str(card.get("type_line") or "").split("—")[0].lower().split()
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or (wanted and not wanted & set(types))
                ):
                    continue
                matches = (
                    value <= threshold
                    if direction == " or less"
                    else (
                        value >= threshold
                        if direction == " or greater"
                        else value == threshold
                    )
                )
                if matches:
                    count += int(card.get("quantity", 1))
            dependencies.append(
                DeckDependency(
                    key=provider_id + ":access:" + match[0].lower(),
                    provider=str(provider.get("name") or ""),
                    source_quote=match[0],
                    kind="mana_value_access",
                    support_count=count,
                )
            )
    return dependencies


def strategy_capabilities(card: dict[str, Any]) -> set[str]:
    """Recognize bounded ingredients without treating token types as card types."""
    if "//" in str(card.get("type_line") or "") or card.get("card_faces"):
        return set()
    text = oracle_text(card)
    front = str(card.get("type_line") or "").lower()
    types, _, subtypes = front.partition("—")
    result = {"type:" + t for t in re.findall(r"\w+", types)}
    if "creature" in types:
        result.update("tribe:" + t for t in re.findall(r"\w+", subtypes))
    if re.search(r"\bcreate\b[^.\n]*\b(?:creature tokens?|tokens? creatures?)\b", text):
        result.add("creature_token_producer")
    if re.search(r"\bcreate\b[^.\n]*\btreasure tokens?\b", text):
        result.add("treasure_producer")
    if "land" not in types and re.search(
        r"(?:^|\n)\{t\}: add (?:\{[cwubrg]\}|one mana)", text
    ):
        result.add("nonland_tap_mana")
    if re.search(
        r"\byou gain (?:a|one|two|three|four|five|[0-9]+|x|that much) life\b", text
    ):
        result.add("life_gain_source")
    # Only a repeatable activated sacrifice cost establishes this route.
    if re.search(
        r"(?:^|[.\n])[^:\n.]*sacrifice (?:another |a |an )(?:creature|artifact)[^:\n.]*:",
        text,
    ):
        result.add("sacrifice_outlet")
    if re.search(r"\b(?:return|cast|play)\b[^.\n]*\bfrom your graveyard\b", text):
        result.add("graveyard_access")
    if re.search(
        r"(?:^|[.\n])\s*(?:you )?mill\b|put the top[^.\n]*your graveyard", text
    ):
        result.add("graveyard_stock")
    if re.search(r"\bput\b[^.\n]*\+1/\+1 counters?\b", text):
        result.add("counter_producer")
    if re.search(r"(?:^|[.\n])\s*defender\s*(?:[.\n]|$)", text):
        result.add("defender_body")
    return result


def _detected_contracts(commander: dict[str, Any]) -> list[tuple[str, str]]:
    """Return capability and exact commander source quote; unsupported stays absent."""
    raw = str(commander.get("oracle_text") or "")
    if commander.get("card_faces") or "//" in str(commander.get("type_line") or ""):
        return []
    rows: list[tuple[str, str]] = []
    # Reuse the stricter cast-trigger recognizer when counting support below.
    probe = cast_trigger_compatibility(
        commander, {"name": "probe", "type_line": "Instant"}
    )
    if probe["status"] != "unknown":
        rows.append(
            (
                "cast:" + probe["trigger_category"],
                probe["source_excerpts"]["commander_oracle"],
            )
        )
    patterns = (
        (r"Whenever you tap a nonland permanent for mana[^.\n]*", "nonland_tap_mana"),
        (r"Whenever you gain life[^.\n]*", "life_gain_source"),
        (
            r"Whenever (?:one or more )?(?:other )?(?:a |another )?creatures? you control die(?:s)?\b[^.\n]*",
            "sacrifice_outlet",
        ),
        (
            r"Whenever you sacrifice (?:a|an|one or more) (?:creature|artifact|permanent)[^.\n]*",
            "sacrifice_outlet",
        ),
        (
            r"Whenever (?:one or more )?(?:creature )?tokens? (?:you control )?(?:enters?|die|dies)[^.\n]*",
            "creature_token_producer",
        ),
        (
            r"Whenever (?:a|another|one or more) artifacts? (?:you control )?enters?[^.\n]*",
            "type:artifact",
        ),
        (
            r"Whenever (?:a|another|one or more) enchantments? (?:you control )?enters?[^.\n]*",
            "type:enchantment",
        ),
        (
            r"Whenever (?:one or more )?(?:a |another )?(?:\+1/\+1 )?counters? (?:is|are) put[^.\n]*",
            "counter_producer",
        ),
        (
            r"(?:creature|permanent|land) cards? (?:in|from) your graveyard[^.\n]*",
            "graveyard_stock",
        ),
        (r"creatures? (?:you control )?with defender[^.\n]*", "defender_body"),
    )
    for pattern, capability in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            rows.append((capability, match[0]))
    # A printed typal payoff, rather than the commander's own subtype or a token.
    for tribe, plural in (
        ("dragon", "dragons"),
        ("vampire", "vampires"),
        ("goblin", "goblins"),
        ("elf", "elves"),
        ("zombie", "zombies"),
        ("merfolk", "merfolk"),
        ("angel", "angels"),
        ("demon", "demons"),
    ):
        match = re.search(
            rf"\b(?:other )?(?:{plural} you control|{tribe} spells you cast)\b[^.\n]*",
            raw,
            re.IGNORECASE,
        )
        if match:
            rows.append(("tribe:" + tribe, match[0]))
    return list(dict.fromkeys(rows))[:12]


def draw_routes(card: dict[str, Any]) -> list[DrawRoute]:
    """Expose costs and events for printed own draw; absence means unknown coverage."""
    if card.get("card_faces") or "//" in str(card.get("type_line") or ""):
        return []
    raw = re.sub(r"\([^)]*\)", "", str(card.get("oracle_text") or ""))
    routes = []
    for clause in re.split(r"[.\n]", raw):
        draw = re.search(
            r"\b(?:you )?draw (?:a|one|two|three|four|five|[0-9]+|x|that many) cards?\b",
            clause,
            re.IGNORECASE,
        )
        if draw is None:
            continue
        text = clause.lower().strip()
        draw_subject = clause[: draw.start()].rsplit(",", 1)[-1].lower()
        if re.search(
            r"\b(?:target (?:player|opponent)|each (?:player|opponent)|opponents?)\b",
            draw_subject,
        ):
            continue
        prerequisites = []
        kind: Literal["spell", "activation", "trigger", "unclassified"] = "unclassified"
        if re.search(r"\bwhen(?:ever)?\b|at the beginning", text):
            kind = "trigger"
            prerequisites.append("trigger_event_required")
        elif ":" in text:
            kind = "activation"
            prerequisites.append("activation_cost:" + clause.split(":", 1)[0].strip())
        elif any(
            t in str(card.get("type_line") or "").lower()
            for t in ("instant", "sorcery")
        ):
            kind = "spell"
            prerequisites.append("cast_and_resolve")
        for pattern, label in (
            (r"combat damage|\battacks?\b", "combat_required"),
            (r"\bdiscard\b", "discard_required"),
            (r"\bsacrifice\b", "sacrifice_required"),
            (r"\bif\b|\bunless\b|\bmay\b", "conditional_clause"),
            (r"opponent|another player", "opponent_action_or_choice"),
            (r"\bdies\b|\bdie\b", "death_event_required"),
        ):
            if re.search(pattern, text):
                prerequisites.append(label)
        economy = card_economy_profile(card)
        independent = (
            kind == "spell" and economy.get("unconditional_card_access") is True
        )
        if kind == "spell":
            prerequisites.extend(economy.get("requirements", []))
            if economy.get("status") != "supported_from_hand_clause_accounting":
                prerequisites.append("unmodeled_spell_conditions")
        routes.append(
            DrawRoute(
                kind=kind,
                source_quote=clause.strip(),
                prerequisites=sorted(set(prerequisites)),
                independent=independent,
            )
        )
    return routes[:8]


def build_strategy_context(
    document: dict[str, Any], constraints: dict[str, Any], assessment: dict[str, Any]
) -> StrategyContext:
    """Derive shared context from current facts; never reuse across changed inputs."""
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    library = _library_entries(document)
    preferences = {
        k: constraints.get(k)
        for k in ("theme", "power_level", "objective", "protected_cards", "budget")
    }
    fields = (
        "id",
        "oracle_id",
        "card_id",
        "quantity",
        "is_commander",
        "zone_name",
        "name",
        "type_line",
        "oracle_text",
        "mana_cost",
        "mana_value",
        "cmc",
        "color_identity",
    )
    canonical = {
        "revision": document.get("revision", 0),
        "entries": sorted(
            [{k: c.get(k) for k in fields} for c in [*commanders, *library]],
            key=lambda c: str(c.get("id")),
        ),
        "preferences": preferences,
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    capabilities = {id(c): strategy_capabilities(c) for c in library}
    contracts = []
    for commander in commanders:
        for capability, quote in _detected_contracts(commander):
            support = []
            for card in library:
                matches = (
                    cast_trigger_compatibility(commander, card)["matches_trigger"]
                    is True
                    if capability.startswith("cast:")
                    else capability in capabilities[id(card)]
                )
                if matches:
                    support.append(
                        StrategySupport(
                            oracle_id=str(card.get("oracle_id") or ""),
                            name=str(card.get("name") or ""),
                            quantity=int(card.get("quantity", 1)),
                        )
                    )
            contracts.append(
                EngineContract(
                    key=str(commander.get("oracle_id") or commander.get("id"))
                    + ":"
                    + capability,
                    commander=str(commander.get("name") or ""),
                    capability=capability,
                    source_quote=quote,
                    support_count=sum(c.quantity for c in support),
                    support=support[:12],
                    support_omitted=max(0, len(support) - 12),
                )
            )
    portfolio: Counter[str] = Counter()
    route_cards = []
    for card in library:
        routes = draw_routes(card)
        if routes:
            quantity = int(card.get("quantity", 1))
            portfolio[
                (
                    "independent_spell_cards"
                    if any(r.independent for r in routes)
                    else "conditional_or_unclassified_cards"
                )
            ] += quantity
            route_cards.append(
                {
                    "name": str(card.get("name") or ""),
                    "oracle_id": str(card.get("oracle_id") or ""),
                    "routes": [r.model_dump() for r in routes],
                }
            )
    return StrategyContext(
        fingerprint=fingerprint,
        revision=int(document.get("revision", 0)),
        commanders=[
            {
                "oracle_id": str(c.get("oracle_id") or ""),
                "name": str(c.get("name") or ""),
            }
            for c in commanders
        ],
        source_card_ids=sorted(
            {str(c.get("oracle_id") or c.get("id")) for c in [*commanders, *library]}
        ),
        preferences=preferences,
        archetype=str(assessment.get("archetype") or "general"),
        contracts=contracts[:24],
        dependencies=_deck_dependencies([*commanders, *library], library),
        requirements=assessment.get("requirements", []),
        draw_portfolio={
            "counts": dict(portfolio),
            "cards": route_cards[:12],
            "cards_omitted": max(0, len(route_cards) - 12),
        },
        limitations=[
            "Detected printed contracts and ingredient quantities only; coverage is incomplete and battlefield availability is unverified.",
            "Targets are authored starting points, not optimal counts or deck power estimates.",
            "Draw routes are bounded clause evidence; unclassified text is unknown, not unconditional draw.",
            (
                "No supported contract was detected."
                if not contracts
                else "Commander support counts do not establish engine execution or a win condition."
            ),
        ],
    )


def assess_strategy_transition(
    before: StrategyContext, after: StrategyContext
) -> dict[str, Any]:
    """Compare whole-transaction support, permitting coordinated replacements."""
    counts = {c.key: c.support_count for c in after.contracts}
    losses = [
        {"contract": c.key, "before": c.support_count, "after": counts.get(c.key, 0)}
        for c in before.contracts
        if counts.get(c.key, 0) < c.support_count
    ]
    dependency_counts = {d.key: d.support_count for d in after.dependencies}
    remaining_providers = set(after.source_card_ids)
    dependency_losses = [
        {
            "dependency": d.key,
            "provider": d.provider,
            "before": d.support_count,
            "after": dependency_counts.get(d.key, 0),
            "source_quote": d.source_quote,
        }
        for d in before.dependencies
        if d.key.split(":", 1)[0] in remaining_providers
        and dependency_counts.get(d.key, 0) < d.support_count
    ]
    critical_losses = [row for row in losses if row["after"] == 0]
    critical_dependencies = [
        row
        for row in dependency_losses
        if row["after"] == 0 or ":named:" in str(row["dependency"])
    ]
    return {
        "allowed": not critical_losses and not critical_dependencies,
        "review_required": bool(losses or dependency_losses),
        "critical_losses": critical_losses + critical_dependencies,
        "before_fingerprint": before.fingerprint,
        "after_fingerprint": after.fingerprint,
        "losses": losses,
        "dependency_losses": dependency_losses,
        "gains": [
            {
                "contract": c.key,
                "before": c.support_count,
                "after": counts.get(c.key, 0),
            }
            for c in before.contracts
            if counts.get(c.key, 0) > c.support_count
        ],
        "scope": "Detected static commander, named-card and fixed mana-value access support only; full effect equivalence, unparsed access routes, gameplay and quality remain unverified.",
    }


def commander_resource_tradeoffs(
    document: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Expose lost flat-pump opportunities for an exact power-based mana line.

    This mirrors the generator's exact-line commander mana contract. It does not
    assume the commander is present, targeted, unmodified, or able to activate.
    Unknown/variable power effects are not treated as known zero contributions.
    """
    results = []
    pump = re.compile(
        r"\ATarget creature(?: you control)? gets \+([1-9][0-9]?)/[+-][0-9]+ until end of turn\."
    )

    def bonus(card: dict[str, Any]) -> tuple[int, str] | None:
        text = str(card.get("oracle_text") or "")
        if "//" in str(card.get("type_line") or "") or card.get("card_faces"):
            return None
        match = pump.match(text)
        if match:
            return int(match[1]), match[0]
        # Unparsed power effects cannot establish that the old opportunity was
        # removed, or that the new card supplies an equivalent opportunity.
        if re.search(
            r"\bpower\b|target creature[^.\n]*(?:gets|counters?)|base power",
            text,
            re.IGNORECASE,
        ):
            return None
        return 0, ""

    for commander in document.get("entries", []):
        if (
            not commander.get("is_commander")
            or "creature" not in str(commander.get("type_line") or "").lower()
            or "//" in str(commander.get("type_line") or "")
            or commander.get("card_faces")
        ):
            continue
        name = str(commander.get("name") or "")
        line = (
            "{0}: Add X mana in any combination of {U} and/or {R}, where X is "
            + name
            + "'s power. Activate only during your turn and only once each turn."
        )
        if not name or line not in str(commander.get("oracle_text") or "").splitlines():
            continue
        outgoing, incoming, quotes = 0, 0, []
        complete = True
        for old, new in pairs:
            a, b = bonus(old), bonus(new)
            if a is None or b is None:
                complete = False
                continue
            outgoing += a[0]
            incoming += b[0]
            if a[1]:
                quotes.append({"card": str(old.get("name") or ""), "quote": a[1]})
            if b[1]:
                quotes.append({"card": str(new.get("name") or ""), "quote": b[1]})
        if outgoing > incoming:
            results.append(
                {
                    "commander": name,
                    "commander_source_quote": line,
                    "outgoing_recognized_power_bonus": outgoing,
                    "incoming_recognized_power_bonus": incoming,
                    "conditional_mana_opportunity_lost": outgoing - incoming,
                    "card_source_quotes": quotes,
                    "all_changed_power_effects_parsed": complete,
                    "conditions": [
                        "Commander on the battlefield and a legal target",
                        "Pump resolves before an unused activation during your turn",
                        "No unmodeled replacement effects or other power changes",
                    ],
                    "scope": "Recognized temporary flat-power route only; not current power, available mana, net mana profit, or complete resource equivalence. Unknown effects can offset this opportunity.",
                }
            )
    return results


def commander_support(
    document: dict[str, Any], card: dict[str, Any], strategy: StrategyContext
) -> set[str]:
    """Return supported contract identities for ranking, not a quality score."""
    commanders = {
        str(c.get("name") or ""): c
        for c in document.get("entries", [])
        if c.get("is_commander")
    }
    caps = strategy_capabilities(card)
    return {
        contract.key
        for contract in strategy.contracts
        if (
            cast_trigger_compatibility(commanders.get(contract.commander, {}), card)[
                "matches_trigger"
            ]
            is True
            if contract.capability.startswith("cast:")
            else contract.capability in caps
        )
    }
