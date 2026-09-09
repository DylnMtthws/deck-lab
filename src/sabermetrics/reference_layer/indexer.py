"""Atomic, generation-keyed embedding builds for reference chunks."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from sabermetrics.reference_layer.chunker import Chunk
from sabermetrics.substrate.bundle import (
    BundleBuildError,
    verify_local_model_revision,
)
from sabermetrics.substrate.dense import (
    DenseEncoder,
    DenseModelUnavailableError,
    LocalSentenceTransformerEncoder,
)
from sabermetrics.substrate.settings import (
    EmbeddingModelSettings,
    load_research_settings,
)

logger = logging.getLogger(__name__)

REFERENCE_DOCUMENT_TEMPLATE_VERSION = "reference-chunk-content.v1"
REFERENCE_EMBEDDING_DTYPE = "float32"
_NORMALIZATION_ATOL = 1e-5


class ReferenceEmbeddingBuildError(RuntimeError):
    """A reference embedding generation could not be built safely."""


def reference_content_sha256(chunks: Sequence[Chunk]) -> str:
    """Return a deterministic hash of canonical reference chunk facts.

    Timestamps and the legacy embedding column are deliberately absent. Chunk
    identity is included because embedding rows are keyed by chunk ID.

    Args:
        chunks: Complete chunk corpus in any order.

    Returns:
        Lowercase SHA-256 digest.
    """
    canonical_chunks = [
        {
            "content": chunk.content,
            "document": chunk.document,
            "id": chunk.id,
            "section": chunk.section,
            "tier": chunk.tier,
        }
        for chunk in sorted(chunks, key=lambda item: item.id)
    ]
    return _sha256_json(
        {
            "document_template_version": REFERENCE_DOCUMENT_TEMPLATE_VERSION,
            "chunks": canonical_chunks,
        }
    )


def reference_embedding_sha256(
    chunk_ids: Sequence[str], matrix: NDArray[np.float32]
) -> str:
    """Hash ordered chunk identities and normalized float32 vector bytes.

    Args:
        chunk_ids: Chunk IDs in matrix row order.
        matrix: Validated embedding matrix.

    Returns:
        Lowercase SHA-256 digest.
    """
    digest = hashlib.sha256()
    for chunk_id, vector in zip(chunk_ids, matrix, strict=True):
        encoded_id = chunk_id.encode("utf-8")
        digest.update(len(encoded_id).to_bytes(8, "big"))
        digest.update(encoded_id)
        digest.update(np.ascontiguousarray(vector, dtype=np.float32).tobytes())
    return digest.hexdigest()


def reference_generation_id(
    *,
    settings: EmbeddingModelSettings,
    content_sha256: str,
    embedding_sha256: str,
    row_count: int,
) -> str:
    """Return deterministic identity for one complete embedding generation.

    Args:
        settings: Pinned embedding model settings.
        content_sha256: Canonical reference corpus hash.
        embedding_sha256: Ordered vector payload hash.
        row_count: Number of chunks and embedding rows.

    Returns:
        Lowercase SHA-256 digest with no timestamp input.
    """
    return _sha256_json(
        {
            "content_sha256": content_sha256,
            "dimensions": settings.dimensions,
            "document_template_version": REFERENCE_DOCUMENT_TEMPLATE_VERSION,
            "dtype": REFERENCE_EMBEDDING_DTYPE,
            "embedding_sha256": embedding_sha256,
            "model_id": settings.model_id,
            "model_revision": settings.revision,
            "normalized": settings.normalize,
            "row_count": row_count,
        }
    )


class EmbeddingIndexer:
    """Build and atomically activate complete reference embedding generations.

    The default adapter loads the pinned BGE model only from its configured
    local directory. Tests may inject the small :class:`DenseEncoder` protocol.
    The legacy ``reference_chunks.embedding`` column is never read or written.
    """

    def __init__(
        self,
        db_path: Path,
        model_name: str | None = None,
        device: str | None = None,
        *,
        encoder: DenseEncoder | None = None,
        settings: EmbeddingModelSettings | None = None,
    ) -> None:
        """Initialize a generation builder.

        Args:
            db_path: SQLite application database.
            model_name: Deprecated compatibility argument. If supplied, it must
                equal the pinned model ID.
            device: Deprecated compatibility argument. If supplied, it must
                equal the pinned device.
            encoder: Optional local/protocol encoder.
            settings: Optional pinned settings injection.

        Raises:
            ValueError: If compatibility arguments try to override the pin.
        """
        pinned = settings or load_research_settings().embedding
        if model_name is not None and model_name != pinned.model_id:
            raise ValueError(
                f"model_name must match pinned embedding model {pinned.model_id!r}"
            )
        if device is not None and device != pinned.device:
            raise ValueError(
                f"device must match pinned embedding device {pinned.device!r}"
            )
        if not pinned.normalize:
            raise ValueError("reference embeddings require normalize=true")

        self.db_path = db_path
        self.settings = pinned
        self.model_name = pinned.model_id
        self.device = pinned.device
        self._encoder = encoder

    def _get_model(self) -> DenseEncoder:
        """Return the local encoder, preserving the legacy missing-extra error."""
        try:
            encoder = self._get_encoder()
            _ = encoder.dimensions
        except DenseModelUnavailableError as exc:
            raise RuntimeError("install sabermetrics[research]") from exc
        return encoder

    def index_chunks(self, chunks: list[Chunk], batch_size: int | None = None) -> int:
        """Upsert chunks, rebuild all current embeddings, and activate atomically.

        The entire source observation, generation write, validation, completion,
        and singleton pointer swap occur in one ``BEGIN IMMEDIATE`` transaction.
        Any exception rolls back both chunk changes and generation changes, so a
        prior active generation remains active.

        Args:
            chunks: New or updated chunks to merge into the current corpus.
            batch_size: Optional positive compatibility override. Defaults to
                the pinned model batch size.

        Returns:
            Number of rows in the activated complete generation.

        Raises:
            ReferenceEmbeddingBuildError: If source rows, encoder output, or
                persisted generation rows violate the generation contract.
        """
        if not chunks:
            return 0
        effective_batch_size = (
            self.settings.batch_size if batch_size is None else batch_size
        )
        if (
            isinstance(effective_batch_size, bool)
            or not isinstance(effective_batch_size, int)
            or effective_batch_size <= 0
        ):
            raise ValueError("batch_size must be a positive integer")
        _validate_input_chunks(chunks)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_chunks(conn, chunks)
            corpus = _load_current_chunks(conn)
            documents = [_render_document(chunk) for chunk in corpus]
            matrix = self._encode_documents(documents, effective_batch_size)
            chunk_ids = [chunk.id for chunk in corpus]
            content_sha256 = reference_content_sha256(corpus)
            embedding_sha256 = reference_embedding_sha256(chunk_ids, matrix)
            generation_id = reference_generation_id(
                settings=self.settings,
                content_sha256=content_sha256,
                embedding_sha256=embedding_sha256,
                row_count=len(corpus),
            )
            self._write_generation(
                conn,
                generation_id=generation_id,
                content_sha256=content_sha256,
                embedding_sha256=embedding_sha256,
                chunks=corpus,
                matrix=matrix,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            "Activated reference embedding generation %s with %d chunks",
            generation_id,
            len(corpus),
        )
        return len(corpus)

    def compute_embedding(self, text: str) -> NDArray[np.float32]:
        """Compute one normalized query embedding with the pinned BGE prefix.

        Args:
            text: Non-empty query text.

        Returns:
            One-dimensional normalized float32 vector.

        Raises:
            ReferenceEmbeddingBuildError: If the query or encoder output is
                malformed.
        """
        if not isinstance(text, str) or not text.strip():
            raise ReferenceEmbeddingBuildError("reference query text must be non-empty")
        prefixed = self.settings.query_prefix + text.strip()
        return cast(
            NDArray[np.float32],
            self._encode_documents([prefixed], self.settings.batch_size)[0],
        )

    def _get_encoder(self) -> DenseEncoder:
        if self._encoder is None:
            try:
                verify_local_model_revision(
                    self.settings.local_dir,
                    self.settings.revision,
                    "reference embedding",
                )
            except BundleBuildError as exc:
                raise ReferenceEmbeddingBuildError(str(exc)) from exc
            self._encoder = LocalSentenceTransformerEncoder(self.settings)
        return self._encoder

    def _encode_documents(
        self, documents: Sequence[str], batch_size: int
    ) -> NDArray[np.float32]:
        encoder = self._get_encoder()
        if encoder.dimensions != self.settings.dimensions:
            raise ReferenceEmbeddingBuildError(
                f"encoder dimensions {encoder.dimensions} do not match pinned "
                f"dimensions {self.settings.dimensions}"
            )
        try:
            raw = encoder.encode(documents, batch_size=batch_size)
            matrix = np.asarray(raw, dtype=np.float32)
        except ReferenceEmbeddingBuildError:
            raise
        except Exception as exc:
            raise ReferenceEmbeddingBuildError(
                f"reference encoder failed: {exc}"
            ) from exc
        _validate_matrix(
            matrix,
            expected_rows=len(documents),
            expected_dimensions=self.settings.dimensions,
            require_normalized=False,
        )
        norms = np.linalg.norm(matrix, axis=1)
        normalized = np.ascontiguousarray(
            matrix / norms[:, np.newaxis], dtype=np.float32
        )
        _validate_matrix(
            normalized,
            expected_rows=len(documents),
            expected_dimensions=self.settings.dimensions,
            require_normalized=True,
        )
        return normalized

    @staticmethod
    def _upsert_chunks(conn: sqlite3.Connection, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            conn.execute(
                """
                INSERT INTO reference_chunks
                    (id, document, section, tier, content, last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    document = excluded.document,
                    section = excluded.section,
                    tier = excluded.tier,
                    content = excluded.content,
                    last_updated = CURRENT_TIMESTAMP
                """,
                (
                    chunk.id,
                    chunk.document,
                    chunk.section,
                    chunk.tier,
                    chunk.content,
                ),
            )

    def _write_generation(
        self,
        conn: sqlite3.Connection,
        *,
        generation_id: str,
        content_sha256: str,
        embedding_sha256: str,
        chunks: Sequence[Chunk],
        matrix: NDArray[np.float32],
    ) -> None:
        existing = conn.execute(
            "SELECT generation_id FROM reference_embedding_generations "
            "WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO reference_embedding_generations (
                    generation_id, model_id, model_revision,
                    document_template_version, dimensions, dtype, normalized,
                    content_sha256, embedding_sha256, row_count, complete,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                """,
                (
                    generation_id,
                    self.settings.model_id,
                    self.settings.revision,
                    REFERENCE_DOCUMENT_TEMPLATE_VERSION,
                    self.settings.dimensions,
                    REFERENCE_EMBEDDING_DTYPE,
                    1,
                    content_sha256,
                    embedding_sha256,
                    len(chunks),
                ),
            )
            conn.executemany(
                """
                INSERT INTO reference_chunk_embeddings
                    (generation_id, chunk_id, embedding)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        generation_id,
                        chunk.id,
                        sqlite3.Binary(
                            np.ascontiguousarray(vector, dtype=np.float32).tobytes()
                        ),
                    )
                    for chunk, vector in zip(chunks, matrix, strict=True)
                ],
            )
            self._validate_persisted_generation(
                conn,
                generation_id=generation_id,
                chunks=chunks,
                matrix=matrix,
            )
            conn.execute(
                """
                UPDATE reference_embedding_generations
                SET complete = 1, completed_at = CURRENT_TIMESTAMP
                WHERE generation_id = ? AND complete = 0
                """,
                (generation_id,),
            )
        else:
            self._validate_persisted_generation(
                conn,
                generation_id=generation_id,
                chunks=chunks,
                matrix=matrix,
            )

        complete = conn.execute(
            """
            SELECT complete, completed_at
            FROM reference_embedding_generations
            WHERE generation_id = ?
            """,
            (generation_id,),
        ).fetchone()
        if (
            complete is None
            or complete["complete"] != 1
            or not complete["completed_at"]
        ):
            raise ReferenceEmbeddingBuildError(
                f"generation {generation_id} is not complete and cannot be activated"
            )
        conn.execute(
            """
            INSERT INTO active_reference_embedding_generation
                (singleton, generation_id, activated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(singleton) DO UPDATE SET
                generation_id = excluded.generation_id,
                activated_at = excluded.activated_at
            """,
            (generation_id,),
        )

    def _validate_persisted_generation(
        self,
        conn: sqlite3.Connection,
        *,
        generation_id: str,
        chunks: Sequence[Chunk],
        matrix: NDArray[np.float32],
    ) -> None:
        metadata = conn.execute(
            "SELECT * FROM reference_embedding_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        expected = {
            "model_id": self.settings.model_id,
            "model_revision": self.settings.revision,
            "document_template_version": REFERENCE_DOCUMENT_TEMPLATE_VERSION,
            "dimensions": self.settings.dimensions,
            "dtype": REFERENCE_EMBEDDING_DTYPE,
            "normalized": 1,
            "content_sha256": reference_content_sha256(chunks),
            "embedding_sha256": reference_embedding_sha256(
                [chunk.id for chunk in chunks], matrix
            ),
            "row_count": len(chunks),
        }
        if metadata is None or any(
            metadata[key] != value for key, value in expected.items()
        ):
            raise ReferenceEmbeddingBuildError(
                f"generation {generation_id} metadata does not match its build"
            )

        rows = conn.execute(
            """
            SELECT chunk_id, embedding
            FROM reference_chunk_embeddings
            WHERE generation_id = ?
            ORDER BY chunk_id
            """,
            (generation_id,),
        ).fetchall()
        if len(rows) != len(chunks):
            raise ReferenceEmbeddingBuildError(
                f"generation {generation_id} is partial: expected {len(chunks)} "
                f"rows, found {len(rows)}"
            )
        for row, chunk, expected_vector in zip(rows, chunks, matrix, strict=True):
            if row["chunk_id"] != chunk.id:
                raise ReferenceEmbeddingBuildError(
                    f"generation {generation_id} has mixed chunk identities"
                )
            vector = np.frombuffer(row["embedding"], dtype=np.float32)
            _validate_vector(vector, self.settings.dimensions)
            if not np.array_equal(vector, expected_vector):
                raise ReferenceEmbeddingBuildError(
                    f"generation {generation_id} vector payload changed"
                )


def _validate_input_chunks(chunks: Sequence[Chunk]) -> None:
    ids: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk.id, str) or not chunk.id:
            raise ReferenceEmbeddingBuildError("chunk IDs must be non-empty strings")
        if chunk.id in ids:
            raise ReferenceEmbeddingBuildError(f"duplicate chunk ID: {chunk.id}")
        ids.add(chunk.id)
        if not isinstance(chunk.document, str) or not chunk.document:
            raise ReferenceEmbeddingBuildError(
                f"chunk {chunk.id} has no document identity"
            )
        if isinstance(chunk.tier, bool) or not isinstance(chunk.tier, int):
            raise ReferenceEmbeddingBuildError(
                f"chunk {chunk.id} tier must be an integer"
            )
        if not isinstance(chunk.content, str) or not chunk.content.strip():
            raise ReferenceEmbeddingBuildError(f"chunk {chunk.id} has empty content")


def _load_current_chunks(conn: sqlite3.Connection) -> list[Chunk]:
    rows = conn.execute("""
        SELECT id, document, section, tier, content
        FROM reference_chunks
        ORDER BY id
        """).fetchall()
    chunks = [
        Chunk(
            id=row["id"],
            document=row["document"],
            section=row["section"],
            tier=row["tier"],
            content=row["content"],
        )
        for row in rows
    ]
    if not chunks:
        raise ReferenceEmbeddingBuildError("reference corpus is empty")
    _validate_input_chunks(chunks)
    return chunks


def _render_document(chunk: Chunk) -> str:
    if REFERENCE_DOCUMENT_TEMPLATE_VERSION != "reference-chunk-content.v1":
        raise ReferenceEmbeddingBuildError(
            "unsupported reference document template version"
        )
    return chunk.content


def _validate_matrix(
    matrix: NDArray[np.float32],
    *,
    expected_rows: int,
    expected_dimensions: int,
    require_normalized: bool,
) -> None:
    expected_shape = (expected_rows, expected_dimensions)
    if matrix.ndim != 2 or matrix.shape != expected_shape:
        raise ReferenceEmbeddingBuildError(
            f"encoder returned shape {matrix.shape}; expected {expected_shape}"
        )
    if matrix.dtype != np.dtype(np.float32):
        raise ReferenceEmbeddingBuildError(
            f"encoder returned dtype {matrix.dtype}; expected float32"
        )
    if not np.all(np.isfinite(matrix)):
        raise ReferenceEmbeddingBuildError("encoder returned non-finite vectors")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0.0):
        raise ReferenceEmbeddingBuildError(
            "encoder returned zero or non-finite vectors"
        )
    if require_normalized and not np.allclose(
        norms, 1.0, rtol=0.0, atol=_NORMALIZATION_ATOL
    ):
        raise ReferenceEmbeddingBuildError("reference vectors are not normalized")


def _validate_vector(vector: NDArray[np.float32], expected_dimensions: int) -> None:
    if vector.ndim != 1 or vector.shape != (expected_dimensions,):
        raise ReferenceEmbeddingBuildError(
            f"stored vector shape {vector.shape} does not match "
            f"({expected_dimensions},)"
        )
    if vector.dtype != np.dtype(np.float32) or not np.all(np.isfinite(vector)):
        raise ReferenceEmbeddingBuildError(
            "stored vector must be finite native float32"
        )
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or not np.isclose(
        norm, 1.0, rtol=0.0, atol=_NORMALIZATION_ATOL
    ):
        raise ReferenceEmbeddingBuildError("stored vector is not normalized")


def _sha256_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
