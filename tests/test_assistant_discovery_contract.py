"""Discovery completion is evidence-backed, bounded and distinct from capacity."""

import pytest
from pydantic import ValidationError

from sabermetrics.assistant_discovery_contract import (
    DiscoveryAnswer,
    DiscoveryAnswerGroup,
    DiscoveryClaim,
    DiscoveryLedger,
    DiscoveryNote,
    DiscoveryRequirement,
    candidate_from_card,
    derive_discovery_requirements,
    merge_discovery_ledger,
    prior_recommendations,
    render_discovery_groups,
    revises_discovered_candidates,
    validate_discovery_answer,
    validate_discovery_prose_counts,
)
from sabermetrics.assistant_research_task import ResearchTask


def candidate(index, **kwargs):
    return candidate_from_card(
        {
            "id": f"id-{index}",
            "name": f"Card {index}",
            "mana_cost": "{1}{B}",
            "oracle_text": "Supplied printed text.",
            **kwargs,
        },
        rank=index + 1,
    )


def test_counted_groups_do_not_promote_open_capacity_or_inventory():
    assert (
        derive_discovery_requirements("I have seven open slots. Find good candidates.")
        == []
    )
    assert derive_discovery_requirements("I have seven cards in my deck.") == []
    result = derive_discovery_requirements(
        "Give me seven candidates and three sacrifice outlets."
    )
    assert [(r.requested_count, r.label) for r in result] == [
        (7, "candidates"),
        (3, "sacrifice outlets"),
    ]
    assert derive_discovery_requirements("Don't recommend seven candidates.") == []
    assert (
        derive_discovery_requirements("Give me options beyond these six core cards.")
        == []
    )


def test_revise_inherits_requested_groups_but_acknowledgement_does_not():
    previous = derive_discovery_requirements("List seven options.")
    assert revises_discovered_candidates("Re-rank those candidates with less setup.")
    assert (
        derive_discovery_requirements(
            "Re-rank those candidates with less setup.", previous
        )
        == previous
    )


@pytest.mark.parametrize(
    "question",
    [
        "Compare these two cards.",
        "Pick between these two options.",
        "Show me the two cards I added.",
        "Compare two cards.",
        "List those two candidates.",
        "Show the two cards you suggested.",
    ],
)
def test_reference_to_existing_set_is_not_a_new_discovery_count(question):
    assert derive_discovery_requirements(question) == []
    previous = derive_discovery_requirements("List seven candidates.")
    assert derive_discovery_requirements(question, previous) == []


def test_additive_count_still_detected_alongside_comparison():
    assert derive_discovery_requirements("Give me seven cards.")[0].requested_count == 7
    assert (
        derive_discovery_requirements(
            "Compare these two cards and recommend three candidates."
        )[0].requested_count
        == 3
    )


@pytest.mark.parametrize(
    "question",
    [
        "Give me three short candidate lists: recursion, draw, and ramp.",
        "Give me three lists of candidates.",
    ],
)
def test_list_count_is_not_a_candidate_count(question):
    assert derive_discovery_requirements(question) == []


def test_requirement_inheritance_applies_only_to_explicit_revision():
    previous = derive_discovery_requirements("List seven options.")
    assert (
        derive_discovery_requirements("Relax the search constraints.", previous)
        == previous
    )
    assert derive_discovery_requirements("Yes, keep the core cards.", previous) == []
    assert (
        derive_discovery_requirements("List three options instead.", previous)[
            0
        ].requested_count
        == 3
    )


def test_six_items_never_complete_a_seven_item_answer_and_keep_grounded_partial():
    ledger = DiscoveryLedger(candidates=[candidate(i) for i in range(6)])
    answer = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(
                group_id="list_1",
                claimed_count=7,
                card_ids=[c.card_id for c in ledger.candidates],
            )
        ]
    )
    result = validate_discovery_answer(
        derive_discovery_requirements("List seven candidates."), ledger, answer
    )
    assert not result.complete
    assert {issue.code for issue in result.issues} == {
        "claimed_count_mismatch",
        "requested_count_mismatch",
    }
    assert len(result.grounded_card_ids) == 6


