"""Package a reviewed simulator release as a reproducible standalone zipapp.

Usage: python scripts/package_goldfish.py /path/to/simulator-release
The source commit and source checksum remain recorded alongside the archive.
App formatting/type tools inspect transport code, not a rewritten engine copy.
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path


def package(release: Path, destination: Path) -> None:
    manifest = json.loads((release / "manifest.json").read_text())
    source = (release / manifest["artifact_file"]).read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest["artifact_sha256"]:
        raise ValueError("Simulator release checksum mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "goldfish_diagnostics.pyz"
    info = zipfile.ZipInfo("__main__.py", date_time=(2026, 1, 1, 0, 0, 0))
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        output.writestr(info, source)
    manifest["source_sha256"] = manifest["artifact_sha256"]
    manifest["artifact_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest["artifact_file"] = archive.name
    (destination / "goldfish-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


if __name__ == "__main__":
    package(
        Path(sys.argv[1]),
        Path(__file__).resolve().parents[1] / "src/sabermetrics/vendor",
    )
