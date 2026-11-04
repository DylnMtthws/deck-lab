#!/usr/bin/env python3
"""Offline, idempotent evidence import and bounded cached-catalog view refresh."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sabermetrics.assistant_knowledge_refresh import refresh_knowledge


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="Defaults to adjacent assistant-card-view-vectors.sqlite",
    )
    parser.add_argument(
        "--expected-cards",
        type=int,
        help="Expected eligible projection identities; larger than present reports partial coverage",
    )
    parser.add_argument(
        "--source-publication-at",
        help="Operator supplied catalog source date; never inferred from file mtime",
    )
    parser.add_argument("--fetched-at", help="Actual catalog fetch time, if known")
    parser.add_argument(
        "--snapshot", type=Path, help="Approved local canonical Scryfall snapshot"
    )
    parser.add_argument("--snapshot-observed-at")
    parser.add_argument("--snapshot-retrieved-at")
    parser.add_argument("--snapshot-version")
    parser.add_argument("--rulings", type=Path, help="Approved local rulings snapshot")
    parser.add_argument("--rulings-sha256")
    parser.add_argument("--rulings-source-url")
    parser.add_argument("--rulings-version")
    parser.add_argument(
        "--rulings-descriptor",
        type=Path,
        help="Pinned bulk-data descriptor; URL, size and source date are validated",
    )
    args = parser.parse_args()
    if args.expected_cards is not None and args.expected_cards < 0:
        parser.error("expected-cards must be nonnegative")
    if args.rulings and not all(
        (args.rulings_sha256, args.rulings_source_url, args.rulings_version)
    ):
        parser.error("rulings requires sha256, source-url and version")
    output = args.output or args.db.parent / "assistant-card-view-vectors.sqlite"
    if output.resolve() in {
        p.resolve()
        for p in (args.db, args.snapshot, args.rulings, args.rulings_descriptor)
        if p
    }:
        parser.error("output must differ from catalog and input evidence files")
    imports = {}
    try:
        if args.snapshot:
            from sabermetrics.assistant_card_facts import import_snapshot

            imports["canonical"] = import_snapshot(
                args.db,
                args.snapshot,
                observed_at=args.snapshot_observed_at,
                retrieved_at=args.snapshot_retrieved_at,
                version=args.snapshot_version,
            )
        if args.rulings:
            from sabermetrics.assistant_rulings_import import (
                BulkDescriptor,
                import_rulings,
            )

            imports["rulings"] = import_rulings(
                args.db,
                args.rulings,
                expected_sha256=args.rulings_sha256,
                source_url=args.rulings_source_url,
                version=args.rulings_version,
                descriptor=(
                    BulkDescriptor.model_validate_json(
                        args.rulings_descriptor.read_text()
                    )
                    if args.rulings_descriptor
                    else None
                ),
            ).model_dump()
        receipt = refresh_knowledge(
            args.db,
            output,
            expected_cards=args.expected_cards,
            source_publication_at=args.source_publication_at,
            fetched_at=args.fetched_at,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "refresh_success_at": None,
                    "published_generation": "prior_generation_retained_if_present",
                    "imports": imports,
                    "counts": getattr(
                        exc,
                        "counts",
                        {
                            "expected": args.expected_cards,
                            "present": None,
                            "eligible": None,
                            "indexed": None,
                            "stale": None,
                            "unsupported": None,
                            "failed": 1,
                            "excluded": None,
                        },
                    ),
                    "previous_generation": getattr(exc, "previous_generation", None),
                    "error": str(exc),
                }
            )
        )
        raise SystemExit(1) from None
    print(
        json.dumps(
            {"status": "success", "output": str(output), "imports": imports, **receipt},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
