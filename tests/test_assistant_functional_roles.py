"""Function recall admits actual printed roles before lexical/semantic ranking."""

import pytest

from sabermetrics.assistant_functional_search import (
    FunctionalSearchArgs,
    functional_profile,
    functional_search,
)
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_deck_intelligence import prepared as _base_prepared

prepared = _base_prepared


@pytest.mark.parametrize(
    "role,text,mechanism",
    [
        ("sacrifice", "Sacrifice a creature: Scry 1.", "activated_sacrifice_cost"),
        (
            "sacrifice",
            "{1}, {T}, Sacrifice another creature: You gain 1 life.",
            "activated_sacrifice_cost",
        ),
        (
            "counters",
            "Put a +1/+1 counter on target creature.",
            "plus_one_counter_placement",
        ),
        (
            "counters",
            "This creature enters with three +1/+1 counters on it.",
            "plus_one_counter_placement",
        ),
        (
            "counters",
            "Whenever another creature enters, put a +1/+1 counter on this creature.",
            "plus_one_counter_placement",
        ),
        (
            "death_payoff",
            "Whenever another creature you control dies, each opponent loses 1 life.",
            "creature_death_trigger",
        ),
        (
            "death_payoff",
            "Whenever one or more creatures you control die, draw a card.",
            "creature_death_trigger",
        ),
        (
            "death_payoff",
            "When Test Card dies, create a Treasure token.",
            "self_death_value",
        ),
        (
            "protection",
            "Hexproof (This creature can't be the target of spells or abilities your opponents control.)",
            "printed_or_granted_protection",
        ),
        ("protection", "Flying, vigilance, hexproof", "printed_or_granted_protection"),
        (
            "protection",
            "Creatures you control have indestructible.",
            "printed_or_granted_protection",
        ),
        (
            "protection",
            "Target creature gains shroud until end of turn.",
            "printed_or_granted_protection",
        ),
        ("protection", "Target creature phases out.", "phase_out_effect"),
        (
            "protection",
            "Counter target noncreature spell unless its controller pays {2}.",
            "targeted_spell_counter",
        ),
    ],
)
def test_profile_admits_actual_role_and_preserves_exact_source(role, text, mechanism):
    profile = functional_profile(
        {"name": "Test Card", "type_line": "Creature", "oracle_text": text}
    )
    routes = profile["function_routes"][role]
    assert any(route["mechanism"] == mechanism for route in routes)
    assert all(route["source_quote"] in text for route in routes)
    assert profile["source_oracle_text"] == text


@pytest.mark.parametrize(
    "role,text",
    [
        ("sacrifice", "Whenever you sacrifice a creature, draw a card."),
        (
            "sacrifice",
            "As an additional cost to cast this spell, sacrifice a creature.",
        ),
        ("sacrifice", "{T}: Target opponent sacrifices a creature."),
        ("counters", "Whenever you put a +1/+1 counter on a creature, draw a card."),
        ("counters", "Remove a +1/+1 counter from this creature: Draw a card."),
        ("counters", "Creatures you control with +1/+1 counters on them have flying."),
        ("counters", "Put a -1/-1 counter on target creature."),
        (
            "counters",
            "Put a charge counter on this artifact for each +1/+1 counter on target creature.",
        ),
        (
            "counters",
            "This artifact enters with charge counters equal to the number of +1/+1 counters on creatures you control.",
        ),
        ("counters", "Players can't put +1/+1 counters on creatures."),
        ("death_payoff", "Sacrifice a creature: Draw a card."),
        ("death_payoff", "Whenever you sacrifice a creature, draw a card."),
        ("death_payoff", "Whenever an artifact enters, draw a card."),
        ("protection", "Creatures your opponents control lose hexproof."),
        ("protection", "Creatures can't gain indestructible."),
        ("protection", "Players can't counter target spell."),
        ("protection", "Destroy target creature without hexproof."),
        ("protection", "Whenever a creature phases out, draw a card."),
        ("protection", "Whenever you counter a spell, draw a card."),
    ],
)
def test_incidental_mentions_cost_consumption_and_trigger_antecedents_do_not_match(
    role, text
):
    assert functional_profile({"oracle_text": text})["function_routes"][role] == []


def test_activation_cost_and_conditional_protection_remain_visible():
    text = "{1}, {T}, Sacrifice another creature: Draw a card."
    route = functional_profile({"oracle_text": text})["function_routes"]["sacrifice"][0]
    assert route["activation_cost"] == "{1}, {T}, Sacrifice another creature"
    assert route["conditional"]
    conditional = functional_profile(
        {"oracle_text": "Counter target spell unless its controller pays {2}."}
    )["function_routes"]["protection"][0]
    assert conditional["conditional"]
    assert "unless its controller pays {2}" in conditional["source_quote"]
    restricted = functional_profile(
        {"oracle_text": "Counter target spell if no mana was spent to cast it."}
    )["function_routes"]["protection"][0]
    assert restricted["conditional"]
    assert "if no mana was spent" in restricted["source_quote"]


