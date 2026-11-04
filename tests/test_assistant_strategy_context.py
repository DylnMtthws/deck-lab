"""Offline snapshot adapters and real runtime selection/review wiring."""

import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

from sabermetrics.assistant_card_facts import import_snapshot
from sabermetrics.assistant_mana_evidence import KINNAN_ORACLE_ID
from sabermetrics.assistant_rules import ingest_rules, retrieve_rules
from sabermetrics.assistant_rulings_import import import_rulings
from sabermetrics.assistant_strategy_context import (
    close_strategy_cache,
    expand_review_sources,
    selection_strategy_context,
    snapshot_card,
    strategy_context,
    strategy_wire_context,
)
from tests.test_assistant_mana_evidence import (
    CR12,
    CR12A,
    HALFLING,
    RULING,
    inputs,  # noqa: F401 -- pytest fixture
)

pytest_plugins = ["tests.test_assistant_experiments"]


def decode_selector(wire):
    """Test the wire's declared decoder, independently of projection internals."""
    decoded = deepcopy(wire)
    decoded["rows"] = []
    for values in wire["rows"]:
        row = dict(zip(wire["row_fields"], values, strict=True))
        row["status"] = wire["statuses"][row.pop("status_ref")]
        row["unknowns"] = [
            wire["unknown_vocabulary"][i] for i in row.pop("unknown_refs")
        ]
        decoded["rows"].append(row)
    decoded["ability_profiles"] = []
    for values in wire["ability_profiles"]:
        ability = dict(zip(wire["ability_fields"], values, strict=True))
        decoded["ability_profiles"].append(
            {
                "activation_cost": ability.pop("activation_cost"),
                "source_mana": ability,
            }
        )
    return decoded


@pytest.fixture(autouse=True)
def watcher_connections(monkeypatch):
    """Even a failed assertion must not leave a retained watcher connection."""
    import sabermetrics.assistant_strategy_context as adapter

    connections = []
    original = adapter._SnapshotWatcher.__init__

    def tracked(self, path):
        original(self, path)
        connections.append(self.connection)

    monkeypatch.setattr(adapter._SnapshotWatcher, "__init__", tracked)
    yield connections
    for connection in connections:
        connection.close()


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("PRAGMA data_version")


@pytest.fixture
def local_snapshot(tmp_path, request):
    deck, commander, _ = request.getfixturevalue("inputs")
    source = {
        "id": "halfling",
        "oracle_id": "halfling-oracle",
        "name": "Delighted Halfling",
        "mana_cost": "{G}",
        "type_line": "Creature — Halfling Citizen",
        "layout": "normal",
        "oracle_text": HALFLING,
    }
    cards = [
        {
            k: v
            for k, v in commander.items()
            if k not in {"source_sha256", "is_current", "oracle_text_complete"}
        },
        source,
    ]
    path = tmp_path / "facts.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE cards (id TEXT PRIMARY KEY, oracle_id TEXT, name TEXT, mana_cost TEXT, type_line TEXT, layout TEXT, oracle_text TEXT)"
        )
        for card in cards:
            conn.execute(
                "INSERT INTO cards VALUES (?,?,?,?,?,?,?)",
                tuple(
                    card[k]
                    for k in (
                        "id",
                        "oracle_id",
                        "name",
                        "mana_cost",
                        "type_line",
                        "layout",
                        "oracle_text",
                    )
                ),
            )
    snapshot = tmp_path / "cards.json"
    snapshot.write_text(json.dumps([{"object": "card", **c} for c in cards]))
    import_snapshot(path, snapshot, version="old-but-active", observed_at="2020-04-17")
    ingest_rules(
        path,
        "100.1. Fixture only.\n\n" + CR12 + "\n\n" + CR12A,
        source_url="https://media.wizards.com/test/rules.txt",
        version="local-edition",
    )
    rulings = tmp_path / "rulings.json"
    rulings.write_text(
        json.dumps(
            [
                {
                    "object": "ruling",
                    "oracle_id": KINNAN_ORACLE_ID,
                    "source": "wotc",
                    "published_at": "2020-04-17",
                    "comment": RULING,
                }
            ]
        )
    )
    import_rulings(
        path,
        rulings,
        expected_sha256=hashlib.sha256(rulings.read_bytes()).hexdigest(),
        source_url="https://data.scryfall.io/rulings/rulings-20261017000000.json",
        version="local-bulk",
    )
    return path, deck, cards, source


