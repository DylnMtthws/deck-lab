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

``--chunk N`` executes the plans in sequential subprocesses of N questions
each, so the reranker's working set is released between chunks rather than
accumulating — an 8 GB machine cannot hold all eighty in one process. The
chunks are execution only. Every chunk must report the same bundle identity,
the union of their question ids must be the whole set with no duplicate, and
the scorecard is computed ONCE over the merged observations. Chunk scores are
never averaged: G2 counts questions, and a mean of per-chunk rates is a
different and wrong number.

Exit codes mirror ``run_g1.py``: 0 pass, 1 measured but below target, 2 refused
to measure.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from sabermetrics.assistant.envelope import PlanRun
from sabermetrics.assistant.eval.g2 import (
    G2Run,
    RulesIndexIdentity,
    authoritative_g2,
    g2_scorecard,
    preflight_refusals,
)
from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    GoldenQuestionSet,
    load_questions,
)
from sabermetrics.assistant.eval.plans import (
    HandWrittenPlanSet,
    check_plan_coverage,
    load_hand_written_plans,
)
from sabermetrics.assistant.eval.rules_support import load_rules_support_labels
from sabermetrics.assistant.eval.runner import (
    LabelIdMap,
    load_label_id_map,
    run_all,
)
from sabermetrics.assistant.executor import ResearchExecutor
from sabermetrics.assistant.sources import BundleCardSource, ReferenceRulesSource
from sabermetrics.substrate.artifacts import resolve_active_bundle
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


class ChunkMismatchError(RuntimeError):
    """Chunks did not describe one consistent run."""


def _execute_chunk(
    questions: GoldenQuestionSet,
    plans: HandWrittenPlanSet,
    id_map: LabelIdMap,
    settings_path: Path | None,
) -> dict[str, Any]:
    """Execute one chunk in this process and return its portable payload."""
    settings = load_research_settings(settings_path)
    rules, rules_identity = _rules_binding(settings)
    with CardRetrievalFacade(settings) as facade:
        manifest = facade.manifest
        executor = ResearchExecutor(BundleCardSource(facade), rules=rules)
        observations, runs = run_all(questions, plans, executor, id_map)
    return {
        "bundle_id": manifest.bundle_id,
        "corpus_sha256": manifest.corpus.content_sha256,
        # Carried so the merge can refuse chunks that consulted different
        # rules texts, for the same reason it refuses different bundles.
        "rules_generation_id": (
            rules_identity.generation_id if rules_identity is not None else ""
        ),
        "rows": [
            {
                "observation": observation.model_dump(mode="json"),
                "run": run.model_dump(mode="json"),
            }
            for observation, run in zip(observations, runs, strict=True)
        ],
    }


#: Checked in by ``scripts/build_rules_index.py``. The repository states which
#: rules document the Ask path answers from; the database only holds it.
RULES_MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "research"
    / ("rules_index.json")
)


def _rules_binding(
    settings: Any,
) -> tuple[ReferenceRulesSource | None, RulesIndexIdentity | None]:
    """Bind the rules index, or return the typed absence of one.

    Both halves have to agree. The manifest says which document was indexed and
    the database says which generation is active, and a run that consulted a
    generation the manifest does not describe would cite an effective date it
    never read. So a mismatch refuses rather than quietly preferring one.

    Args:
        settings: Loaded research settings, for the artifacts root.

    Returns:
        The source and its identity, or ``(None, None)`` when no index exists.

    Raises:
        RuntimeError: If the built index and the checked-in manifest disagree.
    """
    db_path = Path(settings.artifacts.root) / "reference.db"
    if not RULES_MANIFEST.is_file() or not db_path.is_file():
        return None, None
    manifest = json.loads(RULES_MANIFEST.read_text(encoding="utf-8"))
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT generation_id FROM active_reference_embedding_generation "
            "WHERE singleton = 1"
        ).fetchone()
    if row is None:
        return None, None
    active = str(row[0])
    if active != manifest["generation_id"]:
        raise RuntimeError(
            "the active reference generation is not the one the checked-in "
            f"manifest describes: active={active}, "
            f"manifest={manifest['generation_id']}. Rebuild with "
            "scripts/build_rules_index.py"
        )
    identity = RulesIndexIdentity(
        document=manifest["source"]["document"],
        effective_date=manifest["source"]["effective_date"],
        source_sha256=manifest["source"]["content_sha256"],
        generation_id=active,
        chunk_count=int(manifest["chunk_count"]),
        chunker_sha256=manifest["configuration"]["chunker_sha256"],
    )
    return ReferenceRulesSource.open(db_path), identity


