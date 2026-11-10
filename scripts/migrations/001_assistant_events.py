"""Apply the additive assistant public journal migration to an explicit database."""

from __future__ import annotations

import argparse

from sabermetrics.assistant_store import AssistantStore


def main() -> None:
    """Reuse the idempotent application initializer; never infer a live DB path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    args = parser.parse_args()
    AssistantStore(args.database)


if __name__ == "__main__":
    main()
