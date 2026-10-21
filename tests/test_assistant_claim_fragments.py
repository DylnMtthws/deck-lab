"""Review must preserve conditional truth without lending it to other spans."""

from types import SimpleNamespace

from sabermetrics.assistant_claim_fragments import (
    VERSION,
    fragment_units,
    reviewed_claim_text,
)
from sabermetrics.assistant_claim_review import review_claims
from sabermetrics.assistant_review_followthrough import narrow_unverified_findings

SOURCE = "Card: Draw Engine\nMana cost: {B}\nType: Creature\nOracle text:\n{1}, Sacrifice a creature: Draw a card.\nPrinted characteristics: {}"
PARTS = [
    "Candidates",
    "Draw Engine ({B}) — The ability costs {1} and a sacrificed creature. "
    "If you pay that cost, you draw a card. "
    "Repeated use needs no further fodder.",
    "",
]


class Reviewer:
    def __init__(self, *, cross_span=False):
        self.cross_span = cross_span

    def generate(self, request):
        import json

        payload = json.loads(request.user)
        assert payload["parent_parts"]["1"] == PARTS[1]
        assert len(payload["claims"]) == 3
        judgments = []
        for i in range(3):
            item = {
                "claim_id": f"c{i}",
                "category": "printed_fact" if i < 2 else "strategic_judgment",
                "verdict": "supported" if i < 2 else "unverified",
                "citations": (
                    [
                        {
                            "source_index": 0,
                            "quote": "{1}, Sacrifice a creature: Draw a card.",
                        }
                    ]
                    if i < 2
                    else []
                ),
            }
            if i == 0 and self.cross_span:
                item.update(
                    verdict="contradicted",
                    correction_span="needs no further fodder",
                    replacement="requires further fodder",
                )
            judgments.append(item)
        return SimpleNamespace(value={"fulfillment": "partial", "judgments": judgments})


def test_supported_cost_and_conditional_effect_survive_unknown_repeatability():
    corrected, receipt = review_claims(Reviewer(), PARTS, [SOURCE], "repeatable draw")
    result, receipt = narrow_unverified_findings(PARTS, corrected, [SOURCE], receipt)
    assert receipt["claim_unit_version"] == VERSION
    assert "The ability costs {1} and a sacrificed creature." in result[1]
    assert "If you pay that cost, you draw a card." in result[1]
    assert "needs no further fodder" not in result[1]
    assert "Further claims remain unverified." in result[1]
    assert receipt["narrowed_claims"][0]["preserved_fragments"] == 2
    assert all(
        reviewed_claim_text(receipt, j, PARTS) == u["claim"]
        for j, u in zip(receipt["claim_judgments"], fragment_units(PARTS, set()))
    )


def test_stale_receipt_cannot_transfer_support_to_a_rewritten_paragraph():
    _corrected, receipt = review_claims(Reviewer(), PARTS, [SOURCE], "draw")
    changed = [*PARTS]
    changed[1] = changed[1].replace("{1}", "{0}")
    result, _ = narrow_unverified_findings(changed, changed, [SOURCE], receipt)
    assert "ability costs {0}" not in result[1]
    assert reviewed_claim_text(receipt, receipt["claim_judgments"][0], changed) == ""


def test_correction_cannot_edit_a_different_reviewed_fragment():
    corrected, receipt = review_claims(
        Reviewer(cross_span=True), PARTS, [SOURCE], "draw"
    )
    assert corrected == PARTS
    assert receipt["claim_judgments"][0]["verdict"] == "unverified"
    assert not receipt["claim_judgments"][0]["correction_applied"]


def test_dependent_sentence_keeps_antecedent_and_condition_in_one_unit():
    parts = [
        "Lead",
        "Engine ({B}) — If a counter is present, a return is possible. It needs an outlet. A new independent claim follows.",
    ]
    units = fragment_units(parts, set())
    assert len(units) == 2
    assert (
        "If a counter" in units[0]["claim"]
        and "It needs an outlet." in units[0]["claim"]
    )
    assert all(
        parts[u["part_index"]][u["start"] : u["end"]] == u["claim"] for u in units
    )


def test_every_visible_fragment_must_be_reviewed_even_when_all_originals_survive():
    parts = ["Candidates", "Engine ({B}) — Pay {1} to draw a card."]
    unit = fragment_units(parts, set())[0]
    judgment = {**unit, "verdict": "supported", "rendered_claim": unit["claim"]}
    changed = [parts[0], parts[1] + " Repeated use needs no further fodder."]
    result, _ = narrow_unverified_findings(
        parts,
        changed,
        [],
        {"claim_unit_version": VERSION, "claim_judgments": [judgment]},
    )
    assert result == parts


def test_only_requested_fragment_ids_are_rereviewed_without_renumbering():
    class SubsetReviewer:
        def generate(self, request):
            import json

            payload = json.loads(request.user)
            assert [u["claim_id"] for u in payload["claims"]] == ["c2"]
            return SimpleNamespace(
                value={
                    "fulfillment": "unknown",
                    "judgments": [
                        {
                            "claim_id": "c2",
                            "category": "printed_fact",
                            "verdict": "unverified",
                        }
                    ],
                }
            )

    _, receipt = review_claims(
        SubsetReviewer(), PARTS, [SOURCE], "draw", claim_ids={"c2"}
    )
    assert [j["claim_id"] for j in receipt["claim_judgments"]] == ["c2"]


def test_a_patch_cannot_mark_another_contradicted_fragment_corrected():
    class MixedReviewer:
        def generate(self, request):
            return SimpleNamespace(
                value={
                    "fulfillment": "partial",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "printed_fact",
                            "verdict": "contradicted",
                            "correction_span": "{1}",
                            "replacement": "{1}",
                            "citations": [
                                {
                                    "source_index": 0,
                                    "quote": "{1}, Sacrifice a creature: Draw a card.",
                                }
                            ],
                        },
                        {
                            "claim_id": "c1",
                            "category": "printed_fact",
                            "verdict": "contradicted",
                            "citations": [
                                {
                                    "source_index": 0,
                                    "quote": "{1}, Sacrifice a creature: Draw a card.",
                                }
                            ],
                        },
                        {
                            "claim_id": "c2",
                            "category": "strategic_judgment",
                            "verdict": "unverified",
                        },
                    ],
                }
            )

    _, receipt = review_claims(MixedReviewer(), PARTS, [SOURCE], "draw")
    assert receipt["claim_judgments"][0]["correction_applied"]
    assert not receipt["claim_judgments"][1]["correction_applied"]