def test_active_local_snapshot_is_not_verified_latest(local_snapshot):
    path, deck, cards, source = local_snapshot
    context = strategy_context(path, deck, [source])
    row = context["mana_evidence"]["rows"][0]
    assert row["status"] == "bounded_conditional_evidence"
    assert row["admitted_claims"] and row["evidence_id"]
    wire = strategy_wire_context(context)
    assert "rules" not in wire["mana_evidence"]["rows"][0]
    assert "commander_oracle_text" not in wire["mana_evidence"]["rows"][0]
    assert "cards" not in wire and "rules" not in wire
    assert {h["source_id"] for h in context["rules"]} >= {"CR 106.12", "CR 106.12a"}
    assert len(context["cards"]) == 2  # commander is not lost or repeated per source
    for card in context["cards"]:
        assert card["is_current"] and card["freshness"] == "unknown"
        assert card["provenance"]["freshness"] == "not_verified_current"
        assert card["provenance"]["content_hash"] and card["source_sha256"]
    sources = []
    expand_review_sources(sources, context)
    assert any(cards[0]["oracle_text"] in s for s in sources)
    assert any(HALFLING in s for s in sources)
    assert any(CR12 in s for s in sources)
    assert any(CR12A in s for s in sources)
    assert any(RULING in s and "data.scryfall.io" in s for s in sources)


def test_bulk_transport_preserves_actual_retriever_attribution(local_snapshot):
    path, deck, _, source = local_snapshot
    actual = retrieve_rules(
        path,
        "restrictions riders additional mana",
        cards=[{"oracle_id": KINNAN_ORACLE_ID}],
        limit=6,
    ).model_dump()
    ruling = next(h for h in actual["hits"] if h["kind"] == "card_ruling")
    context = strategy_context(path, deck, [source])
    adapted = next(h for h in context["rules"] if h["kind"] == "card_ruling")
    assert {k: adapted[k] for k in ruling} == ruling
    assert "original source labeled 'wotc' by Scryfall" in adapted["source"]
    assert (
        context["mana_evidence"]["rows"][0]["status"] == "bounded_conditional_evidence"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "catalog_only",
        "incomplete",
        "stale_supplied",
        "missing_rule",
        "scryfall_authored",
    ],
)
def test_incomplete_or_disagreeing_evidence_abstains(local_snapshot, failure):
    path, deck, _, source = local_snapshot
    source = deepcopy(source)
    with sqlite3.connect(path) as conn:
        if failure == "catalog_only":
            conn.execute(
                "DELETE FROM assistant_card_fact_current WHERE card_id='halfling'"
            )
        elif failure == "incomplete":
            row = conn.execute(
                "SELECT payload FROM assistant_card_facts WHERE card_id='halfling'"
            ).fetchone()
            data = json.loads(row[0])
            data["oracle_text"] = "{T}: Add {C}." + "x" * 4100
            conn.execute(
                "UPDATE assistant_card_facts SET payload=? WHERE card_id='halfling'",
                (json.dumps(data),),
            )
        elif failure == "stale_supplied":
            source["oracle_text"] = "{T}: Add {G}."
        elif failure == "missing_rule":
            conn.execute("DELETE FROM assistant_rules WHERE rule_id='106.12a'")
        else:
            conn.execute("UPDATE card_rulings SET source='scryfall-bulk:scryfall'")
    context = strategy_context(path, deck, [source])
    row = context["mana_evidence"]["rows"][0]
    assert row["status"] == "unknown"
    assert row["unknowns"] and not row["admitted_claims"]
    if failure in {"catalog_only", "incomplete", "stale_supplied"}:
        assert context["cards"][0]["snapshot_unknowns"]


def test_missing_canonical_fields_do_not_admit_mechanics(local_snapshot):
    path, _, _, source = local_snapshot
    with sqlite3.connect(path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM assistant_card_facts WHERE card_id='halfling'"
            ).fetchone()[0]
        )
        payload.pop("mana_cost")
        conn.execute(
            "UPDATE assistant_card_facts SET payload=? WHERE card_id='halfling'",
            (json.dumps(payload),),
        )
    assert (
        "supplied_mechanics_mismatch:mana_cost"
        in snapshot_card(path, source)["snapshot_unknowns"]
    )


def test_no_kinnan_or_oversized_scope_does_no_snapshot_reads(
    local_snapshot, monkeypatch
):
    path, deck, _, source = local_snapshot

    def forbidden(*args, **kwargs):
        pytest.fail("Out-of-scope evidence must not read canonical snapshots")

    monkeypatch.setattr("sabermetrics.assistant_strategy_context.card_facts", forbidden)
    monkeypatch.setattr(
        "sabermetrics.assistant_strategy_context.retrieve_rules", forbidden
    )
    assert (
        strategy_context(path, {"entries": []}, [source])["status"] == "not_applicable"
    )
    many = [{**source, "id": str(i)} for i in range(16)]
    context = strategy_context(path, deck, many)
    assert context["unknowns"] == ["source_card_count_exceeds_16"]
    assert not context["mana_evidence"]["rows"]


