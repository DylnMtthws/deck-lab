"""Standalone, synthetic manifests: no database, providers, or card-name rules."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from sabermetrics.assistant_inference_search import discover_connections


def event(name, **fields):
    return {"event": name, "actor": "you", "subject": "card", **fields}


def manifest(specs):
    sources, facts = [], []
    for spec in specs:
        spec = deepcopy(spec)
        fid = spec.pop("fact_id")
        oracle = spec.pop("text", f"Synthetic exact clause {fid}.")
        type_line = spec.pop("type_line", "Creature — Scout")
        source_id = "source_" + fid
        record = {
            "card_id": "card_" + fid,
            "name": "Unfamiliar " + fid,
            "oracle_text": oracle,
            "type_line": type_line,
        }
        digest = sha256(
            json.dumps(
                record,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        sources.append(
            {
                "source_id": source_id,
                "card_id": "card_" + fid,
                "name": "Unfamiliar " + fid,
                "source_sha256": digest,
                "oracle_text": oracle,
                "type_line": type_line,
                "copied_fields": record,
                "clauses": [
                    {
                        "clause_id": "clause_" + fid,
                        "start": 0,
                        "end": len(oracle),
                        "text": oracle,
                        "parse_status": "bounded_interpretation",
                        "fact_ids": [fid],
                        "issues": [],
                    }
                ],
            }
        )
        facts.append(
            {
                "fact_id": fid,
                "source_id": source_id,
                "source_sha256": digest,
                "start": 0,
                "end": len(oracle),
                "quote": oracle,
                "kind": "triggered",
                "inputs": [],
                "outputs": [],
                "costs": [],
                "conditions": [],
                "required_rules": [],
                "status": "bounded_interpretation",
                **spec,
            }
        )
    return {
        "version": "synthetic.v1",
        "sources": sources,
        "facts": facts,
        "coverage": {"scope_complete": False},
    }


def ring():
    return manifest(
        [
            {
                "fact_id": "a",
                "kind": "activated",
                "inputs": [event("activate")],
                "costs": [
                    event("discard", from_zone="hand", to_zone="graveyard"),
                    {"kind": "mana", "quantity": "{1}"},
                    {"kind": "tap", "subject": "self"},
                ],
                "outputs": [event("activate")],
            },
            {
                "fact_id": "b",
                "inputs": [event("discard", from_zone="hand", to_zone="graveyard")],
                "outputs": [
                    event(
                        "damage",
                        subject="opponent",
                        source_id="source_b",
                        recipient="opponent",
                        combat=False,
                    )
                ],
            },
            {
                "fact_id": "c",
                "inputs": [
                    event(
                        "damage",
                        subject="opponent",
                        recipient="opponent",
                        source_types=["Scout"],
                        combat=False,
                    )
                ],
                "outputs": [event("explore", subject="creature")],
            },
            {
                "fact_id": "d",
                "kind": "rule_definition",
                "inputs": [event("explore", subject="creature")],
                "outputs": [
                    event(
                        "put_into_hand",
                        subject="land",
                        from_zone="library",
                        to_zone="hand",
                        conditions=["top card is land"],
                    )
                ],
            },
        ]
    )


def links(result):
    return [link for c in result["candidates"] for link in c["links"]]


def test_unfamiliar_ring_is_only_a_conditional_finite_cycle_and_input_immutable():
    bundle = ring()
    original = deepcopy(bundle)
    result = discover_connections(bundle)
    assert bundle == original
    cycles = [c for c in result["candidates"] if c["cycle_detected"]]
    assert cycles
    cycle = next(c for c in cycles if c["fact_ids"] == ["a", "b", "c", "d"])
    assert [(l["from_fact_id"], l["to_fact_id"]) for l in cycle["links"]] == [
        ("a", "b"),
        ("b", "c"),
        ("c", "d"),
        ("d", "a"),
    ]
    assert cycle["links"][-1]["join_kind"] == "resource"
    assert cycle["status"] == "hypothesis" and cycle["verified"] is False
    assert "top card is land" in cycle["assumptions"]
    assert any("Finite fuel" in u for u in cycle["unresolved"])
    assert any("Unsupported cycle semantics" in u for u in cycle["unresolved"])
    assert all(l["status"] == "proposed" for l in cycle["links"])
    assert result["coverage"]["scope_complete"] is False


def test_graveyard_movement_is_never_a_discard_event_or_hand_resource():
    bundle = manifest(
        [
            {
                "fact_id": "a",
                "outputs": [
                    event("move_to_graveyard", from_zone="library", to_zone="graveyard")
                ],
            },
            {
                "fact_id": "b",
                "inputs": [event("discard")],
                "outputs": [event("damage")],
            },
        ]
    )
    assert links(discover_connections(bundle)) == []


@pytest.mark.parametrize(
    "change",
    [
        {"combat": True},
        {"actor": "opponent"},
        {"recipient": "you"},
        {"source_types": ["Dragon"]},
    ],
)
def test_damage_explicit_combat_actor_recipient_or_type_mismatch_rejected(change):
    bundle = ring()
    bundle["facts"][2]["inputs"][0].update(change)
    assert not any(
        l["from_fact_id"] == "b" and l["to_fact_id"] == "c"
        for l in links(discover_connections(bundle))
    )


def test_unknown_damage_subtype_and_combat_are_assumptions_not_guesses():
    bundle = ring()
    output = bundle["facts"][1]["outputs"][0]
    del output["source_id"]
    del output["combat"]
    result = discover_connections(bundle)
    link = next(l for l in links(result) if l["from_fact_id"] == "b")
    assert any("subtype unknown" in c for c in link["conditions"])
    assert any("combat=false" in c for c in link["conditions"])


def test_all_joint_costs_hyperedge_inputs_and_shared_assumptions_survive():
    bundle = ring()
    extra = event("death", subject="another creature")
    bundle["facts"][2]["inputs"].append(extra)
    result = discover_connections(bundle)
    cycle = next(
        c for c in result["candidates"] if c["fact_ids"] == ["a", "b", "c", "d"]
    )
    consumes = [
        r["resource"]
        for r in cycle["resource_trace"]
        if r["fact_id"] == "a" and r["direction"] == "consume" and r["occurrence"] == 0
    ]
    assert consumes == bundle["facts"][0]["costs"]
    assert any(
        r["resource"] == extra and r["direction"] == "require_input"
        for r in cycle["resource_trace"]
    )
    assert any("Joint input c:1" in a for a in cycle["assumptions"])
    assert all(
        r["assumptions"] == cycle["assumptions"] for r in cycle["resource_trace"]
    )
    assert any(
        "finite_mana_or_tap" in limit
        for r in cycle["resource_trace"]
        for limit in r["limits"]
    )
    assert any(
        "finite_library" in limit
        for r in cycle["resource_trace"]
        for limit in r["limits"]
    )


def test_unknown_quantities_remain_missing_and_batch_scope_preserved():
    bundle = ring()
    bundle["facts"][3]["outputs"][0]["quantity"] = {
        "per_card": "one",
        "batch": "unknown",
    }
    result = discover_connections(bundle)
    candidate = result["candidates"][0]
    unknown = next(
        r for r in candidate["resource_trace"] if r["direction"] == "consume"
    )
    assert "quantity" not in unknown["resource"]
    known = next(
        r
        for r in candidate["resource_trace"]
        if r["resource"].get("event") == "put_into_hand"
    )
    assert known["resource"]["quantity"] == {"per_card": "one", "batch": "unknown"}
    assert any(
        "quantity unknown" in issue for l in candidate["links"] for issue in l["issues"]
    )
    assert not any("net" in r or "balance" in r for r in candidate["resource_trace"])


def test_conflicting_conditions_are_retained_as_unknown_not_silently_selected():
    bundle = ring()
    bundle["facts"][0]["conditions"] = ["top card is land"]
    bundle["facts"][1]["conditions"] = ["not top card is land"]
    candidate = discover_connections(bundle)["candidates"][0]
    assert {"top card is land", "not top card is land"} <= set(candidate["assumptions"])
    assert any("Conflicting shared conditions" in u for u in candidate["unresolved"])
    assert candidate["verified"] is False


def test_activate_is_not_an_actual_payment_or_freestanding_event_join():
    bundle = manifest(
        [
            {
                "fact_id": "a",
                "kind": "activated",
                "costs": [{"kind": "mana"}],
                "outputs": [event("activate")],
            },
            {"fact_id": "b", "inputs": [event("activate")], "outputs": [event("draw")]},
        ]
    )
    assert discover_connections(bundle)["candidates"] == []


def test_targeted_rule_gaps_and_same_event_keyword_composition():
    bundle = ring()
    bundle["facts"][2]["required_rules"] = ["explore", "unfamiliar-rule-42"]
    result = discover_connections(bundle)
    assert result["dependency_gaps"] == [
        {
            "required_rule": "unfamiliar-rule-42",
            "target_fact_id": "c",
            "target_source_id": "source_c",
            "status": "unresolved",
        }
    ]
    assert any(
        l["from_fact_id"] == "c" and l["to_fact_id"] == "d" for l in links(result)
    )


@pytest.mark.parametrize(
    "limit,value",
    [
        ("max_expansions", 1),
        ("max_depth", 1),
        ("max_candidates", 1),
        ("max_expansions", 0),
        ("max_depth", 0),
        ("max_candidates", 0),
    ],
)
def test_bounds_and_unexplored_are_deterministic(limit, value):
    kwargs = {limit: value}
    first = discover_connections(ring(), **kwargs)
    assert first == discover_connections(ring(), **kwargs)
    assert first["coverage"]["scope_complete"] is False
    assert first["coverage"]["capped"]
    assert any(u["reason"] == limit for u in first["coverage"]["unexplored"])
    assert first["coverage"]["expansions"] <= kwargs.get("max_expansions", 256)
    assert len(first["candidates"]) <= kwargs.get("max_candidates", 12)
    assert all(
        len(c["links"]) <= kwargs.get("max_depth", 5) for c in first["candidates"]
    )


def test_cancellation_and_callback_exceptions():
    assert discover_connections(ring(), check_current=lambda: False)["coverage"][
        "cancelled"
    ]
    calls = 0

    def current():
        nonlocal calls
        calls += 1
        return calls < 7

    result = discover_connections(ring(), check_current=current)
    assert result["coverage"]["cancelled"]
    assert result["coverage"]["expansions"] < 256

    def raise_cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        discover_connections(ring(), check_current=raise_cancel)


@pytest.mark.parametrize(
    "damage", ["span", "source_hash", "stale_fact", "quote_hash", "fact_id", "clause"]
)
def test_corrupt_bindings_never_participate(damage):
    bundle = ring()
    fact = bundle["facts"][1]
    if damage == "span":
        fact["start"] += 1
    elif damage == "source_hash":
        bundle["sources"][1]["source_sha256"] = "bad"
    elif damage == "stale_fact":
        fact["source_sha256"] = "old"
    elif damage == "quote_hash":
        fact["quote_sha256"] = "bad"
    elif damage == "fact_id":
        fact["fact_id"] = "stale_id"
    else:
        bundle["sources"][1]["clauses"][0]["text"] = "wrong"
    result = discover_connections(bundle)
    assert result["coverage"]["invalid_bindings"]
    assert all(fact["fact_id"] not in c["fact_ids"] for c in result["candidates"])


def test_fingerprint_binds_all_source_and_fact_manifest_data():
    bundle = ring()
    before = discover_connections(bundle)
    bundle["sources"][0]["copied_fields"]["other_field"] = "other exact field"
    bundle["sources"][0]["source_sha256"] = sha256(
        json.dumps(
            bundle["sources"][0]["copied_fields"],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    bundle["facts"][0]["source_sha256"] = bundle["sources"][0]["source_sha256"]
    after = discover_connections(bundle)
    assert before["manifest_fingerprint"] != after["manifest_fingerprint"]
    assert (
        before["candidates"][0]["candidate_id"]
        != after["candidates"][0]["candidate_id"]
    )
    assert all(
        c["manifest_fingerprint"] == after["manifest_fingerprint"]
        for c in after["candidates"]
    )


def test_event_index_avoids_all_pairs_and_preserves_alternative_resources():
    specs = [{"fact_id": "a", "outputs": [event("discard")]}]
    specs += [
        {"fact_id": f"irrelevant_{i:03}", "inputs": [event("death")]}
        for i in range(100)
    ]
    specs += [
        {"fact_id": key, "inputs": [event("discard")], "outputs": [event(goal)]}
        for key, goal in [("goal_hand", "put_into_hand"), ("goal_mana", "mana")]
    ]
    result = discover_connections(manifest(specs))
    assert result["coverage"]["expansions"] == 2
    assert {tuple(c["fact_ids"]) for c in result["candidates"]} == {
        ("a", "goal_hand"),
        ("a", "goal_mana"),
    }


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_invalid_bounds_rejected(bad):
    with pytest.raises(ValueError):
        discover_connections(ring(), max_depth=bad)


@pytest.mark.parametrize("permanent", [False, True])
def test_live_compiler_contract_renamed_chain_keeps_roles_and_costs_conditional(
    permanent,
):
    from sabermetrics.assistant_inference_facts import compile_deck_facts

    def card(identity, name, text):
        return {
            "id": identity,
            "name": name,
            "type_line": "Creature — Pirate",
            "oracle_text": text,
            "mana_cost": "{1}{R}",
        }

    cards = [
        card(
            "activation",
            "Indigo Scribe",
            "{R}, Discard a card: Indigo Scribe gets +1/+1 until end of turn.",
        ),
        card(
            "damage",
            "Violet Lantern",
            "Whenever you discard a card, Violet Lantern deals 1 damage to each opponent.",
        ),
        card(
            "exploration",
            "Azure Wanderer",
            "Whenever one or more Pirates you control deal damage to a player, Azure Wanderer explores.",
        ),
    ]
    rule = {
        "kind": "comprehensive_rules",
        "source": "supplied-test-document",
        "version": "test-edition",
        "complete": True,
        "oracle_text": "To explore, a creature's controller reveals the top card of their library. "
        "If a land card is revealed this way, they put it into their hand. "
        "Otherwise, they put a +1/+1 counter on that creature, then they may put "
        "the revealed card into their graveyard.",
    }
    if permanent:
        rule["oracle_text"] = (
            "Certain abilities instruct a permanent to explore. "
            "To do so, that permanent’s controller reveals the top card of their library. "
            "If a land card is revealed this way, that player puts that card into their hand. "
            "Otherwise, that player puts a +1/+1 counter on the exploring permanent, then may put "
            "the revealed card into their graveyard."
        )
    bundle = compile_deck_facts(cards, rule)
    result = discover_connections(bundle)
    assert result["coverage"]["invalid_bindings"] == []
    assert result["coverage"]["valid_facts"] == 4
    cycle = next(
        c
        for c in result["candidates"]
        if c["cycle_detected"] and len(c["fact_ids"]) == 4
    )
    assert set(cycle["source_ids"]) >= {
        "card:activation",
        "card:damage",
        "card:exploration",
    }
    assert "revealed card is a land" in cycle["assumptions"]
    assert any("actor binding" in a for a in cycle["assumptions"])
    assert any("Plural subtype" in a for a in cycle["assumptions"])
    assert any(
        r["resource"].get("kind") == "printed_symbols"
        and "finite_mana_or_tap_capacity_unverified" in r["limits"]
        for r in cycle["resource_trace"]
    )
    assert all(
        l["join_kind"] != "resource" or l["from_fact_id"] != l["to_fact_id"]
        for l in cycle["links"]
    )
    assert cycle["verified"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("type_line", "Creature — Dragon"),
        ("oracle_text", "Changed source text"),
        ("name", "Stale displayed name"),
    ],
)
def test_displayed_source_fields_must_match_hashed_record(field, value):
    bundle = ring()
    bundle["sources"][1][field] = value
    result = discover_connections(bundle)
    assert all("b" not in c["fact_ids"] for c in result["candidates"])
    assert result["coverage"]["invalid_bindings"]


def test_single_condition_combat_only_and_source_type_string_contract():
    bundle = ring()
    bundle["facts"][1]["outputs"][0]["source_types"] = "Creature — Scout"
    bundle["facts"][2]["inputs"][0]["source_types"] = "Scouts"
    bundle["facts"][2]["inputs"][0].pop("combat")
    bundle["facts"][2]["inputs"][0]["combat_only"] = True
    assert not any(
        l["from_fact_id"] == "b" and l["to_fact_id"] == "c"
        for l in links(discover_connections(bundle))
    )
    bundle["facts"][1]["outputs"][0].pop("combat")
    result = discover_connections(bundle)
    link = next(l for l in links(result) if l["from_fact_id"] == "b")
    assert "Require combat damage; combat provenance unknown" in link["conditions"]


def test_damage_subtype_uses_bound_source_instead_of_stale_event_annotation():
    bundle = ring()
    bundle["facts"][1]["outputs"][0]["source_types"] = ["Dragon"]
    bundle["facts"][2]["inputs"][0]["source_types"] = ["Dragon"]
    assert not any(
        l["from_fact_id"] == "b" and l["to_fact_id"] == "c"
        for l in links(discover_connections(bundle))
    )
