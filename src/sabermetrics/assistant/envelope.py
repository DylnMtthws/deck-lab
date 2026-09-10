"""Provenance-bearing result envelopes for one executed plan step.

Every honesty field here is **required and has no default**. A result cannot be
constructed without its snapshot identity, its coverage and its field basis, so
it cannot be rendered without them — the same structural argument
``cedh/simulator.py`` makes for ``metric`` / ``measures`` / ``does_not_measure``.

Absence is the second arm of a union, not an empty list. A step that could not
run returns :class:`StepNotRun` with a reason from a closed set; an empty
:class:`StepResult` means the step ran and measured nothing, which is a
different fact and reads differently.

Two flags describe two different things and are deliberately not merged:

``truncated``
    This step's own bound removed rows. Normal for any ranked top-k, and
    already fully described by ``eligible`` against ``returned``.
``set_input_incomplete``
    An input to a set operation was cut off, so the set result may be wrong.
    This is the dangerous one, and the only one worth propagating.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sabermetrics.substrate.artifacts import BundleManifest
from sabermetrics.substrate.models import RetrievalAvailability, StageEvidence

#: The on-the-wire contract for one executed step.
ENVELOPE_SCHEMA_VERSION: Literal["research-step-result.v1"] = "research-step-result.v1"
#: The on-the-wire contract for one executed plan.
PLAN_RUN_SCHEMA_VERSION: Literal["research-plan-run.v1"] = "research-plan-run.v1"

#: ADR-029's four tiers. An R3 step produces Fact-tier material only; the
#: remaining members exist so a renderer and a narrator share one vocabulary.
AssertionTier = Literal["fact", "field_evidence", "measurement", "interpretation"]

#: How the rows in a result are ordered. A recall-at-k window is meaningful
#: only over ``ranked``; applying one to ``oracle_id`` order measures the
#: alphabet.
ResultOrdering = Literal["ranked", "oracle_id", "declaration"]

#: Why a result carries no field evidence. Authored by the plan, not inferred
#: by the handler: "this is not a field query" is a claim about the question,
#: and a handler cannot know whether the asker wanted a rate.
NoFieldEvidenceReason = Literal[
    "not_a_field_query",
    "field_statistics_deferred_to_r5",
    "cohort_below_event_size_floor",
    "meta_repository_unavailable",
]

#: Why a step did not run. A closed set: an unmodelled reason is a reason
#: nobody chose to state.
StepNotRunReason = Literal[
    "retrieval_bundle_unavailable",
    "retrieval_config_mismatch",
    "local_model_unavailable",
    "limit_exceeds_retrieval_ceiling",
    "reference_index_absent",
    "reference_index_stale",
    "reference_index_unlabelled",
    "rules_source_unavailable",
    "deck_context_unresolved",
    "deck_context_absent",
    # An input to this step did not run. Propagating is mandatory: a set
    # operation over the surviving inputs is an empty list pretending to be an
    # answer.
    "input_step_not_run",
    "input_tier_mismatch",
    "capability_deferred",
]


class _Envelope(BaseModel):
    """Strict, frozen base for every envelope model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CorpusProvenance(_Envelope):
    """Immutable identity of the snapshot and tag build a step read."""

    bundle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_source_view: str = Field(min_length=1)
    corpus_row_count: int = Field(ge=0)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_version: str = Field(min_length=1)
    retrieval_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tag_library_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tag_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tag_row_count: int = Field(ge=0)

    @classmethod
    def from_manifest(cls, manifest: BundleManifest) -> CorpusProvenance:
        """Build provenance from a validated active bundle manifest.

        Args:
            manifest: The manifest of the bundle the step read.

        Returns:
            Every identity needed to reproduce the step's inputs.
        """
        return cls(
            bundle_id=manifest.bundle_id,
            corpus_source_view=manifest.corpus.source_view,
            corpus_row_count=manifest.corpus.row_count,
            corpus_sha256=manifest.corpus.content_sha256,
            document_version=manifest.document_version,
            retrieval_config_sha256=manifest.retrieval_config_sha256,
            tag_library_sha256=manifest.tags.library_sha256,
            tag_content_sha256=manifest.tags.content_sha256,
            tag_row_count=manifest.tags.row_count,
        )


