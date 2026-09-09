"""The optional legacy stack fails clearly when it is not installed."""

import sys

import pytest


def test_embedding_service_names_the_legacy_extra(monkeypatch):
    from sabermetrics.analytics.embeddings import EmbeddingService

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match=r"install sabermetrics\[legacy\]"):
        EmbeddingService()._load_model()


def test_reference_indexer_names_the_research_extra(monkeypatch, tmp_path):
    from sabermetrics.reference_layer.indexer import EmbeddingIndexer
    from sabermetrics.substrate.settings import load_research_settings

    settings = load_research_settings().embedding.model_copy(
        update={"local_dir": tmp_path / "model"}
    )
    settings.local_dir.mkdir()
    (settings.local_dir / "REVISION").write_text(settings.revision, encoding="ascii")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match=r"install sabermetrics\[research\]"):
        EmbeddingIndexer(tmp_path / "index.db", settings=settings)._get_model()
