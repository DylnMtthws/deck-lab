import copy

import pytest

from sabermetrics import db
from sabermetrics.assistant_deck_intelligence import (
    assess_deck_requirements,
    recommend_replacements,
)
from sabermetrics.deck_documents import DeckDocumentRepo
from tests.test_resimulate_delta import _client, _seed_deck


@pytest.fixture
def prepared(tmp_path):
    _, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    cards = [
        ("draw", "Draw Engine", "Artifact", "{T}: Draw a card."),
        ("ramp", "Mana Engine", "Artifact", "{T}: Add {C}{C}."),
        (
            "protect",
            "Protect Engine",
            "Artifact",
            "Creatures you control have hexproof.",
        ),
        ("foreign", "Foreign Engine", "Artifact", "{T}: Draw two cards."),
        ("illegal", "Illegal Engine", "Artifact", "{T}: Draw three cards."),
    ]
    with db.connect(path) as conn:
        for cid, name, kind, text in cards:
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (
                    cid,
                    cid,
                    name,
                    kind,
                    text,
                    '["R"]' if cid == "foreign" else "[]",
                    2,
                    cid != "illegal",
                ),
            )
        conn.commit()
    return path, DeckDocumentRepo(path).get(owner, deck)


def test_unknown_commander_explicit_general_fallback(prepared):
    _, document = prepared
    report = assess_deck_requirements(document)
    assert report["specialized_status"] == "unavailable"
    assert report["archetype"] == "general"
    assert (
        next(r for r in report["requirements"] if r["requirement"] == "draw")["gap"]
        == 12
    )


def test_commander_shape_not_name_and_quantity(prepared):
    _, document = prepared
    commander = next(e for e in document["entries"] if e["is_commander"])
    commander["oracle_text"] = (
        "Whenever you cast a noncreature spell, this creature deals 1 damage to each opponent."
    )
    report = assess_deck_requirements(document, {"theme": "Curiosity"})
    assert report["archetype"] == "cast_damage_draw"
    assert (
        next(r for r in report["requirements"] if r["requirement"] == "land")["actual"]
        == 99
    )


def test_landfall_requires_trigger_same_clause(prepared):
    _, document = prepared
    commander = next(e for e in document["entries"] if e["is_commander"])
    commander["oracle_text"] = (
        "Whenever a land enters, you gain 1 life. {T}: Draw a card."
    )
    report = assess_deck_requirements(document)
    assert report["archetype"] == "landfall"
    assert (
        next(r for r in report["requirements"] if r["requirement"] == "landfall_cards")[
            "actual"
        ]
        == 0
    )


def test_recommendations_distinct_legal_diverse_and_nonmutating(prepared):
    path, document = prepared
    before = copy.deepcopy(document)
    result = recommend_replacements(path, document)
    assert result["status"] == "ready"
    assert len(result["alternatives"]) == 3
    assert (
        len({a["replacements"][0]["incoming_card_id"] for a in result["alternatives"]})
        == 3
    )
    assert {
        a["replacements"][0]["incoming_card_id"] for a in result["alternatives"]
    } == {"draw", "ramp", "protect"}
    assert all(len(a["replacements"]) == 1 for a in result["alternatives"])
    assert document == before
    assert all(
        e["semantic_verification"] == "unverified" for e in result["candidate_evidence"]
    )


def test_protected_baseline_produces_no_padded_alternatives(prepared):
    path, document = prepared
    result = recommend_replacements(
        path, document, {"protected_cards": ["card-island"]}
    )
    assert result["status"] == "insufficient_candidates"
    assert result["alternatives"] == []


def test_budget_unknown_fails_closed(prepared):
    path, document = prepared
    result = recommend_replacements(path, document, {"budget": 100})
    assert result["alternatives"] == []
    assert any("Budget cannot be verified" in reason for reason in result["rejections"])


def test_focus_prioritizes_function(prepared):
    path, document = prepared
    result = recommend_replacements(path, document, focus="Improve draw")
    assert result["alternatives"][0]["replacements"][0]["incoming_card_id"] == "draw"


def test_budget_full_deck_ceiling(prepared):
    path, document = prepared
    with db.connect(path) as conn:
        for row in conn.execute("SELECT id FROM cards").fetchall():
            conn.execute(
                "INSERT INTO card_prices(card_id,snapshot_date,price_usd) VALUES(?,?,?)",
                (row["id"], "2026-10-14", 2),
            )
        conn.commit()
    assert recommend_replacements(path, document, {"budget": 199})["alternatives"] == []
    result = recommend_replacements(path, document, {"budget": 200})
    assert result["status"] == "ready"
    assert all(e["price_total_usd"] == 200 for e in result["candidate_evidence"])


