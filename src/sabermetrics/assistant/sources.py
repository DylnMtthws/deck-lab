"""The boundary between the executor and the deterministic substrate.

Two protocols, so the executor is exercised in full with no local model file,
no network, and no reference index — and so every failure mode of the layers
beneath arrives as one typed absence rather than as five unrelated exception
types the executor would have to know about individually.

Import discipline, stated because the import graph cannot see it: this module
imports ``reference_layer.retriever`` only. ``reference_layer.evidence`` reaches
into ``sabermetrics.db``, ``analytics`` and ``ingestion``, which ADR-030 places
off-limits to ``assistant``; the package ``__init__`` is empty, so importing the
submodule directly does not pull it in.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from sabermetrics.assistant.envelope import (
    CorpusProvenance,
    RulesRow,
    StepNotRunReason,
)
from sabermetrics.reference_layer.retriever import (
    ReferenceGenerationAbsentError,
    ReferenceGenerationCorruptError,
    ReferenceGenerationIncompleteError,
    ReferenceGenerationMismatchError,
    ReferenceGenerationUnlabelledError,
    ReferenceQuery,
    ReferenceRetrievalError,
    ReferenceRetriever,
)
from sabermetrics.substrate.artifacts import RetrievalArtifactError
from sabermetrics.substrate.bundle import BundleBuildError
from sabermetrics.substrate.catalog import CatalogNotFoundError, CatalogRecord
from sabermetrics.substrate.dense import DenseRetrievalError
from sabermetrics.substrate.models import CardFilters, CardSearchQuery
from sabermetrics.substrate.reranker import RerankerError
from sabermetrics.substrate.retrieval import (
    CardRetrievalFacade,
    RetrievalBundleMismatchError,
    RetrievalTrace,
)
from sabermetrics.substrate.settings import ResearchSettings


class SourceUnavailable(RuntimeError):
    """A substrate layer could not serve a step, with a typed reason."""

    def __init__(self, reason: StepNotRunReason, detail: str) -> None:
        """Record the closed-set reason alongside the human detail.

        Args:
            reason: The value the step envelope will carry.
            detail: A specific, non-generic explanation.
        """
        super().__init__(detail)
        self.reason: StepNotRunReason = reason
        self.detail = detail


class CardSourceUnavailable(SourceUnavailable):
    """The card retrieval bundle could not serve a step."""


class RulesUnavailable(SourceUnavailable):
    """The reference layer could not serve a step."""


_CARD_ERROR_REASONS: tuple[tuple[type[Exception], StepNotRunReason], ...] = (
    (RetrievalBundleMismatchError, "retrieval_config_mismatch"),
    (RetrievalArtifactError, "retrieval_bundle_unavailable"),
    (CatalogNotFoundError, "retrieval_bundle_unavailable"),
    (BundleBuildError, "local_model_unavailable"),
    (DenseRetrievalError, "local_model_unavailable"),
    (RerankerError, "local_model_unavailable"),
)

_RULES_ERROR_REASONS: tuple[tuple[type[Exception], StepNotRunReason], ...] = (
    (ReferenceGenerationAbsentError, "reference_index_absent"),
    (ReferenceGenerationUnlabelledError, "reference_index_unlabelled"),
    (ReferenceGenerationIncompleteError, "reference_index_stale"),
    (ReferenceGenerationMismatchError, "reference_index_stale"),
    (ReferenceGenerationCorruptError, "reference_index_stale"),
)


def _reason_for(
    exc: Exception, table: tuple[tuple[type[Exception], StepNotRunReason], ...]
) -> StepNotRunReason | None:
    """Return the typed reason for an exception, if the table names one."""
    for error_type, reason in table:
        if isinstance(exc, error_type):
            return reason
    return None


@runtime_checkable
class CardSource(Protocol):
    """Everything the executor may ask of the card retrieval substrate."""

    @property
    def result_limit(self) -> int:
        """Return the configured ceiling on ranked results per query."""

    def provenance(self) -> CorpusProvenance:
        """Return the identity of the bundle every result came from."""

    def records(
        self, filters: CardFilters | None = None, *, limit: int | None = None
    ) -> tuple[CatalogRecord, ...]:
        """Return the structured eligible population in Oracle-id order."""

    def search_with_trace(self, query: CardSearchQuery) -> RetrievalTrace:
        """Run one ranked search and return its complete stage rankings."""

    def resolve_names(self, names: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """Map exact card names to the Oracle ids that carry them."""


class BundleCardSource:
    """A :class:`CardSource` backed by one open retrieval facade."""

    def __init__(self, facade: CardRetrievalFacade) -> None:
        """Wrap an already opened and validated facade.

        Args:
            facade: The open facade over the active bundle.
        """
        self._facade = facade
        self._provenance = CorpusProvenance.from_manifest(facade.manifest)

    @property
    def result_limit(self) -> int:
        """Return the configured ceiling on ranked results per query."""
        return self._facade.result_limit

    def provenance(self) -> CorpusProvenance:
        """Return the identity of the bundle every result came from."""
        return self._provenance

    def records(
        self, filters: CardFilters | None = None, *, limit: int | None = None
    ) -> tuple[CatalogRecord, ...]:
        """Return the structured eligible population in Oracle-id order.

        Args:
            filters: Predicates pushed into SQL.
            limit: Optional maximum row count.

        Returns:
            Matching catalog records.

        Raises:
            CardSourceUnavailable: If the bundle cannot serve the query.
        """
        try:
            return self._facade.records(filters, limit=limit)
        except Exception as exc:
            reason = _reason_for(exc, _CARD_ERROR_REASONS)
            if reason is None:
                raise
            raise CardSourceUnavailable(reason, str(exc)) from exc

    def search_with_trace(self, query: CardSearchQuery) -> RetrievalTrace:
        """Run one ranked search and return its complete stage rankings.

        Args:
            query: The bounded search to run.

        Returns:
            The result plus every stage ranking.

        Raises:
            CardSourceUnavailable: If a ranking stage cannot run.
        """
        try:
            return self._facade.search_with_trace(query)
        except Exception as exc:
            reason = _reason_for(exc, _CARD_ERROR_REASONS)
            if reason is None:
                raise
            raise CardSourceUnavailable(reason, str(exc)) from exc

    def resolve_names(self, names: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """Map exact card names to the Oracle ids that carry them.

        Args:
            names: Card names, as a curated list writes them.

        Returns:
            One entry per matched name, mapping to every Oracle id found.

        Raises:
            CardSourceUnavailable: If the bundle cannot serve the lookup.
        """
        try:
            return self._facade.resolve_names(names)
        except Exception as exc:
            reason = _reason_for(exc, _CARD_ERROR_REASONS)
            if reason is None:
                raise
            raise CardSourceUnavailable(reason, str(exc)) from exc


def open_card_source(settings: ResearchSettings) -> BundleCardSource:
    """Open the active bundle and wrap it as a card source.

    Args:
        settings: Validated retrieval configuration.

    Returns:
        A source over the validated active bundle.

    Raises:
        CardSourceUnavailable: If the bundle or the pinned models are absent,
            stale, or configured for a different retrieval contract.
    """
    try:
        return BundleCardSource(CardRetrievalFacade(settings))
    except Exception as exc:
        reason = _reason_for(exc, _CARD_ERROR_REASONS)
        if reason is None:
            raise
        raise CardSourceUnavailable(reason, str(exc)) from exc


@runtime_checkable
class RulesSource(Protocol):
    """Everything the executor may ask of the reference layer."""

    def lookup(
        self,
        question: str,
        *,
        top_k: int,
        tier_filter: Sequence[int] = (),
        document_filter: Sequence[str] = (),
    ) -> tuple[RulesRow, ...]:
        """Return ranked reference chunks for one question."""


class ReferenceRulesSource:
    """A :class:`RulesSource` over one validated active generation."""

    def __init__(self, retriever: ReferenceRetriever) -> None:
        """Wrap a strict active-generation reader.

        Args:
            retriever: A retriever bound to the application database.
        """
        self._retriever = retriever

    @classmethod
    def open(cls, db_path: Path) -> ReferenceRulesSource:
        """Open a rules source over the database at ``db_path``.

        Args:
            db_path: The application SQLite database.

        Returns:
            A rules source. Whether a generation is actually active is not
            known until the first lookup, which is where the typed absence is
            raised.
        """
        return cls(ReferenceRetriever(db_path))

    def lookup(
        self,
        question: str,
        *,
        top_k: int,
        tier_filter: Sequence[int] = (),
        document_filter: Sequence[str] = (),
    ) -> tuple[RulesRow, ...]:
        """Return ranked reference chunks for one question.

        Args:
            question: The rules question, in plain language.
            top_k: Maximum chunks to return.
            tier_filter: Optional reference tiers to restrict to.
            document_filter: Optional source documents to restrict to.

        Returns:
            Ranked chunks, each carrying a citation.

        Raises:
            RulesUnavailable: If no complete labelled generation is active.
        """
        query = ReferenceQuery(
            query_text=question,
            tier_filter=list(tier_filter) or None,
            document_filter=list(document_filter) or None,
            top_k=top_k,
        )
        try:
            chunks = self._retriever.retrieve(query)
        except ReferenceRetrievalError as exc:
            raise RulesUnavailable(_rules_reason(exc), str(exc)) from exc
        return tuple(
            RulesRow(
                citation=_citation(chunk.document, chunk.section, rank),
                document=chunk.document,
                section=chunk.section,
                tier=chunk.tier,
                content=chunk.content,
                similarity=chunk.similarity_score,
                rank=rank,
            )
            for rank, chunk in enumerate(chunks, start=1)
        )


class AbsentRulesSource:
    """A :class:`RulesSource` for a deployment with no reference index.

    R3 does not build the Comprehensive Rules corpus. Standing this in for the
    real source keeps ``rules_lookup`` a real step that returns a real, typed
    absence, rather than a step that quietly returns nothing.
    """

    reason: StepNotRunReason = "reference_index_absent"
    detail = (
        "no active reference embedding generation; the Comprehensive Rules "
        "corpus has not been encoded under the pinned embedding model"
    )

    def lookup(
        self,
        question: str,
        *,
        top_k: int,
        tier_filter: Sequence[int] = (),
        document_filter: Sequence[str] = (),
    ) -> tuple[RulesRow, ...]:
        """Refuse: there is no active generation to query.

        Args:
            question: Ignored.
            top_k: Ignored.
            tier_filter: Ignored.
            document_filter: Ignored.

        Raises:
            RulesUnavailable: Always.
        """
        del question, top_k, tier_filter, document_filter
        raise RulesUnavailable(self.reason, self.detail)


def _rules_reason(exc: ReferenceRetrievalError) -> StepNotRunReason:
    """Map a reference-layer error to its typed step reason."""
    return _reason_for(exc, _RULES_ERROR_REASONS) or "rules_source_unavailable"


def _citation(document: str, section: str | None, rank: int) -> str:
    """Build a Fact-tier citation for one reference chunk.

    The chunk id is deliberately not used: chunk ids are random UUIDs and are
    not reproducible across a re-chunk, so a citation built from one would stop
    resolving the next time the corpus is rebuilt.

    Args:
        document: The source document label.
        section: The rules section, when the chunker identified one.
        rank: The chunk's rank within this lookup.

    Returns:
        A stable citation string.
    """
    if section:
        return f"rules:{section}"
    return f"rules:{document}#{rank}"
