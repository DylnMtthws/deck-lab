"""The hand-written plan artefact, and the checks that keep it honest.

R3's real deliverable is one hand-written :class:`~sabermetrics.assistant.ir.ResearchPlan`
per golden question. A plan is a human judgement in exactly the way a G1 label
is, so it carries the same discipline: an author, a date, a review status, and a
review note saying what the owner must check. ``authoritative_g2`` refuses to
make a claim over a plan that is still ``draft``.

The plan set has one hazard the G1 labels do not, and two checks exist for it.
A plan author holds the answer key, so a plan can pass by naming the answer
rather than by finding it:

* naming an **Oracle id** is refused by the IR itself, structurally; and
* naming a **card** in free text is not, because free text is where a mechanic
  query legitimately lives. :func:`check_plan_coverage` therefore reports every
  plan whose search text names a required card the question's own ask does not,
  and the G2 scorecard publishes that count. A name lookup is a legitimate plan
  for "what does Mental Misstep do"; it is not evidence that the substrate can
  find a card nobody named.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sabermetrics.assistant.eval.models import GoldenQuestionSet
from sabermetrics.assistant.ir import (
    CardSearchStep,
    ResearchPlan,
    RulesLookupStep,
    parse_plan,
)

ROOT = Path(__file__).resolve().parents[4]
#: Where the hand-written plans live, one file per golden question category —
#: the same split ``assistant/eval/questions/`` already uses, so a category's
#: plans sit beside the questions they answer and are reviewed together.
PLANS_DIR = ROOT / "fixtures" / "research" / "r3_plans"
#: The artefact contract this module reads and writes.
PLAN_SET_SCHEMA: Literal["research-r3-plans.v1"] = "research-r3-plans.v1"

#: Step kinds whose result is a set of cards. A question with required Oracle
#: ids can only be answered by one of these, so an answer step that is not one
#: scores zero recall while looking like a plan.
CARD_PRODUCING_KINDS = frozenset(
    {"card_search", "tag_filter", "deck_profile", "union", "intersect", "difference"}
)


class PlanSetError(RuntimeError):
    """The hand-written plan set does not cover the golden question set."""


class HandWrittenPlan(BaseModel):
    """One authored plan, attributable to a person and a date."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    plan: ResearchPlan
    rationale: str = Field(min_length=8)
    author: str = Field(min_length=2)
    authored_on: date
    review_status: Literal["draft", "owner_verified"]
    review_note: str = ""

    @model_validator(mode="after")
    def draft_plans_say_what_to_check(self) -> HandWrittenPlan:
        """Require a draft to name what the owner must verify."""
        if self.review_status == "draft" and not self.review_note.strip():
            raise ValueError(
                f"{self.question_id}: a draft plan must record what the owner "
                "is being asked to check"
            )
        return self


