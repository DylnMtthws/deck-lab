"""G1 scoring cannot confuse portable fixtures with authoritative quality."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sabermetrics.substrate.evaluation import (
    AuthoritativeRun,
    G1InputError,
    RetrievalLabel,
    RetrievalLabelSet,
    RetrievalObservation,
    authoritative_g1,
    load_retrieval_labels,
    score_g1,
)
from sabermetrics.substrate.models import CardSearchQuery
from sabermetrics.substrate.settings import load_research_settings

ROOT = Path(__file__).resolve().parent.parent


def _labels(*, verified: bool = False) -> RetrievalLabelSet:
    return RetrievalLabelSet(
        schema_version="research-g1-labels.v1",
        labels=(
            RetrievalLabel(
                question_id="mechanic-001",
                query=CardSearchQuery(text="free counterspell"),
                required_oracle_ids=("a", "b"),
                forbidden_oracle_ids=("z",),
                labeller="Dylan" if verified else "agent draft",
                labelled_on=date(2026, 9, 9),
                review_status="owner_verified" if verified else "draft",
            ),
        ),
    )


def test_checked_in_g1_labels_use_the_canonical_review_mapping():
    labels = load_retrieval_labels(ROOT / "fixtures/research/g1_labels.yaml")
    review = json.loads(
        (ROOT / "fixtures/research/g1_label_review.json").read_text(encoding="utf-8")
    )
    canonical_ids = {row["canonical_oracle_id"] for row in review["replacements"]}
    fixture_ids = {row["fixture_id"] for row in review["replacements"]}
    labelled_ids = {
        oracle_id
        for label in labels.labels
        for oracle_id in (
            *label.required_oracle_ids,
            *label.forbidden_oracle_ids,
        )
    }
    assert review["status"] == "awaiting_owner_review"
    assert labelled_ids <= canonical_ids
    assert labelled_ids.isdisjoint(fixture_ids)


def _run(settings=None, **updates) -> AuthoritativeRun:
    settings = settings or load_research_settings()
    values = {
        "corpus_sha256": "a" * 64,
        "corpus_row_count": 34_000,
        "full_corpus": True,
        "embedding_model_id": settings.embedding.model_id,
        "embedding_revision": settings.embedding.revision,
    }
    values.update(updates)
    return AuthoritativeRun(**values)


def test_portable_score_reports_macro_micro_and_forbidden_hits():
    score = score_g1(
        _labels(),
        [
            RetrievalObservation(
                question_id="mechanic-001",
                rankings={
                    "lexical": ("a", "x"),
                    "dense": ("a", "b"),
                    "fused": ("a", "b", "z"),
                },
            )
        ],
    )
    assert score["authoritative"] is False
    channels = score["channels"]
    assert channels["lexical"]["macro_recall"] == 0.5
    assert channels["dense"]["micro_recall"] == 1.0
    assert channels["fused"]["forbidden_hits"] == 1


def test_authoritative_g1_requires_owner_verified_labels():
    settings = load_research_settings()
    with pytest.raises(G1InputError, match="owner-verified"):
        authoritative_g1(
            _labels(),
            [],
            _run(settings),
            settings,
            {"a", "b", "z"},
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"full_corpus": False},
        {"corpus_row_count": 29_999},
        {"embedding_model_id": "other/model"},
        {"embedding_revision": "0" * 40},
    ],
)
def test_authoritative_g1_requires_full_corpus_and_pinned_model(updates):
    settings = load_research_settings()
    with pytest.raises(G1InputError):
        authoritative_g1(
            _labels(verified=True),
            [],
            _run(settings, **updates),
            settings,
            {"a", "b", "z"},
        )


def test_authoritative_g1_requires_every_labelled_id_to_resolve():
    settings = load_research_settings()
    with pytest.raises(G1InputError, match="do not resolve"):
        authoritative_g1(
            _labels(verified=True),
            [],
            _run(settings),
            settings,
            {"a"},
        )


def test_authoritative_g1_requires_one_run_per_label():
    settings = load_research_settings()
    with pytest.raises(G1InputError, match="one observation per label"):
        authoritative_g1(
            _labels(verified=True),
            [],
            _run(settings),
            settings,
            {"a", "b", "z"},
        )


def test_authoritative_g1_applies_the_fused_threshold():
    settings = load_research_settings()
    score = authoritative_g1(
        _labels(verified=True),
        [
            RetrievalObservation(
                question_id="mechanic-001",
                rankings={
                    "lexical": ("a",),
                    "dense": ("b",),
                    "rrf": ("a", "b"),
                    "fused": ("a", "b"),
                },
            )
        ],
        _run(settings),
        settings,
        {"a", "b", "z"},
    )
    assert score["authoritative"] is True
    assert score["status"] == "pass"


def test_authoritative_g1_requires_every_ranked_stage():
    settings = load_research_settings()
    with pytest.raises(G1InputError, match="lexical, dense, rrf, and fused"):
        authoritative_g1(
            _labels(verified=True),
            [
                RetrievalObservation(
                    question_id="mechanic-001",
                    rankings={"lexical": ("a", "b")},
                )
            ],
            _run(settings),
            settings,
            {"a", "b", "z"},
        )
