"""Offline contract and abstention tests for the bounded literal compiler."""

import copy
import hashlib
import json

import pytest

from sabermetrics.assistant_inference_facts import (
    MAX_CARDS,
    MAX_TEXT_BYTES,
    VERSION,
    compile_deck_facts,
)
from sabermetrics.assistant_inference_search import discover_connections


def card(text, name="Amber Voyager", identity="amber", **fields):
    return {
        "id": identity,
        "name": name,
        "type_line": "Creature — Pirate",
        "oracle_text": text,
        "power": "2",
        "toughness": "3",
        "mana_cost": "{1}{R}",
        **fields,
    }


RULE_TEXT = (
    "To explore, a creature's controller reveals the top card of their library. "
    "If a land card is revealed this way, they put it into their hand. "
    "Otherwise, they put a +1/+1 counter on that creature, then they may put "
    "the revealed card into their graveyard."
)


def rule(**fields):
    return {
        "kind": "comprehensive_rules",
        "source": "supplied-test-document",
        "version": "test-edition",
        "complete": True,
        "oracle_text": RULE_TEXT,
        **fields,
    }


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_renamed_chain_has_exact_evidence_and_no_name_table():
    cards = [
        card("{R}, Discard a card: Amber Voyager gets +1/+1 until end of turn."),
        card(
            "Whenever you discard a card, Umber Beacon deals 1 damage to each opponent.",
            "Umber Beacon",
            "beacon",
        ),
        card(
            "Whenever one or more Pirates you control deal damage to a player, Copper Seeker explores.",
            "Copper Seeker",
            "seeker",
        ),
    ]
    original = copy.deepcopy(cards)
    result = compile_deck_facts(cards, rule())
    assert set(result) == {"version", "sources", "facts", "coverage"}
    assert result["version"] == VERSION
    assert result["coverage"]["complete"]
    assert len(result["facts"]) == 4
    assert cards == original
    for fact in result["facts"]:
        source = next(
            s for s in result["sources"] if s["source_id"] == fact["source_id"]
        )
        assert source["oracle_text"][fact["start"] : fact["end"]] == fact["quote"]
        assert (
            source["source_sha256"]
            == fact["source_sha256"]
            == digest(source["copied_fields"])
        )
        assert fact["status"] == "bounded_interpretation"
    activation = next(f for f in result["facts"] if f["kind"] == "activated")
    assert activation["inputs"][0]["event"] == "discard"
    assert len(activation["costs"]) == 2
    assert any("paid" in condition for condition in activation["conditions"])
    assert any("consumes a card" in condition for condition in activation["conditions"])
    source = result["sources"][0]
    assert source["copied_fields"]["power"] == "2"
    assert source["copied_fields"]["mana_cost"] == "{1}{R}"
    assert all(
        f["kind"] != "rule_definition" or f["required_rules"] == []
        for f in result["facts"]
    )


def test_explore_branch_is_not_discard_draw_or_guaranteed_fuel():
    result = compile_deck_facts([], rule())
    fact = result["facts"][0]
    assert fact["kind"] == "rule_definition"
    assert {e["event"] for e in fact["outputs"]} == {
        "put_into_hand",
        "move_to_graveyard",
    }
    land, nonland = fact["outputs"]
    assert land["condition"] == "revealed card is a land"
    assert nonland["optional"] is True
    assert "nonland" in nonland["condition"]
    assert nonland["from_zone"] == "library"
    assert any("+1/+1 counter" in c for c in fact["conditions"])
    assert any("leave it on top" in c for c in fact["conditions"])
    assert any("finite" in c for c in fact["conditions"])


@pytest.mark.parametrize("combat", ["", "combat "])
def test_damage_filters_recipient_and_batch_preserved(combat):
    result = compile_deck_facts(
        [
            card(
                f"Whenever one or more Pirates and/or Rogues you control deal {combat}damage to a player, draw a card."
            )
        ]
    )
    fact = result["facts"][0]
    event = fact["inputs"][0]
    assert event["source_types"] == "Pirates and/or Rogues"
    assert event["actor"] == "you"
    assert event["recipient"] == "a player"
    assert event["combat_only"] is bool(combat)
    assert event["quantity"] == "unknown"
    assert fact["outputs"][0]["quantity"] == 1