def test_mismatched_mechanics_preserve_review_source_and_abstain(local_snapshot):
    from sabermetrics.assistant_answer_review import _card_source

    path, deck, _, source = local_snapshot
    stale = {**source, "oracle_text": "{T}: Add {G}."}
    sources = [_card_source(stale)]
    original = sources[0]
    context = strategy_context(path, deck, [stale])
    expand_review_sources(sources, context)
    assert sources[0] == original
    assert sum(s.startswith("Card: Delighted Halfling\n") for s in sources) == 1
    assert not context["mana_evidence"]["rows"][0]["admitted_claims"]
    wire = strategy_wire_context(context)
    assert wire["card_refs"][0]["snapshot_unknowns"] == [
        "supplied_mechanics_mismatch:oracle_text"
    ]
    assert HALFLING not in json.dumps(wire)
    assert CR12 not in json.dumps(wire)


def test_cache_reuses_full_local_evidence_but_rechecks_supplied_mechanics(
    local_snapshot, monkeypatch
):
    from collections import Counter

    import sabermetrics.assistant_strategy_context as adapter

    path, deck, _, source = local_snapshot
    reads = Counter()
    original_card, original_rules = adapter.card_facts, adapter.retrieve_rules

    def card_read(path, cid):
        reads[("card", cid)] += 1
        return original_card(path, cid)

    def rules_read(path, query, **kwargs):
        reads[("rules", query)] += 1
        return original_rules(path, query, **kwargs)

    monkeypatch.setattr(adapter, "card_facts", card_read)
    monkeypatch.setattr(adapter, "retrieve_rules", rules_read)
    cache = {}
    first = strategy_context(path, deck, [source], cache=cache)
    second = strategy_context(path, deck, [source], cache=cache)
    assert first == second
    changed = strategy_context(
        path, deck, [{**source, "oracle_text": "{T}: Add {G}."}], cache=cache
    )
    assert not changed["mana_evidence"]["rows"][0]["admitted_claims"]
    assert len(reads) == 5 and set(reads.values()) == {1}


