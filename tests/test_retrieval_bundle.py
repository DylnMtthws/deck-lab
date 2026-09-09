"""A retrieval bundle is complete, content-addressed, and atomically active."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from sabermetrics.substrate.artifacts import resolve_active_bundle
from sabermetrics.substrate.bundle import (
    BundleBuildError,
    build_bundle,
    verify_local_model_revision,
)
from sabermetrics.substrate.catalog import open_catalog
from sabermetrics.substrate.corpus import (
    CardView,
    CorpusExport,
    SnapshotIdentity,
    corpus_content_sha256,
)
from sabermetrics.substrate.settings import load_research_settings

CARD_A = "00000000-0000-0000-0000-000000000001"
CARD_B = "00000000-0000-0000-0000-000000000002"


class FakeEncoder:
    """Deterministic encoder sized to the supplied documents."""

    dimensions = 2

    def encode(self, texts: Sequence[str], *, batch_size: int) -> NDArray[np.float32]:
        del batch_size
        return np.asarray(
            [[float(index + 1), 1.0] for index, _ in enumerate(texts)],
            dtype=np.float32,
        )


class BrokenEncoder:
    dimensions = 2

    def encode(self, texts: Sequence[str], *, batch_size: int) -> NDArray[np.float32]:
        del texts, batch_size
        raise RuntimeError("encoder broke")


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
        }
    )


def _export(*, changed: bool = False, captured_at: str = "first") -> CorpusExport:
    cards = (
        CardView(
            oracle_id=CARD_B,
            name="Mana Stone",
            mana_value=2,
            type_line="Artifact",
            oracle_text="Add two mana.",
            color_identity=(),
            all_types=("Artifact",),
            commander_legal="legal",
        ),
        CardView(
            oracle_id=CARD_A,
            name="Free Answer",
            mana_value=0,
            type_line="Instant",
            oracle_text="Counter target spell." + (" Changed." if changed else ""),
            color_identity=("U",),
            all_types=("Instant",),
            commander_legal="legal",
        ),
    )
    return CorpusExport(
        identity=SnapshotIdentity(
            source_view="fixture:mtg_v1.card_any_medium",
            row_count=2,
            max_content_updated_at="2026-09-09",
            captured_at=captured_at,
        ),
        cards=cards,
        content_sha256=corpus_content_sha256(cards),
    )


def test_build_installs_validates_and_activates_a_complete_bundle(tmp_path):
    settings = _settings(tmp_path)
    manifest = build_bundle(
        _export(),
        settings,
        encoder=FakeEncoder(),
        verify_model_files=False,
        built_at="2026-09-09T00:00:00Z",
    )
    bundle_dir, active = resolve_active_bundle(settings.artifacts.root)
    assert active == manifest
    assert set(manifest.files) == {"catalog.sqlite", "card-vectors.npy"}
    assert (bundle_dir / "manifest.json").is_file()
    assert isinstance(
        np.load(bundle_dir / "card-vectors.npy", mmap_mode="r"), np.memmap
    )
    with open_catalog(bundle_dir / "catalog.sqlite") as catalog:
        assert [row.oracle_id for row in catalog.records()] == [CARD_A, CARD_B]


def test_capture_and_build_times_do_not_change_bundle_identity(tmp_path):
    settings = _settings(tmp_path)
    first = build_bundle(
        _export(captured_at="first"),
        settings,
        encoder=FakeEncoder(),
        verify_model_files=False,
        built_at="first",
    )
    second = build_bundle(
        _export(captured_at="second"),
        settings,
        encoder=FakeEncoder(),
        verify_model_files=False,
        built_at="second",
    )
    assert first.bundle_id == second.bundle_id
    assert first.built_at == "first"
    assert second == first


def test_failed_rebuild_leaves_the_previous_bundle_active(tmp_path):
    settings = _settings(tmp_path)
    first = build_bundle(
        _export(),
        settings,
        encoder=FakeEncoder(),
        verify_model_files=False,
    )
    with pytest.raises(BundleBuildError, match="encoder broke"):
        build_bundle(
            _export(changed=True),
            settings,
            encoder=BrokenEncoder(),
            verify_model_files=False,
        )
    _, active = resolve_active_bundle(settings.artifacts.root)
    assert active.bundle_id == first.bundle_id


def test_tampered_export_hash_is_refused_before_writing(tmp_path):
    settings = _settings(tmp_path)
    export = _export()
    tampered = CorpusExport(
        identity=export.identity,
        cards=export.cards,
        content_sha256="0" * 64,
    )
    with pytest.raises(BundleBuildError, match="content hash"):
        build_bundle(
            tampered,
            settings,
            encoder=FakeEncoder(),
            verify_model_files=False,
        )
    assert not settings.artifacts.root.exists()


def test_local_model_revision_requires_an_attestation(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    with pytest.raises(BundleBuildError, match="does not attest"):
        verify_local_model_revision(model, "a" * 40, "embedding")
    (model / "REVISION").write_text("a" * 40 + "\n", encoding="ascii")
    verify_local_model_revision(model, "a" * 40, "embedding")
