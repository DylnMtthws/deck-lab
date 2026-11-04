"""Verbatim partial facts never transfer support to their parent recommendation."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_claim_fragments import fragment_units, reviewed_sections
from sabermetrics.assistant_claim_review import evidence_catalog, review_claims
from sabermetrics.assistant_claim_subspans import (
    accepted_subspans,
    bind_subspans,
    discover_subspans,
)
from sabermetrics.assistant_progressive_review import progressive_review
from sabermetrics.assistant_review_followthrough import narrow_unverified_findings

SOURCE = "Card: Mystic\nMana cost: {G}\nType: Creature\nOracle text:\n{T}: Add {G}.\nPrinted characteristics: {}"
PARTS = [
    "Candidates",
    "Mystic ({G}) — Mystic has a tap ability. It needs a repeatable untap loop.",
]


def proposal(parts=PARTS, text="Mystic has a tap ability.", **overrides):
    start = parts[1].index(text)
    return {
        "start": start,
        "end": start + len(text),
        "category": "printed_fact",
        "evidence_ids": ["e2"],
        "dependency_refs": [],
        **overrides,
    }


class Reviewer:
    def __init__(self, spans=None):
        self.spans = spans if spans is not None else [proposal()]
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        assert request.prompt_version == "assistant-claim-review.v9"
        return SimpleNamespace(
            value={
                "fulfillment": "partial",
                "judgments": [
                    {
                        "claim_id": "c0",
                        "category": "rules_interaction",
                        "verdict": "unverified",
                        "reason": "The loop has not been established.",
                        "supported_subspans": self.spans,
                    }
                ],
            }
        )


def reviewed():
    reviewer = Reviewer()
    corrected, receipt = review_claims(reviewer, PARTS, [SOURCE], "mana engines")
    assert reviewer.calls == 1
    return corrected, receipt


def test_partial_fact_is_exact_and_parent_and_fulfillment_stay_unknown():
    corrected, receipt = reviewed()
    j = receipt["claim_judgments"][0]
    assert j["verdict"] == "unverified"
    assert j["supported_subspans"][0]["text"] == "Mystic has a tap ability."
    assert receipt["partial_facts_retained"] == receipt["partial_facts_unknown"] == 1
    assert receipt["fulfillment"] == "partial"
    assert receipt["missing_claim_ids"] == []
    assert receipt["episode_subspans"].startswith("omitted")
    output, final = narrow_unverified_findings(PARTS, corrected, [SOURCE], receipt)
    assert "Mystic has a tap ability." in output[1]
    assert "repeatable untap loop" not in output[1]
    assert "Fit assessment remains conditional" in output[1]
    assert SOURCE.split("Oracle text:\n")[1].split("\nPrinted")[0] in output[1]
    assert final["fulfillment"] == "partial"
    assert final["claim_judgments"] == receipt["claim_judgments"]
    # Source-less callers fail closed until runtime passes current sources.
    assert reviewed_sections(PARTS, corrected, receipt) == []
    assert (
        "Mystic has a tap ability."
        in reviewed_sections(PARTS, corrected, receipt, [SOURCE])[0]
    )


@pytest.mark.parametrize(
    "change",
    [
        "id",
        "source",
        "index",
        "hash",
        "parent",
        "text",
        "overlap",
        "dependency",
        "strategy",
    ],
)
def test_stale_or_unbound_partial_facts_fail_closed(change):
    corrected, receipt = reviewed()
    sources, parts = [SOURCE], list(PARTS)
    j = receipt["claim_judgments"][0]
    span = j["supported_subspans"][0]
    if change == "id":
        span["evidence_ids"] = ["not-real"]
    if change == "source":
        sources[0] = SOURCE.replace("{G}.", "{U}.")
    if change == "index":
        span["citations"][0]["source_index"] = 99
    if change == "hash":
        span["parent_claim_sha256"] = "stale"
    if change == "parent":
        parts[1] += " New assertion."
    if change == "text":
        span["text"] = "Mystic proves the loop."
    if change == "overlap":
        j["supported_subspans"].append(deepcopy(span))
    if change == "dependency":
        span["dependency_refs"] = ["c99"]
    if change == "strategy":
        span["category"] = "strategic_judgment"
    unit = fragment_units(parts, set())[0]
    assert accepted_subspans(unit, j, receipt, sources) == []
    assert reviewed_sections(parts, corrected, receipt, sources) == []
    output, _ = narrow_unverified_findings(parts, corrected, sources, receipt)
    assert "Mystic has a tap ability." not in output[1]


@pytest.mark.parametrize(
    "text,chosen",
    [
        (
            "If Mystic is untapped, Mystic can activate. It needs more research.",
            "If Mystic is untapped",
        ),
        (
            "Mystic adds mana, and Mystic supports a loop. It needs more research.",
            "Mystic adds mana",
        ),
        ("Mystic adds mana. It supports a loop.", "It supports a loop."),
        (
            "Mystic adds mana. Spend this mana only on creatures. It needs research.",
            "Mystic adds mana.",
        ),
        ("If Mystic is untapped. It needs research.", "If Mystic is untapped."),
    ],
)
def test_detached_conditions_pronouns_clauses_and_restrictions_rejected(text, chosen):
    parts = ["Candidates", "Mystic ({G}) — " + text]
    unit = fragment_units(parts, set())[0]
    _, lookup = evidence_catalog([SOURCE])
    assert (
        bind_subspans(
            unit, [proposal(parts, chosen)], [SOURCE], lookup, {0}, [], "unverified"
        )
        == []
    )


def test_complete_conditional_is_not_established_by_card_quote_membership():
    parts = [
        "Candidates",
        "Mystic ({G}) — If Mystic is untapped, Mystic can activate. It needs a loop.",
    ]
    unit = fragment_units(parts, set())[0]
    _, lookup = evidence_catalog([SOURCE])
    spans = bind_subspans(
        unit,
        [proposal(parts, "If Mystic is untapped, Mystic can activate.")],
        [SOURCE],
        lookup,
        {0},
        [],
        "unverified",
    )
    assert spans == []


def test_rule_category_requires_complete_rule_source_and_patch_cannot_intersect():
    unit = fragment_units(PARTS, set())[0]
    _, lookup = evidence_catalog([SOURCE])
    assert not bind_subspans(
        unit,
        [proposal(category="rules_interaction")],
        [SOURCE],
        lookup,
        {0},
        [],
        "unverified",
    )
    patch = {"part_index": 1, "claim": "tap ability", "replacement": "mana ability"}
    assert not bind_subspans(
        unit, [proposal()], [SOURCE], lookup, {0}, [patch], "unverified"
    )


def test_progressive_publication_and_final_share_subspan_contract():
    publications = []
    reviewer = Reviewer()
    corrected, receipt = progressive_review(
        reviewer,
        PARTS,
        [SOURCE],
        "mana engines",
        on_reviewed=lambda p, r: publications.append((p, r)),
    )
    assert reviewer.calls == 1
    assert len(publications) == 1
    assert publications[0][1]["fulfillment"] == "unknown"
    assert receipt["claim_judgments"][0]["verdict"] == "unverified"
    assert receipt["partial_facts_retained"] == 1
    assert reviewed_sections(PARTS, corrected, receipt, [SOURCE])


@pytest.mark.parametrize(
    "sources",
    [
        [SOURCE, SOURCE],
        [SOURCE.replace("Oracle text:", "Oracle text (excerpt; omissions unknown):")],
    ],
)
def test_oracle_fallback_requires_unique_complete_identity(sources):
    parts = ["Candidates", "Mystic ({G}) — Mystic proves an unverified loop."]
    output, _ = narrow_unverified_findings(parts, parts, sources, {})
    assert "Add {G}" not in output[1]
    assert "proves an unverified loop" not in output[1]
    assert "Fit assessment remains conditional" in output[1]


def test_fallback_keeps_complete_oracle_once_even_with_other_retained_fragments():
    oracle = "{T}: Add {G}. Spend this mana only to cast a creature."
    source = SOURCE.replace("{T}: Add {G}.", oracle)
    parts = [
        "Candidates",
        "Mystic ({G}) — Printed card text (ability availability unverified): “"
        + oracle
        + "” An unknown loop follows.",
    ]
    unit = fragment_units(parts, set())[0]
    receipt = {
        "claim_unit_version": "assistant-claim-fragments.v1",
        "claim_judgments": [
            {**unit, "verdict": "supported", "rendered_claim": unit["claim"]}
        ],
    }
    output, _ = narrow_unverified_findings(parts, parts, [source], receipt)
    assert output[1].count(oracle) == 1


def test_wire_without_subspans_uses_same_single_call():
    class WireReviewer:
        def generate(self, request):
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "partial",
                        "c0": {
                            "k": "rules",
                            "v": "unverified",
                            "e": [],
                        },
                    }
                )
            )

    _, receipt = review_claims(WireReviewer(), PARTS, [SOURCE], "mana")
    assert (
        receipt["claim_judgments"][0]["supported_subspans"][0]["text"]
        == "Mystic has a tap ability."
    )


def test_mixed_mystic_clause_is_not_rescued_by_a_generic_reason_or_subclause():
    parts = [
        "Candidates",
        "Mystic ({G}) — Mystic contributes mana with Kinnan only if Mystic is sacrificed. An unrelated uncertain assertion. A second uncertain assertion. Mystic has a tap ability.",
    ]

    class MixedReviewer:
        def generate(self, request):
            return SimpleNamespace(
                value={
                    "fulfillment": "partial",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "rules_interaction",
                            "verdict": "unverified",
                            "reason": "Some general contribution is useful.",
                            "supported_subspans": [
                                proposal(parts, "Mystic contributes mana with Kinnan")
                            ],
                        },
                        {
                            "claim_id": "c1",
                            "category": "strategic_judgment",
                            "verdict": "unverified",
                        },
                        {
                            "claim_id": "c2",
                            "category": "strategic_judgment",
                            "verdict": "unverified",
                        },
                        {
                            "claim_id": "c3",
                            "category": "printed_fact",
                            "verdict": "supported",
                            "citations": [
                                {"source_index": 0, "quote": "{T}: Add {G}."}
                            ],
                        },
                    ],
                }
            )

    corrected, receipt = review_claims(MixedReviewer(), parts, [SOURCE], "Kinnan mana")
    output, final = narrow_unverified_findings(parts, corrected, [SOURCE], receipt)
    assert [j["claim_id"] for j in final["claim_judgments"]] == ["c0", "c1", "c2", "c3"]
    assert final["claim_judgments"][0]["verdict"] == "unverified"
    assert final["claim_judgments"][0]["supported_subspans"] == []
    assert "Mystic has a tap ability." in output[1]
    assert "Kinnan" not in output[1]
    assert "sacrificed" not in output[1]
    assert "{T}: Add {G}." in output[1]
    assert "Fit assessment remains conditional" in output[1]


@pytest.mark.parametrize("category", ["printed_fact", "deck_fact", "rules_interaction"])
@pytest.mark.parametrize(
    "claim,chosen",
    [
        (
            "Mystic has a tap ability. That produces a repeatable loop.",
            "That produces a repeatable loop.",
        ),
        (
            "Mystic adds mana. It requires sacrificing a creature to activate.",
            "Mystic adds mana.",
        ),
        (
            "Mystic can activate while tapped. It needs a loop.",
            "Mystic can activate while tapped.",
        ),
        (
            "Other Engine has a tap ability. It needs a loop.",
            "Other Engine has a tap ability.",
        ),
    ],
)
def test_adversarial_categories_cannot_launder_uncertain_subspans(
    category, claim, chosen
):
    parts = ["Candidates", "Mystic ({G}) — " + claim]
    corrected, receipt = review_claims(
        Reviewer([proposal(parts, chosen, category=category)]), parts, [SOURCE], "mana"
    )
    judgment = receipt["claim_judgments"][0]
    assert judgment["verdict"] == "unverified"
    assert judgment["supported_subspans"] == []
    assert reviewed_sections(parts, corrected, receipt, [SOURCE]) == []
    output, final = narrow_unverified_findings(parts, corrected, [SOURCE], receipt)
    assert chosen not in output[1]
    assert "{T}: Add {G}." in output[1]
    assert final["claim_judgments"][0]["verdict"] == "unverified"


@pytest.mark.parametrize(
    "following",
    [
        "It requires sacrificing a creature to activate.",
        "It takes sacrificing a creature to activate.",
        "It costs a creature each time.",
        "It needs a creature sacrificed first.",
        "That activation requires a sacrifice.",
        "This ability works only during your turn.",
        "You may activate only during your turn.",
        "Requires sacrificing a creature to activate.",
        "Its controller pays one life.",
        "It works if you control another creature.",
    ],
)
def test_even_locally_printed_property_cannot_detach_dependent_context(following):
    parts = ["Candidates", "Mystic ({G}) — Mystic has a tap ability. " + following]
    # Use the complete parent even when the ordinary fragmenter splits a
    # non-pronoun continuation: admission must guard the same parent itself.
    unit = fragment_units(parts, set(), atomic=False)[0]
    _, lookup = evidence_catalog([SOURCE])
    assert not bind_subspans(
        unit, [proposal(parts)], [SOURCE], lookup, {0}, [], "unverified"
    )


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace("Card: Mystic", "Card: Mystic Engine"),
        SOURCE.replace("{T}: Add {G}.", "Add {G}."),
        SOURCE.replace("{T}: Add {G}.", "Untap target permanent with {T} in its cost."),
        SOURCE.replace("Oracle text:", "Oracle text (excerpt):"),
        SOURCE.replace("Printed characteristics: {}", ""),
        SOURCE.replace("{T}: Add {G}.", "{T}: Add {G}. ..."),
    ],
)
def test_printed_property_requires_complete_exact_identity_and_activation_cost(source):
    unit = fragment_units(PARTS, set())[0]
    _, lookup = evidence_catalog([source])
    assert not bind_subspans(
        unit, [proposal()], [source], lookup, {0}, [], "unverified"
    )


def test_duplicate_identity_and_unrelated_card_evidence_fail_closed():
    unit = fragment_units(PARTS, set())[0]
    sources = [SOURCE, SOURCE]
    _, lookup = evidence_catalog(sources)
    assert not bind_subspans(unit, [proposal()], sources, lookup, {0}, [], "unverified")
    # e0 is mana-cost evidence, not the printed activation cost.
    _, lookup = evidence_catalog([SOURCE])
    assert not bind_subspans(
        unit, [proposal(evidence_ids=["e0"])], [SOURCE], lookup, {0}, [], "unverified"
    )


@pytest.mark.parametrize("category", ["deck_fact", "rules_interaction"])
def test_valid_printed_property_cannot_be_relabelled(category):
    unit = fragment_units(PARTS, set())[0]
    _, lookup = evidence_catalog([SOURCE])
    assert not bind_subspans(
        unit, [proposal(category=category)], [SOURCE], lookup, {0}, [], "unverified"
    )


@pytest.mark.parametrize("partial", [False, True])
def test_printed_quote_requires_entire_oracle_and_matching_identity(partial):
    oracle = "{T}: Add {G}. Spend this mana only to cast a creature."
    source = SOURCE.replace("{T}: Add {G}.", oracle)
    quoted = "{T}: Add {G}." if partial else oracle
    chosen = f"Mystic Oracle text: “{quoted}”."
    parts = ["Candidates", chosen + " It needs a loop."]
    unit = fragment_units(parts, set(), atomic=False)[0]
    _, lookup = evidence_catalog([source])
    spans = bind_subspans(
        unit, [proposal(parts, chosen)], [source], lookup, {0}, [], "unverified"
    )
    assert bool(spans) is not partial
    wrong = source.replace("Card: Mystic", "Card: Other Engine")
    _, lookup = evidence_catalog([wrong])
    assert not bind_subspans(
        unit, [proposal(parts, chosen)], [wrong], lookup, {0}, [], "unverified"
    )


def test_rules_require_complete_attributed_context_and_cannot_license_inference():
    body = "An ability can be activated if its costs can be paid. Costs must be paid in full."
    source = "Rule 602.1\n" + body
    _, lookup = evidence_catalog([source])
    for chosen, allowed in [
        (f"Rule 602.1: “{body}”.", True),
        ("Rule 602.1: “An ability can be activated if its costs can be paid.”.", False),
        ("If Mystic is untapped, Mystic can activate.", False),
        ("Mystic can activate while tapped.", False),
    ]:
        parts = ["Candidates", chosen + " It needs a loop."]
        unit = fragment_units(parts, set(), atomic=False)[0]
        spans = bind_subspans(
            unit,
            [
                proposal(
                    parts, chosen, category="rules_interaction", evidence_ids=["e0"]
                )
            ],
            [source],
            lookup,
            {0},
            [],
            "unverified",
        )
        assert bool(spans) is allowed
        excerpt = source.replace("Rule 602.1", "Rule 602.1 (excerpt)")
        _, excerpt_lookup = evidence_catalog([excerpt])
        assert not bind_subspans(
            unit,
            [
                proposal(
                    parts, chosen, category="rules_interaction", evidence_ids=["e0"]
                )
            ],
            [excerpt],
            excerpt_lookup,
            {0},
            [],
            "unverified",
        )


def test_abstention_keeps_full_oracle_cost_instead_of_early_partial_output():
    oracle = "{T}, Sacrifice a creature: Add {G}."
    source = SOURCE.replace("{T}: Add {G}.", oracle)
    parts = [
        "Candidates",
        "Mystic ({G}) — Mystic adds mana. It requires sacrificing a creature to activate.",
    ]
    corrected, receipt = review_claims(
        Reviewer([proposal(parts, "Mystic adds mana.")]), parts, [source], "mana"
    )
    assert receipt["claim_judgments"][0]["supported_subspans"] == []
    assert reviewed_sections(parts, corrected, receipt, [source]) == []
    output, final = narrow_unverified_findings(parts, corrected, [source], receipt)
    assert oracle in output[1]
    assert "Mystic adds mana." not in output[1]
    assert final["claim_judgments"][0]["verdict"] == "unverified"


@pytest.mark.parametrize(
    "oracle",
    [
        "Other creatures you control have “\n{T}: Add {G}.\n”",
        "LEVEL 1-4\n{T}: Add {G}.",
        "As long as you control an artifact, Mystic has\n{T}: Add {G}.",
    ],
)
def test_tap_property_cannot_discard_progression_or_granted_scope(oracle):
    source = SOURCE.replace("{T}: Add {G}.", oracle)
    _, catalog = evidence_catalog([source])
    tap_id = next(
        eid for eid, (_, quote) in catalog.items() if quote == "{T}: Add {G}."
    )
    reviewer = Reviewer([proposal(evidence_ids=[tap_id])])
    corrected, receipt = review_claims(reviewer, PARTS, [source], "mana")
    assert not receipt["claim_judgments"][0]["supported_subspans"]
    assert not reviewed_sections(PARTS, corrected, receipt, [source])


@pytest.mark.parametrize(
    "source",
    [
        "Rule source: unavailable\nNo rule text available.",
        "Rule 602.1 (excerpt; omissions unknown)\nAn ability can be activated.",
        "Rule 602.1 (truncated)\nAn ability can be activated.",
        "Rule 602.1\nPlaceholder rule text.",
        "Rule 602.1\n[Unavailable]",
        "Rule 602.1\nExcerpt; omissions unknown.",
    ],
)
def test_incomplete_rules_cannot_certify_semantic_claim_even_in_subset(source):
    parts = ["Lead", "Mystic ({G}) — Mystic can activate while tapped."]

    class SupportedReviewer:
        def generate(self, request):
            return SimpleNamespace(
                value={
                    "fulfillment": "complete",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "rules_interaction",
                            "verdict": "supported",
                            "citations": [
                                {"source_index": 0, "quote": source.split("\n", 1)[1]}
                            ],
                        }
                    ],
                }
            )

    corrected, receipt = review_claims(
        SupportedReviewer(), parts, [source], "mana", evidence_indices={0}
    )
    assert receipt["claim_judgments"][0]["verdict"] == "unverified"
    assert not reviewed_sections(parts, corrected, receipt, [source])
    published = []
    _, final = progressive_review(
        SupportedReviewer(),
        parts,
        [source],
        "mana",
        on_reviewed=lambda p, r: published.append(r),
    )
    assert all(j["verdict"] != "supported" for j in final["claim_judgments"])
    assert not any(
        j["verdict"] == "supported" for r in published for j in r["claim_judgments"]
    )


def test_local_discovery_matches_only_old_admissible_forms_in_original_coordinates():
    rule_body = "An ability can be activated if its costs can be paid."
    sources = [SOURCE, "Rule 602.1\n" + rule_body]
    texts = [
        "Mystic has a tap ability.",
        "Mystic Oracle text: “{T}: Add {G}.”.",
        f"Rule 602.1: “{rule_body}”.",
        "Mystic has a tap ability.",
    ]
    parts = [
        "Candidates",
        "Unknown assertion. " + " ".join(texts) + " It needs a loop.",
    ]
    # Discovery does not re-fragment the supplied original unit.
    unit = fragment_units(parts, set(), atomic=False)[0]
    _, lookup = evidence_catalog(sources)
    expected = []
    cursor = 0
    for text, category, eid in zip(
        texts[:3],
        ["printed_fact", "printed_fact", "rules_interaction"],
        [
            "e2",
            "e2",
            next(e for e, (i, q) in lookup.items() if i == 1 and q == rule_body),
        ],
    ):
        start = parts[1].index(text, cursor)
        cursor = start + len(text)
        expected.append(
            {
                "start": start,
                "end": cursor,
                "category": category,
                "evidence_ids": [eid],
                "dependency_refs": [],
            }
        )
    proposals = discover_subspans(unit, sources, lookup, {0, 1})
    assert proposals == expected
    assert discover_subspans(unit, sources, lookup, {1, 0}) == expected
    bound = bind_subspans(unit, proposals, sources, lookup, {0, 1}, [], "unverified")
    assert [span["text"] for span in bound] == texts[:3]
    assert len(bound) == 3
    assert all(parts[1][p["start"] : p["end"]] == t for p, t in zip(proposals, texts))


@pytest.mark.parametrize(
    "change", ["unknown", "omitted", "duplicate", "clause", "pronoun", "multiline"]
)
def test_local_discovery_abstains_without_complete_literal_proof(change):
    sources = [SOURCE]
    parts = list(PARTS)
    selected = {0}
    if change == "unknown":
        sources = [SOURCE.replace("Card: Mystic", "Card: Other Engine")]
    elif change == "omitted":
        selected = set()
    elif change == "duplicate":
        sources *= 2
    elif change == "clause":
        parts[1] = (
            "Mystic ({G}) — If untapped, Mystic has a tap ability. It needs a loop."
        )
    elif change == "pronoun":
        parts[1] = PARTS[1].replace("Mystic has", "It has")
    elif change == "multiline":
        oracle = "{T}: Add {G}.\nSpend this mana only to cast creatures."
        sources = [SOURCE.replace("{T}: Add {G}.", oracle)]
        # Neither a tap property nor an entire Oracle quote has a single catalog
        # citation containing the complete required body.
        parts[1] = f"Mystic Oracle text: “{oracle}”. It needs a loop."
    unit = fragment_units(parts, set(), atomic=False)[0]
    _, lookup = evidence_catalog(sources)
    assert discover_subspans(unit, sources, lookup, selected) == []


def test_local_discovery_still_passes_through_patch_guard():
    unit = fragment_units(PARTS, set())[0]
    _, lookup = evidence_catalog([SOURCE])
    proposals = discover_subspans(unit, [SOURCE], lookup, {0})
    assert proposals == [proposal()]
    assert not bind_subspans(
        unit,
        proposals,
        [SOURCE],
        lookup,
        {0},
        [{"part_index": 1, "claim": "tap ability", "replacement": "mana ability"}],
        "unverified",
    )


@pytest.mark.parametrize(
    "spans",
    [[], [proposal(evidence_ids=["unknown"])], [proposal(dependency_refs=["c99"])]],
)
def test_injected_proposals_cannot_weaken_independent_local_discovery(spans):
    _, receipt = review_claims(Reviewer(spans), PARTS, [SOURCE], "mana")
    assert (
        receipt["claim_judgments"][0]["supported_subspans"][0]["text"]
        == "Mystic has a tap ability."
    )
    # Malformed receipt binding remains independently fail closed.
    unit = fragment_units(PARTS, set())[0]
    _, lookup = evidence_catalog([SOURCE])
    assert bind_subspans(unit, spans, [SOURCE], lookup, {0}, [], "unverified") == []


@pytest.mark.parametrize(
    "raw",
    [
        {"fulfillment": "partial", "judgments": []},
        {
            "fulfillment": "partial",
            "judgments": [
                {"claim_id": "c0", "category": "invalid", "verdict": "unverified"}
            ],
        },
        {
            "fulfillment": "partial",
            "judgments": [
                {"claim_id": "c99", "category": "printed_fact", "verdict": "unverified"}
            ],
        },
    ],
)
def test_missing_or_invalid_semantic_receipt_cannot_publish_local_facts(raw):
    class Gateway:
        def generate(self, request):
            return SimpleNamespace(value=raw)

    corrected, receipt = review_claims(Gateway(), PARTS, [SOURCE], "mana")
    assert not receipt.get("partial_facts_retained", 0)
    assert reviewed_sections(PARTS, corrected, receipt, [SOURCE]) == []


def test_binding_runs_after_normalization_with_all_patches(monkeypatch):
    import sabermetrics.assistant_claim_subspans as module

    parts = [
        "Candidates",
        "Mystic ({G}) — Mystic has a tap ability. It needs a loop. Mystic costs {U}.",
    ]
    seen = []
    original = module.bind_subspans

    def capture(unit, proposals, sources, lookup, selected, patches, verdict):
        seen.append((unit["claim_id"], [p.model_dump() for p in patches], verdict))
        return original(unit, proposals, sources, lookup, selected, patches, verdict)

    monkeypatch.setattr(module, "bind_subspans", capture)

    class Gateway:
        def generate(self, request):
            return SimpleNamespace(
                value={
                    "fulfillment": "partial",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "rules_interaction",
                            "verdict": "supported",
                        },
                        {
                            "claim_id": "c1",
                            "category": "printed_fact",
                            "verdict": "contradicted",
                            "correction_span": "{U}",
                            "replacement": "{G}",
                            "citations": [
                                {"source_index": 0, "quote": "Mana cost: {G}"}
                            ],
                        },
                    ],
                }
            )

    _, receipt = review_claims(Gateway(), parts, [SOURCE], "mana")
    assert len(receipt["patches"]) == 1
    assert seen == [
        ("c0", receipt["patches"], "unverified"),
        ("c1", receipt["patches"], "contradicted"),
    ]
    assert receipt["partial_facts_retained"] == 1


@pytest.mark.parametrize(
    "body",
    [
        "No rule text available.",
        "Placeholder rule text.",
        "[Unavailable]",
        "Excerpt; omissions unknown.",
    ],
)
def test_local_rule_discovery_uses_the_semantic_completeness_gate(body):
    source = "Rule 602.1\n" + body
    text = f"Rule 602.1: “{body}”."
    parts = [
        "Lead",
        "Mystic ({G}) — " + text + " Independent research remains uncertain.",
    ]

    class UnknownReviewer:
        def generate(self, request):
            return SimpleNamespace(
                value={
                    "fulfillment": "partial",
                    "judgments": [
                        {
                            "claim_id": "c0",
                            "category": "rules_interaction",
                            "verdict": "unverified",
                        },
                        {
                            "claim_id": "c1",
                            "category": "strategic_judgment",
                            "verdict": "unverified",
                        },
                    ],
                }
            )

    corrected, receipt = review_claims(UnknownReviewer(), parts, [source], "mana")
    assert all(not j.get("supported_subspans") for j in receipt["claim_judgments"])
    assert not reviewed_sections(parts, corrected, receipt, [source])