def test_every_claimed_gap_has_actual_net_improvement(prepared):
    from sabermetrics.assistant_experiments import ExperimentService

    path, document = prepared
    service = ExperimentService(path)
    result = recommend_replacements(path, document)
    before = {
        r["requirement"]: r["actual"] for r in result["assessment"]["requirements"]
    }
    for proposal, evidence in zip(result["alternatives"], result["candidate_evidence"]):
        frozen = service._candidate(
            document, proposal["replacements"], service._constraints({})
        )["document"]
        after = {
            r["requirement"]: r["actual"]
            for r in assess_deck_requirements(frozen)["requirements"]
        }
        assert all(after[role] > before[role] for role in evidence["requirements"])


def test_off_color_results_do_not_starve_bounded_pool(prepared):
    path, document = prepared
    with db.connect(path) as conn:
        for i in range(105):
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"x{i}",
                    f"x{i}",
                    f"AAA{i}",
                    "Artifact",
                    "Draw a card.",
                    '["R"]',
                    0,
                    1,
                ),
            )
        conn.commit()
    result = recommend_replacements(path, document, focus="draw")
    assert result["alternatives"][0]["replacements"][0]["incoming_card_id"] == "draw"


def test_recovery_focus_does_not_substitute_other_roles(prepared):
    path, document = prepared
    result = recommend_replacements(path, document, focus="recovery")
    assert result["alternatives"] == []
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
            (
                "recover",
                "recover",
                "Recovery",
                "Artifact",
                "Return target card from your graveyard to your hand.",
                "[]",
                2,
                1,
            ),
        )
        conn.commit()
    result = recommend_replacements(path, document, focus="recovery")
    assert result["alternatives"][0]["replacements"][0]["incoming_card_id"] == "recover"
    assert result["candidate_evidence"][0]["requirements"] == ["recursion"]


@pytest.mark.parametrize(
    "name,kind,text,cmc,expected,absent",
    [
        (
            "Darksteel Ingot",
            "Artifact",
            "Indestructible\n{T}: Add one mana of any color.",
            3,
            {"ramp", "direct_mana_source"},
            {"protection"},
        ),
        (
            "Diamond Pick-Axe",
            "Artifact — Equipment",
            'Indestructible (Effects that say "destroy" don’t destroy this Equipment.)\nEquipped creature gets +1/+1 and has "Whenever this creature attacks, create a Treasure token." (It’s an artifact with "{T}, Sacrifice this token: Add one mana of any color.")\nEquip {2}',
            1,
            {"conditional_mana"},
            {"ramp", "protection", "direct_mana_source"},
        ),
        (
            "Arcane Signet",
            "Artifact",
            "{T}: Add one mana of any color in your commander’s color identity.",
            2,
            {"ramp", "direct_mana_source"},
            {"protection"},
        ),
        (
            "Slip Out the Back",
            "Instant",
            "Put a +1/+1 counter on target creature. It phases out. (Treat it and anything attached to it as though they don’t exist until its controller’s next turn.)",
            1,
            {"protection"},
            {"ramp"},
        ),
        (
            "Self Shield",
            "Creature",
            "Hexproof\nIndestructible",
            1,
            set(),
            {"protection"},
        ),
    ],
)
def test_scoped_protection_and_mana_facts(name, kind, text, cmc, expected, absent):
    from sabermetrics.assistant_deck_intelligence import _features, _mana_conditions

    card = {"name": name, "type_line": kind, "oracle_text": text, "cmc": cmc}
    features = _features(card)
    assert expected <= features
    assert not features & absent
    if name == "Diamond Pick-Axe":
        assert _mana_conditions(card) == [
            "combat_required",
            "equipped_creature_required",
        ]


def test_mana_focus_ranks_actual_rock_ahead_of_combat_equipment(prepared):
    path, document = prepared
    with db.connect(path) as conn:
        for cid, text, cmc in [
            (
                "signet",
                "{T}: Add one mana of any color in your commander’s color identity.",
                2,
            ),
            ("ingot", "Indestructible\n{T}: Add one mana of any color.", 3),
            (
                "pickaxe",
                'Indestructible\nEquipped creature gets +1/+1 and has "Whenever this creature attacks, create a Treasure token." (It’s an artifact with "{T}, Sacrifice this token: Add one mana of any color.")\nEquip {2}',
                1,
            ),
        ]:
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (cid, cid, cid, "Artifact", text, "[]", cmc, 1),
            )
        conn.commit()
    result = recommend_replacements(path, document, focus="mana")
    ids = [p["replacements"][0]["incoming_card_id"] for p in result["alternatives"]]
    assert "pickaxe" not in ids
    assert ids.index("signet") < ids.index("ingot")
    assert all(
        "protection" not in e["requirements"] for e in result["candidate_evidence"]
    )
