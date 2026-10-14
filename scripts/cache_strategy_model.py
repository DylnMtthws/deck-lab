"""Build-time download of one pinned ONNX model; runtime never downloads."""

import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

REPO = "Qdrant/all-MiniLM-L6-v2-onnx"
# Immutable revision; model identity additionally includes SHA256 of actual assets.
REVISION = "d13954661f83248295ba75c1ed411eef3b7b936e"


def main() -> None:
    target = Path(sys.argv[1])
    revision = sys.argv[2] if len(sys.argv) > 2 else REVISION
    if revision == "main":
        raise ValueError("supply an immutable Hugging Face commit revision")
    source = Path(
        snapshot_download(
            REPO,
            revision=revision,
            allow_patterns=["*.json", "*.txt", "onnx/model.onnx", "model.onnx"],
        )
    )
    shutil.copytree(source, target, dirs_exist_ok=True)
    (target / "source_revision.txt").write_text(f"{REPO}@{revision}\n")


if __name__ == "__main__":
    main()
