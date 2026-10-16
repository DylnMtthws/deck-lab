"""Behavioral regressions for generator-derived assistant strategy machinery."""

from copy import deepcopy

import pytest

from sabermetrics.assistant_deck_intelligence import assess_deck_requirements
from sabermetrics.assistant_strategy import (
    StrategyContext,
    assess_strategy_transition,
    draw_routes,
    strategy_capabilities,
)


def card(name, kind, text="", **extra):
    return {
        "id": name,
        "card_id": name,
        "oracle_id": name,
        "name": name,
        "type_line": kind,
        "oracle_text": text,
        "quantity": 1,
        "cmc": 2,
        "mana_cost": "{2}",
        "color_identity": [],
        **extra,
    }


def context(document, constraints=None):
    return StrategyContext.model_validate(
        assess_deck_requirements(document, constraints)["strategy_context"]
    )


def test_typal_contract_counts_bodies_not_token_subtypes():
    commander = card(
        "Leader",
        "Legendary Creature — Human",
        "Other Dragons you control get +1/+1.",
        is_commander=True,
    )
    dragon = card("Dragon", "Creature — Dragon", "Flying", quantity=3)
    maker = card("Maker", "Artifact", "{T}: Create a 5/5 red Dragon creature token.")
    report = context({"entries": [commander, dragon, maker]})
    assert report.contracts[0].capability == "tribe:dragon"
    assert report.contracts[0].support_count == 3
    assert report.contracts[0].source_quote in commander["oracle_text"]
    assert "tribe:dragon" not in strategy_capabilities(maker)


def test_cast_contract_ignores_lands_and_unknown_faces():
    commander = card(
        "Leader",
        "Creature",
        "Whenever you cast a noncreature spell, draw a card.",
        is_commander=True,
    )
    entries = [
        commander,
        card("Land", "Land"),
        card("Rock", "Artifact"),
        card("Body", "Creature"),
        card("Split", "Instant // Sorcery"),
    ]
    report = context({"entries": entries})
    assert report.contracts[0].support_count == 1
    commander["oracle_text"] = (
        "Whenever you cast a noncreature spell, if you control an artifact, draw a card."
    )
    assert not context({"entries": entries}).contracts


@pytest.mark.parametrize(
    "change", ["revision", "quantity", "oracle", "theme", "objective", "zone"]
)
def test_context_fingerprint_expires_on_relevant_changes(change):
    document = {
        "revision": 1,
        "entries": [
            card("Leader", "Creature", is_commander=True),
            card("Draw", "Instant", "Draw a card."),
        ],
    }
    before = context(document)
    updated = deepcopy(document)
    constraints = {}
    if change == "revision":
        updated["revision"] += 1
    elif change == "quantity":
        updated["entries"][1]["quantity"] += 1
    elif change == "oracle":
        updated["entries"][1]["oracle_text"] = "Draw two cards."
    elif change == "zone":
        updated["entries"][1]["zone_name"] = "Maybeboard"
    else:
        constraints[change] = "different intent"
    assert before.fingerprint != context(updated, constraints).fingerprint
    assert before == context(document)


def test_joint_cuts_preserve_total_support_rather_than_individual_pair_tags():
    commander = card(
        "Leader", "Creature", "Other Dragons you control get +1/+1.", is_commander=True
    )
    before = {
        "entries": [
            commander,
            card("A", "Creature — Dragon", quantity=2),
            card("B", "Artifact"),
        ]
    }
    after = {
        "entries": [
            commander,
            card("C", "Artifact"),
            card("D", "Creature — Dragon", quantity=2),
        ]
    }
    assert assess_strategy_transition(context(before), context(after))["allowed"]
    after["entries"][2]["quantity"] = 1
    transition = assess_strategy_transition(context(before), context(after))
    assert not transition["allowed"]
    assert transition["losses"][0]["before"] == 2


def test_joint_cut_cannot_remove_both_tutor_targets_or_named_support():
    tutor = card(
        "Tutor",
        "Artifact",
        "{T}: Search your library for a creature card with mana value 2 or less, reveal it, then shuffle.",
    )
    commander = card(
        "Leader", "Creature", "Creatures named Body get +1/+1.", is_commander=True
    )
    before = {
        "entries": [
            commander,
            tutor,
            card("Body", "Creature — Dragon"),
            card("Other", "Creature — Elf"),
        ]
    }
    after = {
        "entries": [
            commander,
            tutor,
            card("Rock", "Artifact"),
            card("OtherRock", "Artifact"),
        ]
    }
    transition = assess_strategy_transition(context(before), context(after))
    assert not transition["allowed"]
    assert {r["provider"] for r in transition["dependency_losses"]} == {
        "Leader",
        "Tutor",
    }


def test_draw_routes_keep_activation_and_combat_prerequisites():
    immediate = draw_routes(card("Cantrip", "Sorcery", "Draw a card."))[0]
    assert immediate.independent
    assert (
        draw_routes(card("Engine", "Artifact", "{3}, {T}: Draw a card."))[0].kind
        == "activation"
    )
    combat = draw_routes(
        card(
            "Combat",
            "Enchantment",
            "Whenever a creature you control deals combat damage to a player, draw a card.",
        )
    )[0]
    assert not combat.independent
    assert "combat_required" in combat.prerequisites
    reactive = draw_routes(
        card("Reactive", "Instant", "Counter target spell. Draw a card.")
    )[0]
    assert not reactive.independent
    assert "legal_target_spell" in reactive.prerequisites
    assert not draw_routes(
        card("Opponent", "Sorcery", "Target opponent draws two cards.")
    )


def test_unknown_strategy_is_visible_and_does_not_invent_win_conditions():
    report = context(
        {"entries": [card("Leader", "Creature", "Flying", is_commander=True)]}
    )
    assert report.contracts == []
    assert "No supported contract was detected." in report.limitations
    assert "win_conditions" not in report.model_dump()


def test_nonland_mana_contract_preserves_commander_resource_support():
    commander = card(
        "Leader",
        "Creature",
        "Whenever you tap a nonland permanent for mana, add one mana of any type that permanent produced.",
        is_commander=True,
    )
    rock = card("Rock", "Artifact", "{T}: Add {C}{C}.")
    land = card("Land", "Land", "{T}: Add {C}.")
    before = context({"entries": [commander, rock, land]})
    assert before.contracts[0].capability == "nonland_tap_mana"
    assert before.contracts[0].support_count == 1
    after = context(
        {"entries": [commander, card("Draw", "Sorcery", "Draw a card."), land]}
    )
    assert not assess_strategy_transition(before, after)["allowed"]
