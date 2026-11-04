"""Bounded printed prerequisite matching, never board-state or loop verification."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

VERSION = "assistant-compatibility.v1"
_CONDITIONAL = re.compile(
    r"\bif\b|\bunless\b|\bfor each\b|\bchosen\b|\bonly\b|\bexcept\b", re.IGNORECASE
)
_LEVEL_CLASS = re.compile(r"\blevel\b|\bclass\b", re.IGNORECASE)
_QUOTED_GRANTED = re.compile(r'["“”]|\b(?:have|has)\b', re.IGNORECASE)


def _resource_supported(card: dict[str, Any]) -> bool:
    # Line splitting cannot retain level bands or Class progression scopes.
    return _complete(card) and not (
        str(card.get("layout") or "").lower() in {"leveler", "class"}
        or _LEVEL_CLASS.search(str(card.get("oracle_text") or ""))
        or re.search(r"\bclass\b", str(card.get("type_line") or ""), re.IGNORECASE)
    )


def _unresolved_context(text: str) -> bool:
    return bool(_CONDITIONAL.search(text) or _QUOTED_GRANTED.search(text))


def _complete(card: dict[str, Any]) -> bool:
    return bool(card.get("oracle_text")) and not (
        card.get("oracle_text_truncated")
        or card.get("card_faces")
        or "•" in str(card.get("oracle_text"))
    )


def _identity(card: dict[str, Any]) -> str:
    return str(
        card.get("oracle_id")
        or card.get("card_id")
        or card.get("id")
        or card.get("name")
    )


def _clauses(card: dict[str, Any]) -> list[str]:
    return (
        str(card.get("oracle_text") or "").splitlines()
        if _resource_supported(card)
        else []
    )


@lru_cache(maxsize=8192)
def _tokens(clause: str, required: str) -> bool:
    # Only single supported nouns are matched. Compound type/state restrictions
    # remain unknown instead of treating "artifact creature" as "artifact".
    if required not in {
        "creature",
        "artifact",
        "elf",
        "zombie",
        "soldier",
        "goblin",
        "vampire",
        "wizard",
        "human",
        "spirit",
        "saproling",
        "beast",
    }:
        return False
    # Match the created token itself, never a tribe mentioned elsewhere on its source.
    if re.search(
        r"\bcopy\b|\bnamed\b|\bopponent\b|\btarget player\b|\beach player\b",
        clause,
        re.IGNORECASE,
    ):
        return False
    for match in re.finditer(r"\bcreate ([^.\n]+?tokens?)\b", clause, re.IGNORECASE):
        created = match[1].lower()
        if required == "artifact" and re.search(
            r"\bartifact\b|\btreasure\b|\bfood\b|\bclue\b|\bblood\b", created
        ):
            return True
        if required == "creature" and "creature" in created:
            return True
        if (
            required not in {"artifact", "creature"}
            and re.search(r"\b" + re.escape(required) + r"\b", created)
            and "creature" in created
        ):
            return True
    return False


@lru_cache(maxsize=2048)
def fixed_mana(cost: str) -> int | None:
    """Count mana only after every comma-separated cost is fully recognized.

    This small grammar retains ordinary symbols and explicit resource costs;
    residual prose, variable/hybrid symbols and unsupported costs abstain.
    """
    if _LEVEL_CLASS.search(cost) or _unresolved_context(cost):
        return None
    total = 0
    count = r"(?:a|an|one|two|three|four|five|[0-9]+)"
    resource = (
        r"(?:sacrifice (?:another|an?|this) "
        r"(?:creature|artifact|permanent|land|enchantment|elf|zombie|soldier|"
        r"goblin|vampire|wizard|human|spirit|saproling|beast)(?: you control)?"
        r"|discard " + count + r" cards?"
        r"|pay [0-9]+ life"
        r"|remove " + count + r" (?:[+-][0-9]+/[+-][0-9]+|[a-z]+) counters? "
        r"from this (?:creature|artifact|permanent))"
    )
    for component in cost.strip().split(","):
        component = component.strip()
        if re.fullmatch(r"(?:\{(?:[0-9]+|[WUBRGCTQ])\}\s*)+", component, re.IGNORECASE):
            symbols = re.findall(r"\{([^}]+)\}", component.upper())
            total += sum(
                int(s) if s.isdecimal() else 0 if s in {"T", "Q"} else 1
                for s in symbols
            )
        elif not re.fullmatch(resource, component, re.IGNORECASE):
            return None
    return total


@lru_cache(maxsize=16384)
def _provider_matches(
    quote: str, dimension: str, required: str, type_line: str
) -> bool:
    """Cache literal source judgments only; no card/owner/revision state is retained."""
    lower = quote.lower()
    compatible = False
    if dimension == "fodder":
        compatible = not required.startswith("nontoken ") and _tokens(quote, required)
    elif dimension == "counters":
        if not re.search(
            r"\bcreature\b|\bartifact\b",
            type_line,
            re.IGNORECASE,
        ):
            return False
        target_type = "artifact" if "artifact" in type_line.lower() else "creature"
        compatible = bool(
            re.search(
                r"\bput (?:a|one|\d+) "
                + re.escape(required)
                + r" counters? on (?:target|each|another) "
                + target_type
                + r"(?:[.,]|$| you control)",
                lower,
            )
        )
        # Additional targeting qualifiers require a richer matcher.
        compatible = compatible and not re.search(
            r"opponent|another target|other than|with |without |that |except|and/or",
            lower,
        )
    elif dimension == "sacrifice":
        compatible = bool(re.search(r"\bsacrifice (?:a|another) creature\s*:", lower))
    elif dimension == "recursion":
        compatible = (
            bool(
                re.search(
                    r"return target creature card from your graveyard to the battlefield[.,]?$",
                    lower,
                )
            )
            and "creature" in type_line.lower()
        )
    return bool(compatible)


def ability_compatibility(
    card: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Each credited provider carries both source clauses and residual conditions.

    A matching source establishes only a printed ingredient, not availability,
    sustainable supply, a legal action trace, or strategic superiority.
    """
    requirements: list[dict[str, Any]] = []
    abilities = []
    providers = [c for c in entries if _identity(c) != _identity(card) and _complete(c)]
    candidate_unresolved = _unresolved_context(str(card.get("oracle_text") or ""))
    for line in _clauses(card):
        cost, effect = line.split(":", 1) if ":" in line else (None, line)
        if cost is not None:
            abilities.append(
                {
                    "source_quote": line,
                    "activation_cost": cost.strip(),
                    "fixed_mana_per_activation": (
                        None if candidate_unresolved else fixed_mana(cost)
                    ),
                    "consumes_source": bool(
                        re.search(
                            r"\bsacrifice (?:this (?:creature|artifact|permanent)|"
                            + re.escape(str(card.get("name") or "__unknown__"))
                            + r")\b",
                            cost,
                            re.IGNORECASE,
                        )
                    ),
                    "first_use": "Pay casting cost separately; satisfy every printed activation cost, target and restriction.",
                    "further_uses": "Pay costs again; replenish consumed permanents/cards/counters and untap a tapped source. Returning to hand also requires recasting. No refunds assumed.",
                }
            )
        demands: list[tuple[str, str]] = []
        if cost:
            sacrifice = re.search(
                r"\bsacrifice (another|an?|this) ([^,:]+)", cost, re.IGNORECASE
            )
            if sacrifice and sacrifice[1].lower() != "this":
                requirement = sacrifice[2].strip().lower()
                requirement = re.sub(r" you control$", "", requirement)
                demands.append(("fodder", requirement))
            elif re.search(r"\bsacrifice\b", cost, re.IGNORECASE):
                demands.append(("recursion", "source return"))
            counter = re.search(
                r"remove (?:a|one|\d+) ([+\w/-]+) counters? from (?:this|"
                + re.escape(str(card.get("name") or "__unknown__"))
                + r")",
                cost,
                re.IGNORECASE,
            )
            if counter:
                demands.append(("counters", counter[1].lower()))
        # Self-death does not consume some other creature; token production is irrelevant.
        if re.search(r"\bwhen(?:ever)?\b[^.\n]*\bdie(?:s)?\b", effect, re.IGNORECASE):
            opposing_event = bool(
                re.search(r"\bopponents?\b[^.\n]*\bdie(?:s)?\b", effect, re.IGNORECASE)
            )
            demands.append(
                ("opponent_death" if opposing_event else "sacrifice", "creature")
            )
            if not opposing_event and not re.search(
                r"\bwhen (?:this creature|"
                + re.escape(str(card.get("name") or "__unknown__"))
                + r") dies\b",
                effect,
                re.IGNORECASE,
            ):
                death = re.search(
                    r"(?:another|an?|one or more) (nontoken )?([a-z]+) (?:you control )?die(?:s)?\b",
                    effect,
                    re.IGNORECASE,
                )
                if death:
                    demands.append(
                        (
                            "fodder",
                            ("nontoken " if death[1] else "")
                            + death[2].lower().rstrip("s"),
                        )
                    )
        for dimension, required in demands:
            matches = []
            unresolved = []
            for provider in providers:
                for quote in _clauses(provider):
                    compatible = _provider_matches(
                        quote, dimension, required, str(card.get("type_line") or "")
                    )
                    if not compatible:
                        continue
                    record = {
                        "name": provider.get("name"),
                        "oracle_id": provider.get("oracle_id"),
                        "source_quote": quote,
                    }
                    if (
                        candidate_unresolved
                        or _unresolved_context(str(provider.get("oracle_text") or ""))
                        or any(x in quote for x in ("•", "—"))
                    ):
                        unresolved.append(record)
                    else:
                        matches.append(record)
                    break
            requirements.append(
                {
                    "dimension": dimension,
                    "required": required,
                    "candidate_quote": line,
                    "status": "compatible_printed_ingredient" if matches else "unknown",
                    "providers": matches[:3],
                    "providers_omitted": max(0, len(matches) - 3),
                    "conditional_leads": unresolved[:1],
                    "conditions": "Provider must be available, its costs and restrictions satisfied; supply and repeatability remain unverified.",
                }
            )
    return {
        "version": VERSION,
        "status": (
            "bounded_printed_matching" if _resource_supported(card) else "unknown"
        ),
        "casting_cost": card.get("mana_cost"),
        "printed_initial_resources": [
            line
            for line in _clauses(card)
            if re.search(r"\benters(?: the battlefield)? with\b", line, re.IGNORECASE)
        ],
        "abilities": abilities,
        "requirements": requirements,
        "scope": "Partial clause patterns. Unmatched requirements are unknown, never proof of absent support. Printed ingredients do not establish a repeatable engine or battlefield state.",
    }


