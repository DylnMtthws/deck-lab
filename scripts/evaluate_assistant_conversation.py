"""Score a captured three-turn research conversation without making model calls.

Usage (from an installed checkout):
  python scripts/evaluate_assistant_conversation.py --schema schema.json
  python scripts/evaluate_assistant_conversation.py --run run.json \
    --reviews reviews.json --report report.json [--baseline baseline.json \
    --baseline-reviews baseline-reviews.json]

The schema output describes run and separate review inputs. Bind each run to
benchmark_digest(Benchmark.model_validate(fixture)); bind reviews to SHA256 of
exact UTF-8 answer text. Capture all turns in order in the same conversation.
Normalize runtime artifacts into ``state.task``, ``state.preferences``,
``state.evidence`` and ``state.deliverables`` using the frozen fixture paths;
never fill missing values from expected answers or a model's self-evaluation.
``answer_card_names`` and ``unavailable_card_names`` are capture annotations and
must be checked against the visible answer by the skeptical reviewer. Retrieval
records must come from successful/failed tool receipts, including prior turns.
State assertions alone cannot establish whether prose is correct or useful.
For new captures prefer assistant_conversation_eval.capture_turn: it derives
task counts, preference dimensions, canonical identities and source continuity
from run_turn results. Pass prior captured turns and externally checked deck
integrity; absent observations fail closed. Deliverable quality remains semantic.

Use independent reviewers and the supplied semantic criteria. This evaluator
checks answer hashes, not reviewer enrollment: broad expert qualification still
uses evaluate_assistant_expertise.py and its blinded review/policy contract.
Missing semantic reviews => unknown, missing turns => incomplete, regressions or
failed checks => fail. Exit 0 only on a live-capture pass (and no baseline
regressions), 2 for incomplete/unknown, 1 otherwise. Controlled test outputs can
exercise the gate but can never qualify live quality. The 180-second per-turn
ceiling is a provisional regression guard, not an acceptable UX target or p95.
Never submit private account IDs, credentials or a full private deck in a report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from sabermetrics.assistant_conversation_eval import (
    Benchmark,
    Review,
    Run,
    compare,
    evaluate,
)


def _read(path: Path):
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError("Input exceeds size limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(path.read_text(), object_pairs_hook=unique)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("tests/fixtures/assistant_marchesa_conversation.json"),
    )
    parser.add_argument("--run", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-reviews", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--schema", type=Path)
    args = parser.parse_args(argv)
    if args.schema:
        if args.schema.resolve() == args.benchmark.resolve():
            parser.error("Schema must not overwrite the benchmark")
        args.schema.parent.mkdir(parents=True, exist_ok=True)
        args.schema.write_text(
            json.dumps(
                {
                    "run": Run.model_json_schema(),
                    "reviews": TypeAdapter(list[Review]).json_schema(),
                },
                indent=2,
            )
            + "\n"
        )
        return 0
    if args.run is None or args.report is None:
        parser.error("--run and --report are required without --schema")
    paths = [
        p
        for p in (
            args.benchmark,
            args.run,
            args.reviews,
            args.baseline,
            args.baseline_reviews,
        )
        if p is not None
    ]
    if args.report.resolve() in {p.resolve() for p in paths}:
        parser.error("Report must not overwrite an input")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Invalidate previous successful output before parsing potentially bad inputs.
    args.report.write_text('{"status":"incomplete","quality_gate_pass":false}\n')
    try:
        if args.baseline_reviews is not None and args.baseline is None:
            raise ValueError("Baseline reviews require a baseline")
        benchmark = Benchmark.model_validate(_read(args.benchmark))
        run = Run.model_validate(_read(args.run))
        reviews = (
            TypeAdapter(list[Review]).validate_python(_read(args.reviews))
            if args.reviews
            else []
        )
        report = evaluate(benchmark, run, reviews)
        if args.baseline:
            baseline = Run.model_validate(_read(args.baseline))
            baseline_reviews = (
                TypeAdapter(list[Review]).validate_python(_read(args.baseline_reviews))
                if args.baseline_reviews
                else []
            )
            prior = evaluate(benchmark, baseline, baseline_reviews)
            report["comparison"] = compare(benchmark, report, prior)
            blockers = {report["status"], report["comparison"]["status"]}
            if prior["status"] in {"unknown", "incomplete"}:
                blockers.add(prior["status"])
            report["status"] = next(
                (s for s in ("fail", "incomplete", "unknown") if s in blockers),
                "pass",
            )
            report["quality_gate_pass"] &= report["status"] == "pass"
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in ("status", "quality_gate_pass", "evidence_kind")
                }
            )
        )
        return (
            0
            if report["quality_gate_pass"]
            else 2 if report["status"] in {"unknown", "incomplete"} else 1
        )
    except (OSError, ValueError) as exc:
        # Do not echo supplied answers or private reviewer information.
        failure = {
            "status": "invalid",
            "quality_gate_pass": False,
            "error_type": type(exc).__name__,
        }
        args.report.write_text(json.dumps(failure) + "\n")
        print(json.dumps(failure))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
