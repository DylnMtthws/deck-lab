"""Immutable retrieval bundles and atomic activation.

Index files are generated artifacts rather than app state.  A request opens
only the bundle named by ``CURRENT`` after its manifest and every file digest
have validated; a half-written build is therefore unavailable, never current.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

BUNDLE_SCHEMA = "research-index-bundle.v1"
CURRENT_FILE = "CURRENT"
MANIFEST_FILE = "manifest.json"
_BUNDLE_ID = re.compile(r"^[0-9a-f]{64}$")


class RetrievalArtifactError(RuntimeError):
    """A retrieval bundle is absent, malformed, stale, or corrupt."""


class FileIdentity(BaseModel):
    """Digest and byte length for one immutable bundle file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class CorpusIdentity(BaseModel):
    """The frozen card snapshot from which a bundle was built."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_view: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_content_updated_at: str | None = None
    captured_at: str | None = None


class TagIdentity(BaseModel):
    """The exact mechanic-tag build stored in the catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    library_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0)


class ModelIdentity(BaseModel):
    """Pinned model identity and encoding shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dimensions: int | None = Field(default=None, gt=0)
    dtype: str | None = None
    normalized: bool | None = None


class BundleManifest(BaseModel):
    """Complete provenance for one immutable card retrieval bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = BUNDLE_SCHEMA
    bundle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    built_at: str
    corpus: CorpusIdentity
    tags: TagIdentity
    document_version: str = Field(min_length=1)
    retrieval_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding: ModelIdentity | None = None
    reranker: ModelIdentity | None = None
    files: dict[str, FileIdentity]

    def computed_bundle_id(self) -> str:
        """Compute content identity without timestamps or local paths."""
        payload = self.model_dump(
            mode="json",
            exclude={
                "bundle_id": True,
                "built_at": True,
                "corpus": {"captured_at"},
            },
        )
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_identity(path: Path) -> FileIdentity:
    """Hash one file without loading it wholly into memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return FileIdentity(sha256=digest.hexdigest(), bytes=size)


def write_manifest(path: Path, manifest: BundleManifest) -> None:
    """Write a canonical manifest after verifying its content identity."""
    if manifest.bundle_id != manifest.computed_bundle_id():
        raise RetrievalArtifactError("bundle_id does not match manifest content")
    path.write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )


def validate_bundle(bundle_dir: Path) -> BundleManifest:
    """Validate one complete bundle and all files it names."""
    manifest_path = bundle_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise RetrievalArtifactError(f"retrieval manifest missing: {manifest_path}")
    try:
        manifest = BundleManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise RetrievalArtifactError(f"invalid retrieval manifest: {exc}") from exc
    if bundle_dir.name != manifest.bundle_id:
        raise RetrievalArtifactError("bundle directory does not match bundle_id")
    if manifest.computed_bundle_id() != manifest.bundle_id:
        raise RetrievalArtifactError("bundle manifest content hash does not match")
    for relative, expected in manifest.files.items():
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RetrievalArtifactError(f"unsafe bundle file path: {relative}")
        path = bundle_dir / candidate
        if not path.is_file():
            raise RetrievalArtifactError(f"bundle file missing: {relative}")
        actual = file_identity(path)
        if actual != expected:
            raise RetrievalArtifactError(f"bundle file digest mismatch: {relative}")
    return manifest


def resolve_active_bundle(root: Path) -> tuple[Path, BundleManifest]:
    """Resolve and validate the active bundle.

    Raises:
        RetrievalArtifactError: If no complete active bundle can be opened.
    """
    current = root / CURRENT_FILE
    if not current.is_file():
        raise RetrievalArtifactError(f"active retrieval pointer missing: {current}")
    bundle_id = current.read_text(encoding="ascii").strip()
    if not _BUNDLE_ID.fullmatch(bundle_id):
        raise RetrievalArtifactError("active retrieval pointer is malformed")
    bundle_dir = root / bundle_id
    return bundle_dir, validate_bundle(bundle_dir)


def activate_bundle(root: Path, bundle_id: str) -> BundleManifest:
    """Atomically point ``CURRENT`` at an already validated bundle."""
    if not _BUNDLE_ID.fullmatch(bundle_id):
        raise RetrievalArtifactError("bundle_id must be a lowercase sha256")
    bundle_dir = root / bundle_id
    manifest = validate_bundle(bundle_dir)
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".CURRENT.", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(bundle_id + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / CURRENT_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest
