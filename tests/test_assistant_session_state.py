import json

from sabermetrics.assistant_conversation_context import SessionFinding
from sabermetrics.assistant_discovery_contract import (
    DiscoveryLedger,
    candidate_from_card,
)
from sabermetrics.assistant_session_state import (
    SessionState,
    compact_preferences,
    evidence_text,
    expand_preferences,
    load_session_state,
    protected_session_context,
    session_state_research,
    update_session_state,
)


def fixture_state():
    card = {
        "id": "orb",
        "name": "Winter Orb",
        "type_line": "Artifact",
        "oracle_text": "As long as Winter Orb is untapped, players can't untap more than one land.",
    }
    finding = SessionFinding(
        kind="rules",
        claim="The supplied Orb text has an untapped condition, not a measurement.",
        evidence=[{"reference": "card:orb", "quote": card["oracle_text"]}],
    )
    return update_session_state(
        SessionState(),
        findings=[finding],
        ledger=DiscoveryLedger(candidates=[candidate_from_card(card)]),
        evidence={"card:orb": evidence_text(card)},
        source_turn="u4",
    )


def test_roundtrip_survives_tool_noise_and_source_message_eviction():
    state = fixture_state()
    messages = [
        {
            "role": "assistant",
            "presentation": {"research": session_state_research(state)},
        }
    ]
    messages += [{"role": "tool", "content": "unrelated noise"}] * 40
    messages += [{"role": "user", "content": "Wrap up the Orb conditions."}]
    loaded = load_session_state(messages)
    assert loaded == state
    assert loaded.findings[0].kind == "rules"
    assert loaded.findings[0].source_turn == "u4"


def test_unbacked_assistant_prose_and_standalone_claims_are_not_imported():
    state = fixture_state()
    messages = [
        {
            "role": "assistant",
            "content": "Winter Orb wins 99% of games.",
            "research": {
                "session_findings": [state.findings[0].model_dump()],
                "discovery_ledger": state.ledger.model_dump(),
            },
        }
    ]
    assert load_session_state(messages) == SessionState()
    raw = session_state_research(state)
    raw["session_state"]["source_quotes"] = {}
    assert (
        load_session_state([{"role": "assistant", "research": raw}]) == SessionState()
    )


def test_new_claims_must_match_actual_source_ids_and_exact_quote():
    finding = SessionFinding(
        kind="empirical",
        claim="The actual sample contains 100 games.",
        evidence=[{"reference": "goldfish:7", "quote": "100 measured games"}],
    )
    assert not update_session_state(
        SessionState(),
        findings=[finding],
        evidence={"goldfish:8": "100 measured games"},
    ).findings
    assert not update_session_state(
        SessionState(), findings=[finding], evidence={"goldfish:7": "No measured games"}
    ).findings
    assert update_session_state(
        SessionState(),
        findings=[finding],
        evidence={"goldfish:7": "Report: 100 measured games."},
    ).findings


def test_candidate_identity_facts_and_semantic_annotations_are_checked():
    card = {
        "id": "a",
        "name": "Actual Card",
        "oracle_text": "Draw a card.\nThen discard a card.",
    }
    candidate = candidate_from_card(card).model_copy(
        update={
            "function_relevance": ["Guaranteed instant win"],
            "conditions": ["invented"],
            "costs": ["invented"],
        }
    )
    ledger = DiscoveryLedger(candidates=[candidate])
    missing = update_session_state(
        SessionState(), ledger=ledger, evidence={"card:other": evidence_text(card)}
    )
    assert not missing.ledger.candidates
    wrong_identity = update_session_state(
        SessionState(),
        ledger=ledger,
        evidence={"card:a": "A different card. Draw a card.\nThen discard a card."},
    )
    assert not wrong_identity.ledger.candidates
    result = update_session_state(
        SessionState(), ledger=ledger, evidence={"card:a": json.dumps(card)}
    )
    kept = result.ledger.candidates[0]
    assert kept.source_facts[0].value == card["oracle_text"]
    assert kept.function_relevance == kept.conditions == kept.costs == []


def test_membership_is_refreshed_and_findings_are_bounded():
    state = fixture_state()
    state = update_session_state(state, evidence={}, in_deck_ids={"orb"})
    assert state.ledger.candidates[0].in_deck
    state = update_session_state(state, evidence={}, in_deck_ids=set())
    assert not state.ledger.candidates[0].in_deck
    findings = [
        SessionFinding(
            kind="limitation",
            claim=f"Measured sample {i} is unavailable.",
            evidence=[
                {"reference": f"tool:{i}", "quote": "No measured report is available."}
            ],
        )
        for i in range(40)
    ]
    state = update_session_state(
        state,
        findings=findings,
        evidence={f"tool:{i}": "No measured report is available." for i in range(40)},
    )
    assert len(state.findings) == 32
    assert len(state.source_quotes) == 33  # 32 findings and the retained Orb


