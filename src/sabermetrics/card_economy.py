"""Narrow Oracle clause accounting; unsupported text stays explicitly unknown."""

from __future__ import annotations

import re
from typing import Any

_NUMBER = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}
_N = r"(a|one|two|three|four|five|six|seven|[1-7])"


def _number(value: str) -> int:
    return int(value) if value.isdigit() else _NUMBER[value]


def _text(card: dict[str, Any]) -> str:
    return re.sub(r"\([^)]*\)", "", str(card.get("oracle_text") or "")).strip()


def card_economy_profile(card: dict[str, Any]) -> dict[str, Any]:
    """Count only explicit own hand draw/discard from a spell cast from hand.

    Card selection, graveyard reuse, token value and commander triggers are
    excluded. This is not a complete rules interpreter or card advantage rating.
    """
    text = _text(card)
    lower = text.lower()
    requirements: list[str] = []
    requirement_evidence: list[dict[str, str]] = []
    for target in re.finditer(r"\btarget\s+(?:(?!\btarget\b)[^.\n;,])+", lower):
        prefix = lower[max(0, target.start() - 30) : target.start()]
        if re.search(r"up to (?:one|two|three|[0-9]+)\s*$", prefix):
            continue
        clause = target.group(0)
        if re.search(r"\bspell\b", clause):
            requirement = "legal_target_spell"
        elif re.search(r"\baura\b", clause):
            requirement = (
                "legal_target_aura_you_control"
                if "you control" in clause
                else "legal_target_aura"
            )
        elif re.search(r"\bcreature\b", clause):
            requirement = "legal_target_creature"
        elif re.match(r"target (?:player|opponent)\b", clause):
            requirement = "legal_target_player"
        else:
            requirement = "legal_target_nonplayer"
        if requirement not in requirements:
            requirements.append(requirement)
        requirement_evidence.append(
            {
                "requirement": requirement,
                "source_quote": text[target.start() : target.end()],
            }
        )
    any_target = re.search(r"\bany target\b", lower)
    if any_target:
        requirements.append("legal_target_any_eligible")
        requirement_evidence.append(
            {
                "requirement": "legal_target_any_eligible",
                "source_quote": text[any_target.start() : any_target.end()],
            }
        )
    base: dict[str, Any] = {
        "status": "unknown",
        "scope": "One spell cast from hand; resolves with legal targets; own draws; no replacement effects; excludes tokens, selection, triggers and graveyard reuse",
        "spell_cards_consumed": 1,
        "requirements": requirements,
        "requirement_evidence": requirement_evidence,
        "unconditional_card_access": False,
        "branches": [],
        "source_quotes": [],
        "unsupported": [],
    }
    if (
        card.get("oracle_text_truncated")
        or len(str(card.get("oracle_text") or "")) > 6000
    ):
        base["unsupported"] = [
            "Oracle text is incomplete or exceeds bounded profile scope"
        ]
        return base
    if not any(
        kind in str(card.get("type_line") or "").lower()
        for kind in ("instant", "sorcery")
    ):
        base["unsupported"] = ["Not an instant or sorcery spell profile"]
        return base
    if "//" in str(card.get("type_line") or "") or card.get("card_faces"):
        base["unsupported"].append(
            "Multiface, split and Adventure card shapes are not supported"
        )
        return base
    if re.search(r"choose (?:one|two|three)|•|\b(?:spree|escalate|entwine)\b", lower):
        base["unsupported"].append(
            "Modal or multiple-mode spell branches are not supported"
        )
        return base
    if re.search(
        r"\b(?:buyback|replicate|kicker|multikicker|storm|cipher|splice|overload)\b",
        lower,
    ):
        base["unsupported"].append(
            "Optional cost, copy, return-to-hand or alternate spell branches are not supported"
        )
        return base
    alternate_cast = bool(re.search(r"\b(?:flashback|escape|retrace|rebound)\b", lower))
    if alternate_cast:
        base["unsupported"].append(
            "Alternate/graveyard/repeated casts are not included in from-hand accounting"
        )
    discard_cost = re.search(
        r"as an additional cost to cast this spell, discard " + _N + r" cards?\.", lower
    )
    if "additional cost" in lower and discard_cost is None:
        base["unsupported"].append("Unsupported additional cost")
        return base
    # Other hand mutations, variable/triggered draw, and costs require a richer
    # model. Do not turn a recognizable fragment into a total hand-value claim.
    if re.search(
        r"\bwhenever\b|at the beginning|random|for each|that many|draw x|draw cards? equal|cards? (?:from|in) your hand|put [^.\n]*cards?[^.\n]*(?:top|bottom)|exile [^.\n]*hand|return [^.\n]*to [^.\n]*hand|put [^.\n]*(?:into|in) your hand|you may cast",
        lower,
    ):
        base["unsupported"].append("Complex or additional hand-changing clause")
        return base
    optional = re.search(
        r"you may discard " + _N + r" cards?\.\s*if you do, draw " + _N + r" cards?\.",
        lower,
    )
    draw = re.search(
        r"(?:^|[.\n]|, then)\s*(?:you )?draw " + _N + r" cards?(?:\.|,)", lower
    )
    player_draw = re.search(r"target player draws " + _N + r" cards?\.", lower)
    if optional:
        # No other draw/discard clauses are accepted alongside this branch.
        remainder = lower[: optional.start()] + lower[optional.end() :]
        if re.search(r"\bdraws?\b|\bdiscards?\b", remainder):
            base["unsupported"].append("Multiple or conditional draw/discard clauses")
            return base
        discard_n, draw_n = _number(optional.group(1)), _number(optional.group(2))
        base["branches"] = [
            {
                "condition": "optional_discard_paid",
                "cards_drawn": draw_n,
                "cards_discarded": discard_n,
                "net_hand_delta": draw_n - discard_n - 1,
            },
            {
                "condition": "optional_discard_declined",
                "cards_drawn": 0,
                "cards_discarded": 0,
                "net_hand_delta": -1,
            },
        ]
        base["requirements"].append("card_in_hand_for_optional_discard")
        base["source_quotes"] = [text[optional.start() : optional.end()]]
    elif draw or player_draw:
        match = draw or player_draw
        assert match is not None
        draws = re.findall(r"\bdraws?\b", lower)
        if len(draws) != 1:
            base["unsupported"].append("Multiple draw clauses are not supported")
            return base
        mandatory = discard_cost or re.search(
            r"(?:then |^|[.\n])(?:you )?discard " + _N + r" cards?\.", lower
        )
        if "discard" in lower and not mandatory:
            base["unsupported"].append("Conditional or unsupported discard clause")
            return base
        if len(re.findall(r"\bdiscards?\b", lower)) != (1 if mandatory else 0):
            base["unsupported"].append(
                "Multiple discard costs or resolution clauses are not supported"
            )
            return base
        draw_n, discard_n = (
            _number(match.group(1)),
            _number(mandatory.group(1)) if mandatory else 0,
        )
        base["branches"] = [
            {
                "condition": "target_yourself" if player_draw else "normal_resolution",
                "cards_drawn": draw_n,
                "cards_discarded": discard_n,
                "net_hand_delta": draw_n - discard_n - 1,
            }
        ]
        base["source_quotes"] = [text[match.start() : match.end()]]
        if mandatory:
            base["source_quotes"].append(text[mandatory.start() : mandatory.end()])
        if discard_cost:
            base["requirements"].append("card_in_hand_for_additional_discard_cost")
        if player_draw:
            base["requirements"].append("choose_yourself_as_legal_player_target")
    else:
        base["unsupported"].append("No supported explicit fixed own-draw clause")
        return base
    base["status"] = "supported_from_hand_clause_accounting"
    base["unconditional_card_access"] = (
        not alternate_cast
        and not requirements
        and optional is None
        and player_draw is None
        and not re.search(r"\bif\b|\bunless\b|\bmay\b", lower)
    )
    return base


def printed_cast_trigger_damage(card: dict[str, Any]) -> dict[str, Any]:
    """Expose a printed simple noncreature-cast damage number, not modified damage."""
    text = _text(card)
    match = (
        None
        if card.get("oracle_text_truncated")
        else re.search(
            r"Whenever you cast a noncreature spell,[^.\n]*? deals? ([1-9][0-9]*) damage to each opponent\.",
            text,
            re.IGNORECASE,
        )
    )
    return {
        "status": "supported_printed_clause" if match else "unknown",
        "printed_damage_per_opponent": int(match.group(1)) if match else None,
        "trigger": "you_cast_noncreature_spell" if match else None,
        "source_quote": match.group(0) if match else None,
        "scope": "Printed clause only; excludes power, counters, replacement effects, copies and all contextual modifiers",
    }