@pytest.mark.parametrize(
    "role,text",
    [
        ("sacrifice", "Sacrifice a creature: Scry 1."),
        ("counters", "Put a +1/+1 counter on target creature."),
        ("death_payoff", "Whenever a creature dies, each opponent loses 1 life."),
        ("protection", "Creatures you control have hexproof."),
    ],
)
def test_catalog_recall_is_gated_by_real_role_even_with_unrelated_query(
    prepared, role, text
):
    path, document = prepared
    for index in range(24):
        insert(
            path,
            f"role-decoy-{index}",
            f"AAA role decoy {index}",
            "Creature",
            "Whenever another creature enters, draw a card.",
        )
    insert(path, "real-role", "ZZZ True Role", "Creature", text)
    result = functional_search(
        path,
        document,
        FunctionalSearchArgs(function=role, query="draw cards", limit=20),
    )
    assert "real-role" in {card["id"] for card in result["cards"]}
    assert not any(card["id"].startswith("role-decoy-") for card in result["cards"])
    assert all(
        card["functional_profile"]["function_routes"][role] for card in result["cards"]
    )


@pytest.mark.parametrize(
    "function,text",
    [
        ("draw", "Draw a card."),
        ("selection", "Scry 2."),
        ("mana", "{T}: Add {B}."),
        ("interaction", "Destroy target creature."),
        ("recursion", "Return target creature card from your graveyard to your hand."),
        ("sacrifice", "Sacrifice a creature: Scry 1."),
        ("counters", "This creature enters with two +1/+1 counters on it."),
        ("death_payoff", "Whenever a creature dies, each opponent loses 1 life."),
        (
            "death_payoff",
            "Whenever a creature is put into your graveyard from the battlefield, you gain 1 life.",
        ),
        ("protection", "Phasing"),
        ("protection", "Counter target spell unless its controller pays {2}."),
    ],
)
def test_function_search_does_not_build_expensive_profiles_for_irrelevant_catalog(
    prepared,
    monkeypatch,
    function,
    text,
):
    path, document = prepared
    for index in range(80):
        insert(
            path,
            f"irrelevant-{index}",
            f"Unrelated Vanilla {index}",
            "Creature",
            "Vigilance",
        )
    insert(path, "relevant-perf", "Actual Requested Function", "Creature", text)
    import sabermetrics.assistant_functional_search as search_module

    original_profile = search_module.functional_profile
    profiled = []

    def counted_profile(card):
        profiled.append(card["id"])
        return original_profile(card)

    monkeypatch.setattr(search_module, "functional_profile", counted_profile)
    result = functional_search(
        path, document, FunctionalSearchArgs(function=function, limit=20)
    )
    assert "relevant-perf" in {card["id"] for card in result["cards"]}
    # Profiling should scale with plausible matches, rather than the catalog's
    # size. The fixture's existing relevant cards may also require profiles.
    assert len(profiled) < 10
    assert not any(card_id.startswith("irrelevant-") for card_id in profiled)


@pytest.mark.parametrize(
    "name,text",
    [
        ("Viscera Seer", "Sacrifice a creature: Scry 1."),
        (
            "Carrion Feeder",
            "Sacrifice a creature: Put a +1/+1 counter on Carrion Feeder.",
        ),
        ("Ashnod's Altar", "Sacrifice a creature: Add {C}{C}."),
        (
            "Restricted Outlet",
            "{1}, {T}, Sacrifice another nontoken creature: Draw a card.",
        ),
    ],
)
def test_outlet_cost_can_consume_other_permanents(name, text):
    route = functional_profile({"name": name, "oracle_text": text})["function_routes"][
        "sacrifice"
    ][0]
    assert route["activation_cost"] == text.split(":", 1)[0]


@pytest.mark.parametrize(
    "name,text",
    [
        (
            "Evolving Wilds",
            "{T}, Sacrifice Evolving Wilds: Search your library for a basic land card.",
        ),
        (
            "Polluted Delta",
            "{T}, Pay 1 life, Sacrifice Polluted Delta: Search your library for an Island or Swamp card.",
        ),
        ("Mind Stone", "{1}, {T}, Sacrifice Mind Stone: Draw a card."),
        ("Self Only", "Sacrifice this creature: Draw a card."),
    ],
)
def test_self_only_sacrifice_is_not_an_outlet_role(name, text):
    assert (
        functional_profile({"name": name, "oracle_text": text})["function_routes"][
            "sacrifice"
        ]
        == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "Indestructible\nWhenever another creature dies, draw a card.",
        "Flying, hexproof\n{T}: Add {G}.",
    ],
)
def test_periodless_keyword_line_before_another_ability_is_not_dropped(text):
    route = functional_profile({"oracle_text": text})["function_routes"]["protection"][
        0
    ]
    assert route["source_quote"] == text.splitlines()[0]


