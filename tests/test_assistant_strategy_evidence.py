"""Evidence retrieval must stay bounded and honest about its actual corpus."""

import json
import sqlite3
from pathlib import Path

import pytest

from sabermetrics.assistant_strategy_evidence import retrieve_strategy_evidence


def corpus(path: Path, rows: list[tuple]) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE reference_chunks (id TEXT PRIMARY KEY, document TEXT, "
            "section TEXT, tier INTEGER, content TEXT, embedding BLOB)"
        )
        conn.executemany("INSERT INTO reference_chunks VALUES (?, ?, ?, ?, ?, ?)", rows)
    return path


def test_unembedded_corpus_has_real_citations_and_deterministic_retrieval(tmp_path):
    path = corpus(
        tmp_path / "corpus.db",
        [
            (
                "vivi",
                "https://example.test/primer",
                "Engine",
                2,
                "Vivi uses spells and draw.",
                None,
            ),
            ("other", "Local guide", None, 1, "Spells and draw are useful.", None),
            ("irrelevant", "Land guide", None, 1, "Lands produce mana.", None),
        ],
    )
    result = retrieve_strategy_evidence(path, "spells draw", commander="Vivi", limit=1)
    assert result == retrieve_strategy_evidence(
        path, "spells draw", commander="Vivi", limit=1
    )
    assert result["status"] == "available"
    assert result["retrieval_mode"] == "lexical"
    assert result["matched_chunks"] == 2
    assert result["excerpts"][0]["citation"] == {
        "chunk_id": "vivi",
        "document": "https://example.test/primer",
        "section": "Engine",
        "source_url": "https://example.test/primer",
        "metadata_truncated": False,
    }
    assert result["excerpts"][0]["excerpt"] == "Vivi uses spells and draw."


def test_bounds_and_contiguous_excerpts(tmp_path):
    content = "x " * 800 + "sacrifice" + " y" * 800
    path = corpus(
        tmp_path / "corpus.db",
        [
            (str(i), "source" * 1000, "section" * 100, 1, content, None)
            for i in range(20)
        ],
    )
    result = retrieve_strategy_evidence(path, "sacrifice")
    assert len(result["excerpts"]) == 4
    assert len(json.dumps(result)) < 6500
    for item in result["excerpts"]:
        assert len(item["excerpt"]) <= 600
        assert item["excerpt"] in content
        assert "sacrifice" in item["excerpt"]
        assert item["excerpt_truncated"]
        assert item["citation"]["metadata_truncated"]
        assert item["citation"]["source_url"] is None


def test_empty_missing_and_no_matching_corpus_are_distinguished(tmp_path):
    missing = tmp_path / "missing.db"
    assert retrieve_strategy_evidence(missing, "Vivi")["status"] == "unavailable"
    assert not missing.exists()
    path = corpus(tmp_path / "corpus.db", [])
    empty_result = retrieve_strategy_evidence(path, "Vivi")
    assert empty_result["reference_corpus_status"] == "empty"
    assert empty_result["corpus_source"] == "bundled_authored_guidance"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO reference_chunks VALUES ('a','guide',NULL,1,'land',NULL)"
        )
    result = retrieve_strategy_evidence(path, "Vivi")
    assert result["status"] == "no_matches"
    assert result["excerpts"] == []


@pytest.mark.parametrize(
    "query,limit", [(" ", 4), ("the and", 4), ("x" * 321, 4), ("draw", 5)]
)
def test_invalid_requests_are_rejected(tmp_path, query, limit):
    with pytest.raises(ValueError):
        retrieve_strategy_evidence(tmp_path / "missing", query, limit=limit)


def test_query_is_not_sql_and_embedded_rows_still_work_without_model(tmp_path):
    path = corpus(
        tmp_path / "corpus.db",
        [
            ("a", "guide", None, 1, "draw cards", b"not-a-valid-embedding"),
        ],
    )
    result = retrieve_strategy_evidence(path, "draw'; DROP TABLE reference_chunks; --")
    assert result["status"] == "available"
    assert result["retrieval_mode"] == "lexical"
    assert result["excerpts"][0]["citation"]["source_url"] is None
    assert retrieve_strategy_evidence(path, "draw")["corpus_chunks"] == 1


def test_empty_corpus_uses_labeled_packaged_authored_guidance(tmp_path):
    path = corpus(tmp_path / "corpus.db", [])
    result = retrieve_strategy_evidence(path, "spellslinger")
    assert result["status"] == "available"
    assert result["reference_corpus_status"] == "empty"
    assert result["retrieval_mode"] == "lexical"
    evidence = result["excerpts"][0]
    assert evidence["source_kind"] == "authored_guidance"
    assert evidence["citation"]["document"] == "config/archetype_signatures.yaml"
    assert evidence["citation"]["section"] == "archetypes.spellslinger"
    assert evidence["citation"]["source_url"] is None
    assert any("not an external primer" in line for line in result["limitations"])


def test_populated_corpus_never_silently_substitutes_local_guidance(tmp_path):
    path = corpus(tmp_path / "corpus.db", [("a", "guide", None, 1, "lands", None)])
    result = retrieve_strategy_evidence(path, "spellslinger")
    assert result["status"] == "no_matches"
    assert result["reference_corpus_status"] == "populated"
    assert result["corpus_source"] == "reference_chunks"
    assert result["excerpts"] == []


def test_bundled_signatures_match_actual_project_config():
    import yaml

    import sabermetrics.assistant_strategy_evidence as module

    project = Path(__file__).resolve().parents[1]
    source = yaml.safe_load((project / "config/archetype_signatures.yaml").read_text())
    bundle = json.loads(
        (
            Path(module.__file__).parent
            / "config_data/assistant_strategy_guidance.json"
        ).read_text()
    )
    assert len(bundle["chunks"]) == len(source["archetypes"])
    for chunk in bundle["chunks"]:
        archetype = chunk["section"].removeprefix("archetypes.")
        for card, weight in source["archetypes"][archetype]["signatures"].items():
            assert f"{card} ({weight})" in chunk["content"]


def test_authored_reanimator_guidance_explains_plan_and_preserves_abstention(tmp_path):
    path = corpus(tmp_path / "corpus.db", [])
    result = retrieve_strategy_evidence(
        path, "bring big creatures back from the graveyard"
    )
    evidence = result["excerpts"][0]
    assert evidence["citation"]["chunk_id"] == "authored-archetype-reanimator"
    assert evidence["source_kind"] == "authored_guidance"
    assert (
        evidence["citation"]["document"]
        == "src/sabermetrics/config_data/assistant_strategy_guidance.json"
    )
    assert "returns them to the battlefield" in evidence["excerpt"]
    assert evidence["citation"]["source_url"] is None
    assert (
        retrieve_strategy_evidence(path, "quantum chromodynamics lattice")["status"]
        == "no_matches"
    )
