"""End-to-end Oracle-ID card retrieval over one validated active bundle."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from sabermetrics.substrate.artifacts import BundleManifest, resolve_active_bundle
from sabermetrics.substrate.bundle import (
    CATALOG_FILE,
    DOCUMENT_VERSION,
    VECTOR_FILE,
    verify_local_model_revision,
)
from sabermetrics.substrate.catalog import (
    Bm25Weights,
    CatalogRecord,
    open_catalog,
)
from sabermetrics.substrate.dense import (
    DenseEncoder,
    DenseIndex,
    LocalSentenceTransformerEncoder,
)
from sabermetrics.substrate.fusion import (
    RankedHit,
    weighted_reciprocal_rank_fusion,
)
from sabermetrics.substrate.models import (
    CardFilters,
    CardSearchQuery,
    CardSearchResult,
    IndexProvenance,
    RetrievalAvailability,
    RetrievalHit,
    StageEvidence,
)
from sabermetrics.substrate.reranker import (
    LocalCrossEncoderScorer,
    PairScorer,
    RerankCandidate,
    rerank_candidates,
)
from sabermetrics.substrate.settings import ResearchSettings


class RetrievalBundleMismatchError(RuntimeError):
    """The active bundle does not match the configured retrieval contract."""


@dataclass(frozen=True)
class RetrievalTrace:
    """A search result plus complete stage rankings for evaluation."""

    result: CardSearchResult
    rankings: dict[str, tuple[str, ...]]
    truncated: dict[str, bool]
    elapsed_ms: float


class CardRetrievalFacade:
    """Run structured, lexical, dense, RRF, and reranking stages in order."""

    def __init__(
        self,
        settings: ResearchSettings,
        *,
        encoder: DenseEncoder | None = None,
        scorer: PairScorer | None = None,
        verify_model_files: bool = True,
    ) -> None:
        """Open and validate the active bundle and local ranking models.

        Args:
            settings: Current strict retrieval configuration.
            encoder: Optional protocol encoder for portable tests.
            scorer: Optional protocol pair scorer for portable tests.
            verify_model_files: Require local revision attestations. This may be
                false only when both model protocols are injected by tests.
        """
        bundle_dir, manifest = resolve_active_bundle(settings.artifacts.root)
        _validate_manifest(manifest, settings)
        if verify_model_files:
            verify_local_model_revision(
                settings.embedding.local_dir,
                settings.embedding.revision,
                "embedding",
            )
            verify_local_model_revision(
                settings.reranker.local_dir,
                settings.reranker.revision,
                "reranker",
            )
        self._settings = settings
        self._bundle_dir = bundle_dir
        self._manifest = manifest
        self._catalog = open_catalog(bundle_dir / CATALOG_FILE)
        records = self._catalog.records()
        oracle_ids = tuple(record.oracle_id for record in records)
        self._dense = DenseIndex(
            bundle_dir / VECTOR_FILE,
            oracle_ids,
            encoder=encoder or LocalSentenceTransformerEncoder(settings.embedding),
            settings=settings.embedding,
        )
        self._scorer = scorer or LocalCrossEncoderScorer(
            settings.reranker.local_dir,
            max_length=settings.reranker.max_length,
            device=settings.reranker.device,
        )

    def __enter__(self) -> Self:
        """Return this open retrieval facade."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the read-only catalog."""
        self.close()

    def close(self) -> None:
        """Close the underlying catalog handle."""
        self._catalog.close()

    @property
    def manifest(self) -> BundleManifest:
        """Return the validated active bundle provenance."""
        return self._manifest

    @property
    def oracle_ids(self) -> tuple[str, ...]:
        """Return every indexed Oracle ID in canonical vector order."""
        return self._dense.oracle_ids

    @property
    def result_limit(self) -> int:
        """Return the configured ceiling on ranked results per query.

        A caller asking for more than this does not receive more; exposing the
        true ceiling lets a caller refuse rather than silently under-return.
        """
        return self._settings.retrieval.result_limit

    def records(
        self,
        filters: CardFilters | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[CatalogRecord, ...]:
        """Return the structured eligible population in Oracle-ID order.

        :meth:`search` is a ranked top-k and caps at ``result_limit``. A caller
        that needs the whole eligible set — a mechanic-tag filter, or a deck
        whose card count exceeds that cap — must not read a capped ranking as a
        population, so the unbounded structured path is exposed separately.

        Args:
            filters: Predicates pushed into SQL. Defaults to no constraints.
            limit: Optional maximum row count. ``None`` returns every match.

        Returns:
            Matching catalog records ordered by ``oracle_id``.
        """
        return self._catalog.records(filters, limit=limit)

    def resolve_names(self, names: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """Map exact card names to the Oracle ids that carry them.

        Args:
            names: Card names, as a curated list writes them.

        Returns:
            One entry per matched name, mapping to every Oracle id found.
        """
        return self._catalog.resolve_names(names)

    def search(self, query: CardSearchQuery) -> CardSearchResult:
        """Run one search and return only its bounded player-facing result."""
        return self.search_with_trace(query).result

    def search_with_trace(self, query: CardSearchQuery) -> RetrievalTrace:
        """Run one bounded deterministic card search.

        Structured-only searches remain useful and do not fabricate model
        rankings: they return Oracle-ID ordered eligible rows with an explicit
        availability notice.
        """
        started = time.perf_counter()
        eligible = self._catalog.records(query.filters)
        records = {record.oracle_id: record for record in eligible}
        if not query.text.strip():
            limit = min(query.top_k, self._settings.retrieval.result_limit)
            structured_hits = tuple(
                self._structured_hit(record) for record in eligible[:limit]
            )
            result = CardSearchResult(
                query=query,
                hits=structured_hits,
                eligible_cards=len(eligible),
                fused_truncated=len(eligible) > limit,
                provenance=self._provenance(),
                availability=RetrievalAvailability(
                    lexical=False,
                    dense=False,
                    reranked=False,
                    notices=("structured-only query; ranked stages did not run",),
                ),
            )
            return RetrievalTrace(
                result=result,
                rankings={"structured": tuple(record.oracle_id for record in eligible)},
                truncated={"structured": len(eligible) > limit},
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        lexical_bound = self._settings.retrieval.lexical_pool
        bm25 = self._settings.retrieval.bm25_weights
        lexical_raw = self._catalog.search(
            query.text,
            query.filters,
            limit=lexical_bound + 1,
            weights=Bm25Weights(
                name=bm25["name"],
                type_line=bm25["type_line"],
                oracle_text=bm25["oracle_text"],
            ),
        )
        lexical_truncated = len(lexical_raw) > lexical_bound
        lexical = lexical_raw[:lexical_bound]

        dense_bound = self._settings.retrieval.dense_pool
        dense = self._dense.search(
            query.text,
            candidate_oracle_ids=records.keys(),
            top_k=dense_bound,
        )
        dense_truncated = len(eligible) > dense_bound

        fused = weighted_reciprocal_rank_fusion(
            {
                "lexical": tuple(
                    RankedHit(hit.oracle_id, hit.bm25_score) for hit in lexical
                ),
                "dense": tuple(RankedHit(hit.oracle_id, hit.score) for hit in dense),
            },
            self._settings.retrieval.weights,
            rank_constant=float(self._settings.retrieval.rrf_k),
        )
        rerank_pool = tuple(
            RerankCandidate(
                oracle_id=hit.oracle_id,
                document=records[hit.oracle_id].canonical_document,
                fused_score=hit.fused_score,
            )
            for hit in fused[: self._settings.retrieval.rerank_pool]
        )
        reranked = rerank_candidates(
            query.text,
            rerank_pool,
            self._scorer,
            pool_limit=self._settings.retrieval.rerank_pool,
            batch_size=self._settings.reranker.batch_size,
        )
        final_fused = weighted_reciprocal_rank_fusion(
            {
                "rrf": tuple(
                    RankedHit(hit.oracle_id, hit.fused_score)
                    for hit in fused[: self._settings.retrieval.rerank_pool]
                ),
                "reranker": tuple(
                    RankedHit(hit.oracle_id, hit.reranker_score) for hit in reranked
                ),
            },
            self._settings.retrieval.final_weights,
            rank_constant=float(self._settings.retrieval.rrf_k),
        )
        result_limit = min(query.top_k, self._settings.retrieval.result_limit)
        selected = final_fused[:result_limit]

        lexical_rank = {
            hit.oracle_id: (rank, hit.bm25_score)
            for rank, hit in enumerate(lexical, start=1)
        }
        dense_rank = {
            hit.oracle_id: (rank, hit.score) for rank, hit in enumerate(dense, start=1)
        }
        rrf_rank = {
            hit.oracle_id: (rank, hit.fused_score)
            for rank, hit in enumerate(fused, start=1)
        }
        reranked_rank = {
            hit.oracle_id: (rank, hit.reranker_score)
            for rank, hit in enumerate(reranked, start=1)
        }
        final_hits: list[RetrievalHit] = []
        for final_rank, candidate in enumerate(selected, start=1):
            record = records[candidate.oracle_id]
            stages: list[StageEvidence] = []
            if candidate.oracle_id in lexical_rank:
                rank, score = lexical_rank[candidate.oracle_id]
                stages.append(StageEvidence(stage="lexical", rank=rank, score=score))
            if candidate.oracle_id in dense_rank:
                rank, score = dense_rank[candidate.oracle_id]
                stages.append(StageEvidence(stage="dense", rank=rank, score=score))
            rank, score = rrf_rank[candidate.oracle_id]
            stages.append(StageEvidence(stage="rrf", rank=rank, score=score))
            rank, score = reranked_rank[candidate.oracle_id]
            stages.append(StageEvidence(stage="reranked", rank=rank, score=score))
            stages.append(
                StageEvidence(
                    stage="fused",
                    rank=final_rank,
                    score=candidate.fused_score,
                )
            )
            final_hits.append(_retrieval_hit(record, tuple(stages)))

        result = CardSearchResult(
            query=query,
            hits=tuple(final_hits),
            eligible_cards=len(eligible),
            lexical_truncated=lexical_truncated,
            dense_truncated=dense_truncated,
            fused_truncated=len(final_fused) > result_limit
            or len(fused) > self._settings.retrieval.rerank_pool,
            provenance=self._provenance(),
            availability=RetrievalAvailability(
                lexical=True,
                dense=True,
                reranked=True,
            ),
        )
        return RetrievalTrace(
            result=result,
            rankings={
                "lexical": tuple(hit.oracle_id for hit in lexical),
                "dense": tuple(hit.oracle_id for hit in dense),
                "rrf": tuple(hit.oracle_id for hit in fused),
                "reranked": tuple(hit.oracle_id for hit in reranked),
                "fused": tuple(hit.oracle_id for hit in final_fused),
            },
            truncated={
                "lexical": lexical_truncated,
                "dense": dense_truncated,
                "rrf": len(fused) > self._settings.retrieval.rerank_pool,
                "reranked": len(reranked) > result_limit,
                "fused": len(final_fused) > result_limit
                or len(fused) > self._settings.retrieval.rerank_pool,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _structured_hit(self, record: CatalogRecord) -> RetrievalHit:
        return _retrieval_hit(record, ())

    def _provenance(self) -> IndexProvenance:
        embedding = self._manifest.embedding
        reranker = self._manifest.reranker
        assert embedding is not None
        assert reranker is not None
        return IndexProvenance(
            bundle_id=self._manifest.bundle_id,
            corpus_sha256=self._manifest.corpus.content_sha256,
            tag_library_sha256=self._manifest.tags.library_sha256,
            retrieval_config_sha256=self._manifest.retrieval_config_sha256,
            document_version=self._manifest.document_version,
            embedding_model_id=embedding.model_id,
            embedding_revision=embedding.revision,
            reranker_model_id=reranker.model_id,
            reranker_revision=reranker.revision,
        )


def _retrieval_hit(
    record: CatalogRecord, stages: tuple[StageEvidence, ...]
) -> RetrievalHit:
    return RetrievalHit(
        oracle_id=record.oracle_id,
        name=record.name,
        mana_cost=record.mana_cost,
        mana_value=record.mana_value,
        type_line=record.type_line,
        oracle_text=record.oracle_text or "",
        color_identity=record.color_identity,  # type: ignore[arg-type]
        matched_tags=record.tags,
        stages=stages,
    )


def _validate_manifest(manifest: BundleManifest, settings: ResearchSettings) -> None:
    if manifest.retrieval_config_sha256 != settings.retrieval_sha256():
        raise RetrievalBundleMismatchError(
            "active bundle retrieval configuration does not match current config"
        )
    if manifest.document_version != DOCUMENT_VERSION:
        raise RetrievalBundleMismatchError(
            "active bundle document version is unsupported"
        )
    embedding = manifest.embedding
    if (
        embedding is None
        or embedding.model_id != settings.embedding.model_id
        or embedding.revision != settings.embedding.revision
        or embedding.dimensions != settings.embedding.dimensions
        or embedding.dtype != "float32"
        or embedding.normalized is not True
    ):
        raise RetrievalBundleMismatchError(
            "active bundle embedding identity does not match current config"
        )
    reranker = manifest.reranker
    if (
        reranker is None
        or reranker.model_id != settings.reranker.model_id
        or reranker.revision != settings.reranker.revision
    ):
        raise RetrievalBundleMismatchError(
            "active bundle reranker identity does not match current config"
        )
    required_files = {CATALOG_FILE, VECTOR_FILE}
    if not required_files <= manifest.files.keys():
        raise RetrievalBundleMismatchError(
            "active bundle does not contain catalog and vector artifacts"
        )
