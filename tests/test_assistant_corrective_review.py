"""Corrective evidence rechecks only affected exact spans in one extra batch."""

from types import SimpleNamespace

from sabermetrics import assistant_corrective_review as follow
from sabermetrics.assistant_claim_review import review_claims
from tests.test_assistant_claim_fragments import PARTS, SOURCE, Reviewer


def lookup(*args, **kwargs):
    return {
        "additional_sources": [
            "Rule source: new\nA cost must be paid in full before an ability resolves."
        ],
        "affected_claim_ids": ["c2"],
        "lookups": [{"query": "cost"}],
        "stop_reason": "new_complete_evidence",
    }


def test_new_evidence_rechecks_only_gap_and_preserves_prior_facts(monkeypatch):
    corrected, receipt = review_claims(Reviewer(), PARTS, [SOURCE], "draw")
    monkeypatch.setattr(follow, "corrective_evidence", lookup)

    class SecondReviewer:
        def generate(self, request):
            import json

            state = json.loads(request.user)
            assert [u["claim_id"] for u in state["claims"]] == ["c2"]
            assert len(state["source_records"]) == 2
            return SimpleNamespace(
                value={
                    "fulfillment": "complete",
                    "judgments": [
                        {
                            "claim_id": "c2",
                            "category": "printed_fact",
                            "verdict": "contradicted",
                            "correction_span": "needs no further fodder",
                            "replacement": "requires further fodder",
                            "citations": [
                                {
                                    "source_index": 0,
                                    "quote": "{1}, Sacrifice a creature: Draw a card.",
                                }
                            ],
                        }
                    ],
                }
            )

    rendered, sources, result = follow.corrective_review(
        SecondReviewer(),
        "unused",
        PARTS,
        corrected,
        [SOURCE],
        receipt,
        "draw",
        [],
        {},
        check_current=lambda: None,
        can_review=lambda: True,
    )
    assert sources[0] == SOURCE and len(sources) == 2
    assert "requires further fodder" in rendered[1]
    assert result["claim_judgments"][:2] == receipt["claim_judgments"][:2]
    assert result["claim_judgments"][2]["correction_applied"]
    assert result["fulfillment"] == receipt["fulfillment"] == "partial"
    assert result["status"] == "corrected"


def test_failed_followup_or_time_budget_cannot_discard_initial_review(monkeypatch):
    corrected, receipt = review_claims(Reviewer(), PARTS, [SOURCE], "draw")
    monkeypatch.setattr(follow, "corrective_evidence", lookup)
    for can_review, expected in [(False, "time_budget"), (True, "incomplete_review")]:

        class FailedReviewer:
            def generate(self, request):
                assert can_review
                return SimpleNamespace(
                    value={"fulfillment": "unknown", "judgments": []}
                )

        rendered, sources, result = follow.corrective_review(
            FailedReviewer(),
            "unused",
            PARTS,
            corrected,
            [SOURCE],
            receipt,
            "draw",
            [],
            {},
            check_current=lambda: None,
            can_review=lambda: can_review,
        )
        assert rendered == corrected and sources == [SOURCE]
        assert result["claim_judgments"] == receipt["claim_judgments"]
        assert result["corrective_retrieval"]["review_status"] == expected


def test_blank_saved_kinnan_review_stays_unresolved_without_dispatch(monkeypatch):
    from sabermetrics import assistant_corrective_retrieval as retrieval
    from tests.test_assistant_corrective_retrieval import review

    parts, receipt = review(["Kinnan, Bonder Prodigy adds additional mana."])
    receipt["claim_judgments"][0].update(
        reason="", retrieval_gap=None, verdict="unknown"
    )
    receipt.update(fulfillment="unknown", unresolved=["c0: unverified"], patches=[])
    monkeypatch.setattr(
        retrieval,
        "retrieve_rules",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("lookup")),
    )

    class NoModel:
        def generate(self, request):
            raise AssertionError("model call")

    rendered, sources, result = follow.corrective_review(
        NoModel(),
        "unused",
        parts,
        parts,
        [SOURCE],
        receipt,
        "mana",
        [],
        {},
        check_current=lambda: None,
        can_review=lambda: True,
    )
    assert rendered == parts and sources == [SOURCE]
    assert result["claim_judgments"] == receipt["claim_judgments"]
    assert result["unresolved"] == receipt["unresolved"]
    assert result["fulfillment"] == "unknown"
    assert (
        result["corrective_retrieval"]["gaps"][0]["status"] == "unspecified_review_gap"
    )


