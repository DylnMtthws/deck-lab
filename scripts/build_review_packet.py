"""Generate the owner-review packet from the checked-in artefacts.

    python scripts/build_review_packet.py

Generated rather than written, so it cannot drift from what is actually being
measured. Everything in it comes from the golden questions, the hand-written
plans, the adjudications, the frozen baseline and the last scorecard; nothing
is restated by hand.

It shows Oracle ids as CARD NAMES, because a reviewer cannot adjudicate a uuid.
An id the map cannot name is printed as the bare id and flagged, since a label
nobody can read is a label nobody has reviewed.

It changes no review status. Promoting a plan to owner-verified is the owner's
act, and a script that did it while producing the document to review would make
the review a formality.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sabermetrics.assistant.eval.baseline import (
    adjudication_set,
    current_baseline,
    evaluation_inputs_sha256,
    load_adjudications,
)
from sabermetrics.assistant.eval.models import GoldenQuestion, load_questions
from sabermetrics.assistant.eval.plans import HandWrittenPlan, load_hand_written_plans
from sabermetrics.assistant.eval.runner import LabelIdMap, load_label_id_map

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "docs" / "r3-owner-review.md"
DEFAULT_SCORECARD = ROOT / ".research-dev" / "g2-scorecard.json"
DEFAULT_OBSERVATIONS = ROOT / ".research-dev" / "g2-observations.json"
#: How many returned cards to show per question. Enough to judge whether the
#: answer is on topic; not so many that the document stops being readable.
PREVIEW = 12


def _name(id_map: LabelIdMap, oracle_id: str) -> str:
    """Render one Oracle id for a human, flagging anything unnameable."""
    name = id_map.names_by_label_id.get(oracle_id)
    return name if name else f"`{oracle_id}` **(UNNAMED — cannot be reviewed)**"


def _names(id_map: LabelIdMap, oracle_ids: list[str]) -> str:
    """Join names so that a card whose own name contains a comma stays one item."""
    if not oracle_ids:
        return "_none_"
    return " · ".join(sorted(_name(id_map, value) for value in oracle_ids))


def _steps(plan: HandWrittenPlan) -> list[str]:
    """Render a plan's steps as the reviewer needs to see them."""
    lines: list[str] = []
    for step in plan.plan.steps:
        payload = step.model_dump(mode="json")
        header = f"- **{payload['id']}** (`{payload['kind']}`"
        if payload.get("scope"):
            header += f", scope `{payload['scope']}`"
        lines.append(header + ")")
        query = payload.get("query")
        if query:
            lines.append(f"  - query text: _{query['text'].strip()}_")
            lines.append(f"  - top_k: {query['top_k']}")
            filters = {
                key: value
                for key, value in (query.get("filters") or {}).items()
                if value not in (None, [], "", False)
            }
            if filters:
                lines.append(f"  - filters: `{json.dumps(filters, sort_keys=True)}`")
        if payload.get("question"):
            lines.append(f"  - rules question: _{payload['question'].strip()}_")
        for key in ("limit", "filters", "inputs", "note"):
            value = payload.get(key)
            if key == "filters" and query:
                continue
            if value in (None, [], "", 0):
                continue
            rendered = (
                json.dumps(value, sort_keys=True)
                if not isinstance(value, str)
                else value.strip()
            )
            lines.append(f"  - {key}: {rendered}")
    return lines


