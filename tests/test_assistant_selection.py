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
    CandidateSelectionDraft,
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
    cards[2]["oracle_text"] = "{T}: Draw a card.\n" + "Very long Oracle record. " * 2000
    payload, retained = selection_payload(
        "Two cards", requirements, ledger, {c["id"]: c for c in cards}, [], {"c0"}, []
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == ["c1"]
    assert data["omitted_candidate_ids"] == ["c2"]
    assert "Very long Oracle" not in payload
    assert not any(c["card_id"] == "c0" for c in data["candidates"])


def test_selection_receives_literal_commander_and_support_records():
    from sabermetrics.assistant_suitability import (
        candidate_suitability,
        deck_support_index,
    )

    requirements, cards, ledger = setup()
    entries = [
        {
            "name": "Commander",
            "is_commander": True,
            "oracle_text": "Whenever a creature you control dies, return that card to the battlefield.",
        },
        {
            "name": "Fodder",
            "zone_name": "Library",
            "type_line": "Creature",
            "oracle_text": "Create a 1/1 Soldier creature token.",
        },
    ]
    cards[0]["oracle_text"] = "Sacrifice another creature: Draw a card."
    cards[0]["suitability"] = candidate_suitability(
        cards[0], deck_support_index({"entries": entries})
    )
    payload, _ = selection_payload(
        "Find draw",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
        deck_entries=entries,
    )
    supplied = {
        c["name"]: c["oracle_text"] for c in json.loads(payload)["deck_sources"]
    }
    assert supplied == {c["name"]: c["oracle_text"] for c in entries}


def test_ranking_new_choices_does_not_override_explicit_no_repeats():
    from sabermetrics.assistant_discovery_contract import revises_discovered_candidates

    assert not revises_discovered_candidates(
        "Give me seven more creatures. Rank them best fit first and do not repeat earlier recommendations."
    )
    assert revises_discovered_candidates("Re-rank those previous recommendations.")


def test_redundant_shortfall_is_not_rendered_when_count_is_met():
    requirements, _, ledger = setup()
    selected = selection(shortfall=True)
    answer, issues = validate_selection(selected, requirements, ledger)
    assert not issues
    assert selection_reply(selected, answer, requirements)["next_action"] == ""


def test_sentence_checks_remain_local_and_reject_incomplete_output():
    from pydantic import ValidationError

    from sabermetrics.assistant_selection import Assessment

    schema = Assessment.model_json_schema()["properties"]["fit"]
    assert "pattern" not in schema
    assert Assessment(
        card_id="c0",
        fit="A potential draw engine.",
        conditions_and_tradeoff="Tap costs need setup.",
    )
    for invalid in [".", "........", "Plausible but incomplete"]:
        with pytest.raises(ValidationError):
            Assessment(
                card_id="c0",
                fit=invalid,
                conditions_and_tradeoff="Tap costs need setup.",
            )


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
    assert [
        "\n".join(line["text"] for line in record["lines"])
        for record in payload["source_records"]
    ] == sources
    assert payload["claims"][0]["source_indices"] == [0]


def test_focused_evidence_keeps_oracle_and_named_deck_rows_without_global_certification():
    from sabermetrics.assistant_claim_review import focused_sources

    parts = ["Lead", "Candidate 0 draws a card.", ""]
    oracle = "Card: Candidate 0\nOracle text: {T}: Draw a card."
    inventory = "Current deck printed mana costs (not alternate or effective costs):\nCandidate 0: {1}{B}\nUnmentioned: {0}"
    sources = focused_sources(parts, [oracle, inventory])
    assert sources[0] == oracle
    assert "Candidate 0: {1}{B}" in sources[1]
    assert "Unmentioned: {0}" not in sources[1]
    assert sources.omitted
    gateway = Reviewer()
    _, receipt = review_claims(gateway, parts, [oracle, inventory], "Find draw")
    assert receipt["status"] == "unverified"
    assert receipt["omitted_sources"] == 1


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


@pytest.mark.parametrize("invalid", [False, True])
def test_compact_evidence_ids_resolve_only_to_exact_supplied_quotes(invalid):
    class CompactReviewer:
        def generate(self, request):
            payload = json.loads(request.user)
            eid = next(
                line["id"]
                for record in payload["source_records"]
                for line in record["lines"]
                if "{T}: Draw a card." in line["text"]
            )
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "complete",
                        "c0": {
                            "k": "printed",
                            "v": "supported",
                            "e": ["invented"] if invalid else [eid],
                            "fix": None,
                            "why": "",
                        },
                    }
                )
            )

    parts = ["Lead", "Candidate 0 draws a card.", ""]
    source = "Card: Candidate 0\nOracle text: {T}: Draw a card."
    corrected, receipt = review_claims(CompactReviewer(), parts, [source], "Find draw")
    assert corrected == parts
    if invalid:
        assert receipt["status"] == "unverified"
    else:
        assert receipt["status"] == "no_supported_correction"
        assert receipt["claim_judgments"][0]["citations"] == [
            {"source_index": 0, "quote": "Oracle text: {T}: Draw a card."}
        ]


