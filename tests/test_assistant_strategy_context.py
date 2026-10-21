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


def test_runtime_passes_shared_context_and_expanded_source_basis(
    prepared, monkeypatch, local_snapshot, tmp_path, watcher_connections
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
        for i in range(2)
    ]
    with sqlite3.connect(path) as conn:
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
            "UPDATE deck_entries SET oracle_id=?,name=?,mana_cost=?,type_line=? WHERE card_id='card-cmd'",
            tuple(
                commander[k] for k in ("oracle_id", "name", "mana_cost", "type_line")
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
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    seen = {}
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
            step(call("search_cards", {"query": "Incoming"})),
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
    assert selection_request.prompt_version == "assistant-selection.v6"
    payload = json.loads(selection_request.user)
    selection_wire = payload["shared_strategic_evidence"]
    review_wire = seen["context"]["shared_strategic_evidence"]
    assert (
        selection_wire["mana_evidence"]["rows"][0]["status"]
        == "bounded_conditional_evidence"
    ), selection_wire["mana_evidence"]["rows"][0]["unknowns"]
    assert (
        review_wire["mana_evidence"]["rows"][0]["status"]
        == "bounded_conditional_evidence"
    ), review_wire
    assert "cards" not in selection_wire and "rules" not in selection_wire
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
