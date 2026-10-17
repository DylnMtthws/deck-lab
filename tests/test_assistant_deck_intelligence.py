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


def test_engine_link_ranking_prefers_filler_cut_before_trigger_multiplier(prepared):
    path, document = prepared
    commander = next(c for c in document["entries"] if c["is_commander"])
    commander.update(
        type_line="Legendary Creature — Wizard",
        oracle_text="Whenever you cast a noncreature spell, it deals 1 damage to each opponent.",
    )
    library = next(c for c in document["entries"] if not c["is_commander"])
    library["quantity"] = 97
    for cid, name, text in [
        (
            "multiplier",
            "A core multiplier",
            "If an ability of a Wizard you control triggers, that ability triggers an additional time.",
        ),
        ("filler", "Z filler", "Whenever you gain life, you may gain 1 life."),
    ]:
        extra = copy.deepcopy(library)
        extra.update(
            id=cid + "-entry",
            card_id=cid,
            oracle_id=cid,
            quantity=1,
            name=name,
            type_line="Creature",
            cmc=3,
            oracle_text=text,
        )
        document["entries"].append(extra)
        with db.connect(path) as conn:
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (cid, cid, name, "Creature", text, "[]", 3, 1),
            )
            conn.commit()
    result = recommend_replacements(path, document, focus="draw", candidate_limit=1)
    assert result["alternatives"]
    assert (
        result["alternatives"][0]["replacements"][0]["outgoing_entry_id"]
        != "multiplier-entry"
    )
    assert result["candidate_evidence"][0]["context_rank"]["matched_functions"] == [
        "draw"
    ]


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


def test_filled_draw_target_still_allows_retrieved_same_role_comparisons(prepared):
    from sabermetrics.assistant_experiments import ExperimentService

    path, document = prepared
    service = ExperimentService(path)
    island = next(c for c in document["entries"] if not c.get("is_commander"))
    with db.connect(path) as conn:
        for index in range(12):
            cid = f"old-draw-{index}"
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (
                    cid,
                    cid,
                    cid,
                    "Artifact",
                    "At the beginning of your upkeep, draw a card.",
                    "[]",
                    3,
                    1,
                ),
            )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
            (
                "new-draw",
                "new-draw",
                "New Draw",
                "Artifact",
                "At the beginning of your upkeep, draw two cards.",
                "[]",
                3,
                1,
            ),
        )
        conn.commit()
    for index in range(12):
        document = service._candidate(
            document,
            [
                {
                    "outgoing_entry_id": island["id"],
                    "incoming_card_id": f"old-draw-{index}",
                }
            ],
            service._constraints({}),
        )["document"]
    result = recommend_replacements(path, document, focus="draw", candidate_limit=8)
    assert (
        next(
            r
            for r in result["assessment"]["requirements"]
            if r["requirement"] == "draw"
        )["gap"]
        == 0
    )
    proposed = next(
        e for e in result["candidate_evidence"] if e["card_id"] == "new-draw"
    )
    assert proposed["requirements"] == []
    assert proposed["recommendation_type"] == "exploratory_strategy"
    assert proposed["review_required"] is True


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


def test_sourced_combination_loss_is_disclosed_as_preference_not_prohibition(
    prepared, monkeypatch
):
    from sabermetrics import assistant_deck_intelligence as intelligence

    path, document = prepared
    commander = next(e for e in document["entries"] if e["is_commander"])
    library = next(e for e in document["entries"] if not e["is_commander"])
    source_line = {
        "variant_id": "test-source-quantity",
        "cards": [
            {
                "name": library["name"],
                "oracle_id": library["oracle_id"],
                "quantity": 99,
            },
            {
                "name": commander["name"],
                "oracle_id": commander["oracle_id"],
                "quantity": 1,
            },
        ],
        "required_zones": [{"name": commander["name"], "must_be_commander": True}],
        "citation": {
            "source": "Test source quantity contract",
            "url": "https://example.test/quantity",
        },
    }
    monkeypatch.setattr(
        intelligence, "_eligible_catalog_lines", lambda *_: [source_line]
    )
    result = intelligence.recommend_replacements(path, document)
    assert result["status"] == "ready"
    assert len(result["alternatives"]) == 3
    for evidence in result["candidate_evidence"]:
        assert len(evidence["sourced_combination_lines_lost"]) == 1
        loss = evidence["sourced_combination_lines_lost"][0]
        assert loss["variant_id"] == "test-source-quantity"
        assert loss["execution_verified"] is False
        assert "ranking preference" in evidence["sourced_preservation_scope"]


