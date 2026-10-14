from copy import deepcopy

import pytest

from sabermetrics.combination_intelligence import analyze_combinations


def fixture(kind="vivi"):
    engine = {
        "name": "Engine",
        "type_line": "Creature",
        "oracle_text": (
            "Whenever you cast a noncreature spell, put a +1/+1 counter on Engine and it deals 1 damage to each opponent."
            if kind == "vivi"
            else "Whenever the Ring tempts you, you may discard your hand. If you do, draw four cards."
        ),
    }
    spell = {
        "name": "Cantrip",
        "type_line": "Sorcery",
        "mana_cost": "{U}",
        "oracle_text": (
            "Draw a card." if kind == "vivi" else "Draw a card. The Ring tempts you."
        ),
    }
    aura = {
        "name": "Aura",
        "type_line": "Enchantment — Aura",
        "oracle_text": "Enchant creature\nWhenever enchanted creature deals damage to an opponent, you may draw a card.",
    }
    doc = {"entries": [engine, spell, aura]}
    state = {
        "battlefield": ["Engine", "Aura"],
        "hand": ["Cantrip", "Other"],
        "attachments": {"Aura": "Engine"},
        "hand_count": 2,
        "library_count": 20,
        "mana": {"U": 1},
        "opponent_count": 3,
        "own_turn": True,
        "main_phase": True,
        "priority": True,
        "stack_empty": True,
        "no_interference": True,
        "no_other_effects": True,
        "choose_optional_draw": True,
        "choose_discard": True,
    }
    return doc, state


def replay(doc, state):
    return analyze_combinations(doc, state)["combinations"][0]["replay"]


def test_vivi_three_opponents_exact_resources_and_stack_order():
    doc, state = fixture()
    original = deepcopy(state)
    result = replay(doc, state)
    assert result["status"] == "verified_within_bounded_model"
    assert result["final_resources"] == {
        "mana": {"U": 0},
        "hand_count": 5,
        "library_count": 16,
        "graveyard": ["Cantrip"],
        "engine_counters": 1,
        "damage_each_opponent": 1,
    }
    assert [r["action"] for r in result["trace"]] == [
        "cast",
        "resolve_cast_trigger",
        "resolve_damage_draw_triggers",
        "spell_draw",
        "finish_spell",
    ]
    assert result["trace"][1]["damage_each_opponent"] == 1
    assert result["win_verified"] is False
    assert state == original


def test_sauron_discards_cantrip_draw_and_remaining_hand_after_spell():
    doc, state = fixture("sauron")
    result = replay(doc, state)
    assert result["final_resources"]["hand_count"] == 4
    assert result["final_resources"]["library_count"] == 15
    assert result["trace"][-1]["cards_discarded"] == 2
    assert [r["action"] for r in result["trace"]] == [
        "cast",
        "spell_draw",
        "ring_tempts",
        "finish_spell",
        "resolve_ring_trigger",
    ]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"mana": {"R": 8}}, "colored mana"),
        ({"battlefield": ["Aura"]}, "battlefield"),
        ({"hand": ["Other"]}, "not in hand"),
        ({"attachments": {}}, "attached"),
        ({"own_turn": False}, "Sorcery"),
        ({"main_phase": False}, "Sorcery"),
        ({"library_count": 3}, "Insufficient library"),
        ({"choose_optional_draw": False}, "choice"),
        ({"no_other_effects": False}, "no_other_effects"),
        ({"hand_count": 0}, "contradicts"),
    ],
)
def test_missing_prerequisites_rejected_without_partial_trace(change, reason):
    doc, state = fixture()
    state.update(change)
    result = replay(doc, state)
    assert result["status"] == "rejected"
    assert reason in result["reason"]
    assert result["trace"] == []


def test_combat_only_aura_does_not_draw_from_vivi():
    doc, _ = fixture()
    doc["entries"][2][
        "oracle_text"
    ] = "Enchant creature\nWhenever enchanted creature deals combat damage to an opponent, you may draw a card."
    assert analyze_combinations(doc)["status"] == "abstained"


def test_unknown_spell_text_and_alternate_costs_abstain():
    for change in (
        {"oracle_text": "Draw a card. Sacrifice a creature."},
        {"mana_cost": "{U/P}"},
        {"mana_cost": ""},
    ):
        doc, _ = fixture()
        doc["entries"][1].update(change)
        assert analyze_combinations(doc)["status"] == "abstained"


def test_static_ingredients_are_not_replay_or_win():
    doc, _ = fixture()
    line = analyze_combinations(doc)["combinations"][0]
    assert line["status"] == "inferred"
    assert line["replay"]["status"] == "not_run"


def test_sauron_optional_discard_and_library_rejected():
    doc, state = fixture("sauron")
    state["choose_discard"] = False
    assert replay(doc, state)["status"] == "rejected"
    state["choose_discard"] = True
    state["library_count"] = 4
    assert replay(doc, state)["status"] == "rejected"


def test_opt_prefix_leaves_spell_on_stack_and_tracks_trigger_resources():
    doc, state = fixture()
    doc["entries"][1].update(type_line="Instant", oracle_text="Scry 1.\nDraw a card.")
    result = replay(doc, state)
    assert result["status"] == "verified_prefix_only"
    assert result["stack"] == ["Cantrip"]
    assert result["final_resources"]["hand_count"] == 4
    assert result["final_resources"]["library_count"] == 17
    assert result["final_resources"]["engine_counters"] == 1
    assert result["final_resources"]["damage_each_opponent"] == 1
    assert len(result["trace"]) == 3
    assert result["win_verified"] is False