def _spawn_chunk(start: int, size: int, args: argparse.Namespace) -> dict[str, Any]:
    """Execute one window in a child process and return its payload.

    A separate process rather than a loop is the whole point: the reranker's
    working set is returned to the OS when the child exits, which a long-lived
    process would hold until the end.

    Args:
        start: Index of the first question in this window.
        size: Questions per window.
        args: Parsed CLI arguments, for the paths the child needs.

    Returns:
        The child's decoded payload.

    Raises:
        ChunkMismatchError: If the child exits non-zero.
    """
    with tempfile.NamedTemporaryFile("r+", suffix=".json", delete=False) as handle:
        target = Path(handle.name)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--emit-chunk",
        str(target),
        "--chunk-start",
        str(start),
        "--chunk-size",
        str(size),
    ]
    for flag, value in (
        ("--config", args.config),
        ("--plans", args.plans),
        ("--id-map", args.id_map),
        ("--questions", args.questions),
        ("--rules-support-labels", args.rules_support_labels),
    ):
        if value is not None:
            command += [flag, str(value)]
    try:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise ChunkMismatchError(
                f"chunk starting at {start} exited {completed.returncode}"
            )
        return dict(json.loads(target.read_text(encoding="utf-8")))
    finally:
        target.unlink(missing_ok=True)


def _run_chunks(
    questions: GoldenQuestionSet,
    plans: HandWrittenPlanSet,
    id_map: LabelIdMap,
    size: int,
    args: argparse.Namespace,
) -> tuple[list[CorrectnessObservation], list[PlanRun]]:
    """Execute every question in sequential subprocesses, then merge.

    Each chunk is its own process so the reranker's working set is returned to
    the OS between chunks. The merge is where correctness is enforced: one
    bundle identity across every chunk, and exactly one observation per golden
    question.

    Args:
        questions: The full golden question set.
        plans: The hand-written plans.
        id_map: The id-space translation.
        size: Questions per chunk.
        args: Parsed CLI arguments, for the paths a child needs.

    Returns:
        Merged observations and runs, in golden-question order.

    Raises:
        ChunkMismatchError: If two chunks read different bundles, or the merged
            coverage is incomplete or contains a duplicate.
    """
    total = len(questions.questions)
    payloads: list[dict[str, Any]] = []
    for start in range(0, total, size):
        window = questions.questions[start : start + size]
        print(f"chunk {start}..{start + len(window)} of {total}", file=sys.stderr)
        payloads.append(_spawn_chunk(start, size, args))

    identities = {(p["bundle_id"], p["corpus_sha256"]) for p in payloads}
    if len(identities) != 1:
        raise ChunkMismatchError(
            "chunks read different bundles, so their results describe different "
            f"corpora and cannot be merged: {sorted(identities)}"
        )
    rules_identities = {p.get("rules_generation_id", "") for p in payloads}
    if len(rules_identities) != 1:
        raise ChunkMismatchError(
            "chunks consulted different rules generations, so their rules "
            f"answers came from different documents: {sorted(rules_identities)}"
        )

    seen: dict[str, tuple[CorrectnessObservation, PlanRun]] = {}
    for payload in payloads:
        for row in payload["rows"]:
            observation = CorrectnessObservation.model_validate(row["observation"])
            if observation.question_id in seen:
                raise ChunkMismatchError(
                    f"{observation.question_id} was executed by two chunks"
                )
            seen[observation.question_id] = (
                observation,
                PlanRun.model_validate(row["run"]),
            )
    expected = [question.id for question in questions.questions]
    missing = sorted(set(expected) - set(seen))
    if missing:
        raise ChunkMismatchError(
            "chunked run does not cover every golden question: " + ", ".join(missing)
        )
    ordered = [seen[question_id] for question_id in expected]
    return [row[0] for row in ordered], [row[1] for row in ordered]


