"""Retrieval-only evaluation for gate G1.

Portable tests may exercise the scoring rig with fake rankings.  An
authoritative G1 score additionally requires owner-verified labels, every
labelled Oracle ID in a named full-corpus bundle, and the pinned production
embedding model.  Fixture quality is never printed as production quality.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sabermetrics.substrate.models import CardSearchQuery
from sabermetrics.substrate.settings import ResearchSettings

G1_LABEL_SCHEMA = "research-g1-labels.v1"
G1_SCORECARD_SCHEMA = "research-g1-scorecard.v1"
MINIMUM_FULL_CORPUS_ROWS = 30_000
#: The only card view a production claim may be made over (ADR-020). Defined
#: here rather than in a script so the G1 and G2 gates cannot drift apart.
PRODUCTION_CARD_VIEW = "mtg_v1.card_any_medium"
G1_TARGET_RECALL = 0.90
G1_STOP_RECALL = 0.80
RankedChannel = Literal["lexical", "dense", "rrf", "fused"]


class G1InputError(RuntimeError):
    """The requested evaluation is not eligible to be called G1."""


class RetrievalLabel(BaseModel):
    """One scoreable, human-reviewable card-discovery question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    query: CardSearchQuery
    required_oracle_ids: tuple[str, ...] = Field(min_length=1)
    forbidden_oracle_ids: tuple[str, ...] = ()
    labeller: str = Field(min_length=2)
    labelled_on: date
    review_status: Literal["draft", "owner_verified"]
    review_note: str = ""

    @model_validator(mode="after")
    def check_id_sets(self) -> RetrievalLabel:
        """Require unique, disjoint relevance labels."""
        if len(set(self.required_oracle_ids)) != len(self.required_oracle_ids):
            raise ValueError("required_oracle_ids contains duplicates")
        if len(set(self.forbidden_oracle_ids)) != len(self.forbidden_oracle_ids):
            raise ValueError("forbidden_oracle_ids contains duplicates")
        overlap = set(self.required_oracle_ids) & set(self.forbidden_oracle_ids)
        if overlap:
            raise ValueError("an Oracle ID cannot be both required and forbidden")
        return self


class RetrievalLabelSet(BaseModel):
    """A versioned collection of G1 retrieval labels."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    labels: tuple[RetrievalLabel, ...]

    @model_validator(mode="after")
    def check_set(self) -> RetrievalLabelSet:
        """Validate schema and unique question IDs."""
        if self.schema_version != G1_LABEL_SCHEMA:
            raise ValueError(f"unsupported G1 label schema: {self.schema_version}")
        ids = [label.question_id for label in self.labels]
        if len(set(ids)) != len(ids):
            raise ValueError("G1 labels contain duplicate question IDs")
        return self

    def sha256(self) -> str:
        """Hash the exact labels, including their review state."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RetrievalObservation(BaseModel):
    """Ranked Oracle IDs returned for one question by each stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    rankings: dict[RankedChannel, tuple[str, ...]]
    truncated: dict[RankedChannel, bool] = Field(default_factory=dict)
    elapsed_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def rankings_are_unique(self) -> RetrievalObservation:
        """Reject duplicate cards, which make rank-based metrics ambiguous."""
        for channel, ranking in self.rankings.items():
            if len(set(ranking)) != len(ranking):
                raise ValueError(f"{channel} ranking contains duplicate Oracle IDs")
        return self


class AuthoritativeRun(BaseModel):
    """Evidence that a score came from the full pinned retrieval corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_row_count: int = Field(ge=0)
    full_corpus: bool
    embedding_model_id: str
    embedding_revision: str


