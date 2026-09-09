"""Tests for bounded, deterministic local cross-encoder reranking."""

from __future__ import annotations

import math
import sys
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from sabermetrics.substrate.reranker import (
    LocalCrossEncoderScorer,
    RerankCandidate,
    RerankerError,
    RerankerUnavailableError,
    rerank_candidates,
)

CARD_A = "00000000-0000-0000-0000-000000000001"
CARD_B = "00000000-0000-0000-0000-000000000002"
CARD_C = "00000000-0000-0000-0000-000000000003"
CARD_D = "00000000-0000-0000-0000-000000000004"


@dataclass
class RecordingScorer:
    """Deterministic scorer that records every bounded protocol call."""

    scores_by_document: Mapping[str, float]
    calls: list[tuple[tuple[str, str], ...]] = field(default_factory=list)

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        """Return configured scores while retaining the exact call shape."""
        copied = tuple(pairs)
        self.calls.append(copied)
        return tuple(self.scores_by_document[document] for _, document in copied)


@dataclass
class InvalidScorer:
    """Protocol-shaped fake that deliberately returns an invalid value."""

    output: object

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        """Return malformed output regardless of the supplied pairs."""
        del pairs
        return cast(Sequence[float], self.output)


def _candidate(oracle_id: str, fused_score: float) -> RerankCandidate:
    """Build a candidate whose document exposes its identifier to test fakes."""
    return RerankCandidate(
        oracle_id=oracle_id,
        document=f"document:{oracle_id}",
        fused_score=fused_score,
    )


def test_reranking_obeys_pool_and_batch_bounds() -> None:
    """Only the configured prefix is scored, in bounded scorer calls."""
    oracle_ids = [f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 8)]
    candidates = tuple(
        _candidate(oracle_id, fused_score=float(10 - index))
        for index, oracle_id in enumerate(oracle_ids)
    )
    scorer = RecordingScorer(
        {candidate.document: float(index) for index, candidate in enumerate(candidates)}
    )

    result = rerank_candidates(
        "find interaction",
        candidates,
        scorer,
        pool_limit=5,
        batch_size=2,
    )

    assert len(result) == 5
    assert [len(call) for call in scorer.calls] == [2, 2, 1]
    scored_documents = [document for call in scorer.calls for _, document in call]
    assert scored_documents == [candidate.document for candidate in candidates[:5]]
    assert not set(scored_documents) & {
        candidate.document for candidate in candidates[5:]
    }


@pytest.mark.parametrize(
    ("pool_limit", "batch_size", "label"),
    [
        (0, 1, "pool_limit"),
        (1001, 1, "pool_limit"),
        (1, 0, "batch_size"),
        (1, 1001, "batch_size"),
    ],
)
def test_configured_bounds_fail_before_scoring(
    pool_limit: int,
    batch_size: int,
    label: str,
) -> None:
    """Invalid bounds cannot produce an unbounded scorer call."""
    scorer = RecordingScorer({"document": 1.0})

    with pytest.raises(ValueError, match=label):
        rerank_candidates(
            "query",
            (RerankCandidate(CARD_A, "document", 1.0),),
            scorer,
            pool_limit=pool_limit,
            batch_size=batch_size,
        )

    assert scorer.calls == []


def test_sort_uses_reranker_then_fused_score_then_oracle_id() -> None:
    """Every deterministic tie breaker is applied in the required order."""
    candidates = (
        _candidate(CARD_A, 0.2),
        _candidate(CARD_C, 0.3),
        _candidate(CARD_B, 0.3),
        _candidate(CARD_D, 0.9),
    )
    scorer = RecordingScorer(
        {
            candidates[0].document: 0.5,
            candidates[1].document: 0.5,
            candidates[2].document: 0.5,
            candidates[3].document: 0.4,
        }
    )

    result = rerank_candidates(
        "query",
        candidates,
        scorer,
        pool_limit=4,
        batch_size=4,
    )

    assert [candidate.oracle_id for candidate in result] == [
        CARD_B,
        CARD_C,
        CARD_A,
        CARD_D,
    ]