def compatibility_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Compact model view; exact candidate/provider text travels separately."""
    return {
        "status": profile.get("status", "unknown"),
        "requirements": [
            {
                "kind": r["dimension"],
                "needs": r["required"],
                "status": r["status"],
                "providers": [p["name"] for p in r["providers"]],
            }
            for r in profile.get("requirements", [])[:4]
        ],
        "omitted_requirements": max(0, len(profile.get("requirements", [])) - 4),
        "activation_costs": [
            a["activation_cost"] for a in profile.get("abilities", [])[:3]
        ],
        "initial_resource_clauses": profile.get("printed_initial_resources", [])[:1],
        "repeatability": "Unverified; pay all costs again and replenish consumed resources. Recursion to hand requires recasting.",
    }


def compare_candidates(cards: list[dict[str, Any]]) -> None:
    """Attach one bounded comparison of fixed activation mana; no dominance claim."""
    for card in cards[:8]:
        # Recomputing after a source/profile change must not retain old credit.
        card.pop("functional_comparison", None)
        own = (
            card.get("suitability", {})
            .get("ability_compatibility", {})
            .get("abilities", [])
        )
        if len(own) != 1 or own[0]["fixed_mana_per_activation"] is None:
            continue
        other = next(
            (
                c
                for c in cards[:8]
                if c is not card
                and len(
                    c.get("suitability", {})
                    .get("ability_compatibility", {})
                    .get("abilities", [])
                )
                == 1
            ),
            None,
        )
        if other is None:
            continue
        ability = other["suitability"]["ability_compatibility"]["abilities"][0]
        if ability["fixed_mana_per_activation"] is None:
            continue
        card["functional_comparison"] = {
            "against_card_id": other.get("id"),
            "against_name": other.get("name"),
            "own_casting_cost": card.get("mana_cost"),
            "other_casting_cost": other.get("mana_cost"),
            "own_ability_quote": own[0]["source_quote"],
            "other_ability_quote": ability["source_quote"],
            "own_activation_cost": own[0]["activation_cost"],
            "other_activation_cost": ability["activation_cost"],
            "fixed_activation_mana_difference": own[0]["fixed_mana_per_activation"]
            - ability["fixed_mana_per_activation"],
            "scope": "Printed activation mana comparison only; effects, casting, resource costs, timing and prerequisites must also be compared. No strict superiority inferred.",
        }
