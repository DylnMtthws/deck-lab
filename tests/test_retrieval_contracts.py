"""R2 retrieval contracts fail closed before any index is queried."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sabermetrics.substrate.models import CardFilters, CardSearchQuery
from sabermetrics.substrate.settings import (
    DEFAULT_CONFIG,
    ResearchSettings,
    load_research_settings,
)


def test_the_shipped_research_config_is_pinned_and_hashable():
    settings = load_research_settings()
    assert settings.embedding.model_id == "BAAI/bge-small-en-v1.5"
    assert len(settings.embedding.revision) == 40
    assert settings.embedding.dimensions == 384
    assert settings.reranker.model_id == "BAAI/bge-reranker-base"
    assert len(settings.reranker.revision) == 40
    assert len(settings.retrieval_sha256()) == 64


def test_local_paths_do_not_change_retrieval_identity():
    settings = load_research_settings()
    moved = settings.model_copy(
        update={
            "artifacts": settings.artifacts.model_copy(
                update={"root": Path("/different/index/root")}
            ),
            "embedding": settings.embedding.model_copy(
                update={"local_dir": Path("/different/embedding")}
            ),
            "reranker": settings.reranker.model_copy(
                update={"local_dir": Path("/different/reranker")}
            ),
        }
    )
    assert moved.retrieval_sha256() == settings.retrieval_sha256()


def test_a_ranking_change_moves_retrieval_identity():
    settings = load_research_settings()
    changed = settings.model_copy(
        update={
            "retrieval": settings.retrieval.model_copy(
                update={"rrf_k": settings.retrieval.rrf_k + 1}
            )
        }
    )
    assert changed.retrieval_sha256() != settings.retrieval_sha256()


def test_unknown_config_fields_are_rejected(tmp_path):
    raw = DEFAULT_CONFIG.read_text(encoding="utf-8") + "\nunknown: true\n"
    path = tmp_path / "research.yaml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown"):
        load_research_settings(path)


@pytest.mark.parametrize("revision", ["main", "", "a" * 39, "g" * 40])
def test_model_revisions_must_be_immutable_commits(revision):
    raw = load_research_settings().model_dump(mode="python")
    raw["embedding"]["revision"] = revision
    with pytest.raises(ValidationError):
        ResearchSettings.model_validate(raw)


def test_text_or_a_structured_filter_is_required():
    with pytest.raises(ValidationError, match="text or a structured filter"):
        CardSearchQuery()
    assert CardSearchQuery(text="free counterspell").text
    assert CardSearchQuery(filters=CardFilters(required_tags=("cost:phyrexian_mana",)))


def test_mana_value_range_is_ordered():
    with pytest.raises(ValidationError, match="mana_value_min exceeds"):
        CardFilters(mana_value_min=4, mana_value_max=1)


def test_required_and_excluded_filters_cannot_conflict():
    with pytest.raises(ValidationError, match="both required and excluded"):
        CardFilters(required_tags=("mana:ritual",), excluded_tags=("mana:ritual",))
    with pytest.raises(ValidationError, match="every any_tags alternative"):
        CardFilters(
            any_tags=("cost:evoke", "cost:free_alternative_cost"),
            excluded_tags=("cost:evoke", "cost:free_alternative_cost"),
        )


def test_duplicate_filter_values_are_rejected():
    with pytest.raises(ValidationError, match="contains duplicates"):
        CardFilters(required_types=("Artifact", "Artifact"))


def test_price_and_collection_are_absent_from_the_contract():
    fields = set(CardFilters.model_fields) | set(CardSearchQuery.model_fields)
    assert not any("price" in field or "owned" in field for field in fields)


def test_query_contract_forbids_unknown_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CardSearchQuery.model_validate({"text": "draw", "budget_usd": 100})
