"""Local, fail-closed dense retrieval over normalized NumPy vectors."""

from __future__ import annotations

import importlib
import os
import tempfile
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from sabermetrics.substrate.settings import EmbeddingModelSettings

_NORMALIZATION_ATOL = 1e-5
_DEFAULT_SCORE_BLOCK_SIZE = 4096
_MAX_QUERY_LENGTH = 500


class DenseRetrievalError(RuntimeError):
    """Base error for dense index construction and retrieval."""


class DenseIndexAbsentError(DenseRetrievalError):
    """The requested dense index or local model directory is absent."""


class DenseIndexCorruptError(DenseRetrievalError):
    """A dense index cannot be decoded or violates its storage contract."""


class DenseModelUnavailableError(DenseRetrievalError):
    """The configured local encoder cannot be imported or loaded."""


class DenseModelAbsentError(DenseModelUnavailableError):
    """The configured local encoder directory is absent."""


class DenseModelMismatchError(DenseRetrievalError):
    """The configured encoder or index has an unexpected vector dimension."""


class DenseEncodingError(DenseRetrievalError):
    """An encoder returned malformed, non-finite, or zero embeddings."""


class DenseQueryError(DenseRetrievalError):
    """A dense query is empty or otherwise invalid."""


@runtime_checkable
class DenseEncoder(Protocol):
    """Minimal encoder boundary used by index builds and query retrieval."""

    @property
    def dimensions(self) -> int:
        """Return the encoder's output dimensions."""

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int,
    ) -> NDArray[np.float32]:
        """Encode text in input order without applying a query instruction."""


