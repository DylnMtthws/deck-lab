"""Owned real and synthetic source fixtures; no held-out labels or model calls."""

import copy
import hashlib
import json

import pytest

from sabermetrics.assistant_discovery_contract import (
    DiscoveryLedger,
    candidate_from_card,
)
from sabermetrics.assistant_functional_search import (
    FunctionalSearchArgs,
    functional_search,
)
from sabermetrics.assistant_selection import CandidateSelectionDraft, selection_payload
from sabermetrics.assistant_strategic_obligations import (
    StrategicObligations,
    build_strategic_obligations,
    compact_strategic_obligations,
    render_strategic_obligations,
    strategic_rank_key,
)
from sabermetrics.assistant_suitability import candidate_suitability, deck_support_index
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_deck_intelligence import prepared as _base_prepared

prepared = _base_prepared


def card(text, **fields):
    return {
        "id": "lens",
        "oracle_id": "lens",
        "name": "Synthetic Lens",
        "type_line": "Artifact",
        "oracle_text": text,
        **fields,
    }


def obligations(source, request="repeatable draw", entries=None, packet=None):
    profile = candidate_suitability(
        source,
        deck_support_index({"entries": entries or []}),
        request,
        include_packet=False,
    )
    result = build_strategic_obligations(
        source, request, suitability=profile, deck_entries=entries, packet=packet
    )
    return result, {row.dimension: row for row in result.obligations}


def test_immediate_and_repeatable_requests_reverse_source_based_order():
    burst = card(
        "Draw three cards.", id="burst", oracle_id="burst", type_line="Sorcery"
    )
    ongoing = card("{T}: Draw a card.")

    def ranked(request):
        return sorted(
            [burst, ongoing],
            key=lambda c: strategic_rank_key(obligations(c, request)[0]),
        )

    assert ranked("immediate value draw")[0] == burst
    assert ranked("repeatable draw engine")[0] == ongoing


def test_actual_search_changes_order_before_limit(prepared):
    path, document = prepared
    insert(path, "obligation-burst", "Synthetic Burst", "Sorcery", "Draw three cards.")
    immediate = functional_search(
        path, document, FunctionalSearchArgs(query="immediate value", limit=1)
    )
    recurring = functional_search(
        path, document, FunctionalSearchArgs(query="repeatable draw", limit=1)
    )
    assert immediate["cards"][0]["id"] == "obligation-burst"
    assert recurring["cards"][0]["id"] == "draw"


def test_activation_control_does_not_make_resolution_draw_optional():
    _, rows = obligations(card("{1}: Draw a card."))
    assert rows["optionality"].facts["controls"] == [
        {"initiating": "voluntary_activation", "resolution": "required_if_resolving"}
    ]
    assert rows["future_uses"].status == "conditional"
    _, rows = obligations(card("{1}: You may draw a card."))
    assert (
        rows["optionality"].facts["controls"][0]["resolution"] == "conditional_choice"
    )
    assert rows["hand_delta"].status == "unknown"


def test_filtering_and_consumed_permanents_are_separate_from_hand_delta():
    _, filter_rows = obligations(card("{T}: Draw a card, then discard a card."))
    assert filter_rows["hand_delta"].facts["per_activation"] == 0
    assert filter_rows["net_advantage"].status == "contradicted"
    _, sac_rows = obligations(card("Sacrifice another creature: Draw a card."))
    assert sac_rows["hand_delta"].facts["per_activation"] == 1
    assert sac_rows["net_advantage"].status == "unknown"
    assert sac_rows["net_advantage"].facts["permanent_consumed"]
    assert sac_rows["resource_supply"].status == "unknown"


def test_counter_supply_arithmetic_is_exact_and_not_recursion():
    source = card(
        "Synthetic Lens enters with three charge counters on it.\n{1}, Remove a charge counter from Synthetic Lens: Draw a card."
    )
    entries = [
        {
            "id": "return",
            "name": "Synthetic Return",
            "zone_name": "Library",
            "oracle_text": "Return target artifact card from your graveyard to your hand.",
        }
    ]
    _, rows = obligations(source, entries=entries)
    assert rows["counter_renewal"].facts["finite_supply"] == {
        "starting_counters": 3,
        "counters_per_use": 1,
        "uses_without_renewal": 3,
        "future_uses_after_first": 2,
        "counter_type": "charge",
    }
    assert rows["counter_renewal"].status == "unknown"
    assert "recast" not in rows["counter_renewal"].facts
    _, mismatched = obligations(
        card(source["oracle_text"].replace("three charge", "three +1/+1"))
    )
    assert not mismatched["counter_renewal"].facts.get("finite_supply")


