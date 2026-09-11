"""Labels that say what a rules answer must be supported BY, and the matcher.

A rules question passing on card retrieval alone was always a hole: the plan
found the card the asker named, the rules lookup returned six passages, and
nothing anywhere asked whether those passages answered the question. "Six well-cited
passages" establishes provenance and completeness. It does not establish that
the question was answered.

These labels close that hole, and the way they are MATCHED is the load-bearing
part. Matching on rule numbers would not work: the chunker strips a letter-
suffixed rule number out of the chunk text when it uses that number as the
chunk's section label, so ``605.1a`` can be present in a returned passage and
undetectable in it. So a rule is matched by its TEXT — the verbatim quote the
labeller had to supply — which is chunking-independent and says exactly what is
meant: the returned passages contain the sentence that settles this point.

The quotes are checked against the pinned document, and checked to fall inside a
single chunk of it, because a quote straddling a chunk boundary could never be
matched and would fail every answer forever.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

RULES_SUPPORT_SCHEMA: Literal["research-rules-support-labels.v1"] = (
    "research-rules-support-labels.v1"
)
ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PATH = ROOT / "fixtures" / "research" / "rules_support_labels.yaml"
#: Long enough that a match is the rule and not a stock phrase. The labellers
#: were told this bound; it is re-checked here because a schema the producer
#: honoured is not a schema the file will keep honouring.
MINIMUM_QUOTE_LENGTH = 40


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Passage(Protocol):
    """The part of a retrieved rules row this module reads."""

    @property
    def content(self) -> str: ...

    @property
    def section(self) -> str | None: ...


def normalize(text: str) -> str:
    """Collapse whitespace so a quote matches across differing line wrapping."""
    return re.sub(r"\s+", " ", text).strip()


#: A rule number at the very start of a quote, e.g. "608.2n " or "202.3.".
_LEADING_RULE_NUMBER = re.compile(r"^\s*\d[\d.]*[a-z]?\.?\s+")


def quote_variants(quote: str) -> tuple[str, ...]:
    """Return the forms of a quote a chunk body might actually contain.

    The rule NUMBER is a label the chunker removes: when it splits on a
    letter-suffixed rule it takes that number out of the body to use as the
    chunk's section label. So a quote transcribed faithfully as "608.2n As the
    final part of an instant or sorcery spell's resolution..." is in the
    document and in no chunk, and the rule it stands for would be reported
    missing forever.

    Stripping the number here rather than editing the quote keeps the answer
    key a faithful transcription and puts the chunker's quirk where it belongs,
    in the code that knows about chunks.

    Args:
        quote: The labeller's verbatim quote.

    Returns:
        The normalized quote, and its number-stripped form when they differ.
    """
    whole = normalize(quote)
    stripped = normalize(_LEADING_RULE_NUMBER.sub("", quote))
    return (whole,) if stripped == whole else (whole, stripped)


class QuotedRule(_Strict):
    """One rule, the sentence that carries it, and what it establishes."""

    rule: str = Field(min_length=2)
    quote: str = Field(min_length=MINIMUM_QUOTE_LENGTH)
    why: str = Field(min_length=8)


class RulesSupportSource(_Strict):
    """The document these labels were derived from.

    Named because a rules label is only meaningful against one edition. The
    rules are renumbered and rewritten; a label citing 605.1a means nothing
    without saying which 605.1a.
    """

    document: str = Field(min_length=1)
    effective_date: date
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NearMiss(_Strict):
    """A rule that looks like the answer and is not.

    NEVER scored against an answer — retrieving a trap is fine and only resting
    the answer on one is wrong, and nothing here can see what an answer rests
    on. But whether the trap CAME BACK is worth knowing, and that splits into
    three states the verdict keeps apart:

    * quoted and found — the matcher looked and the trap is in the passages;
    * quoted and not found — the matcher looked and it is not. A real negative;
    * NOT QUOTED — the matcher could not look at all.

    The third is the one that matters. Collapsing it into the second would
    report "no trap was retrieved" for a check that never ran, which is the
    failure this project keeps finding in its own work. An unquoted near miss is
    preserved as an ANNOTATION and reported as unevaluable, because the
    reasoning is valuable on its own: "605.3b reaches the right conclusion via
    the wrong branch" is what a reviewer needs in order to judge the label, and
    it stays useful whether or not a machine can act on it.
    """

    rule: str = Field(min_length=2)
    #: Why it nearly applies and where it fails. Required: a bare rule number
    #: in this list tells a reviewer nothing about the trap.
    why: str = Field(min_length=20)
    #: The sentence that would let the matcher recognise this rule in a
    #: passage. ``None`` means the analysis is an annotation only — see the
    #: class docstring. Required for new label sets via
    #: ``near_miss_quotes_required``.
    quote: str | None = Field(default=None, min_length=MINIMUM_QUOTE_LENGTH)

    @property
    def evaluable(self) -> bool:
        """Whether the matcher has what it needs to look for this rule."""
        return self.quote is not None


class RulesSupportLabel(_Strict):
    """What a set of retrieved passages must contain to answer one question."""

    question_id: str = Field(min_length=3)
    #: Jointly necessary. A passage set missing any one of these has not
    #: answered the question. This is a conjunction and it FAILS answers, so it
    #: is kept to what is load-bearing.
    required_rules: list[str]
    #: Any ONE of these also carries a proposition that has several equally
    #: valid citations. Empty when there is no such choice.
    sufficient_any_of: list[str]
    #: What the passage set must ESTABLISH, as propositions a reader can check.
    #: Prose on purpose: the rule numbers are the mechanism, not the meaning.
    sufficient_support: str = Field(min_length=20)
    #: Rules a keyword search surfaces for this question that do NOT answer it.
    #: The analogue of a card counterexample: retrieving one is fine, resting
    #: the answer on one is wrong. Recorded, never scored against.
    near_miss_rules: list[NearMiss]
    quoted_evidence: list[QuotedRule]
    rationale: str = Field(min_length=20)
    confidence: Literal["high", "medium", "low"]
    #: How much scrutiny this individual label received. Recorded per label
    #: rather than per set because a set is only as reviewed as its least
    #: reviewed member, and averaging that away is how "adversarially verified"
    #: comes to cover a label nobody checked.
    verification: Literal["proposed_unreviewed", "adversarially_reconciled"]

    @model_validator(mode="after")
    def every_scored_rule_is_quoted(self) -> RulesSupportLabel:
        scored = [*self.required_rules, *self.sufficient_any_of]
        if not scored:
            raise ValueError(f"{self.question_id}: a label that requires nothing")
        quoted = {entry.rule for entry in self.quoted_evidence}
        missing = sorted(set(scored) - quoted)
        if missing:
            raise ValueError(
                f"{self.question_id}: no quote for {missing}; a rule is matched "
                "by its text, so an unquoted rule can never be covered"
            )
        overlap = sorted(set(self.required_rules) & set(self.sufficient_any_of))
        if overlap:
            raise ValueError(
                f"{self.question_id}: {overlap} are both required and optional"
            )
        traps = sorted({entry.rule for entry in self.near_miss_rules} & set(scored))
        if traps:
            raise ValueError(f"{self.question_id}: {traps} both answer and do not")
        return self


class RulesSupportLabelSet(_Strict):
    """One versioned, separately identified set of rules-support labels."""

    schema_version: Literal["research-rules-support-labels.v1"] = RULES_SUPPORT_SCHEMA
    #: Its own identity, distinct from the card-label adjudication set. Adding
    #: these labels changes what a rules question means, so the result is a
    #: different evaluation and says so rather than silently revising earlier
    #: scores.
    label_set: str = Field(min_length=3)
    status: Literal["proposed", "awaiting_owner_review", "owner_verified"]
    derived_from: RulesSupportSource
    labelled_by: str = Field(min_length=2)
    labelled_on: date
    #: How the labels were kept independent of what retrieval returned. Free
    #: text, required, and read by a human — an answer key derived from the
    #: system it scores measures nothing.
    independence: str = Field(min_length=40)
    #: Whether every near miss in this set must carry a quote. New sets set
    #: this true so their near misses are machine-evaluable. It is false only
    #: for a round labelled before the requirement existed, and then the
    #: verdicts say `unevaluable` rather than reporting a clean zero.
    near_miss_quotes_required: bool = True
    labels: list[RulesSupportLabel]

    @model_validator(mode="after")
    def unique_questions(self) -> RulesSupportLabelSet:
        ids = [label.question_id for label in self.labels]
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        if duplicates:
            raise ValueError(f"duplicate rules-support labels: {duplicates}")
        if self.near_miss_quotes_required:
            unquoted = sorted(
                f"{label.question_id}/{entry.rule}"
                for label in self.labels
                for entry in label.near_miss_rules
                if not entry.evaluable
            )
            if unquoted:
                raise ValueError(
                    "near_miss_quotes_required is set, so every near miss needs "
                    "a quote the matcher can look for; missing: " + ", ".join(unquoted)
                )
        return self

    @property
    def by_question_id(self) -> dict[str, RulesSupportLabel]:
        return {label.question_id: label for label in self.labels}

    def sha256(self) -> str:
        """Hash the set canonically, status included."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_rules_support_labels(path: Path = DEFAULT_PATH) -> RulesSupportLabelSet | None:
    """Load the label set, or ``None`` when none is checked in.

    Args:
        path: The label file.

    Returns:
        The validated set, or ``None`` — which is a different state from a set
        that exists and labels nothing.
    """
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RulesSupportLabelSet.model_validate(raw)


