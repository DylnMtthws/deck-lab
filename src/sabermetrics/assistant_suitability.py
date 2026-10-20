"""Deck-conditioned discovery signals with literal sources and explicit unknowns.

These bounded heuristics order research candidates, never certify rules, loops,
battlefield availability or measured improvement. Build the deck index once per
search rather than scanning the deck for every candidate.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from sabermetrics.assistant_engine_graph import card_engine_links
from sabermetrics.assistant_strategy import draw_routes, strategy_capabilities
from sabermetrics.card_economy import card_economy_profile
from sabermetrics.deck_evidence import _library_entries

VERSION = "assistant-suitability.v2"
_NUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_COUNT = r"(a|one|two|three|four|five|[1-5])"


def _source(card: dict[str, Any]) -> dict[str, Any]:
    return {
        key: card.get(key)
        for key in ("card_id", "id", "oracle_id", "name", "type_line", "oracle_text")
    }


def deck_support_index(document: dict[str, Any]) -> dict[str, Any]:
    """Scan current library and commanders, excluding sideboards and maybeboards."""
    library = _library_entries(document)
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    groups: dict[str, list[dict[str, Any]]] = {
        key: []
        for key in ("fodder", "counters", "recursion", "sacrifice", "mana", "engines")
    }
    color_sources = dict.fromkeys("WUBRGC", 0)
    land_quantity = 0
    for card in [*commanders, *library]:
        if card.get("oracle_text_truncated"):
            continue
        caps = strategy_capabilities(card)
        for group, capability in (
            ("fodder", "creature_token_producer"),
            ("counters", "counter_producer"),
            ("recursion", "graveyard_access"),
            ("sacrifice", "sacrifice_outlet"),
            ("mana", "nonland_tap_mana"),
        ):
            relevant = capability in caps
            if group == "counters":
                # A self-only counter source cannot replenish another card.
                relevant = relevant and bool(
                    re.search(
                        r"(?:target|each|on a|on another)[^.\n]*creature",
                        str(card.get("oracle_text") or ""),
                        re.IGNORECASE,
                    )
                )
            if relevant:
                groups[group].append(_source(card))
        if draw_routes(card):
            groups["engines"].append(_source(card))
        # Commander death/return text often says "that card", not "from your
        # graveyard". Retain the printed ingredient without certifying a loop.
        text = str(card.get("oracle_text") or "").lower()
        types = str(card.get("type_line") or "").lower()
        colors = set()
        if "land" in types.split("—")[0]:
            land_quantity += int(card.get("quantity", 1))
            for subtype, color in (
                ("plains", "W"),
                ("island", "U"),
                ("swamp", "B"),
                ("mountain", "R"),
                ("forest", "G"),
            ):
                if re.search(r"\b" + subtype + r"\b", types):
                    colors.add(color)
        for clause in re.findall(r"\badd [^.\n]+", text):
            colors.update(re.findall(r"\{([wubrgc])\}", clause.upper(), re.IGNORECASE))
            if "any color" in clause:
                colors.update("WUBRG")
        colors = {color.upper() for color in colors}
        if colors and not card.get("is_commander"):
            for color in colors:
                color_sources[color] += int(card.get("quantity", 1))
            if "nonland_tap_mana" not in caps:
                groups["mana"].append(_source(card))
        if (
            card.get("is_commander")
            and re.search(r"\bdie(?:s)?\b", text)
            and "return that card to the battlefield" in text
            and not any(c.get("name") == card.get("name") for c in groups["recursion"])
        ):
            groups["recursion"].append(_source(card))
    return {
        "revision": document.get("revision"),
        "library_entries_scanned": len(library),
        "missing_text_cards": sum(
            not c.get("oracle_text")
            and "basic land" not in str(c.get("type_line") or "").lower()
            for c in library
        ),
        "groups": groups,
        "commanders": commanders,
        "entries": [*commanders, *library],
        "mana": {
            "land_quantity": land_quantity,
            "detected_potential_color_sources": color_sources,
        },
    }


def _draw_accounting(card: dict[str, Any]) -> dict[str, Any]:
    """Account only for a complete, fixed, single activated draw ability.

    Hand delta is explicitly distinct from total card advantage: sacrificing a
    permanent spends a resource even when that permanent may later recur.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "kind": "unknown",
        "net_hand_delta_per_activation": None,
        "scope": "Hand cards only; sacrifice, life, mana and setup are separate resources. No recursion refunds assumed.",
    }
    economy = card_economy_profile(card)
    if economy["status"] == "supported_from_hand_clause_accounting":
        return {
            **result,
            "status": economy["status"],
            "kind": "spell_from_hand",
            "spell_branches": economy["branches"],
        }
    text = str(card.get("oracle_text") or "")
    # Multiple modes, conditional/variable draws and multiface cards abstain.
    if (
        card.get("card_faces")
        or card.get("oracle_text_truncated")
        or re.search(
            r"\bif\b|\bmay\b|\bunless\b|\bchoose\b|\brandom\b|\bexile\b|\bhand\b|•",
            text,
            re.IGNORECASE,
        )
    ):
        return result
    lines = [line.strip() for line in text.splitlines() if ":" in line]
    draw_lines = [line for line in lines if re.search(r"\bdraw\b", line, re.IGNORECASE)]
    if len(draw_lines) != 1:
        return result
    line = draw_lines[0]
    cost, effect = line.split(":", 1)
    draw = re.search(r"\b(?:you )?draw " + _COUNT + r" cards?\b", effect, re.IGNORECASE)
    if draw is None or re.search(
        r"target player|each player|opponent|that many|for each|\bX\b",
        line,
        re.IGNORECASE,
    ):
        return result
    draws = int(draw[1]) if draw[1].isdigit() else _NUM[draw[1].lower()]
    discards = re.findall(r"\bdiscard " + _COUNT + r" cards?\b", line, re.IGNORECASE)
    if len(re.findall(r"\bdiscard\b", line, re.IGNORECASE)) != len(discards):
        return result
    if len(re.findall(r"\bdraw\b", line, re.IGNORECASE)) != 1:
        return result
    delta = draws - sum(int(n) if n.isdigit() else _NUM[n.lower()] for n in discards)
    return {
        **result,
        "status": "bounded_fixed_activation",
        "kind": "filtering" if delta <= 0 else "hand_gain_with_costs",
        "net_hand_delta_per_activation": delta,
        "permanent_sacrifice_cost": bool(
            re.search(r"\bsacrifice\b", cost, re.IGNORECASE)
        ),
        "source_quote": line,
    }


