"""Gate G2: what fraction of golden questions a hand-written plan answers.

``correctness_scorecard`` reports the **mean** of per-question recall. G2 asks a
different question — *how many questions were fully answered* — and the two
numbers can disagree completely: a run where every question finds three of its
four required cards scores 0.75 mean recall with **zero** questions answered.
This module computes the count, and embeds the existing scorecard unchanged
beside it rather than editing an R0 contract.

Four choices here are deliberate and each is published on the scorecard rather
than settled in a commit message.

**One recall window, both halves.** ``required`` and ``forbidden`` are checked
over the same slice. The embedded ``correctness_scorecard`` checks forbidden
over the untruncated list, so ``forbidden_hit_anywhere`` is reported alongside
and the fix cannot read as a loosening.

**A window only applies to a ranking.** A ``tag_filter`` returns cards in
Oracle-id order. Slicing that at fifty measures the alphabet, so a non-ranked
answer is scored over its whole returned set and the scorecard says which
window each question used.

**The denominator is named, and what it excludes is listed.** Thirty-three of
the eighty questions carry no ``required_oracle_ids``; ``set() <= anything`` is
true, so counting them would hand G2 a free 41%, and counting them as failures
would treat an undefined metric as a failure. They are named instead.

**A gate nothing measured says so.** With no narrator, ``finding_count`` is
structurally zero, so 0/6 manufactured findings is arithmetic and not evidence.
It is reported ``not_measured``, and every key of the embedded scorecard's
``quality`` block carries an explicit measurement status so the numeric reading
and the honest reading cannot diverge inside one document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant.envelope import PlanRun, StepNotRun, StepResult
from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    GoldenQuestion,
    GoldenQuestionSet,
)
from sabermetrics.assistant.eval.plans import (
    HandWrittenPlanSet,
    mentions_card_name,
    plans_naming_the_answer,
)
from sabermetrics.assistant.eval.runner import LabelIdMap, step_availability
from sabermetrics.assistant.eval.scoring import correctness_scorecard
from sabermetrics.assistant.ir import DEFERRED_STEP_KINDS
from sabermetrics.substrate.evaluation import (
    MINIMUM_FULL_CORPUS_ROWS,
    PRODUCTION_CARD_VIEW,
)
from sabermetrics.substrate.settings import ResearchSettings

#: The scorecard contract this module emits.
G2_SCORECARD_SCHEMA = "research-g2-scorecard.v1"
#: The spec's G2 target: at least this fraction of applicable questions fully
#: answered, with no forbidden card returned.
G2_TARGET_PASS_RATE = 0.80
#: Below this, the spec says stop: the tool vocabulary is wrong, and a planner
#: would obscure that rather than repair it.
G2_STOP_PASS_RATE = 0.70

#: Measurement status for one reported gate.
GateStatus = Literal["measured", "declared_not_measured", "not_measured"]


class G2InputError(RuntimeError):
    """The requested evaluation is not eligible to be called G2."""


class G2Run(BaseModel):
    """Identity of the corpus and models one G2 run executed against."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_id: str = Field(min_length=1)
    corpus_source_view: str = Field(min_length=1)
    corpus_row_count: int = Field(ge=0)
    corpus_sha256: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    reranker_model_id: str = Field(min_length=1)
    reranker_revision: str = Field(min_length=1)

    @property
    def full_corpus(self) -> bool:
        """Return whether the corpus is large enough for a production claim."""
        return self.corpus_row_count >= MINIMUM_FULL_CORPUS_ROWS


