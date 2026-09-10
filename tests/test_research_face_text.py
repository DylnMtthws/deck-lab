"""Face-only cards reach retrieval and tagging; ``oracle_text`` alone does not.

891 of the 34,551 corpus rows publish no card-level ``oracle_text`` — split
cards, adventures, transform and modal double-faced cards put their text on
faces. A consumer that reads ``oracle_text`` directly is blind to all of them.

That asymmetry is pinned here rather than rediscovered, because it has already
caught two readers: an audit dump that printed blank text for Invasion of
Ikoria, and an audit finding that concluded from the blank column that the card
"ranked on name and type line alone". The second was wrong — the canonical
document carries face text and so does the tag build — and the way to stop that
inference recurring is to assert both halves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sabermetrics.mechanics.tags.predicates import CardView, FaceView
from sabermetrics.substrate.models import CardFilters

ROOT = Path(__file__).resolve().parent.parent

#: A synthetic modal double-faced control whose text lives entirely on its
#: faces, and whose back face taps for mana. Synthetic rather than a real
#: card because the checked-in deck fixture carries placeholder face text,
#: which would make the tag assertion below vacuous.
FACE_ONLY = "Synthetic Split Sorcery"


def test_a_face_only_card_publishes_no_card_level_oracle_text(research_facade):
    rows = {row.name: row for row in research_facade.records(CardFilters())}
    card = rows[FACE_ONLY]
    assert not (card.oracle_text or "").strip(), (
        "this test is about a card whose text lives on its faces; if this card "
        "gained card-level text, pick another one rather than deleting the test"
    )


def test_the_tag_build_reads_faces(research_facade):
    """A mana tag fires on text that appears only on a face."""
    rows = {row.name: row for row in research_facade.records(CardFilters())}
    card = rows[FACE_ONLY]
    assert "mana:produces_mana" in card.tags, (
        "the land face taps for mana; a tag build reading only the card level "
        "would see nothing here and silently under-tag 891 corpus rows"
    )


def test_the_indexed_document_carries_face_text(research_facade):
    rows = {row.name: row for row in research_facade.records(CardFilters())}
    card = rows[FACE_ONLY]
    assert "Add {U}" in card.canonical_document
    assert card.canonical_document.strip(), "the indexed document must not be empty"


def test_a_predicate_view_reads_card_level_and_every_face():
    """The mechanism the tag build relies on, asserted directly."""
    view = CardView(
        name="Front // Back",
        type_line="Instant // Land",
        oracle_text=None,
        faces=(
            FaceView(name="Front", type_line="Instant", oracle_text="Counter it."),
            FaceView(name="Back", type_line="Land", oracle_text="{T}: Add {U}."),
        ),
    )
    texts = [value for _, value in view.texts("oracle_text")]
    assert texts == [
        "Counter it.",
        "{T}: Add {U}.",
    ], "a card with no card-level text must still expose both faces"


@pytest.mark.parametrize("subtype", ["island", "siege"])
def test_subtypes_are_read_from_every_face(research_facade, subtype):
    """Subtypes come off the face type lines too, not just the card's."""
    rows = research_facade.records(
        CardFilters(required_subtypes=(subtype,), commander_legal=None)
    )
    assert rows, f"no card carried the {subtype} subtype"
    assert all(subtype in row.subtypes for row in rows)


def test_the_face_only_population_is_material_and_recorded():
    """Guard the claim in this module's docstring against silent drift.

    Measured over the checked-in deck fixture rather than the development
    corpus, so it runs offline. The 891-row figure quoted above is over the
    34,551-row Scryfall development snapshot and is not asserted here.
    """
    fixture = json.loads((ROOT / "fixtures/cedh/cards.json").read_text())
    face_only = [
        card
        for card in fixture["cards"]
        if not (card.get("oracle_text") or "").strip() and card.get("faces")
    ]
    for card in face_only:
        assert any(
            (face.get("oracle_text") or "").strip() for face in card["faces"]
        ), f"{card['name']} has neither card-level nor face text"
