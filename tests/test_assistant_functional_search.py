from copy import deepcopy

import pytest

from sabermetrics.assistant_functional_search import (
    FunctionalSearchArgs,
    functional_profile,
    functional_search,
)
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_deck_intelligence import prepared as _base_prepared

prepared = _base_prepared


def test_draw_retrieval_filters_before_ranking_not_alphabetical_limit(prepared):
    path, document = prepared
    for i in range(30):
        insert(
            path,
            f"decoy-{i}",
            f"AAA {i}",
            "Creature",
            "Whenever another creature you control dies, create a Treasure token.",
        )
    insert(
        path,
        "haruspex",
        "Grim Haruspex",
        "Creature",
        "Whenever another nontoken creature you control dies, draw a card.",
        cmc=3,
    )
    insert(
        path,
        "yawgmoth",
        "Yawgmoth, Thran Physician",
        "Creature",
        "Pay 1 life, Sacrifice another creature: Put a -1/-1 counter on up to one target creature and draw a card.",
        cmc=4,
    )
    insert(
        path,
        "optional",
        "Harvester of Souls",
        "Creature",
        "Whenever another nontoken creature dies, you may draw a card.",
        cmc=6,
    )
    before = deepcopy(document)
    result = functional_search(
        path,
        document,
        FunctionalSearchArgs(
            query="repeatable death draw",
            type_line="Creature",
            mana_max=4,
            optionality="preferred",
            limit=2,
        ),
    )
    assert [c["name"] for c in result["cards"]] == [
        "Yawgmoth, Thran Physician",
        "Grim Haruspex",
    ]
    assert result["channel_status"]["semantic"] == "unavailable_no_card_vectors"
    assert document == before


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Pay 1 life, Sacrifice another creature: Draw a card.",
            "voluntary_activation",
        ),
        ("Whenever another creature dies, you may draw a card.", "optional_trigger"),
        ("Whenever another creature dies, draw a card.", "mandatory_trigger"),
    ],
)
def test_draw_control_preserves_activation_vs_resolution(text, expected):
    result = functional_profile({"type_line": "Creature", "oracle_text": text})
    assert result["draw_routes"][0]["draw_control"] == expected
    assert result["source_oracle_text"] == text


def test_color_legality_mana_existing_and_explicit_zero(prepared):
    path, document = prepared
    insert(path, "zero", "Zero Draw", "Artifact", "{T}: Draw a card.", cmc=0)
    insert(path, "known", "Known Draw", "Artifact", "{T}: Draw a card.")
    entry = deepcopy(next(c for c in document["entries"] if not c.get("is_commander")))
    entry.update(oracle_id="known", card_id="known")
    document["entries"].append(entry)
    all_cards = functional_search(path, document, FunctionalSearchArgs())
    ids = {c["id"] for c in all_cards["cards"]}
    assert "zero" in ids
    assert not ids & {"foreign", "illegal", "known"}
    zero = functional_search(path, document, FunctionalSearchArgs(mana_max=0))
    assert [c["id"] for c in zero["cards"]] == ["zero"]
    assert any(
        c["id"] == "known" and c["already_in_library"]
        for c in functional_search(
            path, document, FunctionalSearchArgs(include_existing=True)
        )["cards"]
    )


def test_death_draw_engine_link_has_actual_support_sources(prepared):
    path, document = prepared
    commander = next(c for c in document["entries"] if c.get("is_commander"))
    commander["oracle_text"] = (
        "Whenever a creature you control with a +1/+1 counter on it dies, return that card to the battlefield at the beginning of the next end step."
    )
    outlet = deepcopy(next(c for c in document["entries"] if not c.get("is_commander")))
    outlet.update(name="Viscera Seer", oracle_text="Sacrifice a creature: Scry 1.")
    document["entries"].append(outlet)
    insert(
        path,
        "draw-creature",
        "Death Draw",
        "Creature",
        "Whenever another creature you control dies, draw a card.",
    )
    result = functional_search(
        path, document, FunctionalSearchArgs(type_line="Creature")
    )
    card = next(c for c in result["cards"] if c["id"] == "draw-creature")
    support = card["functional_profile"]["deck_support"]
    assert support["commander_sources"][0]["oracle_text"] == commander["oracle_text"]
    assert support["sacrifice_sources"][0]["name"] == "Viscera Seer"
    assert "engine_ingredients" in card["recall_channels"]


def test_unknown_identity_cannot_broaden_color_pool(prepared):
    path, document = prepared
    next(c for c in document["entries"] if c.get("is_commander"))[
        "color_identity"
    ] = None
    result = functional_search(path, document, FunctionalSearchArgs())
    assert result["cards"] == []
    assert result["status"] == "unknown_commander_identity"


def test_optional_cost_preserves_condition_across_sentences():
    text = "Whenever another creature you control dies, you may pay 2 life. If you do, draw a card."
    result = functional_profile({"type_line": "Creature", "oracle_text": text})
    route = result["draw_routes"][0]
    assert route["draw_control"] == "optional_trigger"
    assert route["source_quote"] + "." == text
    assert "optional_cost_required" in route["prerequisites"]


def test_required_control_excludes_a_second_unavoidable_draw_route(prepared):
    path, document = prepared
    insert(
        path,
        "mixed",
        "Mixed Draw",
        "Creature",
        "{T}: Draw a card.\nWhenever another creature dies, draw a card.",
    )
    result = functional_search(
        path, document, FunctionalSearchArgs(optionality="required")
    )
    assert "mixed" not in {c["id"] for c in result["cards"]}


def test_any_type_means_no_type_filter(prepared):
    path, document = prepared
    result = functional_search(path, document, FunctionalSearchArgs(type_line="any"))
    assert result["cards"]
    assert FunctionalSearchArgs(type_line="Creature").type_line == "creature"


def test_requested_draw_event_beats_irrelevant_secondary_roles(prepared):
    path, document = prepared
    insert(
        path,
        "aura",
        "AAA Death Aura",
        "Enchantment",
        "Whenever enchanted creature dies, draw a card.\nWhenever enchanted creature attacks, create a Treasure token.",
    )
    insert(
        path,
        "tax",
        "ZZZ Opponent Engine",
        "Enchantment",
        "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
    )
    result = functional_search(
        path,
        document,
        FunctionalSearchArgs(
            type_line="enchantment",
            draw_event="opponent_action",
            prefer_recurring=True,
            prefer_multifunctional=True,
            limit=1,
        ),
    )
    assert result["cards"][0]["id"] == "tax"
