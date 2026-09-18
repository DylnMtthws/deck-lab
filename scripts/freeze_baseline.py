"""Preserve one evaluation result as a checked-in artefact.

    python scripts/freeze_baseline.py --scorecard .research-dev/g2-scorecard.json \
        --limitation "..." --limitation "..."

A frozen baseline is a number plus the exact inputs it was measured over. It
refuses to write unless the scorecard's recorded input hash still matches the
checked-in questions, plans and adjudications — freezing a result against files
that have already moved would preserve the number and lose its meaning, which
is worse than not freezing at all.

It never marks a result authoritative. That comes from the scorecard, which
only claims it when the gate actually ran.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from sabermetrics.assistant.eval.baseline import (
    BASELINE_DIR,
    FrozenBaseline,
    baseline_filename,
    evaluation_inputs_sha256,
)
from sabermetrics.assistant.eval.models import load_questions
from sabermetrics.assistant.eval.plans import load_hand_written_plans


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
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
    """Freeze one scorecard, refusing anything whose inputs have moved."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scorecard", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--frozen-on", default="")
    parser.add_argument(
        "--limitation",
        action="append",
        default=[],
        help="what this measurement does NOT establish; repeatable, required",
    )
    args = parser.parse_args()
    if not args.limitation:
        raise SystemExit(
            "FREEZE REFUSED: give at least one --limitation. A preserved number "
            "with no stated limits is quoted as though it had none"
        )

    card = json.loads(args.scorecard.read_text(encoding="utf-8"))
    provenance = card.get("provenance") or {}
    recorded = provenance.get("evaluation_inputs_sha256")
    if not recorded:
        raise SystemExit(
            "FREEZE REFUSED: this scorecard predates evaluation_inputs_sha256, "
            "so there is nothing to check its inputs against. Re-run G2"
        )
    current = evaluation_inputs_sha256(load_questions(), load_hand_written_plans())
    if recorded != current:
        raise SystemExit(
            "FREEZE REFUSED: the questions, plans or adjudications have changed "
            f"since this run.\n  scorecard: {recorded}\n  working tree: {current}\n"
            "Re-run G2 against the current inputs rather than freezing a number "
            "measured over inputs that no longer exist"
        )

    retrieval = card["gates"]["retrieval"]
    discovery = card["gates"]["discovery"]
    support = card["gates"].get("rules_support") or {}
    # ``{"status": "not_built"}`` when no index was bound; every .get below
    # then yields None, which the model records as "not recorded".
    rules_index = provenance.get("rules_index") or {}
    if "generation_id" not in rules_index:
        rules_index = {}
    baseline = FrozenBaseline(
        kind=(
            "authoritative_gate"
            if card.get("authoritative")
            else "development_baseline"
        ),
        adjudication_set=str(provenance["adjudication_set"]),
        frozen_on=(
            date.fromisoformat(args.frozen_on) if args.frozen_on else date.today()
        ),
        inputs_sha256=current,
        plans_sha256=str(provenance["plans_sha256"]),
        bundle_id=str(provenance["bundle_id"]),
        corpus_sha256=str(provenance["corpus_sha256"]),
        corpus_row_count=int(provenance["corpus_row_count"]),
        embedding_model_id=str(provenance["embedding_model_id"]),
        embedding_revision=str(provenance["embedding_revision"]),
        reranker_model_id=str(provenance["reranker_model_id"]),
        reranker_revision=str(provenance["reranker_revision"]),
        retrieval_passed=int(retrieval["passed"]),
        retrieval_applicable=int(retrieval["applicable"]),
        retrieval_failed=list(retrieval["failed"]),
        discovery_passed=int(discovery["passed"]),
        discovery_applicable=int(discovery["applicable"]),
        name_lookup_count=int(card["subsets_name_lookup"]["count"]),
        unscored_pending=dict(retrieval["unscored_pending"]),
        alternative_coverage={
            key: dict(value) for key, value in retrieval["alternative_coverage"].items()
        },
        rules_support_set=support.get("label_set"),
        rules_support_status=support.get("status"),
        rules_support_passed=support.get("passed"),
        rules_support_applicable=support.get("applicable"),
        rules_support_failed=list(support.get("failed") or ()),
        rules_support_unlabelled=list(support.get("unlabelled") or ()),
        rules_index_generation_id=rules_index.get("generation_id"),
        rules_index_chunk_count=rules_index.get("chunk_count"),
        rules_index_chunker_sha256=rules_index.get("chunker_sha256"),
        rules_support_propositions_covered=support.get("propositions_covered"),
        rules_support_propositions_total=support.get("propositions_total"),
        limitations=list(args.limitation),
    )
    output = args.output or (BASELINE_DIR / baseline_filename(baseline))
    _write_atomic(output, baseline.model_dump(mode="json"))
    print(
        f"froze {baseline.kind} {baseline.retrieval_passed}"
        f"/{baseline.retrieval_applicable} for set {baseline.adjudication_set}"
        f" -> {output}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"FREEZE REFUSED: {exc}")
        raise SystemExit(2) from None
