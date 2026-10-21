"""Source-bound regression evidence, not a simulator or strategic-fit benchmark."""

from copy import deepcopy
from hashlib import sha256

import pytest

from sabermetrics.assistant_mana_evidence import (
    KINNAN_ACTIVATION,
    KINNAN_ORACLE_ID,
    KINNAN_TRIGGER,
    build_mana_evidence,
    card_source_hash,
)

# Complete bodies from the supplied local official 2026-09-25 CR snapshot and
# Kinnan's supplied Wizards ruling. They are not held-out evaluation labels.
CR12 = "106.12. To “tap [a permanent] for mana” is to activate a mana ability of that permanent that includes the {T} symbol in its activation cost. See rule 605, “Mana Abilities.”"
CR12A = "106.12a An ability that triggers whenever a permanent “is tapped for mana” or is tapped for mana of a specified type triggers whenever such a mana ability resolves and produces mana or the specified type of mana."
RULING = "Kinnan doesn’t care about any restrictions or riders the nonland permanent put on the mana it produced. The additional mana Kinnan produces won’t have any restrictions or riders."
HALFLING = "{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only to cast a legendary spell, and that spell can't be countered."
HAWKER = "{T}: Add {C}{U}. Spend this mana only to activate abilities."


def seal(card):
    card["source_sha256"] = card_source_hash(card)
    return card


def card(name, text, oracle_id=None, **changes):
    return seal(
        {
            "id": name + "-printing",
            "oracle_id": name + "-oracle" if oracle_id is None else oracle_id,
            "name": name,
            "oracle_text": text,
            "layout": "normal",
            "mana_cost": "{G}",
            "type_line": "Creature — Elf Druid",
            "is_current": True,
            "oracle_text_complete": True,
            **changes,
        }
    )


def hit(source_id, text, kind="rule", **changes):
    return {
        "kind": kind,
        "source_id": source_id,
        "text": text,
        "text_truncated": False,
        "complete": True,
        "is_current": True,
        "version": "2026-09-25",
        "source": "wotc",
        "source_url": "https://media.wizards.com/2026/official-rules.txt",
        "content_sha256": sha256(text.encode()).hexdigest(),
        **changes,
    }


@pytest.fixture
def inputs():
    kinnan = card(
        "Kinnan, Bonder Prodigy",
        KINNAN_TRIGGER + "\n" + KINNAN_ACTIVATION,
        KINNAN_ORACLE_ID,
        mana_cost="{G}{U}",
        type_line="Legendary Creature — Human Druid",
    )
    deck = {
        "revision": 7,
        "entries": [
            {
                "card_id": kinnan["id"],
                "oracle_id": KINNAN_ORACLE_ID,
                "is_commander": True,
            }
        ],
    }
    rules = {
        "hits": [
            hit("CR 106.12", CR12),
            hit("CR 106.12a", CR12A),
            hit(
                "Kinnan ruling 2020-04-17",
                RULING,
                "card_ruling",
                card_oracle_id=KINNAN_ORACLE_ID,
                source_url="https://api.scryfall.com/cards/kinnan/rulings",
            ),
        ]
    }
    return deck, kinnan, rules


def build(inputs, source):
    deck, commander, rules = inputs
    return build_mana_evidence(deck, [commander, source], rules)["rows"][0]


def test_halfling_keeps_restriction_and_conditional_spell_benefit_on_source(inputs):
    source = card("Delighted Halfling", HALFLING)
    row = build(inputs, source)
    assert row["status"] == "bounded_conditional_evidence"
    colorless, colored = row["abilities"]
    assert colorless["source_mana"]["type_options"] == ["C"]
    assert colored["source_mana"]["constraints"][0]["kind"] == "legendary_spell_only"
    benefit = colored["source_mana"]["spell_benefit"]
    assert benefit["kind"] == "spell_cant_be_countered"
    assert "Spend this source mana" in benefit["condition"]
    extra = colored["kinnan_extra_mana"]
    assert extra["quantity"] == 1 and extra["possible_types"] == list("WUBRG")
    assert extra["inherits_source_spending_restriction"] is False
    assert extra["inherits_source_spell_benefit"] is False
    assert extra["producer_oracle_id"] == KINNAN_ORACLE_ID
    assert "unrestricted" not in str(row)
    for ability in row["abilities"]:
        assert HALFLING[ability["char_start"] : ability["char_end"]] == ability["quote"]
    assert row["source_oracle_text"] == HALFLING
    assert row["commander_oracle_text"] == inputs[1]["oracle_text"]
    assert row["source_casting_cost"] == "{G}"
    assert row["commander_casting_cost"] == "{G}{U}"


