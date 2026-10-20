"""Public composition neighbors share Research scope and never query private decks."""

import sqlite3
from copy import deepcopy

import pytest

from sabermetrics.assistant_comparable_decks import comparable_decks
from tests.test_assistant_associations import corpus as _corpus

corpus = _corpus


def test_public_composition_deduplicates_printings_quantities_lists_and_basics(corpus):
    path, document = corpus
    before = deepcopy(document)
    with sqlite3.connect(path) as conn:
        # A private-only list must never enter the public cohort.
        conn.executescript(
            "CREATE TABLE deck_documents(id TEXT, document TEXT); INSERT INTO deck_documents VALUES('private','secret'); INSERT INTO deck_cards VALUES('private','a',1,0);"
        )
    result = comparable_decks(path, document)
    assert result["status"] == "available"
    assert result["cohort_lists"] == 20
    assert result["current_composition"]["oracle_ids"] == ["oa"]
    assert len(result["comparable_decks"]) == 3
    best = result["comparable_decks"][0]
    assert best["deck_id"] != "private"
    assert best["neighbor_denominator"] == 2
    assert best["shared_cards"] == 1
    assert best["union_denominator"] == 2
    assert best["overlap"] == 0.5
    assert best["role_similarity"] is None
    assert best["role_profile"]["status"] == "unavailable"
    compositions = [
        tuple(c["oracle_id"] for c in n["composition"])
        for n in result["comparable_decks"]
    ]
    assert len(set(compositions)) == 3
    assert all("ocmd" not in c and "obasic" not in c for c in compositions)
    assert document == before
    assert result["source_freshness"] == "unknown"


def test_canonical_roles_use_database_text_not_document_roles(corpus):
    path, document = corpus
    document["entries"][1].update(
        oracle_text="Destroy target creature.", role="removal"
    )
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE cards ADD COLUMN oracle_text TEXT")
        conn.execute(
            "UPDATE cards SET oracle_text='Draw a card.' WHERE id IN ('a','a2','b')"
        )
        conn.execute("UPDATE cards SET oracle_text='Flying' WHERE id IN ('s','rare')")
    result = comparable_decks(path, document)
    current = result["current_composition"]["role_profile"]
    assert current["role_counts"] == {"draw": 1}
    assert current["rate_denominator"] == 1
    assert current["status"] == "available"
    assert all(n["role_similarity"] is not None for n in result["comparable_decks"])
    assert all(
        n["retrieval_similarity"]
        == pytest.approx(0.85 * n["overlap"] + 0.15 * n["role_similarity"])
        for n in result["comparable_decks"]
    )


def test_partial_canonical_text_profiles_do_not_influence_ranking(corpus):
    path, document = corpus
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE cards ADD COLUMN oracle_text TEXT")
        conn.execute("UPDATE cards SET oracle_text='Draw a card.' WHERE id='a'")
    best = comparable_decks(path, document)["comparable_decks"][0]
    assert best["role_profile"]["status"] == "partial"
    assert best["role_profile"]["canonical_text_cards"] == 1
    assert best["role_similarity"] is None
    assert best["retrieval_similarity"] == best["overlap"]


def test_scope_matches_associations_and_filters_sideboard(corpus):
    from sabermetrics.assistant_associations import research_card_associations

    path, document = corpus
    document["entries"].extend(
        [
            {"oracle_id": "ob", "zone_name": "Sideboard"},
            {"oracle_id": "obasic", "type_line": "Basic Land — Island"},
        ]
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE research_results SET commander_id='other' WHERE deck_id='19'"
        )
        conn.execute(
            "UPDATE research_results SET tournament_date='2000-01-01' WHERE deck_id='18'"
        )
        conn.execute("UPDATE tournament_results SET player_count=1 WHERE id='17'")
    result = comparable_decks(path, document)
    associations = research_card_associations(path, document, "oa")
    assert result["cohort_lists"] == associations["cohort_lists"] == 17
    assert result["min_event_size"] == associations["min_event_size"]
    assert result["current_composition"]["oracle_ids"] == ["oa"]