@pytest.mark.parametrize(
    "role,text",
    [
        (
            "sacrifice",
            "Sacrifice another creature: Draw a card. Activate only once each turn.",
        ),
        (
            "death_payoff",
            "Whenever another creature dies, draw a card. This ability triggers only once each turn.",
        ),
    ],
)
def test_immediate_activation_and_trigger_restrictions_stay_in_exact_source(role, text):
    route = functional_profile({"oracle_text": text})["function_routes"][role][0]
    assert route["source_quote"] == text
    assert route["conditional"]
    assert len(route["restrictions"]) == 1


def test_self_death_value_is_distinct_from_other_creature_payoff():
    routes = functional_profile(
        {
            "name": "Example",
            "oracle_text": "When this creature dies, draw a card.\nWhenever another creature dies, you gain 1 life.",
        }
    )["function_routes"]["death_payoff"]
    assert [route["mechanism"] for route in routes] == [
        "self_death_value",
        "creature_death_trigger",
    ]


def test_cheap_sacrifice_portfolio_beats_expensive_draw_outlets_without_excluding_them(
    prepared,
):
    path, document = prepared
    commander = next(card for card in document["entries"] if card.get("is_commander"))
    commander["oracle_text"] = (
        "Whenever a creature dies, return that card to the battlefield."
    )
    document["entries"].append(
        {
            "name": "Existing Outlet",
            "oracle_text": "Sacrifice a creature: Scry 1.",
            "card_id": "existing-outlet",
            "quantity": 1,
        }
    )
    cheap = [
        ("seer", "Viscera Seer", "Sacrifice a creature: Scry 1.", 1),
        (
            "feeder",
            "Carrion Feeder",
            "Sacrifice a creature: Put a +1/+1 counter on Carrion Feeder.",
            1,
        ),
        (
            "altar",
            "Altar of Dementia",
            "Sacrifice a creature: Target player mills cards equal to the sacrificed creature's power.",
            2,
        ),
        (
            "bombardment",
            "Goblin Bombardment",
            "Sacrifice a creature: Goblin Bombardment deals 1 damage to any target.",
            2,
        ),
    ]
    for card_id, name, text, mana in cheap:
        insert(
            path,
            card_id,
            name,
            (
                "Creature"
                if mana == 1
                else "Artifact" if card_id == "altar" else "Enchantment"
            ),
            text,
            cmc=mana,
        )
    for index in range(10):
        insert(
            path,
            f"expensive-draw-{index}",
            f"AAA Expensive Draw Outlet {index}",
            "Creature",
            "{1}, Sacrifice another creature: Draw a card.\nWhenever another creature dies, draw a card.",
            cmc=4,
        )
    requested = functional_search(
        path,
        document,
        FunctionalSearchArgs(
            function="sacrifice",
            query="free cheap low-mana sacrifice outlets",
            draw_event="death",
            optionality="preferred",
            prefer_multifunctional=True,
            limit=4,
        ),
    )
    assert {card["id"] for card in requested["cards"]} == {row[0] for row in cheap}
    broad = functional_search(
        path,
        document,
        FunctionalSearchArgs(
            function="sacrifice", query="free cheap sacrifice outlets", limit=20
        ),
    )
    assert any(card["id"].startswith("expensive-draw-") for card in broad["cards"])
    assert all(
        "engine_ingredients" not in card["recall_channels"] for card in broad["cards"]
    )
    assert all(
        card["functional_profile"]["function_routes"]["sacrifice"][0]["activation_cost"]
        for card in requested["cards"]
    )


def test_draw_optionality_does_not_filter_out_non_draw_sacrifice_outlets(prepared):
    path, document = prepared
    insert(
        path,
        "non-draw-outlet",
        "Viscera Seer",
        "Creature",
        "Sacrifice a creature: Scry 1.",
        cmc=1,
    )
    result = functional_search(
        path,
        document,
        FunctionalSearchArgs(function="sacrifice", optionality="required"),
    )
    assert "non-draw-outlet" in {card["id"] for card in result["cards"]}


def test_any_function_ranks_query_relevance_before_unrelated_recurring_draw(prepared):
    path, document = prepared
    for index in range(20):
        insert(
            path,
            f"draw-decoy-{index}",
            f"AAA Draw Engine {index}",
            "Creature",
            "{T}: Draw a card.",
        )
    insert(
        path, "instant-target", "Relevant Answer", "Instant", "Counter target spell."
    )
    result = functional_search(
        path, document, FunctionalSearchArgs(function="any", query="instant", limit=1)
    )
    assert result["cards"][0]["id"] == "instant-target"
