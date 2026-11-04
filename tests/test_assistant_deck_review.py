import copy
import hashlib
import json
import sqlite3

import pytest

from sabermetrics import assistant_deck_review as review
from sabermetrics.assistant_card_facts import card_facts, import_snapshot


@pytest.fixture
def deck(tmp_path):
    db = tmp_path / "cards.db"
    cards = [
        {
            "object": "card",
            "id": f"card-{i}",
            "oracle_id": f"oracle-{i}",
            "name": f"Card {i}",
            "layout": "normal",
            "type_line": "Creature",
            "mana_cost": "{1}",
            "cmc": 1,
            "oracle_text": "",
            "color_identity": [],
            "keywords": [],
            "power": "1",
            "toughness": "1",
        }
        for i in range(25)
    ]
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE cards (id TEXT PRIMARY KEY, oracle_id TEXT, name TEXT, oracle_text TEXT)"
        )
        con.executemany(
            "INSERT INTO cards VALUES (?,?,?,?)",
            [(c["id"], c["oracle_id"], c["name"], c["oracle_text"]) for c in cards],
        )
    snapshot = tmp_path / "snapshot.json"

    def install():
        snapshot.write_text(json.dumps(cards))
        import_snapshot(db, snapshot, version="test")

    install()
    document = {
        "id": "deck",
        "revision": 7,
        "zones": [
            {"id": "library", "name": "Library"},
            {"id": "side", "name": "Sideboard"},
        ],
        "entries": [
            {
                "id": f"entry-{i}",
                "card_id": c["id"],
                "oracle_id": c["oracle_id"],
                "name": c["name"],
                "quantity": i + 1,
                "is_commander": i in (0, 1),
                "zone_id": None if i < 2 else "library",
            }
            for i, c in enumerate(cards)
        ],
    }
    return db, document, cards, install


def fallback(result, reason):
    packet, sources, receipt = result
    assert packet is None and sources == {}
    assert receipt["status"] == "fallback" and receipt["fallback"]
    assert receipt["route"] == "normal_tools"
    assert receipt["reason"] == reason