@pytest.mark.parametrize(
    "discard,damage,quantity",
    [("a card", "1", 1), ("one or more cards", "that many", "discarded_card_count")],
)
def test_discard_multiplicity(discard, damage, quantity):
    result = compile_deck_facts(
        [
            card(
                f"Whenever you discard {discard}, Amber Voyager deals {damage} damage to each opponent."
            )
        ]
    )
    fact = result["facts"][0]
    assert fact["inputs"][0]["event"] == "discard"
    assert fact["outputs"][0]["quantity"] == quantity
    assert fact["outputs"][0]["recipient"] == "each opponent"


@pytest.mark.parametrize(
    "text",
    [
        "{T}, Discard a card: Draw two cards.",
        "Discard a card: Amber Voyager gains flying until end of turn.",
        "Sacrifice Amber Voyager: Draw a card.",
        "Whenever another creature you control dies, draw a card.",
    ],
)
def test_other_narrow_effects(text):
    result = compile_deck_facts([card(text)])
    assert len(result["facts"]) == 1
    assert result["coverage"]["complete"]


@pytest.mark.parametrize(
    "text",
    [
        "Put the top card of your library into your hand.",
        "Draw three cards.",
    ],
)
def test_spell_instructions_only_for_spell_type(text):
    assert not compile_deck_facts([card(text)])["facts"]
    assert len(compile_deck_facts([card(text, type_line="Sorcery")])["facts"]) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Discard two cards: Draw a card.",
        "Pay 2 life, Discard a card: Draw a card.",
        "{T}, Discard a card: Draw a card. Activate only as a sorcery.",
        "{T}, Discard a card: Draw a card.\nActivate only once each turn.",
        "If you control a Wizard, discard a card: Draw a card.",
        'Enchanted creature has "Discard a card: Draw a card."',
        "Creatures you control have the following ability.\nDiscard a card: Draw a card.",
        "Level up {1}\nLEVEL 2–3\nDiscard a card: Draw a card.",
        "As long as you control an artifact, this has the following ability.\nDiscard a card: Draw a card.",
        "Whenever you discard a card, Amber Voyager deals 1 damage to each opponent. You gain 1 life.",
        "Discard a card: Draw a card",
        "Discard a card: Draw a card…",
    ],
)
def test_full_scope_unknown_cost_and_unsupported_scope_abstain(text):
    result = compile_deck_facts([card(text)])
    assert not result["facts"]
    assert not result["coverage"]["complete"]
    assert result["sources"][0]["oracle_text"] == text
    assert result["sources"][0]["clauses"]


@pytest.mark.parametrize(
    "patch",
    [
        {"truncated": True},
        {"text_truncated": True},
        {"oracle_text_truncated": True},
        {"complete": False},
        {"id": None},
        {"name": ""},
        {"oracle_text": ""},
        {"type_line": ""},
        {
            "card_faces": [
                {"name": "Hidden face", "oracle_text": "Discard a card: Draw a card."}
            ]
        },
    ],
)
def test_missing_and_incomplete_sources_withheld(patch):
    record = card(
        "Discard a card: Draw a card.",
        **{k: v for k, v in patch.items() if k not in ("name", "oracle_text")},
    )
    record.update(patch)
    result = compile_deck_facts([record])
    assert not result["facts"]
    assert result["sources"][0]["copied_fields"] == record
    assert result["sources"][0]["clauses"][0]["parse_status"] == "withheld"


def test_conflicting_duplicate_order_is_deterministic_and_no_facts_escape():
    first = card("Discard a card: Draw a card.")
    second = card("Discard a card: Draw two cards.")
    left = compile_deck_facts([first, second])
    right = compile_deck_facts([second, first])
    assert left == right
    assert not left["facts"]
    assert len(left["sources"]) == 2
    assert all(
        "conflicting_duplicate_identity" in s["clauses"][0]["issues"]
        for s in left["sources"]
    )
    exact = compile_deck_facts([first, first])
    assert exact["coverage"]["raw_source_ledger"][0]["occurrences"] == 2


@pytest.mark.parametrize(
    "patch",
    [
        {"complete": False},
        {"version": ""},
        {"source": ""},
        {"kind": ""},
        {"text_hash": "bad"},
        {"oracle_text": "Exploring draws a card."},
        {"oracle_text": RULE_TEXT + " Also draw a card."},
    ],
)
def test_rules_must_be_supplied_complete_and_coherent(patch):
    result = compile_deck_facts([card("{T}: Amber Voyager explores.")], rule(**patch))
    assert [f["kind"] for f in result["facts"]] == ["activated"]
    assert result["coverage"]["unresolved_rules"] == ["explore"]
    assert not result["coverage"]["complete"]


