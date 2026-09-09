"""Strict configuration for the Research Assistant retrieval substrate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

RESEARCH_CONFIG_SCHEMA = "research-retrieval-config.v1"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "config" / "research.yaml"


class ArtifactSettings(BaseModel):
    """Locations for generated, non-source-controlled retrieval artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root: Path


class EmbeddingModelSettings(BaseModel):
    """Pinned local embedding model and encoding convention."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    local_dir: Path
    dimensions: int = Field(gt=0)
    normalize: bool = True
    query_prefix: str = ""
    batch_size: int = Field(default=128, ge=1)
    device: str = "cpu"


class RerankerSettings(BaseModel):
    """Pinned local cross-encoder used only over a bounded fused pool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    local_dir: Path
    max_length: int = Field(default=512, ge=32)
    batch_size: int = Field(default=16, ge=1)
    device: str = "cpu"


class RetrievalSettings(BaseModel):
    """Pool bounds and deterministic fusion weights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lexical_pool: int = Field(default=500, ge=1, le=5000)
    dense_pool: int = Field(default=500, ge=1, le=5000)
    rerank_pool: int = Field(default=200, ge=1, le=1000)
    result_limit: int = Field(default=50, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1)
    weights: dict[str, float]
    final_weights: dict[str, float]
    bm25_weights: dict[str, float]

    @model_validator(mode="after")
    def validate_retrieval(self) -> RetrievalSettings:
        """Reject incomplete or non-positive ranking configuration."""
        if set(self.weights) != {"lexical", "dense"}:
            raise ValueError("weights must define exactly lexical and dense")
        if set(self.final_weights) != {"rrf", "reranker"}:
            raise ValueError("final_weights must define exactly rrf and reranker")
        if set(self.bm25_weights) != {"name", "type_line", "oracle_text"}:
            raise ValueError(
                "bm25_weights must define exactly name, type_line, and oracle_text"
            )
        for label, values in (
            ("weights", self.weights),
            ("final_weights", self.final_weights),
            ("bm25_weights", self.bm25_weights),
        ):
            if any(value <= 0 for value in values.values()):
                raise ValueError(f"{label} values must be positive")
        if self.rerank_pool > self.lexical_pool + self.dense_pool:
            raise ValueError("rerank_pool exceeds the maximum fused pool")
        if self.result_limit > self.rerank_pool:
            raise ValueError("result_limit exceeds rerank_pool")
        return self


class ResearchSettings(BaseModel):
    """Top-level R2 retrieval configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    artifacts: ArtifactSettings
    embedding: EmbeddingModelSettings
    reranker: RerankerSettings
    retrieval: RetrievalSettings

    @model_validator(mode="after")
    def validate_schema(self) -> ResearchSettings:
        """Refuse a config written for another retrieval contract."""
        if self.schema_version != RESEARCH_CONFIG_SCHEMA:
            raise ValueError(
                f"unsupported research config schema: {self.schema_version}"
            )
        return self

    def retrieval_sha256(self) -> str:
        """Hash every setting that can affect ranking, excluding local paths."""
        payload = {
            "schema_version": self.schema_version,
            "embedding": self.embedding.model_dump(mode="json", exclude={"local_dir"}),
            "reranker": self.reranker.model_dump(mode="json", exclude={"local_dir"}),
            "retrieval": self.retrieval.model_dump(mode="json"),
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_research_settings(path: str | Path | None = None) -> ResearchSettings:
    """Load and validate the dedicated retrieval configuration.

    Args:
        path: YAML path. Defaults to ``config/research.yaml``.

    Returns:
        Validated settings.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return ResearchSettings.model_validate(raw)