def test_explicit_missing_rule_followup_keeps_all_source_ids_context_and_unknowns(
    monkeypatch,
):
    import hashlib
    import json

    from sabermetrics import assistant_corrective_retrieval as retrieval
    from sabermetrics.assistant_rules import RulesEvidence
    from tests.test_assistant_corrective_retrieval import hit

    corrected, receipt = review_claims(Reviewer(), PARTS, [SOURCE], "draw")
    receipt["claim_judgments"][0]["verdict"] = "unknown"
    receipt["claim_judgments"][2].update(
        category="rules_interaction",
        retrieval_gap={
            "kind": "missing_rule",
            "missing_proposition": "Whether a sacrificed creature can pay another activation cost.",
        },
    )
    # Cross-card antecedents, commander support, counterevidence and unknown
    # source kinds must survive even when they aren't named in the exact unit.
    sources = [
        SOURCE,
        "Card: Other Engine\nOracle text:\nSacrifice another creature.",
        "Opaque future evidence kind\nA constraint applies.",
        "Rule source: counterevidence\nCosts must be paid separately.",
    ]
    context = {
        "recommendation_packets": [{"name": "Other Engine", "support": ["Commander"]}],
        "scope": "Compare the entire deck",
    }
    monkeypatch.setattr(
        retrieval,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(status="available", query=a[1], hits=[hit()]),
    )
    calls = []

    class StillUnverified:
        def generate(self, request):
            state = json.loads(request.user)
            calls.append(state)
            assert [r["source_index"] for r in state["source_records"]] == list(
                range(5)
            )
            assert state["research_context"] == context
            assert [u["claim_id"] for u in state["claims"]] == ["c2"]
            return SimpleNamespace(
                value={
                    "fulfillment": "complete",
                    "judgments": [
                        {
                            "claim_id": "c2",
                            "category": "rules_interaction",
                            "verdict": "unverified",
                            "reason": "Not established",
                        }
                    ],
                }
            )

    args = (
        StillUnverified(),
        "unused",
        PARTS,
        corrected,
        sources,
        receipt,
        "draw",
        [],
        {"entries": [{"name": "Commander", "is_commander": True}]},
    )
    rendered, expanded, result = follow.corrective_review(
        *args,
        check_current=lambda: None,
        can_review=lambda: True,
        research_context=context,
    )
    assert len(calls) == 1
    assert expanded[:4] == sources
    assert result["claim_judgments"][:2] == receipt["claim_judgments"][:2]
    assert result["fulfillment"] == "partial"
    assert result["patches"] == receipt["patches"]
    assert result["source_sha256"] == [
        hashlib.sha256(s.encode()).hexdigest() for s in expanded
    ]
    assert result["corrective_retrieval"]["new_source_indices_by_claim"] == {"c2": [4]}
    assert result["corrective_retrieval"]["evidence_scope"]["status"] == "full_fallback"
    follow.corrective_review(
        StillUnverified(),
        "unused",
        PARTS,
        rendered,
        expanded,
        result,
        "draw",
        [],
        {},
        check_current=lambda: None,
        can_review=lambda: True,
    )
    assert len(calls) == 1


def test_duplicate_existing_rule_never_dispatches_followup(monkeypatch):
    from sabermetrics import assistant_corrective_retrieval as retrieval
    from sabermetrics.assistant_rules import RulesEvidence
    from tests.test_assistant_corrective_retrieval import hit, review

    record = hit()
    parts, receipt = review(["Widget taps for mana."])
    receipt.update(fulfillment="unknown", unresolved=["c0: unverified"], patches=[])
    sources = ["Rule source: irrelevant duplicate\n" + record.text]
    monkeypatch.setattr(
        retrieval,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(status="available", query=a[1], hits=[record]),
    )

    class NoModel:
        def generate(self, request):
            raise AssertionError("duplicate evidence dispatched model")

    _, returned, result = follow.corrective_review(
        NoModel(),
        "unused",
        parts,
        parts,
        sources,
        receipt,
        "mana",
        [],
        {},
        check_current=lambda: None,
        can_review=lambda: True,
    )
    assert returned == sources
    assert result["claim_judgments"] == receipt["claim_judgments"]
    assert result["corrective_retrieval"]["review_status"] == "not_needed"


def test_followup_cannot_replace_a_different_span(monkeypatch):
    import copy

    corrected, receipt = review_claims(Reviewer(), PARTS, [SOURCE], "draw")
    monkeypatch.setattr(follow, "corrective_evidence", lookup)
    stale = copy.deepcopy(receipt["claim_judgments"][2])
    stale["claim_sha256"] = "different"
    monkeypatch.setattr(
        follow, "review_claims", lambda *a, **k: (PARTS, {"claim_judgments": [stale]})
    )
    rendered, sources, result = follow.corrective_review(
        object(),
        "unused",
        PARTS,
        corrected,
        [SOURCE],
        receipt,
        "draw",
        [],
        {},
        check_current=lambda: None,
        can_review=lambda: True,
    )
    assert rendered == corrected and sources == [SOURCE]
    assert result["claim_judgments"] == receipt["claim_judgments"]
    assert result["corrective_retrieval"]["review_status"] == "stale_followup"