def test_self_consumption_and_entry_value_need_reset_independent_of_other_abilities():
    _, self_rows = obligations(card("Sacrifice Synthetic Lens: Draw two cards."))
    assert self_rows["future_uses"].status == "contradicted"
    _, enters = obligations(
        card("When Synthetic Lens enters, draw a card.\n{1}: Add {C}.")
    )
    assert enters["future_uses"].status == "contradicted"
    assert any("recast" in c for c in enters["future_uses"].conditions)


def test_omitted_context_is_not_failed_support_detection_or_absence():
    source = card("Sacrifice another creature: Draw a card.")
    _, omitted = obligations(source)
    _, scanned = obligations(source, entries=[])
    assert omitted["setup"].gap == "context_omitted"
    assert scanned["setup"].gap == "support_not_found"
    assert scanned["setup"].status == omitted["setup"].status == "unknown"
    assert "absent" not in render_strategic_obligations(obligations(source)[0])


def test_exact_refs_resolve_into_supplied_source_and_keep_named_support():
    support = {
        "id": "token",
        "oracle_id": "token",
        "name": "Synthetic Token Maker",
        "zone_name": "Library",
        "type_line": "Creature",
        "oracle_text": "{2}: Create a 1/1 white Soldier creature token.",
    }
    source = card("Sacrifice another creature: Draw a card.")
    result, rows = obligations(source, entries=[support])
    assert rows["setup"].status == "conditional"
    assert rows["setup"].facts["providers"] == [support["name"]]
    sources = {"lens": source, "token": support}
    for reference in result.evidence:
        text = sources[reference.source_id][reference.field]
        assert hashlib.sha256(text.encode()).hexdigest() == reference.sha256
        assert text[reference.start : reference.end]
    assert rows["resource_supply"].status == "unknown"


def test_joint_cut_packet_overrides_stale_pre_cut_support_and_preserves_losses():
    support = {
        "id": "token",
        "oracle_id": "token",
        "name": "Synthetic Token Maker",
        "zone_name": "Library",
        "type_line": "Creature",
        "oracle_text": "{2}: Create a 1/1 white Soldier creature token.",
    }
    source = card("Sacrifice another creature: Draw a card.")
    # Existing package machinery supplies the prospective composition. Do not
    # rebuild it from deck_entries (which deliberately still contains the cut).
    from sabermetrics.assistant_evidence_packet import build_recommendation_packet

    packet = build_recommendation_packet(
        {"entries": [support]}, source, outgoing=support
    )
    before = copy.deepcopy(packet)
    result, rows = obligations(source, entries=[support], packet=packet)
    assert packet == before
    assert rows["setup"].status == "unknown"
    assert not rows["setup"].facts["providers"]
    assert rows["lost_roles"].status == "established"
    assert "creature_token_producer" in rows["lost_roles"].facts["roles"]
    assert not any(
        e.source_id == "token" and e.field == "oracle_text" for e in result.evidence
    )


def test_missing_and_partial_text_abstains_including_stale_arithmetic():
    for source in [
        card(""),
        card("{T}: Draw a card.", oracle_text_truncated=True),
        card("{T}: Draw a card.", card_faces=[{"name": "Back"}]),
    ]:
        result, rows = obligations(source)
        assert not result.coverage["oracle_complete"]
        assert rows["hand_delta"].status == "unknown"
        assert rows["future_uses"].status == "unknown"
    source = card("{T}: Draw a card, then discard a card.")
    result = build_strategic_obligations(
        source,
        suitability={"resource_accounting": {"net_hand_delta_per_activation": 100}},
    )
    assert (
        next(r for r in result.obligations if r.dimension == "hand_delta").facts[
            "per_activation"
        ]
        == 0
    )


