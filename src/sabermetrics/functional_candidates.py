"""Conservative, auditable pair-level improvements, not optimal-card ratings."""

from __future__ import annotations

import json
import re
from typing import Any

from sabermetrics.card_economy import card_economy_profile, printed_cast_trigger_damage


def functional_improvement(
    old: dict[str, Any], new: dict[str, Any], old_caps: set[str], new_caps: set[str]
) -> dict[str, Any] | None:
    """Require a cheaper cast and a concrete equivalent function.

    Text identity ignores a card's own name, but never conditional costs, timing,
    quantities or targeting. Mana comparison deliberately handles only simple
    unconditional one-mana rocks; Signets, dorks and combo engines need review.
    """
    old_mv, new_mv = (
        old.get("cmc", old.get("mana_value")),
        new.get("cmc", new.get("mana_value")),
    )
    if old_mv is None or new_mv is None or float(new_mv) >= float(old_mv):
        return None
    if any(
        x in str(c.get("mana_cost") or "").upper() for c in (old, new) for x in ("{X}",)
    ):
        return None

    def normalized(c):
        text = str(c.get("oracle_text") or "").lower()
        text = (
            text.replace(str(c.get("name") or "").lower(), "this card")
            if c.get("name")
            else text
        )
        return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", text)).strip()

    old_text, new_text = normalized(old), normalized(new)
    shared = (
        old_caps
        & new_caps
        & {"draw", "card_selection", "removal", "protection", "recursion", "land_ramp"}
    )
    function = None
    if (
        shared
        and old_text == new_text
        and old_text
        and old.get("type_line") == new.get("type_line")
    ):
        function = "identical_oracle_function"
    elif {"direct_mana_source", "nonland_mana_resource"} <= old_caps & new_caps:
        if all(
            "artifact" in str(c.get("type_line") or "").lower()
            and "creature" not in str(c.get("type_line") or "").lower()
            for c in (old, new)
        ):
            one = r"\{t\}: add (?:one mana of any color(?: in your commander[’']s color identity)?|\{([cwubrg])\})\."
            old_match, new_match = re.search(one, old_text), re.search(one, new_text)
            if old_match and new_match and old_match.group(1) == new_match.group(1):
                remainder = new_text.replace(new_match.group(0), "").strip()
                if (
                    not remainder
                    and not old_text.replace(old_match.group(0), "").strip()
                    and "enters tapped" not in new_text
                ):
                    function = "same_one_mana_output"
    if not function:
        return None
    return {
        "kind": function,
        "mana_value_reduction": float(old_mv) - float(new_mv),
        "benefit": "Lower printed casting cost while preserving the compared function",
        "requirements": ["Normal printed-cost cast; alternate costs are not evaluated"],
        "tradeoffs": [
            "Other abilities and colored mana requirements require strategic review",
            "Cheaper printed cost does not establish greater win probability",
        ],
    }


def oracle_facts(card: dict[str, Any]) -> dict[str, Any]:
    return {
        key: card.get(key)
        for key in (
            "id",
            "card_id",
            "oracle_id",
            "name",
            "type_line",
            "mana_cost",
            "cmc",
            "color_identity",
        )
    } | {
        "card_economy": card_economy_profile(card),
        "printed_cast_trigger_damage": printed_cast_trigger_damage(card),
        "cmc": card.get("cmc", card.get("mana_value")),
        "oracle_text": str(card.get("oracle_text") or "")[:6000],
        "oracle_text_truncated": len(str(card.get("oracle_text") or "")) > 6000,
        "provenance": "Imported Oracle card record",
        "citation": "https://scryfall.com/search?q=oracleid%3A"
        + str(card.get("oracle_id") or ""),
    }


