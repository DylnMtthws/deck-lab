"""Regression evidence for the two live QA failures; synthetic supplied facts."""

import copy
import json
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_claim_review import (
    ClaimReviewDecision,
    evidence_catalog,
    review_claims,
)
from sabermetrics.assistant_functional_search import FunctionalSearchArgs
from sabermetrics.assistant_review_followthrough import narrow_unverified_findings
from sabermetrics.assistant_search_goal import (
    compile_search_goal,
    enforce_search_goal,
    produces_mana,
)
from sabermetrics.assistant_selection import selection_payload, validate_selection
from sabermetrics.assistant_tools import AssistantTools
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_selection import selection, setup

REQUEST = "Give me three additional mana-producing creatures for Kinnan, Bonder Prodigy. Prefer low casting cost. Explain what each contributes with Kinnan, its full activation cost, and its biggest drawback."
pytest_plugins = ["tests.test_assistant_experiments"]


def test_literal_request_repairs_planner_drift_without_invented_ceiling():
    goal = compile_search_goal(REQUEST)
    assert goal.function == "mana" and goal.card_type == "creature"
    before = FunctionalSearchArgs(function="any", mana_max=2).model_dump()
    after = enforce_search_goal(before, goal, REQUEST)
    assert after["function"] == "mana" and after["type_line"] == "creature"
    assert after["prefer_low_mana"] and after["mana_production_required"]
    assert after["mana_max"] is None
    assert before["mana_max"] == 2
    for request in [
        "Find 3 mana-producing creatures. Prefer low casting cost.",
        REQUEST,
    ]:
        assert (
            enforce_search_goal(before, compile_search_goal(request), request)[
                "mana_max"
            ]
            is None
        )


@pytest.mark.parametrize(
    "text",
    [
        "Find draw creatures and mana-producing artifacts.",
        "Don't recommend mana-producing creatures.",
        "What does Kinnan do?",
    ],
)
def test_ambiguous_negated_and_factual_requests_do_not_get_forced_contract(text):
    goal = compile_search_goal(text)
    assert goal.function is None and goal.card_type is None


def test_disjunction_and_exceptions_do_not_narrow_type():
    for text in [
        "Find mana-producing creatures or artifacts.",
        "Find ramp without creatures.",
        "Find ramp for creature-heavy decks.",
    ]:
        assert compile_search_goal(text).card_type is None


def test_soft_cost_projection_preserves_explicit_or_prior_hard_bound():
    args = FunctionalSearchArgs(mana_max=3).model_dump()
    for text, prefs in [
        (REQUEST + " At most 3 mana.", []),
        (
            REQUEST,
            [dict(dimension="mana", strength="required", value="mana value 3 or less")],
        ),
    ]:
        assert (
            enforce_search_goal(args, compile_search_goal(text), text, prefs)[
                "mana_max"
            ]
            == 3
        )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("{T}: Add {G}.", True),
        ("{T}: Add one mana of any color.", True),
        ("Whenever this creature attacks, add {R}.", True),
        ("Creature spells cost {1} less to cast.", False),
        ("Other creatures have '{T}: Add {G}.'", False),
        ("When this creature enters, add a +1/+1 counter to another creature.", False),
        ("Sacrifice this creature: Search your library for a land card.", False),
    ],
)
def test_printed_mana_production_is_not_cost_reduction_or_granted_ability(
    text, expected
):
    assert produces_mana(dict(oracle_text=text)) == expected


def test_tool_execution_enforces_role_type_soft_cost_and_receipts(prepared):
    service, path, owner, deck, _doc, _ = prepared
    insert(path, "producer", "Actual Producer", "Creature", "{T}: Add {C}.")
    insert(path, "wrong-type", "Mana Rock", "Artifact", "{T}: Add {C}.")
    insert(
        path,
        "reducer",
        "Creature Reducer",
        "Creature",
        "Creature spells cost {1} less to cast.",
    )
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: False, experiment_service=service
    )
    tools.research_request = REQUEST
    before = copy.deepcopy(tools.check())
    result = tools.execute(
        dict(
            name="search_card_functions",
            arguments=dict(query="low mana value cost", function="any", mana_max=1),
        )
    )
    assert [c["id"] for c in result["cards"]] == ["producer"]
    assert result["effective_search"]["function"] == "mana"
    assert result["effective_search"]["mana_max"] is None
    assert result["search_goal"]["mana_production_required"]
    assert tools.check() == before


