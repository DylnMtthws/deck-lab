import json
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_claim_fragments import text_hash
from sabermetrics.assistant_claim_review import evidence_catalog, review_claims

SOURCES = [
    "Card: Other Engine\nOracle text:\nOther Engine draws a card.",
    "Current deck:\nCommander Engine is in the command zone.",
    "Card: Chosen Engine\nOracle text:\nChosen Engine draws a card.",
]
PARTS = ["Candidates", "Chosen Engine ({1}) — Chosen Engine draws a card."]


@pytest.mark.parametrize("wire", [True, False])
def test_subset_keeps_global_evidence_ids_indices_and_full_hash_manifest(wire):
    _, lookup = evidence_catalog(SOURCES)
    eid = next(
        eid
        for eid, binding in lookup.items()
        if binding == (2, "Chosen Engine draws a card.")
    )

    class Reviewer:
        def generate(self, request):
            data = json.loads(request.user)
            assert [r["source_index"] for r in data["source_records"]] == [1, 2]
            assert data["claims"][0]["source_indices"] == [1, 2]
            assert any(
                line["id"] == eid
                for record in data["source_records"]
                for line in record["lines"]
            )
            if wire:
                value = {
                    "fulfillment": "complete",
                    "c0": {
                        "category": "printed",
                        "verdict": "supported",
                        "evidence_ids": [eid],
                        "correction": None,
                        "reason": "",
                        "unverified_remainder": False,
                    },
                }
            else:
                value = {
                    "fulfillment": "complete",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "printed_fact",
                            "verdict": "supported",
                            "citations": [
                                {
                                    "source_index": 2,
                                    "quote": "Chosen Engine draws a card.",
                                }
                            ],
                        }
                    ],
                }
            return SimpleNamespace(value=value)

    corrected, receipt = review_claims(
        Reviewer(), PARTS, SOURCES, "Find draw", evidence_indices={1, 2}
    )
    assert corrected == PARTS
    assert receipt["claim_judgments"][0]["verdict"] == "supported"
    assert receipt["claim_judgments"][0]["citations"][0]["source_index"] == 2
    assert receipt["source_sha256"] == [text_hash(s) for s in SOURCES]
    assert receipt["evidence_indices"] == [1, 2]
    assert receipt["evidence_bytes_sent"] == sum(len(s.encode()) for s in SOURCES[1:])
    assert receipt["fulfillment"] == "unknown"


@pytest.mark.parametrize("wire", [True, False])
def test_citation_to_unsent_record_never_supports_or_corrects_a_claim(wire):
    _, lookup = evidence_catalog(SOURCES)
    excluded = next(
        eid
        for eid, binding in lookup.items()
        if binding == (0, "Other Engine draws a card.")
    )

    class Reviewer:
        def generate(self, request):
            if wire:
                value = {
                    "fulfillment": "complete",
                    "c0": {
                        "category": "printed",
                        "verdict": "contradicted",
                        "evidence_ids": [excluded],
                        "correction": {
                            "span": "Chosen Engine",
                            "replacement": "Other Engine",
                        },
                        "reason": "",
                        "unverified_remainder": False,
                    },
                }
            else:
                value = {
                    "fulfillment": "complete",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "printed_fact",
                            "verdict": "contradicted",
                            "correction_span": "Chosen Engine",
                            "replacement": "Other Engine",
                            "citations": [
                                {
                                    "source_index": 0,
                                    "quote": "Other Engine draws a card.",
                                }
                            ],
                        }
                    ],
                }
            return SimpleNamespace(value=value)

    corrected, receipt = review_claims(
        Reviewer(), PARTS, SOURCES, "draw", evidence_indices={2}
    )
    assert corrected == PARTS
    assert receipt["claim_judgments"][0]["verdict"] == "unverified"
    assert not receipt["patches"]


@pytest.mark.parametrize("indices", [set(), {-1}, {3}, {True}])
def test_empty_or_invalid_evidence_selection_does_not_call_provider(indices):
    def unexpected(_):
        raise AssertionError("Provider must not run")

    _, receipt = review_claims(
        SimpleNamespace(generate=unexpected),
        PARTS,
        SOURCES,
        "draw",
        evidence_indices=indices,
    )
    assert receipt["status"] == "unverified"