def rule_covered(label: RulesSupportLabel, rule: str, passages: Sequence[Any]) -> bool:
    """Return whether the retrieved passages contain the text of one rule.

    Args:
        label: The label carrying the rule's verbatim quote.
        rule: The rule number to check.
        passages: Retrieved rules rows.

    Returns:
        ``True`` when some passage contains that rule's quote.
    """
    quotes = [
        variant
        for entry in label.quoted_evidence
        if entry.rule == rule
        for variant in quote_variants(entry.quote)
    ]
    if not quotes:
        return False
    bodies = [normalize(passage.content) for passage in passages]
    return any(quote in body for quote in quotes for body in bodies)


def support_verdict(
    label: RulesSupportLabel, passages: Sequence[Any]
) -> dict[str, Any]:
    """Score one question's retrieved passages against its support label.

    Args:
        label: The rules-support label.
        passages: The rules rows the run returned.

    Returns:
        A verdict naming exactly which rules were and were not covered. The
        missing list is the useful part: "failed" says nothing a reader can act
        on, and "missing 605.1a" says where to look.
    """
    covered_required = [
        rule for rule in label.required_rules if rule_covered(label, rule, passages)
    ]
    missing_required = [
        rule for rule in label.required_rules if rule not in covered_required
    ]
    covered_alternatives = [
        rule for rule in label.sufficient_any_of if rule_covered(label, rule, passages)
    ]
    satisfied = not missing_required and (
        not label.sufficient_any_of or bool(covered_alternatives)
    )
    detected: list[str] = []
    absent: list[str] = []
    unevaluable: list[str] = []
    bodies = [normalize(passage.content) for passage in passages]
    for entry in label.near_miss_rules:
        if entry.quote is None:
            unevaluable.append(entry.rule)
        elif any(
            variant in body
            for variant in quote_variants(entry.quote)
            for body in bodies
        ):
            detected.append(entry.rule)
        else:
            absent.append(entry.rule)
    if not label.near_miss_rules:
        detection = "not_applicable"
    elif not unevaluable:
        detection = "measured"
    elif detected or absent:
        detection = "mixed"
    else:
        detection = "not_evaluable"
    return {
        "supported": satisfied,
        "required_covered": sorted(covered_required),
        "required_missing": sorted(missing_required),
        "alternatives_covered": sorted(covered_alternatives),
        "alternatives_total": len(label.sufficient_any_of),
        # Three states, deliberately not two. "The matcher looked and found
        # nothing" and "the matcher had nothing to look with" are different
        # facts, and reporting them as one zero is reporting a check that never
        # ran as a clean result.
        "near_misses_recorded": sorted(entry.rule for entry in label.near_miss_rules),
        "near_misses_detected": sorted(detected),
        "near_misses_absent": sorted(absent),
        "near_misses_unevaluable": sorted(unevaluable),
        "near_miss_detection": detection,
        "confidence": label.confidence,
    }


