import copy
import json

from sabermetrics.assistant_evidence_packet import (
    build_recommendation_packet,
    compact_recommendation_packet,
)
from sabermetrics.assistant_suitability import candidate_suitability, deck_support_index
from tests.test_assistant_ability_clauses import card


def test_packet_preserves_sources_and_resource_consumption():
    maker = card("Maker", "Create a 1/1 Soldier creature token.")
    incoming = card(
        "Draw", "{1}, Sacrifice another creature: Draw a card.", mana_cost="{2}{B}"
    )
    document = {"revision": 7, "entries": [maker]}
    before = copy.deepcopy(document)
    packet = build_recommendation_packet(document, incoming, focus="draw")
    assert json.loads(json.dumps(packet)) == packet
    assert packet["incoming"]["oracle_text"] == incoming["oracle_text"]
    assert packet["casting_cost"] == "{2}{B}"
    assert packet["activation_costs"] == ["{1}, Sacrifice another creature"]
    assert packet["deck_support"][0]["source_quote"] == maker["oracle_text"]
    assert any("permanent independently" in c for c in packet["counterevidence"])
    profile = candidate_suitability(incoming, deck_support_index(document))
    assert profile["resource_accounting"]["net_hand_delta_per_activation"] == 1
    assert profile["resource_accounting"]["permanent_sacrifice_cost"]
    assert profile["recommendation_packet"]["version"] == packet["version"]
    assert document == before


def test_cut_removes_existing_engine_support_and_cannot_support_incoming():
    maker = card("Maker", "Create a 1/1 Soldier creature token.")
    engine = card("Engine", "Sacrifice another creature: Draw a card.")
    incoming = card("New engine", "Sacrifice another creature: Draw two cards.")
    packet = build_recommendation_packet({"entries": [maker, engine]}, incoming, maker)
    assert not packet["deck_support"]
    assert any(
        e["recipient"] == "Engine" and e["provider"] == "Maker"
        for e in packet["lost_engine_links"]
    )
    assert packet["prerequisites"][0]["status"] == "unknown"
    assert packet["lost_roles"]


def test_sideboard_and_multiface_protection_abstain():
    incoming = card("Engine", "{T}: Draw a card.")
    protection = card(
        "Shield",
        "Target creature gains indestructible until end of turn.",
        type_line="Instant",
    )
    packet = build_recommendation_packet({"entries": [protection]}, incoming)
    assert packet["deck_support"][0]["relationship"] == "potential_protection"
    assert any("Timing" in c for c in packet["deck_support"][0]["conditions"])
    for kw in ({"zone_name": "Sideboard"}, {"card_faces": [{"name": "Back"}]}):
        assert not build_recommendation_packet(
            {"entries": [{**protection, **kw}]}, incoming
        )["deck_support"]
    restricted = {
        **protection,
        "oracle_text": "Target creature an opponent controls gains indestructible until end of turn.",
    }
    assert not build_recommendation_packet({"entries": [restricted]}, incoming)[
        "deck_support"
    ]
    compact = compact_recommendation_packet(packet)
    assert "oracle_text" not in json.dumps(compact)


def test_one_copy_cut_preserves_remaining_inventory_and_exact_entry_identity():
    maker = card(
        "Maker", "Create a 1/1 Soldier creature token.", id="entry-a", quantity=3
    )
    second = {**maker, "id": "entry-b", "quantity": 1}
    incoming = card("Draw", "Sacrifice another creature: Draw a card.")
    document = {"entries": [maker, second]}
    packet = build_recommendation_packet(document, incoming, maker)
    assert packet["changes"]["removed_quantity"] == 1
    assert packet["changes"]["remaining_identity_quantity"] == 3
    assert packet["changes"]["outgoing_entry_id"] == "entry-a"
    assert packet["deck_support"] and not packet["lost_engine_links"]
    assert [c["quantity"] for c in packet["deck_sources"]] == [2, 1]
    assert packet["outgoing"]["quantity"] == 3
    packet = build_recommendation_packet(
        document, incoming, {**maker, "cut_quantity": 3}
    )
    assert packet["changes"]["remaining_identity_quantity"] == 1
    assert packet["deck_support"] and not packet["lost_roles"]
    assert maker["quantity"] == 3


def test_old_graph_cached_by_content_and_returned_links_cannot_poison_cache():
    from sabermetrics.assistant_evidence_packet import _old_links

    _old_links.cache_clear()
    maker = card("Maker", "Create a 1/1 Soldier creature token.")
    engine = card("Engine", "Sacrifice another creature: Draw a card.")
    document = {"revision": 1, "entries": [maker, engine]}
    first = build_recommendation_packet(document, engine, maker)
    first["lost_engine_links"][0]["source_quote"] = "tampered"
    second = build_recommendation_packet(document, engine, maker)
    assert _old_links.cache_info().hits == 1
    assert second["lost_engine_links"][0]["source_quote"] == maker["oracle_text"]
    maker["oracle_text"] = "Create a Treasure token."
    third = build_recommendation_packet(document, engine, maker)
    assert _old_links.cache_info().misses == 2
    assert not third["lost_engine_links"]


def test_partial_outgoing_reference_resolves_canonical_identity_and_quantity():
    maker = card(
        "Maker", "Create a 1/1 Soldier creature token.", id="actual-entry", quantity=2
    )
    incoming = card("Draw", "Sacrifice another creature: Draw a card.")
    packet = build_recommendation_packet(
        {"entries": [maker]}, incoming, {"entry_id": "actual-entry"}
    )
    assert packet["outgoing"]["oracle_id"] == maker["oracle_id"]
    assert packet["outgoing"]["oracle_text"] == maker["oracle_text"]
    assert packet["changes"]["remaining_identity_quantity"] == 1
    assert packet["deck_support"]
    for bad in (
        {"entry_id": "missing"},
        {"entry_id": "actual-entry", "oracle_id": "wrong"},
        {"entry_id": "actual-entry", "cut_quantity": 0},
    ):
        unresolved = build_recommendation_packet({"entries": [maker]}, incoming, bad)
        assert unresolved["changes"]["removed_quantity"] == 0
        assert not unresolved["lost_engine_links"]


def test_compact_retains_cut_losses_questions_and_all_omission_counts():
    maker = card("Maker", "Create a 1/1 Soldier creature token.")
    engines = [
        card(f"Engine {i}", "Sacrifice another creature: Draw a card.")
        for i in range(6)
    ]
    full = build_recommendation_packet(
        {"entries": [maker, *engines]}, engines[0], maker
    )
    compact = compact_recommendation_packet(full)
    assert compact["lost_roles"] == full["lost_roles"][:4]
    assert len(compact["lost_engine_links"]) == 3
    assert compact["omitted"]["lost_engine_links"] == 3
    assert compact["counterevidence"] and compact["unresolved_questions"]
    assert compact["changes"]["removed_quantity"] == 1
    assert full["lost_engine_sources"][0]["oracle_text"] == maker["oracle_text"]
    full["unresolved_questions"] += [str(i) for i in range(6)]
    full["counterevidence"] += [str(i) for i in range(4)]
    compact = compact_recommendation_packet(full)
    assert (
        compact["omitted"]["unresolved_questions"]
        == len(full["unresolved_questions"]) - 3
    )
    assert compact["omitted"]["counterevidence"] == len(full["counterevidence"]) - 2
