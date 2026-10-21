"""Offline permission/provenance manifests; metadata never proves expertise."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sabermetrics.reference_layer.strategy_corpus import ingest_text


def source_arguments(manifest: dict[str, Any], folder: Path) -> dict[str, Any]:
    basis = manifest.get("permission_basis")
    if basis not in {
        "direct_user_supplied_authorization",
        "open_license",
        "author_permission",
        "original_authored_work",
    }:
        raise ValueError("A declared permission basis is required")
    if basis == "open_license" and not manifest.get("license_url"):
        raise ValueError("Open-license sources require the license URL")
    if manifest.get("source_kind") == "wiki_secondary" and not all(
        manifest.get(key)
        for key in ("version", "source_date", "source_url", "attribution_url")
    ):
        raise ValueError(
            "Wiki imports require revision, date, permalink and attribution"
        )
    name = str(manifest.get("content_file") or "")
    if name and (Path(name).name != name or name in {".", ".."}):
        raise ValueError("Content must be adjacent to the manifest")
    inline = manifest.get("text")
    text = (folder / name).read_text() if name and (folder / name).is_file() else inline
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 1_000_000:
        raise ValueError("Source text missing or exceeds offline import budget")
    if inline is not None and inline != text:
        raise ValueError("Inline and file content disagree")
    if hashlib.sha256(text.encode()).hexdigest() != manifest.get("sha256"):
        raise ValueError("Source content hash mismatch")
    keys = (
        "document",
        "source_url",
        "version",
        "permission",
        "source_kind",
        "section",
        "commander",
        "archetype",
        "source_key",
        "author",
        "source_date",
        "source_class",
        "tier",
        "primary_links",
        "limitations",
        "permission_basis",
        "review_status",
        "license_url",
        "attribution_url",
    )
    result = {key: manifest[key] for key in keys if key in manifest}
    result["text"] = text
    return result


def import_source_manifest(path: Path, db_path: Path, *, embed=False) -> dict[str, Any]:
    manifest = json.loads(path.read_text())
    arguments = source_arguments(manifest, path.parent)
    count = ingest_text(db_path, embed=embed, **arguments)
    return {
        "status": "imported",
        "source_key": manifest["source_key"],
        "version": manifest["version"],
        "chunks": count,
        "content_sha256": manifest["sha256"],
        "permission_basis": manifest["permission_basis"],
        "review_status": manifest.get("review_status", "not_recorded"),
        "scope": "Declared authorization and review provenance; not legal or expert certification. No network access.",
    }
