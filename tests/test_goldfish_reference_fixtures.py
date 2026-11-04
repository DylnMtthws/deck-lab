"""Faithfulness checks for the user-supplied Goldfish reference decklists."""

import json
from pathlib import Path

from sabermetrics.deck_text_import import (
    build_card_lookup,
    normalize_card_name,
    parse_deck_text,
)

GOLD_FISH_FIXTURES = Path(__file__).parents[1] / "fixtures" / "goldfish"


def test_verified_printed_names_resolve_to_canonical_oracle_identity():
    snapshot = json.loads(
        (GOLD_FISH_FIXTURES / "public-card-additions.json").read_text()
    )
    lookup = build_card_lookup(snapshot["cards"])
    for alias, name in [
        ("Glittering Caves of Aglarond", "Gemstone Caverns"),
        ("Merciless Poisoning", "Toxic Deluge"),
        ("Shu Jing Meteorite", "Fellwar Stone"),
    ]:
        assert lookup[normalize_card_name(alias)] == lookup[normalize_card_name(name)]


def test_alias_does_not_match_wrong_oracle_identity():
    lookup = build_card_lookup(
        [{"id": "wrong", "oracle_id": "wrong", "name": "Fellwar Stone"}]
    )
    assert normalize_card_name("Shu Jing Meteorite") not in lookup


def test_user_reference_fixtures_preserve_counts_and_exclude_sideboards():
    manifest = json.loads((GOLD_FISH_FIXTURES / "manifest.json").read_text())

    assert manifest["source"] == "user supplied 2026-10-10"
    assert len(manifest["decks"]) == 4

    for deck in manifest["decks"]:
        parsed = parse_deck_text((GOLD_FISH_FIXTURES / deck["fixture"]).read_text())
        commander_cards = [line for line in parsed.lines if line.commander]
        mainboard_cards = [
            line
            for line in parsed.lines
            if line.zone == "Unsorted" and not line.commander
        ]
        sideboard_cards = [line for line in parsed.lines if line.zone == "Sideboard"]

        assert sum(line.quantity for line in commander_cards) == deck["commander_count"]
        assert [line.name for line in commander_cards] == [deck["commander"]]
        assert sum(line.quantity for line in mainboard_cards) == deck["mainboard_count"]
        assert sum(line.quantity for line in sideboard_cards) == deck["sideboard_count"]

        # This is the card pool handed to the simulator: commander plus mainboard.
        # Sideboard cards, including the Sauron list's trailing commander in the
        # source text, must never leak into the playable deck.
        simulator_card_count = sum(
            line.quantity for line in parsed.lines if line.zone == "Unsorted"
        )
        assert simulator_card_count == deck["commander_count"] + deck["mainboard_count"]

    affordable_sauron = next(
        deck for deck in manifest["decks"] if deck["id"] == "affordable-sauron"
    )
    assert affordable_sauron["theme_preserved"] == "Lord of the Rings theme preserved"
    assert affordable_sauron["sideboard_count"] == 35

    high_power_vivi = next(
        deck for deck in manifest["decks"] if deck["id"] == "high-power-vivi"
    )
    assert high_power_vivi["primer_url"] == (
        "https://moxfield.com/decks/nBKRWuYyckqPxpCmWBjIBg/primer"
    )
    high_power_sauron = next(
        deck for deck in manifest["decks"] if deck["id"] == "high-power-sauron"
    )
    assert high_power_sauron["primer_url"] == (
        "https://moxfield.com/decks/WDJsYexc9EaHELI7vXeHjQ/primer"
    )
