"""Fail-closed retrieval from one active reference embedding generation."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from sabermetrics.reference_layer.chunker import Chunk
from sabermetrics.reference_layer.indexer import (
    REFERENCE_DOCUMENT_TEMPLATE_VERSION,
    REFERENCE_EMBEDDING_DTYPE,
    EmbeddingIndexer,
    reference_content_sha256,
    reference_embedding_sha256,
    reference_generation_id,
)
from sabermetrics.substrate.settings import (
    EmbeddingModelSettings,
    load_research_settings,
)

logger = logging.getLogger(__name__)

_NORMALIZATION_ATOL = 1e-5


class ReferenceRetrievalError(RuntimeError):
    """Base error for an unavailable or invalid reference embedding index."""


class ReferenceGenerationAbsentError(ReferenceRetrievalError):
    """No active reference embedding generation exists."""


class ReferenceGenerationUnlabelledError(ReferenceRetrievalError):
    """Only legacy embeddings without model/generation identity exist."""


class ReferenceGenerationIncompleteError(ReferenceRetrievalError):
    """The active generation is partial or not marked complete."""


class ReferenceGenerationMismatchError(ReferenceRetrievalError):
    """The active generation does not match current pinned research settings."""


class ReferenceGenerationCorruptError(ReferenceRetrievalError):
    """The active generation contains malformed or mixed vector rows."""


class ReferenceQuery(BaseModel):
    """Query specification for reference retrieval."""

    query_text: str
    tier_filter: list[int] | None = None
    document_filter: list[str] | None = None
    top_k: int = 10


class RetrievedChunk(BaseModel):
    """A retrieved reference chunk with similarity score."""

    id: str
    document: str
    section: str | None
    tier: int
    content: str
    similarity_score: float


class QueryEmbeddingIndexer(Protocol):
    """Boundary for computing one normalized reference query embedding."""

    def compute_embedding(self, text: str) -> NDArray[np.float32]:
        """Return one normalized float32 query vector."""


@dataclass(frozen=True)
class _IndexedChunk:
    chunk: Chunk
    embedding: NDArray[np.float32]


class ReferenceRetriever:
    """Query exactly one validated active reference embedding generation.

    Legacy blobs in ``reference_chunks.embedding`` are inert compatibility
    data. There is intentionally no implicit text fallback: absence, MiniLM,
    stale metadata, partial generations, and corrupt/mixed vectors are explicit
    errors so callers cannot mistake degraded text matching for dense retrieval.
    """

    def __init__(
        self,
        db_path: Path,
        indexer: QueryEmbeddingIndexer | None = None,
        *,
        settings: EmbeddingModelSettings | None = None,
    ) -> None:
        """Initialize the strict active-generation reader.

        Args:
            db_path: Path to the SQLite database.
            indexer: Optional query encoder injection.
            settings: Optional pinned settings injection.
        """
        self.db_path = db_path
        self.settings = settings or load_research_settings().embedding
        self._indexer = indexer

    def retrieve(self, query: ReferenceQuery) -> list[RetrievedChunk]:
        """Retrieve top-K chunks from the active validated generation.

        Args:
            query: Query text and optional tier/document filters.

        Returns:
            Up to ``top_k`` chunks ordered by descending cosine similarity and
            then stable chunk ID.

        Raises:
            ReferenceRetrievalError: If the generation is absent, unlabelled,
                partial, stale, MiniLM-based, mixed, or corrupt.
            ValueError: If query text or ``top_k`` is invalid.
        """
        if not isinstance(query.query_text, str) or not query.query_text.strip():
            raise ValueError("reference query text must be non-empty")
        if (
            isinstance(query.top_k, bool)
            or not isinstance(query.top_k, int)
            or query.top_k <= 0
        ):
            raise ValueError("reference top_k must be a positive integer")

        generation_id, indexed_chunks = self._load_active_generation()
        query_embedding = np.asarray(
            self._get_indexer().compute_embedding(query.query_text),
            dtype=np.float32,
        )
        _validate_vector(
            query_embedding,
            expected_dimensions=self.settings.dimensions,
            label="query",
        )

        tier_filter = set(query.tier_filter or ())
        document_filter = set(query.document_filter or ())
        scored: list[tuple[float, Chunk]] = []
        for indexed in indexed_chunks:
            chunk = indexed.chunk
            if tier_filter and chunk.tier not in tier_filter:
                continue
            if document_filter and chunk.document not in document_filter:
                continue
            score = float(np.dot(query_embedding, indexed.embedding))
            if not np.isfinite(score):
                raise ReferenceGenerationCorruptError(
                    f"generation {generation_id} produced a non-finite similarity"
                )
            scored.append((score, chunk))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            RetrievedChunk(
                id=chunk.id,
                document=chunk.document,
                section=chunk.section,
                tier=chunk.tier,
                content=chunk.content,
                similarity_score=round(score, 4),
            )
            for score, chunk in scored[: query.top_k]
        ]

    def _get_indexer(self) -> QueryEmbeddingIndexer:
        if self._indexer is None:
            self._indexer = EmbeddingIndexer(self.db_path, settings=self.settings)
        return self._indexer

    def _load_active_generation(self) -> tuple[str, tuple[_IndexedChunk, ...]]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            try:
                pointers = conn.execute("""
                    SELECT singleton, generation_id, activated_at
                    FROM active_reference_embedding_generation
                    """).fetchall()
            except sqlite3.OperationalError as exc:
                self._raise_absent_or_unlabelled(conn, exc)
                raise AssertionError("unreachable")

            if not pointers:
                self._raise_absent_or_unlabelled(conn)
            if len(pointers) != 1 or pointers[0]["singleton"] != 1:
                raise ReferenceGenerationCorruptError(
                    "active reference embedding pointer is not a singleton"
                )
            pointer = pointers[0]
            if not pointer["activated_at"]:
                raise ReferenceGenerationIncompleteError(
                    "active reference embedding pointer has no activation metadata"
                )
            generation_id = pointer["generation_id"]
            metadata = conn.execute(
                """
                SELECT *
                FROM reference_embedding_generations
                WHERE generation_id = ?
                """,
                (generation_id,),
            ).fetchone()
            if metadata is None:
                raise ReferenceGenerationIncompleteError(
                    f"active reference generation {generation_id} has no metadata"
                )
            self._validate_metadata(metadata)
            chunks = _load_current_chunks(conn)
            content_sha256 = reference_content_sha256(chunks)
            if content_sha256 != metadata["content_sha256"]:
                raise ReferenceGenerationMismatchError(
                    f"active reference generation {generation_id} is stale: "
                    "current chunk content hash differs"
                )
            if len(chunks) != metadata["row_count"]:
                raise ReferenceGenerationIncompleteError(
                    f"active reference generation {generation_id} row count "
                    "does not match current chunks"
                )
            embedding_rows = conn.execute(
                """
                SELECT chunk_id, embedding
                FROM reference_chunk_embeddings
                WHERE generation_id = ?
                ORDER BY chunk_id
                """,
                (generation_id,),
            ).fetchall()
            if len(embedding_rows) != metadata["row_count"]:
                raise ReferenceGenerationIncompleteError(
                    f"active reference generation {generation_id} is partial: "
                    f"metadata declares {metadata['row_count']} rows but "
                    f"{len(embedding_rows)} exist"
                )

            indexed_chunks: list[_IndexedChunk] = []
            vectors: list[NDArray[np.float32]] = []
            for row, chunk in zip(embedding_rows, chunks, strict=True):
                if row["chunk_id"] != chunk.id:
                    raise ReferenceGenerationCorruptError(
                        f"active reference generation {generation_id} contains "
                        "mixed or stale chunk identities"
                    )
                vector = np.frombuffer(row["embedding"], dtype=np.float32)
                _validate_vector(
                    vector,
                    expected_dimensions=self.settings.dimensions,
                    label=f"generation {generation_id} chunk {chunk.id}",
                )
                vectors.append(vector)
                indexed_chunks.append(_IndexedChunk(chunk=chunk, embedding=vector))

            matrix = np.stack(vectors).astype(np.float32, copy=False)
            embedding_sha256 = reference_embedding_sha256(
                [chunk.id for chunk in chunks], matrix
            )
            if embedding_sha256 != metadata["embedding_sha256"]:
                raise ReferenceGenerationCorruptError(
                    f"active reference generation {generation_id} vector hash "
                    "does not match metadata"
                )
            expected_generation_id = reference_generation_id(
                settings=self.settings,
                content_sha256=content_sha256,
                embedding_sha256=embedding_sha256,
                row_count=len(chunks),
            )
            if expected_generation_id != generation_id:
                raise ReferenceGenerationCorruptError(
                    f"active reference generation identity {generation_id} "
                    "does not match its deterministic payload"
                )
            return generation_id, tuple(indexed_chunks)
        except sqlite3.OperationalError as exc:
            raise ReferenceGenerationUnlabelledError(
                f"reference generation schema is incomplete or unlabelled: {exc}"
            ) from exc
        finally:
            conn.close()

    def _validate_metadata(self, metadata: sqlite3.Row) -> None:
        generation_id = metadata["generation_id"]
        if metadata["complete"] != 1 or not metadata["completed_at"]:
            raise ReferenceGenerationIncompleteError(
                f"active reference generation {generation_id} is not complete"
            )
        model_id = str(metadata["model_id"])
        if "minilm" in model_id.casefold():
            raise ReferenceGenerationMismatchError(
                f"active reference generation {generation_id} uses stale MiniLM "
                f"model {model_id!r}; pinned BGE generation required"
            )
        expected = {
            "model_id": self.settings.model_id,
            "model_revision": self.settings.revision,
            "document_template_version": REFERENCE_DOCUMENT_TEMPLATE_VERSION,
            "dimensions": self.settings.dimensions,
            "dtype": REFERENCE_EMBEDDING_DTYPE,
            "normalized": 1,
        }
        mismatches = [
            f"{key}={metadata[key]!r} (expected {value!r})"
            for key, value in expected.items()
            if metadata[key] != value
        ]
        if mismatches:
            raise ReferenceGenerationMismatchError(
                f"active reference generation {generation_id} is stale or "
                f"mislabelled: {', '.join(mismatches)}"
            )
        if (
            isinstance(metadata["row_count"], bool)
            or not isinstance(metadata["row_count"], int)
            or metadata["row_count"] <= 0
        ):
            raise ReferenceGenerationIncompleteError(
                f"active reference generation {generation_id} has invalid row count"
            )

    @staticmethod
    def _raise_absent_or_unlabelled(
        conn: sqlite3.Connection, cause: Exception | None = None
    ) -> None:
        try:
            legacy_count = conn.execute(
                "SELECT COUNT(*) FROM reference_chunks WHERE embedding IS NOT NULL"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            legacy_count = 0
        if legacy_count:
            error: ReferenceRetrievalError = ReferenceGenerationUnlabelledError(
                f"{legacy_count} legacy reference embeddings are unlabelled and "
                "inert; build and activate a pinned BGE generation"
            )
        else:
            error = ReferenceGenerationAbsentError(
                "no active reference embedding generation; build the pinned "
                "BGE reference index"
            )
        if cause is not None:
            raise error from cause
        raise error


def _load_current_chunks(conn: sqlite3.Connection) -> list[Chunk]:
    rows = conn.execute("""
        SELECT id, document, section, tier, content
        FROM reference_chunks
        ORDER BY id
        """).fetchall()
    return [
        Chunk(
            id=row["id"],
            document=row["document"],
            section=row["section"],
            tier=row["tier"],
            content=row["content"],
        )
        for row in rows
    ]


def _validate_vector(
    vector: NDArray[np.float32], *, expected_dimensions: int, label: str
) -> None:
    if vector.ndim != 1 or vector.shape != (expected_dimensions,):
        raise ReferenceGenerationCorruptError(
            f"{label} has mixed/wrong vector dimensions {vector.shape}; "
            f"expected ({expected_dimensions},)"
        )
    if vector.dtype != np.dtype(np.float32) or not np.all(np.isfinite(vector)):
        raise ReferenceGenerationCorruptError(f"{label} is not a finite float32 vector")
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or not np.isclose(
        norm, 1.0, rtol=0.0, atol=_NORMALIZATION_ATOL
    ):
        raise ReferenceGenerationCorruptError(f"{label} is not normalized")