def test_selection_excludes_restored_wrong_role_or_type_and_rechecks_manifest():
    requirements, cards, ledger = setup()
    requirements[0].request_quote = REQUEST
    sources = {c["id"]: c for c in cards}
    encoded, retained = selection_payload(
        REQUEST, requirements, ledger, sources, [], set(), []
    )
    assert not retained.candidates
    assert json.loads(encoded)["search_contracts"]["list_1"]["function"] == "mana"
    _, issues = validate_selection(selection(), requirements, ledger, sources)
    assert "search_contract_mismatch" in {i["code"] for i in issues}


def test_no_eligible_cards_accepts_an_explicit_shortfall_without_padding():
    from sabermetrics.assistant_selection import CandidateSelection

    requirements, _, ledger = setup()
    empty = CandidateSelection(
        groups=[dict(group_id="list_1", card_ids=[])],
        assessments=[],
        shortfalls=[
            dict(
                group_id="list_1",
                reason="None of the supplied records meets the requested function and type; additional evidence is needed.",
            )
        ],
    )
    _, issues = validate_selection(empty, requirements, ledger)
    assert not issues


SOURCE = "Card: Source Creature\nMana cost: {1}{U}\nType: Creature\nOracle text:\nWhen this creature dies, draw a card.\nPrinted characteristics: {}"
PART = "Requested engines: Source Creature ({1}{U}) — This returns with a counter and loops forever."


def test_unverified_assertion_is_replaced_by_literal_facts_not_blanket_warning():
    original = ["Lead", PART, ""]
    output, receipt = narrow_unverified_findings(
        original,
        original,
        [SOURCE],
        dict(
            status="unverified",
            claim_judgments=[
                dict(
                    claim_id="c0",
                    verdict="unverified",
                    reason="Counter renewal is missing.",
                )
            ],
        ),
    )
    assert (
        "loops forever" not in output[1] and "returns with a counter" not in output[1]
    )
    assert "When this creature dies, draw a card." in output[1]
    assert "remain unverified" in output[1] and "Source Creature ({1}{U})" in output[1]
    assert receipt["narrowed_claims"] and receipt["fulfillment"] == "partial"


def test_missing_review_is_narrowed_but_supported_and_protected_units_survive():
    original = ["Lead", PART, ""]
    supported = dict(claim_judgments=[dict(claim_id="c0", verdict="supported")])
    assert (
        narrow_unverified_findings(original, original, [SOURCE], supported)[0]
        == original
    )
    assert (
        narrow_unverified_findings(
            original, original, [SOURCE], {}, protected_parts={1}
        )[0]
        == original
    )
    assert narrow_unverified_findings(original, original, [SOURCE], {})[0] != original


def test_evidence_ids_never_use_card_or_rule_headers_as_proof():
    _, lookup = evidence_catalog(
        [SOURCE, "Rule source: CR fixture\nAn actual supplied rule sentence."]
    )
    assert all(
        not quote.startswith(("Card:", "Rule source:")) for _, quote in lookup.values()
    )
    assert any("When this creature dies" in quote for _, quote in lookup.values())


def test_rejected_correction_does_not_erase_another_valid_correction():
    class Reviewer:
        def generate(self, request):
            return SimpleNamespace(
                value=ClaimReviewDecision(
                    fulfillment="partial",
                    judgments=[
                        dict(
                            claim_id="c0",
                            category="printed_fact",
                            verdict="contradicted",
                            replacement="Draw a card.",
                            correction_span="Draw three cards.",
                            citations=[dict(source_index=0, quote="Draw a card.")],
                        ),
                        dict(
                            claim_id="c1",
                            category="printed_fact",
                            verdict="contradicted",
                            replacement="Draw nine cards.",
                            correction_span="Draw three cards.",
                            citations=[dict(source_index=0, quote="Draw a card.")],
                        ),
                    ],
                )
            )

    parts = ["Lead", "Draw three cards.", "Draw three cards.", ""]
    output, receipt = review_claims(
        Reviewer(), parts, ["Oracle text: Draw a card."], "Check facts"
    )
    assert output[1] == "Draw a card." and output[2] == "Draw three cards."
    assert receipt["claim_judgments"][0]["correction_applied"] is True
    assert receipt["claim_judgments"][1]["correction_applied"] is False