def test_more_than_twelve_entries_order_quantity_and_lossless_binding(deck):
    db, document, _, _ = deck
    document["entries"] = document["entries"][::-1]
    before = copy.deepcopy(document)
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready"
    decoded = review.decode_deck_review_packet(packet, sources)
    assert len(decoded) == 25
    assert [r["entry_id"] for r in decoded] == [e["id"] for e in document["entries"]]
    assert [r["quantity"] for r in decoded] == [
        e["quantity"] for e in document["entries"]
    ]
    assert [r["card_id"] for r in decoded if r["is_commander"]] == ["card-1", "card-0"]
    assert len(packet["provenance"]) == 1
    for row in decoded:
        exact = card_facts(db, row["card_id"])
        assert sources[row["card_id"]]["printed_evidence"] == exact
        assert row["provenance"] == exact["provenance"]
        assert row["card"] == {
            k: v
            for k, v in exact["card"].items()
            if k not in {"image_uri", "is_legal_in_99", "roles"}
        }
        encoded = json.dumps(
            exact,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        assert row["source_record_hash"] == hashlib.sha256(encoded).hexdigest()
    assert receipt["packet_bytes"] <= 28000
    assert (
        len(json.dumps(packet, ensure_ascii=False).encode()) == receipt["packet_bytes"]
    )
    assert document == before
    decoded[0]["card"]["name"] = "changed"
    assert sources["card-24"]["name"] == "Card 24"


def test_public_scope_and_scope_hash_ignore_private_entries(deck):
    db, document, _, _ = deck
    packet, _, _ = review.build_deck_review_packet(db, document)
    for i, name in enumerate(
        ["Sideboard", " considering ", "Notes", "Maybeboard", "draft"]
    ):
        document["entries"].append(
            {"id": f"private-{i}", "card_id": "missing", "zone_name": name}
        )
    second, sources, _ = review.build_deck_review_packet(db, document)
    assert second == packet and len(sources) == 25
    document["entries"][2]["quantity"] += 1
    third, _, _ = review.build_deck_review_packet(db, document)
    assert third["document_scope_hash"] != packet["document_scope_hash"]


def test_complete_faces_level_bands_and_restrictions(deck):
    db, document, cards, install = deck
    text = "LEVEL 2-4\n{T}: Add {B}. Spend this mana only to cast creatures.\nLEVEL 5+\nActivate only as a sorcery."
    cards[0].update(
        layout="modal_dfc",
        card_faces=[
            {
                "name": "Front",
                "type_line": "Creature",
                "oracle_text": text,
                "mana_cost": "{B}",
                "power": "2",
                "toughness": "3",
            },
            {"name": "Back", "type_line": "Land", "oracle_text": "{T}: Add {B}."},
        ],
    )
    install()
    packet, sources, _ = review.build_deck_review_packet(db, document)
    row = review.decode_deck_review_packet(packet)[0]
    assert row["card"]["layout"] == "modal_dfc"
    assert row["card"]["card_faces"] == cards[0]["card_faces"]
    assert sources["card-0"]["oracle_text"] == ""
    assert row["card"]["card_faces"][0]["oracle_text"] == text


def test_whole_record_utf8_size_fallback(deck):
    db, document, cards, install = deck
    for card in cards:
        card["oracle_text"] = "é" * 800
    install()
    fallback(review.build_deck_review_packet(db, document), "packet_size_limit")


@pytest.mark.parametrize("kind", ["missing", "catalog", "text", "truncated", "face"])
def test_missing_or_incomplete_sources_abstain(deck, kind):
    db, document, cards, install = deck
    reason = "incomplete_printed_record"
    if kind == "missing":
        document["entries"][0]["card_id"] = "missing"
        reason = "missing_or_unsupported_source"
    elif kind == "catalog":
        with sqlite3.connect(db) as con:
            con.execute(
                "DELETE FROM assistant_card_fact_current WHERE card_id='card-0'"
            )
        reason = "missing_or_unsupported_source"
    else:
        if kind == "text":
            del cards[0]["oracle_text"]
        elif kind == "truncated":
            cards[0]["oracle_text"] = "x" * 5000
            reason = "truncated_source"
        else:
            cards[0]["card_faces"] = [
                {"name": "Front", "oracle_text": ""},
                {"name": "Back"},
            ]
        install()
    fallback(review.build_deck_review_packet(db, document), reason)


@pytest.mark.parametrize(
    "change", [{"oracle_id": "wrong"}, {"name": "wrong"}, {"cardid": "wrong"}]
)
def test_identity_conflicts(deck, change):
    db, document, _, _ = deck
    document["entries"][0].update(change)
    fallback(review.build_deck_review_packet(db, document), "identity_conflict")


def test_normalized_alias_and_repeated_identity_preserve_all_entries(deck):
    db, document, _, _ = deck
    raw = document["entries"][0]
    raw["cardid"] = " " + raw.pop("card_id") + " "
    document["entries"].append({**raw, "id": "another-entry", "quantity": 3})
    packet, sources, _ = review.build_deck_review_packet(db, document)
    assert len(packet["entries"]) == 26 and len(sources) == 25
    assert review.decode_deck_review_packet(packet)[-1]["quantity"] == 3
    document["entries"][-1]["id"] = raw["id"]
    fallback(review.build_deck_review_packet(db, document), "duplicate_conflict")


def test_entry_limit_before_any_reads(deck, monkeypatch):
    db, document, _, _ = deck
    document["entries"] = [{**document["entries"][0], "id": str(i)} for i in range(129)]
    monkeypatch.setattr(
        review, "card_facts_from_connection", lambda *a: pytest.fail("must not read")
    )
    fallback(review.build_deck_review_packet(db, document), "entry_limit")


@pytest.mark.parametrize(
    "zone_id,zone_name",
    [
        ("side", "Library"),
        ("library", "Sideboard"),
        ("unknown", "Library"),
        ("unknown", "Sideboard"),
    ],
)
@pytest.mark.parametrize("commander", [False, True])
def test_unresolved_or_conflicting_zone_scope_abstains_before_reads(
    deck, monkeypatch, zone_id, zone_name, commander
):
    db, document, _, _ = deck
    document["entries"].append(
        {
            "id": "private",
            "card_id": "missing",
            "zone_id": zone_id,
            "zone_name": zone_name,
            "is_commander": commander,
        }
    )
    monkeypatch.setattr(
        review, "card_facts_from_connection", lambda *a: pytest.fail("must not read")
    )
    fallback(review.build_deck_review_packet(db, document), "unsupported_scope")


@pytest.mark.parametrize("label", [None, "Library", "Unsorted"])
def test_default_library_without_zone_definitions_is_public(deck, label):
    db, document, _, _ = deck
    document["zones"] = []
    for entry in document["entries"]:
        entry["zone_id"] = None
        if label is not None:
            entry["zone_name"] = label
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready"
    assert len(packet["entries"]) == len(sources) == 25


def test_resolved_zone_labels_allow_normalization_and_exclude_private(deck):
    db, document, _, _ = deck
    document["entries"][2]["zone_name"] = " library "
    document["entries"].append(
        {
            "id": "private",
            "card_id": "missing",
            "zone_id": "side",
            "zone_name": " SIDEBOARD ",
        }
    )
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready"
    assert len(packet["entries"]) == len(sources) == 25
    assert "missing" not in json.dumps((packet, sources, receipt))


@pytest.fixture
def same_oracle_printings(deck):
    db, document, cards, install = deck
    cards[1] = {**copy.deepcopy(cards[0]), "id": "card-1"}
    for i in (0, 1):
        cards[i].update(
            scryfall_uri=f"https://scryfall.com/card/set{i}/{i}/card-0?utm_source=api",
            rulings_uri=f"https://api.scryfall.com/cards/card-{i}/rulings",
            released_at=f"202{i}-01-01",
            prices={"usd": str(i + 1)},
        )
        document["entries"][i].update(name="Card 0", oracle_id="oracle-0")
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE cards SET oracle_id='oracle-0', name='Card 0' WHERE id='card-1'"
        )
    install()
    return db, document, cards, install