@pytest.mark.parametrize(
    "output",
    [
        [],
        [0.1],
        [0.1, math.nan],
        [0.1, math.inf],
        [0.1, -math.inf],
        [0.1, True],
        [0.1, "0.2"],
        [0.1, [0.2]],
        "0.1,0.2",
        0.1,
        None,
    ],
)
def test_invalid_scorer_output_is_rejected(output: object) -> None:
    """Empty, malformed, wrong-length, and non-finite output fails closed."""
    with pytest.raises(RerankerError, match="cross-encoder"):
        rerank_candidates(
            "query",
            (_candidate(CARD_A, 0.2), _candidate(CARD_B, 0.1)),
            InvalidScorer(output),
            pool_limit=2,
            batch_size=2,
        )


def test_absent_local_model_is_visible_and_lazy(tmp_path: Path) -> None:
    """A missing model raises a typed error only when scoring is requested."""
    scorer = LocalCrossEncoderScorer(tmp_path / "absent")

    with pytest.raises(RerankerUnavailableError, match="directory is absent"):
        rerank_candidates(
            "query",
            (_candidate(CARD_A, 1.0),),
            scorer,
            pool_limit=1,
            batch_size=1,
        )


def test_local_adapter_loads_once_from_local_directory_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional adapter is lazy and disables model network fallback."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    constructor_calls: list[tuple[str, dict[str, object]]] = []
    prediction_calls: list[dict[str, object]] = []

    class FakeModel:
        """Small stand-in for a loaded sentence-transformers model."""

        def predict(
            self,
            sentences: Sequence[tuple[str, str]],
            *,
            batch_size: int,
            show_progress_bar: bool,
            convert_to_numpy: bool,
        ) -> object:
            """Record adapter arguments and emit one score per pair."""
            prediction_calls.append(
                {
                    "sentences": tuple(sentences),
                    "batch_size": batch_size,
                    "show_progress_bar": show_progress_bar,
                    "convert_to_numpy": convert_to_numpy,
                }
            )
            return [float(index) for index, _ in enumerate(sentences)]

    def fake_cross_encoder(path: str, **kwargs: object) -> FakeModel:
        """Record construction and return a reusable fake model."""
        constructor_calls.append((path, kwargs))
        return FakeModel()

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = fake_cross_encoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    scorer = LocalCrossEncoderScorer(model_dir, max_length=256, device="cpu")
    assert constructor_calls == []

    first = scorer.score((("query", "doc-a"), ("query", "doc-b")))
    second = scorer.score((("query", "doc-c"),))

    assert first == (0.0, 1.0)
    assert second == (0.0,)
    assert constructor_calls == [
        (
            str(model_dir),
            {
                "max_length": 256,
                "device": "cpu",
                "local_files_only": True,
                "trust_remote_code": False,
            },
        )
    ]
    assert [call["batch_size"] for call in prediction_calls] == [2, 1]
    assert all(call["show_progress_bar"] is False for call in prediction_calls)
    assert all(call["convert_to_numpy"] is True for call in prediction_calls)


def test_deterministic_scores_ignore_input_order_for_a_complete_pool() -> None:
    """Identical complete candidate sets always produce identical ordering."""
    candidates = (
        _candidate(CARD_A, 0.4),
        _candidate(CARD_B, 0.2),
        _candidate(CARD_C, 0.3),
        _candidate(CARD_D, 0.1),
    )
    scores = {
        candidates[0].document: 0.2,
        candidates[1].document: 0.9,
        candidates[2].document: 0.9,
        candidates[3].document: 0.1,
    }

    forward = rerank_candidates(
        "query",
        candidates,
        RecordingScorer(scores),
        pool_limit=4,
        batch_size=3,
    )
    reverse = rerank_candidates(
        "query",
        tuple(reversed(candidates)),
        RecordingScorer(scores),
        pool_limit=4,
        batch_size=2,
    )

    assert forward == reverse
