"""Regression scenarios from the live Vivi, Meren, Lathril and Kinnan review."""

from copy import deepcopy

import pytest

from sabermetrics.assistant_context_rank import rank_card_context, requested_functions
from sabermetrics.assistant_deck_facts import build_deck_facts, summarize_deck_facts
from sabermetrics.assistant_engine_graph import (
    build_engine_graph,
    card_engine_links,
    engine_loss_score,
    summarize_engine_graph,
)
from sabermetrics.assistant_runtime import (
    _encode_context,
    _evidence_fallback,
    _model_receipt,
)
from sabermetrics.assistant_strategy import strategy_capabilities
from tests.test_assistant_strategy import card


def vivi():
    return card(
        "Vivi Ornitier",
        "Legendary Creature — Wizard",
        "{0}: Add X mana in any combination of {U} and/or {R}, where X is Vivi Ornitier's power. Activate only during your turn and only once each turn.\nWhenever you cast a noncreature spell, put a +1/+1 counter on Vivi Ornitier and it deals 1 damage to each opponent.",
        is_commander=True,
    )


@pytest.mark.parametrize(
    "name,text",
    [
        (
            "Supplier",
            "When this creature enters or dies, mill three cards. (Put the top three cards of your library into your graveyard.)",
        ),
        (
            "Wayfinder",
            "When this creature enters, reveal the top four cards of your library. You may put a land card from among them into your hand. Put the rest into your graveyard.",
        ),
        (
            "Salvage",
            "Reveal the top five cards of your library. You may put a creature or land card from among them into your hand. Put the rest into your graveyard.",
        ),
        (
            "Buried",
            "Search your library for up to three creature cards, put them into your graveyard, then shuffle.",
        ),
    ],
)
def test_meren_explicit_stock_survives_trigger_or_choice_wording(name, text):
    c = card(name, "Creature", text)
    document = {"entries": [c]}
    facts = build_deck_facts(document)
    feature = next(f for f in facts.features if f.feature == "graveyard_stock")
    assert feature.detected_quantity == 1
    assert feature.cards[0].source_quote in text
    assert "graveyard_stock" in strategy_capabilities(c)


@pytest.mark.parametrize(
    "text",
    [
        "Target player mills three cards.",
        "Each opponent mills two cards.",
        "Each player mills a card.",
        "Return target card from your graveyard to your hand.",
    ],
)
def test_own_stock_never_inferred_from_ambiguous_or_opponent_mill(text):
    facts = build_deck_facts({"entries": [card("Other", "Sorcery", text)]})
    assert (
        next(
            f for f in facts.features if f.feature == "graveyard_stock"
        ).detected_quantity
        == 0
    )


def test_whole_library_scan_precedes_presentation_limits_and_excludes_sideboard():
    entries = [card(f"Generic {n}", "Artifact", "{T}: Add {C}.") for n in range(80)]
    entries += [
        card(
            "ZZZ supplier",
            "Creature",
            "When this creature enters, mill three cards.",
            quantity=2,
        )
    ]
    entries += [
        card("Sideboard", "Sorcery", "Mill five cards.", zone_name="Maybeboard")
    ]
    facts = build_deck_facts({"revision": 4, "entries": entries}, "graveyard filling")
    assert facts.library_entries_scanned == 81
    assert facts.library_quantity == 82
    stock = next(f for f in facts.features if f.feature == "graveyard_stock")
    assert stock.detected_quantity == 2
    assert stock.cards[0].name == "ZZZ supplier"
    assert facts.matches[0].name == "ZZZ supplier"
    assert "Zero detected does not establish absence" in facts.scope


def test_facts_mark_missing_multiface_and_recompute_on_revision_or_zone():
    document = {
        "entries": [
            card("Unknown", "Creature"),
            card("Faces", "Instant // Land", "Mill three cards."),
        ]
    }
    facts = build_deck_facts(document)
    assert facts.missing_text_cards == 1
    assert facts.unsupported_multiface_cards == 1
    for field, value in [
        ("oracle_text", "Draw a card."),
        ("quantity", 2),
        ("zone_name", "Sideboard"),
    ]:
        changed = deepcopy(document)
        changed["entries"][0][field] = value
        assert build_deck_facts(changed).fingerprint != facts.fingerprint
    assert (
        build_deck_facts({**document, "revision": 1}).fingerprint != facts.fingerprint
    )