def test_partner_scope_requires_both_commanders(corpus):
    path, document = corpus
    document["entries"].append(
        {"card_id": "partner", "oracle_id": "opartner", "is_commander": True}
    )
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO deck_cards VALUES(?,'partner',1,1)",
            [(str(i),) for i in range(12)],
        )
    result = comparable_decks(path, document)
    assert result["cohort_lists"] == 12
    assert all(
        "opartner" not in [c["oracle_id"] for c in n["composition"]]
        for n in result["comparable_decks"]
    )


def test_no_overlap_and_empty_library_are_explicit(corpus):
    path, document = corpus
    document["entries"][1]["oracle_id"] = "unrecorded"
    result = comparable_decks(path, document)
    assert result["status"] == "no_comparable_decks"
    assert result["comparable_decks"] == []
    document["entries"] = document["entries"][:1]
    assert comparable_decks(path, document)["status"] == "empty_comparable_library"


def test_readonly_missing_floor_and_unknown_commander(corpus, tmp_path):
    path, document = corpus
    missing = tmp_path / "missing.db"
    assert comparable_decks(missing, document)["status"] == "unavailable"
    assert not missing.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE tournament_results DROP COLUMN player_count")
    result = comparable_decks(path, document)
    assert result["status"] == "available"
    assert result["min_event_size"] is None
    assert any("none was applied" in note for note in result["limitations"])
    document["entries"][0].update(card_id="unknown", oracle_id="unknown")
    assert comparable_decks(path, document)["status"] == "unknown_commander"


def test_bounds_abstain_instead_of_returning_partial_neighbors(corpus, monkeypatch):
    import sabermetrics.assistant_comparable_decks as module

    path, document = corpus
    monkeypatch.setattr(module, "MAX_COHORT", 15)
    result = comparable_decks(path, document)
    assert result["status"] == "cohort_too_large"
    assert result["comparable_decks"] == []
    assert result["cohort_count_is_lower_bound"]
    monkeypatch.setattr(module, "MAX_COHORT", 2000)
    monkeypatch.setattr(module, "MAX_MEMBERSHIPS", 2)
    assert comparable_decks(path, document)["status"] == "composition_too_large"


def test_deadline_and_argument_guards(corpus, monkeypatch):
    import sabermetrics.assistant_comparable_decks as module

    path, document = corpus
    for args in ({"limit": 4}, {"limit": 0}, {"window_days": 2}):
        with pytest.raises(ValueError):
            comparable_decks(path, document, **args)
    ticks = iter([0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks, 10))
    result = comparable_decks(path, document)
    assert result["status"] == "query_budget_exceeded"
    assert result["comparable_decks"] == []


def test_diversity_penalty_can_select_distinct_neighbor_over_near_duplicate(corpus):
    path, document = corpus
    target = list("abcdefghij")
    document["entries"] = document["entries"][:1] + [{"oracle_id": c} for c in target]
    compositions = {
        "best": set("abcdefghXY"),
        "near": set("abcdefghXYZ"),
        "diverse": set("abcdeijUVW"),
    }
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM research_results")
        conn.execute("DELETE FROM deck_cards")
        for oid in sorted(set(target).union(*compositions.values())):
            conn.execute(
                "INSERT INTO cards VALUES(?,?,?,'Artifact')", (f"new-{oid}", oid, oid)
            )
        for deck, ids in compositions.items():
            conn.execute(
                "INSERT INTO research_results VALUES(?,?,?,date('now','localtime'))",
                (deck, deck, "cmd"),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,100)", (deck,))
            conn.executemany(
                "INSERT INTO deck_cards VALUES(?,?,1,0)",
                [(deck, f"new-{oid}") for oid in ids],
            )
    result = comparable_decks(path, document, limit=2)
    assert [d["deck_id"] for d in result["comparable_decks"]] == ["best", "diverse"]
    assert result["comparable_decks"][1]["maximum_overlap_with_prior_neighbors"] > 0


