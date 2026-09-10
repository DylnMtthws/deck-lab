"""Typed contracts for card retrieval.

The assistant never emits SQL and never reaches into an index directly.  It
constructs :class:`CardSearchQuery`; the deterministic substrate returns a
:class:`CardSearchResult` whose card identities and provenance can be checked
before any model is allowed to narrate them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Color = Literal["W", "U", "B", "R", "G"]
ColorMode = Literal["subset", "exact", "intersects"]
RetrievalStage = Literal["lexical", "dense", "rrf", "fused", "reranked"]


class CardFilters(BaseModel):
    """Deterministic predicates applied before ranked retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    color_identity: tuple[Color, ...] | None = None
    color_mode: ColorMode = "subset"
    required_types: tuple[str, ...] = ()
    excluded_types: tuple[str, ...] = ()
    #: Creature, land, artifact and enchantment subtypes — the part of a type
    #: line after the dash. Without these, "non-Human creature" and "a land
    #: with the Island type" are both inexpressible, and a plan can only gesture
    #: at them through free text that the ranker may or may not honour.
    required_subtypes: tuple[str, ...] = ()
    excluded_subtypes: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    any_tags: tuple[str, ...] = ()
    excluded_tags: tuple[str, ...] = ()
    allowed_oracle_ids: tuple[str, ...] = ()
    mana_value_min: float | None = Field(default=None, ge=0)
    mana_value_max: float | None = Field(default=None, ge=0)
    commander_legal: bool | None = True

    @model_validator(mode="after")
    def validate_filters(self) -> CardFilters:
        """Reject contradictory ranges and duplicate filter values."""
        if (
            self.mana_value_min is not None
            and self.mana_value_max is not None
            and self.mana_value_min > self.mana_value_max
        ):
            raise ValueError("mana_value_min exceeds mana_value_max")
        for label, values in (
            ("color_identity", self.color_identity or ()),
            ("required_types", self.required_types),
            ("excluded_types", self.excluded_types),
            ("required_subtypes", self.required_subtypes),
            ("excluded_subtypes", self.excluded_subtypes),
            ("required_tags", self.required_tags),
            ("any_tags", self.any_tags),
            ("excluded_tags", self.excluded_tags),
            ("allowed_oracle_ids", self.allowed_oracle_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} contains duplicates")
        type_conflicts = set(self.required_types) & set(self.excluded_types)
        if type_conflicts:
            raise ValueError(
                "types cannot be both required and excluded: "
                + ", ".join(sorted(type_conflicts))
            )
        subtype_conflicts = set(self.required_subtypes) & set(self.excluded_subtypes)
        if subtype_conflicts:
            raise ValueError(
                "subtypes cannot be both required and excluded: "
                + ", ".join(sorted(subtype_conflicts))
            )
        tag_conflicts = set(self.required_tags) & set(self.excluded_tags)
        if tag_conflicts:
            raise ValueError(
                "tags cannot be both required and excluded: "
                + ", ".join(sorted(tag_conflicts))
            )
        if self.any_tags and set(self.any_tags) <= set(self.excluded_tags):
            raise ValueError("every any_tags alternative is excluded")
        return self


class CardSearchQuery(BaseModel):
    """One bounded request against a card retrieval bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(default="", max_length=500)
    filters: CardFilters = Field(default_factory=CardFilters)
    top_k: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def require_a_predicate(self) -> CardSearchQuery:
        """Require text or at least one non-default structured constraint."""
        if self.text.strip():
            return self
        if self.filters != CardFilters():
            return self
        raise ValueError("a card search needs text or a structured filter")


class StageEvidence(BaseModel):
    """Where one card ranked at one deterministic retrieval stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: RetrievalStage
    rank: int = Field(ge=1)
    score: float


class RetrievalHit(BaseModel):
    """One Oracle card returned by the retrieval pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    oracle_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mana_cost: str | None = None
    mana_value: float = Field(ge=0)
    type_line: str
    oracle_text: str
    color_identity: tuple[Color, ...] = ()
    matched_tags: tuple[str, ...] = ()
    stages: tuple[StageEvidence, ...] = ()


class IndexProvenance(BaseModel):
    """Immutable identities needed to reproduce a retrieval result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_id: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tag_library_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_version: str = Field(min_length=1)
    embedding_model_id: str | None = None
    embedding_revision: str | None = None
    reranker_model_id: str | None = None
    reranker_revision: str | None = None


class RetrievalAvailability(BaseModel):
    """Which ranked stages ran, and any visible degradation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lexical: bool
    dense: bool
    reranked: bool
    notices: tuple[str, ...] = ()


class CardSearchResult(BaseModel):
    """Bounded card results plus stage and snapshot provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: CardSearchQuery
    hits: tuple[RetrievalHit, ...]
    eligible_cards: int = Field(ge=0)
    lexical_truncated: bool = False
    dense_truncated: bool = False
    fused_truncated: bool = False
    provenance: IndexProvenance
    availability: RetrievalAvailability
