"""Grounded interim summaries, without additional inference calls."""

from typing import Any

from sabermetrics.assistant_suitability import render_fit_reason


def fit_preview(cards: list[dict[str, Any]]) -> str:
    """Preview a bounded sample; retrieval order is not a recommendation."""
    lines = []
    for card in cards:
        profile = card.get("suitability")
        if not isinstance(profile, dict) or not card.get("name"):
            continue
        cost = card.get("mana_cost") or "printed mana cost unavailable"
        reason = render_fit_reason(profile)
        if reason:
            lines.append(f"- {card['name']} ({cost}): {reason}")
        if len(lines) == 3:
            break
    return "\n".join(lines)