@pytest.mark.parametrize("reverse", [False, True])
def test_same_oracle_printing_metadata_is_lossless(same_oracle_printings, reverse):
    db, document, _, _ = same_oracle_printings
    if reverse:
        document["entries"].reverse()
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready", receipt
    assert receipt["packet_hash"] == review._hash(packet)
    decoded = review.decode_deck_review_packet(packet, sources)
    assert len(decoded) == len(sources) == 25
    for row in decoded:
        exact = card_facts(db, row["card_id"])
        assert row["card"] == {
            k: v
            for k, v in exact["card"].items()
            if k not in {"image_uri", "is_legal_in_99", "roles"}
        }
        assert row["provenance"] == exact["provenance"]
        assert row["source_record_hash"] == review._hash(exact)
        assert sources[row["card_id"]]["printed_evidence"] == exact


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        {"mana_cost": "{2}"},
        {"cmc": 2},
        {"oracle_text": "Activate only as a sorcery."},
        {"type_line": "Artifact Creature"},
        {"layout": "token"},
        {"color_identity": ["B"]},
        {"keywords": ["Flying"]},
        {"power": "2"},
        {"toughness": "2"},
        {"loyalty": None},
        {"defense": "3"},
        {
            "card_faces": [
                {"name": "Front", "oracle_text": ""},
                {"name": "Back", "oracle_text": "Spend this mana only on creatures."},
            ]
        },
    ],
)
def test_same_oracle_mechanics_conflicts_abstain_in_either_order(
    same_oracle_printings, change, reverse
):
    db, document, cards, install = same_oracle_printings
    cards[1].update(change)
    install()
    if reverse:
        document["entries"].reverse()
    fallback(review.build_deck_review_packet(db, document), "duplicate_conflict")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        None,
        {"mana_cost": "{B}"},
        {"power": "3"},
        {"type_line": "Land"},
        {"oracle_text": "Activate only as a sorcery."},
    ],
)
def test_face_mechanics_comparison_excludes_only_metadata(
    same_oracle_printings, reverse, change
):
    db, document, cards, install = same_oracle_printings
    for i in (0, 1):
        cards[i].update(
            layout="modal_dfc",
            card_faces=[
                {
                    "name": "Front",
                    "oracle_text": "",
                    "mana_cost": "{1}",
                    "power": "1",
                    "type_line": "Creature",
                    "scryfall_uri": cards[i]["scryfall_uri"],
                },
                {"name": "Back", "oracle_text": "{T}: Add {B}."},
            ],
        )
    if change:
        cards[1]["card_faces"][0].update(change)
    install()
    if reverse:
        document["entries"].reverse()
    result = review.build_deck_review_packet(db, document)
    if change:
        fallback(result, "duplicate_conflict")
    else:
        packet, sources, receipt = result
        assert receipt["status"] == "ready"
        for row in review.decode_deck_review_packet(packet, sources):
            assert row["card"].get("card_faces") == sources[row["card_id"]].get(
                "card_faces"
            )


