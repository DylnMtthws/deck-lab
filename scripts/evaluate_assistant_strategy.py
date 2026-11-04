"""Measure split strategic retrieval; optionally spend TypeSafe usage on Jev."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sabermetrics.assistant_strategy_benchmark import Suite, run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("tests/fixtures/assistant_strategic_eval.json"),
    )
    parser.add_argument(
        "--split", choices=("development", "heldout"), default="development"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--live-jev",
        action="store_true",
        help="Send public fixture state to TypeSafe; incurs API usage",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        help="Read an operator credential locally for --live-jev; never recorded",
    )
    args = parser.parse_args()
    if args.key_file and not args.live_jev:
        parser.error("--key-file requires --live-jev")
    key = args.key_file.read_text().strip() if args.key_file else None
    suite = Suite.model_validate_json(args.suite.read_text())
    report = run_benchmark(suite, split=args.split, live_jev=args.live_jev, api_key=key)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "split",
                    "candidate_recall",
                    "recommendation_quality_pairs",
                    "factual_errors",
                    "latency",
                    "cost",
                )
            }
        )
    )
    if args.live_jev and any(c["jev"]["status"] != "judged" for c in report["cases"]):
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
