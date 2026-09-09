"""Build one content-addressed retrieval bundle from one frozen corpus export."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sabermetrics.mechanics.tags.registry import ALL_TAGS
from sabermetrics.substrate import tagging
from sabermetrics.substrate.artifacts import (
    BundleManifest,
    CorpusIdentity,
    ModelIdentity,
    RetrievalArtifactError,
    TagIdentity,
    activate_bundle,
    file_identity,
    validate_bundle,
    write_manifest,
)
from sabermetrics.substrate.catalog import build_catalog, open_catalog
from sabermetrics.substrate.corpus import (
    CardView,
    CorpusExport,
    CorpusSource,
    PostgresCorpusSource,
    SnapshotIdentity,
    corpus_content_sha256,
)
from sabermetrics.substrate.dense import (
    DenseEncoder,
    LocalSentenceTransformerEncoder,
    build_dense_index,
)
from sabermetrics.substrate.settings import ResearchSettings

CATALOG_FILE = "catalog.sqlite"
VECTOR_FILE = "card-vectors.npy"
DOCUMENT_VERSION = "card-document.v2"
MODEL_REVISION_FILE = "REVISION"


class BundleBuildError(RuntimeError):
    """A complete retrieval bundle could not be built or activated."""


@dataclass(frozen=True)
class _FrozenSource:
    export: CorpusExport

    def identity(self) -> SnapshotIdentity:
        identity = self.export.identity
        return SnapshotIdentity(
            source_view=identity.source_view,
            row_count=identity.row_count,
            max_content_updated_at=identity.max_content_updated_at,
            captured_at=None,
        )

    def iter_cards(self) -> Iterator[CardView]:
        yield from self.export.cards


def materialize_corpus(source: CorpusSource) -> CorpusExport:
    """Read a corpus source once into the immutable R2 export contract.

    The production source supplies its own transactionally atomic export.
    Materialized JSON and in-memory sources are already fixed observations and
    are canonicalized here.
    """
    if isinstance(source, PostgresCorpusSource):
        return source.export()
    identity = source.identity()
    cards: list[CardView] = []
    for card in source.iter_cards():
        if not isinstance(card, CardView):
            raise BundleBuildError(
                "retrieval bundles require substrate CardView rows with legality"
            )
        cards.append(card)
    ordered = tuple(sorted(cards, key=lambda card: (card.oracle_id, card.name)))
    if identity.row_count is not None and identity.row_count != len(ordered):
        raise BundleBuildError(
            f"corpus identity reports {identity.row_count} rows but yielded "
            f"{len(ordered)}"
        )
    return CorpusExport(
        identity=identity,
        cards=ordered,
        content_sha256=corpus_content_sha256(ordered),
    )


def verify_local_model_revision(local_dir: Path, revision: str, label: str) -> None:
    """Require a local model snapshot to attest its immutable revision."""
    resolved = local_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise BundleBuildError(f"{label} model directory missing: {resolved}")
    marker = resolved / MODEL_REVISION_FILE
    recorded = marker.read_text(encoding="ascii").strip() if marker.is_file() else ""
    if resolved.name != revision and recorded != revision:
        raise BundleBuildError(
            f"{label} model at {resolved} does not attest configured revision "
            f"{revision}; expected directory name or {MODEL_REVISION_FILE}"
        )


def build_bundle(
    export: CorpusExport,
    settings: ResearchSettings,
    *,
    encoder: DenseEncoder | None = None,
    built_at: str | None = None,
    verify_model_files: bool = True,
    activate: bool = True,
) -> BundleManifest:
    """Build, validate, install, and optionally activate one retrieval bundle.

    Args:
        export: One already-frozen corpus observation.
        settings: Strict retrieval and artifact configuration.
        encoder: Optional protocol implementation for portable tests.
        built_at: Injectable UTC timestamp for deterministic tests.
        verify_model_files: Require local model revision attestations. This must
            remain true outside protocol-based tests.
        activate: Atomically update ``CURRENT`` after bundle validation.

    Returns:
        The validated immutable manifest.

    Raises:
        BundleBuildError: If any build, validation, installation, or activation
            step fails.
    """
    root = settings.artifacts.root.expanduser()
    if export.identity.row_count is not None and export.identity.row_count != len(
        export.cards
    ):
        raise BundleBuildError("frozen corpus row count does not match its identity")
    actual_corpus_hash = corpus_content_sha256(export.cards)
    if export.content_sha256 != actual_corpus_hash:
        raise BundleBuildError("frozen corpus content hash does not match its cards")
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

    root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".build.", dir=root))
    installed: Path | None = None
    try:
        tag_build = tagging.build(_FrozenSource(export), ALL_TAGS)
        catalog_path = scratch / CATALOG_FILE
        build_catalog(catalog_path, export.cards, tag_build)
        with open_catalog(catalog_path) as catalog:
            records = catalog.records()
        oracle_ids = tuple(record.oracle_id for record in records)
        documents = tuple(record.canonical_document for record in records)
        if oracle_ids != tuple(sorted(card.oracle_id for card in export.cards)):
            raise BundleBuildError("catalog vector offsets do not match the corpus")

        active_encoder = encoder or LocalSentenceTransformerEncoder(settings.embedding)
        vector_path = scratch / VECTOR_FILE
        build_dense_index(
            vector_path,
            documents,
            encoder=active_encoder,
            settings=settings.embedding,
        )
        files = {
            CATALOG_FILE: file_identity(catalog_path),
            VECTOR_FILE: file_identity(vector_path),
        }
        stamp = built_at or datetime.now(UTC).isoformat()
        seed = BundleManifest(
            bundle_id="0" * 64,
            built_at=stamp,
            corpus=CorpusIdentity(
                source_view=export.identity.source_view,
                row_count=len(export.cards),
                content_sha256=export.content_sha256,
                max_content_updated_at=export.identity.max_content_updated_at,
                captured_at=export.identity.captured_at,
            ),
            tags=TagIdentity(
                library_sha256=tag_build.library_sha256,
                content_sha256=tag_build.content_sha256,
                row_count=len(tag_build.rows),
            ),
            document_version=DOCUMENT_VERSION,
            retrieval_config_sha256=settings.retrieval_sha256(),
            embedding=ModelIdentity(
                model_id=settings.embedding.model_id,
                revision=settings.embedding.revision,
                dimensions=settings.embedding.dimensions,
                dtype="float32",
                normalized=settings.embedding.normalize,
            ),
            reranker=ModelIdentity(
                model_id=settings.reranker.model_id,
                revision=settings.reranker.revision,
            ),
            files=files,
        )
        manifest = seed.model_copy(update={"bundle_id": seed.computed_bundle_id()})
        write_manifest(scratch / "manifest.json", manifest)

        destination = root / manifest.bundle_id
        if destination.exists():
            existing = validate_bundle(destination)
            shutil.rmtree(scratch)
            manifest = existing
        else:
            os.rename(scratch, destination)
            installed = destination
            validate_bundle(destination)
        if activate:
            activate_bundle(root, manifest.bundle_id)
        return manifest
    except Exception as exc:
        if scratch.exists():
            shutil.rmtree(scratch)
        if installed is not None and installed.exists():
            shutil.rmtree(installed)
        if isinstance(exc, BundleBuildError):
            raise
        if isinstance(exc, RetrievalArtifactError):
            raise BundleBuildError(str(exc)) from exc
        raise BundleBuildError(f"retrieval bundle build failed: {exc}") from exc


def build_from_source(
    source: CorpusSource,
    settings: ResearchSettings,
    *,
    encoder: DenseEncoder | None = None,
    built_at: str | None = None,
    verify_model_files: bool = True,
    activate: bool = True,
) -> BundleManifest:
    """Materialize one source observation and pass it to :func:`build_bundle`."""
    return build_bundle(
        materialize_corpus(source),
        settings,
        encoder=encoder,
        built_at=built_at,
        verify_model_files=verify_model_files,
        activate=activate,
    )