def test_disputed_counter_renewal_prioritizes_loaded_rule_definitions(tmp_path):
    from sabermetrics.assistant_rules import _ZONE_COUNTERS, _ZONE_OBJECT, ingest_rules
    from sabermetrics.assistant_targeted_evidence import finalist_evidence

    path = tmp_path / "rules.db"
    # Synthetic fixture uses the matching anchors as data, never a hard-coded
    # claim that these numbers establish semantics in an arbitrary edition.
    text = (
        "Synthetic rule fixture.\n101.1 A synthetic setup is not game evidence.\n"
        + f"122.2 {_ZONE_COUNTERS[1]} in this synthetic scenario.\n"
        + f"400.7 A synthetic thing {_ZONE_OBJECT[1]} its previous fixture state.\n"
    )
    ingest_rules(
        path,
        text,
        source_url="https://media.wizards.com/synthetic/rules.txt",
        version="synthetic",
    )
    cards = [
        dict(
            name="Death Card",
            oracle_text="When this creature dies, draw a card.",
            type_line="Creature",
        )
    ]
    support = [
        dict(
            name="Return Source",
            oracle_text="Whenever a creature dies, return that card to the battlefield.",
            type_line="Creature",
        )
    ]
    rules, receipt = finalist_evidence(
        path,
        cards,
        dict(hits=[]),
        support,
        claims=["This card returns with a counter and loops repeatedly."],
    )
    assert receipt["lookup_count"] == 2
    assert receipt["queries"][0]["query"].startswith("zone change")
    assert {"CR 122.2", "CR 400.7"} <= {h["source_id"] for h in rules["hits"]}


def test_opposing_death_trigger_cannot_get_own_sacrifice_support():
    from sabermetrics.assistant_compatibility import ability_compatibility

    candidate = dict(
        id="candidate",
        name="Candidate",
        type_line="Creature",
        oracle_text="Whenever a creature an opponent controls dies, draw a card.",
    )
    own_outlet = dict(
        id="outlet",
        name="Own outlet",
        type_line="Creature",
        oracle_text="Sacrifice a creature: Scry 1.",
    )
    result = ability_compatibility(candidate, [own_outlet])
    assert result["requirements"][0]["dimension"] == "opponent_death"
    assert not result["requirements"][0]["providers"]


def test_runtime_refuses_unrelated_candidates_without_paying_for_selection(
    prepared, monkeypatch
):
    from sabermetrics.assistant_research_task import DiscoveryListPlan
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_request_planner import plan
    from tests.test_assistant_runtime import Gateway

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    request = "Give me two additional mana-producing creatures."
    planned = plan(request, scope="candidate_discovery")
    planned.research_task.discovery_lists = [
        DiscoveryListPlan(
            label="mana creatures",
            query="low mana value",
            request_quote=request,
            requested_count=2,
            function="any",
        )
    ]
    gateway = Gateway([planned])
    result = run_turn(
        path,
        owner,
        deck,
        [dict(role="user", content=request)],
        gateway=gateway,
        experiment_service=service,
        selection_first=True,
        request_planner=True,
    )
    assert len(gateway.requests) == 1
    assert (
        result["status"] == "incomplete"
        and result["reason"] == "no_eligible_candidates"
    ), result
    assert not result.get("proposals")


def test_grounded_list_compiles_function_and_type_before_selection(
    prepared, monkeypatch
):
    from sabermetrics.assistant_research_task import DiscoveryListPlan
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_request_planner import plan
    from tests.test_assistant_runtime import Gateway

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    for i in range(2):
        insert(path, f"producer-{i}", f"Mana Source {i}", "Creature", "{T}: Add {C}.")
    request = REQUEST.replace("three", "two")
    planned = plan(request, scope="candidate_discovery")
    planned.research_task.discovery_lists = [
        DiscoveryListPlan(
            label="mana creatures",
            query="low mana value",
            request_quote=request,
            requested_count=2,
            function="any",
        )
    ]
    gateway = Gateway([planned, selection(("producer-0", "producer-1"))])
    result = run_turn(
        path,
        owner,
        deck,
        [dict(role="user", content=request)],
        gateway=gateway,
        experiment_service=service,
        selection_first=True,
        request_planner=True,
    )
    assert result["status"] == "done", result
    search = next(
        a
        for a in result["actions"]
        if a.get("type") == "tool_started" and a.get("name") == "search_card_functions"
    )
    assert (
        search["arguments"]["function"] == "mana"
        and search["arguments"]["type_line"] == "creature"
    )
    assert search["arguments"]["mana_production_required"]
    assert (
        search["arguments"]["prefer_low_mana"]
        and search["arguments"]["mana_max"] is None
    )
    assert len(gateway.requests) == 2


def test_context_mentions_do_not_become_requested_card_types():
    assert compile_search_goal("Find draw spells for creature decks.").card_type is None
    assert compile_search_goal("Find protection for my creatures.").card_type is None
    request = "Find mana-producing creatures and draw artifacts."
    goal = compile_search_goal("mana-producing creatures", request)
    assert goal.function == "mana" and goal.card_type == "creature"
    assert (
        compile_search_goal(
            "mana-producing creatures", "Tell me about draw spells."
        ).function
        is None
    )
