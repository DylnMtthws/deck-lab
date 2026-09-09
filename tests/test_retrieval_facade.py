"""The facade applies one filter set across every deterministic ranking stage."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from sabermetrics.substrate.bundle import build_bundle
from sabermetrics.substrate.corpus import (
    CardView,
    CorpusExport,
    SnapshotIdentity,
    corpus_content_sha256,
)
from sabermetrics.substrate.models import CardFilters, CardSearchQuery
from sabermetrics.substrate.retrieval import (
    CardRetrievalFacade,
    RetrievalBundleMismatchError,
)
from sabermetrics.substrate.settings import load_research_settings

COUNTER = "00000000-0000-0000-0000-000000000001"
ROCK = "00000000-0000-0000-0000-000000000002"
GREEN = "00000000-0000-0000-0000-000000000003"


class KeywordEncoder:
    dimensions = 2

    def encode(self, texts: Sequence[str], *, batch_size: int) -> NDArray[np.float32]:
        del batch_size
        rows = []
        for text in texts:
            lowered = text.casefold()
            rows.append([1.0, 0.0] if "counter" in lowered else [0.0, 1.0])
        return np.asarray(rows, dtype=np.float32)


class KeywordScorer:
    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        self.pairs.extend(pairs)
        return [10.0 if "Counterspell" in document else 0.0 for _, document in pairs]


class MustNotScore:
    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        raise AssertionError(f"structured-only query tried to score {pairs!r}")


def _settings(tmp_path: Path):
    settings = load_research_settings()
    return settings.model_copy(
        update={
            "artifacts": settings.artifacts.model_copy(
                update={"root": tmp_path / "indexes"}
            ),
            "embedding": settings.embedding.model_copy(
                update={"dimensions": 2, "query_prefix": ""}
            ),
            "retrieval": settings.retrieval.model_copy(
                update={
                    "lexical_pool": 3,
                    "dense_pool": 3,
                    "rerank_pool": 3,
                    "result_limit": 3,
                }
            ),
        }
    )


def _export() -> CorpusExport:
    cards = (
        CardView(
            oracle_id=COUNTER,
            name="Counterspell",
            mana_cost="{U}{U}",
            mana_value=2,
            type_line="Instant",
            oracle_text="Counter target spell.",
            color_identity=("U",),
            all_types=("Instant",),
            commander_legal="legal",
        ),
        CardView(
            oracle_id=ROCK,
            name="Mana Rock",
            mana_cost="{2}",
            mana_value=2,
            type_line="Artifact",
            oracle_text="{T}: Add {C}{C}.",
            color_identity=(),
            all_types=("Artifact",),
            commander_legal="legal",
        ),
        CardView(
            oracle_id=GREEN,
            name="Green Counter",
            mana_cost="{G}",
            mana_value=1,
            type_line="Creature",
            oracle_text="Put a counter on Green Counter.",
            color_identity=("G",),
            all_types=("Creature",),
            commander_legal="legal",
        ),
    )
    return CorpusExport(
        identity=SnapshotIdentity(
            source_view="fixture:mtg_v1.card_any_medium",
            row_count=len(cards),
        ),
        cards=cards,
        content_sha256=corpus_content_sha256(cards),
    )


def _build(tmp_path: Path):
    settings = _settings(tmp_path)
    build_bundle(
        _export(),
        settings,
        encoder=KeywordEncoder(),
        verify_model_files=False,
    )
    return settings


def test_facade_returns_final_ranking_with_all_stage_provenance(tmp_path):
    settings = _build(tmp_path)
    scorer = KeywordScorer()
    with CardRetrievalFacade(
        settings,
        encoder=KeywordEncoder(),
        scorer=scorer,
        verify_model_files=False,
    ) as facade:
        trace = facade.search_with_trace(
            CardSearchQuery(text="counter", filters=CardFilters(commander_legal=True))
        )
    result = trace.result
    assert [hit.oracle_id for hit in result.hits] == [COUNTER, GREEN, ROCK]
    assert set(trace.rankings) == {
        "lexical",
        "dense",
        "rrf",
        "reranked",
        "fused",
    }
    assert [stage.stage for stage in result.hits[0].stages] == [
        "lexical",
        "dense",
        "rrf",
        "reranked",
        "fused",
    ]
    assert result.provenance.bundle_id
    assert result.provenance.embedding_revision == settings.embedding.revision
    assert len(scorer.pairs) == 3


def test_structured_filters_bound_lexical_dense_and_reranking(tmp_path):
    settings = _build(tmp_path)
    scorer = KeywordScorer()
    with CardRetrievalFacade(
        settings,
        encoder=KeywordEncoder(),
        scorer=scorer,
        verify_model_files=False,
    ) as facade:
        result = facade.search(
            CardSearchQuery(
                text="counter",
                filters=CardFilters(
                    color_identity=("U",),
                    color_mode="subset",
                    required_types=("Instant",),
                ),
            )
        )
    assert [hit.oracle_id for hit in result.hits] == [COUNTER]
    assert result.eligible_cards == 1
    assert len(scorer.pairs) == 1
    assert "Green Counter" not in scorer.pairs[0][1]


def test_structured_only_search_is_explicit_and_uses_no_models(tmp_path):
    settings = _build(tmp_path)
    with CardRetrievalFacade(
        settings,
        encoder=KeywordEncoder(),
        scorer=MustNotScore(),
        verify_model_files=False,
    ) as facade:
        result = facade.search(
            CardSearchQuery(
                filters=CardFilters(required_types=("Artifact",)),
            )
        )
    assert [hit.oracle_id for hit in result.hits] == [ROCK]
    assert result.availability.lexical is False
    assert result.availability.dense is False
    assert result.availability.reranked is False
    assert result.availability.notices


def test_current_config_must_match_the_active_bundle(tmp_path):
    settings = _build(tmp_path)
    changed = settings.model_copy(
        update={
            "retrieval": settings.retrieval.model_copy(
                update={"rrf_k": settings.retrieval.rrf_k + 1}
            )
        }
    )
    with pytest.raises(RetrievalBundleMismatchError, match="configuration"):
        CardRetrievalFacade(
            changed,
            encoder=KeywordEncoder(),
            scorer=KeywordScorer(),
            verify_model_files=False,
        )