@pytest.mark.parametrize("retrieved_count", [2, 16])
def test_runtime_passes_shared_context_and_expanded_source_basis(
    prepared,
    monkeypatch,
    local_snapshot,
    tmp_path,
    watcher_connections,
    retrieved_count,
):
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_runtime import Gateway, call, step
    from tests.test_assistant_selection import selection

    service, path, owner, deck, _, _ = prepared
    local_path, _, canonical_cards, source = local_snapshot
    commander = {**canonical_cards[0], "id": "card-cmd"}
    candidates = [
        {
            **source,
            "id": f"incoming-{i}",
            "oracle_id": f"incoming-oracle-{i}",
            "name": f"Incoming {i}",
        }
        for i in range(retrieved_count)
    ]
    if retrieved_count == 16:
        # Eight whole comparison records fit; the other eight are deliberately
        # too large. All sixteen remain retrieved sources, plus the commander.
        for card in candidates[3:8]:
            card["oracle_text"] = "{T}: Draw a card."
        for card in candidates[8:]:
            card["oracle_text"] = "{T}: Draw a card. " + "x" * 24000
        monkeypatch.setattr(
            "sabermetrics.assistant_tools.search_cards",
            lambda conn, **kwargs: deepcopy(
                candidates[:8] if kwargs["query"] == "batch-a" else candidates[8:]
            ),
        )
    with sqlite3.connect(path) as conn:
        for card in candidates[4:]:
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,color_identity,is_legal_in_99) VALUES(?,?,?,?,?,?)",
                (
                    card["id"],
                    card["oracle_id"],
                    card["name"],
                    card["type_line"],
                    "[]",
                    1,
                ),
            )
        for card in [commander, *candidates]:
            conn.execute(
                "UPDATE cards SET oracle_id=?,name=?,mana_cost=?,type_line=?,oracle_text=? WHERE id=?",
                tuple(
                    card[k]
                    for k in (
                        "oracle_id",
                        "name",
                        "mana_cost",
                        "type_line",
                        "oracle_text",
                        "id",
                    )
                ),
            )
        conn.execute(
            "UPDATE deck_entries SET oracle_id=?,name=?,mana_cost=?,type_line=?,oracle_text=? WHERE card_id='card-cmd'",
            tuple(
                commander[k]
                for k in ("oracle_id", "name", "mana_cost", "type_line", "oracle_text")
            ),
        )
    snapshot = tmp_path / "runtime-cards.json"
    snapshot.write_text(
        json.dumps([{"object": "card", **c} for c in [commander, *candidates]])
    )
    import_snapshot(path, snapshot, version="runtime")
    ingest_rules(
        path,
        "100.1. Fixture only.\n\n" + CR12 + "\n\n" + CR12A,
        source_url="https://media.wizards.com/test/rules.txt",
        version="local-edition",
    )
    with sqlite3.connect(path) as conn:
        conn.execute("ATTACH DATABASE ? AS fixture", (str(local_path),))
        for table in ("card_rulings", "card_rulings_snapshots"):
            conn.execute(f"DROP TABLE IF EXISTS main.{table}")
            conn.execute(f"CREATE TABLE main.{table} AS SELECT * FROM fixture.{table}")
    monkeypatch.setattr(
        "sabermetrics.assistant_runtime.MAX_TOOL_CALLS",
        2 if retrieved_count == 16 else 1,
    )
    seen = {}
    import sabermetrics.assistant_selection as selector

    original_payload = selector.selection_payload

    def frozen_payload(*args, **kwargs):
        encoded, retained = original_payload(*args, **kwargs)
        seen["frozen"] = json.loads(encoded)
        seen["retrieved"] = len(args[3])
        return encoded, retained

    monkeypatch.setattr(selector, "selection_payload", frozen_payload)
    import sabermetrics.assistant_strategy_context as adapter

    lookup_queries = []
    original_lookup = adapter.retrieve_rules

    def lookup(path, query, **kwargs):
        lookup_queries.append(query)
        return original_lookup(path, query, **kwargs)

    monkeypatch.setattr(adapter, "retrieve_rules", lookup)
    import sabermetrics.assistant_claim_fragments as fragments
    import sabermetrics.assistant_claim_review as claims

    original_sections = fragments.reviewed_sections

    def review(checker, parts, sources, request, **kwargs):
        seen.update(sources=sources, context=kwargs["research_context"])
        return parts, {"status": "checked", "fulfillment": "complete"}

    def sections(parts, corrected, receipt, sources=None):
        assert sources is seen["sources"]
        seen["preservation"] = True
        return original_sections(parts, corrected, receipt, sources)

    monkeypatch.setattr(claims, "review_claims", review)
    monkeypatch.setattr(fragments, "reviewed_sections", sections)
    gateway = Gateway(
        [
            (
                step(
                    call("search_cards", {"query": "batch-a"}),
                    call("search_cards", {"query": "batch-b"}),
                )
                if retrieved_count == 16
                else step(call("search_cards", {"query": "Incoming"}))
            ),
            selection(("incoming-0", "incoming-1")).model_dump(),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two additional cards."}],
        gateway=gateway,
        experiment_service=service,
        answer_review_gateway=gateway,
        selection_first=True,
    )
    assert result["status"] == "done", result
    assert len(gateway.requests) == 2
    selection_request = gateway.requests[1]
    assert selection_request.prompt_version == "assistant-selection.v8"
    payload = json.loads(selection_request.user)
    assert len(selection_request.user.encode()) <= 24000
    assert payload["candidates"] == seen["frozen"]["candidates"]
    assert payload["deck_sources"] == seen["frozen"]["deck_sources"]
    assert payload["omitted_candidate_ids"] == seen["frozen"]["omitted_candidate_ids"]
    assert any(
        c.get("card_id", c.get("id")) == commander["id"]
        and c.get("oracle_text") == commander["oracle_text"]
        for c in payload["deck_sources"]
    )
    assert (
        payload["strategic_obligation_vocabulary"]
        == seen["frozen"]["strategic_obligation_vocabulary"]
    )
    if retrieved_count == 16:
        assert seen["retrieved"] == 16
        assert len(payload["candidates"]) == 8
        assert len(payload["omitted_candidate_ids"]) == 8
    assert "shared_strategic_evidence" in payload, (
        len(selection_request.user.encode()),
        payload.get("omitted_shared_evidence"),
    )
    selection_wire = decode_selector(payload["shared_strategic_evidence"])
    assert [r["source_id"] for r in selection_wire["rows"]] == [
        c["card_id"] for c in payload["candidates"]
    ]
    colored = selection_wire["ability_profiles"][
        selection_wire["rows"][0]["ability_refs"][1]
    ]["source_mana"]
    assert colored["restrictions"] == ["legendary_spell_only"]
    assert colored["spell_benefit"]["kind"] == "spell_cant_be_countered"
    assert (
        selection_wire["bounded_rows_only"]["kinnan_extra_mana"][
            "inherits_source_spell_benefit"
        ]
        is False
    )
    stage = next(
        s
        for s in result["research"]["runtime"]["stages"]
        if s["stage"] == "selection_evidence"
    )
    assert stage["shared_strategic_evidence_included"] is True
    assert stage["omitted_shared_evidence"] == payload.get(
        "omitted_shared_evidence", []
    )
    assert stage["bounded_conditional_mana_source_count"] == (
        3 if retrieved_count == 16 else 2
    )
    assert stage["scope_source_count"] == len(payload["candidates"]) + 1
    review_wire = seen["context"]["shared_strategic_evidence"]
    assert (
        selection_wire["rows"][0]["status"] == "bounded_conditional_evidence"
    ), selection_wire["rows"][0]["unknowns"]
    assert (
        review_wire["mana_evidence"]["rows"][0]["status"]
        == "bounded_conditional_evidence"
    ), review_wire
    assert "cards" not in selection_wire and "rules" not in selection_wire
    assert selection_wire.get("rule_sources") or selection_wire.get(
        "rule_sources_context_omitted"
    )
    assert "cards" not in review_wire and "rules" not in review_wire
    assert lookup_queries == [
        "CR 106.12",
        "CR 106.12a",
        "restrictions riders additional mana",
    ]
    assert "resource_comparison_aid" in seen["context"]
    assert seen["preservation"]
    sources = seen["sources"]
    assert any(commander["oracle_text"] in s for s in sources)
    assert any(CR12 in s for s in sources) and any(CR12A in s for s in sources)
    assert any(s.startswith("Ruling source:") and RULING in s for s in sources)
    headers = [s.split("\n", 1)[0] for s in sources if s.startswith("Card: ")]
    assert len(headers) == len(set(headers))

    assert watcher_connections
    for connection in watcher_connections:
        assert_closed(connection)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("oracle_id", "other-oracle"),
        ("name", "Other name"),
        ("oracle_text", "{T}: Add {G}."),
        ("mana_cost", "{2}"),
        ("type_line", "Land"),
        ("layout", "transform"),
        ("card_faces", [{"oracle_text": "Other mechanics"}]),
        ("oracle_text_truncated", True),
        ("oracle_text_complete", False),
        ("truncation", {"characters": 1}),
    ],
)
def test_duplicate_mechanics_conflicts_abstain_before_deduplication(
    local_snapshot, reverse, field, value
):
    path, deck, _, source = local_snapshot
    source = {
        **source,
        "card_faces": [],
        "oracle_text_truncated": False,
        "oracle_text_complete": True,
        "truncation": {"characters": 0},
    }
    conflicting = {**source, field: value}
    cards = [source, conflicting]
    if reverse:
        cards.reverse()
    context = strategy_context(path, deck, cards)
    assert len(context["cards"]) == 2
    assert "conflicting_supplied_mechanics" in context["cards"][0]["snapshot_unknowns"]
    assert context["mana_evidence"]["rows"][0]["status"] == "unknown"
    assert not context["mana_evidence"]["rows"][0]["admitted_claims"]