def _outcome(
    question: GoldenQuestion,
    scorecard: dict[str, Any] | None,
    returned: list[str],
    id_map: LabelIdMap,
) -> list[str]:
    """Render what actually happened, or say plainly that nothing did."""
    if scorecard is None:
        return ["- **Result:** no scorecard on this machine; nothing measured."]
    gate = scorecard["gates"]["retrieval"]
    lines: list[str] = []
    if question.id in gate.get("unscored_pending", {}):
        lines.append(
            f"- **Result:** UNSCORED — pending {gate['unscored_pending'][question.id]}"
        )
    elif question.id in gate["failed"]:
        lines.append("- **Result:** FAIL")
    elif question.id in gate["not_applicable_ids"]:
        lines.append("- **Result:** no required labels; nothing to score")
    else:
        lines.append("- **Result:** pass")
    for label, source in (
        ("returned", gate["answer_returned"]),
        ("eligible population", gate["eligible_population"]),
        ("window", gate["windows"]),
    ):
        if question.id in source:
            lines.append(f"- {label}: {source[question.id]}")
    coverage = gate["alternative_coverage"].get(question.id)
    if coverage:
        lines.append(
            f"- qualifying alternatives returned: {coverage['returned']} of "
            f"{coverage['qualifying']}"
        )
    support = scorecard["gates"].get("rules_support") or {}
    if question.id in support.get("passages_returned", {}):
        sections = ", ".join(support["sections_returned"].get(question.id, ()))
        lines.append(
            f"- rules passages: {support['passages_returned'][question.id]}"
            + (f" ({sections})" if sections else "")
        )
    if returned:
        shown = [_name(id_map, value) for value in returned[:PREVIEW]]
        more = len(returned) - len(shown)
        lines.append(
            "- returned cards, in rank order: "
            + " · ".join(shown)
            + (f" · … and {more} more" if more > 0 else "")
        )
    else:
        lines.append("- returned cards: _not recorded; re-run with --observations_")
    return lines


def _header(
    scorecard: dict[str, Any] | None, inputs_hash: str, id_map: LabelIdMap
) -> list[str]:
    rulings = load_adjudications()
    baseline = current_baseline(inputs_hash)
    lines = [
        "# R3 owner-review packet",
        "",
        "Generated by `scripts/build_review_packet.py`. Do not edit by hand —",
        "edit the questions, the plans or the adjudications and regenerate.",
        "",
        "## What is being asked of the reviewer",
        "",
        "Every question below is `contested` and every plan is `draft`, and both",
        "stay that way until this review happens. Three separate decisions:",
        "",
        "1. **Is the wording right?** The `clarified_ask` is what the plan was",
        "   written against. If it does not say what the `ask` means, the plan is",
        "   solving a different problem and its result is not evidence.",
        "2. **Are the labelled cards right?** Required means every one is needed;",
        "   any-of means one suffices; forbidden means returning it is wrong;",
        "   counterexample means retrieving it is fine and recommending it is not.",
        "3. **Is the plan expressing the ask, or fitting the answer?** A bound",
        "   chosen after seeing where the answer ranked is tuning, not retrieval.",
        "",
        "### Two decisions the artefacts cannot make",
        "",
        "- **Should rules questions label their expected sections?** The index",
        "  retrieves passages and preserves their provenance, and that is all",
        "  that is measured. Whether `CR 605.3b` actually answers \"is Kinnan's",
        '  ability a mana ability" is a judgement nobody has recorded, so the',
        "  rules questions currently pass on card retrieval alone. Labelling the",
        "  sections each question needs is what would make rule support",
        "  measurable — and it is labelling work, not code.",
        "- **Should the label id map be marked reviewed?** It is the bijection",
        "  from fixture ids to corpus ids and it is `awaiting_owner_review`.",
        "  Authoritative G2 refuses until it is reviewed, which is deliberate: an",
        "  unreviewed map means the cards being scored may not be the cards",
        "  intended.",
        "",
        f"- adjudication set: `{adjudication_set()}`",
        f"- ratifies the golden set: "
        f"`{bool(rulings.get('ratifies_golden_set', False))}`",
        f"- evaluation inputs: `{inputs_hash}`",
        f"- label id map: `{id_map.status}`",
    ]
    if baseline is not None:
        lines += [
            f"- frozen result: **{baseline.retrieval_passed}/"
            f"{baseline.retrieval_applicable}** retrieval, "
            f"{baseline.discovery_passed}/{baseline.discovery_applicable} discovery "
            f"({baseline.kind})",
            "",
            "### What that number is not",
            "",
            *(f"- {limitation}" for limitation in baseline.limitations),
        ]
    else:
        lines += [
            "- frozen result: **none for these inputs** — the questions or plans",
            "  have changed since the last freeze, so no preserved number",
            "  describes the current set.",
        ]
    if scorecard is not None:
        rules = scorecard["provenance"].get("rules_index") or {"status": "not_built"}
        lines += [
            "",
            "### Sources the last run consulted",
            "",
            f"- cards: `{scorecard['provenance']['corpus_source_view']}`, "
            f"{scorecard['provenance']['corpus_row_count']} rows, "
            f"`{scorecard['provenance']['corpus_sha256'][:12]}`",
            (
                f"- rules: {rules.get('document')} effective "
                f"{rules.get('effective_date')}, {rules.get('chunk_count')} chunks, "
                f"source `{str(rules.get('source_sha256'))[:12]}`"
                if "status" not in rules
                else "- rules: **not built**"
            ),
        ]
    return lines + [""]