def test_conflicting_duplicate_oracle_records_abstain(deck, monkeypatch):
    db, document, _, _ = deck
    original = review.card_facts_from_connection
    document["entries"][1]["oracle_id"] = "oracle-0"

    def read(path, card_id):
        result = original(path, card_id)
        if card_id == "card-1":
            result["card"]["oracle_id"] = "oracle-0"
            result["card"]["oracle_text"] = "Conflicting printed ability."
        return result

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    fallback(review.build_deck_review_packet(db, document), "duplicate_conflict")


def test_missing_and_explicit_null_printed_fields_remain_distinct(deck):
    db, document, cards, install = deck
    cards[0]["loyalty"] = None
    install()
    packet, _, _ = review.build_deck_review_packet(db, document)
    first = review.decode_deck_review_packet(packet)[0]["card"]
    assert "loyalty" in first and first["loyalty"] is None
    assert "defense" not in first


def test_unsupported_scope_and_quantity_abstain(deck):
    db, document, _, _ = deck
    document["entries"][2]["zone_id"] = "unknown-zone"
    fallback(review.build_deck_review_packet(db, document), "unsupported_scope")
    document["entries"][2]["zone_id"] = "library"
    document["entries"][2]["quantity"] = True
    fallback(review.build_deck_review_packet(db, document), "unsupported_quantity")


@pytest.mark.parametrize("exception", [ValueError("cancel"), RuntimeError("cancel")])
def test_cancellation_propagates_between_reads_and_closes_observer(
    deck, monkeypatch, exception
):
    db, document, _, _ = deck
    original = review.card_facts_from_connection
    calls = []

    def read(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(review, "card_facts_from_connection", read)

    def check():
        if len(calls) == 2:
            raise exception

    with pytest.raises(type(exception)) as caught:
        review.build_deck_review_packet(db, document, check_current=check)
    assert caught.value is exception and len(calls) == 2
    with sqlite3.connect(db, timeout=0) as con:
        con.execute("BEGIN EXCLUSIVE")


def test_generation_change_abstains_and_no_cache_across_calls(deck, monkeypatch):
    db, document, _, _ = deck
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA journal_mode=WAL")
    original = review.card_facts_from_connection
    calls = []

    def read(*args):
        result = original(*args)
        calls.append(args[1])
        if len(calls) == 2:
            with sqlite3.connect(db) as con:
                con.execute(
                    "UPDATE cards SET oracle_text='new catalog text' WHERE id='card-0'"
                )
        return result

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    fallback(review.build_deck_review_packet(db, document), "source_changed")
    assert len(calls) == 25
    packet, _, receipt = review.build_deck_review_packet(db, document)
    assert packet and receipt["status"] == "ready"
    assert len(calls) == 50


def test_database_errors_are_controlled_without_raw_details(tmp_path):
    db = tmp_path / "bad.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE unrelated (id)")
    result = review.build_deck_review_packet(
        db, {"entries": [{"id": "entry", "card_id": "card"}]}
    )
    fallback(result, "invalid_or_unavailable_source")
    assert "no such table" not in json.dumps(result)