def test_identical_duplicates_preserve_card_order_and_admission(local_snapshot):
    path, deck, _, source = local_snapshot
    assert strategy_context(path, deck, [source, deepcopy(source)]) == strategy_context(
        path, deck, [source]
    )


@pytest.mark.parametrize("mutation", ["card", "rule", "both", "payload"])
def test_cache_invalidates_all_evidence_on_database_write(local_snapshot, mutation):
    path, deck, _, source = local_snapshot
    cache = {}
    try:
        assert strategy_context(path, deck, [source], cache=cache)["mana_evidence"][
            "rows"
        ][0]["admitted_claims"]
        with sqlite3.connect(path) as connection:
            if mutation in {"card", "both"}:
                connection.execute(
                    "DELETE FROM assistant_card_fact_current WHERE card_id='halfling'"
                )
            if mutation in {"rule", "both"}:
                connection.execute(
                    "DELETE FROM assistant_rules WHERE rule_id='106.12a'"
                )
            if mutation == "payload":
                payload = json.loads(
                    connection.execute(
                        "SELECT payload FROM assistant_card_facts WHERE card_id='halfling'"
                    ).fetchone()[0]
                )
                payload["oracle_text"] = "{T}: Add {G}."
                connection.execute(
                    "UPDATE assistant_card_facts SET payload=? WHERE card_id='halfling'",
                    (json.dumps(payload),),
                )
        cached = strategy_context(path, deck, [source], cache=cache)
        assert cached == strategy_context(path, deck, [source])
        assert not cached["mana_evidence"]["rows"][0]["admitted_claims"]
    finally:
        close_strategy_cache(cache)


@pytest.mark.parametrize("replace", [False, True])
def test_cache_binds_database_path_and_replacement(local_snapshot, tmp_path, replace):
    import os

    path, deck, _, source = local_snapshot
    other = tmp_path / "other.db"
    with sqlite3.connect(path) as original, sqlite3.connect(other) as replacement:
        original.backup(replacement)
        replacement.execute(
            "DELETE FROM assistant_card_fact_current WHERE card_id='halfling'"
        )
    cache = {}
    try:
        assert strategy_context(path, deck, [source], cache=cache)["mana_evidence"][
            "rows"
        ][0]["admitted_claims"]
        if replace:
            stat = path.stat()
            other.replace(path)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            other = path
        context = strategy_context(other, deck, [source], cache=cache)
        assert not any(
            row["admitted_claims"] for row in context["mana_evidence"]["rows"]
        )
        # A replacement may conservatively abstain once; the next read recovers.
        assert strategy_context(other, deck, [source], cache=cache) == strategy_context(
            other, deck, [source]
        )
    finally:
        close_strategy_cache(cache)