class Coverage(_Envelope):
    """What population a step drew from, and what it did not return.

    ``eligible_is_exact`` exists because two step kinds otherwise put different
    quantities in one field: a ranked search knows its true eligible count,
    while a bounded structured scan knows only that the population is at least
    as large as the bound it asked for.
    """

    eligible: int = Field(ge=0)
    eligible_is_exact: bool
    examined: int | None = Field(default=None, ge=0)
    examined_absent_because: str | None = None
    returned: int = Field(ge=0)
    dropped: int = Field(ge=0)
    truncated: bool
    set_input_incomplete: bool
    truncation_source: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_arithmetic(self) -> Coverage:
        """Refuse a coverage claim that contradicts its own counts."""
        if self.truncated != (self.dropped > 0):
            raise ValueError("truncated must be exactly whether rows were dropped")
        if self.eligible_is_exact and self.returned + self.dropped > self.eligible:
            raise ValueError("returned plus dropped exceeds the eligible population")
        if self.examined is None and not self.examined_absent_because:
            raise ValueError("an unmeasured examined count must say why")
        if self.examined is not None and self.examined_absent_because:
            raise ValueError("a measured examined count must not also state a reason")
        if self.set_input_incomplete and not self.truncation_source:
            raise ValueError("an incomplete set input must name the truncating steps")
        return self


class FieldWindow(_Envelope):
    """A tournament-field cohort, with the numbers that make a rate readable."""

    status: Literal["field_window"] = "field_window"
    window_days: int = Field(ge=1)
    since: date
    min_event_size: int = Field(ge=1)
    denominator: int = Field(ge=0)
    incomplete_decks: int = Field(ge=0)

    @model_validator(mode="after")
    def check_cohort(self) -> FieldWindow:
        """Refuse an incomplete-deck count larger than the cohort."""
        if self.incomplete_decks > self.denominator:
            raise ValueError("incomplete_decks exceeds the cohort denominator")
        return self


class NoFieldEvidence(_Envelope):
    """This result states no rate, and names why it states none."""

    status: Literal["no_field_evidence"] = "no_field_evidence"
    reason: NoFieldEvidenceReason


#: Required on every card-bearing result. There is no third state in which a
#: rate is present but its denominator is not.
FieldEvidence = FieldWindow | NoFieldEvidence


class CardRow(_Envelope):
    """One Oracle card in a step result.

    Carries no price and no ownership field, and cannot acquire one: it is
    built from ``CatalogRecord``, which has neither (ADR-025).
    """

    oracle_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type_line: str
    mana_cost: str | None
    mana_value: float = Field(ge=0)
    color_identity: tuple[str, ...]
    types: tuple[str, ...]
    tags: tuple[str, ...]
    rank: int = Field(ge=1)
    stages: tuple[StageEvidence, ...] = ()
    #: ``None`` when no deck context resolved, which is not the same fact as
    #: "this card is not in the deck". A plan whose context failed to resolve
    #: would otherwise publish a false negative on every row of an
    #: otherwise-successful result.
    deck_roles: tuple[str, ...] | None = None
    in_deck: bool | None = None


class RulesRow(_Envelope):
    """One retrieved reference chunk and the citation that identifies it."""

    citation: str = Field(min_length=1)
    document: str = Field(min_length=1)
    section: str | None
    tier: int | None
    content: str = Field(min_length=1)
    similarity: float
    rank: int = Field(ge=1)


class ProfileFacetValue(_Envelope):
    """One deterministic fact about the bound deck context."""

    facet: str = Field(min_length=1)
    labels: tuple[str, ...] = ()
    counts: dict[str, int] = Field(default_factory=dict)
    oracle_ids: tuple[str, ...] = ()
    note: str = ""