@pytest.fixture
def captured_shape_deck(deck):
    """Synthetic Oracle-keyed catalog, distinct printing UUIDs and long URLs."""
    db, document, cards, install = deck
    template = copy.deepcopy(cards[0])
    cards[:] = []
    for i in range(12):
        oracle_id = f"00000000-0000-4000-8000-{i:012d}"
        printing_id = f"10000000-0000-4000-8000-{i:012d}"
        card = {
            **template,
            "id": printing_id,
            "oracle_id": oracle_id,
            "name": f"Synthetic captured card {i}",
            "oracle_text": (
                "{1}, {T}, Sacrifice another creature: Draw a card. "
                "Activate only as a sorcery and only once each turn."
            ),
            "scryfall_uri": (
                f"https://scryfall.com/card/test/{i}/synthetic-captured-card-{i}"
                "?utm_source=api"
            ),
            "rulings_uri": f"https://api.scryfall.com/cards/{printing_id}/rulings",
            "released_at": "2026-10-20",
            "keywords": ["Flying", "Vigilance"] if i % 2 else [],
        }
        if i % 3 == 0:
            del card["power"]
            del card["toughness"]
        if i == 7:
            card["layout"] = "modal_dfc"
            card["card_faces"] = [
                {
                    "name": "Front",
                    "oracle_text": card["oracle_text"],
                    "mana_cost": "{B}",
                },
                {
                    "name": "Back",
                    "oracle_text": "{T}: Add {B}. Spend this mana only to cast creatures.",
                },
            ]
        cards.append(card)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM cards")
        con.executemany(
            "INSERT INTO cards VALUES (?,?,?,?)",
            [
                (c["oracle_id"], c["oracle_id"], c["name"], c["oracle_text"])
                for c in cards
            ],
        )
    document["entries"] = [
        {
            "id": f"20000000-0000-4000-8000-{i:012d}",
            "card_id": card["oracle_id"],
            "oracle_id": card["oracle_id"],
            "quantity": 1 if i else 3,
            "is_commander": i == 1,
            "zone_id": "library",
        }
        for i, card in enumerate(cards)
    ]
    install()
    return db, document


def test_captured_shape_metadata_interning_preserves_all_entries(captured_shape_deck):
    db, document = captured_shape_deck
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready", receipt
    assert receipt["packet_bytes"] == len(
        json.dumps(packet, ensure_ascii=False).encode()
    )
    assert receipt["packet_bytes"] <= 28000
    assert receipt["packet_hash"] == review._hash(packet)
    decoded = review.decode_deck_review_packet(packet, sources)
    assert len(decoded) == len(sources) == len(document["entries"]) == 12
    for row, entry in zip(decoded, document["entries"], strict=True):
        exact = sources[entry["card_id"]]["printed_evidence"]
        assert row["entry_id"] == entry["id"]
        assert row["card_id"] == row["oracle_id"] == entry["card_id"]
        assert row["quantity"] == entry["quantity"]
        assert row["is_commander"] == entry["is_commander"]
        assert row["card"] == {
            k: v
            for k, v in exact["card"].items()
            if k not in {"image_uri", "is_legal_in_99", "roles"}
        }
        assert row["provenance"] == exact["provenance"]
        assert row["source_record_hash"] == review._hash(exact)
        assert row["source_record_ref"] == entry["card_id"]
    # Each source identity is sent once, even when catalog ID equals Oracle ID.
    wire = json.dumps(packet, ensure_ascii=False)
    for card_id, source in sources.items():
        assert wire.count(card_id) == 1
        assert source["source_record_hash"] not in wire
    assert len(packet["card_schemas"]) < len(sources)
    assert (
        len(wire.encode()) < len(json.dumps(decoded, ensure_ascii=False).encode()) / 2
    )
    refs = review.decode_deck_review_packet(json.loads(wire))
    assert all("source_record_hash" not in row for row in refs)
    assert [r["source_record_ref"] for r in refs] == list(sources)