@pytest.mark.parametrize(
    "read_kind", ["card_facts", "retrieve_rules", "build_mana_evidence"]
)
def test_generation_change_during_assembly_discards_mixed_evidence(
    local_snapshot, monkeypatch, read_kind
):
    import sabermetrics.assistant_strategy_context as adapter

    path, deck, _, source = local_snapshot
    original = getattr(adapter, read_kind)
    changed = False

    def read(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "DELETE FROM assistant_card_fact_current WHERE card_id='halfling'"
                )
        return result

    cache = {}
    if read_kind == "build_mana_evidence":
        strategy_context(path, deck, [source], cache=cache)
    monkeypatch.setattr(adapter, read_kind, read)
    context = strategy_context(path, deck, [source], cache=cache)
    assert context["unknowns"] == ["snapshot_generation_unproven"]
    assert not context["cards"] and not context["rules"]
    assert not context["mana_evidence"]["rows"]
    assert not cache


def test_watcher_cleanup_for_owned_and_explicit_cache(
    local_snapshot, watcher_connections
):
    path, deck, _, source = local_snapshot
    strategy_context(path, deck, [source])
    snapshot_card(path, source)
    for connection in watcher_connections:
        assert_closed(connection)
    cache = {}
    strategy_context(path, deck, [source], cache=cache)
    connection = watcher_connections[-1]
    connection.execute("PRAGMA data_version")
    close_strategy_cache(cache)
    close_strategy_cache(cache)
    assert not cache
    assert_closed(connection)


def test_unproven_generation_abstains_and_closes(
    local_snapshot, monkeypatch, watcher_connections
):
    import sabermetrics.assistant_strategy_context as adapter

    path, deck, _, source = local_snapshot
    cache = {}
    strategy_context(path, deck, [source], cache=cache)

    def unavailable(self):
        raise sqlite3.OperationalError("Cannot establish generation")

    monkeypatch.setattr(adapter._SnapshotWatcher, "generation", unavailable)
    assert strategy_context(path, deck, [source], cache=cache)["unknowns"] == [
        "snapshot_generation_unproven"
    ]
    assert not snapshot_card(path, source)["is_current"]
    assert not cache
    for connection in watcher_connections:
        assert_closed(connection)


@pytest.mark.parametrize("operation", ["snapshot_card", "strategy_context"])
def test_owned_watcher_closes_on_read_exception(
    local_snapshot, monkeypatch, watcher_connections, operation
):
    import sabermetrics.assistant_strategy_context as adapter

    path, deck, _, source = local_snapshot

    def failed(*args, **kwargs):
        raise RuntimeError("Read failed")

    monkeypatch.setattr(adapter, "card_facts", failed)
    with pytest.raises(RuntimeError, match="Read failed"):
        if operation == "snapshot_card":
            snapshot_card(path, source)
        else:
            strategy_context(path, deck, [source])
    assert watcher_connections
    for connection in watcher_connections:
        assert_closed(connection)


def test_wal_commit_invalidates_cache_without_database_stat_change(local_snapshot):
    path, deck, _, source = local_snapshot
    cache = {}
    # Keep the writer open so committed changes remain in the WAL.
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        strategy_context(path, deck, [source], cache=cache)
        before = path.stat()
        connection.execute(
            "DELETE FROM assistant_card_fact_current WHERE card_id='halfling'"
        )
        connection.commit()
        after = path.stat()
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        context = strategy_context(path, deck, [source], cache=cache)
        assert not context["mana_evidence"]["rows"][0]["admitted_claims"]
    finally:
        close_strategy_cache(cache)
        connection.close()


def test_snapshot_card_cache_generation_is_checked_independently(local_snapshot):
    path, _, _, source = local_snapshot
    cache = {}
    try:
        assert snapshot_card(path, source, cache)["is_current"]
        with sqlite3.connect(path) as connection:
            connection.execute(
                "DELETE FROM assistant_card_fact_current WHERE card_id='halfling'"
            )
        assert not snapshot_card(path, source, cache)["is_current"]
    finally:
        close_strategy_cache(cache)