def test_core_cards_are_not_counted_as_new_recommendations():
    ledger = DiscoveryLedger(
        candidates=[candidate(0), candidate(1, already_in_library=True)]
    )
    answer = DiscoveryAnswer(
        groups=[DiscoveryAnswerGroup(group_id="list_1", card_ids=["id-0", "id-1"])]
    )
    result = validate_discovery_answer(
        derive_discovery_requirements("List two options beyond the core."),
        ledger,
        answer,
    )
    assert result.grounded_card_ids == ["id-0"]
    assert {i.code for i in result.issues} == {
        "already_in_deck",
        "requested_count_mismatch",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"value": "{B}"},
        {"source_id": "invented"},
        {"fact_id": "missing"},
        {"kind": "oracle_text"},
    ],
)
def test_unsupported_structured_cost_text_or_source_is_rejected(change):
    card = candidate(0)
    claim = DiscoveryClaim(
        card_id=card.card_id, **{**card.source_facts[0].model_dump(), **change}
    )
    result = validate_discovery_answer(
        [],
        DiscoveryLedger(candidates=[card]),
        DiscoveryAnswer(
            groups=[DiscoveryAnswerGroup(group_id="list_1", card_ids=[card.card_id])],
            claims=[claim],
        ),
    )
    assert not result.complete
    assert result.issues[0].code == "unsupported_claim"
    assert result.grounded_card_ids == []


def test_exact_structured_claim_is_supported_but_not_a_prose_safety_certificate():
    card = candidate(0)
    answer = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(
                group_id="list_1", claimed_count=1, card_ids=[card.card_id]
            )
        ],
        claims=[
            DiscoveryClaim(card_id=card.card_id, **card.source_facts[0].model_dump())
        ],
    )
    result = validate_discovery_answer([], DiscoveryLedger(candidates=[card]), answer)
    assert result.complete
    assert "free text" in result.scope
    assert "legality" in result.scope


def test_duplicate_unknown_and_missing_facts_do_not_supply_counts():
    empty = candidate_from_card({"id": "empty", "name": "Empty"})
    result = validate_discovery_answer(
        derive_discovery_requirements("List three cards."),
        DiscoveryLedger(candidates=[empty]),
        DiscoveryAnswer(
            groups=[
                DiscoveryAnswerGroup(
                    group_id="list_1", card_ids=["empty", "unknown", "unknown"]
                )
            ]
        ),
    )
    assert {i.code for i in result.issues} == {
        "duplicate_card",
        "unknown_card",
        "missing_card_facts",
        "requested_count_mismatch",
        "empty_group",
    }
    assert result.grounded_card_ids == []


def test_ledger_bound_retains_scarce_role_and_refreshed_membership():
    old = DiscoveryLedger(
        candidates=[
            candidate(i).model_copy(update={"group_ids": ["broad"]}) for i in range(40)
        ]
    )
    rare = candidate(50).model_copy(update={"group_ids": ["rare"]})
    updated = candidate(0, already_in_library=True)
    merged = merge_discovery_ledger(old, DiscoveryLedger(candidates=[rare, updated]))
    assert len(merged.candidates) == 40
    assert "id-50" in [c.card_id for c in merged.candidates]
    assert next(c for c in merged.candidates if c.card_id == "id-0").in_deck
    with pytest.raises(ValidationError):
        DiscoveryLedger(candidates=[candidate(0), candidate(0)])


def test_plain_markdown_count_guard_is_structural_only():
    content = "Here are seven candidates:\n" + "\n".join(
        f"{i+1}. Card {i}" for i in range(6)
    )
    assert validate_discovery_prose_counts(content)[0].code == "claimed_count_mismatch"
    assert (
        validate_discovery_prose_counts("Here are seven candidates. They are good.")
        == []
    )
    assert (
        validate_discovery_prose_counts("One card can draw three cards.\n- A detail")
        == []
    )


def test_partial_search_refresh_does_not_erase_retrieved_printed_facts():
    original = candidate(0).model_copy(
        update={"conditions": ["Supplied condition"], "group_ids": ["role"]}
    )
    refreshed = candidate_from_card(
        {"id": "id-0", "name": "Card 0", "already_in_library": True}
    )
    merged = merge_discovery_ledger(
        DiscoveryLedger(candidates=[original]), DiscoveryLedger(candidates=[refreshed])
    )
    retained = merged.candidates[0]
    assert retained.in_deck
    assert retained.source_facts == original.source_facts
    assert retained.conditions == original.conditions
    assert retained.group_ids == ["role"]