def test_metadata_urls_null_empty_unicode_and_nonstandard_values_round_trip(
    deck, monkeypatch
):
    db, document, cards, install = deck
    cards[0].update(released_at=None, keywords=None)
    cards[1].update(
        scryfall_uri="https://scryfall.com/é/00000000-0000-4000-8000-000000000001?q=é",
        rulings_uri="https://api.scryfall.com/relative/path/é",
    )
    install()
    original = review.card_facts_from_connection

    def read(*args):
        evidence = original(*args)
        if args[1] == "card-0":
            evidence["card"].update(scryfall_uri=None, rulings_uri="")
        return evidence

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready"
    for row in review.decode_deck_review_packet(packet):
        for key in (*review.INTERN_FIELDS, *review.URL_FIELDS):
            exact = sources[row["card_id"]]["printed_evidence"]["card"]
            if key in exact:
                assert row["card"][key] == exact[key]


def test_maximum_scope_keeps_all_128_entries_and_reads_each(deck, monkeypatch):
    db, document, _, _ = deck
    originals = document["entries"]
    document["entries"] = [
        {**originals[i % len(originals)], "id": f"scope-entry-{i}"} for i in range(128)
    ]
    original = review.card_facts_from_connection
    reads = []

    def read(path, card_id):
        reads.append(card_id)
        return original(path, card_id)

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready"
    decoded = review.decode_deck_review_packet(packet, sources)
    assert len(decoded) == 128
    assert reads == [e["card_id"] for e in document["entries"]]
    assert [e["entry_id"] for e in decoded] == [e["id"] for e in document["entries"]]
    assert [e["quantity"] for e in decoded] == [
        e["quantity"] for e in document["entries"]
    ]


def test_wal_unrelated_writes_do_not_invalidate_and_all_reads_share_snapshot(
    deck, monkeypatch
):
    db, document, _, _ = deck
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE assistant_sessions (id INTEGER, cost REAL)")
    original = review.card_facts_from_connection
    connections = []

    def read(connection, card_id):
        assert connection.in_transaction
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE cards SET name=name")
        connections.append(connection)
        with sqlite3.connect(db) as writer:
            writer.execute("INSERT INTO assistant_sessions VALUES (1,0.1)")
            # Even changes to canonical tables outside this deck are irrelevant.
            writer.execute(
                "INSERT OR REPLACE INTO assistant_card_fact_current VALUES ('outside','other')"
            )
        return original(connection, card_id)

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    packet, sources, receipt = review.build_deck_review_packet(db, document)
    assert receipt["status"] == "ready"
    assert packet and len(sources) == len(connections) == 25
    assert all(connection is connections[0] for connection in connections)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


