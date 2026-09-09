"""Build and atomically activate the R2 card retrieval bundle.

Production omits ``--snapshot`` and reads ``mtg_v1`` once in a read-only,
repeatable-read transaction. A snapshot path is for local development only and
retains its honest source label in the manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sabermetrics.substrate.bundle import build_from_source
from sabermetrics.substrate.corpus import resolve_source
from sabermetrics.substrate.settings import load_research_settings


def main() -> int:
    """Build a complete bundle and print its immutable manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="development JSON/JSONL snapshot; omit for production mtg_v1",
    )
    parser.add_argument("--dsn", default=None, help="mtg_v1 Postgres DSN override")
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="install the bundle without changing CURRENT",
    )
    args = parser.parse_args()
    settings = load_research_settings(args.config)
    manifest = build_from_source(
        resolve_source(args.snapshot, dsn=args.dsn),
        settings,
        activate=not args.no_activate,
    )
    print(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