def test_context_protects_all_scope_core_and_identity_fields_under_pressure():
    state = fixture_state()
    preferences = [
        {
            "dimension": "core_card",
            "value": "Winter Orb",
            "strength": "required",
            "scope": "recommendations",
            "polarity": "include",
            "operation": "set",
            "quote": "My core cards are: Winter Orb",
            "source_turn": "u1",
        }
    ]
    context = protected_session_context(state, preferences, max_chars=1)
    assert expand_preferences(context["preferences"]) == preferences
    assert context["discovery_candidates"][0]["name"] == "Winter Orb"
    assert context["findings_omitted"] == len(state.findings)
    assert context["candidate_facts_omitted"] == 1
    assert context["mandatory_context_exceeds_budget"]
    assert len(state.ledger.candidates[0].source_facts) == 2


def test_latest_explicit_empty_envelope_clears_prior_state():
    messages = [
        {"role": "assistant", "research": session_state_research(fixture_state())},
        {"role": "assistant", "research": session_state_research(SessionState())},
    ]
    assert load_session_state(messages) == SessionState()


def test_direct_facts_are_automatic_findings_and_empirical_revision_is_scoped():
    card = {
        "id": "a",
        "name": "Actual Card",
        "mana_cost": "{2}",
        "oracle_text": "Draw a card.",
    }
    state = update_session_state(
        SessionState(),
        ledger=DiscoveryLedger(candidates=[candidate_from_card(card)]),
        evidence={"card:a": json.dumps(card)},
        revision=1,
    )
    assert state.discovery_ledger.candidates[0].name == "Actual Card"
    assert any("mana_cost: {2}" in row.claim for row in state.findings)
    assert any("oracle_text: Draw a card." in row.claim for row in state.findings)
    empirical = SessionFinding(
        kind="empirical",
        claim="Revision one sample has 100 games.",
        evidence=[{"reference": "sim:1", "quote": "Measured 100 games"}],
    )
    state = update_session_state(
        state,
        findings=[empirical],
        evidence={"sim:1": "Measured 100 games"},
        revision=1,
    )
    assert any(row.kind == "empirical" for row in state.findings)
    persisted = load_session_state(
        [{"role": "assistant", "research": session_state_research(state)}]
    )
    assert persisted.revision == 1
    assert any(row.kind == "empirical" for row in persisted.findings)
    revised = update_session_state(state, evidence={}, revision=2)
    assert not any(row.kind == "empirical" for row in revised.findings)
    assert any(row.kind == "candidate" for row in revised.findings)


def test_twelve_candidate_context_fits_and_retains_every_identity():
    cards = [
        {
            "id": str(i),
            "name": f"Candidate {i}",
            "mana_cost": "{2}",
            "oracle_text": "Draw a card. " * 8,
        }
        for i in range(12)
    ]
    state = update_session_state(
        SessionState(),
        ledger=DiscoveryLedger(
            candidates=[candidate_from_card(card) for card in cards]
        ),
        evidence={f"card:{card['id']}": json.dumps(card) for card in cards},
    )
    context = protected_session_context(state, [])
    assert len(json.dumps(context, ensure_ascii=False)) <= 6000
    assert len(context["discovery_candidates"]) == 12
    assert context["findings_omitted"] or context["candidate_facts_omitted"]


def test_twenty_candidates_drop_duplicate_findings_before_all_printed_facts():
    cards = [
        {
            "id": str(i),
            "name": f"Candidate {i}",
            "mana_cost": "{2}",
            "type_line": "Artifact",
            "oracle_text": "Draw a card. " * 8,
        }
        for i in range(20)
    ]
    state = update_session_state(
        SessionState(),
        ledger=DiscoveryLedger(
            candidates=[
                candidate_from_card(card, rank=i + 1) for i, card in enumerate(cards)
            ]
        ),
        evidence={f"card:{card['id']}": json.dumps(card) for card in cards},
    )
    context = protected_session_context(state, [])
    assert len(json.dumps(context, ensure_ascii=False)) <= 6000
    assert len(context["discovery_candidates"]) == 20
    assert context["findings_omitted"] == len(state.findings)
    kept = context["discovery_candidates"]
    assert "oracle_text" in kept[0]["printed_fields"]
    assert any("mana_cost" in card["printed_fields"] for card in kept)
    assert context["candidate_facts_omitted"] == sum(
        len(card.source_facts) for card in state.ledger.candidates
    ) - sum(len(card["printed_fields"]) for card in kept)