@pytest.mark.parametrize(
    "change",
    [
        "pointer",
        "remove_pointer",
        "remove_catalog",
        "catalog_identity",
        "catalog_mechanics",
        "payload",
        "remove_payload",
        "metadata",
        "remove_metadata",
        "pointer_aba_mutated_payload",
    ],
)
def test_wal_canonical_changes_fail_closed_without_mixed_facts(
    deck, monkeypatch, change
):
    db, document, _, _ = deck
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
    before = {
        entry["card_id"]: card_facts(db, entry["card_id"])
        for entry in document["entries"]
    }
    original = review.card_facts_from_connection
    reads = []

    def read(connection, card_id):
        evidence = original(connection, card_id)
        reads.append((card_id, evidence))
        if len(reads) == 2:
            with sqlite3.connect(db) as writer:
                snapshot_hash = writer.execute(
                    "SELECT snapshot_hash FROM assistant_card_fact_current WHERE card_id='card-3'"
                ).fetchone()[0]
                if change == "pointer":
                    writer.execute(
                        "UPDATE assistant_card_fact_current SET snapshot_hash='other' WHERE card_id='card-3'"
                    )
                elif change == "remove_pointer":
                    writer.execute(
                        "DELETE FROM assistant_card_fact_current WHERE card_id='card-3'"
                    )
                elif change == "remove_catalog":
                    writer.execute("DELETE FROM cards WHERE id='card-3'")
                elif change == "catalog_identity":
                    writer.execute("UPDATE cards SET name='changed' WHERE id='card-3'")
                elif change == "catalog_mechanics":
                    writer.execute(
                        "UPDATE cards SET oracle_text='changed' WHERE id='card-3'"
                    )
                elif change in {"payload", "pointer_aba_mutated_payload"}:
                    payload = json.loads(
                        writer.execute(
                            "SELECT payload FROM assistant_card_facts WHERE card_id='card-3' AND snapshot_hash=?",
                            (snapshot_hash,),
                        ).fetchone()[0]
                    )
                    payload["oracle_text"] = "Changed ability."
                    writer.execute(
                        "UPDATE assistant_card_facts SET payload=? WHERE card_id='card-3' AND snapshot_hash=?",
                        (json.dumps(payload), snapshot_hash),
                    )
                    if change == "pointer_aba_mutated_payload":
                        writer.execute(
                            "UPDATE assistant_card_fact_current SET snapshot_hash='other' WHERE card_id='card-3'"
                        )
                        writer.commit()
                        writer.execute(
                            "UPDATE assistant_card_fact_current SET snapshot_hash=? WHERE card_id='card-3'",
                            (snapshot_hash,),
                        )
                elif change == "remove_payload":
                    writer.execute(
                        "DELETE FROM assistant_card_facts WHERE card_id='card-3' AND snapshot_hash=?",
                        (snapshot_hash,),
                    )
                elif change == "metadata":
                    writer.execute(
                        "UPDATE assistant_fact_snapshots SET version='changed' WHERE content_hash=?",
                        (snapshot_hash,),
                    )
                else:
                    writer.execute(
                        "DELETE FROM assistant_fact_snapshots WHERE content_hash=?",
                        (snapshot_hash,),
                    )
        return evidence

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    fallback(review.build_deck_review_packet(db, document), "source_changed")
    assert len(reads) == 25
    assert all(evidence == before[card_id] for card_id, evidence in reads)


def test_database_path_replacement_abstains_even_with_identical_content(
    deck, monkeypatch
):
    import shutil

    db, document, _, _ = deck
    replacement = db.with_name("replacement.db")
    shutil.copyfile(db, replacement)
    original = review.card_facts_from_connection
    reads = []

    def read(connection, card_id):
        result = original(connection, card_id)
        reads.append(card_id)
        if len(reads) == 2:
            replacement.replace(db)
        return result

    monkeypatch.setattr(review, "card_facts_from_connection", read)
    fallback(review.build_deck_review_packet(db, document), "source_changed")
    assert len(reads) == 2


def test_oversized_raw_payload_abstains_before_reader(deck, monkeypatch):
    db, document, _, _ = deck
    with sqlite3.connect(db) as writer:
        writer.execute(
            "UPDATE assistant_card_facts SET payload=? WHERE card_id='card-0'",
            ("x" * (review.MAX_SOURCE_VALUE_BYTES + 1),),
        )
    monkeypatch.setattr(
        review,
        "card_facts_from_connection",
        lambda *args: pytest.fail("unbounded reader"),
    )
    fallback(
        review.build_deck_review_packet(db, document), "invalid_or_unavailable_source"
    )