def test_forged_replay_payload_rejected():
    from sabermetrics.combination_intelligence import replay_combination

    doc, state = fixture()
    line = analyze_combinations(doc)["combinations"][0]
    line["model_cards"][0]["oracle_text"] = "Flying"
    assert replay_combination(line, state)["status"] == "rejected"


def test_call_of_ring_sauron_upkeep_and_missing_choice():
    doc, state = fixture("sauron")
    source = {
        "name": "Call",
        "oracle_text": "At the beginning of your upkeep, the Ring tempts you.\nWhenever you choose a creature as your Ring-bearer, you may pay 2 life. If you do, draw a card.",
    }
    doc["entries"] = [doc["entries"][0], source]
    state.update(
        battlefield=["Engine", "Call"],
        beginning_of_upkeep=True,
        decline_ring_bearer_draw=True,
    )
    result = replay(doc, state)
    assert result["status"] == "verified_within_bounded_model"
    assert result["final_resources"]["hand_count"] == 4
    assert result["final_resources"]["library_count"] == 16
    assert result["trace"][-1]["cards_discarded"] == 2
    state.pop("decline_ring_bearer_draw")
    assert replay(doc, state)["status"] == "rejected"


def reference_decks():
    import json
    from pathlib import Path

    return json.loads(
        (
            Path(__file__).parent / "fixtures" / "combination_oracle_reference.json"
        ).read_text()
    )["decks"]


@pytest.mark.parametrize("deck_name", ["affordable-vivi", "high-power-vivi"])
def test_actual_reference_vivi_supports_three_payoffs_without_claiming_loop(deck_name):
    report = analyze_combinations(reference_decks()[deck_name])
    assert {"Curiosity", "Ophidian Eye", "Tandem Lookout"} <= {
        line["cards"][2] for line in report["combinations"]
    }
    for line in report["combinations"]:
        state = fixture()[1]
        state.update(
            battlefield=[line["cards"][0], line["cards"][2]],
            hand=[line["cards"][1]],
            hand_count=1,
            attachments={line["cards"][2]: line["cards"][0]},
            paired_with={line["cards"][2]: line["cards"][0]},
        )
        from sabermetrics.combination_intelligence import replay_combination

        result = replay_combination(line, state)
        assert result["status"] == "verified_prefix_only"
        assert result["final_resources"]["hand_count"] == 3
        assert result["final_resources"]["library_count"] == 17
        assert result["win_verified"] is False
        state["paired_with"] = {}
        if line["cards"][2] == "Tandem Lookout":
            assert replay_combination(line, state)["status"] == "rejected"


@pytest.mark.parametrize("deck_name", ["affordable-sauron", "high-power-sauron"])
def test_actual_reference_sauron_upkeep_and_unsupported_breach(deck_name):
    report = analyze_combinations(reference_decks()[deck_name])
    assert len(report["combinations"]) == 1
    assert report["combinations"][0]["cards"] == [
        "Sauron, the Dark Lord",
        "Call of the Ring",
    ]
    assert report["combinations"][0]["kind"] == "upkeep_ring_discard_draw"
    assert "Underworld Breach" not in report["combinations"][0]["cards"]


def test_sideboard_piece_does_not_supply_deck_combination():
    doc, _ = fixture()
    doc["entries"][2]["zone_name"] = "Sideboard"
    assert analyze_combinations(doc)["status"] == "abstained"


def test_reference_summary_is_bounded_and_analysis_does_not_mutate():
    import json

    from sabermetrics.combination_intelligence import summarize_combinations

    doc = reference_decks()["affordable-vivi"]
    original = deepcopy(doc)
    report = analyze_combinations(doc)
    summary = summarize_combinations(report)
    assert doc == original
    assert len(summary["combinations"]) <= 4
    assert summary["omitted_count"] == len(report["combinations"]) - len(
        summary["combinations"]
    )
    assert all(len(e["oracle_excerpt"]) <= 240 for e in summary["evidence"].values())
    assert len(json.dumps(summary).encode()) < 8000
    assert "model_cards" not in json.dumps(summary)
    assert "relationships" not in json.dumps(summary)


def test_summary_retains_original_replay_indices_for_diverse_payoffs():
    from sabermetrics.combination_intelligence import summarize_combinations

    report = analyze_combinations(reference_decks()["affordable-vivi"])
    summary = summarize_combinations(report)
    for line in summary["combinations"]:
        assert (
            report["combinations"][line["combination_index"]]["cards"] == line["cards"]
        )
    assert {"Curiosity", "Ophidian Eye", "Tandem Lookout"} <= {
        line["cards"][-1] for line in summary["combinations"]
    }


def test_summary_retains_conditional_action_costs_timing_and_pairing():
    from sabermetrics.combination_intelligence import summarize_combinations

    report = analyze_combinations(reference_decks()["affordable-vivi"])
    summary = summarize_combinations(report)
    for line in summary["combinations"]:
        contract = summary["action_contracts"][line["action_contract_ref"]]
        assert contract[0]["cost"] == "printed colored and generic mana"
        assert "timing" in contract[0]
        assert line["prerequisites"]
        if line["cards"][-1] == "Tandem Lookout":
            assert contract[2]["choice"] == "mandatory draws"
            assert "damage-draw creature paired with engine" in line["prerequisites"]