def test_rule_sources_have_distinct_identity_and_ambiguous_dependencies_unresolved():
    result = compile_deck_facts(
        [card("{T}: Amber Voyager explores.")],
        {"sources": [rule(), rule(version="other-edition")]},
    )
    assert len({s["source_id"] for s in result["sources"]}) == 3
    assert result["coverage"]["unresolved_rules"] == ["explore"]
    valid = rule(text_hash=hashlib.sha256(RULE_TEXT.encode()).hexdigest())
    assert compile_deck_facts([], valid)["coverage"]["complete"]
    assert compile_deck_facts([card("{T}: Amber Voyager explores.")])["coverage"][
        "unresolved_rules"
    ] == ["explore"]


def test_mutations_never_poison_subsequent_compilation_and_hash_covers_all_fields():
    record = card("Discard a card: Draw a card.")
    rules = rule()
    original = copy.deepcopy([record, rules])
    one = compile_deck_facts([record], rules)
    two = compile_deck_facts([record], rules)
    one["sources"][0]["copied_fields"]["power"] = "100"
    one["facts"][0]["costs"].append({"bogus": True})
    assert compile_deck_facts([record], rules) == two
    assert [record, rules] == original
    before = next(s for s in two["sources"] if s["card_id"])
    record["power"] = "3"
    after = compile_deck_facts([record])["sources"][0]
    assert before["source_sha256"] != after["source_sha256"]
    assert before["source_id"] == after["source_id"]
    json.dumps(two, allow_nan=False)


def test_card_limit_retains_ledger_and_withholds_all_facts():
    cards = [
        card("Discard a card: Draw a card.", identity=f"card-{i}")
        for i in range(MAX_CARDS + 1)
    ]
    result = compile_deck_facts(cards)
    assert result["coverage"]["overflow"]
    assert not result["facts"]
    assert not result["coverage"]["complete"]
    assert len(result["coverage"]["raw_source_ledger"]) == len(cards)


def test_utf8_byte_limit_and_nested_raw_source_retention():
    record = card("é" * MAX_TEXT_BYTES)
    result = compile_deck_facts([record])
    source = result["sources"][0]
    assert len(source["oracle_text"].encode()) <= MAX_TEXT_BYTES
    assert source["source_sha256"] == digest(record)
    assert result["coverage"]["overflow"]
    assert not result["facts"]
    assert "raw_record_omitted_byte_limit" in source["clauses"][0]["issues"]
    face = card(
        "",
        card_faces=[
            {
                "name": "Back",
                "oracle_text": "Whenever you discard a card, Back deals 1 damage to each opponent.",
            }
        ],
    )
    assert compile_deck_facts([face])["sources"][0]["copied_fields"] == face


def test_cancellation_before_and_during_compilation():
    with pytest.raises(InterruptedError):
        compile_deck_facts([], check_current=lambda: False)
    calls = []

    def callback():
        calls.append(True)
        if len(calls) == 4:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        compile_deck_facts(
            [card("Discard a card: Draw a card.")], check_current=callback
        )
    assert len(calls) == 4


def test_permanent_explore_supplied_shape_and_rule_number_not_hardcoded():
    text = (
        "987.12z Certain abilities instruct a permanent to explore. To do so, "
        "that permanent’s controller reveals the top card of their library. "
        "If a land card is revealed this way, that player puts that card into their hand. "
        "Otherwise, that player puts a +1/+1 counter on the exploring permanent, "
        "then may put the revealed card into their graveyard."
    )
    result = compile_deck_facts([], rule(oracle_text=text))
    assert len(result["facts"]) == 1
    assert result["facts"][0]["inputs"][0]["subject"] == "exploring permanent"
    assert result["facts"][0]["quote"] == text
    assert not compile_deck_facts(
        [], rule(oracle_text=text + "\nInstead, draw a card.")
    )["facts"]


def test_granted_following_ability_does_not_detach_and_offsets_include_unicode():
    text = "Other creatures gain the following ability.\nDiscard a card: Draw a card."
    assert not compile_deck_facts([card(text)])["facts"]
    text = "Unrecognized — raw ability.\n{T}: Draw a card."
    result = compile_deck_facts([card(text)])
    fact = result["facts"][0]
    assert fact["start"] == text.index("{T}")
    assert text[fact["start"] : fact["end"]] == fact["quote"]
    assert result["coverage"]["uninterpreted"] == 1
    assert result["coverage"]["interpreted"] == 1
    assert not result["coverage"]["complete"]


