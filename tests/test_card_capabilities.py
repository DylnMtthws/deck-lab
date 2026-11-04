from sabermetrics.assistant_deck_intelligence import _features, assess_deck_requirements
from sabermetrics.card_capabilities import casting_cost_basis, relationships


def card(text, kind="Creature", cmc=3):
    return {"oracle_text": text, "type_line": kind, "cmc": cmc}


def test_conditional_mana_not_baseline():
    assert "ramp" not in _features(
        card("Whenever a creature dies, create a Treasure token.")
    )
    assert "ramp" not in _features(
        card(
            "{T}: Add X mana, where X is equal to this creature’s power. Activate only during your turn."
        )
    )


def test_combat_draw_does_not_satisfy_noncombat_engine():
    assert "opponent_damage_draw" not in _features(
        card(
            "Whenever enchanted creature deals combat damage to an opponent, draw a card."
        )
    )


def test_unsupported_evidence_is_unknown():
    assert relationships(card("Choose a number secretly."))[0]["status"] == "unknown"


def test_sauron_shape_not_name():
    report = assess_deck_requirements(
        {
            "entries": [
                {
                    **card(
                        "Whenever an opponent casts a spell, amass Orcs 1. Whenever an Army you control deals combat damage to a player, the Ring tempts you. Whenever the Ring tempts you, you may discard your hand. If you do, draw four cards."
                    ),
                    "is_commander": True,
                }
            ]
        }
    )
    assert report["archetype"] == "army_ring_discard"
    assert report["relationship_status"] == "inferred"
    relations = report["commander_relationships"][0]["relationships"]
    assert any(r["resource"] == "army" and r["status"] == "inferred" for r in relations)


def test_unseen_counter_commander_supported():
    report = assess_deck_requirements(
        {
            "entries": [
                {
                    **card("At the beginning of your upkeep, proliferate."),
                    "is_commander": True,
                }
            ]
        }
    )
    assert report["archetype"] == "counters"


def test_delve_cost_is_explicitly_unknown():
    assert casting_cost_basis(card("Delve", "Sorcery", 8)) == (
        3.0,
        "unknown_effective_cost",
    )


def test_vivi_conditions_and_engine_ingredients():
    commander = {
        **card(
            "Whenever you cast a noncreature spell, put a +1/+1 counter on Vivi and it deals 1 damage to each opponent. {0}: Add X mana, where X is Vivi’s power. Activate only during your turn."
        ),
        "is_commander": True,
    }
    report = assess_deck_requirements({"entries": [commander]}, {"theme": "Curiosity"})
    assert report["archetype"] == "cast_damage_draw"
    assert all(link["status"] == "inferred" for link in report["engine_links"])
    assert "ramp" not in _features(commander)


def test_quoted_self_ability_not_board_protection():
    assert "protection" not in _features(card("Ward {2}\nHexproof\nIndestructible"))


def test_graveyard_direction_predicate():
    assert "graveyard_land_access" not in _features(
        card("Exile target land card from an opponent’s graveyard.")
    )
    assert "graveyard_land_access" in _features(
        card("You may play lands from your graveyard.")
    )


def test_ring_trigger_is_consumer_not_producer():
    c = card(
        "Whenever the Ring tempts you, you may discard your hand. If you do, draw four cards."
    )
    assert "ring_tempt" not in _features(c)
    assert not any(
        r["kind"] == "produces" and r["resource"] == "ring_tempt"
        for r in relationships(c)
    )


def test_opponent_army_is_conditional_for_goldfish():
    army = next(
        r
        for r in relationships(
            card("Whenever an opponent casts a spell, amass Orcs 1.")
        )
        if r["resource"] == "army"
    )
    assert "opponent_cast_required" in army["conditions"]
    assert army["status"] == "inferred"
    assert army["evidence_status"] == "oracle_text_match"


def test_trigger_draw_retains_noncreature_prerequisite():
    draw = next(
        r
        for r in relationships(
            card("Whenever you cast a noncreature spell, draw a card.")
        )
        if r["resource"] == "cards"
    )
    assert "noncreature_cast_required" in draw["conditions"]


