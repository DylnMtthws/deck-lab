"""Download the exact pinned retrieval model snapshots for an offline runtime.

This is the only R2 command that contacts a model hub. Bundle construction and
query retrieval load ``local_dir`` with ``local_files_only=True`` and require
the revision marker written here.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from sabermetrics.substrate.settings import load_research_settings


def _write_revision(path: Path, revision: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=".REVISION.", suffix=".tmp", dir=path
    )
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(revision + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path / "REVISION")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    """Download selected pinned snapshots and attest their revisions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--model",
        choices=("embedding", "reranker", "all"),
        default="all",
    )
    args = parser.parse_args()
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface-hub is unavailable; install sabermetrics[research]"
        ) from exc

    settings = load_research_settings(args.config)
    selected = (
        ("embedding", settings.embedding),
        ("reranker", settings.reranker),
    )
    for label, model in selected:
        if args.model not in {label, "all"}:
            continue
        model.local_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=model.model_id,
            revision=model.revision,
            local_dir=model.local_dir,
        )
        _write_revision(model.local_dir, model.revision)
        print(f"{label}: {model.model_id}@{model.revision} -> {model.local_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