def candidate_suitability(
    card: dict[str, Any], index: dict[str, Any], focus: str = ""
) -> dict[str, Any]:
    """Describe costs and relevant current-deck ingredients without invented fits."""
    raw = str(card.get("oracle_text") or "")
    text = raw.lower()
    complete = (
        bool(raw)
        and not card.get("oracle_text_truncated")
        and not card.get("card_faces")
    )
    activations = []
    for line in raw.splitlines():
        if ":" in line:
            cost, effect = line.split(":", 1)
            activations.append(
                {"cost": cost.strip(), "effect_and_conditions": effect.strip()}
            )
    gaps = []
    awkward = []
    if "{t}" in text:
        awkward.append(
            "Tap-symbol cost: this creature needs continuous control or haste; untapping is not assumed."
            if "creature" in str(card.get("type_line") or "").lower().split("—")[0]
            else "Tap cost requires an untapped source; further activations need an untap."
        )
    for pattern, condition in (
        (
            r"\bdiscard\b",
            "Discard consumes hand cards; filtering is not net card advantage.",
        ),
        (
            r"\bsacrifice\b",
            "Sacrifice consumes an eligible permanent; tokens and nontokens may differ.",
        ),
        (r"\bpay [^.\n]*life", "Life payment is an additional resource cost."),
        (r"\banother\b", "Respect the printed 'another' restriction."),
        (
            r"\bnontoken\b",
            "Token fodder does not satisfy the printed nontoken restriction.",
        ),
        (
            r"\bexhaust\b|only once|only during|only as a sorcery",
            "Respect the printed activation limit; a reset or repeatable loop is unverified.",
        ),
        (
            r"remove [^.\n:]*counter",
            "Required counters must exist and be replenished for further activations.",
        ),
    ):
        if re.search(pattern, text):
            awkward.append(condition)
    if not complete:
        gaps.append(
            "Complete single-face Oracle evidence is unavailable; fit remains unverified."
        )
    accounting = (
        _draw_accounting(card) if complete else {"status": "unknown", "kind": "unknown"}
    )
    links = (
        card_engine_links({"entries": index["commanders"]}, card) if complete else []
    )
    # Transparent bounded signals. Support never compensates for hard filters;
    # callers apply legality/type/exclusion rules before computing this profile.
    from sabermetrics.assistant_compatibility import ability_compatibility

    compatibility = ability_compatibility(card, index.get("entries", []))
    requirements = compatibility["requirements"]
    # Category detection nominates sources; only ability-matched providers earn
    # credit. Requiring more ingredients must not itself produce a higher score.
    support_score = (
        2 if requirements and all(r["providers"] for r in requirements) else 0
    )
    engine_bonus = min(4, len(links) * 2) if not requirements or support_score else 0
    support = [
        {
            "dimension": r["dimension"],
            "detected_sources": len(r["providers"]) + r["providers_omitted"],
            "cards": r["providers"],
            "sources_omitted": r["providers_omitted"],
            "status": r["status"],
        }
        for r in requirements
    ]
    if any(
        re.search(r"\{(?:[1-9][0-9]*|[WUBRGC])\}", a["cost"], re.IGNORECASE)
        for a in activations
    ):
        # Source counts are not availability; preserve the existing unknown row.
        support.append(
            {
                "dimension": "mana",
                "detected_sources": 0,
                "cards": [],
                "sources_omitted": 0,
            }
        )
    gaps.extend(
        "Compatible "
        + r["dimension"]
        + " support is unconfirmed for "
        + r["required"]
        + "."
        for r in requirements
        if not r["providers"]
    )
    friction = min(
        4,
        sum(not r["providers"] for r in requirements)
        + int(any("{t}" in a["cost"].lower() for a in activations)),
    )
    filtering_penalty = (
        3
        if accounting["kind"] == "filtering"
        and re.search(r"draw|advantage", focus, re.IGNORECASE)
        else 0
    )
    # A card does not become preferable simply by needing more ingredients.
    # A token-producing ETB is not an unlimited supply of expendable permanents.
    consumption_penalty = 3 if accounting.get("permanent_sacrifice_cost") else 0
    value = card.get("mana_value", card.get("cmc"))
    cheap_requested = bool(
        re.search(r"cheap|cheaper|low.cost|low.mana|efficient", focus, re.IGNORECASE)
    )
    casting_penalty = (
        min(6, int(value))
        if cheap_requested
        and isinstance(value, (float, int))
        and math.isfinite(value)
        and value >= 0
        else 0
    )
    return {
        "version": VERSION,
        "deck_revision": index["revision"],
        "printed_facts": {
            "casting_cost": card.get("mana_cost"),
            "mana_value": value,
            "activations": activations,
            "oracle_complete": complete,
            "oracle_text": raw,
        },
        "mana_context": {
            **index["mana"],
            "printed_cast_colors": sorted(
                set(re.findall(r"[WUBRG]", str(card.get("mana_cost") or "")))
            ),
            "status": "potential_sources_only",
            "scope": "Printed source detection, not untapped availability, casting probability or simulation. Conditional and restricted sources require individual inspection.",
        },
        "resource_accounting": accounting,
        "ability_compatibility": compatibility,
        "strategic_judgment": {
            "status": "heuristic_ingredients" if complete else "unverified",
            "support": support,
            "setup_gaps": gaps,
            "awkwardness": awkward,
            "signals": {
                "support": support_score,
                "setup_friction": friction,
                "engine_fit": engine_bonus,
                "filtering_penalty": filtering_penalty,
                "casting_penalty": casting_penalty,
                "permanent_consumption_penalty": consumption_penalty,
            },
            "engine_relationships": [link.model_dump() for link in links],
            "priority": support_score
            + engine_bonus
            - friction
            - filtering_penalty
            - casting_penalty
            - consumption_penalty,
        },
        "rules_interactions": {
            "status": "unverified",
            "note": "Ingredients do not establish survival, timing, counter renewal, a loop, or mana availability.",
        },
        "coverage": {
            key: index[key] for key in ("library_entries_scanned", "missing_text_cards")
        },
    }