def test_missing_mana_value_is_unknown_and_zero_is_known():
    assert casting_cost_basis({"oracle_text": ""}) == (3.0, "unknown_effective_cost")
    assert casting_cost_basis({"cmc": None}) == (3.0, "unknown_effective_cost")
    assert casting_cost_basis({"cmc": 0}) == (0.0, "printed_mana_value")
    assert casting_cost_basis({"cmc": None, "mana_value": 2}) == (
        2.0,
        "printed_mana_value",
    )


def test_actual_vivi_power_wording():
    c = card(
        "{0}: Add X mana in any combination of {U} and/or {R}, where X is Vivi Ornitier’s power. Activate only during your turn and only once each turn."
    )
    assert any(
        r["resource"] == "power" and "power_required" in r["conditions"]
        for r in relationships(c)
    )
    assert "ramp" not in _features(c)


def test_ring_producer_has_no_ring_trigger():
    rs = relationships(card("The Ring tempts you."))
    assert any(r["kind"] == "produces" and r["resource"] == "ring_tempt" for r in rs)
    assert not any(r["kind"] == "triggers" for r in rs)
    army = relationships(
        card(
            "Whenever an Army you control deals combat damage to a player, the Ring tempts you."
        )
    )
    producer = next(r for r in army if r["kind"] == "produces")
    assert "combat_required" in producer["conditions"]


def test_opponents_draw_not_own_draw():
    for text in (
        "Target opponent draws a card.",
        "Each opponent draws two cards.",
        "An opponent draws a card.",
    ):
        assert "draw" not in _features(card(text, "Sorcery"))
        assert not any(
            r["resource"] == "cards" and r["kind"] == "produces"
            for r in relationships(card(text))
        )
    assert "draw" in _features(
        card("Each opponent draws a card. You draw two cards.", "Sorcery")
    )


def test_invalid_mana_value_remains_unknown():
    for value in (True, float("nan"), float("inf"), -1, "bad"):
        assert casting_cost_basis({"cmc": value}) == (3.0, "unknown_effective_cost")
    assert casting_cost_basis({"cmc": None, "mana_value": 4}) == (
        4.0,
        "printed_mana_value",
    )


def test_canonical_vivi_and_sauron_prerequisites():
    # Oracle excerpts from the supplied 2026-10-14 canonical snapshot.
    vivi = card(
        "{0}: Add X mana in any combination of {U} and/or {R}, where X is Vivi Ornitier's power. Activate only during your turn and only once each turn.\nWhenever you cast a noncreature spell, put a +1/+1 counter on Vivi Ornitier and it deals 1 damage to each opponent."
    )
    assert any(r["resource"] == "power" for r in relationships(vivi))
    assert "ramp" not in _features(vivi)
    sauron = card(
        "Ward—Sacrifice a legendary artifact or legendary creature.\nWhenever an opponent casts a spell, amass Orcs 1.\nWhenever an Army you control deals combat damage to a player, the Ring tempts you.\nWhenever the Ring tempts you, you may discard your hand. If you do, draw four cards."
    )
    rs = relationships(sauron)
    assert (
        "opponent_cast_required"
        in next(r for r in rs if r["resource"] == "army")["conditions"]
    )
    assert (
        "combat_required"
        in next(
            r for r in rs if r["resource"] == "ring_tempt" and r["kind"] == "produces"
        )["conditions"]
    )
    draw = next(r for r in rs if r["resource"] == "cards")
    assert {"ring_tempt_required", "optional_action_required"} <= set(
        draw["conditions"]
    )
    assert "protection" not in _features(sauron)


def test_land_recursion_plural_and_destination_scoping():
    for text in (
        "You may play lands from your graveyard.",
        "Return up to three target land cards from your graveyard to your hand.",
        "Return target land card from your graveyard to the battlefield.",
    ):
        assert "land_recursion" in _features(card(text))
    for text in (
        "Return target land card from your graveyard to your library.",
        "Return target land card from an opponent’s graveyard to the battlefield.",
        "Return target creature from your graveyard to your hand. You may play an additional land.",
    ):
        assert "land_recursion" not in _features(card(text))