def test_hawker_has_two_restricted_source_mana_plus_one_separate_choice(inputs):
    row = build(inputs, card("Omen Hawker", HAWKER, mana_cost="{U}"))
    ability = row["abilities"][0]
    assert ability["source_mana"]["quantity"] == 2
    assert ability["source_mana"]["type_options"] == ["C", "U"]
    assert (
        ability["source_mana"]["constraints"][0]["kind"] == "activated_abilities_only"
    )
    assert ability["kinnan_extra_mana"]["quantity"] == 1
    assert ability["kinnan_extra_mana"]["possible_types"] == ["C", "U"]
    assert ability["activation_cost"] == "{T}"
    assert "{5}{G}{U}" in row["commander_oracle_text"]
    assert any(
        "outlet, not payment or availability" in claim
        for claim in row["admitted_claims"]
    )
    assert "availability" in " ".join(row["conditions"]) or "unverified" in " ".join(
        row["conditions"]
    )
    assert not any(k in row for k in ("score", "fit", "available_mana", "playable_now"))


def test_mystic_preserves_contribution_without_discharging_creature_tap_condition(
    inputs,
):
    row = build(inputs, card("Elvish Mystic", "{T}: Add {G}."))
    assert row["admitted_claims"]
    ability = row["abilities"][0]
    assert ability["source_mana"]["type_options"] == ["G"]
    assert ability["kinnan_extra_mana"]["possible_types"] == ["G"]
    assert any(
        "creature tap eligibility" in c and "unverified" in c for c in row["conditions"]
    )


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_complete_rule_dependency_is_required(inputs, index):
    inputs[2]["hits"].pop(index)
    row = build(inputs, card("Omen Hawker", HAWKER))
    assert row["status"] == "unknown"
    assert not row["admitted_claims"]
    assert "kinnan_extra_mana" not in row["abilities"][0]
    assert row["abilities"][0]["source_mana"]["constraints"]


@pytest.mark.parametrize(
    "change",
    [
        {"text_truncated": True},
        {"complete": False},
        {"is_current": False},
        {"content_sha256": "0" * 64},
        {"version": ""},
        {"source_url": "https://media.wizards.com.evil.test/rules"},
        {"source_url": "http://media.wizards.com/rules"},
    ],
)
def test_invalid_rule_provenance_never_admits_correction(inputs, change):
    inputs[2]["hits"][0].update(change)
    assert not build(inputs, card("Omen Hawker", HAWKER))["admitted_claims"]


@pytest.mark.parametrize(
    "change",
    [
        {"card_oracle_id": "other-card"},
        {"source": "mtgapi"},
        {"source_url": "https://example.com/ruling"},
        {"text": "Source restrictions apply to the additional mana."},
    ],
)
def test_ruling_must_be_official_exact_and_bound_to_kinnan(inputs, change):
    inputs[2]["hits"][2].update(change)
    assert not build(inputs, card("Omen Hawker", HAWKER))["admitted_claims"]


def test_valid_hash_does_not_admit_inapplicable_or_contradictory_rule(inputs):
    inputs[2]["hits"][0] = hit("CR 106.12", "Not a mana ability definition.")
    assert build(inputs, card("Mystic", "{T}: Add {G}."))["status"] == "unknown"
    inputs[2]["hits"].append(hit("CR 106.12", CR12))
    row = build(inputs, card("Mystic", "{T}: Add {G}."))
    assert "conflicting_current_CR_106.12" in row["unknowns"]
    assert not row["admitted_claims"]


def test_mixed_rule_editions_abstain(inputs):
    inputs[2]["hits"][1]["version"] = "other-edition"
    row = build(inputs, card("Mystic", "{T}: Add {G}."))
    assert "mixed_rules_snapshots" in row["unknowns"]