def strategic_tradeoff(old, new, old_caps, new_caps):
    """Propose cheaper spell-based card access, exposing unequal effects for review."""
    if not all(
        any(
            kind in str(card.get("type_line") or "").lower()
            for kind in ("instant", "sorcery")
        )
        for card in (old, new)
    ):
        return None
    old_mv, new_mv = (
        old.get("cmc", old.get("mana_value")),
        new.get("cmc", new.get("mana_value")),
    )
    if old_mv is None or new_mv is None or float(new_mv) >= float(old_mv):
        return None
    old_text, new_text = (
        str(card.get("oracle_text") or "").lower() for card in (old, new)
    )
    # Printed costs are misleading for these mechanics. They need their own
    # resource model and cannot justify a cheaper-card proposal here.
    if any(
        re.search(
            r"\b(?:delve|convoke|affinity|improvise|suspend|cascade|discover)\b|rather than pay|without paying|costs?[^.]*less",
            text,
        )
        for text in (old_text, new_text)
    ):
        return None
    if any("{X}" in str(card.get("mana_cost") or "").upper() for card in (old, new)):
        return None
    incoming_requirements = set(card_economy_profile(new)["requirements"])
    if incoming_requirements & {
        "legal_target_spell",
        "legal_target_creature",
        "legal_target_aura_you_control",
        "legal_target_aura",
        "legal_target_nonplayer",
        "legal_target_any_eligible",
    }:
        return None
    shared_access = old_caps & new_caps & {"draw", "card_selection"}
    if not shared_access:
        return None
    # A single unconditional resolution is a bounded comparable shape. Never
    # substitute a delayed/triggered/mana-intensive engine for a cheap spell.
    if re.search(
        r"\b(?:whenever|if|unless|at the beginning|additional cost|sacrifice|pay|opponent)\b|target player",
        new_text,
    ):
        return None
    if "draw" in shared_access and not re.search(
        r"(?:^|[.\n])\s*(?:you )?draw (?:a|one|two|three|four) cards?", new_text
    ):
        return None
    if "triggered_ability" in old_caps or "triggered_ability" in new_caps:
        return None
    return {
        "kind": "strategic_tradeoff",
        "shared_functions": sorted(shared_access),
        "mana_value_reduction": float(old_mv) - float(new_mv),
        "benefit": "Lower printed casting cost for a spell sharing card-access functionality; effects are not equivalent",
        "requirements": [
            "Normal printed-cost cast",
            "Strategic reviewer must assess card quantity, selection depth, timing and graveyard reuse",
        ],
        "tradeoffs": [
            "Outgoing and incoming draw quantity and selection may differ",
            "Outgoing flashback, other reuse, targeting and additional effects may be lost",
            "Incoming discard or cards returned to library may reduce net advantage",
            "Full Oracle text and deck plan must justify the tradeoff; no universal upgrade claimed",
        ],
    }


_BASIC_COLORS = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}


def basic_land_color_tradeoff(document, old, new):
    """Bounded zero-source repair, requiring complete recognizable mana sources.

    This deliberately abstains for nonbasic lands and conditional sources whose
    production cannot be proved by the narrow source shapes below.
    """

    def basic_color(card):
        types = str(card.get("type_line") or "").lower()
        if "basic land" not in types:
            return None
        matches = [
            color
            for kind, color in _BASIC_COLORS.items()
            if re.search(r"\b" + kind + r"\b", types)
        ]
        return matches[0] if len(matches) == 1 else None

    outgoing_color, incoming_color = basic_color(old), basic_color(new)
    if not outgoing_color or not incoming_color or outgoing_color == incoming_color:
        return None
    colors = set()
    for entry in document.get("entries", []):
        if entry.get("is_commander"):
            raw = entry.get("color_identity") or []
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    return None
            colors.update(raw)
    if incoming_color not in colors:
        return None
    sources = {color: 0 for color in "WUBRG"}
    demand = {color: 0 for color in "WUBRG"}
    for entry in document.get("entries", []):
        quantity = int(entry.get("quantity", 1))
        types = str(entry.get("type_line") or "").lower()
        cost = str(entry.get("mana_cost") or "").upper()
        if "land" not in types:
            for color in demand:
                demand[color] += quantity * len(re.findall(r"\{" + color + r"\}", cost))
        if entry.get("is_commander"):
            # Opening mana access excludes the commander, which must first be cast.
            continue
        basic = basic_color(entry)
        if basic:
            sources[basic] += quantity
            continue
        text = (
            re.sub(r"\([^)]*\)", "", str(entry.get("oracle_text") or ""))
            .lower()
            .strip()
        )
        if "land" in types or not text:
            return None
        if "add " not in text and not re.search(
            r"treasure|mana of|mana in|search your library[^.]*land", text
        ):
            continue
        if re.fullmatch(r"\{t\}: add (?:\{[cwubrg]\})+\.", text):
            for color in set(re.findall(r"\{([wubrg])\}", text)):
                sources[color.upper()] += quantity
        elif re.fullmatch(
            r"\{t\}: add one mana of any color(?: in your commander[’']s color identity)?\.",
            text,
        ):
            for color in colors:
                sources[color] += quantity
        else:
            return None
    if (
        not demand[incoming_color]
        or sources[incoming_color] != 0
        or sources[outgoing_color] - 1 < 2
    ):
        return None
    return {
        "kind": "mana_color_tradeoff",
        "benefit": "Add a first recognized source for an explicitly demanded commander-legal colored pip",
        "requirements": [
            "Zero-source proof limited to fully recognized basic lands and unconditional explicit mana abilities",
            "No nonbasic or unresolved conditional mana sources present",
        ],
        "tradeoffs": [
            "Removes one source of the outgoing color",
            "One replacement does not establish an optimal mana base or overall deck improvement",
        ],
        "color_demand_evidence": {
            "incoming_color": incoming_color,
            "outgoing_color": outgoing_color,
            "colored_pip_demand": demand,
            "recognized_source_counts_before": sources,
            "outgoing_sources_after": sources[outgoing_color] - 1,
            "unresolved_source_count": 0,
            "proof_scope": "Library sources for opening mana access; excludes commander abilities that require prior commander access",
        },
    }