def g2_scorecard(
    question_set: GoldenQuestionSet,
    observations: Sequence[CorrectnessObservation],
    runs: Sequence[PlanRun],
    *,
    plans: HandWrittenPlanSet,
    id_map: LabelIdMap,
    run: G2Run,
    authoritative: bool = False,
    recall_k: int = 50,
) -> dict[str, Any]:
    """Score one complete R3 evaluation run.

    Args:
        question_set: The golden questions.
        observations: One structured observation per executed plan.
        runs: The executed plans, aligned with ``observations``.
        plans: The hand-written plan artefact.
        id_map: The id-space translation used to build the observations.
        run: Corpus and model identity for this run.
        authoritative: Whether the caller has already cleared every
            precondition in :func:`authoritative_g2`.
        recall_k: The ranked window. Applied only to ranked answers.

    Returns:
        A JSON-serializable scorecard.

    Raises:
        G2InputError: If the observations do not correspond to the questions.
    """
    by_id = {question.id: question for question in question_set.questions}
    rows = {row.question_id: row for row in observations}
    runs_by_id = {r.question_id: r for r in runs if r.question_id is not None}
    unknown = sorted(set(rows) - set(by_id))
    if unknown:
        raise G2InputError(f"observations reference unknown questions: {unknown}")

    retrieval = _retrieval_gate(by_id, rows, runs_by_id, recall_k)
    absence = _absence_gate(by_id, runs_by_id)
    clarification = _clarification_gate(by_id, rows)
    no_finding = _no_finding_gate(by_id)
    composite = _composite_gate(by_id, (retrieval, absence, clarification), no_finding)

    leaked = plans_naming_the_answer(plans, question_set, id_map.names_by_label_id)
    unnamed = _unnamed_label_ids(question_set, id_map)
    name_leaked = _asks_that_name_their_own_answer(question_set, id_map)
    discovery = _discovery_gate(by_id, retrieval, name_leaked)
    embedded = correctness_scorecard(
        question_set, observations, mode="substrate", recall_k=recall_k
    )
    pass_rate = float(retrieval["pass_rate"])
    # Only an authoritative run gets a `status`, because `status` is what a
    # gate reads. A measured run against a development bundle publishes its
    # number and says plainly that the number is not the gate — the same shape
    # `score_g1` uses, where the threshold lives in `authoritative_g1` alone.
    status = (
        _status(pass_rate, retrieval["applicable"])
        if authoritative
        else "not_authoritative"
    )
    return {
        "schema_version": G2_SCORECARD_SCHEMA,
        "authoritative": authoritative,
        "status": status,
        "measured_pass_rate": pass_rate,
        "target_pass_rate": G2_TARGET_PASS_RATE,
        "stop_pass_rate": G2_STOP_PASS_RATE,
        "recall_k": recall_k,
        "questions_total": len(question_set.questions),
        "questions_evaluated": len(rows),
        "gates": {
            "retrieval": retrieval,
            "discovery": discovery,
            "absence": absence,
            "clarification": clarification,
            "no_finding": no_finding,
            "composite": composite,
        },
        "subsets": {
            "plans_naming_a_card_the_ask_does_not": {
                # A count of zero means two very different things: nothing
                # leaked, or nothing could be checked because the id map has no
                # names for these labels. The status says which.
                "status": "not_measured" if unnamed else "measured",
                "count": len(leaked),
                "by_question": {key: list(value) for key, value in leaked.items()},
                "unnamed_label_ids": unnamed,
                "note": (
                    "a plan whose search text supplies a required card name the "
                    "asker never wrote is a lookup, not retrieval; counting it "
                    "as a retrieval success overstates the tool vocabulary. "
                    "a label the id map cannot name cannot be checked, so any "
                    "such id is listed and the subset reports not_measured"
                ),
            },
        },
        "subsets_name_lookup": {
            "status": "not_measured" if unnamed else "measured",
            "count": len(name_leaked),
            "question_ids": name_leaked,
            "passed_and_name_leaked": sorted(
                set(name_leaked) - set(retrieval["failed"])
            ),
            "note": (
                "these questions print every required card's name in their own "
                "ask, so finding them is a name lookup the asker requested "
                "rather than evidence that the substrate can find a card "
                "nobody named. they are legitimate passes and they are not "
                "retrieval evidence; a headline pass rate over a denominator "
                "containing them overstates the tool vocabulary"
            ),
        },
        "deferred": _deferred(by_id, runs_by_id),
        "step_availability": step_availability(runs),
        "provenance": {
            "bundle_id": run.bundle_id,
            "corpus_source_view": run.corpus_source_view,
            "corpus_row_count": run.corpus_row_count,
            "corpus_sha256": run.corpus_sha256,
            "full_corpus": run.full_corpus,
            "embedding_model_id": run.embedding_model_id,
            "embedding_revision": run.embedding_revision,
            "reranker_model_id": run.reranker_model_id,
            "reranker_revision": run.reranker_revision,
            "plans_sha256": plans.sha256(),
            "plans_unverified": list(plans.unverified),
            "id_map_status": id_map.status,
            "id_map_sha256": id_map.sha256(),
        },
        "correctness": embedded,
        "correctness_quality_status": _quality_status(embedded, recall_k),
        "correctness_notes": {
            "forbidden_window": (
                "the embedded scorecard checks forbidden ids over the whole "
                "returned list; gates.retrieval checks them over the same "
                "window as required, and reports both"
            ),
            "invariants": (
                "citation coverage, cards-named-outside-result-set and bare "
                "rates are all not_measured: R3 has no narrator, so there are "
                "zero assertions to measure. They are G4's, at R4"
            ),
        },
    }