def load_retrieval_labels(path: Path) -> RetrievalLabelSet:
    """Load strict G1 labels from YAML."""
    return RetrievalLabelSet.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def score_g1(
    labels: RetrievalLabelSet,
    observations: Iterable[RetrievalObservation],
    *,
    k: int = 50,
) -> dict[str, object]:
    """Compute per-channel macro and micro recall plus forbidden-hit rates."""
    if k < 1:
        raise ValueError("k must be positive")
    by_label = {label.question_id: label for label in labels.labels}
    rows = list(observations)
    unknown = sorted({row.question_id for row in rows} - set(by_label))
    if unknown:
        raise ValueError(f"observations reference unknown labels: {unknown}")
    if len({row.question_id for row in rows}) != len(rows):
        raise ValueError("duplicate retrieval observations")

    channels = sorted({channel for row in rows for channel in row.rankings})
    metrics: dict[str, dict[str, float | int]] = {}
    per_question: dict[str, dict[str, float]] = {}
    for row in rows:
        label = by_label[row.question_id]
        required = set(label.required_oracle_ids)
        per_question[row.question_id] = {
            channel: _recall_at(required, ranking, k)
            for channel, ranking in sorted(row.rankings.items())
        }
    for channel in channels:
        recalls: list[float] = []
        required_found = 0
        required_total = 0
        forbidden_hits = 0
        forbidden_total = 0
        for row in rows:
            ranking = row.rankings.get(channel)
            if ranking is None:
                continue
            label = by_label[row.question_id]
            required = set(label.required_oracle_ids)
            found = len(required & set(ranking[:k]))
            recalls.append(found / len(required))
            required_found += found
            required_total += len(required)
            forbidden = set(label.forbidden_oracle_ids)
            forbidden_hits += len(forbidden & set(ranking[:k]))
            forbidden_total += len(forbidden)
        metrics[channel] = {
            "questions": len(recalls),
            "macro_recall": sum(recalls) / len(recalls) if recalls else 0.0,
            "required_found": required_found,
            "required_total": required_total,
            "micro_recall": (
                required_found / required_total if required_total else 0.0
            ),
            "forbidden_hits": forbidden_hits,
            "forbidden_total": forbidden_total,
            "forbidden_hit_rate": (
                forbidden_hits / forbidden_total if forbidden_total else 0.0
            ),
        }
    return {
        "schema_version": G1_SCORECARD_SCHEMA,
        "authoritative": False,
        "labels_sha256": labels.sha256(),
        "recall_k": k,
        "labels_total": len(labels.labels),
        "questions_evaluated": len(rows),
        "channels": metrics,
        "per_question_recall": per_question,
        "truncated_questions": {
            channel: sum(row.truncated.get(channel, False) for row in rows)
            for channel in channels
        },
        "elapsed_ms": sum(row.elapsed_ms for row in rows),
    }


def authoritative_g1(
    labels: RetrievalLabelSet,
    observations: Iterable[RetrievalObservation],
    run: AuthoritativeRun,
    settings: ResearchSettings,
    corpus_oracle_ids: set[str],
) -> dict[str, object]:
    """Validate production evidence, score it, and apply the G1 threshold."""
    unverified = [
        label.question_id
        for label in labels.labels
        if label.review_status != "owner_verified"
    ]
    if unverified:
        raise G1InputError(
            "authoritative G1 requires owner-verified labels: " + ", ".join(unverified)
        )
    if not run.full_corpus or run.corpus_row_count < MINIMUM_FULL_CORPUS_ROWS:
        raise G1InputError(
            "authoritative G1 requires a full corpus of at least "
            f"{MINIMUM_FULL_CORPUS_ROWS} cards"
        )
    if (
        run.embedding_model_id != settings.embedding.model_id
        or run.embedding_revision != settings.embedding.revision
    ):
        raise G1InputError("authoritative G1 requires the pinned embedding model")
    labelled_ids = {
        oracle_id
        for label in labels.labels
        for oracle_id in (
            *label.required_oracle_ids,
            *label.forbidden_oracle_ids,
        )
    }
    missing = sorted(labelled_ids - corpus_oracle_ids)
    if missing:
        raise G1InputError(
            "authoritative G1 labels do not resolve in the corpus: "
            + ", ".join(missing)
        )
    rows = list(observations)
    observed = {row.question_id for row in rows}
    expected = {label.question_id for label in labels.labels}
    if observed != expected:
        raise G1InputError(
            "authoritative G1 requires one observation per label; "
            f"missing={sorted(expected - observed)}, unknown={sorted(observed - expected)}"
        )
    required_channels = {"lexical", "dense", "rrf", "fused"}
    incomplete = {
        row.question_id: sorted(required_channels - row.rankings.keys())
        for row in rows
        if required_channels - row.rankings.keys()
    }
    if incomplete:
        raise G1InputError(
            "authoritative G1 requires lexical, dense, rrf, and fused rankings: "
            f"{incomplete}"
        )
    scorecard = score_g1(labels, rows)
    channels = scorecard["channels"]
    assert isinstance(channels, Mapping)
    fused = channels.get("fused")
    if not isinstance(fused, Mapping):
        raise G1InputError("authoritative G1 requires a fused ranking")
    recall = float(fused["macro_recall"])
    status = (
        "pass"
        if recall >= G1_TARGET_RECALL
        else ("stop_and_fix" if recall < G1_STOP_RECALL else "fail_target")
    )
    scorecard.update(
        {
            "authoritative": True,
            "corpus_sha256": run.corpus_sha256,
            "corpus_row_count": run.corpus_row_count,
            "embedding_model_id": run.embedding_model_id,
            "embedding_revision": run.embedding_revision,
            "target_macro_recall": G1_TARGET_RECALL,
            "stop_below_macro_recall": G1_STOP_RECALL,
            "status": status,
        }
    )
    return scorecard


def _recall_at(required: set[str], ranking: tuple[str, ...], k: int) -> float:
    return len(required & set(ranking[:k])) / len(required)
