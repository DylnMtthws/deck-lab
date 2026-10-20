"""Capture bounded deck-conditioned retrieval separately from expert/model quality."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from pathlib import Path

from sabermetrics.assistant_functional_search import (
    FunctionalSearchArgs,
    functional_search,
)
from sabermetrics.assistant_strategy_benchmark import _seed


def _source_fingerprint() -> str:
    from sabermetrics import assistant_functional_search

    root = Path(assistant_functional_search.__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        parser.error(
            "--source-sha must be the full base commit SHA; source tree hash records working edits"
        )
    raw = args.pilot.read_bytes()
    pilot = json.loads(raw)
    decks = {d["id"]: d for d in pilot["decks"]}
    captures = []
    with tempfile.TemporaryDirectory(prefix="decklab-compatibility-") as directory:
        path = Path(directory) / "catalog.db"
        _seed(path, pilot["catalog"])
        for case in pilot["cases"]:
            started = time.perf_counter()
            result = functional_search(
                path,
                decks[case["deck_id"]]["document"],
                FunctionalSearchArgs.model_validate(case["args"]),
            )
            captures.append(
                dict(
                    id=case["id"],
                    request=case["request"],
                    family=decks[case["deck_id"]]["family"],
                    retrieval_seconds=time.perf_counter() - started,
                    result=result,
                )
            )
    report = dict(
        version="assistant-compatibility-deck-capture.v1",
        source_sha=args.source_sha,
        source_tree_sha256=_source_fingerprint(),
        capture_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        pilot_sha256=hashlib.sha256(raw).hexdigest(),
        captures=captures,
        scope=pilot["provenance"],
        independent_expert_preference=None,
        factual_error_rate=None,
        candidate_recall=None,
        end_to_end_answer_seconds=None,
        configured_model_cost_usd=0,
        model_calls=0,
    )
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                cases=len(captures),
                families=sorted({c["family"] for c in captures}),
                model_calls=0,
                report=str(args.report),
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