def authoritative_g2(
    question_set: GoldenQuestionSet,
    observations: Sequence[CorrectnessObservation],
    runs: Sequence[PlanRun],
    *,
    plans: HandWrittenPlanSet,
    id_map: LabelIdMap,
    run: G2Run,
    settings: ResearchSettings,
    corpus_oracle_ids: set[str],
    recall_k: int = 50,
) -> dict[str, Any]:
    """Validate production evidence, then score it as G2.

    Every refusal below has the same shape as G1's: a claim is not made because
    something that would make it a claim about production is absent.

    Args:
        question_set: The golden questions.
        observations: One observation per plan.
        runs: The executed plans.
        plans: The hand-written plan artefact.
        id_map: The id-space translation used.
        run: Corpus and model identity.
        settings: The pinned retrieval configuration.
        corpus_oracle_ids: Every Oracle id in the executing corpus.
        recall_k: The ranked window.

    Returns:
        The scorecard, with ``authoritative`` true.

    Raises:
        G2InputError: If any precondition for a production claim is unmet.
    """
    contested = sorted(
        question.id for question in question_set.questions if question.contested
    )
    if contested:
        raise G2InputError(
            "authoritative G2 requires an owner-reviewed golden set; still "
            f"contested: {', '.join(contested)}"
        )
    unverified = list(plans.unverified)
    if unverified:
        raise G2InputError(
            "authoritative G2 requires owner-verified plans: " + ", ".join(unverified)
        )
    expected = {question.id for question in question_set.questions}
    observed = {row.question_id for row in observations}
    if observed != expected:
        raise G2InputError(
            "authoritative G2 requires one observation per golden question; "
            f"missing={sorted(expected - observed)}, "
            f"unknown={sorted(observed - expected)}"
        )
    if id_map.status not in {"owner_verified", "identity"}:
        raise G2InputError(
            f"authoritative G2 requires a reviewed id map; status is "
            f"{id_map.status!r}"
        )
    unnamed = _unnamed_label_ids(question_set, id_map)
    if unnamed:
        raise G2InputError(
            "authoritative G2 requires an id map that names every labelled "
            "card, or the plan-naming check silently passes: " + ", ".join(unnamed)
        )
    if run.corpus_source_view != PRODUCTION_CARD_VIEW:
        raise G2InputError(
            f"authoritative G2 requires {PRODUCTION_CARD_VIEW!r}; the active "
            f"bundle is {run.corpus_source_view!r}"
        )
    if not run.full_corpus:
        raise G2InputError(
            "authoritative G2 requires a full corpus of at least "
            f"{MINIMUM_FULL_CORPUS_ROWS} cards; this one has "
            f"{run.corpus_row_count}"
        )
    if (
        run.embedding_model_id != settings.embedding.model_id
        or run.embedding_revision != settings.embedding.revision
        or run.reranker_model_id != settings.reranker.model_id
        or run.reranker_revision != settings.reranker.revision
    ):
        raise G2InputError("authoritative G2 requires the pinned local models")
    labelled = {
        oracle_id
        for question in question_set.questions
        for oracle_id in (
            *question.required_oracle_ids,
            *question.forbidden_oracle_ids,
        )
    }
    missing = sorted(set(id_map.resolve(sorted(labelled))) - corpus_oracle_ids)
    if missing:
        raise G2InputError(
            "authoritative G2 labels do not resolve in the corpus: "
            + ", ".join(missing)
        )
    unsupported = sorted(
        run_.question_id or "?"
        for run_ in runs
        for step in run_.steps
        if isinstance(step, StepNotRun) and step.reason == "reference_index_absent"
    )
    if unsupported:
        raise G2InputError(
            "authoritative G2 cannot include a rules_lookup with no reference "
            "index: " + ", ".join(unsupported)
        )
    return g2_scorecard(
        question_set,
        observations,
        runs,
        plans=plans,
        id_map=id_map,
        run=run,
        authoritative=True,
        recall_k=recall_k,
    )


