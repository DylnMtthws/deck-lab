"""Explicit offline preparation of pinned per-view vectors; never downloads models."""

import argparse
import json
from pathlib import Path

from sabermetrics.assistant_card_views import prepare_card_view_vectors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cards", type=Path, required=True, help="JSON array of public card facts"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cards = json.loads(args.cards.read_text())
    if not isinstance(cards, list) or not all(isinstance(card, dict) for card in cards):
        raise ValueError("cards must be a JSON array of card objects")
    artifact = prepare_card_view_vectors(cards)
    args.output.write_text(json.dumps(artifact, sort_keys=True) + "\n")
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