def synthesis_fit_context(
    cards: list[dict[str, Any]], max_chars: int = 1800
) -> dict[str, Any]:
    """Preserve named support when older tool receipts leave the prompt.

    Printed text is already stored once in the discovery ledger. This slot adds
    only compact fit signals, never a second Oracle corpus.
    """
    rows: list[dict[str, Any]] = []
    for card in cards[:12]:
        profile = card.get("suitability")
        if not profile:
            continue
        judgment = profile["strategic_judgment"]
        accounting = profile["resource_accounting"]
        row = {
            "card_id": card["id"],
            "support": {
                r["dimension"]: [c["name"] for c in r["cards"][:1]]
                for r in judgment["support"]
                if r["dimension"] != "mana"
            },
            "hand_delta": accounting.get("net_hand_delta_per_activation"),
            "accounting_kind": accounting["kind"],
        }
        if len(json.dumps([*rows, row], ensure_ascii=False)) > max_chars:
            break
        rows.append(row)
    return {
        "cards": rows,
        "omitted": len(cards) - len(rows),
        "scope": "Heuristic ingredients, rules unverified. Full costs/restrictions are in retained Oracle facts. Empty support is incomplete detection, not absence. The renderer appends support and awkwardness to selected notes.",
    }


def render_fit_reason(profile: dict[str, Any]) -> str:
    """Render transparent ingredient support without requiring model invention."""
    judgment = profile["strategic_judgment"]
    support = []
    for row in judgment["support"]:
        if row["cards"] and row["dimension"] != "mana":
            support.append(str(row["cards"][0]["name"]) + " (" + row["dimension"] + ")")
    if not support:
        support = [
            str(row["commander"]) for row in judgment["engine_relationships"][:1]
        ]
    text = (
        "Potential deck support: " + "; ".join(support[:2]) + "."
        if support
        else "Relevant deck support remains unconfirmed."
    )
    # Costs are rendered from supplied text as well as retained for the model.
    abilities = profile.get("ability_compatibility", {}).get("abilities", [])
    for ability in abilities[:2]:
        text += " Activation cost: " + ability["activation_cost"] + "."
    if len(abilities) > 2:
        text += " Additional activation costs are in the full card text."
    if judgment["awkwardness"]:
        text += " " + judgment["awkwardness"][0]
    elif judgment["setup_gaps"]:
        text += " Additional setup support remains unconfirmed."
    else:
        text += " Casting, timing and activation resources still need to be available."
    return text