def test_vivi_core_multipliers_and_damage_draw_have_sourced_relationships():
    core = [
        card(
            "Harmonic Prodigy",
            "Creature — Human Wizard",
            "If an ability of a Shaman or another Wizard you control triggers, that ability triggers an additional time.",
        ),
        card(
            "Tandem Lookout",
            "Creature — Human Scout",
            'Soulbond\nAs long as Tandem Lookout is paired with another creature, each of those creatures has "Whenever this creature deals damage to an opponent, draw a card."',
        ),
        card(
            "Curiosity",
            "Enchantment — Aura",
            "Enchant creature\nWhenever enchanted creature deals damage to an opponent, you may draw a card.",
        ),
        card("Generic rock", "Artifact", "{T}: Add {C}."),
    ]
    document = {"entries": [vivi(), *core]}
    graph = build_engine_graph(document)
    for c in core[:3]:
        assert engine_loss_score(graph, c) > engine_loss_score(graph, core[-1])
        for link in card_engine_links(document, c):
            assert link.source_quote in (c["oracle_text"], c["type_line"])
            assert link.commander_quote == vivi()["oracle_text"]
            assert link.conditions
    assert "trigger_multiplier" in {l.relationship for l in graph.links}
    assert "commander_damage_to_draw" in {l.relationship for l in graph.links}


def test_multiplier_requires_printed_commander_tribe_not_token_word():
    multiplier = card(
        "Multiplier",
        "Creature",
        "If an ability of a Wizard you control triggers, that ability triggers an additional time.",
    )
    commander = card(
        "Maker",
        "Creature — Human",
        "Whenever you cast an instant, create a 1/1 blue Wizard creature token.",
        is_commander=True,
    )
    assert not card_engine_links({"entries": [commander]}, multiplier)


def test_request_draw_ranks_actual_draw_ahead_of_generic_engine_cast():
    document = {"entries": [vivi()]}
    draw = rank_card_context(
        document,
        card("Draw", "Instant", "Draw two cards."),
        "My draw count is sufficient; compare better draw options",
    )
    rock = rank_card_context(
        document,
        card("Rock", "Artifact", "{T}: Add {C}."),
        "My draw count is sufficient; compare better draw options",
    )
    assert draw.priority > rock.priority
    assert draw.matched_functions == ["draw"]
    assert "cast_trigger_ingredient" in draw.engine_relationships


def test_fresh_facts_and_core_links_survive_context_eviction():
    prodigy = card(
        "Harmonic Prodigy",
        "Creature",
        "If an ability of a Wizard you control triggers, that ability triggers an additional time.",
    )
    document = {
        "entries": [
            vivi(),
            prodigy,
            card(
                "Supplier", "Creature", "When this creature enters, mill three cards."
            ),
        ]
    }
    state = {
        "constraints": {},
        "grounded_deck_facts": summarize_deck_facts(build_deck_facts(document)),
        "engine_graph": summarize_engine_graph(build_engine_graph(document)),
    }
    encoded = _encode_context(
        state,
        "find improvements",
        [{"role": "user", "content": "irrelevant " * 10000}] * 10,
    )
    assert len(encoded) <= 16000
    assert "Harmonic Prodigy" in encoded and "Supplier" in encoded
    assert "Zero detected does not establish absence" in encoded


def test_meren_links_distinguish_creature_outlet_from_artifact_only_cost():
    commander = card(
        "Meren",
        "Creature",
        "Whenever another creature you control dies, you get an experience counter.\nAt the beginning of your end step, choose target creature card in your graveyard.",
        is_commander=True,
    )
    document = {"entries": [commander]}
    creature = card("Seer", "Creature", "Sacrifice a creature: Scry 1.")
    artifact = card("Other", "Creature", "Sacrifice another artifact: Draw a card.")
    stock = card("Supplier", "Creature", "When this creature enters, mill three cards.")
    assert "creature_death_support" in {
        l.relationship for l in card_engine_links(document, creature)
    }
    assert "creature_death_support" not in {
        l.relationship for l in card_engine_links(document, artifact)
    }
    assert "graveyard_setup" in {
        l.relationship for l in card_engine_links(document, stock)
    }


def test_lathril_elf_body_retains_tap_cost_distinction():
    commander = card(
        "Lathril",
        "Creature — Elf Noble",
        "{T}, Tap ten untapped Elves you control: Each opponent loses 10 life and you gain 10 life.",
        is_commander=True,
    )
    elf = card("Elf", "Creature — Elf Druid", "{T}: Add {G}.")
    links = card_engine_links({"entries": [commander]}, elf)
    body = next(l for l in links if l.relationship == "elf_body_for_activation")
    assert any("other Elves may be summoning sick" in c for c in body.conditions)


@pytest.mark.parametrize(
    "text",
    [
        "Defending player mills two cards.",
        "{T}: Its controller mills a card.",
        "Whenever you mill one or more cards, put a +1/+1 counter on this creature.",
    ],
)
def test_stock_does_not_count_other_subject_or_mill_payoff(text):
    facts = build_deck_facts({"entries": [card("Other", "Creature", text)]})
    assert (
        next(
            f for f in facts.features if f.feature == "graveyard_stock"
        ).detected_quantity
        == 0
    )


