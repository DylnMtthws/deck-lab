from sabermetrics.assistant_ability_clauses import (
    ability_clauses,
    bounded_compatibility,
)


def card(name, text, **kw):
    return {
        "oracle_id": name,
        "name": name,
        "oracle_text": text,
        "type_line": "Creature",
        "zone_name": "Library",
        **kw,
    }


def test_exact_clauses_separate_cost_target_and_timing():
    c = card(
        "Draw",
        "{1}, Sacrifice another creature: Target player draws a card. Activate only as a sorcery.",
        mana_cost="{B}",
    )
    result = ability_clauses(c)
    row = result["clauses"][0]
    assert result["casting_cost"] == "{B}"
    assert row["costs"] == ["{1}, Sacrifice another creature"]
    assert row["targets"] == ["Target player draws a card"]
    assert row["timing"] and row["source_quote"] == c["oracle_text"]


def test_multiface_truncated_and_compound_restrictions_abstain():
    maker = card("Maker", "Create a 1/1 Soldier creature token.")
    for kw in ({"card_faces": [{"name": "Back"}]}, {"oracle_text_truncated": True}):
        c = card("Outlet", "Sacrifice a creature: Draw a card.", **kw)
        result = bounded_compatibility(c, [maker])
        assert result["status"] == "unknown"
        assert all(not r["providers"] for r in result["requirements"])
    c = card("Outlet", "Sacrifice a creature: If you control an artifact, draw a card.")
    assert not bounded_compatibility(c, [maker])["requirements"][0]["providers"]


def test_counter_mismatch_and_opponent_death_do_not_use_own_ingredients():
    plus = card("Plus", "Put a +1/+1 counter on target creature.")
    charge = card("Charge", "Remove a charge counter from this creature: Draw a card.")
    assert not bounded_compatibility(charge, [plus])["requirements"][0]["providers"]
    outlet = card("Outlet", "Sacrifice a creature: Gain 1 life.")
    opponent = card(
        "Death", "Whenever a creature an opponent controls dies, draw a card."
    )
    assert all(
        not r["providers"]
        for r in bounded_compatibility(opponent, [outlet])["requirements"]
    )


def test_cached_parse_returns_independent_rows_and_current_card_metadata():
    source = card(
        "First",
        "{T}, Sacrifice another creature: Target player draws a card.",
        mana_cost="{B}",
    )
    first = ability_clauses(source)
    row = first["clauses"][0]
    row["costs"].clear()
    row["targets"].append("invented target")
    row["consumed_resources"][0]["source_quote"] = "invented cost"
    row["effects"].clear()
    row["unknowns"].append("invented unknown")
    first["unknowns"].append("invented unknown")
    fresh = ability_clauses(source)
    assert fresh["clauses"][0]["costs"] == ["{T}, Sacrifice another creature"]
    assert fresh["clauses"][0]["targets"] == ["Target player draws a card"]
    assert (
        fresh["clauses"][0]["consumed_resources"][0]["source_quote"]
        == "Sacrifice another creature"
    )
    assert fresh["clauses"][0]["effects"] == ["Target player draws a card."]
    assert not fresh["unknowns"] and not fresh["clauses"][0]["unknowns"]
    other = ability_clauses(
        {
            **source,
            "oracle_id": "Second",
            "mana_cost": "{U}",
            "oracle_text_truncated": True,
        }
    )
    assert other["clauses"][0]["clause_id"] == "Second:ability:0"
    assert other["casting_cost"] == "{U}"
    assert other["coverage"] == other["clauses"][0]["coverage"] == "unknown"


def test_source_and_condition_changes_invalidate_warmed_parse():
    candidate = card("Outlet", "Sacrifice a creature: Draw a card.")
    provider = card("Maker", "Create a 1/1 Soldier creature token.")
    assert bounded_compatibility(candidate, [provider])["requirements"][0]["providers"]
    provider["oracle_text"] = (
        "If you control an artifact, create a 1/1 Soldier creature token."
    )
    result = bounded_compatibility(candidate, [provider])
    assert result["requirements"][0]["status"] == "unknown"
    assert not result["requirements"][0]["providers"]
    provider["oracle_text"] = "Create a 1/1 Soldier creature token."
    for flag in (
        {"card_faces": [{"name": "Back"}]},
        {"layout": "transform"},
        {"oracle_text_truncated": True},
        {"type_line": "Creature // Creature"},
    ):
        assert not bounded_compatibility(candidate, [{**provider, **flag}])[
            "requirements"
        ][0]["providers"]
    candidate["oracle_text"] = (
        "Sacrifice a creature: Unless you control an artifact, draw a card."
    )
    assert not bounded_compatibility(candidate, [provider])["requirements"][0][
        "providers"
    ]


def test_parse_cache_contains_only_bounded_immutable_values():
    from sabermetrics.assistant_ability_clauses import _line_parts

    def immutable(value):
        return (
            isinstance(value, (str, bool, type(None)))
            or isinstance(value, tuple)
            and all(immutable(v) for v in value)
        )

    assert immutable(
        _line_parts("{T}: Target creature gains indestructible until end of turn.")
    )
    assert _line_parts.cache_info().maxsize == 4096