def test_six_or_more_explicit_core_names_are_not_truncated():
    names = [f"Core {i}" for i in range(12)]
    assert ResearchTask(named_cards=names).named_cards == names


def test_nested_rationale_bullets_do_not_inflate_top_level_pick_count():
    text = (
        "Two candidates:\n- Card A\n  - Its rationale\n- Card B\n   - Another rationale"
    )
    assert validate_discovery_prose_counts(text) == []
    bad = "Three candidates:\n  - Card A\n    - Its rationale\n  - Card B"
    assert validate_discovery_prose_counts(bad)[0].code == "claimed_count_mismatch"


def test_fresh_candidates_outrank_inherited_records_even_with_new_query_groups():
    old = DiscoveryLedger(
        candidates=[
            candidate(i).model_copy(update={"rank": 1, "group_ids": ["old-query"]})
            for i in range(40)
        ]
    )
    incoming = DiscoveryLedger(
        candidates=[
            candidate(i).model_copy(update={"rank": 999, "group_ids": ["new-query"]})
            for i in range(40, 80)
        ]
    )
    merged = merge_discovery_ledger(old, incoming)
    assert {row.card_id for row in merged.candidates} == {
        row.card_id for row in incoming.candidates
    }
    assert all(row.group_ids == ["new-query"] for row in merged.candidates)


def test_renderer_emits_one_whole_candidate_per_row_with_actual_cost_and_note():
    cards = [candidate(i) for i in range(3)]
    ledger = DiscoveryLedger(candidates=cards)
    requirements = derive_discovery_requirements("Give me three sacrifice outlets.")
    note = "This is a candidate-specific strategic hypothesis; its printed costs and restrictions still apply."
    answer = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(
                group_id="list_1", card_ids=[card.card_id for card in cards]
            )
        ],
        notes=[DiscoveryNote(card_id=card.card_id, note=note) for card in cards],
    )
    rows = render_discovery_groups(requirements, ledger, answer)
    assert len(rows) == 3
    for index, row in enumerate(rows):
        assert row == f"sacrifice outlets: Card {index} ({{1}}{{B}}) — {note}"
        assert "\n" not in row
    assert validate_discovery_answer(requirements, ledger, answer).complete


def test_note_validation_requires_unique_selected_retrieved_ids():
    card = candidate(0)
    other = candidate(1)
    answer = DiscoveryAnswer(
        groups=[DiscoveryAnswerGroup(group_id="list_1", card_ids=[card.card_id])],
        notes=[
            DiscoveryNote(card_id=card.card_id, note="First candidate note."),
            DiscoveryNote(card_id=card.card_id, note="Duplicate candidate note."),
            DiscoveryNote(card_id=other.card_id, note="Unselected candidate note."),
            DiscoveryNote(card_id="unknown", note="Unretrieved candidate note."),
        ],
    )
    ledger = DiscoveryLedger(candidates=[card, other])
    result = validate_discovery_answer([], ledger, answer)
    assert not result.complete
    assert {issue.code for issue in result.issues} == {
        "duplicate_note",
        "unselected_note",
        "unknown_note_card",
    }
    assert render_discovery_groups([], ledger, answer) == [
        "Candidates: Card 0 ({1}{B})"
    ]


def test_renderer_preserves_complete_long_note_and_discloses_missing_cost():
    card = candidate_from_card(
        {"id": "unknown-cost", "name": "Unpriced Fact", "type_line": "Creature"}
    )
    note = "A complete explanation. " * 15
    answer = DiscoveryAnswer(
        groups=[DiscoveryAnswerGroup(group_id="list_1", card_ids=[card.card_id])],
        notes=[DiscoveryNote(card_id=card.card_id, note=note)],
    )
    row = render_discovery_groups([], DiscoveryLedger(candidates=[card]), answer)[0]
    assert row.endswith(note.strip())
    assert "mana cost not retrieved" in row
    with pytest.raises(ValidationError):
        DiscoveryNote(card_id=card.card_id, note="x" * 401)
    with pytest.raises(ValidationError):
        DiscoveryNote(card_id=card.card_id, note="short")


