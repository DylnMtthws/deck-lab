"""Run hand-written plans and turn each run into one structured observation.

Two things here are easy to get subtly wrong, so both are spelled out.

**Id-space translation is positional.** The golden questions carry the cEDH
fixture's uuid5 ids; a full corpus carries canonical Oracle ids. Translation
therefore maps canonical results back into label space **without dropping
anything**: an unmapped corpus card becomes a ``corpus:<id>`` sentinel and keeps
its rank. Filtering unmapped ids out instead would let a card ranked 200th
survive into a top-50 window and inflate recall.

**Narrator fields are zero because there is no narrator.** ``assertion_count``,
``cited_assertion_count``, ``bare_rate_count``, ``named_oracle_ids``,
``finding_count`` and ``cost_usd`` are all honestly zero in R3. That makes the
three structural invariants in :func:`correctness_scorecard` report
``not_measured`` over zero assertions, which is correct and is exactly what R0
built them to do. The G2 scorecard says so explicitly rather than letting a
reader infer that three invariants passed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant.envelope import PlanRun, StepNotRun
from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    GoldenQuestion,
    GoldenQuestionSet,
)
from sabermetrics.assistant.eval.plans import HandWrittenPlan, HandWrittenPlanSet
from sabermetrics.assistant.executor import ResearchExecutor
from sabermetrics.assistant.ir import AbsenceReason

ROOT = Path(__file__).resolve().parents[4]
#: Where the fixture-to-corpus id bijection lives.
ID_MAP_PATH = ROOT / "fixtures" / "research" / "deck_context_id_map.json"
#: The artefact contract :mod:`scripts.map_deck_context_ids` writes.
ID_MAP_SCHEMA = "research-deck-context-id-map.v1"

#: Absences that are true because the engine has no such field, or because a
#: published contract does not carry the fact, or because the product does not
#: do it. Each is checkable by reading the code or the schema, so stating one
#: is a claim about the system rather than about this run.
STRUCTURAL_ABSENCES: frozenset[str] = frozenset(
    {
        "price_is_not_in_the_engine",
        "collection_is_not_in_the_engine",
        "popularity_is_not_quality",
        "opponent_behaviour_is_not_modelled",
        "personal_game_logs_are_not_ingested",
        "corpus_records_past_events_not_future_pods",
        "simulator_contract_has_no_per_card_identity",
        "simulator_contract_requires_a_complete_list",
        "simulator_measures_assembly_not_win_rate",
        "card_presence_does_not_establish_a_simulator_state",
        "card_presence_does_not_establish_sufficiency",
        "unauthored_card_behaviour_is_not_measurable",
        "ask_has_no_deck_generation_tool",
        "model_may_not_create_simulator_mechanics",
        "commander_is_not_supported_by_a_pack",
    }
)

#: Absences that are true only because a capability is not built yet. Each maps
#: to what the run must actually show for the claim to be earned.
CAPABILITY_ABSENCES: Mapping[str, str] = {
    "field_statistics_deferred_to_r5": "field_stats",
    "combo_corpus_deferred_to_d4": "combo_lookup",
    "simulator_study_deferred_to_r6": "sim_study",
    "rules_index_not_built": "rules_lookup",
}


#: Field bases a plan may honestly declare in R3. The two cohort reasons
#: describe a MetaRepository that answered and found too little; R3 binds no
#: meta repository at all, so a plan claiming either would be describing a
#: measurement that never happened.
EARNABLE_FIELD_ABSENCES: frozenset[str] = frozenset(
    {"not_a_field_query", "field_statistics_deferred_to_r5"}
)


class UnearnedAbsenceError(RuntimeError):
    """A plan declared an absence the run does not support."""


class UnearnedFieldBasisError(RuntimeError):
    """A plan declared a field basis this phase cannot have observed."""


class IdMapError(RuntimeError):
    """The fixture-to-corpus id map is absent, foreign, or malformed."""


class LabelIdMap(BaseModel):
    """A bijection between golden-label ids and executing-corpus ids."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    status: str
    bundle_id: str = ""
    corpus_sha256: str = ""
    label_to_canonical: dict[str, str] = Field(default_factory=dict)
    canonical_to_label: dict[str, str] = Field(default_factory=dict)
    names_by_label_id: dict[str, str] = Field(default_factory=dict)

    @property
    def is_identity(self) -> bool:
        """Return whether label ids and corpus ids are the same space."""
        return not self.label_to_canonical

    def sha256(self) -> str:
        """Hash the map canonically, review status included."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_label_space(self, oracle_ids: Sequence[str]) -> list[str]:
        """Translate corpus ids into label space, preserving rank.

        Args:
            oracle_ids: Ids in result order.

        Returns:
            The same number of entries in the same order. An id the map does
            not cover becomes ``corpus:<id>``, so nothing is ever removed and
            a lower-ranked card cannot be promoted into a top-k window.
        """
        if self.is_identity:
            return list(oracle_ids)
        return [
            self.canonical_to_label.get(oracle_id, f"corpus:{oracle_id}")
            for oracle_id in oracle_ids
        ]

    def resolve(self, label_ids: Sequence[str]) -> list[str]:
        """Translate label ids into corpus ids, dropping nothing.

        Args:
            label_ids: Ids as the golden questions record them.

        Returns:
            One entry per input; an uncovered id becomes ``unmapped:<id>``.
        """
        if self.is_identity:
            return list(label_ids)
        return [
            self.label_to_canonical.get(label_id, f"unmapped:{label_id}")
            for label_id in label_ids
        ]


def identity_id_map() -> LabelIdMap:
    """Return the map to use when labels and corpus share one id space."""
    return LabelIdMap(schema_version=ID_MAP_SCHEMA, status="identity")


def load_label_id_map(path: Path = ID_MAP_PATH) -> LabelIdMap:
    """Load the checked-in fixture-to-corpus id map.

    Args:
        path: The JSON artefact. Defaults to the checked-in map.

    Returns:
        The validated map.

    Raises:
        IdMapError: If the artefact is absent, foreign, or not a bijection.
    """
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IdMapError(f"id map is absent: {path}") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != ID_MAP_SCHEMA:
        raise IdMapError(f"id map is not a {ID_MAP_SCHEMA} document: {path}")
    label_to_canonical: dict[str, str] = {}
    names: dict[str, str] = {}
    for entry in raw.get("entries") or ():
        label = str(entry["fixture_oracle_id"])
        canonical = str(entry["canonical_oracle_id"])
        if label in label_to_canonical:
            raise IdMapError(f"id map maps {label} twice")
        label_to_canonical[label] = canonical
        names[label] = str(entry["name"])
    canonical_to_label = {value: key for key, value in label_to_canonical.items()}
    if len(canonical_to_label) != len(label_to_canonical):
        raise IdMapError("id map is not a bijection: two labels share one corpus id")
    return LabelIdMap(
        schema_version=str(raw["schema_version"]),
        status=str(raw.get("status") or "unknown"),
        bundle_id=str(raw.get("bundle_id") or ""),
        corpus_sha256=str(raw.get("corpus_sha256") or ""),
        label_to_canonical=label_to_canonical,
        canonical_to_label=canonical_to_label,
        names_by_label_id=names,
    )


def run_plan_for_question(
    question: GoldenQuestion,
    hand: HandWrittenPlan,
    executor: ResearchExecutor,
    id_map: LabelIdMap,
) -> tuple[CorrectnessObservation, PlanRun]:
    """Execute one hand-written plan and observe it structurally.

    Args:
        question: The golden question the plan answers.
        hand: The hand-written plan.
        executor: A bound deterministic executor.
        id_map: Translation between corpus ids and label ids.

    Returns:
        The observation the correctness rig consumes, and the full run.

    Raises:
        UnearnedAbsenceError: If the plan declares a capability absence the run
            does not actually exhibit.
    """
    run = executor.run(hand.plan, question_id=question.id)
    _check_absences(hand, run)
    _check_field_basis(hand)
    return (
        CorrectnessObservation(
            question_id=question.id,
            returned_oracle_ids=id_map.to_label_space(run.returned_oracle_ids),
            result_set_oracle_ids=id_map.to_label_space(run.result_set_oracle_ids),
            # Every field below is a narrator artefact. R3 has no narrator, so
            # each is honestly zero rather than optimistically absent.
            named_oracle_ids=[],
            assertion_count=0,
            cited_assertion_count=0,
            bare_rate_count=0,
            absence_stated=bool(run.stated_absences),
            clarification_requested=run.clarification_required,
            finding_count=0,
            cost_usd=0.0,
            latency_ms=run.elapsed_ms,
        ),
        run,
    )


def run_all(
    questions: GoldenQuestionSet,
    plans: HandWrittenPlanSet,
    executor: ResearchExecutor,
    id_map: LabelIdMap,
) -> tuple[list[CorrectnessObservation], list[PlanRun]]:
    """Run every hand-written plan, in golden question order.

    Args:
        questions: The golden question set.
        plans: One plan per question.
        executor: A bound deterministic executor.
        id_map: Translation between corpus ids and label ids.

    Returns:
        Observations and runs, aligned and in question order.
    """
    planned = plans.by_question_id
    observations: list[CorrectnessObservation] = []
    runs: list[PlanRun] = []
    for question in questions.questions:
        hand = planned.get(question.id)
        if hand is None:
            continue
        observation, run = run_plan_for_question(question, hand, executor, id_map)
        observations.append(observation)
        runs.append(run)
    return observations, runs


def step_availability(runs: Sequence[PlanRun]) -> dict[str, bool]:
    """Report which step kinds actually produced a result in this run set.

    Args:
        runs: Every executed plan.

    Returns:
        Step kind to whether at least one instance of it ran successfully.
        A kind that appears only as an absence reports ``False``.
    """
    availability: dict[str, bool] = {}
    for run in runs:
        for step in run.steps:
            ran = not isinstance(step, StepNotRun)
            availability[step.kind] = availability.get(step.kind, False) or ran
    return dict(sorted(availability.items()))


def _check_field_basis(hand: HandWrittenPlan) -> None:
    """Refuse a field basis no R3 deployment could have established.

    ``stated_absences`` are checked against the run; ``field_absence`` was not
    checked against anything, so a plan could assert "this cohort is below the
    event-size floor" on a deployment that never queried a cohort.
    """
    for step in hand.plan.steps:
        basis = getattr(step, "field_absence", None)
        if basis is not None and basis not in EARNABLE_FIELD_ABSENCES:
            raise UnearnedFieldBasisError(
                f"{hand.question_id}: step {step.id!r} declares field basis "
                f"{basis!r}, which describes a cohort query. R3 binds no meta "
                "repository, so no step can have observed one"
            )


def _check_absences(hand: HandWrittenPlan, run: PlanRun) -> None:
    """Refuse a capability absence the run does not exhibit."""
    reasons_not_run: set[str] = {
        step.reason for step in run.steps if isinstance(step, StepNotRun)
    }
    kinds_not_run: set[str] = {
        step.kind for step in run.steps if isinstance(step, StepNotRun)
    }
    for absence in hand.plan.stated_absences:
        if not _earned(absence.reason, reasons_not_run, kinds_not_run, hand):
            raise UnearnedAbsenceError(
                f"{hand.question_id}: the plan states {absence.reason!r}, but "
                "nothing in the run or in the shipped capability set supports it"
            )


def _earned(
    reason: AbsenceReason,
    reasons_not_run: set[str],
    kinds_not_run: set[str],
    hand: HandWrittenPlan,
) -> bool:
    """Return whether one stated absence is supported by the system or the run."""
    if reason in STRUCTURAL_ABSENCES:
        return True
    capability = CAPABILITY_ABSENCES.get(reason)
    if capability is None:
        return False
    if capability == "rules_lookup":
        # Earned ONLY by a rules_lookup that actually failed. Granting it to a
        # plan that simply contains no rules_lookup would let any plan claim
        # the rules index is missing without ever asking for it, which is a
        # claim about the deployment inferred from the plan's own shape.
        return "reference_index_absent" in reasons_not_run
    # field_stats, combo_lookup and sim_study are not implemented, so a plan
    # cannot contain one: the IR refuses the kind outright. The absence is
    # earned exactly because the capability is absent from the closed set.
    return capability not in {step.kind for step in hand.plan.steps}