def test_kess_without_trigger_does_not_protect_prodigy_for_commander():
    kess = card(
        "Kess",
        "Creature — Human Wizard",
        "During each of your turns, you may cast an instant or sorcery spell from your graveyard.",
        is_commander=True,
    )
    prodigy = card(
        "Prodigy",
        "Creature",
        "If an ability of a Shaman or another Wizard you control triggers, that ability triggers an additional time.",
    )
    assert not card_engine_links({"entries": [kess]}, prodigy)


def test_mana_base_request_preserves_land_relevance_and_negation_is_not_intent():
    document = {"entries": [vivi()]}
    land = rank_card_context(
        document, card("Land", "Land", "{T}: Add {U} or {R}."), "fix my mana base"
    )
    rock = rank_card_context(
        document, card("Rock", "Artifact", "{T}: Add {C}."), "fix my mana base"
    )
    assert land.priority >= rock.priority
    assert "draw" not in requested_functions("I don't want more draw, I need removal")
    assert requested_functions("I don't want more draw, I need removal") == {
        "interaction"
    }
    assert "protection" not in requested_functions("cut the protection package")
    assert "draw" in requested_functions(
        "Draw count is sufficient; compare better draw options"
    )


def test_shadowspear_is_not_protection_and_cost_quote_is_the_actual_activation():
    from sabermetrics.assistant_deck_facts import feature_quotes

    quotes = feature_quotes(
        card(
            "Spear",
            "Artifact",
            "{1}: Permanents your opponents control lose hexproof and indestructible until end of turn.",
        )
    )
    assert "protection" not in quotes
    quotes = feature_quotes(
        card(
            "Body",
            "Creature",
            "At the beginning of your upkeep, sacrifice this creature unless you pay {1}.\nSacrifice a creature: Scry 1.",
        )
    )
    assert quotes["sacrifice_outlet"] == "Sacrifice a creature: Scry 1"


def test_realistic_facts_receipt_preserves_every_feature_and_explicit_omissions():
    import json

    from sabermetrics.assistant_deck_facts import deck_facts_view

    entries = [
        card(
            f"Card {n}",
            "Artifact",
            "{T}: Add {C}.\n{2}: Draw a card.\nWhen this artifact enters, mill three cards.\n"
            + "Other text. " * 100,
        )
        for n in range(99)
    ]
    facts = build_deck_facts({"entries": entries}, "draw graveyard")
    assert len(json.dumps(facts.model_dump())) > 12000
    view = deck_facts_view(facts)
    receipt = _model_receipt({"name": "retrieve_deck_facts", "data": view})
    assert len(json.dumps(receipt, ensure_ascii=False)) < 12000
    assert {
        f["feature"]: f["detected_quantity"] for f in receipt["data"]["features"]
    } == {f.feature: f.detected_quantity for f in facts.features}
    assert any(f["omitted_cards"] > 0 for f in receipt["data"]["features"])


def test_rule_scope_uses_whole_card_names_and_no_sideboard():
    from sabermetrics.assistant_deck_facts import scoped_rule_cards

    document = {
        "entries": [
            vivi(),
            card("Opt", "Instant", "Scry 1. Draw a card."),
            card("Maybeboard Card", "Instant", zone_name="Sideboard"),
        ]
    }
    assert [
        c["name"]
        for c in scoped_rule_cards(document, "compare options and Maybeboard Card")
    ] == ["Vivi Ornitier"]
    assert "Opt" in [c["name"] for c in scoped_rule_cards(document, "compare Opt")]


def test_budget_fallback_discloses_actual_action_state():
    assert "No experiment was started" in _evidence_fallback({})
    assert "An isolated experiment was started" in _evidence_fallback({}, 1)
    assert "source deck was not changed" in _evidence_fallback({}, 1)


@pytest.mark.parametrize(
    "focus,required",
    [
        ("what should i cut to add more card draw", {"draw"}),
        ("i don't have enough draw", {"draw"}),
        ("not enough mana", {"nonland_tap_mana", "physical_land"}),
        ("remove a land and add ramp", {"nonland_tap_mana"}),
        ("more draw without losing lands", {"draw"}),
    ],
)
def test_need_statements_are_not_discarded_by_negation(focus, required):
    assert required <= requested_functions(focus)


def test_kinnan_dork_to_identical_text_land_keeps_loss_visible():
    commander = card(
        "Kinnan",
        "Creature",
        "Whenever you tap a nonland permanent for mana, add one mana of any type that permanent produced.",
        is_commander=True,
    )
    dork = card("Dork", "Creature — Elf Druid", "{T}: Add {G}.")
    land = card("Land", "Land", "{T}: Add {G}.")
    graph = build_engine_graph({"entries": [commander, dork]})
    assert engine_loss_score(graph, dork, land) > 0


@pytest.mark.parametrize(
    "focus",
    [
        "without removal and protection",
        "too much ramp and mana rocks",
        "I don't want removal and counterspells",
        "I have no interest in removal",
    ],
)
def test_negation_carries_over_function_lists(focus):
    assert requested_functions(focus) == set()
