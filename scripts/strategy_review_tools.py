"""Offline inventory, signed source review/import, and development receipt scoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sabermetrics.strategy_development_evaluation import (
    DEFAULT_SUITE,
    compare,
    evaluate,
)
from sabermetrics.strategy_review_workflow import (
    assess_review,
    import_reviewed,
    inventory,
)


def read(path: Path):
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("inventory")
    audit.add_argument(
        "--config-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/sabermetrics/config_data",
    )
    for name in ("queue", "import"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--review", type=Path, required=name == "import")
        command.add_argument(
            "--trusted-reviewers", type=Path, required=name == "import"
        )
        if name == "import":
            command.add_argument("--db-path", type=Path, required=True)
    score = commands.add_parser("evaluate")
    score.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    score.add_argument("--receipts", type=Path)
    score.add_argument("--baseline-receipts", type=Path)
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory(args.config_root)
    elif args.command in {"queue", "import"}:
        review = read(args.review) if args.review else None
        trusted = read(args.trusted_reviewers) if args.trusted_reviewers else {}
        if args.command == "import":
            if not isinstance(review, dict):
                parser.error("--review must contain a signed review object")
            result = import_reviewed(args.manifest, args.db_path, review, trusted)
        else:
            result = assess_review(
                read(args.manifest), args.manifest.parent, review, trusted
            )
    else:
        if args.baseline_receipts and not args.receipts:
            parser.error("--baseline-receipts requires --receipts")
        result = evaluate(read(args.receipts) if args.receipts else [], args.suite)
        if args.baseline_receipts:
            result["comparison"] = compare(
                evaluate(read(args.baseline_receipts), args.suite), result
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
