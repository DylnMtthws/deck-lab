"""Supplemental catalog objects excluded from ordinary Commander deck slots.

This guard supplements, rather than replaces, catalog legality and deck color
identity validation. Missing type metadata is left to those existing checks.
"""

from __future__ import annotations

import re
from typing import Any

SUPPLEMENTAL_OBJECT_TYPES = frozenset(
    {
        "attraction",
        "contraption",
        "sticker",
        "dungeon",
        "conspiracy",
        "scheme",
        "plane",
        "phenomenon",
        "vanguard",
        "token",
        "emblem",
    }
)


def supplemental_object_reason(card: dict[str, Any]) -> str | None:
    """Return a rejection reason for supplemental objects, else no objection."""
    tokens = set(re.findall(r"[a-z]+", str(card.get("type_line") or "").casefold()))
    supplemental = sorted(tokens & SUPPLEMENTAL_OBJECT_TYPES)
    if supplemental:
        return (
            "Supplemental catalog object is not an ordinary Commander deck card: "
            + ", ".join(supplemental)
            + "."
        )
    return None