def main() -> int:
    """Write the packet from whatever evidence this machine actually has."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    args = parser.parse_args()

    questions = load_questions()
    plans = {plan.question_id: plan for plan in load_hand_written_plans().plans}
    id_map = load_label_id_map()
    inputs_hash = evaluation_inputs_sha256(questions, load_hand_written_plans())
    scorecard = (
        json.loads(args.scorecard.read_text(encoding="utf-8"))
        if args.scorecard.is_file()
        else None
    )
    returned_by_question: dict[str, list[str]] = {}
    if args.observations.is_file():
        payload = json.loads(args.observations.read_text(encoding="utf-8"))
        if payload.get("evaluation_inputs_sha256") == inputs_hash:
            returned_by_question = {
                row["observation"]["question_id"]: list(
                    row["observation"]["returned_oracle_ids"]
                )
                for row in payload["rows"]
            }
        else:
            print(
                "observations describe different inputs and were ignored; "
                "re-run G2 with --observations"
            )

    lines = _header(scorecard, inputs_hash, id_map)
    by_category: dict[str, list[GoldenQuestion]] = {}
    for question in questions.questions:
        by_category.setdefault(question.category, []).append(question)

    for category in sorted(by_category):
        lines += [f"## {category.replace('_', ' ')}", ""]
        for question in by_category[category]:
            plan = plans.get(question.id)
            lines += [
                f"### {question.id}",
                "",
                f"**Ask.** {question.ask.strip()}",
                "",
                f"**Clarified ask.** {question.clarified_ask.strip()}",
                "",
                "**Proposed labels**",
                "",
                f"- required (all needed): {_names(id_map, question.required_oracle_ids)}",
            ]
            if question.satisfied_by_any_of:
                lines.append(
                    "- any one of these answers it: "
                    + _names(id_map, question.satisfied_by_any_of)
                )
            lines.append(
                f"- forbidden (returning it is wrong): "
                f"{_names(id_map, question.forbidden_oracle_ids)}"
            )
            if question.counterexample_oracle_ids:
                lines.append(
                    "- counterexamples (retrieving fine, recommending wrong): "
                    + _names(id_map, question.counterexample_oracle_ids)
                )
            if question.expected_absences:
                lines.append(
                    "- expected absences: " + ", ".join(question.expected_absences)
                )
            for flag, text in (
                (question.clarification_expected, "expects a clarifying question"),
                (question.no_finding_expected, "must not manufacture a finding"),
            ):
                if flag:
                    lines.append(f"- {text}")
            if question.unscored_pending:
                lines.append(f"- unscored, pending: {question.unscored_pending}")
            lines += ["", "**Plan**", ""]
            if plan is None:
                lines.append("- _no hand-written plan_")
            else:
                lines.append(f"- intent: {plan.plan.intent.strip()}")
                if plan.plan.context_id:
                    lines.append(f"- context: `{plan.plan.context_id}`")
                lines += _steps(plan)
                lines += ["", f"_Rationale._ {plan.rationale.strip()}"]
                if plan.review_note:
                    lines += ["", f"_Author's note._ {plan.review_note.strip()}"]
            lines += ["", "**What happened**", ""]
            lines += _outcome(
                question, scorecard, returned_by_question.get(question.id, []), id_map
            )
            lines.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(questions.questions)} questions)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"REVIEW PACKET REFUSED: {exc}")
        raise SystemExit(2) from None