def test_compact_correction_changes_only_the_cited_claim_span():
    class CompactReviewer:
        def generate(self, request):
            data = json.loads(request.user)
            eid = next(
                line["id"]
                for source in data["source_records"]
                for line in source["lines"]
                if "Draw a card." in line["text"]
            )
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "complete",
                        "c0": {
                            "k": "printed",
                            "v": "contradicted",
                            "e": [eid],
                            "fix": {"span": "three cards", "text": "a card"},
                            "why": "Printed draw amount.",
                        },
                    }
                )
            )

    parts = ["Lead", "Candidate 0 draws three cards.", ""]
    corrected, receipt = review_claims(
        CompactReviewer(),
        parts,
        ["Card: Candidate 0\nOracle text: {T}: Draw a card."],
        "Find draw",
    )
    assert corrected == ["Lead", "Candidate 0 draws a card.", ""]
    assert receipt["status"] == "corrected"
    assert receipt["patches"][0]["claim"] == "three cards"


def test_printed_correction_preserves_unverified_remainder():
    class MixedReviewer:
        def generate(self, request):
            data = json.loads(request.user)
            eid = next(
                line["id"]
                for source in data["source_records"]
                for line in source["lines"]
                if "Draw a card." in line["text"]
            )
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "partial",
                        "c0": {
                            "k": "printed",
                            "v": "contradicted",
                            "e": [eid],
                            "fix": {"span": "three cards", "text": "a card"},
                            "why": "Loop remains unsupported.",
                            "u": True,
                        },
                    }
                )
            )

    parts = ["Lead", "Draw three cards; the loop repeats indefinitely.", ""]
    corrected, receipt = review_claims(
        MixedReviewer(), parts, ["Oracle text: {T}: Draw a card."], "Check this claim"
    )
    assert corrected[1] == "Draw a card; the loop repeats indefinitely."
    assert receipt["patches"]
    assert receipt["status"] == "unverified"
    assert receipt["claim_judgments"][0]["unverified_remainder"] is True


def test_one_bad_evidence_id_does_not_discard_an_independent_valid_correction():
    class ReviewerWithBadID:
        def generate(self, request):
            data = json.loads(request.user)
            eid = next(
                line["id"]
                for record in data["source_records"]
                for line in record["lines"]
                if "Draw a card." in line["text"]
            )
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "partial",
                        "c0": {
                            "k": "printed",
                            "v": "contradicted",
                            "e": [eid],
                            "fix": {"span": "three cards", "text": "a card"},
                            "why": "Wrong printed amount.",
                        },
                        "c1": {
                            "k": "printed",
                            "v": "supported",
                            "e": ["invented"],
                            "fix": None,
                            "why": "",
                        },
                    }
                )
            )

    corrected, receipt = review_claims(
        ReviewerWithBadID(),
        ["Lead", "Draw three cards.", "Another supplied claim.", ""],
        ["Oracle text: {T}: Draw a card."],
        "Check these claims",
    )
    assert corrected[1] == "Draw a card."
    assert receipt["status"] == "unverified"
    assert len(receipt["patches"]) == 1
    assert receipt["claim_judgments"][1]["invalid_evidence_ids"] == ["invented"]


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
    assert gateway.requests[1].schema is CandidateSelectionDraft
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
        CandidateSelectionDraft,
        CandidateSelectionDraft,
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
    schema = reviewer.requests[0].schema.model_json_schema()
    assert {"f", "c0", "c1"} <= set(schema["required"])
    assert result["answer_review"]["status"] == "unverified"
    # Each candidate contains two independent sentence fragments. Both remain
    # reviewed in the single batch, with exact paragraph/span bindings.
    assert result["answer_review"]["claim_count"] == 4


def test_comparing_additional_candidates_still_excludes_prior_picks(
    prepared, monkeypatch
):
    from sabermetrics.assistant_runtime import AssistantStep, run_turn
    from tests.test_assistant_runtime import Gateway, call

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    prior_manifest = {"groups": [{"group_id": "list_1", "card_ids": ["incoming-0"]}]}
    prior_ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(
                {"id": "incoming-0", "name": "Incoming 0", "type_line": "Artifact"}
            )
        ]
    )
    request = "Give me two additional cards and compare their fit."
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {
                        "kind": "compare",
                        "evidence_scope": "candidate_discovery",
                        "current_request_quote": request,
                    },
                    "tool_calls": [call("search_cards", {"query": "Incoming"})],
                }
            ),
            selection(("incoming-1", "incoming-2")).model_dump(),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            {"role": "user", "content": "One candidate."},
            {
                "role": "assistant",
                "research": {
                    "selected_candidates": prior_manifest,
                    "discovery_ledger": prior_ledger.model_dump(),
                },
            },
            {"role": "user", "content": request},
        ],
        gateway=gateway,
        experiment_service=service,
        selection_first=True,
    )
    assert result["status"] == "done", [
        (r.schema.__name__, json.loads(r.user).get("repair")) for r in gateway.requests
    ]
    assert "incoming-0" not in {
        c["card_id"] for c in json.loads(gateway.requests[-1].user)["candidates"]
    }
