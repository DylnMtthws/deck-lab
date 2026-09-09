"""R2 reference embedding generation migration tests."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from sabermetrics.reference_layer.chunker import Chunk
from sabermetrics.reference_layer.indexer import (
    REFERENCE_DOCUMENT_TEMPLATE_VERSION,
    EmbeddingIndexer,
    ReferenceEmbeddingBuildError,
    reference_content_sha256,
)
from sabermetrics.reference_layer.retriever import (
    ReferenceGenerationAbsentError,
    ReferenceGenerationCorruptError,
    ReferenceGenerationIncompleteError,
    ReferenceGenerationMismatchError,
    ReferenceGenerationUnlabelledError,
    ReferenceQuery,
    ReferenceRetriever,
)
from sabermetrics.substrate.settings import EmbeddingModelSettings
from scripts.setup_db import setup_database


class FakeEncoder:
    """Small deterministic encoder implementing the dense encoder protocol."""

    def __init__(self, dimensions: int = 4) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        """Return the fixed test dimension."""
        return self._dimensions

    def encode(self, texts: Sequence[str], *, batch_size: int) -> NDArray[np.float32]:
        """Encode stable lexical features in input order."""
        assert batch_size > 0
        rows: list[list[float]] = []
        for text in texts:
            lowered = text.casefold()
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            rows.append(
                [
                    2.0 if "alpha" in lowered else 0.25,
                    2.0 if "beta" in lowered else 0.25,
                    1.0 if "rule" in lowered else 0.25,
                    1.0 + digest[0] / 255.0,
                ]
            )
        return np.asarray(rows, dtype=np.float32)


class FailingEncoder(FakeEncoder):
    """Encoder that fails after the transaction has begun."""

    def encode(self, texts: Sequence[str], *, batch_size: int) -> NDArray[np.float32]:
        """Raise a deterministic build failure."""
        raise RuntimeError("injected encoder failure")


def _settings(tmp_path: Path) -> EmbeddingModelSettings:
    return EmbeddingModelSettings(
        model_id="BAAI/bge-small-en-v1.5",
        revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        local_dir=tmp_path / "models" / "bge-small-en-v1.5",
        dimensions=4,
        normalize=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        batch_size=2,
        device="cpu",
    )


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            id="chunk-alpha",
            document="comprehensive_rules",
            section="CR 100",
            tier=1,
            content="Alpha rule and interaction.",
        ),
        Chunk(
            id="chunk-beta",
            document="strategy",
            section=None,
            tier=2,
            content="Beta engine guidance.",
        ),
    ]


def _active_generation(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT generation_id FROM active_reference_embedding_generation "
            "WHERE singleton = 1"
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_setup_adds_generation_schema_idempotently(tmp_path: Path) -> None:
    """Setup retains the legacy column and safely creates all R2 tables twice."""
    db_path = tmp_path / "reference.db"
    setup_database(db_path)
    setup_database(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        legacy_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(reference_chunks)")
        }

    assert "embedding" in legacy_columns
    assert {
        "reference_embedding_generations",
        "reference_chunk_embeddings",
        "active_reference_embedding_generation",
    } <= tables


def test_build_activates_complete_normalized_bge_generation(tmp_path: Path) -> None:
    """A build records full provenance and retrieval ignores legacy blobs."""
    db_path = tmp_path / "reference.db"
    setup_database(db_path)
    settings = _settings(tmp_path)
    chunks = _chunks()
    legacy_blob = np.asarray([99.0], dtype=np.float32).tobytes()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reference_chunks
                (id, document, section, tier, content, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chunks[0].id,
                chunks[0].document,
                chunks[0].section,
                chunks[0].tier,
                chunks[0].content,
                legacy_blob,
            ),
        )

    indexer = EmbeddingIndexer(db_path, encoder=FakeEncoder(), settings=settings)
    assert indexer.index_chunks(chunks) == 2
    generation_id = _active_generation(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        metadata = conn.execute(
            "SELECT * FROM reference_embedding_generations " "WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        vectors = conn.execute(
            "SELECT embedding FROM reference_chunk_embeddings "
            "WHERE generation_id = ? ORDER BY chunk_id",
            (generation_id,),
        ).fetchall()
        stored_legacy = conn.execute(
            "SELECT embedding FROM reference_chunks WHERE id = ?",
            (chunks[0].id,),
        ).fetchone()[0]

    assert metadata is not None
    assert metadata["model_id"] == settings.model_id
    assert metadata["model_revision"] == settings.revision
    assert metadata["document_template_version"] == REFERENCE_DOCUMENT_TEMPLATE_VERSION
    assert metadata["dimensions"] == 4
    assert metadata["dtype"] == "float32"
    assert metadata["normalized"] == 1
    assert metadata["content_sha256"] == reference_content_sha256(chunks)
    assert metadata["row_count"] == 2
    assert metadata["complete"] == 1
    assert metadata["completed_at"]
    assert len(generation_id) == 64
    assert len(metadata["embedding_sha256"]) == 64
    assert all(
        np.isclose(
            np.linalg.norm(np.frombuffer(row["embedding"], dtype=np.float32)),
            1.0,
        )
        for row in vectors
    )
    assert stored_legacy == legacy_blob

    retriever = ReferenceRetriever(db_path, indexer=indexer, settings=settings)
    results = retriever.retrieve(ReferenceQuery(query_text="alpha rule", top_k=2))
    assert [result.id for result in results] == ["chunk-alpha", "chunk-beta"]


def test_hash_and_generation_are_deterministic_without_timestamps(
    tmp_path: Path,
) -> None:
    """Rebuilding identical content reuses one deterministic generation."""
    db_path = tmp_path / "reference.db"
    setup_database(db_path)
    settings = _settings(tmp_path)
    indexer = EmbeddingIndexer(db_path, encoder=FakeEncoder(), settings=settings)

    assert reference_content_sha256(_chunks()) == reference_content_sha256(
        list(reversed(_chunks()))
    )
    indexer.index_chunks(_chunks())
    first_generation = _active_generation(db_path)
    indexer.index_chunks(list(reversed(_chunks())))
    second_generation = _active_generation(db_path)

    with sqlite3.connect(db_path) as conn:
        generation_count = conn.execute(
            "SELECT COUNT(*) FROM reference_embedding_generations"
        ).fetchone()[0]
    assert first_generation == second_generation
    assert generation_count == 1


def test_new_generation_switches_pointer_and_retains_history(tmp_path: Path) -> None:
    """Changed content creates a new complete generation before activation."""
    db_path = tmp_path / "reference.db"
    setup_database(db_path)
    settings = _settings(tmp_path)
    indexer = EmbeddingIndexer(db_path, encoder=FakeEncoder(), settings=settings)
    indexer.index_chunks(_chunks())
    first_generation = _active_generation(db_path)

    changed = _chunks()
    changed[0].content = "Alpha rule changed deterministically."
    indexer.index_chunks(changed)
    second_generation = _active_generation(db_path)

    with sqlite3.connect(db_path) as conn:
        generations = conn.execute(
            "SELECT generation_id, complete FROM reference_embedding_generations"
        ).fetchall()
    assert second_generation != first_generation
    assert {row[0] for row in generations} == {
        first_generation,
        second_generation,
    }
    assert all(row[1] == 1 for row in generations)


def test_failed_build_preserves_prior_active_generation(tmp_path: Path) -> None:
    """Encoder failure rolls back chunk writes and leaves activation unchanged."""
    db_path = tmp_path / "reference.db"
    setup_database(db_path)
    settings = _settings(tmp_path)
    EmbeddingIndexer(db_path, encoder=FakeEncoder(), settings=settings).index_chunks(
        _chunks()
    )
    active_before = _active_generation(db_path)

    failing = EmbeddingIndexer(db_path, encoder=FailingEncoder(), settings=settings)
    with pytest.raises(ReferenceEmbeddingBuildError, match="encoder failed"):
        failing.index_chunks(
            [
                Chunk(
                    id="chunk-new",
                    document="new",
                    section=None,
                    tier=2,
                    content="A new reference chunk.",
                )
            ]
        )

    assert _active_generation(db_path) == active_before
    with sqlite3.connect(db_path) as conn:
        new_chunk_count = conn.execute(
            "SELECT COUNT(*) FROM reference_chunks WHERE id = 'chunk-new'"
        ).fetchone()[0]
        generation_count = conn.execute(
            "SELECT COUNT(*) FROM reference_embedding_generations"
        ).fetchone()[0]
    assert new_chunk_count == 0
    assert generation_count == 1


def test_retriever_refuses_absent_and_unlabelled_legacy_rows(
    tmp_path: Path,
) -> None:
    """No pointer is distinct from inert legacy MiniLM-era blob data."""
    db_path = tmp_path / "reference.db"
    setup_database(db_path)
    settings = _settings(tmp_path)
    retriever = ReferenceRetriever(
        db_path, indexer=FakeQueryIndexer(), settings=settings
    )
    query = ReferenceQuery(query_text="rules")

    with pytest.raises(ReferenceGenerationAbsentError, match="no active"):
        retriever.retrieve(query)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reference_chunks
                (id, document, tier, content, embedding)
            VALUES ('legacy', 'rules', 1, 'old text', ?)
            """,
            (np.ones(4, dtype=np.float32).tobytes(),),
        )
    with pytest.raises(ReferenceGenerationUnlabelledError, match="unlabelled"):
        retriever.retrieve(query)


