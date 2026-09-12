"""Why a required rule did not come back: bound, ranking, or absent.

    python scripts/diagnose_rules_support.py --output docs/r3-rules-support-diagnosis.md

A bare "rules support 1/10" cannot distinguish three very different causes, and
they call for three different responses:

* the rule ranks inside a wider window — the plan's BOUND is what excluded it;
* the rule ranks deep — retrieval reaches it, but barely;
* the rule never ranks at all — a genuine gap between the query and the corpus.

READ THE OUTPUT WITH CARE. Every rank here was obtained by asking where a known
answer sits. A bound chosen from these numbers is fitted to this answer key and
will not survive the key being corrected — which is not a hypothetical, it is
exactly what happened to deck-local-009. The honest use of this table is to
decide which QUESTIONS deserve investigation, not to pick a number.

This command changes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sabermetrics.assistant.eval.plans import load_hand_written_plans
from sabermetrics.assistant.eval.rules_support import (
    load_rules_support_labels,
    normalize,
    quote_variants,
)
from sabermetrics.assistant.sources import ReferenceRulesSource
from sabermetrics.substrate.settings import load_research_settings

ROOT = Path(__file__).resolve().parent.parent
#: Function words carry no topical signal, so counting them would flatter every
#: pair equally and hide the gradient this measures.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "is",
        "are",
        "and",
        "or",
        "if",
        "it",
        "its",
        "that",
        "this",
        "for",
        "be",
        "as",
        "on",
        "at",
        "by",
        "with",
        "what",
        "when",
        "how",
        "does",
        "do",
        "you",
        "your",
        "player",
        "their",
        "they",
        "them",
        "may",
        "can",
        "not",
        "no",
    ]
)


def _terms(text: str) -> set[str]:
    """Content words, lowercased, for a crude topical-overlap measure."""
    return {
        word
        for word in re.findall(r"[a-z]+", text.lower())
        if word not in _STOPWORDS and len(word) > 2
    }


DEFAULT_SCORECARD = ROOT / ".research-dev" / "g2-scorecard.json"
#: Deliberately far beyond any plan's bound. The question is where a rule sits,
#: not whether it sits inside a window somebody already chose.
PROBE_DEPTH = 60


def main() -> int:
    """Report the rank of every required rule a run did not cover."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--depth", type=int, default=PROBE_DEPTH)
    args = parser.parse_args()

    labels = load_rules_support_labels()
    if labels is None:
        raise SystemExit("DIAGNOSIS REFUSED: no rules-support labels are checked in")
    by_question = labels.by_question_id
    plans = {plan.question_id: plan for plan in load_hand_written_plans().plans}
    card = json.loads(args.scorecard.read_text(encoding="utf-8"))
    verdicts = card["gates"]["rules_support"]["verdicts"]
    settings = load_research_settings()
    database = Path(settings.artifacts.root) / "reference.db"
    source = ReferenceRulesSource.open(database)

    rows: list[tuple[str, str, int, int | None, float]] = []
    #: Overlap for EVERY required rule, bucketed by what happened to it. The
    #: comparison that matters is covered vs missed, so restricting it to the
    #: missed ones would drop the only group that shows the gradient.
    covered_overlap: list[float] = []
    for question_id in sorted(by_question):
        verdict = verdicts.get(question_id)
        if verdict is None:
            continue
        label = by_question[question_id]
        step_for_overlap = next(
            step
            for step in plans[question_id].plan.steps
            if step.kind == "rules_lookup"
        )
        for rule in verdict["required_covered"]:
            quote = next(
                (entry.quote for entry in label.quoted_evidence if entry.rule == rule),
                "",
            )
            rule_terms = _terms(quote)
            if rule_terms:
                covered_overlap.append(
                    len(_terms(step_for_overlap.question) & rule_terms)
                    / len(rule_terms)
                )
        # Unsatisfied ALTERNATIVE groups matter as much as missing required
        # rules, and enumerating only the latter made this report silent about
        # any question that failed on its disjunction alone — which rules-007
        # did, with every required rule covered.
        missing_rules = list(verdict["required_missing"])
        for group in verdict.get("alternative_groups_unsatisfied", []):
            missing_rules.extend(group)
        if not missing_rules:
            continue
        step = next(
            step
            for step in plans[question_id].plan.steps
            if step.kind == "rules_lookup"
        )
        passages = source.lookup(step.question, top_k=args.depth)
        bodies = [normalize(passage.content) for passage in passages]
        label = by_question[question_id]
        for rule in missing_rules:
            quotes = [
                variant
                for entry in label.quoted_evidence
                if entry.rule == rule
                for variant in quote_variants(entry.quote)
            ]
            rank = next(
                (
                    index
                    for index, body in enumerate(bodies, 1)
                    if any(quote in body for quote in quotes)
                ),
                None,
            )
            quote = next(
                (entry.quote for entry in label.quoted_evidence if entry.rule == rule),
                "",
            )
            rule_terms = _terms(quote)
            overlap = (
                len(_terms(step.question) & rule_terms) / len(rule_terms)
                if rule_terms
                else 0.0
            )
            rows.append((question_id, rule, step.limit, rank, overlap))

    within = [row for row in rows if row[3] is not None and row[3] <= 20]
    deep = [row for row in rows if row[3] is not None and row[3] > 20]
    absent = [row for row in rows if row[3] is None]

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    lines = [
        "# Why the required rules did not come back",
        "",
        "Generated by `scripts/diagnose_rules_support.py`. Reproducible, and it",
        "changes nothing.",
        "",
        "**Every rank below was obtained by asking where a known answer sits.** A",
        "bound chosen from these numbers is fitted to this answer key and will not",
        "survive the key being corrected — which is not hypothetical, it is what",
        "happened to `deck-local-009`. Use this to decide which QUESTIONS deserve",
        "investigation, not to pick a number.",
        "",
        f"- **{len(within)}** missing rules sit at rank 20 or better: the plan's",
        "  bound is what excluded them.",
        f"- **{len(deep)}** sit deeper than 20: retrieval reaches them, barely.",
        f"- **{len(absent)}** never appear within {args.depth}: a genuine gap",
        "  between the query and the corpus, and the only group where a wider",
        "  window would not have helped.",
        "",
        "",
        "## Vocabulary overlap tracks retrievability",
        "",
        "The share of a rule's own content words that also appear in the plan's",
        "query, across **every** required rule and bucketed by what became of it:",
        "",
        f"- covered: **{_mean(covered_overlap):.2f}** " f"(n={len(covered_overlap)})",
        f"- ranked but outside the bound: "
        f"**{_mean([row[4] for row in within + deep]):.2f}** "
        f"(n={len(within) + len(deep)})",
        f"- never within {args.depth}: "
        f"**{_mean([row[4] for row in absent]):.2f}** (n={len(absent)})",
        "",
        "A question phrased in words the rule does not use tends not to reach it,",
        "which is a statement about the QUERY and the LABEL rather than the bound",
        "— no window is wide enough to fix the third group.",
        "",
        "**Treat this as a gradient, not a law.** The denominator varies: quote",
        "lengths in this key run from ten to sixty-one words, so a short rule's",
        "overlap is computed over far fewer terms than a long one's and the three",
        "means are not comparing like with like. Counterexamples exist — any row",
        "below with a high overlap and no rank is one — and the sample is small",
        "enough that one matters. It is a reason to look at queries, not a",
        "finding on its own.",
        "",
        "| question | missing rule | plan bound | rank | overlap | reading |",
        "|---|---|---:|---:|---:|---|",
    ]
    for question_id, rule, bound, rank, overlap in rows:
        if rank is None:
            reading = f"never within {args.depth} — retrieval gap"
        elif rank <= 20:
            reading = "excluded by the bound"
        else:
            reading = "reachable only very wide"
        lines.append(
            f"| {question_id} | `{rule}` | {bound} | "
            f"{rank if rank is not None else '—'} | {overlap:.2f} | {reading} |"
        )
    report = "\n".join(lines) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"DIAGNOSIS REFUSED: {exc}")
        raise SystemExit(2) from None
