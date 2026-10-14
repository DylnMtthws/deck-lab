"""Provenance, lexical fallback and model/version compatibility regressions."""

import json
import sqlite3
from pathlib import Path

import pytest

from sabermetrics.assistant_strategy_evidence import retrieve_strategy_evidence
from sabermetrics.reference_layer import strategy_corpus as module


def ingest(path, text="Sacrifice creatures to draw cards.", **kwargs):
    return module.ingest_text(
        path,
        text,
        document="Licensed primer",
        source_url="https://example.test/primer",
        version=kwargs.pop("version", "v1"),
        permission="user supplied with permission",
        **kwargs,
    )


def test_versioned_provenance_and_exact_excerpts(tmp_path):
    db = tmp_path / "corpus.db"
    assert ingest(db) == 1
    assert ingest(db) == 1
    ingest(db, version="v2")
    result = retrieve_strategy_evidence(db, "sacrifice draw")
    assert result["corpus_chunks"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_chunks").fetchone()[0] == 2
    assert result["excerpts"][0]["citation"]["provenance"]["version"] == "v2"
    assert result["retrieval_mode"] == "lexical"
    for excerpt in result["excerpts"]:
        assert excerpt["excerpt"] == "Sacrifice creatures to draw cards."
        assert excerpt["citation"]["source_url"] == "https://example.test/primer"
        assert excerpt["citation"]["provenance"][
            "content_sha256"
        ] == module.content_hash(excerpt["excerpt"])
        assert excerpt["score_components"]["bm25"] > 0
    assert (
        retrieve_strategy_evidence(db, "galactic hyperspace")["status"] == "no_matches"
    )


class Vector(list):
    def tolist(self):
        return list(self)


class Provider:
    _model_name = "real-provider-fixture@immutable-revision"

    def embed(self, text):
        return Vector([1.0, 0.0])


def test_hybrid_and_stale_model_or_content_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "local_embedding_service", lambda: Provider())
    db = tmp_path / "corpus.db"
    ingest(db, embed=True)
    result = retrieve_strategy_evidence(db, "sacrifice")
    assert result["retrieval_mode"] == "hybrid_bm25_semantic"
    assert result["excerpts"][0]["score_components"]["semantic_cosine"] == 1
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE reference_chunk_metadata SET embedding_model='old-model'")
    result = retrieve_strategy_evidence(db, "sacrifice")
    assert result["retrieval_mode"] == "lexical"
    assert any("Rejected 1" in line for line in result["limitations"])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE reference_chunk_metadata SET embedding_model=?",
            (Provider._model_name,),
        )
        conn.execute(
            "UPDATE reference_chunks SET content='Sacrifice updated creatures.'"
        )
    assert retrieve_strategy_evidence(db, "sacrifice")["retrieval_mode"] == "lexical"


def test_metadata_section_identity_does_not_replace_other_rules(tmp_path):
    db = tmp_path / "corpus.db"
    ingest(db, section="903.5a")
    ingest(db, section="903.5b")
    assert retrieve_strategy_evidence(db, "sacrifice")["corpus_chunks"] == 2


def test_permissions_required_and_bad_urls_rejected(tmp_path):
    with pytest.raises(ValueError):
        module.ingest_text(
            tmp_path / "db",
            "x",
            document="doc",
            source_url="javascript:alert(1)",
            version="v1",
            permission="provided",
        )
    with pytest.raises(ValueError):
        module.ingest_text(
            tmp_path / "db",
            "x",
            document="doc",
            source_url=None,
            version="v1",
            permission="",
        )


def test_seed_is_actual_official_rules_with_source_versions():
    import sabermetrics

    seed = json.loads(
        (
            Path(sabermetrics.__file__).parent / "config_data/strategy_source_seed.json"
        ).read_text()
    )
    assert len(seed["sources"]) >= 10
    assert all(
        (s["source_url"] is None or s["source_url"].startswith("https://"))
        and s["version"]
        for s in seed["sources"]
    )
    commander_rule = next(s for s in seed["sources"] if s["section"] == "903.5a")
    assert "exactly 100 cards" in commander_rule["text"]