def test_provenance_overhead_does_not_exhaust_raw_text_budget():
    cards = [
        card(
            "Discard a card: Draw a card.",
            identity=f"card-{i}",
            provenance={"source": "Repeated offline metadata " * 90},
        )
        for i in range(92)
    ]
    result = compile_deck_facts(cards)
    coverage = result["coverage"]
    assert coverage["input_record_bytes"] > MAX_TEXT_BYTES
    assert coverage["input_text_bytes"] < MAX_TEXT_BYTES
    assert not coverage["overflow"]
    assert coverage["complete"]
    assert len(result["facts"]) == 92
    by_id = {c["id"]: c for c in cards}
    for source in result["sources"]:
        original = by_id[source["card_id"]]
        assert source["source_sha256"] == digest(original)
        assert source["copied_fields"] == original


def test_record_budget_and_nested_face_text_have_separate_limits():
    from sabermetrics.assistant_inference_facts import MAX_RECORD_BYTES

    record = card("Discard a card: Draw a card.", provenance="p" * MAX_RECORD_BYTES)
    result = compile_deck_facts([record])
    assert result["coverage"]["input_text_bytes"] < MAX_TEXT_BYTES
    assert result["coverage"]["overflow"]
    assert not result["facts"]
    assert result["sources"][0]["source_sha256"] == digest(record)
    record = card("", card_faces=[{"oracle_text": "é" * MAX_TEXT_BYTES}])
    result = compile_deck_facts([record])
    assert result["coverage"]["input_record_bytes"] < MAX_RECORD_BYTES
    assert result["coverage"]["input_text_bytes"] == 2 * MAX_TEXT_BYTES
    assert result["coverage"]["overflow"]
    assert not result["facts"]


@pytest.mark.parametrize(
    "name,type_line,mana_cost,first_line",
    [
        (
            "Shadow Rift",
            "Instant",
            "{U}",
            "Target creature gains shadow until end of turn. "
            "(It can block or be blocked by only creatures with shadow.)",
        ),
        (
            "Slip Through Space",
            "Sorcery",
            "{U}",
            "Devoid (This card has no color.)\n"
            "Target creature can't be blocked this turn.",
        ),
        (
            "Zhalfirin Shapecraft",
            "Instant",
            "{1}{U}",
            "Until end of turn, target creature has base power and toughness 1/1, "
            "becomes a blue Frog, and loses all abilities.",
        ),
    ],
)
def test_full_spell_records_keep_independent_draw(
    name, type_line, mana_cost, first_line
):
    record = {
        "id": name.lower().replace(" ", "-"),
        "oracle_id": "supplied-oracle-" + name,
        "name": name,
        "type_line": type_line,
        "oracle_text": first_line + "\nDraw a card.",
        "mana_cost": mana_cost,
        "cmc": 2 if name == "Zhalfirin Shapecraft" else 1,
        "layout": "normal",
        "colors": [] if name == "Slip Through Space" else ["U"],
        "color_identity": ["U"],
        "legalities": {"commander": "legal"},
        "set": "offline-fixture",
        "collector_number": "1",
    }
    bundle = compile_deck_facts([record])
    assert len(bundle["facts"]) == 1
    fact = bundle["facts"][0]
    assert fact["kind"] == "spell"
    assert fact["outputs"][0]["event"] == "draw"
    assert fact["quote"] == "Draw a card."
    assert fact["start"] == len(first_line) + 1
    source = bundle["sources"][0]
    assert source["copied_fields"] == record
    assert source["source_sha256"] == fact["source_sha256"] == digest(record)
    assert source["oracle_text"][fact["start"] : fact["end"]] == fact["quote"]
    assert not bundle["coverage"]["complete"]
    search = discover_connections(bundle)
    assert search["coverage"]["invalid_bindings"] == []
    assert search["coverage"]["valid_facts"] == 1


@pytest.mark.parametrize(
    "scope",
    [
        'Target creature has "Flying\nDiscard a card: Draw a card."',
        "Other creatures have the following abilities.\nFlying\nDiscard a card: Draw a card.",
        "Level up {1}\nLEVEL 2–3\nDiscard a card: Draw a card.",
        "Class\n{1}: Level 2\nDiscard a card: Draw a card.",
        "If you control a Wizard,\nDiscard a card: Draw a card.",
        "As long as you control a Wizard, this has the following ability.\nDiscard a card: Draw a card.",
        "As long as you control a Wizard,\nDiscard a card: Draw a card.",
        "Discard a card: Draw a card.\nActivate only once each turn.",
        "Whenever you discard a card, Amber Voyager deals 1 damage to each opponent.\n"
        "This ability triggers only once each turn.",
    ],
)
def test_unsupported_enclosing_scope_retains_full_span(scope):
    bundle = compile_deck_facts([card(scope)])
    assert not bundle["facts"]
    clauses = bundle["sources"][0]["clauses"]
    assert len(clauses) == 1
    assert (clauses[0]["start"], clauses[0]["end"], clauses[0]["text"]) == (
        0,
        len(scope),
        scope,
    )
    assert clauses[0]["parse_status"] == "withheld"
    assert discover_connections(bundle)["coverage"]["valid_facts"] == 0