def test_payload_vocabulary_and_render_hooks_keep_whole_facts_and_provider_schema():
    source = card("{T}: Draw a card.")
    ledger = DiscoveryLedger(candidates=[candidate_from_card(source)])
    payload, retained = selection_payload(
        "repeatable draw", [], ledger, {"lens": source}, [], set(), []
    )
    data = json.loads(payload)
    contract = data["candidates"][0]["strategic_obligations"]
    vocab = data["strategic_obligation_vocabulary"]
    assert contract["view"] == "shared_vocabulary"
    assert all(
        row["dimension"] in vocab["propositions"] for row in contract["obligations"]
    )
    assert all(
        i < len(vocab["conditions"])
        for row in contract["obligations"]
        for i in row.get("condition_refs", [])
    )
    assert retained.candidates
    result, _ = obligations(source)
    assert (
        StrategicObligations.model_validate(compact_strategic_obligations(result))
        == result
    )
    rendered = render_strategic_obligations(result, max_chars=800)
    assert "future_uses [conditional]" in rendered
    assert "additional strategic obligations" in rendered
    assert len(rendered) <= 800
    schema = CandidateSelectionDraft.model_json_schema()
    assert set(schema["properties"]) == {"groups", "assessments", "shortfalls"}


@pytest.mark.parametrize("limit", [0, 10, 50])
def test_tiny_render_budgets_do_not_slice_statements(limit):
    result, _ = obligations(card("{T}: Draw a card."))
    rendered = render_strategic_obligations(result, max_chars=limit)
    assert len(rendered) <= limit


def test_external_entry_triggers_and_per_turn_limits_remain_repeatable():
    for text in (
        "Whenever another creature enters, draw a card.",
        "{1}: Draw a card. Activate only once each turn.",
    ):
        _, rows = obligations(card(text))
        assert rows["future_uses"].status == "conditional"
    _, rows = obligations(card("When Synthetic Lens dies, draw a card."))
    assert rows["future_uses"].status == "contradicted"


def test_negated_repeatability_does_not_override_positive_immediate_request():
    source = card("{T}: Draw a card.")
    result, _ = obligations(
        source, "I do not want repeatable engines but want immediate value"
    )
    assert result.request_priorities[0] == "first_use"
    assert "future_uses" not in result.request_priorities


def test_choice_after_draw_does_not_make_that_draw_optional():
    _, rows = obligations(card("{1}: Draw a card, then you may discard a card."))
    assert (
        rows["optionality"].facts["controls"][0]["resolution"]
        == "required_if_resolving"
    )


def test_delayed_hand_payment_is_not_netted_against_current_draw():
    _, rows = obligations(
        card("{T}: Draw a card. At the beginning of your next upkeep, discard a card.")
    )
    assert rows["hand_delta"].status == "unknown"


def test_payload_omission_downgrades_named_support_instead_of_certifying_it():
    source = card("Sacrifice another creature: Draw a card.")
    support = {
        "id": "token",
        "oracle_id": "token",
        "name": "Synthetic Token Maker",
        "zone_name": "Library",
        "type_line": "Creature",
        "oracle_text": "{2}: Create a 1/1 white Soldier creature token.\n"
        + "Long unrelated source record. " * 350,
    }
    source["suitability"] = candidate_suitability(
        source, deck_support_index({"entries": [support]}), "draw"
    )
    ledger = DiscoveryLedger(candidates=[candidate_from_card(source)])
    payload, _ = selection_payload(
        "draw", [], ledger, {"lens": source}, [], set(), [], deck_entries=[support]
    )
    data = json.loads(payload)
    assert data["omitted_deck_sources"] == [support["name"]]
    setup = next(
        row
        for row in data["candidates"][0]["strategic_obligations"]["obligations"]
        if row["dimension"] == "setup"
    )
    assert setup["status"] == "unknown"
    assert setup["gap"] == "context_omitted"


def test_spell_cast_card_consumption_is_included_in_net_hand_change():
    cantrip, rows = obligations(card("Draw a card.", type_line="Sorcery"))
    assert rows["hand_delta"].facts["spell_branches"][0]["net_hand_delta"] == 0
    assert rows["net_advantage"].status == "contradicted"
    gain, _ = obligations(card("Draw three cards.", type_line="Sorcery"))
    assert strategic_rank_key(gain) < strategic_rank_key(cantrip)