@pytest.mark.parametrize(
    "kind",
    [
        "Artifact — Attraction",
        "Dungeon",
        "Conspiracy",
        "Scheme",
        "Plane — Ravnica",
        "Phenomenon",
        "Vanguard",
        "Token Creature",
        "Emblem",
    ],
)
def test_non_deck_objects_do_not_enter_or_starve_recommendations(prepared, kind):
    path, document = prepared
    with db.connect(path) as conn:
        for i in range(105):
            cid = f"side-{i}"
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (cid, cid, f"AAA {i}", kind, "Draw a card.", "[]", 0, 1),
            )
        conn.commit()
    result = recommend_replacements(path, document, focus="draw")
    assert result["alternatives"]
    assert all(
        p["replacements"][0]["incoming_card_id"] == "draw"
        for p in result["alternatives"]
    )


def test_generic_gap_does_not_cut_only_broad_damage_payoff(prepared):
    path, document = prepared
    library = next(e for e in document["entries"] if not e["is_commander"])
    payoff = copy.deepcopy(library)
    payoff.update(
        {
            "id": "ignition-entry",
            "card_id": "ignition",
            "oracle_id": "ignition",
            "quantity": 1,
            "name": "Chandra's Ignition",
            "type_line": "Sorcery",
            "cmc": 5,
            "mana_cost": "{3}{R}{R}",
            "oracle_text": "Target creature you control deals damage equal to its power to each other creature and each opponent.",
        }
    )
    library["quantity"] = 98
    document["entries"].append(payoff)
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
            (
                "ignition",
                "ignition",
                payoff["name"],
                "Sorcery",
                payoff["oracle_text"],
                "[]",
                5,
                1,
            ),
        )
        conn.commit()
    result = recommend_replacements(path, document)
    assert result["alternatives"]
    assert all(
        p["replacements"][0]["outgoing_entry_id"] != "ignition-entry"
        for p in result["alternatives"]
    )
    for evidence in result["candidate_evidence"]:
        assert "type_line" in evidence and "mana_cost" in evidence
        assert "commander_cast_interactions" in evidence
        assert "commander_cast_interactions" in evidence["outgoing_card"]
        assert "roles_lost" in evidence["outgoing_card"]
    from sabermetrics.assistant_runtime import _retain_swap_context

    state = {}
    _retain_swap_context(state, result)
    assert len(state["swap_review"]["candidates"]) == len(result["alternatives"])
    for retained, evidence in zip(
        state["swap_review"]["candidates"], result["candidate_evidence"]
    ):
        assert retained["incoming"]["type_line"] == evidence["type_line"]
        assert retained["outgoing"]["name"] == evidence["outgoing_card"]["name"]
        assert "commander_cast_interactions" in retained["incoming"]


def test_variable_cost_rock_separates_cheap_cast_from_productive_mana():
    from sabermetrics.assistant_deck_intelligence import _features

    caps = _features(
        {
            "name": "Astral Cornucopia",
            "type_line": "Artifact",
            "cmc": 0,
            "mana_cost": "{X}{X}{X}",
            "oracle_text": "This artifact enters with X charge counters on it.\n{T}: Choose a color. Add one mana of that color for each charge counter on this artifact.",
        }
    )
    assert "cheap_noncreature_cast" in caps
    assert "conditional_mana" in caps
    assert "ramp" not in caps
    assert "direct_mana_source" not in caps


def test_imprint_mana_is_conditional_not_baseline_ramp():
    from sabermetrics.assistant_deck_intelligence import _features, _mana_conditions

    card = {
        "type_line": "Artifact",
        "cmc": 0,
        "mana_cost": "{0}",
        "oracle_text": "Imprint — When this artifact enters, you may exile a nonartifact, nonland card from your hand.\n{T}: Add one mana of any of the exiled card’s colors.",
    }
    assert "exiled_card_required" in _mana_conditions(card)
    assert "ramp" not in _features(card)
    assert "cheap_noncreature_cast" in _features(card)


def test_unknown_multiface_is_not_a_default_cut(prepared):
    path, document = prepared
    library = next(e for e in document["entries"] if not e["is_commander"])
    library.update(
        {"type_line": "Creature — Monk // Land", "oracle_text": "", "cmc": 5}
    )
    assert recommend_replacements(path, document)["alternatives"] == []