@pytest.mark.parametrize(
    "unrelated",
    [
        "Whenever a creature dies, you may draw a card. This ability triggers only once each turn.",
        "{T}: Gain 1 life.\nActivate only as a sorcery.",
        "This creature has flying.",
    ],
)
def test_unrelated_restrictions_do_not_hide_independent_ability(unrelated):
    text = unrelated + "\nDiscard a card: Draw a card."
    bundle = compile_deck_facts([card(text)])
    assert len(bundle["facts"]) == 1
    fact = bundle["facts"][0]
    assert fact["quote"] == "Discard a card: Draw a card."
    assert fact["start"] == len(unrelated) + 1
    assert discover_connections(bundle)["coverage"]["invalid_bindings"] == []


@pytest.mark.parametrize(
    "scope",
    [
        "Whenever a creature dies,\nDraw a card.",
        "When a creature enters,\nDraw a card.",
        "At the beginning of your upkeep,\nDraw a card.",
        "{T}:\nDraw a card.",
    ],
)
def test_wrapped_ability_does_not_escape_as_independent_spell(scope):
    bundle = compile_deck_facts([card(scope, type_line="Sorcery")])
    assert not bundle["facts"]
    assert bundle["sources"][0]["clauses"][0]["text"] == scope
    assert not bundle["coverage"]["complete"]
    assert discover_connections(bundle)["coverage"]["valid_facts"] == 0


def test_closed_multiline_quote_preserves_following_independent_spell():
    grant = "Target creature has “Flying\nDiscard a card: Draw a card.”"
    record = card(grant + "\nDraw a card.", type_line="Instant")
    bundle = compile_deck_facts([record])
    assert len(bundle["facts"]) == 1
    assert bundle["facts"][0]["quote"] == "Draw a card."
    assert bundle["facts"][0]["start"] == len(grant) + 1
    assert bundle["sources"][0]["clauses"][0]["text"] == grant
    assert bundle["sources"][0]["clauses"][0]["parse_status"] == "withheld"
    assert discover_connections(bundle)["coverage"]["valid_facts"] == 1


@pytest.mark.parametrize("identity_field", ["id", "source_id", None])
def test_conflicting_logical_rule_identity_withholds_both_and_search_dependency(
    identity_field,
):
    original = rule(
        **({identity_field: "explore-definition"} if identity_field else {})
    )
    altered = {
        **original,
        "oracle_text": RULE_TEXT.replace("into their hand", "into their graveyard"),
    }
    cards = [card("{T}: Amber Voyager explores.")]
    bundle = compile_deck_facts(cards, {"sources": [original, altered]})
    assert bundle == compile_deck_facts(cards, {"sources": [altered, original]})
    assert [f["kind"] for f in bundle["facts"]] == ["activated"]
    assert bundle["facts"][0]["required_rules"] == ["explore"]
    assert bundle["coverage"]["unresolved_rules"] == ["explore"]
    assert not bundle["coverage"]["complete"]
    sources = [s for s in bundle["sources"] if s["source_id"].startswith("rule:")]
    assert len({s["source_id"] for s in sources}) == 2
    assert {s["source_sha256"] for s in sources} == {digest(original), digest(altered)}
    for source in sources:
        assert source["copied_fields"] in [original, altered]
        clause = source["clauses"][0]
        assert clause["text"] == source["oracle_text"]
        assert clause["parse_status"] == "withheld"
        assert "conflicting_duplicate_identity" in clause["issues"]
    search = discover_connections(bundle)
    assert search["coverage"]["invalid_bindings"] == []
    assert search["coverage"]["valid_facts"] == 1
    assert [gap["required_rule"] for gap in search["dependency_gaps"]] == ["explore"]


