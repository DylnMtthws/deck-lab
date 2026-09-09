"""Bounded, deterministic cross-encoder reranking for card retrieval.

The ordinary entry point returns only the configured prefix of the fused
ranking.  Candidates outside that prefix are neither scored nor appended,
because appending them after a cross-encoder ranking would not establish a
total order between the scored and unscored groups.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, SupportsFloat, cast

MAX_RERANK_POOL = 1000


class RerankerError(RuntimeError):
    """Base error for visible cross-encoder scoring failures."""


class RerankerUnavailableError(RerankerError):
    """Raised when the configured local cross-encoder cannot be loaded."""


class PairScorer(Protocol):
    """Structural interface for a query-document pair scorer."""

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        """Score query-document pairs in their supplied order.

        Args:
            pairs: Ordered ``(query, document)`` pairs.

        Returns:
            Exactly one finite real-valued score per pair.
        """


class _CrossEncoderModel(Protocol):
    """The portion of ``sentence_transformers.CrossEncoder`` used here."""

    def predict(
        self,
        sentences: Sequence[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
    ) -> object:
        """Return one score for every supplied sentence pair."""


@dataclass(frozen=True)
class RerankCandidate:
    """One fused candidate eligible for bounded cross-encoder reranking.

    Attributes:
        oracle_id: Stable Oracle card identifier.
        document: Deterministic card document presented to the cross-encoder.
        fused_score: Finite score from the preceding fusion stage.
    """

    oracle_id: str
    document: str
    fused_score: float

    def __post_init__(self) -> None:
        """Reject candidate values that cannot participate in a total order."""
        if not isinstance(self.oracle_id, str) or not self.oracle_id.strip():
            raise ValueError("oracle_id must be a non-empty string")
        if not isinstance(self.document, str) or not self.document.strip():
            raise ValueError("document must be a non-empty string")
        fused_score = _finite_number(self.fused_score, label="fused_score")
        object.__setattr__(self, "fused_score", fused_score)


@dataclass(frozen=True)
class RerankedCandidate:
    """One candidate with its validated cross-encoder score.

    Attributes:
        oracle_id: Stable Oracle card identifier.
        document: Deterministic card document scored by the cross-encoder.
        fused_score: Finite score from the preceding fusion stage.
        reranker_score: Finite cross-encoder relevance score.
    """

    oracle_id: str
    document: str
    fused_score: float
    reranker_score: float


class LocalCrossEncoderScorer:
    """Lazy, offline adapter for a sentence-transformers ``CrossEncoder``.

    Only ``local_dir`` is passed as the model location, and
    ``local_files_only=True`` is mandatory.  Importing sentence-transformers
    and loading model weights are both deferred until the first non-empty
    scoring call.

    Args:
        local_dir: Directory containing a complete local model snapshot.
        max_length: Maximum tokenized query-document length.
        device: Device understood by sentence-transformers, normally ``cpu``.

    Raises:
        TypeError: If ``max_length`` has the wrong runtime type.
        ValueError: If static adapter configuration is malformed.
    """

    def __init__(
        self,
        local_dir: str | Path,
        *,
        max_length: int = 512,
        device: str = "cpu",
    ) -> None:
        """Store local model configuration without loading model weights."""
        if isinstance(max_length, bool) or not isinstance(max_length, int):
            raise TypeError("max_length must be a positive integer")
        if max_length <= 0:
            raise ValueError("max_length must be a positive integer")
        if not isinstance(device, str) or not device.strip():
            raise ValueError("device must be a non-empty string")
        self._local_dir = Path(local_dir).expanduser()
        self._max_length = max_length
        self._device = device
        self._model: _CrossEncoderModel | None = None

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        """Score one already-bounded batch with the local cross-encoder.

        Args:
            pairs: Ordered query-document pairs. An empty batch returns without
                loading the model.

        Returns:
            A tuple containing one finite score per pair.

        Raises:
            RerankerError: If prediction fails or returns malformed scores.
            RerankerUnavailableError: If the package or local model is absent.
        """
        if not pairs:
            return ()
        model = self._load_model()
        try:
            raw_scores = model.predict(
                pairs,
                batch_size=len(pairs),
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        except Exception as exc:
            raise RerankerError("local cross-encoder prediction failed") from exc
        return _validated_scores(raw_scores, expected=len(pairs))

    def _load_model(self) -> _CrossEncoderModel:
        """Load and cache the configured model without network fallback."""
        if self._model is not None:
            return self._model
        if not self._local_dir.is_dir():
            raise RerankerUnavailableError(
                f"local cross-encoder directory is absent: {self._local_dir}"
            )
        try:
            module = importlib.import_module("sentence_transformers")
        except ImportError as exc:
            raise RerankerUnavailableError(
                "sentence-transformers is unavailable for local reranking"
            ) from exc

        cross_encoder = getattr(module, "CrossEncoder", None)
        if not callable(cross_encoder):
            raise RerankerUnavailableError(
                "sentence-transformers does not provide CrossEncoder"
            )
        try:
            model = cross_encoder(
                str(self._local_dir),
                max_length=self._max_length,
                device=self._device,
                local_files_only=True,
                trust_remote_code=False,
            )
        except Exception as exc:
            raise RerankerUnavailableError(
                f"local cross-encoder could not be loaded: {self._local_dir}"
            ) from exc
        if not callable(getattr(model, "predict", None)):
            raise RerankerUnavailableError(
                "loaded local cross-encoder has no predict method"
            )
        self._model = cast(_CrossEncoderModel, model)
        return self._model


def rerank_candidates(
    query: str,
    candidates: Sequence[RerankCandidate],
    scorer: PairScorer,
    *,
    pool_limit: int,
    batch_size: int,
) -> tuple[RerankedCandidate, ...]:
    """Rerank only the bounded prefix of a fused candidate ranking.

    The result contains at most ``pool_limit`` candidates. Unscored candidates
    after that prefix are intentionally omitted; callers requesting ordinary
    search results therefore receive one complete, deterministic ordering.
    Pair scoring is split into calls of at most ``batch_size`` elements.

    Args:
        query: Non-empty text supplied to the cross-encoder.
        candidates: Fused candidates in their existing deterministic order.
        scorer: Protocol-compatible pair scorer.
        pool_limit: Maximum prefix to score, from 1 through
            :data:`MAX_RERANK_POOL`.
        batch_size: Positive maximum number of pairs in one scorer call.

    Returns:
        The reranked bounded pool, ordered by descending reranker score,
        descending fused score, and ascending oracle ID.

    Raises:
        TypeError: If a bound has the wrong runtime type.
        ValueError: If query or bounds are malformed.
        RerankerError: If candidates are duplicated, scoring fails, or scorer
            output is empty, malformed, the wrong length, or non-finite.
        RerankerUnavailableError: If a local scorer's model is unavailable.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    _bounded_positive_integer(
        pool_limit,
        label="pool_limit",
        maximum=MAX_RERANK_POOL,
    )
    _bounded_positive_integer(
        batch_size,
        label="batch_size",
        maximum=MAX_RERANK_POOL,
    )

    pool = tuple(candidates[:pool_limit])
    if not pool:
        return ()
    if any(not isinstance(candidate, RerankCandidate) for candidate in pool):
        raise RerankerError("all candidates must be RerankCandidate values")
    oracle_ids = [candidate.oracle_id for candidate in pool]
    if len(oracle_ids) != len(set(oracle_ids)):
        raise RerankerError("rerank pool contains duplicate oracle_id values")

    reranked: list[RerankedCandidate] = []
    for start in range(0, len(pool), batch_size):
        batch = pool[start : start + batch_size]
        pairs = tuple((query, candidate.document) for candidate in batch)
        try:
            raw_scores = scorer.score(pairs)
        except RerankerError:
            raise
        except Exception as exc:
            raise RerankerError("cross-encoder scorer failed") from exc
        scores = _validated_scores(raw_scores, expected=len(batch))
        reranked.extend(
            RerankedCandidate(
                oracle_id=candidate.oracle_id,
                document=candidate.document,
                fused_score=candidate.fused_score,
                reranker_score=score,
            )
            for candidate, score in zip(batch, scores, strict=True)
        )

    reranked.sort(
        key=lambda candidate: (
            -candidate.reranker_score,
            -candidate.fused_score,
            candidate.oracle_id,
        )
    )
    return tuple(reranked)