class LocalSentenceTransformerEncoder:
    """Lazily load a sentence-transformers model from a local directory only.

    The model identifier and revision in :class:`EmbeddingModelSettings` are
    provenance. Runtime loading uses only ``local_dir`` and explicitly disables
    model-hub lookup and remote model code.
    """

    def __init__(self, settings: EmbeddingModelSettings) -> None:
        """Create an unloaded local encoder.

        Args:
            settings: Pinned embedding settings, including the local model path.
        """
        self._settings = settings
        self._model: Any | None = None
        self._dimensions: int | None = None

    @property
    def dimensions(self) -> int:
        """Load the local model if needed and return its declared dimensions."""
        self._load_model()
        assert self._dimensions is not None
        return self._dimensions

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int,
    ) -> NDArray[np.float32]:
        """Encode text with the pinned local model and no network fallback.

        Args:
            texts: Strings to encode in their supplied order.
            batch_size: Positive encoder batch size.

        Returns:
            A two-dimensional float32 array. Normalization and strict shape
            validation are performed by the dense substrate, not this adapter.

        Raises:
            DenseModelUnavailableError: If sentence-transformers or the local
                model cannot be loaded.
        """
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError("batch_size must be a positive integer")
        if batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        model = self._load_model()
        try:
            encoded = model.encode(
                list(texts),
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise DenseModelUnavailableError(
                f"local embedding model failed to encode text: {exc}"
            ) from exc
        return np.asarray(encoded, dtype=np.float32)

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        local_dir = self._settings.local_dir.expanduser().resolve()
        if not local_dir.is_dir():
            raise DenseModelAbsentError(
                f"local embedding model directory missing: {local_dir}"
            )
        try:
            module = importlib.import_module("sentence_transformers")
            model_class = module.SentenceTransformer
        except (ImportError, AttributeError) as exc:
            raise DenseModelUnavailableError(
                "sentence-transformers is required for local dense retrieval"
            ) from exc

        try:
            model = model_class(
                str(local_dir),
                device=self._settings.device,
                local_files_only=True,
                trust_remote_code=False,
            )
            dimensions = model.get_sentence_embedding_dimension()
        except Exception as exc:
            raise DenseModelUnavailableError(
                f"failed to load local embedding model at {local_dir}: {exc}"
            ) from exc
        if (
            isinstance(dimensions, bool)
            or not isinstance(dimensions, int)
            or dimensions <= 0
        ):
            raise DenseModelMismatchError(
                "local embedding model did not declare positive dimensions"
            )
        if dimensions != self._settings.dimensions:
            raise DenseModelMismatchError(
                "local embedding model dimensions "
                f"{dimensions} do not match configured dimensions "
                f"{self._settings.dimensions}"
            )
        self._model = model
        self._dimensions = dimensions
        return model


@dataclass(frozen=True)
class DenseHit:
    """One cosine-ranked card from the dense index."""

    oracle_id: str
    score: float


def build_dense_index(
    path: str | Path,
    documents: Sequence[str],
    *,
    encoder: DenseEncoder,
    settings: EmbeddingModelSettings,
) -> None:
    """Encode documents and atomically write a normalized float32 ``.npy`` index.

    Documents have no BGE query instruction. Their supplied order is retained
    exactly and must be the catalog's canonical ``vector_offset`` order.

    Args:
        path: Destination ``.npy`` file.
        documents: Canonical card documents in vector-offset order.
        encoder: Protocol-based encoder.
        settings: Pinned model dimensions and batch size.

    Raises:
        DenseEncodingError: If documents or encoder output are invalid.
        DenseModelMismatchError: If the encoder has the wrong dimensions.
    """
    _require_normalized(settings)
    _validate_documents(documents)
    matrix = _encode_normalized(
        encoder,
        documents,
        expected_rows=len(documents),
        settings=settings,
    )
    _atomic_save(Path(path), matrix)


class DenseIndex:
    """Memory-mapped normalized vectors plus their vector-offset identities."""

    def __init__(
        self,
        path: str | Path,
        oracle_ids: Sequence[str],
        *,
        encoder: DenseEncoder,
        settings: EmbeddingModelSettings,
        score_block_size: int = _DEFAULT_SCORE_BLOCK_SIZE,
    ) -> None:
        """Open and validate a dense vector artifact.

        Args:
            path: NumPy ``.npy`` matrix path.
            oracle_ids: Oracle IDs in canonical ``vector_offset`` order.
            encoder: Encoder used for subsequent queries.
            settings: Pinned embedding settings.
            score_block_size: Maximum candidate rows copied for one dot product.

        Raises:
            DenseIndexAbsentError: If ``path`` does not exist.
            DenseIndexCorruptError: If the matrix or identity mapping is invalid.
            DenseModelMismatchError: If stored dimensions differ from settings.
        """
        _require_normalized(settings)
        if (
            isinstance(score_block_size, bool)
            or not isinstance(score_block_size, int)
            or score_block_size <= 0
        ):
            raise ValueError("score_block_size must be a positive integer")
        canonical_ids = _validate_oracle_ids(oracle_ids)
        vectors = _load_memmap(
            Path(path),
            expected_rows=len(canonical_ids),
            expected_dimensions=settings.dimensions,
        )

        self._vectors = vectors
        self._oracle_ids = canonical_ids
        self._offset_by_oracle_id = {
            oracle_id: offset for offset, oracle_id in enumerate(canonical_ids)
        }
        self._encoder = encoder
        self._settings = settings
        self._score_block_size = score_block_size

    @property
    def vectors(self) -> np.memmap:
        """Return the validated read-only memory map."""
        return self._vectors

    @property
    def oracle_ids(self) -> tuple[str, ...]:
        """Return identities in vector-offset order."""
        return self._oracle_ids

    def search(
        self,
        query: str,
        *,
        candidate_oracle_ids: Collection[str] | None = None,
        top_k: int,
    ) -> tuple[DenseHit, ...]:
        """Rank eligible cards by cosine similarity.

        The candidate allowlist is converted to vector offsets before any
        scoring or truncation. Since stored and query vectors are normalized,
        cosine similarity is their dot product.

        Args:
            query: Non-empty user text. It is treated only as encoder input.
            candidate_oracle_ids: Eligible cards from structured SQL. ``None``
                means every indexed card; an empty collection means no cards.
            top_k: Maximum number of hits.

        Returns:
            Hits ordered by descending cosine score, then oracle ID.

        Raises:
            DenseQueryError: If the query is empty.
            DenseIndexCorruptError: If the allowlist names an unindexed card.
            DenseModelMismatchError: If the query encoder has wrong dimensions.
            DenseEncodingError: If the query embedding is invalid.
        """
        if not isinstance(query, str):
            raise TypeError("dense query must be a string")
        stripped_query = query.strip()
        if not stripped_query:
            raise DenseQueryError("dense query must not be empty")
        if len(stripped_query) > _MAX_QUERY_LENGTH:
            raise DenseQueryError(f"dense query exceeds {_MAX_QUERY_LENGTH} characters")
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise TypeError("top_k must be a positive integer")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")

        offsets = self._candidate_offsets(candidate_oracle_ids)
        if not offsets:
            return ()

        prefixed_query = self._settings.query_prefix + stripped_query
        query_matrix = _encode_normalized(
            self._encoder,
            [prefixed_query],
            expected_rows=1,
            settings=self._settings,
        )
        query_vector = query_matrix[0]
        scored: list[DenseHit] = []
        for start in range(0, len(offsets), self._score_block_size):
            block_offsets = offsets[start : start + self._score_block_size]
            block = self._vectors[np.asarray(block_offsets, dtype=np.intp)]
            scores = block @ query_vector
            for offset, score in zip(block_offsets, scores, strict=True):
                value = float(score)
                if not np.isfinite(value):
                    raise DenseIndexCorruptError(
                        "dense dot product produced a non-finite score"
                    )
                scored.append(DenseHit(oracle_id=self._oracle_ids[offset], score=value))
        scored.sort(key=lambda hit: (-hit.score, hit.oracle_id))
        return tuple(scored[:top_k])

    def _candidate_offsets(
        self, candidate_oracle_ids: Collection[str] | None
    ) -> list[int]:
        if candidate_oracle_ids is None:
            return list(range(len(self._oracle_ids)))

        requested = set(candidate_oracle_ids)
        unknown = requested - self._offset_by_oracle_id.keys()
        if unknown:
            raise DenseIndexCorruptError(
                "candidate allowlist contains oracle IDs absent from the dense "
                f"index: {sorted(unknown)!r}"
            )
        return [
            offset
            for offset, oracle_id in enumerate(self._oracle_ids)
            if oracle_id in requested
        ]


def _require_normalized(settings: EmbeddingModelSettings) -> None:
    if not settings.normalize:
        raise DenseModelMismatchError(
            "dense retrieval requires normalized embedding settings"
        )


def _validate_documents(documents: Sequence[str]) -> None:
    for offset, document in enumerate(documents):
        if not isinstance(document, str) or not document.strip():
            raise DenseEncodingError(
                f"dense document at vector offset {offset} must be non-empty text"
            )


def _validate_oracle_ids(oracle_ids: Sequence[str]) -> tuple[str, ...]:
    validated: list[str] = []
    for oracle_id in oracle_ids:
        if not isinstance(oracle_id, str):
            raise DenseIndexCorruptError(
                "dense oracle IDs must be canonical UUID strings"
            )
        try:
            parsed = uuid.UUID(oracle_id)
        except (ValueError, AttributeError) as exc:
            raise DenseIndexCorruptError(
                f"malformed dense oracle ID: {oracle_id!r}"
            ) from exc
        if str(parsed) != oracle_id:
            raise DenseIndexCorruptError(f"malformed dense oracle ID: {oracle_id!r}")
        validated.append(oracle_id)
    if len(validated) != len(set(validated)):
        raise DenseIndexCorruptError("dense oracle IDs contain duplicates")
    return tuple(validated)


def _validate_encoder_dimensions(
    encoder: DenseEncoder, settings: EmbeddingModelSettings
) -> None:
    dimensions = encoder.dimensions
    if (
        isinstance(dimensions, bool)
        or not isinstance(dimensions, int)
        or dimensions <= 0
    ):
        raise DenseModelMismatchError(
            "encoder must declare positive integer dimensions"
        )
    if dimensions != settings.dimensions:
        raise DenseModelMismatchError(
            f"encoder dimensions {dimensions} do not match configured dimensions "
            f"{settings.dimensions}"
        )


def _encode_normalized(
    encoder: DenseEncoder,
    texts: Sequence[str],
    *,
    expected_rows: int,
    settings: EmbeddingModelSettings,
) -> NDArray[np.float32]:
    _validate_encoder_dimensions(encoder, settings)
    try:
        raw = encoder.encode(texts, batch_size=settings.batch_size)
        matrix = np.asarray(raw, dtype=np.float32)
    except DenseRetrievalError:
        raise
    except Exception as exc:
        raise DenseEncodingError(f"encoder failed: {exc}") from exc

    expected_shape = (expected_rows, settings.dimensions)
    if matrix.ndim != 2 or matrix.shape != expected_shape:
        raise DenseEncodingError(
            f"encoder returned shape {matrix.shape}; expected {expected_shape}"
        )
    if not np.all(np.isfinite(matrix)):
        raise DenseEncodingError("encoder returned non-finite vectors")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0.0):
        raise DenseEncodingError("encoder returned zero or non-finite vectors")
    normalized = matrix / norms[:, np.newaxis]
    normalized = np.ascontiguousarray(normalized, dtype=np.float32)
    if not np.all(np.isfinite(normalized)):
        raise DenseEncodingError("normalization produced non-finite vectors")
    return normalized