class StepResult(_Envelope):
    """A step that ran, with everything needed to read its output honestly."""

    schema_version: Literal["research-step-result.v1"] = ENVELOPE_SCHEMA_VERSION
    status: Literal["ok"] = "ok"
    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    tier: AssertionTier
    ordering: ResultOrdering
    provenance: CorpusProvenance
    coverage: Coverage
    field: FieldEvidence
    availability: RetrievalAvailability
    cards: tuple[CardRow, ...] = ()
    rules: tuple[RulesRow, ...] = ()
    facets: tuple[ProfileFacetValue, ...] = ()
    notices: tuple[str, ...] = ()
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def check_counts(self) -> StepResult:
        """Refuse a coverage count that disagrees with the rows carried."""
        rows = len(self.cards) + len(self.rules)
        if self.facets and not self.cards:
            # A profile step may report facets whose value is a count rather
            # than a row set; its coverage counts the facet population.
            return self
        if self.coverage.returned != rows:
            raise ValueError("coverage.returned disagrees with the rows returned")
        return self

    @property
    def oracle_ids(self) -> tuple[str, ...]:
        """Return this result's card identities in result order."""
        return tuple(card.oracle_id for card in self.cards)


class StepNotRun(_Envelope):
    """No step output happened, and this is why."""

    schema_version: Literal["research-step-result.v1"] = ENVELOPE_SCHEMA_VERSION
    status: Literal["not_run"] = "not_run"
    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    reason: StepNotRunReason
    detail: str = ""
    provenance: CorpusProvenance | None = None

    @property
    def oracle_ids(self) -> tuple[str, ...]:
        """Return no card identities, on the same accessor as a result.

        A renderer must not have to branch on ``hasattr`` to find out whether
        a step produced cards.
        """
        return ()


#: What every step handler returns.
StepOutcome = StepResult | StepNotRun


class StatedAbsenceRecord(_Envelope):
    """One absence a plan declared, as executed."""

    reason: str = Field(min_length=1)
    detail: str = Field(min_length=8)
    answers_expectation: int | None = None


class PlanRun(_Envelope):
    """One executed plan: every step outcome, plus the designated answer."""

    schema_version: Literal["research-plan-run.v1"] = PLAN_RUN_SCHEMA_VERSION
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_id: str | None = None
    intent: str = Field(min_length=1)
    context_id: str | None = None
    steps: tuple[StepOutcome, ...] = ()
    answer_step: str | None = None
    clarification_required: bool = False
    stated_absences: tuple[StatedAbsenceRecord, ...] = ()
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def check_answer_step(self) -> PlanRun:
        """Refuse an answer that names a step the run does not contain."""
        if self.answer_step is None:
            return self
        if self.answer_step not in {step.step_id for step in self.steps}:
            raise ValueError(f"answer_step {self.answer_step!r} is not in this run")
        return self

    @property
    def answer(self) -> StepOutcome | None:
        """Return the outcome the plan designated as its answer."""
        if self.answer_step is None:
            return None
        for step in self.steps:
            if step.step_id == self.answer_step:
                return step
        return None

    @property
    def returned_oracle_ids(self) -> tuple[str, ...]:
        """Return the answer step's card identities, in result order."""
        answer = self.answer
        return () if answer is None else answer.oracle_ids

    @property
    def result_set_oracle_ids(self) -> tuple[str, ...]:
        """Return every card identity any step in this run produced.

        This is the enclosure ADR-030 forbids a narrator to escape: a card the
        assistant names must appear here.
        """
        seen: dict[str, None] = {}
        for step in self.steps:
            for oracle_id in step.oracle_ids:
                seen.setdefault(oracle_id, None)
        return tuple(seen)

    @property
    def answer_ordering(self) -> ResultOrdering | None:
        """Return how the answer step ordered its rows, if it ran."""
        answer = self.answer
        return answer.ordering if isinstance(answer, StepResult) else None
