from sabermetrics.assistant_engine_graph import (
    build_engine_graph,
    engine_loss_score,
    local_support_edges,
)
from tests.test_assistant_ability_clauses import card


def test_local_counter_and_sacrifice_edges_retain_source_endpoints():
    source = card("Counter source", "{T}: Put a charge counter on target artifact.")
    engine = card(
        "Charge engine",
        "Remove a charge counter from this artifact: Draw a card.",
        type_line="Artifact",
    )
    graph = build_engine_graph({"revision": 3, "entries": [source, engine]})
    assert graph.local_edges[0]["provider_id"] == source["oracle_id"]
    assert graph.local_edges[0]["recipient_id"] == engine["oracle_id"]
    assert graph.local_edges[0]["source_quote"] == source["oracle_text"]
    assert engine_loss_score(graph, source) > 0
    assert engine_loss_score(graph, source, {**source, "oracle_id": "Replacement"}) == 0


def test_complex_or_wrong_target_protection_is_not_credited():
    engine = card("Engine", "{T}: Draw a card.")
    for text in (
        "Target artifact gains indestructible until end of turn.",
        "Target creature with flying gains indestructible until end of turn.",
        "If you control an Elf, target creature gains indestructible until end of turn.",
        "Sacrifice a creature: Target creature an opponent controls gains indestructible until end of turn.",
    ):
        assert local_support_edges(engine, [card("Shield", text)]) == []


def test_complete_retention_precedes_stable_display_truncation():
    from random import Random

    from sabermetrics.assistant_engine_graph import summarize_engine_graph

    makers = [
        card(f"Maker {i}", "Create a 1/1 Soldier creature token.") for i in range(2)
    ]
    engines = [
        card(f"Engine {i:02}", "Sacrifice a creature: Draw a card.") for i in range(70)
    ]
    entries = makers + engines
    expected = build_engine_graph({"revision": 9, "entries": entries})
    assert len(expected.local_edges) == 128
    assert expected.local_edges_omitted == 12
    assert expected.local_retention_counts == {
        **{c["oracle_id"]: 70 for c in makers},
        **{c["oracle_id"]: 2 for c in engines},
    }
    assert expected.local_retention_status == "complete_aggregates"
    assert summarize_engine_graph(expected)["local_edges_omitted"] == 132
    displayed = {e["recipient_id"] for e in expected.local_edges}
    omitted_engine = next(c for c in engines if c["oracle_id"] not in displayed)
    assert engine_loss_score(expected, omitted_engine) == 2
    assert engine_loss_score(expected, makers[0]) == 4
    assert engine_loss_score(expected, card("Unrelated", "Flying")) == 0
    shuffled = entries[:]
    Random(21).shuffle(shuffled)
    for permutation in (list(reversed(entries)), shuffled):
        graph = build_engine_graph({"revision": 9, "entries": permutation})
        assert graph.model_dump() == expected.model_dump()
        assert [engine_loss_score(graph, c) for c in entries] == [4, 4] + [2] * 70


def test_legacy_truncated_graph_has_explicit_conservative_fallback():
    from sabermetrics.assistant_engine_graph import EngineGraph, summarize_engine_graph

    outgoing = card("Omitted engine", "Sacrifice a creature: Draw a card.")
    legacy = EngineGraph(revision=1, links=[], local_edges_omitted=12)
    assert legacy.local_retention_counts is None
    assert legacy.local_retention_status == "incomplete_conservative_fallback"
    assert summarize_engine_graph(legacy)["local_retention_status"] == (
        "incomplete_conservative_fallback"
    )
    assert engine_loss_score(legacy, outgoing) == 4
    assert engine_loss_score(legacy, outgoing, dict(outgoing)) == 0
    complete_legacy = EngineGraph(revision=1, links=[])
    assert complete_legacy.local_retention_status == "complete_legacy_edges"
    assert engine_loss_score(complete_legacy, outgoing) == 0
    complete_legacy.local_edges = [
        {"provider_id": "Provider", "recipient_id": outgoing["oracle_id"]}
    ]
    assert engine_loss_score(complete_legacy, outgoing) == 1


def test_graph_and_scoring_are_pure_and_do_not_reuse_changed_source_facts():
    from copy import deepcopy

    from sabermetrics.assistant_engine_graph import EngineGraph

    maker = card("Maker", "Create a 1/1 Soldier creature token.")
    engine = card("Engine", "Sacrifice a creature: Draw a card.")
    document = {"owner_id": "owner-a", "revision": 1, "entries": [maker, engine]}
    before = deepcopy(document)
    graph = build_engine_graph(document)
    graph_before = graph.model_dump()
    assert document == before
    assert engine_loss_score(graph, engine) == 1
    assert engine_loss_score(graph, engine) == 1
    assert graph.model_dump() == graph_before
    assert document == before
    assert EngineGraph.model_validate(graph_before).model_dump() == graph_before
    # Owner/revision changes and in-place fact changes cannot hit an identity-only
    # cache. An old graph remains an independent snapshot of its source evidence.
    document["owner_id"] = "owner-b"
    document["revision"] = 2
    maker["oracle_text"] = "Flying"
    changed = build_engine_graph(document)
    assert engine_loss_score(changed, engine) == 0
    assert graph.model_dump() == graph_before
    maker["oracle_text"] = before["entries"][0]["oracle_text"]
    maker["oracle_text_truncated"] = True
    assert engine_loss_score(build_engine_graph(document), engine) == 0


def test_commander_weight_and_identical_source_replacement_are_preserved():
    from sabermetrics.assistant_engine_graph import card_engine_links

    commander = card(
        "Commander",
        "Whenever a creature you control dies, draw a card.",
        is_commander=True,
        zone_name="Command",
    )
    outlet = card("Outlet", "Sacrifice a creature: Draw a card.")
    document = {"entries": [commander, outlet]}
    links = card_engine_links(document, outlet)
    assert [(link.relationship, link.retention_weight) for link in links] == [
        ("creature_death_support", 2)
    ]
    graph = build_engine_graph(document)
    assert graph.links == links
    local_count = graph.local_retention_counts[outlet["oracle_id"]]
    assert engine_loss_score(graph, outlet) == min(4, local_count) + 2
    assert engine_loss_score(graph, outlet, {**outlet, "oracle_id": "New"}) == 0


def test_name_identity_and_protection_reuse_preserve_pure_call_behavior():
    from copy import deepcopy

    shield = card("Shield", "Target creature gains indestructible until end of turn.")
    engine = card("Engine", "{T}: Draw a card.")
    for c in (shield, engine):
        del c["oracle_id"]
    entries = [shield, engine]
    before = deepcopy(entries)
    direct = local_support_edges(engine, entries)
    graph = build_engine_graph({"entries": entries})
    assert direct == [e for e in graph.local_edges if e["recipient_id"] == "Engine"]
    assert engine_loss_score(graph, engine) == 1
    assert entries == before
    shield["oracle_text"] = "Target artifact gains indestructible until end of turn."
    assert engine_loss_score(build_engine_graph({"entries": entries}), engine) == 0
    assert direct[0]["source_quote"] == before[0]["oracle_text"]
