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
    attach_selection_evidence,
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
    payload = json.loads(gateway.requests[1].user)
    assert "shared_strategic_evidence" not in payload
    stage = next(
        s
        for s in result["research"]["runtime"]["stages"]
        if s["stage"] == "selection_evidence"
    )
    assert stage["shared_strategic_evidence_included"] is False
    assert stage["bounded_conditional_mana_source_count"] == 0
    assert stage["scope_source_count"] == 0
    assert stage["omitted_shared_evidence"] == payload.get(
        "omitted_shared_evidence", []
    )


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


def test_runtime_publishes_bound_review_cards_before_global_completion(
    prepared, monkeypatch
):
    from sabermetrics import db
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_runtime import Gateway, call, step

    service, path, owner, deck, _, _ = prepared
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET oracle_text='{T}: Draw a card.', mana_cost='{1}', type_line='Creature' "
            "WHERE id IN ('incoming-0','incoming-1')"
        )
        conn.commit()
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    publications = []
    from sabermetrics import assistant_targeted_evidence

    targeted_claims = []
    original_finalist = assistant_targeted_evidence.finalist_evidence

    def capture_finalist(*args, **kwargs):
        targeted_claims.extend(kwargs.get("claims", []))
        return original_finalist(*args, **kwargs)

    monkeypatch.setattr(
        assistant_targeted_evidence, "finalist_evidence", capture_finalist
    )

    class BoundReviewer:
        def __init__(self):
            self.calls = []

        def generate(self, request):
            payload = json.loads(request.user)
            self.calls.append(payload)
            judgments = []
            for claim in payload["claims"]:
                parent = payload["parent_parts"][str(claim["part_index"])]
                source = next(
                    row
                    for row in payload["source_records"]
                    if any(
                        name in parent
                        and any(name in line["text"] for line in row["lines"])
                        for name in ("Incoming 0", "Incoming 1")
                    )
                )
                judgments.append(
                    {
                        "claim_id": claim["claim_id"],
                        "category": "printed_fact",
                        "verdict": "supported",
                        "reason": "Supplied evidence.",
                        "citations": [
                            {
                                "source_index": source["source_index"],
                                "quote": "{T}: Draw a card.",
                            }
                        ],
                    }
                )
            return SimpleNamespace(
                value={"fulfillment": "complete", "judgments": judgments}
            )

    reviewer = BoundReviewer()
    local_selection = selection(("incoming-0", "incoming-1"))
    for assessment in local_selection.assessments:
        assessment.fit = "Activation cost: {T}."

    def observe(event):
        if event.get("name") == "research_card_update":
            for record in event["data"]["cards"]:
                if record["status"] == "reviewed":
                    publications.append((len(reviewer.calls), record))

    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two additional cards."}],
        gateway=Gateway(
            [
                step(call("search_cards", {"query": "Incoming"})),
                local_selection.model_dump(),
            ]
        ),
        answer_review_gateway=reviewer,
        experiment_service=service,
        selection_first=True,
        progressive_reviews=True,
        on_event=observe,
    )
    assert result["answer_review"]["progressive_review"]["enabled"]
    assert targeted_claims
    assert not any("Application note:" in claim for claim in targeted_claims)
    assert any("Activation cost:" in claim for claim in targeted_claims)
    assert len(reviewer.calls) == 1
    assert {record["card_id"] for count, record in publications if count == 0} == {
        "incoming-0",
        "incoming-1",
    }
    assert all(
        claim["text"] == "Activation cost: {T}."
        for count, record in publications
        if count == 0
        for claim in record["claims"]
    )
    assert all(
        claim["claim"] == "Tap cost needs continuous control or haste."
        for claim in reviewer.calls[-1]["claims"]
    )
    for _, record in publications:
        assert (
            record["source_sha256"]
            == result["answer_review"]["workspace_card_sources"][record["card_id"]]
        )
        assert record["claims"]
        assert all(claim["evidence"] for claim in record["claims"])


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