def _retrieval_gate(
    by_id: Mapping[str, GoldenQuestion],
    rows: Mapping[str, CorrectnessObservation],
    runs: Mapping[str, PlanRun],
    recall_k: int,
) -> dict[str, Any]:
    """Score the gate the spec's G2 sentence actually describes."""
    applicable = [
        question
        for question in by_id.values()
        if question.required_oracle_ids and question.id in rows
    ]
    passed: list[str] = []
    failed: list[str] = []
    windows: dict[str, str] = {}
    populations: dict[str, int] = {}
    answer_size: dict[str, int] = {}
    unranked_over_window: list[str] = []
    forbidden_anywhere: list[str] = []
    for question in applicable:
        row = rows[question.id]
        ordering = _ordering(runs.get(question.id))
        returned = row.returned_oracle_ids
        populations[question.id] = _population(runs.get(question.id))
        answer_size[question.id] = len(returned)
        required = set(question.required_oracle_ids)
        forbidden = set(question.forbidden_oracle_ids)
        if forbidden & set(returned):
            forbidden_anywhere.append(question.id)

        if ordering == "ranked":
            window = returned[:recall_k]
            windows[question.id] = f"recall@{recall_k}"
        elif len(returned) > recall_k:
            # An unranked answer has no best-first order, so there is no
            # principled way to take its top k — and scoring it whole would let
            # a plan pass by returning the entire bound deck. Returning more
            # rows than the window without a ranking is therefore not an
            # answer, and is recorded as one of these rather than scored.
            windows[question.id] = "unranked_over_window"
            unranked_over_window.append(question.id)
            failed.append(question.id)
            continue
        else:
            window = returned
            windows[question.id] = "full_returned_set"

        if required <= set(window) and not (forbidden & set(window)):
            passed.append(question.id)
        else:
            failed.append(question.id)
    not_applicable = sorted(
        question.id for question in by_id.values() if not question.required_oracle_ids
    )
    return {
        "name": "full required recall and zero forbidden",
        "status": "measured",
        "denominator_name": "questions_with_required_labels",
        "applicable": len(applicable),
        "passed": len(passed),
        "failed": sorted(failed),
        "pass_rate": len(passed) / len(applicable) if applicable else 0.0,
        "not_applicable": len(not_applicable),
        "not_applicable_ids": not_applicable,
        "forbidden_hit_anywhere": sorted(forbidden_anywhere),
        "windows": windows,
        # How large a population each answer was drawn from. A question scored
        # over a 100-card deck scope is a different test from one scored over
        # the whole corpus, and a single pass rate hides that.
        "eligible_population": populations,
        # How many rows each answer actually returned. Read beside
        # eligible_population this is the narrowing the plan achieved, which is
        # the thing a retrieval gate is supposed to be measuring.
        "answer_returned": answer_size,
        "breadth": _breadth(applicable, answer_size),
        "unranked_over_window": sorted(unranked_over_window),
        "note": (
            "a question with no required_oracle_ids has no recall to measure; "
            "it is listed, not counted as a pass and not counted as a failure. "
            "an unranked answer returning more rows than the window is counted "
            "as a failure, because a set with no best-first order cannot be "
            "truncated to a top k and returning the whole bound deck would "
            "otherwise pass without retrieving anything"
        ),
    }