@pytest.mark.parametrize("combat", ["", "combat "])
def test_compiled_noncombat_pirate_damage_only_joins_unrestricted_trigger(combat):
    cards = [
        card("Discard a card: Draw a card.", identity="cost"),
        card(
            "Whenever you discard a card, Violet Beacon deals 1 damage to each opponent.",
            "Violet Beacon",
            "damage",
        ),
        card(
            f"Whenever one or more Pirates you control deal {combat}damage to a player, Copper Seeker explores.",
            "Copper Seeker",
            "explore",
        ),
    ]
    bundle = compile_deck_facts(cards, rule())
    damage = next(f for f in bundle["facts"] if f["source_id"] == "card:damage")
    target = next(f for f in bundle["facts"] if f["source_id"] == "card:explore")
    assert damage["outputs"][0]["combat"] is False
    assert target["inputs"][0]["combat_only"] is bool(combat)
    search = discover_connections(bundle)
    assert search["coverage"]["invalid_bindings"] == []
    assert search["coverage"]["valid_facts"] == 4
    links = [link for candidate in search["candidates"] for link in candidate["links"]]
    assert any(link["to_fact_id"] == damage["fact_id"] for link in links)
    damage_joins = [
        link
        for link in links
        if link["from_fact_id"] == damage["fact_id"]
        and link["to_fact_id"] == target["fact_id"]
    ]
    assert bool(damage_joins) is (not bool(combat))
    assert search["dependency_gaps"] == []
    assert all(candidate["verified"] is False for candidate in search["candidates"])


@pytest.mark.parametrize(
    "type_line,reference",
    [
        ("Creature — Pirate", "this creature"),
        ("Artifact — Equipment", "This artifact"),
        ("Legendary Artifact Creature — Pirate", "this artifact"),
        ("Legendary Artifact Creature — Pirate", "This creature"),
        ("Enchantment", "this permanent"),
        ("Land", "THIS PERMANENT"),
        ("Planeswalker — Test", "this permanent"),
        ("Battle — Siege", "this permanent"),
    ],
)
def test_generic_self_reference_is_source_bound_and_type_checked(type_line, reference):
    record = card(
        f"Whenever you discard a card, {reference} deals 1 damage to each opponent.",
        name="Renamed Source",
        type_line=type_line,
    )
    bundle = compile_deck_facts([record])
    fact = bundle["facts"][0]
    assert bundle["coverage"]["complete"]
    assert fact["quote"] == record["oracle_text"]
    assert fact["inputs"][0]["actor"] == "you"
    assert fact["outputs"] == [
        {
            "event": "damage",
            "actor": "self",
            "subject": "self",
            "source_id": "card:amber",
            "source_types": type_line,
            "recipient": "each opponent",
            "quantity": 1,
            "combat": False,
        }
    ]
    assert fact["source_sha256"] == digest(record)
    assert discover_connections(bundle)["coverage"]["invalid_bindings"] == []


@pytest.mark.parametrize(
    "text",
    [
        "Discard a card: This creature gains flying until end of turn.",
        "{R}, Discard a card: This creature gets +1/+1 until end of turn.",
        "Sacrifice this creature: Draw a card.",
        "Whenever this creature dies, draw a card.",
        "{T}: This creature explores.",
    ],
)
def test_modern_self_reference_works_across_existing_ability_grammar(text):
    bundle = compile_deck_facts([card(text)])
    fact = bundle["facts"][0]
    assert fact["quote"] == text
    assert fact["start"] == 0 and fact["end"] == len(text)
    assert fact["status"] == "bounded_interpretation"
    assert all(e.get("actor") == "you" for e in fact["inputs"] + fact["outputs"])
    if "flying" in text or "gets" in text:
        assert text.split(": ")[1] in fact["conditions"]
    if "Sacrifice" in text or "dies" in text:
        assert fact["inputs"][0]["subject"] == "self"
    if "explores" in text:
        assert fact["outputs"][0]["source_id"] == fact["source_id"]


@pytest.mark.parametrize(
    "name,reference",
    [
        ("Francisco, Fowl Marauder", "Francisco"),
        ("Saffron, Cloud Surveyor", "Saffron"),
        ("Saffron, Cloud Surveyor", "Saffron, Cloud Surveyor"),
    ],
)
def test_legendary_reference_is_derived_from_source_name(name, reference):
    text = f"Whenever one or more Pirates you control deal damage to a player, {reference} explores."
    bundle = compile_deck_facts(
        [card(text, name=name, type_line="Legendary Creature — Bird Pirate")], rule()
    )
    fact = next(f for f in bundle["facts"] if f["kind"] == "triggered")
    assert fact["quote"] == text
    assert fact["outputs"] == [
        {
            "event": "explore",
            "actor": "you",
            "subject": "self",
            "source_id": "card:amber",
        }
    ]
    assert fact["inputs"][0]["source_types"] == "Pirates"
    assert fact["inputs"][0]["combat_only"] is False
    assert bundle["coverage"]["complete"]