def _bounded_positive_integer(value: int, *, label: str, maximum: int) -> int:
    """Validate a positive integer bound and return it unchanged."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be a positive integer")
    if value <= 0 or value > maximum:
        raise ValueError(f"{label} must be between 1 and {maximum}")
    return value


def _validated_scores(raw_scores: object, *, expected: int) -> tuple[float, ...]:
    """Validate an opaque scorer response without accepting partial output."""
    if isinstance(raw_scores, (str, bytes)):
        raise RerankerError("cross-encoder output is malformed")
    try:
        values = list(cast(Sequence[object], raw_scores))
    except (TypeError, ValueError) as exc:
        raise RerankerError("cross-encoder output is malformed") from exc
    if not values:
        raise RerankerError("cross-encoder output is empty")
    if len(values) != expected:
        raise RerankerError(
            "cross-encoder output length mismatch: "
            f"expected {expected}, received {len(values)}"
        )
    return tuple(
        _finite_number(value, label=f"cross-encoder score {index}")
        for index, value in enumerate(values)
    )


def _finite_number(value: object, *, label: str) -> float:
    """Convert one real-like numeric value while rejecting coercible strings."""
    if isinstance(value, (bool, str, bytes)):
        raise RerankerError(f"{label} must be a finite number")
    try:
        converted = float(cast(SupportsFloat, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RerankerError(f"{label} must be a finite number") from exc
    if not math.isfinite(converted):
        raise RerankerError(f"{label} must be a finite number")
    return converted
