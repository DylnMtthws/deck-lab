"""The Query IR: a versioned, typed DAG of bounded deterministic operations.

This is the interface the whole design rests on. A planner — a model, from R4;
a person, in R3 — emits a :class:`ResearchPlan`. The executor validates it and
runs it. The plan never contains SQL, never contains a ranking, and never
contains a card identity.

That last rule is load-bearing and is enforced here rather than described:
:func:`validate_plan` refuses any plan containing an Oracle-id literal, and
refuses any plan that populates ``allowed_oracle_ids``. Restricting a search to
a deck is expressed as ``scope="deck"``; the *executor* materializes the ids
from the resolved context. Without that rule a hand-written plan could score
perfect recall by naming its own answer, and G2 would measure nothing.

R3 ships a reduced step set. A kind that is planned but not yet built raises a
distinct error naming the phase that owns it, so "not built yet" and "not a
thing" never read the same.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sabermetrics.assistant.envelope import NoFieldEvidenceReason
from sabermetrics.mechanics.tags.registry import ALL_TAGS
from sabermetrics.substrate.catalog import compile_fts_query
from sabermetrics.substrate.models import CardFilters, CardSearchQuery

#: The plan contract this module reads and writes.
PLAN_SCHEMA_VERSION: Literal["research-plan.v1"] = "research-plan.v1"

#: Every step kind the R3 executor implements.
StepKind = Literal[
    "card_search",
    "tag_filter",
    "rules_lookup",
    "deck_profile",
    "union",
    "intersect",
    "difference",
]

#: Step kinds the IR knows about and R3 does not implement, each mapped to the
#: phase that owns it. Naming the owner is the difference between a deferral
#: and a silent truncation.
DEFERRED_STEP_KINDS: Mapping[str, str] = {
    "field_stats": "R5 (blocked on the D1 corpus census)",
    "sim_study": "R6 (blocked on the Track B simulator study)",
    "combo_lookup": "D4 (the Commander Spellbook corpus is not ingested)",
    "deck_diff": "R5 (deck comparison arrives with Analyze deck)",
}

#: Where a card-producing step draws its population from.
StepScope = Literal["corpus", "deck"]

#: Deterministic facts a ``deck_profile`` may read off the bound context.
#:
#: There is deliberately no simulator facet. Reading a saved
#: ``cedh-simulation-result.v3`` document without going through the simulator
#: client loses the deck-identity check and the ``fixture:`` marker, so a
#: development fixture would render as a measurement of the deck in front of
#: it. ``sim_study`` is R6's, with the honesty envelope attached.
DeckFacet = Literal[
    "card_list",
    "commander",
    "primary_win_package",
    "secondary_win_packages",
    "roles",
    "role_targets",
    "auto_include",
]

#: Why an answer is being withheld. A closed set, so a plan cannot invent a
#: justification, and every member is either a charter rule, a published
#: contract, or a named deferral.
AbsenceReason = Literal[
    # Charter absences: the engine has no such field to read.
    "price_is_not_in_the_engine",
    "collection_is_not_in_the_engine",
    "popularity_is_not_quality",
    "opponent_behaviour_is_not_modelled",
    "personal_game_logs_are_not_ingested",
    "corpus_records_past_events_not_future_pods",
    # Contract absences: a published schema does not carry the fact.
    "simulator_contract_has_no_per_card_identity",
    "simulator_contract_requires_a_complete_list",
    "simulator_measures_assembly_not_win_rate",
    "card_presence_does_not_establish_a_simulator_state",
    "card_presence_does_not_establish_sufficiency",
    "unauthored_card_behaviour_is_not_measurable",
    # Capability absences: the capability exists in the plan and not yet here.
    "field_statistics_deferred_to_r5",
    "combo_corpus_deferred_to_d4",
    "simulator_study_deferred_to_r6",
    "rules_index_not_built",
    # Scope absences: the product does not do this.
    "ask_has_no_deck_generation_tool",
    "model_may_not_create_simulator_mechanics",
    "commander_is_not_supported_by_a_pack",
]

_ORACLE_ID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_STEP_ID = r"^[a-z][a-z0-9_]{1,31}$"
_TAG_IDS = frozenset(definition.id for definition in ALL_TAGS)
_SET_OPS = frozenset({"union", "intersect", "difference"})
_CARD_PRODUCING = frozenset(
    {"card_search", "tag_filter", "deck_profile", "union", "intersect", "difference"}
)


class PlanValidationError(RuntimeError):
    """A plan is not executable, and this names the reason."""


class UnknownStepKindError(PlanValidationError):
    """A step names an operation outside the closed vocabulary."""


class DeferredStepKindError(PlanValidationError):
    """A step names a real operation that a later phase owns."""


class DanglingInputError(PlanValidationError):
    """A set operation names a step that is not declared before it."""


class PlanNamesACardError(PlanValidationError):
    """A plan contains a card identity, which only the executor may produce."""


class UnknownTagError(PlanValidationError):
    """A step names a mechanic tag outside the shipped library."""


class QueryTooLongError(PlanValidationError):
    """A search query exceeds the lexical stage's bounded token budget."""