def _atomic_save(path: Path, matrix: NDArray[np.float32]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".npy", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.save(handle, matrix, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _load_memmap(
    path: Path,
    *,
    expected_rows: int,
    expected_dimensions: int,
) -> np.memmap:
    if not path.is_file():
        raise DenseIndexAbsentError(f"dense vector index missing: {path}")
    try:
        matrix = np.load(path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError, EOFError) as exc:
        raise DenseIndexCorruptError(
            f"cannot open dense vector index {path}: {exc}"
        ) from exc
    if not isinstance(matrix, np.memmap):
        raise DenseIndexCorruptError("dense vector index did not open as a memory map")
    if matrix.ndim != 2:
        raise DenseIndexCorruptError(
            f"dense vector index must be two-dimensional, got {matrix.shape}"
        )
    if matrix.shape[1] != expected_dimensions:
        raise DenseModelMismatchError(
            f"dense index dimensions {matrix.shape[1]} do not match configured "
            f"dimensions {expected_dimensions}"
        )
    if matrix.shape[0] != expected_rows:
        raise DenseIndexCorruptError(
            f"dense index vector count {matrix.shape[0]} does not match oracle ID "
            f"count {expected_rows}"
        )
    if matrix.dtype != np.dtype(np.float32):
        raise DenseIndexCorruptError(
            f"dense vector index dtype {matrix.dtype} is not float32"
        )
    if not np.all(np.isfinite(matrix)):
        raise DenseIndexCorruptError("dense vector index contains non-finite vectors")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0.0):
        raise DenseIndexCorruptError(
            "dense vector index contains zero or non-finite vectors"
        )
    if not np.allclose(norms, 1.0, rtol=0.0, atol=_NORMALIZATION_ATOL):
        raise DenseIndexCorruptError(
            "dense vector index contains vectors that are not normalized"
        )
    return matrix