def test_multikicker_mana_requires_investment():
    from sabermetrics.assistant_deck_intelligence import _features, _mana_conditions

    card = {
        "type_line": "Artifact",
        "cmc": 0,
        "mana_cost": "{0}",
        "oracle_text": "Multikicker {2}\nThis artifact enters with a charge counter for each time it was kicked.\n{T}: Add {C} for each charge counter on this artifact.",
    }
    assert "kicker_investment_required" in _mana_conditions(card)
    assert "ramp" not in _features(card)
    assert "cheap_noncreature_cast" in _features(card)


@pytest.mark.parametrize(
    "card, expected",
    [
        (
            {
                "type_line": "Instant",
                "cmc": 8,
                "oracle_text": "Delve\nLook at the top seven cards of your library. Put two of them into your hand and the rest on the bottom of your library in any order.",
            },
            "card_selection",
        ),
        (
            {"type_line": "Artifact", "cmc": 2, "oracle_text": "{1}, {T}: Add {U}{R}."},
            "mana_resource",
        ),
    ],
)
def test_selection_and_conditional_mana_remain_recognized_functions(card, expected):
    from sabermetrics.assistant_deck_intelligence import _features

    assert expected in _features(card)


def test_discard_land_mana_is_conditional():
    from sabermetrics.assistant_deck_intelligence import _features, _mana_conditions

    card = {
        "type_line": "Artifact",
        "cmc": 0,
        "oracle_text": "If this artifact would enter, you may discard a land card instead. If you do, put it onto the battlefield. If you don't, put it into its owner's graveyard.\n{T}: Add one mana of any color.",
    }
    assert "entry_resource_cost_required" in _mana_conditions(card)
    assert "ramp" not in _features(card)
    assert "mana_resource" in _features(card)


def test_suspend_mana_and_conditional_counter_keep_real_requirements():
    from sabermetrics.assistant_deck_intelligence import _features, _mana_conditions

    mox = {
        "type_line": "Artifact",
        "cmc": 0,
        "mana_cost": None,
        "oracle_text": "Suspend 3—{0}\n{T}: Add one mana of any color.",
    }
    assert "suspend_delay_required" in _mana_conditions(mox)
    assert "ramp" not in _features(mox)
    assert "removal" in _features(
        {
            "type_line": "Instant",
            "oracle_text": "Counter target noncreature spell unless its controller pays {1}.",
        }
    )
    assert "free_cast_access" in _features(
        {
            "type_line": "Sorcery",
            "oracle_text": "You may cast instant and sorcery spells from among them without paying their mana costs.",
        }
    )


def test_conditional_treasure_discovery_tag_is_not_baseline_ramp(prepared):
    path, document = prepared
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
            (
                "conditional-treasure",
                "conditional-treasure",
                "Conditional Treasure",
                "Artifact",
                "Whenever you discard a card, create a Treasure token. (It has '{T}, Sacrifice this artifact: Add one mana of any color.')",
                "[]",
                0,
                1,
            ),
        )
        conn.commit()
    report = recommend_replacements(path, document, focus="ramp")
    assert report["alternatives"]
    assert all(
        e["card_id"] != "conditional-treasure" for e in report["candidate_evidence"]
    )


def test_triggered_mana_engine_is_preserved_and_source_swap_does_not_fill_gap(prepared):
    from sabermetrics.assistant_deck_intelligence import _features

    assert "triggered_mana_engine" in _features(
        {
            "type_line": "Creature",
            "oracle_text": "Magecraft — Whenever you cast or copy an instant or sorcery spell, create a Treasure token.",
        }
    )
    path, document = prepared
    library = next(e for e in document["entries"] if not e["is_commander"])
    library.update(
        type_line="Artifact",
        oracle_text="{1}, {T}: Add {U}{R}.",
        mana_cost="{2}",
        cmc=2,
    )
    assert recommend_replacements(path, document, focus="ramp")["alternatives"] == []


def test_intrinsic_basic_mana_and_repeatable_trigger_are_not_lost_to_text_stripping():
    from sabermetrics.assistant_deck_intelligence import _features

    island = {"type_line": "Basic Land — Island", "oracle_text": "({T}: Add {U}.)"}
    assert {"land", "mana_resource", "direct_mana_source"} <= _features(island)
    aura = {
        "type_line": "Enchantment — Aura",
        "oracle_text": "Whenever enchanted creature deals damage to a player, return target creature that player controls to its owner's hand.",
    }
    assert "triggered_ability" in _features(aura)


