"""Explicit offline preparation of pinned per-view vectors; never downloads models."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from sabermetrics.assistant_card_views import prepare_card_view_vectors
from sabermetrics.assistant_knowledge_refresh import iter_card_file
from sabermetrics.assistant_view_index import refresh_view_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cards", type=Path, required=True, help="JSON array of public card facts"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--format",
        choices=("sqlite", "legacy-json"),
        default="sqlite",
        help="SQLite streams and reuses rows; legacy JSON is bounded to 16 MiB",
    )
    args = parser.parse_args()
    if args.cards.resolve() == args.output.resolve():
        parser.error("output must differ from the input card file")
    if args.format == "sqlite":
        print(
            json.dumps(
                refresh_view_index(iter_card_file(args.cards), args.output),
                sort_keys=True,
            )
        )
        return
    if args.cards.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("legacy input exceeds 16 MiB; use SQLite")
    cards = json.loads(args.cards.read_text())
    if not isinstance(cards, list) or not all(isinstance(card, dict) for card in cards):
        raise ValueError("cards must be a JSON array of card objects")
    if len(cards) * 3 > 10000:
        raise ValueError("legacy record budget exceeded; use SQLite")
    artifact = prepare_card_view_vectors(cards)
    payload = json.dumps(artifact, sort_keys=True) + "\n"
    if len(payload.encode()) > 16 * 1024 * 1024 or len(artifact["records"]) > 10000:
        raise ValueError("legacy artifact exceeds bounded loader; use SQLite")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + args.output.name, dir=args.output.parent
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "cards": len(cards),
                "view_vectors": len(artifact["records"]),
                "embedding_model": artifact["embedding_model"],
            }
        )
    )


if __name__ == "__main__":
    main()
