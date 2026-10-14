"""Import an offline Commander Spellbook snapshot; no fetching or chat-time network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sabermetrics.combo_catalog import (
    MAX_SNAPSHOT_BYTES,
    SNAPSHOT_VERSION,
    import_combo_snapshot,
    parse_snapshot_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument(
        "--retrieved-at",
        help="Required timezone-aware date to wrap a raw API response; never inferred from import time",
    )
    args = parser.parse_args()
    if args.snapshot.stat().st_size > MAX_SNAPSHOT_BYTES:
        parser.error("Snapshot exceeds byte bound")
    try:
        snapshot = parse_snapshot_json(args.snapshot.read_text())
        if args.retrieved_at:
            if not isinstance(snapshot, dict):
                raise ValueError("Snapshot must be an object")
            if "retrieved_at" in snapshot or "snapshot_version" in snapshot:
                raise ValueError(
                    "Do not override existing provenance using --retrieved-at"
                )
            snapshot.update(
                retrieved_at=args.retrieved_at, snapshot_version=SNAPSHOT_VERSION
            )
        result = import_combo_snapshot(args.db_path, snapshot)
    except (ValueError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
