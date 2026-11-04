"""Run the labeled Build assistant evaluation suite and write a JSON report.

Usage:
    PYTHONPATH=src python scripts/evaluate_assistant_intelligence.py \
        [--suite tests/fixtures/assistant_intelligence_eval.json] \
        [--db data/sabermetrics.db] [--report data/eval/assistant-intelligence.json]

Exit codes: 0 every case passed; 1 failures, errors, an empty suite, or a
malformed suite; 2 only unavailable cases (for example ``--db`` missing for
``corpus: database`` cases). The suite runs hermetically and makes no network
or model calls; ``--db`` is opened read-only by the APIs under test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sabermetrics.assistant_evaluation import (
    SuiteError,
    exit_code,
    load_suite,
    run_suite,
    summary_lines,
)

DEFAULT_SUITE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "assistant_intelligence_eval.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
    except SuiteError as exc:
        print(f"status=fail malformed suite: {exc}", file=sys.stderr)
        return 1
    report = run_suite(suite, db_path=args.db)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for line in summary_lines(report):
        print(line)
    if args.report is not None:
        print(f"report={args.report}")
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
