"""Creature subtypes are expressible as a filter, and the filter discriminates.

R3 shipped with "non-Human creature" inexpressible: the catalog carried card
types only, never the part of a type line after the dash. deck-local-010 asks
for exactly that, and its plan passed on the Kinnan list only because every
mana dork in that list happens to be non-Human — the plan never expressed the
ask's central word, and on a list containing a Human mana dork it would have
returned a wrong answer confidently.

These tests are that list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sabermetrics.assistant.context import DeckContextRegistry
from sabermetrics.assistant.executor import ResearchExecutor
from sabermetrics.assistant.ir import parse_plan
from sabermetrics.assistant.sources import BundleCardSource
from sabermetrics.mechanics.text import type_line_subtypes
from sabermetrics.substrate.models import CardFilters

CONTROL = "Synthetic Human Druid"
NON_HUMAN_DORKS = {
    "Birds of Paradise",
    "Elvish Mystic",
    "Llanowar Elves",
    "Fyndhorn Elves",
}
TEST_PACKS = Path(__file__).resolve().parent / "fixtures" / "packs"
TEST_CONTEXTS = {
    "test:subtype-control": {
        "label": "Subtype control deck",
        "pack_file": "subtype_control.yaml",
        "baseline_of": None,
    }
}


@pytest.mark.parametrize(
    ("type_line", "expected"),
    [
        ("Creature — Halfling Citizen", ("Halfling", "Citizen")),
        ("Legendary Creature — Human Druid", ("Human", "Druid")),
        ("Artifact Creature — Robot Insect", ("Robot", "Insect")),
        ("Land — Island Forest", ("Island", "Forest")),
        ("Instant // Land — Island", ("Island",)),
        ("Land — Urza's Mine", ("Urza's", "Mine")),
        ("Instant", ()),
        ("", ()),
        (None, ()),
    ],
)
def test_subtypes_are_parsed_from_the_part_after_the_dash(type_line, expected):
    assert type_line_subtypes(type_line) == expected


def test_the_control_card_is_mechanically_identical_to_a_qualifying_dork(
    research_facade,
):
    """The counterexample is only a counterexample if nothing else separates it.

    If the Human control carried a different tag, type or mana value, a test
    that excluded it would prove nothing about subtypes.
    """
    rows = {row.name: row for row in research_facade.records(CardFilters())}
    control, elves = rows[CONTROL], rows["Llanowar Elves"]
    assert set(control.tags) == set(elves.tags), "tags must not discriminate"
    assert set(control.types) == set(elves.types), "card types must not discriminate"
    assert control.mana_value == elves.mana_value
    assert control.oracle_text == elves.oracle_text
    assert "human" in control.subtypes
    assert "human" not in elves.subtypes, "only the subtype may differ"


def test_a_subtype_filter_excludes_the_human_and_keeps_the_others(research_facade):
    rows = research_facade.records(
        CardFilters(required_tags=("mana:mana_dork",), commander_legal=None)
    )
    names = {row.name for row in rows}
    assert CONTROL in names, "without a subtype filter the Human is indistinguishable"
    assert NON_HUMAN_DORKS <= names

    filtered = research_facade.records(
        CardFilters(
            required_tags=("mana:mana_dork",),
            excluded_subtypes=("Human",),
            commander_legal=None,
        )
    )
    kept = {row.name for row in filtered}
    assert CONTROL not in kept, "the Human mana dork must be excluded"
    assert NON_HUMAN_DORKS <= kept, "every qualifying non-Human must be retained"


def test_a_required_subtype_selects_land_types(research_facade):
    """The other gap this closes: an intrinsic land type is never printed.

    Island, Tropical Island and Breeding Pool tap for blue because of their
    land subtype, and that ability appears in no oracle text, so no mechanic
    query could reach them. A subtype filter can.
    """
    islands = research_facade.records(
        CardFilters(required_subtypes=("Island",), commander_legal=None)
    )
    names = {row.name for row in islands}
    assert {"Island", "Tropical Island", "Breeding Pool"} <= names
    assert all("island" in row.subtypes for row in islands)


def test_the_deck_local_010_plan_shape_excludes_a_human_mana_dork(research_facade):
    """The acceptance condition: the PLAN, run over a deck containing one.

    This is deck-local-010's plan with the subtype restriction its ask always
    implied and the IR could not previously carry.
    """
    cards = BundleCardSource(research_facade)
    executor = ResearchExecutor(
        cards,
        contexts=DeckContextRegistry(TEST_PACKS, contexts=TEST_CONTEXTS),
    )
    plan = parse_plan(
        {
            "intent": "Find non-Human creature mana sources in this list.",
            "context_id": "test:subtype-control",
            "steps": [
                {
                    "id": "non_human_dorks",
                    "kind": "tag_filter",
                    "scope": "deck",
                    "field_absence": "not_a_field_query",
                    "limit": 200,
                    "filters": {
                        "required_types": ["Creature"],
                        "required_tags": ["mana:mana_dork"],
                        "excluded_subtypes": ["Human"],
                        "commander_legal": None,
                    },
                }
            ],
            "answer_step": "non_human_dorks",
        }
    )
    run = executor.run(plan)
    answer = run.answer
    names = {card.name for card in answer.cards}
    assert CONTROL not in names, (
        "the plan returned a Human mana dork; this is the exact wrong answer "
        "R3 would have given confidently before subtypes existed"
    )
    assert NON_HUMAN_DORKS <= names
    assert all(card.in_deck for card in answer.cards)


def test_the_same_plan_without_the_subtype_filter_returns_the_wrong_answer(
    research_facade,
):
    """The test above must be able to fail. Here is it failing."""
    cards = BundleCardSource(research_facade)
    executor = ResearchExecutor(
        cards,
        contexts=DeckContextRegistry(TEST_PACKS, contexts=TEST_CONTEXTS),
    )
    plan = parse_plan(
        {
            "intent": "Find creature mana sources in this list, subtype-blind.",
            "context_id": "test:subtype-control",
            "steps": [
                {
                    "id": "any_dorks",
                    "kind": "tag_filter",
                    "scope": "deck",
                    "field_absence": "not_a_field_query",
                    "limit": 200,
                    "filters": {
                        "required_types": ["Creature"],
                        "required_tags": ["mana:mana_dork"],
                        "commander_legal": None,
                    },
                }
            ],
            "answer_step": "any_dorks",
        }
    )
    names = {card.name for card in executor.run(plan).answer.cards}
    assert CONTROL in names, (
        "without the subtype filter the Human is returned, which is what makes "
        "the filter load-bearing rather than decorative"
    )
