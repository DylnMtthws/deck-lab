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


def test_checker_requires_explicit_fulfillment_on_provider_schema():
    from pydantic import ValidationError

    from sabermetrics.assistant_answer_review import AnswerReviewDecision

    assert "fulfillment" in AnswerReviewDecision.model_json_schema()["required"]
    with pytest.raises(ValidationError):
        AnswerReviewDecision.model_validate({"patches": [], "unresolved": []})
    assert AnswerReview.model_validate({"patches": []}).fulfillment == "unknown"


def test_nonanswer_decision_survives_rejected_mechanical_patch():
    gateway = SimpleNamespace(
        generate=lambda request: SimpleNamespace(
            value={
                "fulfillment": "nonanswer",
                "patches": [
                    {
                        "part_index": 0,
                        "claim": "Let me research",
                        "replacement": "A fabricated correction",
                        "citations": [
                            {
                                "source_index": 0,
                                "quote": "This quote is not in the supplied source",
                            }
                        ],
                    }
                ],
                "unresolved": ["The requested two lists are missing."],
            }
        )
    )
    parts = ["Let me research the candidates.", ""]
    corrected, receipt = review_answer(
        gateway,
        parts,
        ["An actual canonical card source."],
        "Provide two useful lists.",
    )
    assert corrected == parts
    assert receipt["status"] == "unverified"
    assert receipt["fulfillment"] == "nonanswer"
    assert receipt["unresolved"] == ["The requested two lists are missing."]


@pytest.mark.parametrize(
    "name,replacement", [("Opt", "optional draw"), ("Island", "Islands can matter")]
)
def test_name_guard_does_not_match_names_inside_other_words(name, replacement):
    quote = "You may draw a card, then discard a card."
    assert apply_patches(
        ["incorrect claim"],
        [quote],
        review("incorrect claim", replacement, quote),
        known_names=[name],
    ) == [replacement]


@pytest.mark.parametrize(
    "replacement",
    [
        "Use Opt instead",
        "Use Island instead",
        "Use Fell Mire instead",
        "Use Teval’s Judgment instead",
    ],
)
def test_name_guard_still_rejects_introduced_whole_card_or_face_names(replacement):
    quote = "You may draw a card, then discard a card."
    with pytest.raises(ValueError, match="card name outside its cited evidence"):
        apply_patches(
            ["incorrect claim"],
            [quote],
            review("incorrect claim", replacement, quote),
            known_names=[
                "Opt",
                "Island",
                "Fell the Profane // Fell Mire",
                "Teval's Judgment",
            ],
        )


def test_source_selection_uses_whole_card_names_not_word_prefixes():
    document = {
        "entries": [
            {"name": "Opt", "oracle_text": "Scry 1. Draw a card."},
            {"name": "Island", "oracle_text": "{T}: Add {U}."},
        ]
    }
    assert (
        evidence_sources(document, [], ["Draw is optional. Islands can matter."]) == []
    )
    assert any(
        "Card: Opt" in source
        for source in evidence_sources(document, [], ["Opt is a draw spell."])
    )


def test_canonical_face_aliases_and_normalized_apostrophes_are_allowed():
    quote = "You may put that card onto the battlefield tapped."
    assert apply_patches(
        ["Fell the Profane enters untapped"],
        [quote],
        review("Fell the Profane enters untapped", "Fell Mire enters tapped", quote),
        known_names=["Fell the Profane // Fell Mire"],
    ) == ["Fell Mire enters tapped"]
    assert apply_patches(
        ["Teval's Judgment is mandatory"],
        [quote],
        review("Teval's Judgment is mandatory", "Teval’s Judgment is optional", quote),
        known_names=["Teval's Judgment", "Opt"],
    ) == ["Teval’s Judgment is optional"]


def test_face_metadata_is_available_for_source_selection_and_patch_guard():
    card = {
        "name": "Fell the Profane",
        "oracle_text": "Fell Mire enters the battlefield tapped.",
        "card_faces": [{"name": "Fell the Profane"}, {"name": "Fell Mire"}],
    }
    sources = evidence_sources({"entries": [card]}, [], ["Fell Mire enters untapped."])
    assert any("Card: Fell the Profane // Fell Mire" in source for source in sources)
    assert "Fell the Profane // Fell Mire" in sources.card_names


def test_yawgmoth_cost_correction_survives_unrelated_opt_in_known_names():
    oracle = "Pay 1 life, Sacrifice another creature: Put a -1/-1 counter on up to one target creature and draw a card."
    document = {
        "entries": [
            {"name": "Yawgmoth, Thran Physician", "oracle_text": oracle},
            {"name": "Opt", "oracle_text": "Scry 1. Draw a card."},
        ]
    }
    draft = ["Yawgmoth, Thran Physician is free-to-activate."]
    sources = evidence_sources(document, [], draft)
    source_index = next(
        index for index, source in enumerate(sources) if oracle in source
    )
    replacement = "optional to activate, with no mana payment but costing 1 life and another creature"
    value = review("free-to-activate", replacement, oracle).model_dump()
    value["patches"][0]["citations"][0]["source_index"] = source_index
    requests = []

    def generate(request):
        requests.append(request)
        return SimpleNamespace(value=value)

    corrected, receipt = review_answer(
        SimpleNamespace(generate=generate), draft, sources, "Compare draw engines."
    )
    assert corrected == ["Yawgmoth, Thran Physician is " + replacement + "."]
    assert receipt["status"] == "corrected"
    assert "No mana cost does not mean free to activate" in requests[0].system
    assert "Preserve requirements such as 'another'" in requests[0].system


def test_common_word_split_faces_do_not_turn_mechanical_prose_into_card_names():
    quote = "Sacrifice another creature: Draw a card."
    replacement = (
        "pay life when a creature faces death; return a card, never ignore fire or ice"
    )
    known_names = ["Life // Death", "Never // Return", "Fire // Ice"]
    assert apply_patches(
        ["incorrect claim"],
        [quote],
        review("incorrect claim", replacement, quote),
        known_names=known_names,
    ) == [replacement]
    document = {
        "entries": [{"name": name, "oracle_text": quote} for name in known_names]
    }
    assert evidence_sources(document, [], [replacement]) == []


@pytest.mark.parametrize(
    "replacement",
    [
        "Use Life instead",
        "Use Return instead",
        "Use Fire instead",
        "Use life//death instead",
    ],
)
def test_titled_split_faces_and_full_joined_names_still_guard_real_card_mentions(
    replacement,
):
    quote = "Sacrifice another creature: Draw a card."
    with pytest.raises(ValueError, match="card name outside its cited evidence"):
        apply_patches(
            ["incorrect claim"],
            [quote],
            review("incorrect claim", replacement, quote),
            known_names=["Life // Death", "Never // Return", "Fire // Ice"],
        )
