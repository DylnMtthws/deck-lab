import json

from sabermetrics.assistant_conversation_context import SessionFinding
from sabermetrics.assistant_discovery_contract import (
    DiscoveryLedger,
    candidate_from_card,
)
from sabermetrics.assistant_session_state import (
    SessionState,
    evidence_text,
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
    assert context["preferences"] == preferences
    assert context["discovery_candidates"][0]["name"] == "Winter Orb"
    assert context["findings_omitted"] == len(state.findings)
    assert context["candidate_facts_omitted"] == 2
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
    assert any(f["kind"] == "oracle_text" for f in kept[0]["source_facts"])
    assert any(f["kind"] == "mana_cost" for card in kept for f in card["source_facts"])
    assert context["candidate_facts_omitted"] == sum(
        len(card.source_facts) for card in state.ledger.candidates
    ) - sum(len(card["source_facts"]) for card in kept)
