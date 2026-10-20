import json
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_claim_review import ClaimReviewDecision, review_claims
from sabermetrics.assistant_discovery_contract import (
    DiscoveryLedger,
    DiscoveryRequirement,
    candidate_from_card,
)
from sabermetrics.assistant_selection import (
    CandidateSelection,
    selection_payload,
    selection_reply,
    validate_selection,
)


def setup(count=2):
    requirements = [
        DiscoveryRequirement(
            group_id="list_1",
            label="draw candidates",
            requested_count=count,
            request_quote="Give two draw candidates",
        )
    ]
    cards = [
        {
            "id": f"c{i}",
            "name": f"Candidate {i}",
            "mana_cost": "{1}{B}",
            "type_line": "Creature",
            "oracle_text": "{T}: Draw a card.",
        }
        for i in range(3)
    ]
    ledger = DiscoveryLedger(
        candidates=[candidate_from_card(c, group_ids=["list_1"]) for c in cards]
    )
    return requirements, cards, ledger


def selection(ids=("c0", "c1"), shortfall=False):
    return CandidateSelection.model_validate(
        {
            "groups": [{"group_id": "list_1", "card_ids": list(ids)}],
            "assessments": [
                {
                    "card_id": cid,
                    "fit": "A potential draw engine.",
                    "conditions_and_tradeoff": "Tap cost needs continuous control or haste.",
                }
                for cid in dict.fromkeys(ids)
            ],
            "shortfalls": (
                [
                    {
                        "group_id": "list_1",
                        "reason": "Only these candidates have sufficient support.",
                    }
                ]
                if shortfall
                else []
            ),
        }
    )


@pytest.mark.parametrize(
    "ids,code",
    [
        (("c0", "c0"), "duplicate_card"),
        (("c0", "invented"), "unknown_card"),
        (("c0",), "requested_count_mismatch"),
    ],
)
def test_selection_rejects_identity_and_count_errors_before_render(ids, code):
    requirements, _, ledger = setup()
    _, issues = validate_selection(selection(ids), requirements, ledger)
    assert code in {i["code"] for i in issues}


def test_selection_accepts_honest_shortfall_but_not_excess():
    requirements, _, ledger = setup()
    answer, issues = validate_selection(selection(("c0",), True), requirements, ledger)
    assert not issues
    assert (
        "Shortfall:" in selection_reply(selection(("c0",), True), answer)["next_action"]
    )
    _, issues = validate_selection(
        selection(("c0", "c1", "c2"), True), requirements, ledger
    )
    assert any(i["code"] == "requested_count_mismatch" for i in issues)


def test_selection_payload_excludes_novelty_and_whole_record_omissions():
    requirements, cards, ledger = setup()
    cards[2]["oracle_text"] = "Very long Oracle record. " * 2000
    payload, retained = selection_payload(
        "Two cards", requirements, ledger, {c["id"]: c for c in cards}, [], {"c0"}, []
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == ["c1"]
    assert data["omitted_candidate_ids"] == ["c2"]
    assert "Very long Oracle" not in payload
    assert not any(c["card_id"] == "c0" for c in data["candidates"])


class Reviewer:
    def __init__(self, transform=None):
        self.requests = []
        self.transform = transform

    def generate(self, request):
        self.requests.append(request)
        payload = json.loads(request.user)
        decisions = [
            {
                "claim_id": c["claim_id"],
                "category": "printed_fact",
                "verdict": "supported",
                "reason": "The supplied Oracle text establishes this ability.",
                "replacement": None,
                "citations": [{"source_index": 0, "quote": "{T}: Draw a card."}],
            }
            for c in payload["claims"]
        ]
        if self.transform:
            self.transform(decisions)
        return SimpleNamespace(
            value=ClaimReviewDecision(fulfillment="complete", judgments=decisions)
        )


def test_claim_review_records_every_claim_and_preserves_exact_evidence():
    gateway = Reviewer()
    parts = ["Application lead.", "Candidate 0 draws a card.", ""]
    sources = ["Card: Candidate 0\nOracle text: {T}: Draw a card."]
    corrected, receipt = review_claims(gateway, parts, sources, "Find draw")
    assert corrected == parts
    assert receipt["status"] == "no_supported_correction"
    assert receipt["claim_count"] == 1
    payload = json.loads(gateway.requests[0].user)
    assert "reply_parts" not in payload
    assert payload["sources"] == sources
    assert payload["claims"][0]["source_indices"] == [0]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.clear(),
        lambda d: d[0].update(citations=[]),
        lambda d: d[0].update(category="rules_interaction"),
    ],
)
def test_missing_judgments_citations_or_rules_stay_unverified(change):
    _, receipt = review_claims(
        Reviewer(change),
        ["Lead", "Candidate 0 repeats indefinitely.", ""],
        ["Card: Candidate 0\nOracle text: {T}: Draw a card."],
        "Find draw",
    )
    assert receipt["status"] == "unverified"
    assert receipt["unresolved"]