class _Model(BaseModel):
    """Strict, frozen base for every plan model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StatedAbsence(_Model):
    """One thing this plan declines to answer, and why."""

    reason: AbsenceReason
    detail: str = Field(min_length=8, max_length=400)
    #: Index into the golden question's ``expected_absences``, when the plan is
    #: answering one. ``None`` for a plan written outside the eval harness.
    answers_expectation: int | None = Field(default=None, ge=0)


class _Step(_Model):
    """Fields every step carries."""

    id: str = Field(pattern=_STEP_ID)
    note: str = Field(default="", max_length=400)


class CardSearchStep(_Step):
    """Ranked hybrid retrieval over a structurally narrowed population."""

    kind: Literal["card_search"] = "card_search"
    query: CardSearchQuery
    scope: StepScope = "corpus"
    #: Authored, not inferred. A handler cannot know whether the asker wanted a
    #: rate, so the claim "this is not a field query" is the plan's to make.
    field_absence: NoFieldEvidenceReason


class TagFilterStep(_Step):
    """Unranked structured selection over mechanic tags and card facts."""

    kind: Literal["tag_filter"] = "tag_filter"
    filters: CardFilters
    scope: StepScope = "corpus"
    limit: int = Field(default=200, ge=1, le=500)
    field_absence: NoFieldEvidenceReason

    @model_validator(mode="after")
    def require_a_tag_or_predicate(self) -> TagFilterStep:
        """Refuse a tag filter that constrains nothing."""
        if self.filters == CardFilters():
            raise ValueError("a tag filter needs at least one predicate")
        return self


class RulesLookupStep(_Step):
    """Retrieval over the reference layer's active embedding generation."""

    kind: Literal["rules_lookup"] = "rules_lookup"
    question: str = Field(min_length=8, max_length=500)
    tier_filter: tuple[int, ...] = ()
    document_filter: tuple[str, ...] = ()
    limit: int = Field(default=5, ge=1, le=50)


class DeckProfileStep(_Step):
    """Deterministic facts about the deck context bound to this plan."""

    kind: Literal["deck_profile"] = "deck_profile"
    facets: tuple[DeckFacet, ...] = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=500)
    field_absence: NoFieldEvidenceReason = "field_statistics_deferred_to_r5"

    @model_validator(mode="after")
    def unique_facets(self) -> DeckProfileStep:
        """Refuse a repeated facet."""
        if len(set(self.facets)) != len(self.facets):
            raise ValueError("facets contains duplicates")
        return self


class UnionStep(_Step):
    """Every card any input produced."""

    kind: Literal["union"] = "union"
    inputs: tuple[str, ...] = Field(min_length=2)


class IntersectStep(_Step):
    """Only cards every input produced."""

    kind: Literal["intersect"] = "intersect"
    inputs: tuple[str, ...] = Field(min_length=2)