def test_known_fixed_gain_is_ranked_by_amount_without_a_quality_sum():
    one, _ = obligations(card("{1}: Draw a card."))
    two, _ = obligations(card("{1}: Draw two cards."))
    assert strategic_rank_key(two) < strategic_rank_key(one)


@pytest.mark.parametrize(
    "support_text,other_request",
    [
        ("{T}: Add {C}{C}.", "Choose repeatable mana production."),
        (
            "Whenever you cast an instant or sorcery spell, create a 1/1 red creature token.",
            "Choose a cast-triggered token producer; preserve the draw engine.",
        ),
        (
            "You may play an additional land on each of your turns.",
            "Choose extra land-play permission; preserve the draw engine.",
        ),
        (
            "Sacrifice another creature: Scry 1.",
            "Choose a repeatable sacrifice outlet; preserve the draw engine.",
        ),
    ],
)
def test_paired_function_requests_do_not_reward_support_as_a_draw_engine(
    support_text, other_request
):
    engine = card("Whenever you cast or copy an instant or sorcery spell, draw a card.")
    support = card(support_text)
    draw_engine, _ = obligations(engine, "Choose repeatable draw.")
    draw_support, _ = obligations(support, "Choose repeatable draw.")
    assert strategic_rank_key(draw_engine) < strategic_rank_key(draw_support)
    for source in (engine, support):
        contract, rows = obligations(source, other_request)
        assert strategic_rank_key(contract) == ()
        assert rows["request_relevance"].facts["ranking_scope"] == "abstain"
        # Abstaining from preordering still retains the typed evidence for the
        # caller/model, including controls, reset conditions and hand accounting.
        assert "future_uses" in rows
        assert "hand_delta" in rows


@pytest.mark.parametrize(
    "query",
    [
        "Choose a mana rock whose draw cash-in has no mana cost.",
        "Choose two mana output from one tap at low setup cost.",
        "Choose immediate mana from a landfall trigger.",
        "Choose extra land-play permission with landfall draw.",
        "Choose draw and ramp.",
        "Find draw and counterspells.",
        "Find draw and a counterspell.",
        "Recommend draw and protection.",
        "Find draw and mana rocks.",
        "Choose a repeatable engine.",
        "Compare internet resources.",
        "I do not want draw but want mana rocks.",
        "",
    ],
)
def test_draw_economics_cannot_preorder_unsupported_or_ambiguous_requests(query):
    sources = [
        card("{T}: Add {C}{C}."),
        card("{T}: Add {C}{C}.\n{2}, Sacrifice this artifact: Draw two cards."),
        card("Draw three cards.", type_line="Sorcery"),
        card("Whenever a land you control enters, draw a card."),
    ]
    assert {strategic_rank_key(obligations(c, query)[0]) for c in sources} == {()}


def test_unrelated_abilities_cannot_earn_draw_relevance_or_economics_tiebreaks():
    request = "Choose immediate draw with net card advantage."
    for text in ("{T}: Add {C}.", "Target opponent draws three cards."):
        unrelated, rows = obligations(card(text), request)
        own_draw, _ = obligations(card("{1}: Draw a card."), request)
        assert not rows["request_relevance"].facts["draw_route_detected"]
        assert strategic_rank_key(own_draw) < strategic_rank_key(unrelated)
    assert strategic_rank_key(obligations(card("{T}: Add {C}."), request)[0]) == (
        strategic_rank_key(obligations(card("Create a creature token."), request)[0])
    )


def test_old_contract_without_applicability_abstains_instead_of_using_global_defaults():
    contract, _ = obligations(card("{T}: Draw a card."))
    contract.coverage.pop("ranking_scope")
    assert strategic_rank_key(contract.model_dump()) == ()


@pytest.mark.parametrize("label", ["Magecraft", "Landfall", "Synthetic ability"])
def test_ability_word_labels_do_not_hide_repeatable_draw_or_change_exact_evidence(
    label,
):
    text = (
        f"{label} — Whenever you cast or copy an instant or sorcery spell, draw a card."
    )
    engine, rows = obligations(card(text))
    plain, _ = obligations(card(text.split(" — ", 1)[1]))
    cantrip, _ = obligations(card("Draw a card.", type_line="Sorcery"))
    assert rows["future_uses"].status == "conditional"
    assert strategic_rank_key(engine) == strategic_rank_key(plain)
    assert strategic_rank_key(engine) < strategic_rank_key(cantrip)
    assert rows["optionality"].facts["controls"] == [
        {"initiating": "triggered", "resolution": "required_if_resolving"}
    ]
    assert all(text[r.start : r.end] == text for r in engine.evidence)
    burst_request = "Choose immediate one-shot draw."
    assert strategic_rank_key(obligations(card(text), burst_request)[0]) > (
        strategic_rank_key(
            obligations(card("Draw a card.", type_line="Sorcery"), burst_request)[0]
        )
    )