def _breadth(
    applicable: Sequence[GoldenQuestion], answer_size: Mapping[str, int]
) -> dict[str, Any]:
    """Report how wide each answer was against how many cards were asked for.

    A pass says the required cards were somewhere in the returned set. It does
    not say the set was an answer: forty cards containing the right four passes
    exactly as a well-aimed four does. No threshold is applied here, because
    where "too wide" begins is a judgement — the ratios are published and the
    widest are named so a reader makes it themselves.
    """
    ratios = {
        question.id: round(
            answer_size.get(question.id, 0) / len(question.required_oracle_ids), 2
        )
        for question in applicable
        if question.required_oracle_ids
    }
    widest = sorted(ratios, key=lambda key: (-ratios[key], key))[:10]
    return {
        "returned_per_required": ratios,
        "widest": [
            {
                "question_id": key,
                "required": len(
                    next(q for q in applicable if q.id == key).required_oracle_ids
                ),
                "returned": answer_size.get(key, 0),
                "ratio": ratios[key],
            }
            for key in widest
        ],
        "note": (
            "returned rows divided by required cards. a pass over a wide set is "
            "a weaker result than a pass over a narrow one, and the gate cannot "
            "tell them apart"
        ),
    }


def _discovery_gate(
    by_id: Mapping[str, GoldenQuestion],
    retrieval: Mapping[str, Any],
    name_leaked: Sequence[str],
) -> dict[str, Any]:
    """Score only the questions whose ask does not name its own answer.

    The headline retrieval gate is the sentence the spec wrote, and it counts
    name lookups as passes because they are passes. They are not evidence that
    the substrate can find a card nobody named, which is the thing R4's planner
    will have to do, so the discovery population is scored separately rather
    than folded into one rate.
    """
    leaked = set(name_leaked)
    scored = {
        question.id for question in by_id.values() if question.required_oracle_ids
    } & (set(retrieval["failed"]) | _passed_ids(retrieval))
    applicable = sorted(scored - leaked)
    failed = sorted(set(retrieval["failed"]) & set(applicable))
    passed = [key for key in applicable if key not in set(failed)]
    return {
        "name": "retrieval over questions whose ask does not name its answer",
        "status": retrieval["status"],
        "denominator_name": "questions_requiring_discovery",
        "applicable": len(applicable),
        "passed": len(passed),
        "failed": failed,
        "pass_rate": len(passed) / len(applicable) if applicable else 0.0,
        "excluded_name_lookups": sorted(leaked & scored),
        "note": (
            "a name lookup the asker requested is a legitimate pass and is "
            "excluded here rather than discounted. this rate is the one that "
            "speaks to whether the substrate finds cards nobody named"
        ),
    }


def _passed_ids(retrieval: Mapping[str, Any]) -> set[str]:
    """Recover the passing ids from a scored retrieval gate."""
    return set(retrieval["windows"]) - set(retrieval["failed"])


def _absence_gate(
    by_id: Mapping[str, GoldenQuestion], runs: Mapping[str, PlanRun]
) -> dict[str, Any]:
    """Score whether each expected absence was answered by a stated one."""
    applicable = [
        question
        for question in by_id.values()
        if question.expected_absences and question.id in runs
    ]
    passed: list[str] = []
    failed: list[str] = []
    for question in applicable:
        answered = {
            absence.answers_expectation
            for absence in runs[question.id].stated_absences
            if absence.answers_expectation is not None
        }
        if answered >= set(range(len(question.expected_absences))):
            passed.append(question.id)
        else:
            failed.append(question.id)
    return {
        "name": "every expected absence is answered by a stated absence",
        # Declared, not observed: a person wrote the mapping from a stated
        # absence to the expectation it answers. The runner refuses an
        # unearned capability absence, which is a real check; the binding
        # itself is still authored.
        "status": "declared_not_measured",
        "denominator_name": "questions_with_expected_absences",
        "applicable": len(applicable),
        "passed": len(passed),
        "failed": sorted(failed),
        "pass_rate": len(passed) / len(applicable) if applicable else 0.0,
        "expectations_total": sum(
            len(question.expected_absences) for question in applicable
        ),
    }