class DifferenceStep(_Step):
    """Cards the left input produced and the right input did not.

    Ordered rather than a tuple, because difference is not commutative and a
    two-element tuple would let a plan express the wrong one by accident.
    """

    kind: Literal["difference"] = "difference"
    left: str = Field(pattern=_STEP_ID)
    right: str = Field(pattern=_STEP_ID)


PlanStep = Annotated[
    CardSearchStep
    | TagFilterStep
    | RulesLookupStep
    | DeckProfileStep
    | UnionStep
    | IntersectStep
    | DifferenceStep,
    Field(discriminator="kind"),
]


class ResearchPlan(_Model):
    """A versioned, typed, inspectable query over the deterministic substrate.

    A zero-step plan is legal and common: "this needs clarification" and "this
    is a stated absence" are both answers, and both are represented as plans so
    the executor has exactly one input type. What is not legal is a zero-step
    plan that declares neither, which would be an empty result reading as an
    answer.
    """

    schema_version: Literal["research-plan.v1"] = PLAN_SCHEMA_VERSION
    intent: str = Field(min_length=8, max_length=600)
    context_id: str | None = None
    steps: tuple[PlanStep, ...] = ()
    answer_step: str | None = None
    clarification_required: bool = False
    stated_absences: tuple[StatedAbsence, ...] = ()

    @model_validator(mode="after")
    def check_plan(self) -> ResearchPlan:
        """Apply every structural rule that does not need a corpus."""
        _check_structure(self)
        return self

    @property
    def step_ids(self) -> tuple[str, ...]:
        """Return every step id in declaration order."""
        return tuple(step.id for step in self.steps)

    def step(self, step_id: str) -> PlanStep:
        """Return the step with ``step_id``.

        Args:
            step_id: A declared step identifier.

        Returns:
            The matching step.

        Raises:
            KeyError: If no step declares that id.
        """
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    def sha256(self) -> str:
        """Hash the plan canonically, so an identical plan is a cache hit."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_structure(plan: ResearchPlan) -> None:
    """Raise the specific :class:`PlanValidationError` a plan violates."""
    ids = plan.step_ids
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise PlanValidationError(f"duplicate step ids: {', '.join(duplicates)}")

    if not plan.steps:
        if not plan.clarification_required and not plan.stated_absences:
            raise PlanValidationError(
                "a plan with no steps must require clarification or state an absence"
            )
        if plan.answer_step is not None:
            raise PlanValidationError("a plan with no steps cannot name an answer step")
    elif plan.answer_step is None:
        raise PlanValidationError("a plan with steps must name its answer step")
    elif plan.answer_step not in ids:
        raise DanglingInputError(
            f"answer_step {plan.answer_step!r} is not a declared step"
        )

    if plan.clarification_required and plan.steps:
        raise PlanValidationError(
            "a plan that requires clarification must not also run steps"
        )

    declared: set[str] = set()
    for step in plan.steps:
        for reference in _inputs_of(step):
            if reference not in declared:
                raise DanglingInputError(
                    f"step {step.id!r} names {reference!r}, "
                    "which is not declared before it"
                )
            kind = plan.step(reference).kind
            if kind not in _CARD_PRODUCING:
                raise PlanValidationError(
                    f"step {step.id!r} consumes {reference!r}, "
                    f"which is a {kind} and produces no cards"
                )
        _check_step(plan, step)
        declared.add(step.id)

    if _ORACLE_ID.search(_scannable_text(plan)):
        raise PlanNamesACardError(
            "a plan may not contain an Oracle id; "
            "restrict a search to a deck with scope='deck' instead"
        )

    seen_expectations = [
        absence.answers_expectation
        for absence in plan.stated_absences
        if absence.answers_expectation is not None
    ]
    if len(set(seen_expectations)) != len(seen_expectations):
        raise PlanValidationError("two absences answer the same expectation")


def _check_step(plan: ResearchPlan, step: PlanStep) -> None:
    """Validate one step against the shipped vocabulary and the plan context."""
    scope = getattr(step, "scope", "corpus")
    if scope == "deck" and plan.context_id is None:
        raise PlanValidationError(
            f"step {step.id!r} is deck-scoped but the plan binds no context"
        )
    if isinstance(step, DeckProfileStep) and plan.context_id is None:
        raise PlanValidationError(
            f"step {step.id!r} profiles a deck but the plan binds no context"
        )

    filters: CardFilters | None = None
    if isinstance(step, CardSearchStep):
        filters = step.query.filters
    elif isinstance(step, TagFilterStep):
        filters = step.filters
    if isinstance(step, CardSearchStep):
        # Compile the query the way the lexical stage will, so every bound it
        # enforces — token count and token length alike — is checked once, here,
        # and the two cannot drift apart. Refused at authoring time so an
        # over-long query fails when it is written rather than inside a step
        # handler, where the only options are a crash or an unhelpful absence.
        try:
            compile_fts_query(step.query.text)
        except ValueError as exc:
            raise QueryTooLongError(f"step {step.id!r}: {exc}") from exc
    if filters is None:
        return
    if filters.allowed_oracle_ids:
        raise PlanNamesACardError(
            f"step {step.id!r} sets allowed_oracle_ids; "
            "only the executor may restrict a search to specific cards"
        )
    unknown = sorted(
        set(filters.required_tags + filters.any_tags + filters.excluded_tags) - _TAG_IDS
    )
    if unknown:
        raise UnknownTagError(
            f"step {step.id!r} names unshipped mechanic tags: {', '.join(unknown)}"
        )


def _inputs_of(step: PlanStep) -> tuple[str, ...]:
    """Return the step ids one step consumes."""
    if isinstance(step, UnionStep | IntersectStep):
        return step.inputs
    if isinstance(step, DifferenceStep):
        return (step.left, step.right)
    return ()


def _scannable_text(plan: ResearchPlan) -> str:
    """Return every string a plan carries, for the Oracle-id scan."""
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=True)


def validate_plan(plan: ResearchPlan) -> None:
    """Re-run structural validation on an already constructed plan.

    Construction validates, so this exists for callers holding a plan they did
    not build and want to check explicitly.

    Args:
        plan: The plan to check.

    Raises:
        PlanValidationError: If any structural rule is violated.
    """
    _check_structure(plan)


def parse_plan(raw: object) -> ResearchPlan:
    """Validate untrusted plan data into a :class:`ResearchPlan`.

    Args:
        raw: A mapping, typically decoded from YAML or a model response.

    Returns:
        The validated plan.

    Raises:
        UnknownStepKindError: If a step names an operation that does not exist.
        DeferredStepKindError: If a step names an operation a later phase owns.
        PlanValidationError: If the plan is otherwise not executable.
    """
    if isinstance(raw, Mapping):
        _check_step_kinds(raw.get("steps"))
    try:
        return ResearchPlan.model_validate(raw)
    except PlanValidationError:
        raise
    except Exception as exc:  # pydantic ValidationError and friends
        raise PlanValidationError(str(exc)) from exc


def _check_step_kinds(steps: object) -> None:
    """Refuse unknown and deferred step kinds before schema validation.

    Pydantic reports an unknown discriminator as one of several union
    mismatches. A planner needs to be told which of two different things went
    wrong, so the kind is checked first.
    """
    if not isinstance(steps, list | tuple):
        return
    known = set(get_args(StepKind))
    for entry in steps:
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind in known:
            continue
        owner = DEFERRED_STEP_KINDS.get(kind)
        if owner is not None:
            raise DeferredStepKindError(
                f"step kind {kind!r} is not implemented in R3; it belongs to {owner}"
            )
        raise UnknownStepKindError(
            f"unknown step kind {kind!r}; the closed set is " + ", ".join(sorted(known))
        )


def plan_from_dict(payload: dict[str, Any]) -> ResearchPlan:
    """Validate one plan mapping. Alias of :func:`parse_plan` for readability.

    Args:
        payload: A decoded plan mapping.

    Returns:
        The validated plan.
    """
    return parse_plan(payload)