def test_legacy_manifest_without_notes_still_renders_canonical_facts():
    card = candidate(0)
    answer = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(group_id="list_1", card_ids=[card.card_id, "invented"])
        ]
    )
    assert answer.notes == []
    assert render_discovery_groups([], DiscoveryLedger(candidates=[card]), answer) == [
        "Candidates: Card 0 ({1}{B})"
    ]


def test_uncounted_requested_list_cannot_be_fulfilled_by_an_empty_group():
    requirement = DiscoveryRequirement(
        group_id="list_1", label="mana sinks", request_quote="mana sinks"
    )
    answer = DiscoveryAnswer(
        groups=[DiscoveryAnswerGroup(group_id="list_1", card_ids=[])]
    )
    result = validate_discovery_answer([requirement], DiscoveryLedger(), answer)
    assert not result.complete
    assert "empty_group" in {issue.code for issue in result.issues}


def test_additional_seven_must_exclude_prior_three_but_comparisons_can_reuse_them():
    ledger = DiscoveryLedger(candidates=[candidate(index) for index in range(7)])
    prior = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(group_id="list_1", card_ids=["id-0", "id-1", "id-2"])
        ]
    )
    messages = [
        {
            "role": "assistant",
            "research": {
                "selected_candidates": prior.model_dump(),
                "discovery_ledger": ledger.model_dump(),
            },
        }
    ]
    excluded = prior_recommendations(messages)
    assert excluded == {f"id-{index}": f"Card {index}" for index in range(3)}
    answer = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(
                group_id="list_1",
                claimed_count=7,
                card_ids=[card.card_id for card in ledger.candidates],
            )
        ]
    )
    requirements = derive_discovery_requirements("Give me seven additional candidates.")
    assert validate_discovery_answer(requirements, ledger, answer).complete
    result = validate_discovery_answer(
        requirements, ledger, answer, excluded_card_ids=set(excluded)
    )
    assert not result.complete
    assert sum(issue.code == "previously_recommended" for issue in result.issues) == 3
    assert "requested_count_mismatch" in {issue.code for issue in result.issues}
    assert result.grounded_card_ids == [f"id-{index}" for index in range(3, 7)]


def test_prior_recommendations_need_selected_ids_and_same_receipt_retrieved_facts():
    first, second = candidate(0), candidate(1)
    selected = DiscoveryAnswer(
        groups=[
            DiscoveryAnswerGroup(
                group_id="list_1", card_ids=[first.card_id, "invented"]
            )
        ]
    )
    receipt = {
        "selected_candidates": selected.model_dump(),
        "session_state": {
            "ledger": DiscoveryLedger(candidates=[first, second]).model_dump()
        },
    }
    messages = [
        {"role": "user", "research": receipt},
        {"role": "assistant", "content": "I recommend Invented Card."},
        {
            "role": "assistant",
            "research": {"selected_candidates": selected.model_dump()},
        },
        {"role": "assistant", "presentation": {"research": receipt}},
    ]
    assert prior_recommendations(messages) == {first.card_id: first.name}
    # A later receipt cannot retroactively ground an earlier unknown selection.
    assert (
        prior_recommendations(
            [
                {
                    "role": "assistant",
                    "research": {"selected_candidates": selected.model_dump()},
                },
                {
                    "role": "assistant",
                    "research": {
                        "discovery_ledger": DiscoveryLedger(
                            candidates=[first]
                        ).model_dump()
                    },
                },
            ]
        )
        == {}
    )


def test_prior_recommendations_bounds_messages_and_ignores_malformed_state():
    card = candidate(0)
    receipt = {
        "role": "assistant",
        "research": {
            "selected_candidates": {
                "groups": [{"group_id": "list_1", "card_ids": [card.card_id]}]
            },
            "discovery_ledger": DiscoveryLedger(candidates=[card]).model_dump(),
        },
    }
    assert prior_recommendations([receipt]) == {card.card_id: card.name}
    assert (
        prior_recommendations([receipt] + [{"role": "user", "content": "later"}] * 100)
        == {}
    )
    assert (
        prior_recommendations(
            [
                {
                    "role": "assistant",
                    "research": {
                        "selected_candidates": "bad",
                        "discovery_ledger": "bad",
                    },
                }
            ]
        )
        == {}
    )
