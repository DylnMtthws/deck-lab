import pytest

from sabermetrics.assistant_deck_intelligence import _features
from sabermetrics.card_economy import card_economy_profile, printed_cast_trigger_damage
from sabermetrics.functional_candidates import oracle_facts, strategic_tradeoff


@pytest.mark.parametrize(
    "text,expected",
    [
        ("You may discard a card. If you do, draw two cards.", [0, -1]),
        ("Draw a card.", [0]),
        ("Draw two cards, then discard two cards.", [-1]),
        (
            "As an additional cost to cast this spell, discard a card.\nDraw two cards.",
            [0],
        ),
        ("Target player draws two cards.\nFlashback {1}{U}", [1]),
    ],
)
def test_explicit_from_hand_branches(text, expected):
    profile = card_economy_profile({"type_line": "Sorcery", "oracle_text": text})
    assert profile["status"] == "supported_from_hand_clause_accounting"
    assert [branch["net_hand_delta"] for branch in profile["branches"]] == expected
    assert all(quote in text for quote in profile["source_quotes"])
    assert all(
        branch["net_hand_delta"]
        == branch["cards_drawn"]
        - branch["cards_discarded"]
        - profile["spell_cards_consumed"]
        for branch in profile["branches"]
    )


def test_reactive_draw_is_not_a_consistency_tradeoff():
    old = {"type_line": "Sorcery", "mana_value": 4, "oracle_text": "Draw two cards."}
    reactive = {
        "type_line": "Instant",
        "cmc": 2,
        "oracle_text": "Counter target spell that targets a creature.\nDraw a card.",
    }
    profile = card_economy_profile(reactive)
    assert "legal_target_spell" in profile["requirements"]
    assert profile["unconditional_card_access"] is False
    assert (
        strategic_tradeoff(old, reactive, _features(old), _features(reactive)) is None
    )
    assert oracle_facts(reactive)["card_economy"] == profile


@pytest.mark.parametrize(
    "text",
    [
        "Draw cards equal to your power.",
        "Whenever you cast a spell, draw a card.",
        "Put two cards from your hand on top of your library. Draw a card.",
    ],
)
def test_complex_accounting_stays_unknown(text):
    profile = card_economy_profile({"type_line": "Sorcery", "oracle_text": text})
    assert profile["status"] == "unknown"
    assert profile["branches"] == []


def test_printed_damage_does_not_assume_power_growth():
    text = "Whenever you cast a noncreature spell, put a +1/+1 counter on this creature and it deals 1 damage to each opponent."
    profile = printed_cast_trigger_damage({"oracle_text": text})
    assert profile["printed_damage_per_opponent"] == 1
    assert profile["source_quote"] == text
    assert (
        printed_cast_trigger_damage(
            {
                "oracle_text": "Whenever you cast a spell, it deals damage equal to its power to each opponent."
            }
        )["status"]
        == "unknown"
    )


@pytest.mark.parametrize(
    "text,requirement",
    [
        (
            "Target creature gets +1/+0 and gains trample until end of turn.\nDraw a card.",
            "legal_target_creature",
        ),
        (
            "Target creature gains haste until end of turn.\nDraw a card.",
            "legal_target_creature",
        ),
        (
            "Attach target Aura you control to target creature you control.\nDraw a card.",
            "legal_target_aura_you_control",
        ),
    ],
)
def test_board_target_cantrip_is_not_unconditional_access(text, requirement):
    incoming = {"type_line": "Instant", "cmc": 1, "oracle_text": text}
    profile = card_economy_profile(incoming)
    assert requirement in profile["requirements"]
    if requirement == "legal_target_aura_you_control":
        assert "legal_target_creature" in profile["requirements"]
    assert profile["unconditional_card_access"] is False
    assert all(item["source_quote"] in text for item in profile["requirement_evidence"])
    old = {"type_line": "Sorcery", "cmc": 4, "oracle_text": "Draw two cards."}
    assert (
        strategic_tradeoff(old, incoming, _features(old), _features(incoming)) is None
    )


@pytest.mark.parametrize(
    "text",
    [
        "Creatures you control gain trample until end of turn.\nDraw a card.",
        "Create a Wicked Role token attached to up to one target creature you control.\nDraw a card.",
    ],
)
def test_empty_board_cantrips_do_not_invent_required_creature(text):
    profile = card_economy_profile({"type_line": "Sorcery", "oracle_text": text})
    assert profile["status"] == "supported_from_hand_clause_accounting"
    assert profile["requirements"] == []
    assert profile["unconditional_card_access"] is True


def test_optional_discard_spell_optional_role_target_is_not_required():
    text = "You may discard a card. If you do, draw two cards.\nCreate a Wicked Role token attached to up to one target creature you control."
    profile = card_economy_profile({"type_line": "Sorcery", "oracle_text": text})
    assert profile["requirements"] == ["card_in_hand_for_optional_discard"]
    assert profile["branches"][0]["net_hand_delta"] == 0


@pytest.mark.parametrize(
    "card,reason",
    [
        (
            {
                "type_line": "Sorcery",
                "oracle_text": "As an additional cost to cast this spell, discard a card.\nDraw three cards, then discard a card.",
            },
            "Multiple discard",
        ),
        (
            {
                "type_line": "Instant // Instant",
                "oracle_text": "Fire deals 2 damage divided as you choose among one or two targets.\nTap target permanent. Draw a card.",
            },
            "Multiface",
        ),
        (
            {
                "type_line": "Instant",
                "card_faces": [{"name": "Face"}],
                "oracle_text": "Draw a card.",
            },
            "Multiface",
        ),
        (
            {
                "type_line": "Sorcery",
                "oracle_text": "Choose one —\n• Target player draws two cards.\n• Destroy target artifact.",
            },
            "Modal",
        ),
        (
            {"type_line": "Instant", "oracle_text": "Buyback {3}\nDraw a card."},
            "Optional cost",
        ),
        (
            {"type_line": "Instant", "oracle_text": "Replicate {U}\nDraw a card."},
            "Optional cost",
        ),
        (
            {"type_line": "Sorcery", "oracle_text": "Kicker {2}\nDraw a card."},
            "Optional cost",
        ),
        (
            {"type_line": "Instant", "oracle_text": "Storm\nDraw a card."},
            "Optional cost",
        ),
        (
            {
                "type_line": "Instant",
                "oracle_text": "Target player draws two cards.\nDraw a card.",
            },
            "Multiple draw",
        ),
        (
            {
                "type_line": "Instant",
                "oracle_text": "Draw two cards.\nDiscard a card unless you control a Wizard.",
            },
            "Conditional",
        ),
    ],
)
def test_adversarial_spell_shapes_abstain_with_reason(card, reason):
    profile = card_economy_profile(card)
    assert profile["status"] == "unknown"
    assert profile["branches"] == []
    assert profile["unconditional_card_access"] is False
    assert any(reason in item for item in profile["unsupported"])
