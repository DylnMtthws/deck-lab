from types import SimpleNamespace

import pytest

from sabermetrics.assistant_swap_review import review_candidates
from sabermetrics.functional_candidates import oracle_facts


class Gateway:
    def __init__(self, decisions=None, error=False):
        self.decisions = decisions
        self.error = error
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise RuntimeError("private provider detail")
        return SimpleNamespace(value={"decisions": self.decisions})


@pytest.fixture
def case():
    old = {
        "id": "old-entry",
        "oracle_id": "old",
        "name": "Slow Rock",
        "type_line": "Artifact",
        "mana_cost": "{3}",
        "cmc": 3,
        "color_identity": [],
        "oracle_text": "Indestructible\n{T}: Add one mana of any color.",
    }
    new = {
        "id": "new",
        "oracle_id": "new",
        "name": "Fast Rock",
        "type_line": "Artifact",
        "mana_cost": "{2}",
        "cmc": 2,
        "color_identity": [],
        "oracle_text": "{T}: Add one mana of any color.",
    }
    document = {
        "entries": [
            {**old, "quantity": 1},
            {
                "name": "Commander",
                "type_line": "Creature",
                "oracle_text": "Whenever you cast a noncreature spell, draw a card.",
                "is_commander": True,
            },
        ]
    }
    alternatives = [
        {
            "rationale": "Test cheaper same output",
            "replacements": [
                {"incoming_card_id": "new", "outgoing_entry_id": "old-entry"}
            ],
        }
    ]
    evidence = [
        {
            "incoming_oracle_facts": oracle_facts(new),
            "outgoing_oracle_facts": oracle_facts(old),
            "recommendation_type": "functional_improvement",
        }
    ]
    decision = {
        "candidate_index": 0,
        "decision": "accept",
        "reason": "Cheaper matching mana function supports the commander cast plan",
        "benefit": "Earlier mana deployment",
        "tradeoffs": ["Loses indestructible"],
        "citations": [
            {"side": "incoming", "quote": "{T}: Add one mana of any color."},
            {"side": "outgoing", "quote": "Indestructible"},
        ],
    }
    return document, alternatives, evidence, decision


def run(gateway, case):
    document, alternatives, evidence, _ = case
    return review_candidates(gateway, document, {}, "mana", alternatives, evidence)


@pytest.mark.parametrize("acknowledged", [False, True])
def test_review_cannot_hide_known_conditional_commander_mana_loss(case, acknowledged):
    import json

    document, _, evidence, decision = case
    document["entries"][1].update(
        name="Vivi Ornitier",
        oracle_text="{0}: Add X mana in any combination of {U} and/or {R}, where X is Vivi Ornitier's power. Activate only during your turn and only once each turn.",
    )
    evidence[0]["outgoing_oracle_facts"].update(
        name="Blazing Crescendo",
        oracle_text="Target creature gets +3/+1 until end of turn.",
    )
    evidence[0]["incoming_oracle_facts"].update(
        oracle_text="You and permanents you control gain hexproof until end of turn."
    )
    decision["citations"] = [
        {"side": "outgoing", "quote": "Target creature gets +3/+1 until end of turn."},
        {
            "side": "incoming",
            "quote": "You and permanents you control gain hexproof until end of turn.",
        },
    ]
    decision["resource_tradeoffs_acknowledged"] = acknowledged
    decision["tradeoffs"] = [
        "Loses +3 temporary power and its conditional 3-mana opportunity from Vivi"
    ]
    gateway = Gateway([decision])
    result = run(gateway, case)
    assert bool(result["alternatives"]) is acknowledged
    fact = json.loads(gateway.requests[0].user)["candidates"][0]["context"][
        "commander_resource_tradeoffs"
    ][0]
    assert fact["conditional_mana_opportunity_lost"] == 3
    if acknowledged:
        from sabermetrics.assistant_runtime import _retain_swap_context

        state = {}
        _retain_swap_context(state, result)
        assert (
            state["swap_review"]["candidates"][0]["commander_resource_tradeoffs"][0][
                "conditional_mana_opportunity_lost"
            ]
            == 3
        )


def test_acceptance_is_bounded_and_has_verified_receipt(case):
    gateway = Gateway([case[3]])
    result = run(gateway, case)
    assert result["alternatives"] == case[1]
    assert (
        result["candidate_evidence"][0]["semantic_verification"] == "reviewed_fit_only"
    )
    assert result["review_receipts"][0]["citations_verified"]
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert len(request.user.encode()) <= 24576
    assert request.mode.value == "explain"
    assert request.evidence_hash == result["review_receipts"][0]["evidence_hash"]


