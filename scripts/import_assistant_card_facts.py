#!/usr/bin/env python3
"""Import a local Scryfall snapshot; never fetches the network."""

import argparse
import json

from sabermetrics.assistant_card_facts import import_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--observed-at", help="Source snapshot date, if known (ISO 8601)"
    )
    parser.add_argument(
        "--retrieved-at", help="Actual retrieval date, if known (ISO 8601)"
    )
    parser.add_argument("--version", help="Source version label, if supplied")
    parser.add_argument(
        "--allow-older",
        action="store_true",
        help="Explicitly allow superseding dated evidence with older or undated evidence",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            import_snapshot(
                args.db,
                args.snapshot,
                observed_at=args.observed_at,
                retrieved_at=args.retrieved_at,
                version=args.version,
                allow_older=args.allow_older,
            )
        )
    )


if __name__ == "__main__":
    main()
