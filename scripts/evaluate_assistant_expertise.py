"""Prepare blind review packets or qualify operator-collected expert evidence.

Exit 0: demonstrated benchmark improvement; 1: failure/no demonstrated gain or
invalid input; 2: missing independent evidence. Never calls a model or deploys.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from sabermetrics.assistant_expertise import (
    Benchmark,
    Key,
    Policy,
    Review,
    Run,
    evaluate,
    prepare_packet,
)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def read(path: Path):
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError("Evaluation input exceeds 8 MiB")
    return json.loads(path.read_text(), object_pairs_hook=_unique)


def write(path: Path, value, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        # Never overwrite a previously reviewed key or expose it through a
        # symlink/shared default umask. Reviewers must not receive this file.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(value, indent=2) + "\n")
    else:
        path.write_text(json.dumps(value, indent=2) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    packet = sub.add_parser("packet")
    gate = sub.add_parser("gate")
    schema = sub.add_parser("schema")
    schema.add_argument("--output", required=True, type=Path)
    for command in (packet, gate):
        command.add_argument("--benchmark", required=True, type=Path)
        command.add_argument("--run", required=True, type=Path)
        command.add_argument("--key", required=True, type=Path)
    packet.add_argument("--packet", required=True, type=Path)
    gate.add_argument("--reviews", required=True, type=Path)
    gate.add_argument(
        "--policy", type=Path, default=Path("config/assistant_expertise_policy.json")
    )
    gate.add_argument("--expected-candidate", required=True)
    gate.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    gate_output = None
    try:
        if args.command == "schema":
            write(
                args.output,
                {
                    "benchmark": Benchmark.model_json_schema(),
                    "run": Run.model_json_schema(),
                    "key": Key.model_json_schema(),
                    "reviews": TypeAdapter(list[Review]).json_schema(),
                    "policy": Policy.model_json_schema(),
                },
            )
            return 0
        if args.command == "gate":
            if args.report.resolve() in {
                p.resolve()
                for p in (args.benchmark, args.run, args.key, args.reviews, args.policy)
            }:
                raise ValueError("Report cannot overwrite an input or trusted policy")
            gate_output = args.report
            # An interrupted or malformed retry must not leave a previous
            # successful report at the requested output path.
            write(gate_output, {"status": "incomplete", "promotion_eligible": False})
        benchmark = Benchmark.model_validate(read(args.benchmark))
        run = Run.model_validate(read(args.run))
        if args.command == "packet":
            inputs = {args.benchmark.resolve(), args.run.resolve()}
            if args.packet.resolve() == args.key.resolve() or inputs & {
                args.packet.resolve(),
                args.key.resolve(),
            }:
                raise ValueError(
                    "Packet/key outputs must be separate from each other and inputs"
                )
            if args.packet.exists() or args.key.exists():
                raise ValueError("Refusing to overwrite an existing blind packet/key")
            value, key = prepare_packet(benchmark, run)
            write(args.key, key.model_dump(), private=True)
            write(args.packet, value)
            print("Created blind packet; keep its private key away from reviewers")
            return 0
        if run.candidate.revision != args.expected_candidate:
            raise ValueError("Candidate revision does not match the requested release")
        policy = Policy.model_validate(read(args.policy))
        reviews = TypeAdapter(list[Review]).validate_python(read(args.reviews))
        key = Key.model_validate(read(args.key))
        report = evaluate(benchmark, run, key, reviews, policy)
        write(args.report, report)
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "status",
                        "promotion_eligible",
                        "cases",
                        "blocked_reasons",
                        "failures",
                    )
                }
            )
        )
        return (
            2
            if report["status"] == "blocked"
            else 0 if report["promotion_eligible"] else 1
        )
    except (OSError, ValueError, ValidationError) as exc:
        # Do not print input values: benchmark text, responses and reviewer
        # identities may be confidential. Detailed failures belong locally.
        failure = {
            "status": "invalid",
            "promotion_eligible": False,
            "error_type": type(exc).__name__,
        }
        if gate_output is not None:
            write(gate_output, failure)
        print(json.dumps(failure))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