def _chunks_containing(quotes: Sequence[str], bodies: Sequence[str]) -> set[int]:
    """Indices of the chunks whose text contains any of these quote variants."""
    return {
        index
        for index, body in enumerate(bodies)
        if any(quote in body for quote in quotes)
    }


def strictness_report(
    label: RulesSupportLabel, chunk_bodies: Sequence[str]
) -> dict[str, Any]:
    """How demanding a label ACTUALLY is, given how the corpus is chunked.

    A conjunction of four rules sounds twice as strict as one of two. It is not,
    if three of the four live in the same chunk: retrieval returns chunks, so a
    rule sharing a chunk with another scored rule CANNOT INDEPENDENTLY FAIL. It
    is then not a requirement, and a verdict reporting it under
    ``required_covered`` overstates what was measured.

    The same collapse can neuter a disjunction outright. When an alternative
    shares a chunk with a required rule, covering the required rule always
    covers the alternative, the disjunction is satisfied automatically, and
    whatever proposition it was meant to carry is never tested.

    The panel's completeness critic found both in five of the first ten labels,
    which is why this is a function and not a paragraph. Both are properties of
    the label crossed with the chunker rather than of the Magic, so they are
    checkable — and a bar that varies twofold across a set makes any aggregate
    over that set mean less than it appears to.

    Args:
        label: The label to measure.
        chunk_bodies: Normalized text of every chunk of the pinned document.

    Returns:
        A report naming the rules that cannot independently fail and the
        minimum number of distinct chunks a passing answer must contain.
    """
    quotes: dict[str, list[str]] = {}
    for entry in label.quoted_evidence:
        quotes.setdefault(entry.rule, []).extend(quote_variants(entry.quote))
    located = {
        rule: _chunks_containing(values, chunk_bodies)
        for rule, values in quotes.items()
    }
    unlocatable = sorted(rule for rule, found in located.items() if not found)

    required_chunks: set[int] = set()
    collapsed: list[str] = []
    for rule in label.required_rules:
        found = located.get(rule, set())
        if found and found & required_chunks:
            collapsed.append(rule)
        required_chunks |= found
    auto = sorted(
        rule
        for rule in label.sufficient_any_of
        if located.get(rule, set()) & required_chunks
    )
    minimum = len(
        {min(located[rule]) for rule in label.required_rules if located.get(rule)}
    )
    if label.sufficient_any_of and not auto:
        minimum += 1
    return {
        "minimum_distinct_chunks": minimum,
        # Required rules sharing a chunk with an earlier required rule. Each
        # adds nothing a passing answer must do, so the conjunction is shorter
        # than it looks.
        "required_rules_that_cannot_independently_fail": sorted(collapsed),
        # Alternatives co-located with a required rule. The disjunction is then
        # always satisfied and carries no constraint at all.
        "disjunction_satisfied_by_a_required_rule": auto,
        "disjunction_is_inert": bool(label.sufficient_any_of) and bool(auto),
        # A quote in the document but in no chunk can never be matched.
        "rules_in_no_chunk": unlocatable,
    }


def quote_problems(label_set: RulesSupportLabelSet, document_text: str) -> list[str]:
    """Return every quote that is not a verbatim substring of the document.

    A hallucinated quote is the failure mode that matters: it can never be
    matched, so the rule it stands for is permanently uncovered and the
    question permanently fails for a reason nobody can see.

    Args:
        label_set: The labels to check.
        document_text: The pinned rules document.

    Returns:
        Human-readable problems, empty when every quote checks out.
    """
    haystack = normalize(document_text)
    problems: list[str] = []
    for label in label_set.labels:
        quoted: list[tuple[str, str, str]] = [
            ("answer", entry.rule, entry.quote) for entry in label.quoted_evidence
        ]
        # Near-miss quotes are checked too. A mistranscribed one would report
        # `near_misses_absent` — "the matcher looked and the trap did not come
        # back" — for a check that could never have matched anything.
        quoted += [
            ("near miss", entry.rule, entry.quote)
            for entry in label.near_miss_rules
            if entry.quote is not None
        ]
        for kind, rule, quote in quoted:
            if not any(variant in haystack for variant in quote_variants(quote)):
                problems.append(
                    f"{label.question_id}/{rule} ({kind}): quote is not in the "
                    f"document: {quote[:70]!r}"
                )
    return problems
