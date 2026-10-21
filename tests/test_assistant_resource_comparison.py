from copy import deepcopy
from fractions import Fraction

import pytest

from sabermetrics.assistant_resource_comparison import (
    printed_draw_resource,
    resource_comparison_aid,
)


def card(cid, text, **kwargs):
    return {
        "id": cid,
        "name": cid,
        "oracle_text": text,
        "type_line": "Creature",
        **kwargs,
    }


def test_exact_ratios_keep_colored_requirements_and_never_rank_quality():
    cards = [
        card("azure", "{3}{U}: Draw a card."),
        card("archaeologist", "{3}{U}{U}: Draw two cards."),
    ]
    original = deepcopy(cards)
    aid = resource_comparison_aid(cards)
    assert [r["card_id"] for r in aid["records"]] == ["azure", "archaeologist"]
    a, b = aid["records"]
    assert Fraction(*a["mana_per_drawn_card"]) == 4
    assert Fraction(*b["mana_per_drawn_card"]) == Fraction(5, 2)
    assert a["mana_symbol_requirements"] == {"U": 1}
    assert b["mana_symbol_requirements"] == {"U": 2}
    assert aid["pairs"] == [{"left": 0, "right": 1, "mana_per_card_order": 1}]
    assert cards == original
    assert "deck superiority" in aid["scope"]


@pytest.mark.parametrize(
    "text",
    [
        "{T}, Pay six {E}: Draw three cards.",
        "{U}, {T}: Draw two cards.",
        "{1}, Sacrifice a creature: Draw a card.",
        "{1}: Draw a card. Activate only as a sorcery.",
        "{1}: Draw a card unless an opponent pays {1}.",
        "{1}: You may draw a card.",
        "LEVEL 1-4\n{1}: Draw a card.",
        "{1}: Draw a card.\n{T}: Add {G}.",
        "Creatures you control have “{1}: Draw a card.”",
        "{X}: Draw a card.",
        "{U/P}: Draw a card.",
    ],
)
def test_unlike_or_unsupported_resources_abstain(text):
    assert printed_draw_resource(card("c", text)) is None


@pytest.mark.parametrize(
    "flags",
    [
        {"oracle_text_truncated": True},
        {"oracle_text_complete": False},
        {"card_faces": [{"name": "back"}]},
        {"layout": "leveler"},
    ],
)
def test_incomplete_or_unsupported_sources_abstain(flags):
    assert printed_draw_resource(card("c", "{1}: Draw a card.", **flags)) is None


def test_source_changes_and_zero_cost_have_explicit_exact_values():
    first = printed_draw_resource(card("c", "{0}: Draw a card."))
    later = printed_draw_resource(card("c", "{2}: Draw a card."))
    assert first["activation_mana"] == 0
    assert first["oracle_text_sha256"] != later["oracle_text_sha256"]


def test_colorless_requirement_is_a_mana_symbol_not_a_color():
    profile = printed_draw_resource(card("c", "{2}{C}: Draw a card."))
    assert profile["mana_symbol_requirements"] == {"C": 1}
    assert profile["activation_mana"] == 3
    assert "colored_requirements" not in profile