def test_cheaper_role_preserving_mana_candidate_without_role_gap_gain(prepared):
    path, document = prepared
    library = next(e for e in document["entries"] if not e["is_commander"])
    library["quantity"] = 98
    old = {
        **library,
        "id": "slow-entry",
        "card_id": "slow",
        "oracle_id": "slow",
        "name": "Slow Rock",
        "quantity": 1,
        "type_line": "Artifact",
        "cmc": 3,
        "mana_cost": "{3}",
        "oracle_text": "{T}: Add one mana of any color.",
    }
    document["entries"].append(old)
    with db.connect(path) as conn:
        for cid, name, text, cmc in [
            ("slow", "Slow Rock", old["oracle_text"], 3),
            (
                "fast",
                "Fast Rock",
                "{T}: Add one mana of any color in your commander’s color identity.",
                2,
            ),
        ]:
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
                (cid, cid, name, "Artifact", text, "[]", cmc, 1),
            )
        conn.commit()
    report = recommend_replacements(path, document, focus="mana", candidate_limit=6)
    first = report["candidate_evidence"][0]
    assert first["name"] == "Fast Rock"
    assert first["outgoing_card"]["entry_id"] == "slow-entry"
    assert first["recommendation_type"] == "functional_improvement"
    assert first["requirements"] == []
    assert first["functional_improvement"]["mana_value_reduction"] == 1
    assert first["outgoing_oracle_facts"]["oracle_text"] == old["oracle_text"]
    assert report == recommend_replacements(
        path, document, focus="mana", candidate_limit=6
    )


def test_functional_candidates_do_not_conflate_mana_outputs_or_engines():
    from sabermetrics.assistant_deck_intelligence import _features
    from sabermetrics.functional_candidates import functional_improvement

    old = {"type_line": "Artifact", "cmc": 3, "oracle_text": "{T}: Add {C}{C}."}
    new = {
        "type_line": "Artifact",
        "cmc": 2,
        "oracle_text": "{T}: Add one mana of any color.",
    }
    assert functional_improvement(old, new, _features(old), _features(new)) is None
    old = {
        "type_line": "Enchantment",
        "cmc": 3,
        "oracle_text": "Whenever you cast a noncreature spell, draw a card.",
    }
    new = {"type_line": "Sorcery", "cmc": 1, "oracle_text": "Draw a card."}
    assert functional_improvement(old, new, _features(old), _features(new)) is None


def test_stored_document_generates_explicit_card_access_tradeoff(prepared):
    path, document = prepared
    deck_id = document["id"]
    with db.connect(path) as conn:
        owner = conn.execute(
            "SELECT owner_id FROM deck_documents WHERE id=?", (deck_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE deck_entries SET quantity=98 WHERE deck_id=? AND is_commander=0",
            (deck_id,),
        )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES('slow-access','slow-access','Deep Access','Sorcery','Target player draws two cards. Flashback {1}{U} (You may cast this card from your graveyard for its flashback cost.)','[]',4,1)"
        )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES('fast-access','fast-access','Fast Access','Sorcery','Scry 2. Draw a card.','[]',1,1)"
        )
        conn.execute(
            "INSERT INTO deck_entries(id,deck_id,card_id,oracle_id,name,quantity,is_commander,sort_order,type_line,mana_value,oracle_text,color_identity) VALUES('slow-access-entry',?,'slow-access','slow-access','Deep Access',1,0,2,'Sorcery',4,'Target player draws two cards. Flashback {1}{U} (You may cast this card from your graveyard for its flashback cost.)','[]')",
            (deck_id,),
        )
        conn.commit()
    stored = DeckDocumentRepo(path).get(owner, deck_id)
    old = next(e for e in stored["entries"] if e["id"] == "slow-access-entry")
    assert "cmc" not in old and old["mana_value"] == 4
    report = recommend_replacements(path, stored, focus="card selection consistency")
    pair = next(e for e in report["candidate_evidence"] if e["name"] == "Fast Access")
    assert pair["recommendation_type"] == "strategic_tradeoff"
    assert pair["functional_improvement"] is None
    assert pair["strategic_tradeoff"]["mana_value_reduction"] == 3
    assert pair["outgoing_oracle_facts"]["cmc"] == 4


