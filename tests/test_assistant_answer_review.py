"""Exact-span and source guards around the bounded mechanical cross-check."""

import json
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_answer_review import (
    AnswerReview,
    apply_patches,
    evidence_sources,
    review_answer,
)
from sabermetrics.cedh.errors import ModelProviderError, ModelValidationError


def review(
    claim="until your next turn",
    replacement="until the end of your next turn",
    quote="until the end of your next turn",
):
    return AnswerReview.model_validate(
        {
            "patches": [
                {
                    "part_index": 0,
                    "claim": claim,
                    "replacement": replacement,
                    "citations": [{"source_index": 0, "quote": quote}],
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "claim,replacement,source",
    [
        (
            "until your next turn",
            "until the end of your next turn",
            "You may play that card until the end of your next turn.",
        ),
        (
            "mana or mill once each turn",
            "mana or mill per tap; extra untapping can permit both",
            "{T}: Add {B} or {G}. {T}: Mill two cards.",
        ),
        (
            "the cheapest reanimation",
            "a reanimation spell costing {1}{B}; Reanimate costs {B}",
            "Current deck: Animate Dead: {1}{B}; Reanimate: {B}",
        ),
        (
            "only Lathril",
            "any target creature",
            "Return an Elf you control to its owner's hand: Untap target creature. Activate only once each turn.",
        ),
    ],
)
def test_supported_exact_corrections_preserve_surrounding_answer(
    claim, replacement, source
):
    parts = ["Context: " + claim + ".", "Useful caveat."]
    result = apply_patches(parts, [source], review(claim, replacement, source))
    assert result == ["Context: " + replacement + ".", "Useful caveat."]
    assert parts[0] == "Context: " + claim + "."


@pytest.mark.parametrize(
    "failure",
    [
        "missing_claim",
        "ambiguous",
        "missing_quote",
        "bad_source",
        "bad_part",
        "overlap",
    ],
)
def test_invalid_patch_set_is_rejected_atomically(failure):
    parts = ["until your next turn"]
    value = review().model_dump()
    patch = value["patches"][0]
    if failure == "missing_claim":
        patch["claim"] = "not present"
    elif failure == "ambiguous":
        parts[0] += " until your next turn"
    elif failure == "missing_quote":
        patch["citations"][0]["quote"] = "a fabricated source quote"
    elif failure == "bad_source":
        patch["citations"][0]["source_index"] = 10
    elif failure == "bad_part":
        patch["part_index"] = 7
    else:
        value["patches"].append(patch)
    with pytest.raises(ValueError):
        apply_patches(
            parts,
            ["until the end of your next turn"],
            AnswerReview.model_validate(value),
        )


@pytest.mark.parametrize(
    "failure",
    [
        ModelProviderError("private provider detail"),
        ModelValidationError("malformed"),
        {"patches": "invalid"},
    ],
)
def test_unavailable_or_malformed_review_keeps_draft_and_reports_unverified(failure):
    def generate(request):
        assert request.call_type == "build:answer-review"
        assert request.max_output_tokens == 3072
        if isinstance(failure, Exception):
            raise failure
        return SimpleNamespace(value=failure)

    draft = ["A source-based draft."]
    corrected, receipt = review_answer(
        SimpleNamespace(generate=generate), draft, ["A real source passage."], "Explain"
    )
    assert corrected == draft
    assert receipt["status"] == "unverified"
    assert "private" not in json.dumps(receipt)


def test_source_selection_excludes_failed_tools_and_retains_actual_cost_comparison():
    doc = {
        "entries": [
            {
                "name": "Animate Dead",
                "mana_cost": "{1}{B}",
                "oracle_text": "Enchant creature card in a graveyard.",
            },
            {"name": "Reanimate", "mana_cost": "{B}"},
        ]
    }
    sources = evidence_sources(
        doc,
        [
            {
                "type": "tool_result",
                "name": "card_details",
                "ok": False,
                "data": {"card": {"oracle_text": "invented"}},
            }
        ],
        ["Animate Dead is cheapest."],
    )
    assert any("Reanimate: {B}" in source for source in sources)
    assert not any("invented" in source for source in sources)
    assert evidence_sources(doc, [], ["Hello there."]) == []


def test_review_is_one_call_and_records_patch_citations():
    calls = []

    def generate(request):
        calls.append(request)
        return SimpleNamespace(value=review())

    corrected, receipt = review_answer(
        SimpleNamespace(generate=generate),
        ["until your next turn"],
        ["until the end of your next turn"],
        "Exact timing?",
    )
    assert corrected == ["until the end of your next turn"]
    assert len(calls) == 1 and receipt["status"] == "corrected"
    assert receipt["patches"][0]["citations"][0]["source_index"] == 0


def test_multiline_oracle_quotes_are_literal_not_json_escaped():
    oracle = '{T}: Add {B}.\n{T}: Mill two cards.\nCreate a token named "Example".'
    sources = evidence_sources(
        {"entries": [{"name": "Skull Prophet", "oracle_text": oracle}]},
        [],
        ["Skull Prophet can tap."],
    )
    assert any(oracle in s for s in sources)
    value = review(
        "once each turn", "per tap, with extra untapping possible", oracle
    ).model_dump()
    value["patches"][0]["citations"][0]["source_index"] = next(
        i for i, source in enumerate(sources) if oracle in source
    )
    result = apply_patches(
        ["once each turn"], sources, AnswerReview.model_validate(value)
    )
    assert "extra untapping" in result[0]


def test_faces_and_priority_sources_survive_many_cards():
    cards = [{"name": f"Card {i}", "oracle_text": "Long text " * 90} for i in range(24)]
    cards.append(
        {
            "name": "Fell the Profane // Fell Mire",
            "oracle_text": "Destroy target creature or planeswalker.",
        }
    )
    sources = evidence_sources(
        {"entries": cards},
        [],
        ["Fell the Profane " + " ".join(c["name"] for c in cards)],
        {
            "hits": [
                {
                    "source_id": "CR 302.6",
                    "text": "A creature cannot attack unless it has been under its controller's control continuously.",
                }
            ]
        },
    )
    assert sources.omitted
    assert "CR 302.6" in sources[0]
    assert any("Current deck printed mana costs" in s for s in sources)
    face = evidence_sources(
        {"entries": [cards[-1]]}, [], ["Fell the Profane destroys a creature."]
    )
    assert any("Destroy target creature" in s for s in face)


def test_long_type_lines_do_not_displace_the_cost_comparison():
    document = {
        "entries": [
            {
                "name": f"Current library card {i}",
                "mana_cost": "{2}{B}",
                "type_line": "Legendary Artifact Enchantment Creature — "
                + "LongSubtype " * 10,
                "zone_name": "Library",
                "is_commander": i == 0,
            }
            for i in range(100)
        ]
    }
    sources = evidence_sources(document, [], ["The commander has useful synergies."])
    assert any(
        "Current deck printed mana costs" in s
        and "Current library card 99: {2}{B}" in s
        for s in sources
    )
    assert any("Current deck printed types" in s for s in sources)
    assert all(len(s) <= 6000 for s in sources)


def test_checker_cannot_rewrite_measurement_or_introduce_unsupported_mana():
    def generate(request):
        return SimpleNamespace(
            value=review(
                "Improved by 0.4 turns",
                "Improved by 4 turns",
                "A genuine Oracle clause.",
            )
        )

    draft = ["Improved by 0.4 turns in Goldfish."]
    result, receipt = review_answer(
        SimpleNamespace(generate=generate),
        draft,
        ["A genuine Oracle clause."],
        "Compare",
    )
    assert result == draft and receipt["status"] == "unverified"
    with pytest.raises(ValueError, match="unsupported number or mana"):
        apply_patches(
            ["costs mana"],
            ["Pay {1}{R} to cast it."],
            review("costs mana", "costs {5}{U}", "Pay {1}{R} to cast it."),
        )


def test_cost_ceiling_preserves_draft_without_another_attempt():
    from sabermetrics.errors import LLMCostCeilingExceeded

    calls = []

    def generate(request):
        calls.append(request)
        raise LLMCostCeilingExceeded("Ceiling reached")

    draft = ["A useful draft."]
    result, receipt = review_answer(
        SimpleNamespace(generate=generate),
        draft,
        ["A genuine Oracle clause."],
        "Explain",
    )
    assert result == draft and receipt == {
        "status": "not_checked",
        "reason": "cost_ceiling",
    }
    assert len(calls) == 1


def test_quantity_words_and_explicit_protected_parts_are_guarded():
    with pytest.raises(ValueError, match="unsupported number or mana"):
        apply_patches(
            ["draw one card"],
            ["Draw two cards, then discard a card."],
            review(
                "draw one card",
                "draw three cards",
                "Draw two cards, then discard a card.",
            ),
        )
    with pytest.raises(ValueError, match="cannot be rewritten"):
        apply_patches(
            ["Averaged a turn-4.2 win across 500 games."],
            ["Draw two cards, then discard a card."],
            review(
                "Averaged a turn-4.2 win across 500 games.",
                "No useful result.",
                "Draw two cards, then discard a card.",
            ),
            protected_parts={0},
        )