def main() -> int:
    """Execute every hand-written plan and emit the G2 scorecard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--plans", type=Path, default=None, help="plan directory")
    parser.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="question directory; a held-out set. Implies --measured: a run "
        "over questions other than the checked-in golden set is never the gate",
    )
    parser.add_argument(
        "--rules-support-labels",
        type=Path,
        default=None,
        help="rules-support label file to score against instead of the "
        "checked-in one; for a held-out set. Implies --measured",
    )
    parser.add_argument("--id-map", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--observations",
        type=Path,
        default=None,
        help="also write the merged observations and runs, which the owner "
        "review packet needs to show a reviewer which cards came back",
    )
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
    parser.add_argument(
        "--chunk",
        type=int,
        default=0,
        metavar="N",
        help="execute in sequential subprocesses of N questions, then score "
        "once over the merged result. Chunk scores are never averaged.",
    )
    # Internal: how a chunk subprocess is told what to execute and where to
    # put it. Not part of the interface a person uses.
    parser.add_argument("--emit-chunk", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--chunk-start", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--chunk-size", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.chunk and args.chunk < 1:
        raise RuntimeError("--chunk must be a positive number of questions")
    if args.chunk and args.only:
        raise RuntimeError("--chunk and --only are mutually exclusive")

    if args.questions or args.rules_support_labels:
        # A held-out run publishes a number about a different question set or
        # a different key. It can never be the gate, and it must not be able to
        # overwrite the checked-in measurement, so both are forced here rather
        # than left to the flag the operator remembered to pass.
        args.measured = True
    rules_support_labels = (
        load_rules_support_labels(args.rules_support_labels)
        if args.rules_support_labels
        else load_rules_support_labels()
    )
    questions = load_questions(args.questions) if args.questions else load_questions()
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

    # Refuse BEFORE executing anything, on the preconditions that depend only
    # on the checked-in artefacts. The same checks run again inside
    # authoritative_g2 against the executed evidence; what this avoids is
    # spending a quarter of an hour to be told something that was knowable
    # before the first query.
    if not (args.measured or args.only or args.emit_chunk):
        refusals = preflight_refusals(
            questions,
            plans=plans,
            id_map=id_map,
            rules_support=rules_support_labels,
        )
        if refusals:
            raise RuntimeError(
                "authoritative G2 cannot be claimed:\n  - "
                + "\n  - ".join(
                    f"[{item.blocker_id}] {item.detail}" for item in refusals
                )
                + "\nEach blocker's owner and closure criterion are in "
                "fixtures/research/g2_blockers.yaml.\nRun with --measured for "
                "development evidence, which is explicitly not the gate."
            )

    if args.emit_chunk is not None:
        window = questions.questions[
            args.chunk_start : args.chunk_start + args.chunk_size
        ]
        payload = _execute_chunk(
            questions.model_copy(update={"questions": list(window)}),
            plans,
            id_map,
            args.config,
        )
        args.emit_chunk.write_text(json.dumps(payload), encoding="utf-8")
        return 0

    if args.chunk:
        observations, runs = _run_chunks(questions, plans, id_map, args.chunk, args)
        settings = load_research_settings(args.config)
        # Read the manifest without opening a facade. The facade
        # constructor materializes all 34,551 catalog records and maps
        # the vector file, which is exactly the working set chunking
        # exists to avoid holding in the parent.
        _, manifest = resolve_active_bundle(settings.artifacts.root)
        return _finish(
            questions,
            observations,
            runs,
            plans,
            id_map,
            manifest,
            settings,
            args,
        )

    settings = load_research_settings(args.config)
    rules, _ = _rules_binding(settings)
    with CardRetrievalFacade(settings) as facade:
        manifest = facade.manifest
        executor = ResearchExecutor(BundleCardSource(facade), rules=rules)
        observations, runs = run_all(questions, plans, executor, id_map)

    return _finish(
        questions,
        observations,
        runs,
        plans,
        id_map,
        manifest,
        settings,
        args,
    )


def _corpus_oracle_ids(settings: Any) -> set[str]:
    """Return every Oracle id in the active bundle.

    Only an authoritative claim needs this — it is the check that every label
    resolves in the executing corpus — and obtaining it costs a full catalog
    read, so it is deferred to the one caller that cannot do without it.
    """
    with CardRetrievalFacade(settings) as facade:
        return set(facade.oracle_ids)


def _finish(
    questions: GoldenQuestionSet,
    observations: list[CorrectnessObservation],
    runs: list[PlanRun],
    plans: HandWrittenPlanSet,
    id_map: LabelIdMap,
    manifest: Any,
    settings: Any,
    args: argparse.Namespace,
) -> int:
    """Score one complete run and emit it. The only place a score is computed."""
    embedding = manifest.embedding
    reranker = manifest.reranker
    if embedding is None or reranker is None:
        raise RuntimeError("the active bundle names no ranking models")
    _, rules_identity = _rules_binding(settings)
    run = G2Run(
        rules_index=rules_identity,
        bundle_id=manifest.bundle_id,
        corpus_source_view=manifest.corpus.source_view,
        corpus_row_count=manifest.corpus.row_count,
        corpus_sha256=manifest.corpus.content_sha256,
        embedding_model_id=embedding.model_id,
        embedding_revision=embedding.revision,
        reranker_model_id=reranker.model_id,
        reranker_revision=reranker.revision,
    )

    # Loaded here as well as in main(): this function is also the scoring end
    # of a chunked run, whose subprocesses re-enter through main() and whose
    # merge does not, so the labels must be resolved from args at the point
    # of use rather than threaded through.
    rules_support_labels = (
        load_rules_support_labels(args.rules_support_labels)
        if args.rules_support_labels
        else load_rules_support_labels()
    )
    if args.measured or args.only:
        scorecard = g2_scorecard(
            questions,
            observations,
            runs,
            plans=plans,
            id_map=id_map,
            run=run,
            rules_support=rules_support_labels,
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
            corpus_oracle_ids=_corpus_oracle_ids(settings),
            rules_support=rules_support_labels,
        )

    print(json.dumps(scorecard, indent=2, sort_keys=True))
    if args.output:
        _write_atomic(args.output, scorecard)
    if args.observations:
        _write_atomic(
            args.observations,
            {
                "schema_version": "research-g2-observations.v1",
                "bundle_id": run.bundle_id,
                "evaluation_inputs_sha256": scorecard["provenance"][
                    "evaluation_inputs_sha256"
                ],
                "rows": [
                    {
                        "observation": observation.model_dump(mode="json"),
                        "run": plan_run.model_dump(mode="json"),
                    }
                    for observation, plan_run in zip(observations, runs, strict=True)
                ],
            },
        )
    # Exit 0 means "the G2 gate passed". A measured run is explicitly not the
    # gate, so it never returns 0 however good its number is.
    return 0 if scorecard["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"G2 REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