def test_preference_compaction_is_exact_and_shares_core_quote():
    preferences = [
        {
            "dimension": "core_card",
            "value": name,
            "strength": "required",
            "polarity": "include",
            "scope": "recommendations",
            "operation": "set",
            "quote": "My six core cards are: Winter Orb; Static Orb; Dramatic Reversal; Isochron Scepter; Sol Ring; Skullclamp.",
            "source_turn": "u1",
        }
        for name in [
            "Winter Orb",
            "Static Orb",
            "Dramatic Reversal",
            "Isochron Scepter",
            "Sol Ring",
            "Skullclamp",
        ]
    ]
    preferences.append(
        {
            "dimension": "constraint",
            "value": "permanents only",
            "strength": "required",
            "scope": "recommendations",
            "polarity": "include",
            "operation": "set",
            "quote": "Hard rule: permanents only.",
        }
    )
    packed = compact_preferences(preferences)
    assert len(packed["evidence"]) == 2
    assert expand_preferences(packed) == preferences
    assert len(json.dumps(packed)) < len(json.dumps(preferences))


def test_lean_fields_flag_oracle_excerpts_and_keep_group_balanced_facts():
    cards = [
        {
            "id": str(i),
            "name": f"Candidate {i}",
            "mana_cost": "{2}",
            "type_line": "Artifact",
            "oracle_text": "Draw a card. " * 50,
        }
        for i in range(24)
    ]
    candidates = [
        candidate_from_card(
            card, rank=i + 1, group_ids=["common" if i < 20 else "rare"]
        )
        for i, card in enumerate(cards)
    ]
    state = update_session_state(
        SessionState(),
        ledger=DiscoveryLedger(candidates=candidates),
        evidence={f"card:{card['id']}": json.dumps(card) for card in cards},
    )
    packed = protected_session_context(state, [])
    assert len(json.dumps(packed, ensure_ascii=False)) <= 6000
    assert len(packed["discovery_candidates"]) == 24
    assert any(
        card["printed_fields"]
        for card in packed["discovery_candidates"]
        if "rare" in card["group_ids"]
    )
    assert any(card.get("excerpted_fields") for card in packed["discovery_candidates"])
    assert all("source_facts" not in card for card in packed["discovery_candidates"])


def test_each_group_head_keeps_oracle_and_cost_for_twenty_four_candidates():
    cards = [
        {
            "id": f"card-{i}",
            "name": f"Candidate {i}",
            "type_line": "Creature",
            "mana_cost": "{2}{B}",
            "oracle_text": "When another creature dies, draw a card. " * 12,
        }
        for i in range(24)
    ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(card, rank=i % 8 + 1, group_ids=[f"list_{i // 8 + 1}"])
            for i, card in enumerate(cards)
        ]
    )
    packed = protected_session_context(SessionState(ledger=ledger), [])
    assert len(json.dumps(packed, ensure_ascii=False)) <= 6000
    assert len(packed["discovery_candidates"]) == 24
    by_id = {row["card_id"]: row for row in packed["discovery_candidates"]}
    for head in (0, 8, 16):
        assert "oracle_text" in by_id[f"card-{head}"]["printed_fields"]
        assert "mana_cost" in by_id[f"card-{head}"]["printed_fields"]


def test_head_oracle_retains_trailing_activation_restriction_at_nine_thousand():
    oracle = (
        "{2}, Sacrifice another creature: Draw a card. "
        + "This is printed rules text. " * 12
        + "Activate only once each turn."
    )
    cards = [
        {
            "id": f"card-{i}",
            "name": f"Candidate {i}",
            "type_line": "Creature",
            "mana_cost": "{2}{B}",
            "oracle_text": oracle,
        }
        for i in range(24)
    ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(card, rank=i % 8 + 1, group_ids=[f"list_{i // 8 + 1}"])
            for i, card in enumerate(cards)
        ]
    )
    packed = protected_session_context(SessionState(ledger=ledger), [], max_chars=9000)
    assert len(json.dumps(packed, ensure_ascii=False)) <= 9000
    by_id = {row["card_id"]: row for row in packed["discovery_candidates"]}
    for i in (0, 8, 16):
        assert by_id[f"card-{i}"]["printed_fields"]["oracle_text"] == oracle
        assert "oracle_text" not in by_id[f"card-{i}"].get("excerpted_fields", [])
    assert "retrieve card_details" in packed["scope"]