@pytest.mark.parametrize(
    "failure",
    [
        "invented_quote",
        "one_side",
        "omitted",
        "duplicate",
        "failure",
        "truncated",
        "missing",
        "bad_source",
        "mismatched_pair",
    ],
)
def test_review_fails_closed(case, failure):
    decision = case[3]
    decisions = [decision]
    if failure == "invented_quote":
        decision["citations"][0]["quote"] = "Draw three cards."
    elif failure == "one_side":
        decision["citations"] = decision["citations"][:1]
    elif failure == "omitted":
        decisions = []
    elif failure == "duplicate":
        decisions = [decision, decision]
    elif failure == "truncated":
        case[2][0]["incoming_oracle_facts"]["oracle_text_truncated"] = True
    elif failure == "missing":
        del case[2][0]["outgoing_oracle_facts"]["oracle_text"]
    elif failure == "bad_source":
        case[2][0]["incoming_oracle_facts"]["citation"] = "https://example.test/claim"
    elif failure == "mismatched_pair":
        case[2][0]["incoming_oracle_facts"]["id"] = "another-card"
    result = run(Gateway(decisions, error=failure == "failure"), case)
    assert result["alternatives"] == []
    assert result["candidate_evidence"] == []
    assert "private provider detail" not in result["reason"]


@pytest.mark.parametrize("decision", ["reject", "uncertain"])
def test_nonacceptance_never_becomes_a_recommendation(case, decision):
    case[3]["decision"] = decision
    result = run(Gateway([case[3]]), case)
    assert result["status"] == "reviewed"
    assert result["alternatives"] == []
    assert result["review_receipts"][0]["decision"] == decision


@pytest.mark.parametrize(
    "preference,field", [("theme", "theme_fit"), ("power_level", "power_fit")]
)
def test_supplied_preferences_need_explicit_fit(case, preference, field):
    document, alternatives, evidence, decision = case
    constraints = {preference: "requested preference"}
    result = review_candidates(
        Gateway([decision]), document, constraints, "mana", alternatives, evidence
    )
    assert result["alternatives"] == []
    assert result["review_receipts"][0]["decision"] == "uncertain"
    decision[field] = "fit"
    result = review_candidates(
        Gateway([decision]), document, constraints, "mana", alternatives, evidence
    )
    assert result["alternatives"] == alternatives


def test_blank_oracle_basic_land_can_cite_printed_type(case):
    evidence = case[2][0]
    evidence["outgoing_oracle_facts"].update(
        type_line="Basic Land — Island", oracle_text=""
    )
    case[3]["citations"][1] = {
        "side": "outgoing",
        "source_field": "type_line",
        "quote": "Basic Land — Island",
    }
    result = run(Gateway([case[3]]), case)
    assert result["alternatives"] == case[1]


def test_preview_incoming_entry_uses_card_id(case):
    case[2][0]["incoming_oracle_facts"].update(id="preview-entry-id", card_id="new")
    assert run(Gateway([case[3]]), case)["alternatives"] == case[1]


def test_empty_power_preference_is_not_a_constraint(case):
    assert (
        review_candidates(
            Gateway([case[3]]), case[0], {"power_level": ""}, "mana", case[1], case[2]
        )["alternatives"]
        == case[1]
    )


def test_review_trims_pairs_before_reducing_deck_context(case):
    import copy
    import json

    document, alternatives, evidence, decision = case
    evidence[0]["incoming_oracle_facts"]["oracle_text"] += (
        " Additional relevant Oracle facts." * 90
    )
    alternatives = [copy.deepcopy(alternatives[0]) for _ in range(8)]
    evidence = [copy.deepcopy(evidence[0]) for _ in range(8)]

    class BudgetGateway(Gateway):
        def generate(self, request):
            payload = json.loads(request.user)
            assert len(request.user.encode()) <= 24576
            assert len(payload["candidates"]) < 8
            assert payload["deck"][0]["oracle_context_excerpt"]
            self.decisions = [
                {**decision, "candidate_index": index}
                for index in range(len(payload["candidates"]))
            ]
            return super().generate(request)

    result = review_candidates(
        BudgetGateway(), document, {}, "mana", alternatives, evidence
    )
    assert result["status"] == "reviewed"
    assert result["omitted_count"] > 0
    assert len(result["alternatives"]) + result["omitted_count"] == 8


@pytest.mark.parametrize(
    "deck_name",
    ["high-power-vivi", "high-power-sauron", "affordable-vivi", "affordable-sauron"],
)
def test_full_reference_deck_fits_review_budget(case, deck_name):
    import json
    from pathlib import Path

    document = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "swap-quality"
            / f"{deck_name}.json"
        ).read_text()
    )
    gateway = Gateway([case[3]])
    result = review_candidates(gateway, document, {}, "consistency", case[1], case[2])
    assert result["alternatives"] == case[1]
    payload = json.loads(gateway.requests[0].user)
    assert len(payload["deck"]) == len(document["entries"])
    assert len(gateway.requests[0].user.encode()) <= 24576
    assert payload["role_counts"]
    commander = next(c for c in payload["deck"] if c.get("is_commander"))
    assert commander["oracle_context_excerpt"]
    if "vivi" in deck_name:
        assert (
            commander["printed_cast_trigger_damage"]["printed_damage_per_opponent"] == 1
        )


@pytest.mark.parametrize(
    "quote,field", [("a", "oracle_text"), ("Artifact", "type_line")]
)
def test_trivial_or_type_only_quotes_cannot_approve_swap(case, quote, field):
    case[3]["citations"][0].update(quote=quote, source_field=field)
    result = run(Gateway([case[3]]), case)
    assert not result["alternatives"]
    assert result["review_receipts"][0]["decision"] == "uncertain"
