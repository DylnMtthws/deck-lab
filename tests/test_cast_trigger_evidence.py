import pytest

from sabermetrics.cast_trigger_evidence import (
    cast_trigger_compatibility,
    document_cast_trigger_evidence,
)

VIVI = {
    "name": "Vivi Ornitier",
    "oracle_text": "Whenever you cast a noncreature spell, put a +1/+1 counter on Vivi Ornitier and it deals 1 damage to each opponent.",
    "is_commander": True,
}


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("Artifact", True),
        ("Enchantment", True),
        ("Planeswalker", True),
        ("Battle — Siege", True),
        ("Instant", True),
        ("Sorcery", True),
        ("Artifact Creature — Construct", False),
        ("Creature — Wizard", False),
        ("Land", False),
        ("Artifact Land", False),
        ("", None),
        ("Instant // Land", None),
        ("Unknown", None),
    ],
)
def test_vivi_printed_types(kind, expected):
    result = cast_trigger_compatibility(VIVI, {"type_line": kind})
    assert result["matches_trigger"] is expected
    assert result["source_excerpts"]["card_type_line"] == kind


def test_cornucopia_zero_still_matches_noncreature_trigger():
    card = {
        "name": "Astral Cornucopia",
        "type_line": "Artifact",
        "mana_cost": "{X}{X}{X}",
        "chosen_x": 0,
        "native_support": "missing",
    }
    result = cast_trigger_compatibility(VIVI, card)
    assert result["status"] == "compatible"
    assert result["trigger_category"] == "noncreature"
    assert (
        "Whenever you cast a noncreature spell,"
        == result["source_excerpts"]["commander_oracle"]
    )
    assert result["assumptions"]


def test_noncreature_is_distinct_from_instant_sorcery():
    commander = {
        "oracle_text": "Whenever you cast an instant or sorcery spell, draw a card."
    }
    assert (
        cast_trigger_compatibility(commander, {"type_line": "Artifact"})[
            "matches_trigger"
        ]
        is False
    )
    assert (
        cast_trigger_compatibility(commander, {"type_line": "Instant"})[
            "matches_trigger"
        ]
        is True
    )


def test_faces_and_conditional_or_missing_triggers_are_unknown():
    assert (
        cast_trigger_compatibility(
            VIVI,
            {
                "type_line": "Artifact",
                "card_faces": [{"type_line": "Artifact"}, {"type_line": "Creature"}],
            },
        )["status"]
        == "unknown"
    )
    for text in (
        "",
        "Whenever you cast your second noncreature spell each turn, draw a card.",
        "Whenever you cast a noncreature spell from exile, draw a card.",
    ):
        assert (
            cast_trigger_compatibility(
                {"oracle_text": text}, {"type_line": "Artifact"}
            )["status"]
            == "unknown"
        )


def test_document_partner_commanders_get_separate_evidence():
    document = {
        "entries": [VIVI, {"name": "Partner", "is_commander": True}, {"name": "Other"}]
    }
    results = document_cast_trigger_evidence(document, {"type_line": "Artifact"})
    assert [r["commander"] for r in results] == ["Vivi Ornitier", "Partner"]
    assert [r["status"] for r in results] == ["compatible", "unknown"]


def test_granted_trigger_and_supplemental_card_are_unknown():
    granted = {
        "oracle_text": 'Creatures you control have "Whenever you cast a noncreature spell, draw a card."'
    }
    assert (
        cast_trigger_compatibility(granted, {"type_line": "Artifact"})["status"]
        == "unknown"
    )
    assert (
        cast_trigger_compatibility(VIVI, {"type_line": "Artifact — Attraction"})[
            "status"
        ]
        == "unknown"
    )


def test_card_details_attaches_current_document_commander_evidence(monkeypatch):
    from sabermetrics import assistant_card_facts
    from sabermetrics.assistant_tools import AssistantTools

    monkeypatch.setattr(
        assistant_card_facts,
        "card_facts",
        lambda *args: {"card": {"name": "Astral Cornucopia", "type_line": "Artifact"}},
    )
    tools = AssistantTools.__new__(AssistantTools)
    tools.path = "unused"
    tools.check = lambda: {"entries": [VIVI]}
    facts = tools.execute(
        {"name": "card_details", "arguments": {"card_id": "cornucopia"}}
    )
    assert facts["commander_cast_interactions"][0]["matches_trigger"] is True


def test_intervening_if_and_noncard_types_are_unknown():
    commander = {
        "oracle_text": "Whenever you cast a noncreature spell, if you control a creature, draw a card."
    }
    assert (
        cast_trigger_compatibility(commander, {"type_line": "Artifact"})["status"]
        == "unknown"
    )
    for kind in ("Token Artifact — Treasure", "Emblem"):
        assert (
            cast_trigger_compatibility(VIVI, {"type_line": kind})["status"] == "unknown"
        )