def test_semantic_relevance_can_outrank_wrong_topic_keyword_overlap(
    tmp_path, monkeypatch
):
    class SemanticProvider(Provider):
        def embed(self, text):
            # Deterministic provider fixture tests fusion, not model intelligence.
            return Vector([0.0, 1.0] if "portrait" in text else [1.0, 0.0])

    monkeypatch.setattr(module, "local_embedding_service", lambda: SemanticProvider())
    db = tmp_path / "corpus.db"
    ingest(
        db,
        "A portrait artist uses draw cards as labels on a canvas.",
        section="wrong topic",
        embed=True,
    )
    ingest(
        db,
        "Refill your hand with a recurring source of resources.",
        section="strategy",
        embed=True,
    )
    result = retrieve_strategy_evidence(db, "draw cards", limit=1)
    assert result["retrieval_mode"] == "hybrid_bm25_semantic"
    assert result["excerpts"][0]["citation"]["section"] == "strategy"
    assert result["excerpts"][0]["score_components"]["query_coverage"] == 0


def test_archived_old_rules_do_not_compete_even_with_changed_url(tmp_path):
    db = tmp_path / "corpus.db"
    module.ingest_text(
        db,
        "Obsolete draw rules.",
        document="Rules",
        source_url="https://example.test/old.txt",
        version="opaque-old",
        permission="provided",
        source_key="official-rules:121",
        section="121",
    )
    module.ingest_text(
        db,
        "Current draw rules.",
        document="Rules",
        source_url="https://example.test/new.txt",
        version="opaque-new",
        permission="provided",
        source_key="official-rules:121",
        section="121",
    )
    module.ingest_text(
        db,
        "Historical draw rules.",
        document="Rules",
        source_url="https://example.test/archive.txt",
        version="archive",
        permission="provided",
        source_key="official-rules:121",
        section="121",
        activate=False,
    )
    result = retrieve_strategy_evidence(db, "draw")
    assert result["corpus_chunks"] == 1
    assert result["excerpts"][0]["excerpt"] == "Current draw rules."
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_chunks").fetchone()[0] == 3


def test_seed_sections_survive_idempotent_ingestion(tmp_path):
    import sabermetrics

    bundle = json.loads(
        (
            Path(sabermetrics.__file__).parent / "config_data/strategy_source_seed.json"
        ).read_text()
    )
    db = tmp_path / "seed.db"
    for _ in range(2):
        for source in bundle["sources"]:
            module.ingest_text(db, **source)
    with sqlite3.connect(db) as conn:
        active = conn.execute(
            "SELECT r.document,r.section,r.content FROM reference_chunks r JOIN reference_chunk_metadata m ON r.id=m.chunk_id WHERE m.is_current=1"
        ).fetchall()
    assert len(active) == len(bundle["sources"])
    assert set(active) == {
        (s["document"], s["section"], s["text"]) for s in bundle["sources"]
    }


def test_archiving_current_version_is_rejected_without_changing_active_evidence(
    tmp_path,
):
    db = tmp_path / "corpus.db"
    ingest(db)
    with pytest.raises(ValueError, match="Cannot archive the currently active"):
        ingest(db, activate=False)
    result = retrieve_strategy_evidence(db, "sacrifice")
    assert result["corpus_source"] == "reference_chunks"
    assert result["corpus_chunks"] == 1
    assert result["excerpts"][0]["citation"]["provenance"]["version"] == "v1"


def test_renamed_title_matching_url_requires_explicit_lineage(tmp_path):
    db = tmp_path / "corpus.db"
    common = dict(
        source_url="https://example.test/primer",
        section="Engine",
        permission="provided",
    )
    module.ingest_text(
        db, "Draw original cards.", document="Original title", version="v1", **common
    )
    with pytest.raises(ValueError, match="explicit source_key"):
        module.ingest_text(
            db, "Draw revised cards.", document="Renamed title", version="v2", **common
        )
    assert retrieve_strategy_evidence(db, "draw")["corpus_chunks"] == 1
    # Explicitly bind both versions to a deliberate stable source identity.
    module.ingest_text(
        db,
        "Draw original cards.",
        document="Original title",
        version="v1",
        source_key="primer:engine",
        **common,
    )
    module.ingest_text(
        db,
        "Draw revised cards.",
        document="Renamed title",
        version="v2",
        source_key="primer:engine",
        **common,
    )
    result = retrieve_strategy_evidence(db, "draw")
    assert result["corpus_chunks"] == 1
    assert result["excerpts"][0]["citation"]["document"] == "Renamed title"