def test_nonhead_oracle_never_exposes_half_activation_cost():
    cards = [
        {"id": "head", "name": "Head", "oracle_text": "Draw a card."},
        {
            "id": "long",
            "name": "Long Cost",
            "oracle_text": "Sacrifice "
            + "a creature and " * 20
            + ": Draw a card. Activate only once each turn.",
        },
    ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(card, rank=i + 1) for i, card in enumerate(cards)
        ]
    )
    packed = protected_session_context(SessionState(ledger=ledger), [])
    long_card = next(
        card for card in packed["discovery_candidates"] if card["card_id"] == "long"
    )
    assert "oracle_text" not in long_card["printed_fields"]
    assert "oracle_text" in long_card["omitted_fields"]


def test_priority_view_keeps_eight_complete_current_bundles_and_forty_identities():
    cards = [
        {
            "id": f"00000000-0000-0000-0000-{i:012d}",
            "name": f"Source Candidate {i}",
            "mana_cost": "{2}{B}",
            "type_line": "Creature — Human",
            "oracle_text": "{1}, {T}, Sacrifice another creature: Draw a card. "
            + "This ability has a printed condition. " * 3
            + "Activate only once each turn.",
        }
        for i in range(40)
    ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(card, rank=i + 1, group_ids=[f"list_{i % 3 + 1}"])
            for i, card in enumerate(cards)
        ]
    )
    state = SessionState(ledger=ledger)
    priorities = [card["id"] for card in cards[-8:]]
    packed = protected_session_context(
        state, [], max_chars=9000, priority_card_ids=priorities
    )
    assert len(json.dumps(packed, ensure_ascii=False)) <= 9000
    assert len(packed["discovery_candidates"]) == 40
    assert packed["priority_bundles_retained"] == 8
    for index, row in enumerate(packed["discovery_candidates"]):
        assert row["name"] == cards[index]["name"]
        assert row["group_ids"] == [f"list_{index % 3 + 1}"]
        if row["card_id"] in priorities:
            assert row["printed_fields"]["oracle_text"] == cards[index]["oracle_text"]
            assert row["printed_fields"]["mana_cost"] == cards[index]["mana_cost"]
            assert row["printed_fields"]["type_line"] == cards[index]["type_line"]
        else:
            assert "printed_fields" not in row
            assert "omitted_fields" not in row
    assert len(state.ledger.candidates[0].source_facts) == 3


def test_priority_view_drops_whole_lower_priority_bundles_without_oracle_cuts():
    cards = [
        {
            "id": str(i),
            "name": f"Card {i}",
            "mana_cost": "{B}",
            "type_line": "Creature",
            "oracle_text": "Pay 1 life, Sacrifice another creature: Draw a card. " * 25,
        }
        for i in range(12)
    ]
    state = SessionState(
        ledger=DiscoveryLedger(
            candidates=[
                candidate_from_card(card, rank=i + 1) for i, card in enumerate(cards)
            ]
        )
    )
    priorities = [card["id"] for card in cards]
    packed = protected_session_context(
        state, [], max_chars=6000, priority_card_ids=priorities
    )
    assert len(json.dumps(packed, ensure_ascii=False)) <= 6000
    assert packed["priority_bundle_shortfall"]
    retained = [
        row for row in packed["discovery_candidates"] if "printed_fields" in row
    ]
    assert retained
    assert [row["card_id"] for row in retained] == priorities[: len(retained)]
    for row in retained:
        assert (
            row["printed_fields"]["oracle_text"]
            == cards[int(row["card_id"])]["oracle_text"]
        )
        assert set(row["printed_fields"]) == {"oracle_text", "mana_cost", "type_line"}
    assert packed["candidate_facts_omitted"] == (12 - len(retained)) * 3


def test_unknown_priority_ids_do_not_consume_the_bundle_limit():
    from sabermetrics.assistant_discovery_contract import candidate_from_card

    candidates = [
        candidate_from_card(
            {
                "id": f"known-{i}",
                "name": f"Known {i}",
                "mana_cost": "{U}",
                "type_line": "Creature",
                "oracle_text": "{T}: Draw a card.",
            },
            rank=i + 1,
            in_deck=False,
        )
        for i in range(8)
    ]
    state = SessionState(ledger=DiscoveryLedger(candidates=candidates))
    priority = [f"unknown-{i}" for i in range(12)] + [c.card_id for c in candidates]
    view = protected_session_context(
        state, [], max_chars=9000, priority_card_ids=priority
    )
    assert view["priority_bundles_requested"] == 8
    assert view["priority_bundles_retained"] == 8
    assert view["priority_bundle_shortfall"] is False