@pytest.mark.parametrize(
    "changes",
    [
        {"card_faces": [{"oracle_text": "{T}: Add {G}."}]},
        {"layout": "modal_dfc"},
        {"layout": "leveler"},
        {"oracle_text_complete": False},
        {"oracle_text_truncated": True},
        {"is_current": False},
        {"oracle_id": ""},
        {"oracle_id": 42},
        {"id": 42},
        {"mana_cost": ""},
        {"type_line": "Land Creature — Forest Dryad"},
        {"type_line": "Instant"},
    ],
)
def test_unsupported_or_incomplete_card_shapes_abstain(inputs, changes):
    row = build(inputs, card("Mystic", "{T}: Add {G}.", **changes))
    assert row["status"] == "unknown" and not row["admitted_claims"]


@pytest.mark.parametrize(
    "text",
    [
        "{T}: Choose one —\n• Add {G}.",
        "Level up {1}\n{T}: Add {G}.",
        "{T}, Pay 1 life: Add {G}.",
        "{T}: Add {G}. Activate only once each turn.",
        "{T}: Add {G}. Spend this mana only to cast creature spells.",
        "{T}: Add {G}. If you control a Dinosaur, draw a card.",
        "Add {G}.",
        "{T}: Add {G}.\nFlying",
    ],
)
def test_unparsed_abilities_never_generalize_into_mana_claims(inputs, text):
    row = build(inputs, card("Unsupported", text))
    assert row["status"] == "unknown" and not row["admitted_claims"]


def test_source_and_rule_changes_invalidate_evidence_identity(inputs):
    source = card("Mystic", "{T}: Add {G}.")
    first = build(inputs, source)
    source["oracle_text"] = "{T}: Add {U}."
    stale = build(inputs, source)
    assert "card_source_hash_mismatch" in stale["unknowns"]
    assert stale["evidence_id"] != first["evidence_id"]
    seal(source)
    fresh = build(inputs, source)
    assert fresh["status"] == "bounded_conditional_evidence"
    assert fresh["evidence_id"] != first["evidence_id"]
    inputs[2]["hits"][2]["version"] = "new-ruling-snapshot"
    assert build(inputs, source)["evidence_id"] != fresh["evidence_id"]


def test_deck_and_commander_identity_binding_is_required(inputs):
    source = card("Mystic", "{T}: Add {G}.")
    inputs[0]["entries"][0]["card_id"] = "wrong-printing"
    row = build(inputs, source)
    assert "kinnan_not_bound_to_deck_commander" in row["unknowns"]
    assert not row["admitted_claims"]


def test_changed_commander_and_duplicate_identities_abstain(inputs):
    source = card("Mystic", "{T}: Add {G}.")
    deck, commander, rules = inputs
    original = deepcopy(commander)
    commander["oracle_text"] = commander["oracle_text"].replace("nonland", "land")
    seal(commander)
    assert "unsupported_kinnan_oracle" in build(inputs, source)["unknowns"]
    rows = build_mana_evidence(deck, [original, original, source], rules)["rows"]
    assert not rows[0]["admitted_claims"]
    rows = build_mana_evidence(deck, [original, source, source], rules)["rows"]
    assert all("ambiguous_source_identity" in r["unknowns"] for r in rows)


def test_results_are_detached_deterministic_and_do_not_mutate_input(inputs):
    source = card("Mystic", "{T}: Add {G}.")
    original = deepcopy((inputs, source))
    first = build(inputs, source)
    assert build(inputs, source) == first
    first["rules"][0]["text"] = "mutated"
    first["abilities"][0]["source_mana"]["type_options"].clear()
    assert (inputs, source) == original
    assert build(inputs, source)["abilities"][0]["source_mana"]["type_options"] == ["G"]


def test_two_current_snapshots_for_same_rule_abstain_even_with_equal_text(inputs):
    inputs[2]["hits"].append(hit("CR 106.12", CR12, version="another-snapshot"))
    row = build(inputs, card("Mystic", "{T}: Add {G}."))
    assert "ambiguous_current_snapshot_CR_106.12" in row["unknowns"]
    assert not row["admitted_claims"]


def test_complete_evidence_identity_changes_when_casting_cost_changes(inputs):
    source = card("Mystic", "{T}: Add {G}.")
    first = build(inputs, source)
    source["mana_cost"] = "{1}{G}"
    assert not build(inputs, source)["admitted_claims"]
    seal(source)
    row = build(inputs, source)
    assert row["source_casting_cost"] == "{1}{G}"
    assert row["source"]["source_sha256"] != first["source"]["source_sha256"]
    assert row["evidence_id"] != first["evidence_id"]
