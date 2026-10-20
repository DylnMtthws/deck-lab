import copy

import pytest

from sabermetrics.assistant_package_analysis import analyze_package, substitute_profiles
from tests.test_assistant_ability_clauses import card


def test_package_member_support_is_prospective_and_missing_member_is_exposed():
    old = [
        card("Old one", "Flying", id="old-1"),
        card("Old two", "Vigilance", id="old-2"),
    ]
    new = [
        card("Maker", "Create a 1/1 Soldier creature token.", id="maker"),
        card("Draw", "Sacrifice another creature: Draw a card.", id="draw"),
    ]
    document = {"revision": 1, "entries": old}
    original = copy.deepcopy(document)
    result = analyze_package(document, new, old, "draw")
    assert result["engine_execution"] == "unverified"
    assert any(
        e["provider"] == "Maker" and e["recipient"] == "Draw"
        for e in result["shared_ingredient_links"]
    )
    missing = next(
        x for x in result["missing_member_analysis"] if x["missing_member"] == "Maker"
    )
    assert missing["lost_printed_support_links"]
    assert document == original
    assert all(
        p["version"] == "assistant-recommendation-packet.v1"
        for p in result["replacement_packets"]
    )


def test_all_cuts_are_removed_before_joint_support_is_evaluated():
    maker = card("Old maker", "Create a 1/1 Soldier creature token.", id="old-maker")
    filler = card("Old filler", "Flying", id="filler")
    new = [
        card("Draw", "Sacrifice another creature: Draw a card.", id="draw"),
        card("Other", "Vigilance", id="other"),
    ]
    result = analyze_package({"entries": [maker, filler]}, new, [maker, filler])
    draw = next(p for p in result["joint_member_packets"] if p["oracle_id"] == "Draw")
    assert not draw["support"]
    assert draw["unknown_prerequisites"]
    assert result["lost_engine_links"] == []  # no existing recipient in this deck


def test_quantity_and_invalid_cuts():
    maker = card(
        "Maker", "Create a 1/1 Soldier creature token.", id="maker", quantity=2
    )
    draw = card("Draw", "Sacrifice another creature: Draw a card.", id="draw")
    result = analyze_package({"entries": [maker]}, [draw], [maker])
    assert result["joint_member_packets"][0]["support"]
    with pytest.raises(ValueError, match="invalid cut"):
        analyze_package({"entries": [maker]}, [draw], [{**maker, "cut_quantity": 3}])
    with pytest.raises(ValueError, match="actual current"):
        analyze_package({"entries": [maker]}, [draw], [{**maker, "id": "not-present"}])


def test_substitute_dimensions_keep_full_cost_and_restrictions():
    profile = substitute_profiles(
        [
            card(
                "Draw",
                "{2}, Sacrifice another artifact: Draw a card. Activate only as a sorcery.",
                mana_cost="{B}",
            )
        ]
    )[0]
    assert profile["casting_cost"] == "{B}"
    ability = profile["abilities"]["clauses"][0]
    assert ability["costs"] == ["{2}, Sacrifice another artifact"]
    assert ability["timing"]


def test_every_package_evidence_view_uses_all_cuts_regardless_of_pairing():
    maker = card("Removed maker", "Create a 1/1 Soldier creature token.", id="maker")
    filler = card("Filler", "Flying", id="filler")
    engine = card(
        "Draw engine", "Sacrifice another creature: Draw a card.", id="engine"
    )
    extra = card("Extra", "Vigilance", id="extra")
    for cuts in ([filler, maker], [maker, filler]):
        result = analyze_package({"entries": [filler, maker]}, [engine, extra], cuts)
        packet = result["replacement_packets"][0]
        assert not packet["deck_support"]
        assert any(p["status"] == "unknown" for p in packet["prerequisites"])
        assert (
            result["interruption_points"][0]["prerequisites"] == packet["prerequisites"]
        )
        assert packet["changes"]["support_context"] == "complete_prospective_package"