@pytest.mark.parametrize(
    "type_line,reference",
    [
        ("Artifact", "this creature"),
        ("Creature — Artificer", "this artifact"),
        ("Instant", "this permanent"),
        ("Sorcery", "this creature"),
        ("Kindred — Creature", "this creature"),
        ("Creature — Pirate", "another creature"),
        ("Legendary Creature — Pirate", "Other Card"),
        ("Legendary Creature — Pirate", "Saff"),
        ("Legendary Creature — Pirate", "Saffron Extra"),
        ("Creature — Pirate", "Saffron"),
    ],
)
def test_wrong_type_other_name_and_prefix_references_abstain(type_line, reference):
    record = card(
        f"Whenever you discard a card, {reference} deals 1 damage to each opponent.",
        name="Saffron, Cloud Surveyor",
        type_line=type_line,
    )
    bundle = compile_deck_facts([record])
    assert not bundle["facts"]
    assert not bundle["coverage"]["complete"]
    assert bundle["sources"][0]["copied_fields"] == record


@pytest.mark.parametrize(
    "other_name", ["Saffron", "Saffron, Cave Surveyor", "saffron, Cave Surveyor"]
)
def test_ambiguous_short_name_abstains_but_full_name_still_matches(other_name):
    name = "Saffron, Cloud Surveyor"
    records = [
        card(
            "{T}: Saffron explores.", name=name, type_line="Legendary Creature — Pirate"
        ),
        card("Flying", name=other_name, identity="other"),
    ]
    assert not compile_deck_facts(records)["facts"]
    records[0]["oracle_text"] = f"{{T}}: {name} explores."
    assert len(compile_deck_facts(records)["facts"]) == 1


def test_parenthetical_threshold_condition_keeps_independent_discard_activation():
    threshold = (
        "Threshold — This creature gets +2/+2. "
        "(As long as seven or more cards are in your graveyard, it has threshold.)"
    )
    ability = "Discard a card: This creature gains flying until end of turn."
    text = threshold + "\n" + ability
    bundle = compile_deck_facts([card(text)])
    assert len(bundle["facts"]) == 1
    fact = bundle["facts"][0]
    assert fact["quote"] == ability
    assert fact["start"] == len(threshold) + 1
    assert text[fact["start"] : fact["end"]] == ability
    assert fact["inputs"][0]["event"] == "discard"
    assert bundle["coverage"]["withheld"] == 1


@pytest.mark.parametrize("separator", [" ", "\n"])
def test_costly_restricted_draw_activation_never_loses_restriction(separator):
    ability = "{3}, Discard a card: Draw two cards."
    text = ability + separator + "Activate only if this creature attacked this turn."
    bundle = compile_deck_facts([card(text)])
    assert not bundle["facts"]
    assert bundle["sources"][0]["clauses"][0]["text"] == text


@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_modern_renamed_chain_integrates_search_with_both_rule_forms(permanent, batch):
    discard = "one or more cards" if batch else "a card"
    damage = "that much" if batch else "1"
    cards = [
        card(
            "Discard a card: This creature gains flying until end of turn.",
            name="Renamed Glider",
            identity="cost",
        ),
        card(
            f"Whenever you discard {discard}, this creature deals {damage} damage to each opponent.",
            name="Renamed Beacon",
            identity="damage",
        ),
        card(
            "Whenever one or more Pirates you control deal damage to a player, Saffron explores.",
            name="Saffron, Cloud Surveyor",
            identity="explore",
            type_line="Legendary Creature — Bird Pirate",
        ),
    ]
    supplied_rule = rule()
    if permanent:
        supplied_rule["oracle_text"] = (
            "Certain abilities instruct a permanent to explore. "
            "To do so, that permanent’s controller reveals the top card of their library. "
            "If a land card is revealed this way, that player puts that card into their hand. "
            "Otherwise, that player puts a +1/+1 counter on the exploring permanent, "
            "then may put the revealed card into their graveyard."
        )
    bundle = compile_deck_facts(cards, supplied_rule)
    assert bundle["coverage"]["complete"]
    assert len(bundle["facts"]) == 4
    damage_fact = next(f for f in bundle["facts"] if f["source_id"] == "card:damage")
    assert damage_fact["inputs"][0]["quantity"] == ("one_or_more" if batch else 1)
    assert damage_fact["outputs"][0]["quantity"] == (
        "discarded_card_count" if batch else 1
    )
    assert damage_fact["conditions"][-1] == (
        "Once per discard batch." if batch else "Once per discarded card."
    )
    for fact in bundle["facts"]:
        source = next(
            s for s in bundle["sources"] if s["source_id"] == fact["source_id"]
        )
        assert source["oracle_text"][fact["start"] : fact["end"]] == fact["quote"]
        assert (
            source["source_sha256"]
            == fact["source_sha256"]
            == digest(source["copied_fields"])
        )
    search = discover_connections(bundle)
    assert search["coverage"]["invalid_bindings"] == []
    assert search["coverage"]["valid_facts"] == 4
    assert search["dependency_gaps"] == []
    cycle = next(
        c
        for c in search["candidates"]
        if c["cycle_detected"] and len(c["fact_ids"]) == 4
    )
    assert "revealed card is a land" in cycle["assumptions"]
    assert any("actor binding" in a for a in cycle["assumptions"])
    assert any("Finite fuel" in u for u in cycle["unresolved"])
    assert cycle["verified"] is False