def _clarification_gate(
    by_id: Mapping[str, GoldenQuestion],
    rows: Mapping[str, CorrectnessObservation],
) -> dict[str, Any]:
    """Score whether clarification was requested exactly where expected."""
    applicable = [question for question in by_id.values() if question.id in rows]
    passed = [
        question.id
        for question in applicable
        if rows[question.id].clarification_requested == question.clarification_expected
    ]
    return {
        "name": "clarification requested exactly where expected",
        # With no planner, nothing decides to ask. This copies a hand-written
        # plan's own declaration, so it measures the author, not the system.
        "status": "declared_not_measured",
        "denominator_name": "questions_evaluated",
        "applicable": len(applicable),
        "passed": len(passed),
        "failed": sorted({question.id for question in applicable} - set(passed)),
        "pass_rate": len(passed) / len(applicable) if applicable else 0.0,
    }


def _no_finding_gate(by_id: Mapping[str, GoldenQuestion]) -> dict[str, Any]:
    """Report the manufactured-finding gate as unmeasurable in R3."""
    applicable = sorted(
        question.id for question in by_id.values() if question.no_finding_expected
    )
    return {
        "name": "no manufactured finding on a healthy deck",
        "status": "not_measured",
        "denominator_name": "questions_expecting_no_finding",
        "applicable": len(applicable),
        "applicable_ids": applicable,
        "passed": 0,
        "pass_rate": 0.0,
        "note": (
            "R3 has no narrator, so finding_count is structurally zero and "
            "0 manufactured findings is arithmetic rather than evidence. This "
            "gate is measured at R4, with the narrator that could manufacture "
            "one"
        ),
    }


def _composite_gate(
    by_id: Mapping[str, GoldenQuestion],
    gates: Sequence[Mapping[str, Any]],
    no_finding: Mapping[str, Any],
) -> dict[str, Any]:
    """Score every question against whichever criteria apply to it."""
    failed: set[str] = set()
    for gate in gates:
        failed.update(gate["failed"])
    # Applicability comes from the questions, not from the gates: each gate
    # reports only its own population, and a question can belong to several.
    applicable = {
        question.id
        for question in by_id.values()
        if question.required_oracle_ids
        or question.expected_absences
        or question.clarification_expected
    }
    unmeasurable = {
        question.id
        for question in by_id.values()
        if question.id not in applicable and question.no_finding_expected
    }
    none_applicable = sorted(
        question.id
        for question in by_id.values()
        if question.id not in applicable and question.id not in unmeasurable
    )
    passed = sorted(applicable - failed)
    # A component gate can fail a question this gate does not consider
    # applicable — the clarification gate's population is every evaluated
    # question, while applicability here comes from the labels. Subtracting the
    # two would drop that failure silently, so it is reported instead.
    outside = sorted(failed - applicable)
    return {
        "name": "every applicable criterion satisfied",
        "status": "mixed",
        "denominator_name": "questions_with_at_least_one_applicable_criterion",
        "applicable": len(applicable),
        "passed": len(passed),
        "failed": sorted(applicable & failed),
        "pass_rate": len(passed) / len(applicable) if applicable else 0.0,
        "not_measurable": sorted(unmeasurable),
        "no_applicable_criterion": none_applicable,
        "failed_outside_this_denominator": outside,
        "note": (
            "two of the three component gates are declared rather than "
            f"observed, and {len(unmeasurable)} questions are scoreable only "
            "by the not_measured no-finding gate; this number is context for "
            "the retrieval gate, not a substitute for it. a component-gate "
            "failure on a question outside this denominator is listed under "
            "failed_outside_this_denominator rather than dropped"
        ),
    }


