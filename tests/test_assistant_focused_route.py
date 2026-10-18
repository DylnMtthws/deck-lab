"""Focused routes skip planning only with exact supplied evidence."""

import pytest

from sabermetrics.assistant_focused_route import focused_route

CARD = {
    "id": "printed-id",
    "oracle_id": "oracle-id",
    "name": "Grave Pact",
    "mana_cost": "{1}{B}{B}{B}",
    "cmc": 4,
    "type_line": "Enchantment",
    "oracle_text": "Supplied authoritative text.",
}


@pytest.mark.parametrize(
    "question,field",
    [
        ("What does Grave Pact do?", "oracle_text"),
        ("Please show the Oracle text for grave pact.", "oracle_text"),
        ("What is the mana cost of Grave Pact?", "mana_cost"),
        ("How much mana does Grave Pact cost?", "mana_cost"),
        ("What's the cmc of Grave Pact?", "mana_value"),
        ("What is the type line of Grave Pact?", "type_line"),
        ("Tell me about Grave Pact.", "card_facts"),
    ],
)
def test_exact_printed_fact_question_routes_without_name_invention(question, field):
    plan = focused_route(question, [CARD])
    assert plan.route == "named_card_facts"
    assert plan.named_cards == ["Grave Pact"]
    assert plan.fact_field == field


@pytest.mark.parametrize(
    "question",
    [
        "Find cards like Grave Pact.",
        "List seven cards besides Grave Pact.",
        "What does Grave Pact do, and recommend a replacement?",
        "What does Grave Pact do? Run a simulation.",
        "Does Grave Pact improve our measured turn four win rate?",
        "What does Grave Pact do with a replacement effect?",
        "How does Grave Pact interact with persist?",
        "Does Grave Pact combo with another card?",
        "Is Grave Pact legal in Commander?",
        "Cut Grave Pact.",
        "Compare Grave Pact and Dictate of Erebos.",
        "Summarize our findings and suggest new cards.",
        "What does Pact do?",
    ],
)
def test_discovery_action_empirical_complex_or_ambiguous_requests_keep_planner(
    question,
):
    assert focused_route(question, [CARD]).route == "fallback"


def test_missing_fact_or_disagreeing_authoritative_records_keep_planner():
    assert (
        focused_route("What does Grave Pact do?", [{"name": "Grave Pact"}]).route
        == "fallback"
    )
    assert (
        focused_route(
            "What does Grave Pact do?", [CARD, {**CARD, "oracle_text": "Different"}]
        ).route
        == "fallback"
    )
    assert (
        focused_route("What does Grave Pact do?", [CARD, dict(CARD)]).route
        == "named_card_facts"
    )
    assert focused_route("What does Made Up Card do?", [CARD]).named_cards == []


def test_summary_requires_retained_structured_provenance():
    finding = {
        "kind": "limitation",
        "claim": "No simulation result was collected.",
        "evidence": [{"reference": "receipt:1", "quote": "Not simulated"}],
    }
    assert (
        focused_route(
            "Summarize what we've learned so far.", [], session_findings=[finding]
        ).route
        == "session_summary"
    )
    assert (
        focused_route(
            "Recap our research.", [], session_findings=[finding]
        ).retained_findings_count
        == 1
    )
    assert focused_route("Summarize our findings.", []).route == "fallback"
    assert (
        focused_route(
            "Summarize our findings.",
            [],
            session_findings=[{"claim": "Unattributed prose"}],
        ).route
        == "fallback"
    )


def test_deck_mana_value_and_catalog_cmc_are_equivalent_authoritative_facts():
    deck_card = {key: value for key, value in CARD.items() if key != "cmc"}
    deck_card["mana_value"] = 4.0
    assert (
        focused_route("What does Grave Pact do?", [CARD, deck_card]).route
        == "named_card_facts"
    )


def test_bad_numeric_metadata_falls_back_instead_of_crashing():
    assert (
        focused_route("What's the cmc of Grave Pact?", [{**CARD, "cmc": "X"}]).route
        == "fallback"
    )


def test_empty_duplicate_fields_do_not_hide_actual_oracle_text():
    partial = {**CARD, "oracle_text": ""}
    assert (
        focused_route("What does Grave Pact do?", [partial, CARD]).route
        == "named_card_facts"
    )