def test_tradeoff_never_uses_delve_printed_cost_as_upgrade():
    from sabermetrics.assistant_deck_intelligence import _features
    from sabermetrics.functional_candidates import strategic_tradeoff

    old = {
        "type_line": "Sorcery",
        "mana_value": 8,
        "oracle_text": "Delve\nDraw three cards.",
    }
    new = {"type_line": "Sorcery", "cmc": 1, "oracle_text": "Draw a card."}
    assert strategic_tradeoff(old, new, _features(old), _features(new)) is None


def test_functional_matcher_preserves_timing_and_extra_mana_abilities():
    from sabermetrics.assistant_deck_intelligence import _features
    from sabermetrics.functional_candidates import functional_improvement

    old = {"type_line": "Instant", "mana_value": 3, "oracle_text": "Draw two cards."}
    new = {"type_line": "Sorcery", "cmc": 2, "oracle_text": "Draw two cards."}
    assert functional_improvement(old, new, _features(old), _features(new)) is None
    old = {
        "type_line": "Artifact",
        "mana_value": 3,
        "oracle_text": "Indestructible\n{T}: Add one mana of any color.",
    }
    new = {
        "type_line": "Artifact",
        "cmc": 2,
        "oracle_text": "{T}: Add one mana of any color.",
    }
    assert functional_improvement(old, new, _features(old), _features(new)) is None


def test_stored_wrong_color_deck_discovers_first_needed_basic_source(prepared):
    path, document = prepared
    deck_id = document["id"]
    with db.connect(path) as conn:
        owner = conn.execute(
            "SELECT owner_id FROM deck_documents WHERE id=?", (deck_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE deck_entries SET quantity=46,card_id='card-mountain',oracle_id=(SELECT oracle_id FROM cards WHERE id='card-mountain'),name='Mountain',type_line='Basic Land — Mountain',oracle_text='',color_identity='[\"R\"]' WHERE deck_id=? AND is_commander=0",
            (deck_id,),
        )
        conn.execute(
            "UPDATE deck_entries SET color_identity='[\"U\",\"R\"]',mana_cost='{1}{U}{R}' WHERE deck_id=? AND is_commander=1",
            (deck_id,),
        )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99,mana_cost) VALUES('blue-spell','blue-spell','Blue Spell','Sorcery','Draw a card.','[\"U\"]',1,1,'{U}')"
        )
        for index in range(53):
            card_id = f"blue-card-{index}"
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99,mana_cost) VALUES(?,?,?,'Sorcery','Draw a card.','[\"U\"]',1,1,'{U}')",
                (card_id, card_id, f"Blue Spell {index}"),
            )
            conn.execute(
                "INSERT INTO deck_entries(id,deck_id,card_id,oracle_id,name,quantity,is_commander,sort_order,type_line,mana_value,mana_cost,oracle_text,color_identity) VALUES(?,?,?,?,?,1,0,2,'Sorcery',1,'{U}','Draw a card.','[\"U\"]')",
                (
                    f"blue-entry-{index}",
                    deck_id,
                    card_id,
                    card_id,
                    f"Blue Spell {index}",
                ),
            )
        conn.commit()
    stored = DeckDocumentRepo(path).get(owner, deck_id)
    report = recommend_replacements(path, stored, focus="mana consistency")
    assert report["candidate_evidence"], report
    pair = next(
        e
        for e in report["candidate_evidence"]
        if (e.get("strategic_tradeoff") or {}).get("kind") == "mana_color_tradeoff"
    )
    assert pair["name"] == "Island"
    proof = pair["strategic_tradeoff"]["color_demand_evidence"]
    assert proof["recognized_source_counts_before"]["U"] == 0
    assert proof["colored_pip_demand"]["U"] == 54
    assert proof["outgoing_sources_after"] == 45


@pytest.mark.parametrize(
    "change",
    [
        "illegal",
        "same_color",
        "existing_blue",
        "unknown_nonbasic",
        "conditional_source",
    ],
)
def test_basic_color_tradeoff_abstains_without_zero_source_proof(change):
    from sabermetrics.functional_candidates import basic_land_color_tradeoff

    mountain = {"type_line": "Basic Land — Mountain", "quantity": 46}
    island = {"type_line": "Basic Land — Island"}
    entries = [
        {"is_commander": True, "color_identity": ["U", "R"], "mana_cost": "{U}{R}"},
        mountain,
        {"type_line": "Sorcery", "mana_cost": "{U}", "oracle_text": "Draw a card."},
    ]
    if change == "illegal":
        entries[0]["color_identity"] = ["R"]
    elif change == "same_color":
        island = mountain
    elif change == "existing_blue":
        entries.append(island)
    elif change == "unknown_nonbasic":
        entries.append(
            {"type_line": "Land", "oracle_text": "{T}: Add one mana of any color."}
        )
    else:
        entries.append(
            {
                "type_line": "Artifact",
                "oracle_text": "Whenever you cast a spell, add {U}.",
            }
        )
    assert basic_land_color_tradeoff({"entries": entries}, mountain, island) is None