class HandWrittenPlanSet(BaseModel):
    """Every hand-written plan, keyed by golden question id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    plans: tuple[HandWrittenPlan, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_set(self) -> HandWrittenPlanSet:
        """Refuse a foreign schema or a repeated question."""
        if self.schema_version != PLAN_SET_SCHEMA:
            raise ValueError(f"unsupported plan set schema: {self.schema_version}")
        ids = [plan.question_id for plan in self.plans]
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        if duplicates:
            raise ValueError(f"duplicate plan question ids: {', '.join(duplicates)}")
        return self

    @property
    def by_question_id(self) -> dict[str, HandWrittenPlan]:
        """Return the plans keyed by the question each answers."""
        return {plan.question_id: plan for plan in self.plans}

    @property
    def unverified(self) -> tuple[str, ...]:
        """Return every question whose plan the owner has not verified."""
        return tuple(
            plan.question_id
            for plan in self.plans
            if plan.review_status != "owner_verified"
        )

    def sha256(self) -> str:
        """Hash the set canonically, review status included.

        Promoting one plan to owner-verified changes this digest, so a
        scorecard cannot be reused across a review.
        """
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_hand_written_plans(directory: Path = PLANS_DIR) -> HandWrittenPlanSet:
    """Load and validate every checked-in plan file, deterministically.

    Args:
        directory: Directory of per-category YAML files. Defaults to
            ``fixtures/research/r3_plans/``.

    Returns:
        The validated, merged plan set.

    Raises:
        PlanSetError: If the directory is empty, a file is not a mapping, or
            two files disagree about the schema version.
    """
    paths = sorted((*directory.glob("*.yaml"), *directory.glob("*.yml")))
    if not paths:
        raise PlanSetError(f"no hand-written plan files found in {directory}")
    schema_versions: set[str] = set()
    entries: list[dict[str, object]] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise PlanSetError(f"plan file is not a mapping: {path}")
        schema_versions.add(str(raw.get("schema_version")))
        for entry in raw.get("plans") or ():
            payload = dict(entry)
            try:
                payload["plan"] = parse_plan(payload.get("plan"))
            except Exception as exc:
                raise PlanSetError(
                    f"{path.name}: {payload.get('question_id')}: {exc}"
                ) from exc
            entries.append(payload)
    if len(schema_versions) != 1:
        raise PlanSetError(
            f"plan files disagree about the schema version: {sorted(schema_versions)}"
        )
    return HandWrittenPlanSet(
        schema_version=schema_versions.pop(),
        plans=tuple(HandWrittenPlan.model_validate(entry) for entry in entries),
    )


def check_plan_coverage(
    plans: HandWrittenPlanSet, questions: GoldenQuestionSet
) -> None:
    """Refuse a plan set that does not answer exactly the golden question set.

    Args:
        plans: The hand-written plans.
        questions: The golden questions they answer.

    Raises:
        PlanSetError: On a missing plan, a plan for an unknown question, a
            context mismatch, or an answer step that cannot produce the cards
            the question requires.
    """
    by_question = {question.id: question for question in questions.questions}
    planned = plans.by_question_id
    missing = sorted(set(by_question) - set(planned))
    unknown = sorted(set(planned) - set(by_question))
    problems: list[str] = []
    if missing:
        problems.append(f"no plan for: {', '.join(missing)}")
    if unknown:
        problems.append(f"plan for unknown question: {', '.join(unknown)}")
    for question_id, hand in sorted(planned.items()):
        question = by_question.get(question_id)
        if question is None:
            continue
        if hand.plan.context_id != question.context_id:
            problems.append(
                f"{question_id}: plan binds {hand.plan.context_id!r} but the "
                f"question binds {question.context_id!r}"
            )
        if question.required_oracle_ids:
            answer = hand.plan.answer_step
            if answer is None:
                problems.append(
                    f"{question_id}: the question requires cards but the plan "
                    "names no answer step"
                )
            elif hand.plan.step(answer).kind not in CARD_PRODUCING_KINDS:
                problems.append(
                    f"{question_id}: the answer step is a "
                    f"{hand.plan.step(answer).kind} and produces no cards, so "
                    "the question's required cards can never be returned"
                )
        for absence in hand.plan.stated_absences:
            index = absence.answers_expectation
            if index is None:
                continue
            if index >= len(question.expected_absences):
                problems.append(
                    f"{question_id}: an absence answers expectation {index}, "
                    f"but the question states {len(question.expected_absences)}"
                )
    if problems:
        raise PlanSetError("; ".join(problems))


def plans_naming_the_answer(
    plans: HandWrittenPlanSet,
    questions: GoldenQuestionSet,
    names_by_oracle_id: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    """Report plans whose search text names a card the ask does not.

    Naming a card in a query is legitimate when the asker named it. When the
    asker did not, the plan is a lookup wearing a query's clothes, and a gate
    that counts it as a retrieval success overstates what the substrate can do.

    Both required and forbidden names are scanned: naming the card a
    question forbids is the same tell as naming the one it requires, because
    both mean the author was reading the answer key rather than the ask.

    Args:
        plans: The hand-written plans.
        questions: The golden questions.
        names_by_oracle_id: Card names for the question labels' id space.

    Returns:
        Question id to the card names its plan supplies and its ask does not.
        Questions with nothing to report are absent.
    """
    by_question = {question.id: question for question in questions.questions}
    report: dict[str, tuple[str, ...]] = {}
    for question_id, hand in sorted(plans.by_question_id.items()):
        question = by_question.get(question_id)
        if question is None:
            continue
        asked = f"{question.ask}\n{question.clarified_ask}"
        text = "\n".join(_searchable_text(hand.plan))
        leaked = tuple(
            sorted(
                {
                    name
                    for oracle_id in (
                        *question.required_oracle_ids,
                        *question.forbidden_oracle_ids,
                    )
                    if (name := names_by_oracle_id.get(oracle_id))
                    and mentions_card_name(text, name)
                    and not mentions_card_name(asked, name)
                }
            )
        )
        if leaked:
            report[question_id] = leaked
    return report


def _searchable_text(plan: ResearchPlan) -> list[str]:
    """Return every free-text field a plan uses to retrieve."""
    out: list[str] = []
    for step in plan.steps:
        if isinstance(step, CardSearchStep):
            out.append(step.query.text)
        elif isinstance(step, RulesLookupStep):
            out.append(step.question)
    return out


def mentions_card_name(haystack: str, name: str) -> bool:
    """Return whether a card name appears as a word run in ``haystack``.

    Matched on the front face alone as well as the whole printed name, because
    a split or modal card is printed ``"Front // Back"`` and named by its front
    face everywhere a person writes it down.

    Both sides are casefolded here rather than by the caller. An earlier
    version folded only the needle, so a caller passing un-folded prose got a
    silent ``False`` on every capitalized card name — and a check that silently
    finds nothing reads exactly like a check that found nothing wrong.

    Args:
        haystack: Text to search. Case is not significant.
        name: The printed card name.

    Returns:
        Whether the name occurs as a whole word run.
    """
    folded = haystack.casefold()
    for candidate in {name, name.split(" // ")[0]}:
        token = candidate.casefold().strip()
        if token and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", folded):
            return True
    return False
