"""Association discovery must distinguish joint use from global popularity."""

import sqlite3
from datetime import date

import pytest

from sabermetrics.assistant_associations import research_card_associations
from sabermetrics.assistant_tools import TOOL_ADAPTER


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "associations.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE cards(id TEXT, oracle_id TEXT, name TEXT, type_line TEXT);
            CREATE TABLE research_commanders(id TEXT, oracle_id TEXT);
            CREATE TABLE research_results(id TEXT, deck_id TEXT, commander_id TEXT, tournament_date TEXT);
            CREATE TABLE tournament_results(id TEXT, player_count INTEGER);
            CREATE TABLE deck_cards(deck_id TEXT, card_id TEXT, quantity INTEGER, is_commander INTEGER);
            INSERT INTO research_commanders VALUES('cmd','ocmd');
            INSERT INTO research_commanders VALUES('partner','opartner');
            INSERT INTO cards VALUES('cmd','ocmd','Commander','Creature');
            INSERT INTO cards VALUES('partner','opartner','Partner','Creature');
            INSERT INTO cards VALUES('a','oa','Anchor','Artifact');
            INSERT INTO cards VALUES('a2','oa','Anchor','Artifact');
            INSERT INTO cards VALUES('b','ob','Associated','Instant');
            INSERT INTO cards VALUES('s','os','Staple','Artifact');
            INSERT INTO cards VALUES('rare','orare','Rare','Instant');
            INSERT INTO cards VALUES('basic','obasic','Island','Basic Land — Island');
        """)
        for i in range(20):
            conn.execute(
                "INSERT INTO research_results VALUES(?,?,?,?)",
                (str(i), str(i), "cmd", date.today().isoformat()),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,100)", (str(i),))
            cards = [("cmd", 1), ("s", 0), ("basic", 0)]
            if i < 10:
                cards += [("a", 0), ("a2", 0)]
            if i < 8:
                cards += [("b", 0)]
            if i < 2:
                cards += [("rare", 0)]
            conn.executemany(
                "INSERT INTO deck_cards VALUES(?,?,4,?)",
                [(str(i), card, flag) for card, flag in cards],
            )
        # Same list observed again must not increase any denominator.
        conn.execute(
            "INSERT INTO research_results VALUES('repeat','0','cmd',?)",
            (date.today().isoformat(),),
        )
        conn.execute("INSERT INTO tournament_results VALUES('repeat',100)")
    document = {
        "entries": [
            {"card_id": "cmd", "oracle_id": "ocmd", "is_commander": True},
            {"card_id": "a", "oracle_id": "oa", "is_commander": False},
        ]
    }
    return path, document


def test_baseline_adjustment_deduplicates_and_suppresses_staples(corpus):
    path, document = corpus
    result = research_card_associations(path, document, "oa")
    assert result["status"] == "available"
    assert result["cohort_lists"] == 20
    assert result["anchor_lists"] == 10
    assert len(result["associations"]) == 1
    pair = result["associations"][0]
    assert pair["name"] == "Associated"
    assert pair["pair_lists"] == pair["baseline_lists"] == 8
    assert pair["conditional_rate"] == 0.8
    assert pair["baseline_rate"] == 0.4
    assert pair["adjusted_conditional_rate"] == pytest.approx(2 / 3)
    assert pair["adjusted_lift"] == pytest.approx(5 / 3)
    assert not pair["already_in_library"]
    assert result["min_event_size"] is not None


def test_thin_cohort_anchor_and_no_match_are_distinct(corpus):
    path, document = corpus
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM deck_cards WHERE card_id='b'")
    assert (
        research_card_associations(path, document, "oa")["status"]
        == "no_supported_associations"
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "DELETE FROM deck_cards WHERE card_id IN ('a','a2') AND CAST(deck_id AS INT)>=4"
        )
    assert (
        research_card_associations(path, document, "oa")["status"]
        == "insufficient_anchor_sample"
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE tournament_results SET player_count=1 WHERE CAST(id AS INT)>=5"
        )
    assert (
        research_card_associations(path, document, "oa")["status"]
        == "insufficient_sample"
    )


def test_partner_cohort_requires_both_commanders(corpus):
    path, document = corpus
    document["entries"].append(
        {"card_id": "partner", "oracle_id": "opartner", "is_commander": True}
    )
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO deck_cards VALUES(?,'partner',1,1)",
            [(str(i),) for i in range(12)],
        )
    result = research_card_associations(path, document, "oa")
    assert result["cohort_lists"] == 12
    assert result["anchor_lists"] == 10


def test_event_dates_and_commander_scope(corpus):
    path, document = corpus
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE research_results SET commander_id='other' WHERE deck_id='19'"
        )
        conn.execute(
            "UPDATE research_results SET tournament_date='2000-01-01' WHERE deck_id='18'"
        )
        conn.execute("UPDATE tournament_results SET player_count=1 WHERE id='17'")
    assert research_card_associations(path, document, "oa")["cohort_lists"] == 17
    assert (
        research_card_associations(path, document, "oa", window_days=0)["cohort_lists"]
        == 18
    )


def test_missing_corpus_is_readonly_and_anchor_cannot_be_invented(tmp_path, corpus):
    path, document = corpus
    missing = tmp_path / "missing.db"
    assert (
        research_card_associations(missing, document, "oa")["status"] == "unavailable"
    )
    assert not missing.exists()
    with pytest.raises(ValueError, match="current library"):
        research_card_associations(path, document, "invented")
    for kwargs in ({"limit": 9}, {"window_days": 5}):
        with pytest.raises(ValueError):
            research_card_associations(path, document, "oa", **kwargs)


def test_unknown_commander_and_oversized_cohort_abstain(corpus, monkeypatch):
    import sabermetrics.assistant_associations as module

    path, document = corpus
    monkeypatch.setattr(module, "MAX_COHORT", 15)
    result = research_card_associations(path, document, "oa")
    assert result["status"] == "cohort_too_large"
    assert result["associations"] == []
    assert result["cohort_count_is_lower_bound"]
    document["entries"][0]["card_id"] = "unknown"
    document["entries"][0]["oracle_id"] = "unknown"
    assert (
        research_card_associations(path, document, "oa")["status"]
        == "unknown_commander"
    )


def test_typed_tool_contract():
    assert (
        TOOL_ADAPTER.validate_python(
            {"name": "research_card_associations", "arguments": {"oracle_id": "oa"}}
        ).arguments.limit
        == 6
    )
    with pytest.raises(ValueError):
        TOOL_ADAPTER.validate_python(
            {
                "name": "research_card_associations",
                "arguments": {"oracle_id": "oa", "owner_id": "other"},
            }
        )


def test_query_deadline_abstains(corpus, monkeypatch):
    import sabermetrics.assistant_associations as module

    path, document = corpus
    ticks = iter([0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks, 10))
    result = research_card_associations(path, document, "oa")
    assert result["status"] == "query_budget_exceeded"
    assert result["associations"] == []


def test_missing_event_floor_is_explicit(corpus):
    path, document = corpus
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE tournament_results DROP COLUMN player_count")
    result = research_card_associations(path, document, "oa")
    assert result["status"] == "available"
    assert result["min_event_size"] is None
    assert any("none was applied" in note for note in result["limitations"])
    assert len(result["aggregate_sha256"]) == 64
    assert result["source_freshness"] == "unknown"


def test_ubiquitous_anchor_never_creates_rounding_associations(corpus):
    path, document = corpus
    document["entries"].append({"oracle_id": "os", "is_commander": False})
    with sqlite3.connect(path) as conn:
        for i in range(20, 158):
            conn.execute(
                "INSERT INTO research_results VALUES(?,?,?,?)",
                (str(i), str(i), "cmd", date.today().isoformat()),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,100)", (str(i),))
            conn.execute("INSERT INTO deck_cards VALUES(?,'s',1,0)", (str(i),))
    result = research_card_associations(path, document, "os")
    assert result["cohort_lists"] == result["anchor_lists"] == 158
    assert result["status"] == "uninformative_anchor"
    assert "not insufficient pair support" in result["interpretation"].replace(
        "\n", " "
    )
    assert result["associations"] == []


def _two_periods(path, *, reversed_recent=False):
    from datetime import timedelta

    earlier = (date.today() - timedelta(days=60)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE research_results SET tournament_date=?", (earlier,))
        for i in range(20):
            deck = f"recent-{i}"
            conn.execute(
                "INSERT INTO research_results VALUES(?,?,?,?)",
                (deck, deck, "cmd", date.today().isoformat()),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,100)", (deck,))
            conn.execute("INSERT INTO deck_cards VALUES(?,'cmd',1,1)", (deck,))
            cards = ["s"] + (["a"] if i < 10 else [])
            if (i < 5 or 10 <= i < 17) if reversed_recent else i < 8:
                cards += ["b"]
            conn.executemany(
                "INSERT INTO deck_cards VALUES(?,?,1,0)",
                [(deck, card) for card in cards],
            )


def test_temporal_stability_counts_disjoint_lists_and_not_repeat_observations(corpus):
    path, document = corpus
    _two_periods(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO research_results VALUES('cross-band','0','cmd',?)",
            (date.today().isoformat(),),
        )
        conn.execute("INSERT INTO tournament_results VALUES('cross-band',100)")
    result = research_card_associations(path, document, "oa")
    temporal = result["associations"][0]["temporal_stability"]
    assert temporal["state"] == "stable"
    assert result["cohort_lists"] == 40
    assert [p["cohort_lists"] for p in temporal["periods"]] == [20, 20]
    assert [p["anchor_lists"] for p in temporal["periods"]] == [10, 10]
    assert [p["pair_lists"] for p in temporal["periods"]] == [8, 8]
    assert all(p["adjusted_lift"] == pytest.approx(5 / 3) for p in temporal["periods"])


def test_supported_period_reversal_is_unstable_without_hiding_global_lead(corpus):
    path, document = corpus
    _two_periods(path, reversed_recent=True)
    result = research_card_associations(path, document, "oa")
    item = result["associations"][0]
    assert item["adjusted_lift"] > 1
    assert item["temporal_stability"]["state"] == "unstable"
    assert item["temporal_stability"]["periods"][1]["adjusted_lift"] < 1


def test_empty_recent_band_retains_lead_with_insufficient_stability(corpus):
    from datetime import timedelta

    path, document = corpus
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE research_results SET tournament_date=?",
            ((date.today() - timedelta(days=60)).isoformat(),),
        )
    item = research_card_associations(path, document, "oa")["associations"][0]
    assert item["temporal_stability"]["state"] == "insufficient"
    recent = item["temporal_stability"]["periods"][1]
    assert recent["cohort_lists"] == recent["pair_lists"] == 0
    assert recent["adjusted_lift"] is None


def test_partner_repeat_rows_do_not_double_count_periods(corpus):
    path, document = corpus
    _two_periods(path)
    document["entries"].append(
        {"card_id": "partner", "oracle_id": "opartner", "is_commander": True}
    )
    with sqlite3.connect(path) as conn:
        decks = [
            r[0] for r in conn.execute("SELECT DISTINCT deck_id FROM research_results")
        ]
        conn.executemany(
            "INSERT INTO deck_cards VALUES(?,'partner',1,1)", [(d,) for d in decks]
        )
        for i, deck in enumerate(decks):
            observed = conn.execute(
                "SELECT MIN(tournament_date) FROM research_results WHERE deck_id=?",
                (deck,),
            ).fetchone()[0]
            rid = f"partner-result-{i}"
            conn.execute(
                "INSERT INTO research_results VALUES(?,?,?,?)",
                (rid, deck, "partner", observed),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,100)", (rid,))
    result = research_card_associations(path, document, "oa")
    periods = result["associations"][0]["temporal_stability"]["periods"]
    assert result["cohort_lists"] == sum(p["cohort_lists"] for p in periods) == 40
    assert [p["pair_lists"] for p in periods] == [8, 8]


def test_temporal_observation_date_obeys_event_floor(corpus):
    from datetime import timedelta

    path, document = corpus
    with sqlite3.connect(path) as conn:
        for i in range(20):
            rid = f"small-event-{i}"
            conn.execute(
                "INSERT INTO research_results VALUES(?,?,?,?)",
                (rid, str(i), "cmd", (date.today() - timedelta(days=60)).isoformat()),
            )
            conn.execute("INSERT INTO tournament_results VALUES(?,1)", (rid,))
    result = research_card_associations(path, document, "oa")
    assert [p["cohort_lists"] for p in result["temporal_diagnostics"]["periods"]] == [
        0,
        20,
    ]


def test_invalid_observation_dates_do_not_fabricate_stability(corpus):
    path, document = corpus
    # Preserve the lexical Research window while making one calendar date invalid.
    result = research_card_associations(path, document, "oa")
    month = result["window_end_exclusive"][:7]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE research_results SET tournament_date=? WHERE deck_id='0'",
            (month + "-00",),
        )
    result = research_card_associations(path, document, "oa")
    assert result["status"] == "available"
    assert result["temporal_diagnostics"]["reason"] == "invalid_observation_date"
    assert result["associations"][0]["temporal_stability"]["state"] == "insufficient"