def test_outgoing_entry_uuid_does_not_choose_different_named_pair(prepared):
    path, document = prepared
    library = next(e for e in document["entries"] if not e["is_commander"])
    library["quantity"] = 97
    with db.connect(path) as conn:
        for index, name in enumerate(("Alpha Draw", "Beta Draw")):
            cid = f"old-draw-{index}"
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,'Sorcery','Draw two cards.','[]',4,1)",
                (cid, cid, name),
            )
            document["entries"].append(
                {
                    **library,
                    "id": f"old-entry-{1 - index}",
                    "card_id": cid,
                    "oracle_id": cid,
                    "name": name,
                    "type_line": "Sorcery",
                    "oracle_text": "Draw two cards.",
                    "mana_value": 4,
                    "quantity": 1,
                }
            )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES('new-draw','new-draw','New Draw','Sorcery','Draw a card.','[]',1,1)"
        )
        conn.commit()
    first = recommend_replacements(path, document, focus="draw")
    for entry in document["entries"]:
        if entry["name"] == "Alpha Draw":
            entry["id"] = "z-entry"
        elif entry["name"] == "Beta Draw":
            entry["id"] = "a-entry"
    second = recommend_replacements(path, document, focus="draw")
    named = lambda result: [
        (e["outgoing_card"]["name"], e["name"]) for e in result["candidate_evidence"]
    ]
    assert named(first) == named(second)
    assert named(first)[0] == ("Alpha Draw", "New Draw")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Scry 1.\nDraw a card.", True),
        ("Surveil 2.\nDraw a card.", True),
        (
            "Look at the top three cards of your library, then put them back in any order. You may shuffle. Draw a card.",
            True,
        ),
        ("Each player scries 2.", False),
        ("Target opponent surveils 2.", False),
        (
            "Look at the top three cards of target opponent's library, then put them back in any order.",
            False,
        ),
    ],
)
def test_self_selection_features_are_scoped(text, expected):
    from sabermetrics.assistant_deck_intelligence import _features

    assert (
        "card_selection" in _features({"type_line": "Instant", "oracle_text": text})
    ) == expected


def test_selection_family_survives_saturated_draw_pool_and_preserves_opt(prepared):
    path, document = prepared
    deck_id = document["id"]
    with db.connect(path) as conn:
        owner = conn.execute(
            "SELECT owner_id FROM deck_documents WHERE id=?", (deck_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE deck_entries SET quantity=97 WHERE deck_id=? AND is_commander=0",
            (deck_id,),
        )
        for cid, name, text, cost in [
            ("slow-selection", "Slow Selection", "Scry 2. Draw two cards.", 4),
            ("existing-opt", "Existing Opt", "Scry 1. Draw a card.", 1),
        ]:
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,'Instant',?,'[]',?,1)",
                (cid, cid, name, text, cost),
            )
            conn.execute(
                "INSERT INTO deck_entries(id,deck_id,card_id,oracle_id,name,quantity,is_commander,sort_order,type_line,mana_value,oracle_text,color_identity) VALUES(?,?,?,?,?,1,0,2,'Instant',?,?,'[]')",
                (cid + "-entry", deck_id, cid, cid, name, cost, text),
            )
        for index in range(105):
            cid = f"irrelevant-{index}"
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,'Sorcery','Draw a card.','[]',1,1)",
                (cid, cid, f"AAA Cheap Draw {index:03}"),
            )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES('new-scry','new-scry','ZZZ Scry Candidate','Instant','Scry 2. Draw a card.','[]',1,1)"
        )
        conn.commit()
    stored = DeckDocumentRepo(path).get(owner, deck_id)
    result = recommend_replacements(path, stored, focus="card selection consistency")
    assert result["candidate_evidence"][0]["name"] == "ZZZ Scry Candidate"
    assert result["candidate_evidence"][0]["outgoing_card"]["name"] == "Slow Selection"
    assert all(
        e["outgoing_card"]["name"] != "Existing Opt"
        for e in result["candidate_evidence"]
    )
