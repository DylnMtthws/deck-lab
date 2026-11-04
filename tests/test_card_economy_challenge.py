"""Independent authored branch arithmetic; no claim of strategic validation."""

import json
from pathlib import Path

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures/card_economy_challenge.json").read_text()
)


def test_authored_hand_math_includes_spell_and_discard_costs():
    cases = CORPUS["cases"]
    for case in cases:
        if case["net_hand_change"] is not None:
            assert (
                case["net_hand_change"]
                == case["draw_count"] - case["discard_count"] - case["hand_spell_cost"]
            ), case["id"]
    by_id = {c["id"]: c for c in cases}
    assert by_id["witches-mark-accepted-discard"]["net_hand_change"] == 0
    assert by_id["faithless-looting"]["net_hand_change"] == -1
    assert by_id["divination-positive"]["net_hand_change"] == 1
    assert by_id["deep-analysis-hand"]["net_hand_change"] == 1
    assert by_id["deep-analysis-flashback"]["net_hand_change"] == 2
    assert by_id["confound-reactive"]["net_hand_change"] == 0


def test_challenge_preserves_conditional_branches_and_unknown_states():
    cases = CORPUS["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    by_id = {c["id"]: c for c in cases}
    assert (
        by_id["witches-mark-accepted-discard"]["card"]["name"]
        == by_id["witches-mark-decline-discard"]["card"]["name"]
    )
    assert (
        by_id["witches-mark-accepted-discard"]["net_hand_change"]
        != by_id["witches-mark-decline-discard"]["net_hand_change"]
    )
    assert by_id["truncated-cost-unknown"]["net_hand_change"] is None
    assert by_id["variable-draw-unknown"]["net_hand_change"] is None
    assert "reactive_target_requirement" in by_id["confound-reactive"]["must_expose"]
    assert "additional_cost" in by_id["tormenting-voice-additional-cost"]["must_expose"]


def test_actual_profiles_match_authored_hand_branches_and_keep_unknowns():
    from sabermetrics.card_economy import card_economy_profile

    branch_names = {
        "witches-mark-accepted-discard": "optional_discard_paid",
        "witches-mark-decline-discard": "optional_discard_declined",
        "deep-analysis-hand": "target_yourself",
    }
    for case in CORPUS["cases"]:
        if case["cast_zone"] != "hand":
            continue  # The API explicitly supports from-hand accounting only.
        profile = card_economy_profile(case["card"])
        if case["net_hand_change"] is None:
            assert profile["status"] == "unknown", case["id"]
            assert profile["branches"] == [], case["id"]
            assert profile["unsupported"], case["id"]
            continue
        assert profile["status"] == "supported_from_hand_clause_accounting", case["id"]
        branch = next(
            b
            for b in profile["branches"]
            if b["condition"] == branch_names.get(case["id"], "normal_resolution")
        )
        assert branch["net_hand_delta"] == case["net_hand_change"], case["id"]
        assert branch["cards_drawn"] == case["draw_count"], case["id"]
        assert branch["cards_discarded"] == case["discard_count"], case["id"]
        assert profile["spell_cards_consumed"] == case["hand_spell_cost"], case["id"]
        assert profile["source_quotes"], case["id"]
        assert all(
            quote in case["card"]["oracle_text"] for quote in profile["source_quotes"]
        ), case["id"]


def test_actual_profiles_preserve_reactive_optional_and_flashback_requirements():
    from sabermetrics.card_economy import card_economy_profile

    cases = {c["id"]: c for c in CORPUS["cases"]}
    reactive = card_economy_profile(cases["confound-reactive"]["card"])
    assert "legal_target_spell" in reactive["requirements"]
    assert reactive["unconditional_card_access"] is False
    optional = card_economy_profile(cases["witches-mark-accepted-discard"]["card"])
    assert "card_in_hand_for_optional_discard" in optional["requirements"]
    assert optional["unconditional_card_access"] is False
    additional = card_economy_profile(cases["tormenting-voice-additional-cost"]["card"])
    assert "card_in_hand_for_additional_discard_cost" in additional["requirements"]
    assert additional["unconditional_card_access"] is False
    deep = card_economy_profile(cases["deep-analysis-flashback"]["card"])
    assert "choose_yourself_as_legal_player_target" in deep["requirements"]
    assert any("graveyard" in note.lower() for note in deep["unsupported"])
    assert "from hand" in deep["scope"]
    assert all(b["net_hand_delta"] == 1 for b in deep["branches"])
    assert (
        deep["branches"][0]["net_hand_delta"]
        != cases["deep-analysis-flashback"]["net_hand_change"]
    )


def test_printed_vivi_damage_is_fixed_despite_power_boost_context():
    from sabermetrics.card_economy import printed_cast_trigger_damage

    challenge = CORPUS["commander_claim_challenges"][0]
    result = printed_cast_trigger_damage({"oracle_text": challenge["commander_text"]})
    assert result["status"] == "supported_printed_clause"
    assert result["printed_damage_per_opponent"] == 1
    assert result["source_quote"] in challenge["commander_text"]
    assert "excludes power" in result["scope"]
    assert (
        printed_cast_trigger_damage({"oracle_text": challenge["incoming_text"]})[
            "status"
        ]
        == "unknown"
    )