def test_invalid_replacement_quote_cannot_change_the_answer():
    gateway = Reviewer(
        lambda d: d[0].update(
            verdict="contradicted",
            replacement="Draw two cards.",
            citations=[{"source_index": 0, "quote": "Invented quote not in source"}],
        )
    )
    parts = ["Lead", "Draw three cards.", ""]
    corrected, receipt = review_claims(
        gateway,
        parts,
        ["Card: Candidate 0\nOracle text: {T}: Draw a card."],
        "Find draw",
    )
    assert corrected == parts
    assert receipt["status"] == "unverified"


def test_runtime_selection_does_not_generate_prose_or_retry_whole_answer(
    prepared, monkeypatch
):
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_runtime import Gateway, call, step

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    raw = selection(("incoming-0", "incoming-1")).model_dump()
    gateway = Gateway([step(call("search_cards", {"query": "Incoming"})), raw])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two additional cards."}],
        gateway=gateway,
        experiment_service=service,
        selection_first=True,
    )
    assert result["status"] == "done", result
    assert len(gateway.requests) == 2
    assert gateway.requests[1].schema is CandidateSelection
    assert "Incoming" in result["content"]
    assert result["research"]["selection_validation"]["complete"]
    assert result["research"]["runtime"]["model_calls"][1]["route"] == "selection"


pytest_plugins = ["tests.test_assistant_experiments"]


def test_invalid_selection_repairs_only_selection_and_never_renders_bad_ids(
    prepared, monkeypatch
):
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_runtime import Gateway, call, step

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    gateway = Gateway(
        [
            step(call("search_cards", {"query": "Incoming"})),
            selection(("incoming-0", "invented")).model_dump(),
            selection(("incoming-0", "incoming-1")).model_dump(),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two additional cards."}],
        gateway=gateway,
        experiment_service=service,
        selection_first=True,
    )
    assert result["status"] == "done"
    assert [r.schema for r in gateway.requests[1:]] == [
        CandidateSelection,
        CandidateSelection,
    ]
    assert "unknown_card" in gateway.requests[2].user
    assert "invented" not in result["content"]
    assert result["research"]["selection_validation"]["repair_attempted"]


def test_focused_review_can_apply_an_exact_cited_correction():
    gateway = Reviewer(
        lambda d: d[0].update(verdict="contradicted", replacement="Draw a card.")
    )
    parts = ["Lead", "Draw three cards.", ""]
    corrected, receipt = review_claims(
        gateway, parts, ["Oracle text: {T}: Draw a card."], "Find draw"
    )
    assert corrected[1] == "Draw a card."
    assert receipt["status"] == "corrected"
    assert len(receipt["patches"]) == 1


def test_focused_review_cannot_patch_protected_measurements():
    gateway = Reviewer(
        lambda d: d[0].update(verdict="contradicted", replacement="Draw a card.")
    )
    parts = ["Lead", "Draw three cards.", ""]
    corrected, receipt = review_claims(
        gateway,
        parts,
        ["Oracle text: {T}: Draw a card."],
        "Find draw",
        protected_parts={1},
    )
    assert corrected == parts
    assert receipt["status"] == "unverified"


def test_selection_runtime_uses_focused_review_once(prepared, monkeypatch):
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_runtime import Gateway, call, step

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    gateway = Gateway(
        [
            step(call("search_cards", {"query": "Incoming"})),
            selection(("incoming-0", "incoming-1")).model_dump(),
        ]
    )
    reviewer = Reviewer(
        lambda d: [item.update(verdict="unverified", citations=[]) for item in d]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two additional cards."}],
        gateway=gateway,
        answer_review_gateway=reviewer,
        experiment_service=service,
        selection_first=True,
    )
    assert len(reviewer.requests) == 1
    assert reviewer.requests[0].schema is ClaimReviewDecision
    assert result["answer_review"]["status"] == "unverified"
    assert result["answer_review"]["claim_count"] == 2
