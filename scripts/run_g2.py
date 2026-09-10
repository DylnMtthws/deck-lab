"""Run the hand-written plans through the executor and score gate G2.

Default mode asks to make the production G2 claim, and therefore refuses on a
contested golden set, a draft plan, an unreviewed id map, a corpus that is not
``mtg_v1.card_any_medium``, a corpus below the full-card floor, an unpinned
local model, or a ``rules_lookup`` with no reference index. Every one of those
would make the number a claim about something other than production.

``--measured`` runs the same plans against whatever bundle is active and writes
a scorecard marked ``authoritative: false``. That is legitimate tuning evidence
and is explicitly not G2, exactly as the R2 development bundle was explicitly
not G1.

Exit codes mirror ``run_g1.py``: 0 pass, 1 measured but below target, 2 refused
to measure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from sabermetrics.assistant.eval.g2 import G2Run, authoritative_g2, g2_scorecard
from sabermetrics.assistant.eval.models import load_questions
from sabermetrics.assistant.eval.plans import (
    check_plan_coverage,
    load_hand_written_plans,
)
from sabermetrics.assistant.eval.runner import load_label_id_map, run_all
from sabermetrics.assistant.executor import ResearchExecutor
from sabermetrics.assistant.sources import BundleCardSource
from sabermetrics.substrate.retrieval import CardRetrievalFacade
from sabermetrics.substrate.settings import load_research_settings

ROOT = Path(__file__).resolve().parent.parent


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON durably, so a killed run cannot leave a partial scorecard."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    """Execute every hand-written plan and emit the G2 scorecard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--plans", type=Path, default=None, help="plan directory")
    parser.add_argument("--id-map", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--measured",
        action="store_true",
        help="score against the active bundle without claiming G2",
    )
    parser.add_argument(
        "--only",
        default="",
        help="score a single question id; always non-authoritative",
    )
    args = parser.parse_args()

    questions = load_questions()
    plans = (
        load_hand_written_plans(args.plans) if args.plans else load_hand_written_plans()
    )
    if args.only:
        # A single-question run is a plan-authoring aid, so full coverage is
        # not required — but the one question asked for must exist and must
        # have a plan, or the run would report an empty scorecard as a pass.
        questions = questions.model_copy(
            update={
                "questions": [
                    question
                    for question in questions.questions
                    if question.id == args.only
                ]
            }
        )
        if not questions.questions:
            raise RuntimeError(f"no golden question {args.only!r}")
        plans = plans.model_copy(
            update={
                "plans": tuple(
                    plan for plan in plans.plans if plan.question_id == args.only
                )
            }
        )
        if not plans.plans:
            raise RuntimeError(f"no hand-written plan for {args.only!r}")
    check_plan_coverage(plans, questions)
    id_map = load_label_id_map(args.id_map) if args.id_map else load_label_id_map()

    settings = load_research_settings(args.config)
    with CardRetrievalFacade(settings) as facade:
        manifest = facade.manifest
        cards = BundleCardSource(facade)
        executor = ResearchExecutor(cards)
        observations, runs = run_all(questions, plans, executor, id_map)
        corpus_oracle_ids = set(facade.oracle_ids)

    embedding = manifest.embedding
    reranker = manifest.reranker
    if embedding is None or reranker is None:
        raise RuntimeError("the active bundle names no ranking models")
    run = G2Run(
        bundle_id=manifest.bundle_id,
        corpus_source_view=manifest.corpus.source_view,
        corpus_row_count=manifest.corpus.row_count,
        corpus_sha256=manifest.corpus.content_sha256,
        embedding_model_id=embedding.model_id,
        embedding_revision=embedding.revision,
        reranker_model_id=reranker.model_id,
        reranker_revision=reranker.revision,
    )

    if args.measured or args.only:
        scorecard = g2_scorecard(
            questions,
            observations,
            runs,
            plans=plans,
            id_map=id_map,
            run=run,
        )
    else:
        scorecard = authoritative_g2(
            questions,
            observations,
            runs,
            plans=plans,
            id_map=id_map,
            run=run,
            settings=settings,
            corpus_oracle_ids=corpus_oracle_ids,
        )

    print(json.dumps(scorecard, indent=2, sort_keys=True))
    if args.output:
        _write_atomic(args.output, scorecard)
    # Exit 0 means "the G2 gate passed". A measured run is explicitly not the
    # gate, so it never returns 0 however good its number is.
    return 0 if scorecard["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"G2 REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