def test_selector_projection_is_compact_without_losing_conditions_or_mana(request):
    from sabermetrics.assistant_mana_evidence import build_mana_evidence
    from tests.test_assistant_mana_evidence import HAWKER, card

    deck, commander, rules = request.getfixturevalue("inputs")
    sources = [
        card("Delighted Halfling", HALFLING),
        card("Omen Hawker", HAWKER),
        card("Elvish Mystic", "{T}: Add {G}."),
    ]
    context = {
        "mana_evidence": build_mana_evidence(deck, [*sources, commander], rules),
        "rules": rules["hits"],
        "unknowns": [],
    }
    original = deepcopy(context)
    wire = decode_selector(selection_strategy_context(context))
    assert (
        len(
            json.dumps(
                selection_strategy_context(context),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        <= 2500
    )
    assert context == original
    assert wire["conditions"] == context["mana_evidence"]["rows"][0]["conditions"]
    assert wire["commander_id"] == commander["id"]
    assert [r["source_id"] for r in wire["rows"]] == [c["id"] for c in sources]
    source_mana = wire["ability_profiles"][wire["rows"][1]["ability_refs"][0]][
        "source_mana"
    ]
    assert source_mana == {
        "quantity": 2,
        "type_options": ["C", "U"],
        "restrictions": ["activated_abilities_only"],
        "spell_benefit": None,
    }
    bonus = wire["bounded_rows_only"]["kinnan_extra_mana"]
    assert bonus["quantity"] == 1 and "actually produced" in bonus["type_choice"]
    assert bonus["inherits_source_spending_restriction"] is False
    assert bonus["inherits_source_spell_benefit"] is False
    assert [r["text"] for r in wire["rule_sources"]] == [
        h["text"] for h in rules["hits"]
    ]
    assert "sha256" not in json.dumps(wire) and "quote" not in json.dumps(wire)


def test_selector_projection_keeps_unknown_source_ids_explicit(local_snapshot):
    path, deck, _, source = local_snapshot
    context = strategy_context(path, deck, [{**source, "oracle_text": "{T}: Add {G}."}])
    wire = decode_selector(selection_strategy_context(context))
    assert wire["rows"][0]["source_id"] == source["id"]
    assert wire["rows"][0]["status"] == "unknown"
    assert wire["rows"][0]["unknowns"]
    assert "abilities" not in wire["rows"][0]


def test_actual_eight_source_reconstruction_fits_frozen_21675_byte_pool(request):
    # Public discovery_ledger printed facts from final/results.json Kinnan QA.
    # Reconstructed helper admission, not exact live wire replay. Pad only the
    # already-frozen baseline to its measured 21,675-byte size; never repack it.
    from sabermetrics.assistant_discovery_contract import (
        DiscoveryLedger,
        DiscoveryRequirement,
        candidate_from_card,
    )
    from sabermetrics.assistant_mana_evidence import build_mana_evidence
    from sabermetrics.assistant_selection import (
        attach_selection_evidence,
        selection_payload,
    )
    from tests.test_assistant_mana_evidence import card

    printed_facts = [
        (
            "a3ce0fa9-8742-4e2c-a8a0-00fc009f998a",
            "Ixalli's Lorekeeper",
            "{G}",
            "Creature — Human Druid",
            "{T}: Add one mana of any color. Spend this mana only to cast a Dinosaur spell or "
            "activate an ability of a Dinosaur source.",
        ),
        (
            "f9d3b046-0b95-4103-a630-4b3fb88bb60b",
            "Delighted Halfling",
            "{G}",
            "Creature — Halfling Citizen",
            "{T}: Add {C}.\n"
            "{T}: Add one mana of any color. Spend this mana only to cast a legendary spell, and "
            "that spell can't be countered.",
        ),
        (
            "215fd0b4-5817-4335-a0ed-1694e44bde41",
            "Omen Hawker",
            "{U}",
            "Creature — Octopus Advisor",
            "{T}: Add {C}{U}. Spend this mana only to activate abilities.",
        ),
        (
            "3f3b2c10-21f8-4e13-be83-4ef3fa36e123",
            "Elvish Mystic",
            "{G}",
            "Creature — Elf Druid",
            "{T}: Add {G}.",
        ),
        (
            "df317532-7d36-40fd-938f-e972749c8792",
            "Fyndhorn Elves",
            "{G}",
            "Creature — Elf Druid",
            "{T}: Add {G}.",
        ),
        (
            "2fcc69ff-8ab5-4e14-afe3-db892049a872",
            "Boreal Druid",
            "{G}",
            "Snow Creature — Elf Druid",
            "{T}: Add {C}.",
        ),
        (
            "a011aefe-5853-4435-b686-179e931ba897",
            "Joraga Treespeaker",
            "{G}",
            "Creature — Elf Druid",
            "Level up {1}{G} ({1}{G}: Put a level counter on this. Level up only as a sorcery.)\n"
            "LEVEL 1-4\n"
            "1/2\n"
            "{T}: Add {G}{G}.\n"
            "LEVEL 5+\n"
            "1/4\n"
            'Elves you control have "{T}: Add {G}{G}."',
        ),
        (
            "eb2ff7f2-b7fd-4984-a4e8-cf57b619c1a5",
            "Wose Pathfinder",
            "{1}{G}",
            "Creature — Human Shaman",
            "{T}: Add one mana of any color.\n"
            "{6}{G}, {T}: Another target creature gets +3/+3 and gains trample until end of turn.",
        ),
    ]
    deck, commander, rules = request.getfixturevalue("inputs")
    # Use a UUID-sized commander identity too.
    commander["id"] = "00000000-0000-0000-0000-000000000001"
    from tests.test_assistant_mana_evidence import seal

    seal(commander)
    deck["entries"][0]["card_id"] = commander["id"]
    sources = [
        card(
            name,
            text,
            id=cid,
            mana_cost=cost,
            type_line=kind,
            layout="leveler" if name == "Joraga Treespeaker" else "normal",
        )
        for cid, name, cost, kind, text in printed_facts
    ]
    requirement = DiscoveryRequirement(
        group_id="list_1",
        label="Low-cost mana-producing creatures",
        request_quote="Give me three additional mana-producing creatures for Kinnan, Bonder Prodigy. Prefer low casting cost.",
        requested_count=3,
    )
    ledger = DiscoveryLedger(
        candidates=[
            candidate_from_card(c, group_ids=["list_1"], rank=i + 1)
            for i, c in enumerate(sources)
        ]
    )
    encoded, retained = selection_payload(
        requirement.request_quote,
        [requirement],
        ledger,
        {c["id"]: c for c in sources},
        [],
        set(),
        [],
        deck_entries=[commander],
    )
    frozen = json.loads(encoded)
    assert [c.card_id for c in retained.candidates] == [c["id"] for c in sources]
    frozen["fixture_padding"] = ""
    encode = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    assert len(encode(frozen).encode()) <= 21675
    frozen["fixture_padding"] = "x" * (21675 - len(encode(frozen).encode()))
    baseline = encode(frozen)
    assert len(baseline.encode()) == 21675
    context = {
        "mana_evidence": build_mana_evidence(deck, [*sources, commander], rules),
        "rules": rules["hits"],
        "unknowns": [],
    }
    original = deepcopy(context)
    wire = selection_strategy_context(context)
    decoded = decode_selector(wire)
    assert [r["source_id"] for r in decoded["rows"]] == [c["id"] for c in sources]
    for actual, row in zip(
        context["mana_evidence"]["rows"], decoded["rows"], strict=True
    ):
        assert row["status"] == actual["status"]
        assert row["unknowns"] == actual["unknowns"]
        assert [wire["conditions"][i] for i in row["condition_refs"]] == actual[
            "conditions"
        ]
        if actual["status"] == "unknown":
            assert row["ability_refs"] == []
        else:
            abilities = [decoded["ability_profiles"][i] for i in row["ability_refs"]]
            assert len(abilities) == len(actual["abilities"])
            for ability, full in zip(abilities, actual["abilities"], strict=True):
                assert ability["activation_cost"] == full["activation_cost"]
                mana = ability["source_mana"]
                assert mana["quantity"] == full["source_mana"]["quantity"]
                assert mana["type_options"] == full["source_mana"]["type_options"]
                assert mana["restrictions"] == [
                    c["kind"] for c in full["source_mana"]["constraints"]
                ]
                benefit = full["source_mana"]["spell_benefit"]
                assert mana["spell_benefit"] == (
                    {k: v for k, v in benefit.items() if k != "quote"}
                    if benefit
                    else None
                )
    assert (
        sum(r["status"] == "bounded_conditional_evidence" for r in decoded["rows"]) == 5
    )
    packed = attach_selection_evidence(baseline, wire)
    payload = json.loads(packed)
    attached = payload.pop("shared_strategic_evidence")
    assert attached["rule_sources_context_omitted"] is True
    assert {
        k: v for k, v in attached.items() if k != "rule_sources_context_omitted"
    } == {k: v for k, v in wire.items() if k != "rule_sources"}
    # Every source, contract, alias and condition reference remains byte-equivalent
    # after decoding; optional arithmetic alone can yield space to the helper.
    for key, value in frozen.items():
        if key not in {"shared_resource_comparisons", "omitted_shared_evidence"}:
            assert payload[key] == value
    assert context == original
    assert len(packed.encode()) <= 24000
    projection_bytes = len(encode(attached).encode())
    print(
        f"eight-source reconstruction: base={len(baseline.encode())}, helper={projection_bytes}, attached={len(packed.encode())} bytes"
    )
    # Near-full frozen payload: disclose omission without trimming even one row.
    frozen["fixture_padding"] += "x" * (23900 - len(baseline.encode()))
    near_full = encode(frozen)
    omitted = json.loads(attach_selection_evidence(near_full, wire))
    assert "shared_strategic_evidence" not in omitted
    assert "shared_strategic_evidence" in omitted["omitted_shared_evidence"]
    assert omitted["candidates"] == frozen["candidates"]
    assert len(encode(omitted).encode()) <= 24000
