#!/usr/bin/env python3
"""List frozen public inputs or evaluate captured/callback receipts offline."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sabermetrics.assistant_quality_evaluation import load_cases, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=["development", "heldout"], default="development"
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--receipts", type=Path)
    inputs.add_argument(
        "--callback", help="Import path module:function, no paid calls supplied"
    )
    parser.add_argument("--final-code-freeze")
    parser.add_argument("--capture-output", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.receipts:
        data = json.loads(args.receipts.read_text())
        if isinstance(data, list):
            ids = [r["case_id"] for r in data]
            if len(ids) != len(set(ids)):
                parser.error("Duplicate receipt case_id")
            data = {r["case_id"]: r for r in data}
        if not isinstance(data, dict):
            parser.error("Receipts must be an object keyed by case ID or a list")
        expected = {c["id"] for c in load_cases(args.split)}
        if set(data) - expected:
            parser.error("Receipts contain IDs outside selected split")
        callback = lambda case: data.get(case["id"], {"case_id": case["id"]})
    elif args.callback:
        module, name = args.callback.rsplit(":", 1)
        callback = getattr(importlib.import_module(module), name)
    else:
        callback = None
    captured: list[dict] = []
    try:
        result = (
            run(
                callback,
                args.split,
                final_code_freeze=args.final_code_freeze,
                captured_receipts=captured,
            )
            if callback
            else {"mode": "public-inputs-only", "cases": load_cases(args.split)}
        )
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    if args.capture_output:
        if callback is None:
            parser.error("--capture-output requires --receipts or --callback")
        args.capture_output.write_text(json.dumps(captured, indent=2) + "\n")


if __name__ == "__main__":
    main()