def test_many_printings_keep_canonical_facts_with_bounded_sql_work(corpus, monkeypatch):
    import sabermetrics.assistant_comparable_decks as module

    path, document = corpus
    with sqlite3.connect(path) as conn:
        # Match the catalog's indexes without relying on elapsed wall time.
        conn.executescript(
            "CREATE UNIQUE INDEX cards_id ON cards(id);"
            "CREATE INDEX cards_oracle ON cards(oracle_id);"
            "ALTER TABLE cards ADD COLUMN oracle_text TEXT;"
        )
        conn.execute("UPDATE cards SET oracle_text='Draw a card.'")
    expected = comparable_decks(path, document)
    with sqlite3.connect(path) as conn:
        # Later printings disagree deliberately: the minimum id still wins.
        conn.executemany(
            "INSERT INTO cards VALUES(?, 'oa', 'Later printing', 'Creature', 'Flying')",
            [(f"z-printing-{i:04}",) for i in range(600)],
        )
    connect = sqlite3.connect
    steps = 0

    class WorkBoundedConnection(sqlite3.Connection):
        def set_progress_handler(self, callback, interval):
            def bounded():
                nonlocal steps
                steps += interval
                return 1 if steps > 100_000 else callback()

            super().set_progress_handler(bounded, interval)

    monkeypatch.setattr(
        module.sqlite3,
        "connect",
        lambda *args, **kwargs: connect(*args, **kwargs, factory=WorkBoundedConnection),
    )
    result = comparable_decks(path, document)
    assert expected["status"] == result["status"] == "available"
    assert result == expected
    assert 0 < steps <= 100_000


def test_empty_public_cohort_does_not_include_unscoped_memberships(corpus):
    path, document = corpus
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM research_results")
    result = comparable_decks(path, document)
    assert result["status"] == "no_comparable_decks"
    assert result["cohort_lists"] == 0
    assert result["comparable_decks"] == []


def test_public_printing_resolution_does_not_repeat_for_every_membership(
    corpus, monkeypatch
):
    import sabermetrics.assistant_comparable_decks as module

    path, document = corpus
    with sqlite3.connect(path) as conn:
        for i in range(100):
            deck = f"repeated-{i}"
            conn.execute(
                "INSERT INTO research_results VALUES(?,?, 'cmd',date('now','localtime'))",
                (deck, deck),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,100)", (deck,))
            conn.execute(
                "INSERT INTO deck_cards SELECT ?,card_id,quantity,is_commander "
                "FROM deck_cards WHERE deck_id='0'",
                (deck,),
            )
    expected = comparable_decks(path, document)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "ALTER TABLE cards RENAME TO card_catalog;"
            "CREATE VIEW cards AS SELECT id,oracle_identity(oracle_id) AS oracle_id,"
            "name,type_line FROM card_catalog;"
        )
    connect = sqlite3.connect
    reads = 0

    def oracle_identity(value):
        nonlocal reads
        reads += 1
        return value

    def counted_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.create_function("oracle_identity", 1, oracle_identity)
        return conn

    monkeypatch.setattr(module.sqlite3, "connect", counted_connect)
    assert comparable_decks(path, document) == expected
    # A shared printing is resolved once, independent of the 120 public lists.
    assert reads < 500


def test_membership_bound_counts_distinct_pairs_before_commander_exclusion(
    corpus, monkeypatch
):
    import sabermetrics.assistant_comparable_decks as module

    path, document = corpus
    with sqlite3.connect(path) as conn:
        # Commander identity in a library slot still counts toward the SQL
        # membership bound, although excluded from comparable compositions.
        conn.execute("INSERT INTO deck_cards VALUES('0','cmd',1,0)")
        count = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT dc.deck_id,c.oracle_id "
            "FROM deck_cards dc JOIN cards c ON c.id=dc.card_id "
            "WHERE dc.is_commander=0 AND dc.quantity>0 "
            "AND c.oracle_id IS NOT NULL AND c.oracle_id!='' "
            "AND lower(COALESCE(c.type_line,'')) NOT LIKE '%basic%land%')"
        ).fetchone()[0]
    monkeypatch.setattr(module, "MAX_MEMBERSHIPS", count)
    assert comparable_decks(path, document)["status"] == "available"
    monkeypatch.setattr(module, "MAX_MEMBERSHIPS", count - 1)
    result = comparable_decks(path, document)
    assert result["status"] == "composition_too_large"
    assert result["comparable_decks"] == []
