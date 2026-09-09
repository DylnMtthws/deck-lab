"""Run authoritative full-corpus retrieval gate G1.

This command intentionally has no fixture mode. Portable scoring belongs in the
unit suite; invoking this file asks to make the production-quality G1 claim and
therefore fails on draft labels, a non-mtg_v1 corpus, missing local models, or a
corpus below the full-card floor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from sabermetrics.substrate.evaluation import (
    AuthoritativeRun,
    RetrievalObservation,
    authoritative_g1,
    load_retrieval_labels,
)
from sabermetrics.substrate.retrieval import CardRetrievalFacade
from sabermetrics.substrate.settings import load_research_settings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = ROOT / "fixtures" / "research" / "g1_labels.yaml"
PRODUCTION_CARD_VIEW = "mtg_v1.card_any_medium"


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
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
    """Load the active production bundle, execute every label, and score G1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    settings = load_research_settings(args.config)
    labels = load_retrieval_labels(args.labels)
    observations: list[RetrievalObservation] = []
    with CardRetrievalFacade(settings) as facade:
        manifest = facade.manifest
        if manifest.corpus.source_view != PRODUCTION_CARD_VIEW:
            raise SystemExit(
                "G1 REFUSED: active bundle source is "
                f"{manifest.corpus.source_view!r}, expected {PRODUCTION_CARD_VIEW!r}"
            )
        for label in labels.labels:
            trace = facade.search_with_trace(label.query)
            observations.append(
                RetrievalObservation(
                    question_id=label.question_id,
                    rankings={
                        channel: trace.rankings[channel]
                        for channel in ("lexical", "dense", "rrf", "fused")
                    },
                    truncated={
                        channel: trace.truncated[channel]
                        for channel in ("lexical", "dense", "rrf", "fused")
                    },
                    elapsed_ms=trace.elapsed_ms,
                )
            )
        assert manifest.embedding is not None
        scorecard = authoritative_g1(
            labels,
            observations,
            AuthoritativeRun(
                corpus_sha256=manifest.corpus.content_sha256,
                corpus_row_count=manifest.corpus.row_count,
                full_corpus=True,
                embedding_model_id=manifest.embedding.model_id,
                embedding_revision=manifest.embedding.revision,
            ),
            settings,
            set(facade.oracle_ids),
        )
    scorecard["bundle_id"] = manifest.bundle_id
    scorecard["retrieval_config_sha256"] = manifest.retrieval_config_sha256
    scorecard["tag_library_sha256"] = manifest.tags.library_sha256
    rendered = json.dumps(scorecard, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        _write_atomic(args.output, scorecard)
    return 0 if scorecard["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"G1 REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