def test_compact_no_cut_packet_can_use_exact_retained_current_deck_source():
    source = card("Sacrifice another creature: Draw a card.")
    support = {
        "id": "token",
        "oracle_id": "token",
        "name": "Synthetic Token Maker",
        "zone_name": "Library",
        "type_line": "Creature",
        "oracle_text": "{2}: Create a 1/1 white Soldier creature token.",
    }
    source["suitability"] = candidate_suitability(
        source, deck_support_index({"entries": [support]}), "draw"
    )
    ledger = DiscoveryLedger(candidates=[candidate_from_card(source)])
    payload, _ = selection_payload(
        "draw", [], ledger, {"lens": source}, [], set(), [], deck_entries=[support]
    )
    data = json.loads(payload)
    setup = next(
        row
        for row in data["candidates"][0]["strategic_obligations"]["obligations"]
        if row["dimension"] == "setup"
    )
    assert setup["status"] == "conditional"
    assert setup["facts"]["providers"] == [support["name"]]


def test_full_prospective_packet_with_no_matching_sources_is_scanned_not_omitted():
    from sabermetrics.assistant_evidence_packet import build_recommendation_packet

    source = card("Sacrifice another creature: Draw a card.")
    packet = build_recommendation_packet({"entries": []}, source)
    result = build_strategic_obligations(source, packet=packet)
    setup = next(row for row in result.obligations if row.dimension == "setup")
    assert setup.gap == "support_not_found"


def test_upkeep_self_sacrifice_is_a_consumed_source_requiring_reset():
    result, rows = obligations(
        card(
            "At the beginning of your upkeep, sacrifice Synthetic Lens and draw a card."
        )
    )
    assert rows["future_uses"].status == "contradicted"
    assert result.ranking["future_uses"] == 2
    assert rows["resource_supply"].facts["consumed"] == ["permanent"]
    assert rows["resource_supply"].status == "unknown"
    assert rows["net_advantage"].facts["permanent_consumed"]
    assert any(
        "return/recast" in condition for condition in rows["future_uses"].conditions
    )
    ongoing, _ = obligations(card("At the beginning of your upkeep, draw a card."))
    assert strategic_rank_key(ongoing) < strategic_rank_key(result)


def test_triggered_counter_consumption_requires_supply_and_renewal():
    for source in (
        card(
            "At the beginning of your upkeep, remove a charge counter from Synthetic Lens and draw a card."
        ),
        card(
            "Midnight Oil enters with seven hour counters on it.\n"
            "At the beginning of your upkeep, draw a card and remove two hour counters from Midnight Oil.\n"
            "Your maximum hand size is equal to the number of hour counters on Midnight Oil.\n"
            "Whenever you discard a card, you lose 1 life.",
            name="Midnight Oil",
            type_line="Enchantment",
        ),
    ):
        result, rows = obligations(source)
        assert "counter" in rows["resource_supply"].facts["consumed"]
        assert rows["resource_supply"].status == "unknown"
        assert rows["counter_renewal"].status == "unknown"
        assert result.ranking["future_uses"] > 0
        assert result.ranking["supply"] > 0
        assert any("replenishment" in c for c in rows["future_uses"].conditions)


def test_conditional_self_loss_is_not_certified_as_mandatory():
    _, rows = obligations(
        card(
            "At the beginning of your upkeep, you may sacrifice Synthetic Lens. If you do, draw a card."
        )
    )
    assert rows["future_uses"].status == "unknown"
    assert rows["resource_supply"].facts["consumed"] == ["permanent"]
    assert any("may consume" in c for c in rows["future_uses"].conditions)