def _deferred(
    by_id: Mapping[str, GoldenQuestion], runs: Mapping[str, PlanRun]
) -> dict[str, Any]:
    """Name every question a deferred capability keeps from being answered."""
    from sabermetrics.assistant.eval.runner import CAPABILITY_ABSENCES

    owners = {
        reason: DEFERRED_STEP_KINDS.get(kind, kind)
        for reason, kind in CAPABILITY_ABSENCES.items()
    }
    by_question: dict[str, list[str]] = {}
    for question_id, run in sorted(runs.items()):
        if question_id not in by_id:
            continue
        reasons = sorted(
            {
                f"{absence.reason} -> {owners.get(absence.reason, 'R3')}"
                for absence in run.stated_absences
                if absence.reason in owners
            }
        )
        if reasons:
            by_question[question_id] = reasons
    return {
        "count": len(by_question),
        "by_question": by_question,
        "note": (
            "a deferred capability is named with the phase that owns it; it is "
            "never silently dropped from a denominator"
        ),
    }


def _asks_that_name_their_own_answer(
    question_set: GoldenQuestionSet, id_map: LabelIdMap
) -> list[str]:
    """Return questions whose ask already names every card they require."""
    out: list[str] = []
    for question in question_set.questions:
        if not question.required_oracle_ids:
            continue
        names = [
            id_map.names_by_label_id.get(oracle_id)
            for oracle_id in question.required_oracle_ids
        ]
        if not all(names):
            continue
        asked = f"{question.ask}\n{question.clarified_ask}"
        if all(mentions_card_name(asked, name) for name in names if name):
            out.append(question.id)
    return sorted(out)


def _unnamed_label_ids(
    question_set: GoldenQuestionSet, id_map: LabelIdMap
) -> list[str]:
    """Return every labelled Oracle id the id map cannot resolve to a name.

    The name-leak check can only look for names it has. Without this, a map
    with an empty name table reports a clean zero over a check that never ran.
    """
    labelled = {
        oracle_id
        for question in question_set.questions
        for oracle_id in (
            *question.required_oracle_ids,
            *question.forbidden_oracle_ids,
        )
    }
    return sorted(labelled - set(id_map.names_by_label_id))


def _quality_status(
    embedded: Mapping[str, Any], recall_k: int
) -> dict[str, GateStatus]:
    """Declare a measurement status for every embedded quality number."""
    statuses: dict[str, GateStatus] = {
        f"required_recall_at_{recall_k}": "measured",
        "forbidden_oracle_id_hit_rate": "measured",
        "absence_stated_rate": "declared_not_measured",
        "clarification_appropriateness": "declared_not_measured",
        "manufactured_finding_rate": "not_measured",
    }
    quality = embedded.get("quality") or {}
    return {key: statuses.get(key, "not_measured") for key in sorted(quality)}


def _population(run: PlanRun | None) -> int:
    """Return the largest population any step of this plan searched.

    The answer step's own ``eligible`` is the wrong number for a set
    operation: a union over three 50-row results reports about 150, which says
    nothing about whether the haystack was a 100-card deck or the whole
    corpus. The maximum across the run answers the question a reader is
    actually asking.
    """
    if run is None:
        return 0
    return max(
        (step.coverage.eligible for step in run.steps if isinstance(step, StepResult)),
        default=0,
    )


def _ordering(run: PlanRun | None) -> str:
    """Return how the answer step ordered its rows, or ``"none"``."""
    if run is None:
        return "none"
    answer = run.answer
    return answer.ordering if isinstance(answer, StepResult) else "none"


def _status(pass_rate: float, applicable: int) -> str:
    """Apply the spec's two thresholds to the measured retrieval gate."""
    if applicable == 0:
        return "not_measured"
    if pass_rate < G2_STOP_PASS_RATE:
        return "stop_and_fix"
    if pass_rate < G2_TARGET_PASS_RATE:
        return "fail_target"
    return "pass"
