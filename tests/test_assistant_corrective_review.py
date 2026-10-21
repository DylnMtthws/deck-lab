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