@pytest.mark.parametrize("reverse", [False, True])
def test_payload_reserves_printed_records_before_redundant_enrichments(reverse):
    requirements, cards, _ = setup(count=3)
    requirements[0] = requirements[0].model_copy(
        update={"request_quote": "Compare candidates"}
    )
    for card in cards:
        card["retrieval_views"] = {"repeated_metadata": "x" * 30000}
        card["card_faces"] = [
            {"type_line": "Creature", "oracle_text": "{T}: Draw a card."}
        ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(c, group_ids=["list_1"], rank=i + 1)
            for i, c in enumerate(cards)
        ]
    )
    if reverse:
        ledger.candidates.reverse()
    payload, retained = selection_payload(
        "Three draw candidates",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == ["c0", "c1", "c2"]
    assert len(payload.encode()) <= 24000
    assert not data["omitted_candidate_ids"]
    for record, card in zip(data["candidates"], cards):
        assert record["printed"]["oracle_text"] == card["oracle_text"]
        assert record["printed"]["card_faces"] == card["card_faces"]
        assert "retrieval_views" in record["context_omitted"]
        assert "retrieval_views" not in record


def test_payload_reserves_each_group_before_spending_on_alternatives():
    requirements = [
        DiscoveryRequirement(
            group_id=gid,
            label="Draw",
            requested_count=n,
            request_quote="Draw candidates",
        )
        for gid, n in [("a", 2), ("b", 1)]
    ]
    cards = [
        {
            "id": cid,
            "name": cid,
            "type_line": "Creature",
            "oracle_text": "{T}: Draw a card. " + "x" * size,
        }
        for cid, size in [("a0", 9000), ("a1", 9000), ("b0", 6000)]
    ]
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(c, group_ids=[c["id"][0]], rank=i + 1)
            for i, c in enumerate(cards)
        ]
    )
    payload, retained = selection_payload(
        "Draw candidates",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == ["a0", "b0"]
    assert data["omitted_candidate_ids"] == ["a1"]
    assert "not ineligible" in data["context_scope"]
    assert len(payload.encode()) <= 24000
    for record in data["candidates"]:
        assert record["printed"]["oracle_text"] == next(
            c["oracle_text"] for c in cards if c["id"] == record["card_id"]
        )


def test_payload_counts_shared_candidates_for_each_eligible_group():
    requirements, cards, _ = setup(count=1)
    requirements.append(requirements[0].model_copy(update={"group_id": "list_2"}))
    cards[0]["oracle_text"] += "x" * 14000
    cards[1]["oracle_text"] += "x" * 14000
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(cards[0], group_ids=["list_1", "list_2"], rank=1),
            candidate_from_card(cards[1], group_ids=["list_2"], rank=2),
        ]
    )
    payload, retained = selection_payload(
        "Draw candidates",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == ["c0"]
    assert data["candidates"][0]["group_ids"] == ["list_1", "list_2"]
    assert data["omitted_candidate_ids"] == ["c1"]


def test_payload_filters_mixed_request_membership_per_group():
    requirements = [
        DiscoveryRequirement(
            group_id="draw",
            label="draw",
            requested_count=1,
            request_quote="Find repeatable draw creatures",
        ),
        DiscoveryRequirement(
            group_id="mana",
            label="mana",
            requested_count=1,
            request_quote="Find mana rocks",
        ),
    ]
    cards = [
        {
            "id": "draw",
            "name": "Draw",
            "type_line": "Creature",
            "oracle_text": "{T}: Draw a card.",
        },
        {
            "id": "mana",
            "name": "Mana",
            "type_line": "Artifact",
            "oracle_text": "{T}: Add {U}.",
        },
    ]
    ledger = DiscoveryLedger(candidates=[candidate_from_card(c) for c in cards])
    payload, retained = selection_payload(
        "Find repeatable draw creatures and mana rocks",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    assert len(retained.candidates) == 2
    assert {
        c["card_id"]: c["group_ids"] for c in json.loads(payload)["candidates"]
    } == {
        "draw": ["draw"],
        "mana": ["mana"],
    }


def test_payload_retains_exact_contract_hashes_conditions_and_gap_statuses():
    from sabermetrics.assistant_strategic_obligations import (
        build_strategic_obligations,
        compact_strategic_obligations,
    )

    requirements, cards, ledger = setup(count=1)
    cards[0][
        "oracle_text"
    ] = "{T}, Remove a +1/+1 counter from this creature: Draw a card."
    request = "Find repeatable draw creatures"
    payload, _ = selection_payload(
        request, requirements, ledger, {c["id"]: c for c in cards}, [], set(), []
    )
    data = json.loads(payload)
    record = next(c for c in data["candidates"] if c["card_id"] == "c0")
    vocabulary = {"propositions": {}, "conditions": []}
    expected = compact_strategic_obligations(
        build_strategic_obligations(cards[0], request),
        vocabulary=vocabulary,
    )
    contract = record["strategic_obligations"]
    assert contract["evidence"] == expected["evidence"]
    assert data["strategic_obligation_vocabulary"]["wire_fields"] == {
        "e": "evidence_refs",
        "c": "condition_refs",
    }
    for row, original in zip(contract["obligations"], expected["obligations"]):
        decoded = dict(row)
        for alias, key in data["strategic_obligation_vocabulary"][
            "wire_fields"
        ].items():
            if alias in decoded:
                decoded[key] = decoded.pop(alias)
        decoded["condition_refs"] = [
            data["strategic_obligation_vocabulary"]["conditions"][i]
            for i in decoded.get("condition_refs", [])
        ]
        semantic_original = dict(original)
        semantic_original["condition_refs"] = [
            vocabulary["conditions"][i] for i in original.get("condition_refs", [])
        ]
        assert decoded == semantic_original
        assert row.get("gap") == original.get("gap")
        assert row["status"] == original["status"]
        assert [
            data["strategic_obligation_vocabulary"]["conditions"][i]
            for i in row.get("c", [])
        ] == [vocabulary["conditions"][i] for i in original.get("condition_refs", [])]


def test_payload_rejected_contract_does_not_leak_vocabulary_or_truncate_source():
    requirements, cards, ledger = setup(count=1)
    cards[0]["oracle_text"] += "x" * 22000
    payload, retained = selection_payload(
        "Find draw",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        {"c1", "c2"},
        [],
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == ["c0"]
    record = data["candidates"][0]
    assert record["printed"]["oracle_text"] == cards[0]["oracle_text"]
    assert "strategic_obligations" in record["context_omitted"]
    assert "strategic_obligations" not in record
    assert data["strategic_obligation_vocabulary"]["conditions"] == []
    assert data["strategic_obligation_vocabulary"]["propositions"] == {}
    assert len(payload.encode()) <= 24000


def test_application_caution_is_visible_once_without_becoming_a_board_claim():
    from sabermetrics.assistant_claim_review import claim_units
    from sabermetrics.assistant_suitability import (
        candidate_suitability,
        deck_support_index,
        render_fit_reason,
    )

    requirements, cards, ledger = setup()
    chosen = selection()
    answer, issues = validate_selection(chosen, requirements, ledger)
    assert not issues
    reply = selection_reply(chosen, answer, requirements)
    profile = candidate_suitability(
        {**cards[0], "oracle_text": "{3}{U}: Draw a card."},
        deck_support_index({"entries": []}),
    )
    note = render_fit_reason(profile)
    parts = [reply["content"], "Candidate 0 ({1}{B}) — " + note, ""]
    assert reply["content"].count("Application note:") == 1
    assert (
        "current mana, resources and legal timing are not assumed" in reply["content"]
    )
    assert (
        "Casting, timing and activation resources still need to be available"
        not in note
    )
    units = claim_units(parts, set())
    assert all("not assumed" not in unit["claim"] for unit in units)
    assert any("Activation cost:" in unit["claim"] for unit in units)
    # A model cannot hide an incorrect interaction by naming it a caution.
    parts[1] += " Application note: This draws without paying any cost."
    assert any(
        "without paying any cost" in unit["claim"] for unit in claim_units(parts, set())
    )


def test_shared_arithmetic_preserves_whole_candidates_and_is_not_a_ranker():
    requirements, cards, ledger = setup(count=2)
    cards[0]["oracle_text"] = "{3}{U}: Draw a card."
    cards[1]["oracle_text"] = "{3}{U}{U}: Draw two cards."
    cards[2]["oracle_text"] = "{T}, Pay six {E}: Draw three cards."
    sources = {c["id"]: c for c in cards}
    payload, retained = selection_payload(
        "Find draw creatures", requirements, ledger, sources, [], set(), []
    )
    data = json.loads(payload)
    assert [c.card_id for c in retained.candidates] == [
        c["card_id"] for c in data["candidates"]
    ]
    for c in data["candidates"]:
        assert c["printed"]["oracle_text"] == sources[c["card_id"]]["oracle_text"]
    aid = data["shared_resource_comparisons"]
    assert set(aid["by_card"]) == {"c0", "c1"}
    assert aid["unsupported_cards"] == 1
    assert "deck superiority" in aid["scope"]
    assert all("oracle_text_sha256" not in row for row in aid["profiles"])
    assert all("ledger_scope" not in c for c in data["candidates"])
    assert data["ledger_scope"]


def test_optional_shared_strategy_cannot_displace_candidates_or_contracts():
    requirements, cards, ledger = setup()
    args = (
        "Find draw creatures",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    base, retained = selection_payload(*args)
    large, same = selection_payload(*args, strategic_evidence={"large": "x" * 30000})
    a, b = json.loads(base), json.loads(large)
    assert [c.card_id for c in retained.candidates] == [
        c.card_id for c in same.candidates
    ]
    assert a["candidates"] == b["candidates"]
    assert a["omitted_candidate_ids"] == b["omitted_candidate_ids"]
    assert "shared_strategic_evidence" not in b
    assert b["omitted_shared_evidence"] == ["shared_strategic_evidence"]
    assert len(large.encode()) <= 24000


def test_unparsed_resources_do_not_add_empty_comparison_context():
    requirements, cards, ledger = setup()
    payload, _ = selection_payload(
        "Find draw creatures",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    assert "shared_resource_comparisons" not in json.loads(payload)


def test_optional_aid_uses_bytes_freed_after_lossless_alias_compression():
    requirements, cards, ledger = setup()
    args = (
        "Find draw creatures",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    base, retained = selection_payload(*args)
    original = json.loads(base)
    # Fit just below the final bound; long reference keys would evict this aid.
    overhead = len(b',"shared_strategic_evidence":{"note":""}')
    evidence = {"note": "x" * (24000 - len(base.encode()) - overhead - 5)}
    encoded, same = selection_payload(*args, strategic_evidence=evidence)
    payload = json.loads(encoded)
    assert len(encoded.encode()) <= 24000
    assert payload["shared_strategic_evidence"] == evidence
    assert payload["candidates"] == original["candidates"]
    assert same == retained
    for record in payload["candidates"]:
        for row in record.get("strategic_obligations", {}).get("obligations", []):
            for alias, key in (("e", "evidence_refs"), ("c", "condition_refs")):
                if alias in row:
                    row[key] = row.pop(alias)
    assert (
        len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
        > 24000
    )


def test_postpacking_aid_omits_only_shared_rule_bodies_when_needed():
    requirements, cards, ledger = setup()
    encoded, _ = selection_payload(
        "Find draw creatures",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    base = json.loads(encoded)
    evidence = {
        "conditions": ["Every exact conditional assumption remains."],
        "rows": [
            {"source_id": "c0", "status": "unknown", "unknowns": ["missing_rule"]}
        ],
        "rule_sources": [{"ref": "large-complete-rule", "text": "x" * 24000}],
    }
    packed = attach_selection_evidence(encoded, evidence)
    data = json.loads(packed)
    assert len(packed.encode()) <= 24000
    aid = data["shared_strategic_evidence"]
    assert aid["conditions"] == evidence["conditions"]
    assert aid["rows"] == evidence["rows"]
    assert aid["rule_sources_context_omitted"] is True
    assert data["candidates"] == base["candidates"]
    assert (
        data["strategic_obligation_vocabulary"]
        == base["strategic_obligation_vocabulary"]
    )
    omitted = json.loads(
        attach_selection_evidence(encoded, {"unknowns": ["x" * 24000]})
    )
    assert "shared_strategic_evidence" not in omitted
    assert "shared_strategic_evidence" in omitted["omitted_shared_evidence"]
    assert omitted["candidates"] == base["candidates"]


def test_empty_not_applicable_selector_context_adds_no_wire_padding():
    requirements, cards, ledger = setup()
    encoded, _ = selection_payload(
        "Find draw creatures",
        requirements,
        ledger,
        {c["id"]: c for c in cards},
        [],
        set(),
        [],
    )
    assert attach_selection_evidence(encoded, {"rows": [], "unknowns": []}) == encoded


def test_near_bound_single_whole_source_can_disclose_optional_omission():
    requirements, cards, _ = setup()
    # No enrichment fits, so there are no contract aliases to supply headroom.
    card = cards[0]
    card["oracle_text"] = "{T}: Draw a card. " + "x" * 21800
    ledger = DiscoveryLedger(
        candidates=[candidate_from_card(card, group_ids=["list_1"])]
    )
    args = (
        "Find draw creatures",
        requirements,
        ledger,
        {card["id"]: card},
        [],
        set(),
        [],
    )
    encoded, _ = selection_payload(*args)
    # Tune the complete source up to the original packing boundary.
    payload = json.loads(encoded)
    scope_savings = len(
        "Omitted candidates/enrichments are context_omitted, not ineligible or exhausted discovery. Printed records are whole retrieved sources; honor their truncation flags. Missing enrichments establish no negative facts."
    ) - len(payload["context_scope"])
    scope_savings += len(
        "Retrieved ledger fields may be bounded excerpts; do not infer completeness or absence from them."
    ) - len(payload["ledger_scope"])
    # wire_fields is added after packing, even with zero accepted contracts.
    alias_overhead = len(',"wire_fields":{"e":"evidence_refs","c":"condition_refs"}')
    card["oracle_text"] += "x" * (
        24000 - len(encoded.encode()) - scope_savings + alias_overhead - 1
    )
    ledger.candidates[0] = candidate_from_card(card, group_ids=["list_1"])
    encoded, retained = selection_payload(*args)
    base = json.loads(encoded)
    assert len(retained.candidates) == 1
    assert "strategic_obligations" in base["candidates"][0]["context_omitted"]
    assert len(encoded.encode()) > 23800
    packed = attach_selection_evidence(encoded, {"unknowns": ["x" * 24000]})
    data = json.loads(packed)
    assert len(packed.encode()) <= 24000
    assert data["omitted_shared_evidence"] == ["shared_strategic_evidence"]
    assert data["candidates"] == base["candidates"]
    assert data["candidates"][0]["printed"]["oracle_text"] == card["oracle_text"]
