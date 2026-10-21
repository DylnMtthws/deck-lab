"""Exact arithmetic for a tiny printed draw grammar, never a quality ranking."""

from __future__ import annotations

import hashlib
import re
from fractions import Fraction
from typing import Any

_SYMBOLS = r"(?:\{(?:[0-9]+|[WUBRGC])\})+"
_DRAW = re.compile(
    rf"(?P<cost>{_SYMBOLS}): Draw (?P<count>a|two|three|four|five|[1-9][0-9]?) cards?\."
)
_COUNTS = {"a": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def printed_draw_resource(card: dict[str, Any]) -> dict[str, Any] | None:
    """A complete isolated ordinary-mana draw ability; unfamiliar forms abstain."""
    raw = card.get("oracle_text")
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > 1200
        or card.get("oracle_text_truncated")
        or card.get("oracle_text_complete") is False
        or card.get("card_faces")
        or str(card.get("layout") or "normal") not in {"normal", ""}
        or "//" in str(card.get("type_line") or "")
    ):
        return None
    match = _DRAW.fullmatch(raw)
    if match is None:
        return None
    symbols = re.findall(r"\{([^}]+)\}", match["cost"])
    mana = sum(int(s) if s.isdecimal() else 1 for s in symbols)
    count = _COUNTS.get(match["count"])
    if count is None:
        count = int(match["count"])
    ratio = Fraction(mana, count)
    return {
        "card_id": str(
            card.get("id") or card.get("card_id") or card.get("oracle_id") or ""
        ),
        "printed_activation_cost": match["cost"],
        "activation_mana": mana,
        "cards_drawn": count,
        "colored_requirements": {c: symbols.count(c) for c in "WUBRGC" if c in symbols},
        "mana_per_drawn_card": [ratio.numerator, ratio.denominator],
        "oracle_text_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "source_span": [0, len(raw)],
    }


def resource_comparison_aid(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain identities; compute only explicitly named incomparable dimensions.

    No filtering, scalar suitability score, resource exchange rate or availability
    inference. Ratios assume printed draw resolves without replacement/modifiers.
    Source facts are also retained whole by selection; hashes bind local traces.
    """
    records = []
    for card in cards:
        profile = printed_draw_resource(card)
        if profile is not None and profile["card_id"]:
            records.append(profile)
    pairs = []
    for i, left in enumerate(records):
        for j, right in enumerate(records[i + 1 :], i + 1):
            a = Fraction(*left["mana_per_drawn_card"])
            b = Fraction(*right["mana_per_drawn_card"])
            pairs.append(
                {"left": i, "right": j, "mana_per_card_order": (a > b) - (a < b)}
            )
    return {
        "scope": "Exact printed activation mana per drawn card only. Colored requirements remain distinct. Assumes printed unmodified resolution, never payable resources, repeatability, speed or deck superiority. Mana, energy, taps, life and sacrificed cards have no inferred exchange rate.",
        "records": records,
        "pairs": pairs,
        "unsupported_cards": len(cards) - len(records),
    }