@pytest.mark.parametrize("reference", ["Saffron", "Saffron, Cloud Surveyor"])
def test_comma_in_canonical_sacrifice_cost_retains_exact_cost_tokens(reference):
    cost = "Sacrifice " + reference
    record = card(
        "{R}, " + cost + ": Draw a card.",
        name="Saffron, Cloud Surveyor",
        type_line="Legendary Creature — Pirate",
    )
    fact = compile_deck_facts([record])["facts"][0]
    assert fact["costs"] == [
        {"kind": "printed_symbols", "text": "{R}"},
        {"kind": "sacrifice", "text": cost, "quantity": 1},
    ]
    assert fact["inputs"][0]["subject"] == "self"
    assert fact["quote"] == record["oracle_text"]


@pytest.mark.parametrize(
    "text",
    [
        'Other creatures have "Discard a card: This creature gains flying until end of turn."',
        "Other creatures gain the following ability.\nDiscard a card: This creature gains flying until end of turn.",
        "Level up {1}\nLEVEL 2–3\nDiscard a card: This creature gains flying until end of turn.",
        "As long as you control a Wizard,\nDiscard a card: This creature gains flying until end of turn.",
        "Whenever you discard a card, this creature deals 1 damage to each opponent.\nThis ability triggers only once each turn.",
    ],
)
def test_generic_references_preserve_granted_level_and_restriction_guards(text):
    bundle = compile_deck_facts([card(text)])
    assert not bundle["facts"]
    assert len(bundle["sources"][0]["clauses"]) == 1
    assert bundle["sources"][0]["clauses"][0]["text"] == text


def test_current_permanent_explore_rule_wording_keeps_exact_source():
    text = (
        "701.44a Certain spells and abilities instruct a permanent to explore. "
        "To do so, that permanent’s controller reveals the top card of their library. "
        "If a land card is revealed this way, that player puts that card into their hand. "
        "Otherwise, that player puts a +1/+1 counter on the exploring permanent and may put "
        "the revealed card into their graveyard."
    )
    source = {
        "source_id": "CR 701.44a",
        "kind": "rule",
        "source": "supplied snapshot",
        "version": "test",
        "complete": True,
        "text": text,
    }
    result = compile_deck_facts([], {"sources": [source]})
    assert len(result["facts"]) == 1
    assert result["facts"][0]["quote"] == text
    assert result["facts"][0]["kind"] == "rule_definition"


@pytest.mark.parametrize(
    "restriction",
    [
        " Activate only once each turn.",
        "You may activate this ability only once each turn.",
        "\tActivate only if you control another creature.",
    ],
)
def test_following_restriction_stays_inside_exact_ability_scope(restriction):
    text = "Discard a card: Draw a card.\n" + restriction
    bundle = compile_deck_facts([card(text)])
    assert not bundle["facts"]
    assert bundle["sources"][0]["clauses"][0]["text"] == text
    assert not bundle["coverage"]["complete"]


def test_search_cannot_resolve_compiler_declared_rule_edition_ambiguity():
    first = rule()
    second = {**first, "version": "another-edition"}
    bundle = compile_deck_facts(
        [card("{T}: Amber Voyager explores.")], {"sources": [first, second]}
    )
    assert bundle["coverage"]["unresolved_rules"] == ["explore"]
    result = discover_connections(bundle)
    assert any(gap["required_rule"] == "explore" for gap in result["dependency_gaps"])
    assert all(
        any(
            "Required rule unresolved: explore" in issue
            for issue in candidate["unresolved"]
        )
        for candidate in result["candidates"]
    )
