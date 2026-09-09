"""Immutable bundle validation and activation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sabermetrics.substrate.artifacts import (
    BundleManifest,
    CorpusIdentity,
    FileIdentity,
    RetrievalArtifactError,
    TagIdentity,
    activate_bundle,
    file_identity,
    resolve_active_bundle,
    validate_bundle,
    write_manifest,
)

SHA = "a" * 64


def _manifest(files: dict[str, FileIdentity], *, built_at: str = "2026-09-09T00:00Z"):
    seed = BundleManifest(
        bundle_id="0" * 64,
        built_at=built_at,
        corpus=CorpusIdentity(
            source_view="mtg_v1.card_any_medium",
            row_count=1,
            content_sha256=SHA,
            captured_at=built_at,
        ),
        tags=TagIdentity(
            library_sha256="b" * 64,
            content_sha256="c" * 64,
            row_count=2,
        ),
        document_version="card-document.v1",
        retrieval_config_sha256="d" * 64,
        files=files,
    )
    return seed.model_copy(update={"bundle_id": seed.computed_bundle_id()})


def _bundle(root: Path, *, content: bytes = b"index") -> tuple[Path, BundleManifest]:
    scratch = root / "scratch"
    scratch.mkdir()
    data = scratch / "catalog.sqlite"
    data.write_bytes(content)
    manifest = _manifest({"catalog.sqlite": file_identity(data)})
    bundle = root / manifest.bundle_id
    scratch.rename(bundle)
    write_manifest(bundle / "manifest.json", manifest)
    return bundle, manifest


def test_activation_and_resolution_validate_the_whole_bundle(tmp_path):
    bundle, manifest = _bundle(tmp_path)
    activated = activate_bundle(tmp_path, manifest.bundle_id)
    active_dir, resolved = resolve_active_bundle(tmp_path)
    assert activated == resolved == manifest
    assert active_dir == bundle


def test_timestamps_do_not_change_content_identity():
    first = _manifest({}, built_at="2026-01-01T00:00Z")
    second = _manifest({}, built_at="2026-12-31T23:59Z")
    assert first.bundle_id == second.bundle_id


def test_corrupt_file_is_refused(tmp_path):
    bundle, manifest = _bundle(tmp_path)
    (bundle / "catalog.sqlite").write_bytes(b"tampered")
    with pytest.raises(RetrievalArtifactError, match="digest mismatch"):
        validate_bundle(bundle)
    with pytest.raises(RetrievalArtifactError, match="digest mismatch"):
        activate_bundle(tmp_path, manifest.bundle_id)


def test_absent_active_bundle_is_visible(tmp_path):
    with pytest.raises(RetrievalArtifactError, match="pointer missing"):
        resolve_active_bundle(tmp_path)


@pytest.mark.parametrize("value", ["../elsewhere", "main", "A" * 64, ""])
def test_active_pointer_cannot_escape_the_bundle_root(tmp_path, value):
    (tmp_path / "CURRENT").write_text(value, encoding="ascii")
    with pytest.raises(RetrievalArtifactError, match="malformed"):
        resolve_active_bundle(tmp_path)


def test_manifest_with_unsafe_file_path_is_refused(tmp_path):
    manifest = _manifest({"../outside": FileIdentity(sha256=SHA, bytes=0)})
    bundle = tmp_path / manifest.bundle_id
    bundle.mkdir()
    write_manifest(bundle / "manifest.json", manifest)
    with pytest.raises(RetrievalArtifactError, match="unsafe"):
        validate_bundle(bundle)


def test_manifest_semantic_tampering_is_refused(tmp_path):
    bundle, _ = _bundle(tmp_path)
    raw = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    raw["document_version"] = "card-document.v2"
    (bundle / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RetrievalArtifactError, match="content hash"):
        validate_bundle(bundle)
