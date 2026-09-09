"""Research metrics state their real cohorts and missing-data coverage."""

import inspect
from datetime import date, timedelta

from sabermetrics import db
from sabermetrics.research import ResearchRepo
from sabermetrics.substrate.artifacts import RetrievalArtifactError
from sabermetrics.substrate.models import (
    CardSearchResult,
    IndexProvenance,
    RetrievalAvailability,
    RetrievalHit,
)
from scripts.setup_db import setup_database


class _StaticCardSearcher:
    def search(self, query):
        assert query.filters.required_types == ("artifact",)
        assert query.filters.color_identity == ()
        assert query.filters.mana_value_min == query.filters.mana_value_max == 1
        return CardSearchResult(
            query=query,
            hits=(
                RetrievalHit(
                    oracle_id="or",
                    name="Sol Ring",
                    mana_value=1,
                    type_line="Artifact",
                    oracle_text="",
                ),
            ),
            eligible_cards=1,
            provenance=IndexProvenance(
                bundle_id="bundle",
                corpus_sha256="a" * 64,
                tag_library_sha256="b" * 64,
                retrieval_config_sha256="c" * 64,
                document_version="card-document.v1",
            ),
            availability=RetrievalAvailability(
                lexical=False,
                dense=False,
                reranked=False,
            ),
        )


class _UnavailableCardSearcher:
    def search(self, query):
        del query
        raise RetrievalArtifactError("CURRENT is missing")


def test_commander_metrics_and_inclusion_denominators(tmp_path):
    path = tmp_path / "research.db"
    setup_database(path)
    today = date.today()
    current = today.isoformat()
    prior = (today - timedelta(days=100)).isoformat()
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("kinnan", "ok", "Kinnan", 2, "Legendary Creature", '["G","U"]', 1, 1),
                ("tymna", "ot", "Tymna", 3, "Legendary Creature", '["W","B"]', 1, 1),
                ("ring", "or", "Sol Ring", 1, "Artifact", "[]", 0, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO decks(id,source,source_id,commander_id) VALUES(?,?,?,?)",
            [
                ("d1", "test", "1", "kinnan"),
                ("d2", "test", "2", "kinnan"),
                ("d3", "test", "3", "tymna"),
                ("old", "test", "4", "kinnan"),
            ],
        )
        conn.execute(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES('d1','ring',1,0)"
        )
        conn.executemany(
            """INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,standing,tournament_date)
            VALUES(?,?,?,?,?,?)""",
            [
                ("r1", "event-1", "d1", "kinnan", 8, current),
                ("r2", "event-2", "d2", "kinnan", None, current),
                ("r3", "event-2", "d3", "tymna", 20, current),
                ("r4", "event-old", "old", "kinnan", 1, prior),
            ],
        )
        conn.commit()

    repo = ResearchRepo(path, card_searcher=_StaticCardSearcher())
    data = repo.commanders(query="Kinnan", window_days=90)
    assert data["recorded_entries"] == 3
    row = data["results"][0]
    assert row["entries"] == 2
    assert row["meta_share"] == 2 / 3
    assert row["finish_coverage"] == 1
    assert row["top16_rate"] == 1.0

    detail = repo.commander_detail("kinnan", window_days=90)
    assert detail is not None
    assert detail["inclusion_denominator"] == 1
    assert detail["inclusions"][0]["name"] == "Sol Ring"
    assert detail["inclusions"][0]["decks_including"] == 1
    assert detail["metrics"]["average_nonland_mv"] == 1.0
    assert detail["metrics"]["mv_list_count"] == 1

    filtered = repo.commanders(
        colors=["G", "U"], color_mode="exact", mana_max=2, meta_min=0.6
    )
    assert [item["name"] for item in filtered["results"]] == ["Kinnan"]
    assert repo.commanders(meta_min=0.7)["results"] == []

    cards = repo.cards(
        "",
        type_line="Artifact",
        mana_operator="eq",
        mana_value=1,
        color_mode="exact",
        colors=["C"],
    )
    assert [item["name"] for item in cards["results"]] == ["Sol Ring"]


def test_card_retrieval_absence_is_visible(tmp_path):
    repo = ResearchRepo(
        tmp_path / "unused.db", card_searcher=_UnavailableCardSearcher()
    )
    result = repo.cards("counterspell")
    assert result["results"] == []
    assert result["has_next"] is False
    assert "Card retrieval unavailable" in result["unavailable"]


def test_card_search_has_no_legacy_like_ranking_path():
    assert " LIKE " not in inspect.getsource(ResearchRepo.cards).upper()
