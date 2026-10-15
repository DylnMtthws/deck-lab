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
    assert result["status"] == "no_supported_associations"
    assert result["associations"] == []