def test_other_players_discard_is_not_our_resource_consumption():
    _, rows = obligations(
        card(
            "At the beginning of your upkeep, draw a card. Target opponent discards a card."
        )
    )
    assert rows["resource_supply"].facts["consumed"] == []


@pytest.mark.parametrize(
    "entry",
    [
        "Synthetic Lens enters with three charge counters on it for each artifact you control.",
        "Synthetic Lens enters with three charge counters on it if you control a creature.",
        "If you control a creature, Synthetic Lens enters with three charge counters on it.",
        "Synthetic Lens enters with three charge counters on it unless an opponent pays {1}.",
        "Synthetic Lens enters with three charge counters on it instead of two.",
        "Synthetic Lens enters with three charge counters on it or five if you control a creature.",
        "You may have Synthetic Lens enter with three charge counters on it.",
    ],
)
def test_contextual_counter_entry_never_yields_fixed_supply(entry):
    _, rows = obligations(
        card(entry + "\nRemove a charge counter from Synthetic Lens: Draw a card.")
    )
    assert rows["counter_renewal"].facts["finite_supply"] is None


def test_real_fixed_entry_retains_exact_supply_and_scaled_removal_abstains():
    source = card(
        "Trigon of Thought enters with three charge counters on it.\n"
        "{U}, {U}, {T}: Put a charge counter on Trigon of Thought.\n"
        "{2}, {T}, Remove a charge counter from Trigon of Thought: Draw a card.",
        name="Trigon of Thought",
    )
    _, rows = obligations(source)
    assert rows["counter_renewal"].facts["finite_supply"]["uses_without_renewal"] == 3
    _, rows = obligations(
        card(
            "Synthetic Lens enters with three charge counters on it.\n"
            "Remove a charge counter from Synthetic Lens for each creature you control: Draw a card."
        )
    )
    assert rows["counter_renewal"].facts["finite_supply"] is None


def test_separate_counter_replacement_clause_prevents_fixed_supply_claim():
    _, rows = obligations(
        card(
            "Synthetic Lens enters with three charge counters on it.\n"
            "If one or more counters would be put on Synthetic Lens, twice that many are put on it instead.\n"
            "Remove a charge counter from Synthetic Lens: Draw a card."
        )
    )
    assert rows["counter_renewal"].facts["finite_supply"] is None


@pytest.mark.parametrize(
    "text",
    [
        "{1}: Draw a card unless an opponent pays {1}.",
        "{1}: Draw a card if you control a creature.",
        "{1}: If you control a creature, draw a card.",
        "{1}: You may draw a card unless an opponent pays {1}.",
        "{1}: If you control a creature, you may draw a card.",
        "{1}: You may pay {1}. If you do, draw a card unless an opponent pays {1}.",
    ],
)
def test_activation_and_conditional_draw_outcome_are_separate(text):
    result, rows = obligations(card(text))
    control = rows["optionality"].facts["controls"][0]
    assert control["initiating"] == "voluntary_activation"
    assert control["resolution"] == "unknown"
    assert control["outcome_conditions"] == [text.split(":", 1)[1].strip()]
    compact = compact_strategic_obligations(result)
    restored = StrategicObligations.model_validate(compact)
    assert restored == result
    assert all(
        text[ref.start : ref.end] == text
        for ref in result.evidence
        if ref.field == "oracle_text"
    )


@pytest.mark.parametrize(
    "name,text",
    [
        (
            "Mindstorm Crown",
            "At the beginning of your upkeep, Mindstorm Crown deals 1 damage to you if you have a card in hand. If you have no cards in hand, draw a card.",
        ),
        (
            "Rhystic Study",
            "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
        ),
    ],
)
def test_real_conditional_draw_keeps_full_outcome(name, text):
    _, rows = obligations(card(text, name=name))
    control = rows["optionality"].facts["controls"][0]
    assert control["initiating"] == "triggered"
    assert control["resolution"] == "unknown"
    assert control["outcome_conditions"] == [text.split(",", 1)[1].strip()]


def test_intervening_if_trigger_condition_is_not_lost():
    text = "At the beginning of your upkeep, if you control a creature, draw a card."
    _, rows = obligations(card(text))
    control = rows["optionality"].facts["controls"][0]
    assert control["resolution"] == "unknown"
    assert control["outcome_conditions"] == ["if you control a creature, draw a card."]