class FakeQueryIndexer:
    """Query-only encoder for unavailable-generation tests."""

    def compute_embedding(self, text: str) -> NDArray[np.float32]:
        """Return a normalized test query vector."""
        return np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


@pytest.mark.parametrize(
    ("mutation", "error_type", "message"),
    [
        (
            "minilm",
            ReferenceGenerationMismatchError,
            "MiniLM",
        ),
        (
            "partial",
            ReferenceGenerationIncompleteError,
            "partial",
        ),
        (
            "stale",
            ReferenceGenerationMismatchError,
            "stale",
        ),
        (
            "mixed",
            ReferenceGenerationCorruptError,
            "dimensions",
        ),
    ],
)
def test_retriever_refuses_invalid_active_generations(
    tmp_path: Path,
    mutation: str,
    error_type: type[Exception],
    message: str,
) -> None:
    """MiniLM, partial, stale, and mixed generations fail explicitly."""
    db_path = tmp_path / f"{mutation}.db"
    setup_database(db_path)
    settings = _settings(tmp_path)
    indexer = EmbeddingIndexer(db_path, encoder=FakeEncoder(), settings=settings)
    indexer.index_chunks(_chunks())
    generation_id = _active_generation(db_path)

    with sqlite3.connect(db_path) as conn:
        if mutation == "minilm":
            conn.execute(
                "UPDATE reference_embedding_generations "
                "SET model_id = 'sentence-transformers/all-MiniLM-L6-v2' "
                "WHERE generation_id = ?",
                (generation_id,),
            )
        elif mutation == "partial":
            conn.execute(
                "DELETE FROM reference_chunk_embeddings "
                "WHERE generation_id = ? AND chunk_id = 'chunk-beta'",
                (generation_id,),
            )
        elif mutation == "stale":
            conn.execute(
                "UPDATE reference_chunks SET content = 'changed after build' "
                "WHERE id = 'chunk-alpha'"
            )
        else:
            conn.execute(
                "UPDATE reference_chunk_embeddings SET embedding = ? "
                "WHERE generation_id = ? AND chunk_id = 'chunk-alpha'",
                (np.ones(2, dtype=np.float32).tobytes(), generation_id),
            )

    retriever = ReferenceRetriever(db_path, indexer=indexer, settings=settings)
    with pytest.raises(error_type, match=message):
        retriever.retrieve(ReferenceQuery(query_text="alpha"))
