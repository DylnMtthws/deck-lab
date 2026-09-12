"""Rules-support labels, and why they are matched by text rather than by number.

The obvious design — label a question with the rule numbers that answer it, then
check whether a returned passage carries those numbers — does not work here, and
the reason is a property of the chunker rather than of the rules. When a chunk's
section label is a letter-suffixed rule like ``605.1a``, the chunker has already
REMOVED that number from the chunk body to use it as the label. So the text of
605.1a can be sitting in a returned passage with the string "605.1a" nowhere in
it, and a number-matching gate would report the rule missing.

Matching on the labeller's verbatim quote is chunking-independent and says what
is actually meant: the passages contain the sentence that settles the point.
That only holds if the quotes are real, so they are checked against the pinned
document, and checked to fit inside a single chunk of it — a quote straddling a
chunk boundary could never be matched and would fail its question forever.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from sabermetrics.assistant.eval.rules_support import (
    RulesSupportLabel,
    RulesSupportLabelSet,
    load_rules_support_labels,
    normalize,
    quote_problems,
    quote_variants,
    rule_covered,
    strictness_report,
    support_verdict,
)
from sabermetrics.reference_layer.chunker import DocumentChunker

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "data" / "reference" / "comprehensive_rules"

QUOTE = "An activated ability is a mana ability if it meets all of the following"
OTHER = "Once a player begins to activate a mana ability, that ability"


@dataclass(frozen=True)
class FakePassage:
    content: str
    section: str | None = None


def _label(**overrides) -> RulesSupportLabel:
    payload = {
        "question_id": "rules-999",
        "required_rules": ["605.1a"],
        "sufficient_any_of": [],
        "sufficient_support": "the answer must establish what a mana ability is",
        "near_miss_rules": [],
        "quoted_evidence": [
            {"rule": "605.1a", "quote": QUOTE, "why": "defines a mana ability"}
        ],
        "rationale": "a synthetic rationale long enough to validate",
        "confidence": "high",
        "verification": "adversarially_reconciled",
    }
    payload.update(overrides)
    return RulesSupportLabel.model_validate(payload)


def test_a_rule_is_covered_by_its_text_not_its_number():
    """The chunking hazard, stated as a test.

    The passage here carries the rule's sentence with the number stripped out,
    which is exactly what the chunker produces for a letter-suffixed rule it
    used as a section label. A number-matching gate reports this missing.
    """
    label = _label()
    stripped = FakePassage(content=f"Mana Abilities\n\n{QUOTE} criteria: it ...")
    assert rule_covered(label, "605.1a", [stripped])
    assert "605.1a" not in stripped.content


def test_a_number_without_the_text_does_not_cover_the_rule():
    """The converse: a passage that merely cites the number has not carried it."""
    label = _label()
    citation_only = FakePassage(content="See rule 605.1a for the full definition.")
    assert not rule_covered(label, "605.1a", [citation_only])


def test_matching_survives_different_line_wrapping():
    label = _label()
    rewrapped = FakePassage(content=QUOTE.replace(" ", "\n   "))
    assert rule_covered(label, "605.1a", [rewrapped])
    assert normalize("a  b\n c") == "a b c"


def test_an_unquoted_rule_can_never_be_covered_so_it_is_rejected():
    """A schema-level refusal, because the failure would be silent otherwise."""
    with pytest.raises(ValueError, match="no quote for"):
        _label(required_rules=["605.1a", "605.3b"])


def test_a_rule_cannot_be_both_required_and_optional():
    with pytest.raises(ValueError, match="both required and optional"):
        _label(
            required_rules=["605.1a"],
            sufficient_any_of=["605.1a"],
        )


def test_a_near_miss_cannot_also_be_an_answer():
    with pytest.raises(ValueError, match="both answer and do not"):
        _label(
            near_miss_rules=[
                {"rule": "605.1a", "why": "a synthetic reason long enough to pass"}
            ]
        )


def test_support_needs_every_required_rule():
    label = _label(
        required_rules=["605.1a", "605.5b"],
        quoted_evidence=[
            {"rule": "605.1a", "quote": QUOTE, "why": "defines a mana ability"},
            {"rule": "605.5b", "quote": OTHER, "why": "when it may be activated"},
        ],
    )
    half = support_verdict(label, [FakePassage(content=QUOTE)])
    assert half["supported"] is False
    assert half["required_missing"] == ["605.5b"]
    whole = support_verdict(label, [FakePassage(content=f"{QUOTE} ... {OTHER}")])
    assert whole["supported"] is True
    assert whole["required_missing"] == []


def test_support_needs_one_alternative_when_alternatives_exist():
    label = _label(
        sufficient_any_of=["605.5b"],
        quoted_evidence=[
            {"rule": "605.1a", "quote": QUOTE, "why": "defines a mana ability"},
            {"rule": "605.5b", "quote": OTHER, "why": "one valid citation"},
        ],
    )
    without = support_verdict(label, [FakePassage(content=QUOTE)])
    assert without["supported"] is False
    assert without["alternatives_covered"] == []
    with_one = support_verdict(label, [FakePassage(content=f"{QUOTE}\n{OTHER}")])
    assert with_one["supported"] is True


def test_an_unquoted_near_miss_is_unevaluable_not_absent():
    """ "The matcher found nothing" and "the matcher could not look" differ.

    Collapsing them publishes a clean zero for a check that never ran. The
    analysis is still worth keeping — it tells a reviewer why the rule nearly
    applies — so it survives as an annotation and the verdict says so.
    """
    label = _label(
        near_miss_rules=[
            {"rule": "605.5b", "why": "adjacent to the answer and does not carry it"}
        ],
    )
    verdict = support_verdict(label, [FakePassage(content=f"{QUOTE}\n{OTHER}")])
    assert verdict["supported"] is True
    assert verdict["near_misses_recorded"] == ["605.5b"]
    assert verdict["near_misses_unevaluable"] == ["605.5b"]
    assert verdict["near_misses_absent"] == []
    assert verdict["near_misses_detected"] == []
    assert verdict["near_miss_detection"] == "not_evaluable"


def test_a_quoted_near_miss_is_detected_when_it_comes_back():
    label = _label(
        near_miss_rules=[
            {
                "rule": "605.5b",
                "why": "adjacent to the answer and does not carry it",
                "quote": OTHER,
            }
        ],
    )
    came_back = support_verdict(label, [FakePassage(content=f"{QUOTE}\n{OTHER}")])
    assert came_back["near_misses_detected"] == ["605.5b"]
    assert came_back["near_miss_detection"] == "measured"
    assert came_back["supported"] is True, "a retrieved trap is never a failure"

    did_not = support_verdict(label, [FakePassage(content=QUOTE)])
    assert did_not["near_misses_absent"] == ["605.5b"]
    assert did_not["near_misses_detected"] == []
    assert did_not["near_miss_detection"] == "measured"


def test_a_mixed_set_of_near_misses_reports_mixed():
    """One evaluable and one not is neither a measurement nor a blank."""
    label = _label(
        near_miss_rules=[
            {
                "rule": "605.5b",
                "why": "adjacent and quoted for the matcher",
                "quote": OTHER,
            },
            {"rule": "605.2", "why": "adjacent, analysed in prose only"},
        ],
    )
    verdict = support_verdict(label, [FakePassage(content=f"{QUOTE}\n{OTHER}")])
    assert verdict["near_misses_detected"] == ["605.5b"]
    assert verdict["near_misses_unevaluable"] == ["605.2"]
    assert verdict["near_miss_detection"] == "mixed"


def test_a_new_label_set_must_quote_its_near_misses():
    """The requirement applies going forward, not retroactively."""
    payload = {
        "label_set": "test",
        "status": "proposed",
        "derived_from": {
            "document": "comprehensive_rules",
            "effective_date": "2026-08-07",
            "content_sha256": "c" * 64,
        },
        "labelled_by": "test",
        "labelled_on": "2026-09-11",
        "independence": (
            "derived from the question and the pinned document only, with "
            "every retrieval artefact withheld"
        ),
        "labels": [
            json.loads(
                _label(
                    near_miss_rules=[
                        {"rule": "605.5b", "why": "adjacent, analysed in prose only"}
                    ]
                ).model_dump_json()
            )
        ],
    }
    with pytest.raises(ValueError, match="every near miss needs"):
        RulesSupportLabelSet.model_validate(payload)

    legacy = RulesSupportLabelSet.model_validate(
        {**payload, "near_miss_quotes_required": False}
    )
    assert legacy.labels[0].near_miss_rules[0].evaluable is False


def test_quote_problems_catches_a_quote_the_document_does_not_contain():
    label_set = RulesSupportLabelSet.model_validate(
        {
            "label_set": "test",
            "status": "proposed",
            "derived_from": {
                "document": "comprehensive_rules",
                "effective_date": "2026-08-07",
                "content_sha256": "c" * 64,
            },
            "labelled_by": "test",
            "labelled_on": "2026-09-10",
            "independence": (
                "derived from the question and the pinned document only, with "
                "every retrieval artefact withheld"
            ),
            "labels": [json.loads(_label().model_dump_json())],
        }
    )
    assert quote_problems(label_set, f"preamble {QUOTE} criteria") == []
    problems = quote_problems(label_set, "a document that says something else")
    assert len(problems) == 1
    assert "rules-999/605.1a" in problems[0]


def _pinned_source() -> Path | None:
    candidates = sorted(SOURCE_DIR.glob("*/normalized.txt"))
    return candidates[-1] if candidates else None


def test_every_checked_in_quote_is_verbatim_in_the_pinned_document():
    """A hallucinated quote fails its question forever, for an invisible reason."""
    labels = load_rules_support_labels()
    if labels is None:
        pytest.skip("no rules-support labels checked in")
    source = _pinned_source()
    if source is None:
        pytest.skip("the pinned rules document is not provisioned on this machine")
    problems = quote_problems(labels, source.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_every_checked_in_quote_fits_inside_one_chunk():
    """A quote spanning a chunk boundary can never be matched by any answer."""
    labels = load_rules_support_labels()
    if labels is None:
        pytest.skip("no rules-support labels checked in")
    source = _pinned_source()
    if source is None:
        pytest.skip("the pinned rules document is not provisioned on this machine")
    bodies = [
        normalize(chunk.content)
        for chunk in DocumentChunker().chunk_comprehensive_rules(source)
    ]
    straddling = [
        f"{label.question_id}/{entry.rule}"
        for label in labels.labels
        for entry in label.quoted_evidence
        if not any(
            variant in body
            for variant in quote_variants(entry.quote)
            for body in bodies
        )
    ]
    assert not straddling, (
        "these quotes are in the document but not inside any single chunk, so "
        f"no retrieved passage could ever contain them: {straddling}"
    )


def test_the_checked_in_labels_name_the_document_they_came_from():
    labels = load_rules_support_labels()
    if labels is None:
        pytest.skip("no rules-support labels checked in")
    source_json = SOURCE_DIR / labels.derived_from.effective_date.isoformat()
    if not (source_json / "source.json").is_file():
        pytest.skip("that rules edition is not provisioned on this machine")
    recorded = json.loads((source_json / "source.json").read_text(encoding="utf-8"))
    assert labels.derived_from.content_sha256 == recorded["content_sha256"], (
        "the labels name a different document from the one on disk; a rules "
        "label means nothing without saying which edition it is about"
    )


def test_a_quote_that_includes_its_rule_number_still_matches():
    """The chunker removes the number; the answer key should not have to know.

    Three of the checked-in quotes were transcribed faithfully, starting with
    the rule number, and were in the document but in no chunk — so the rules
    they stand for could never have been covered by any answer.
    """
    label = _label(
        quoted_evidence=[
            {
                "rule": "605.1a",
                "quote": f"605.1a {QUOTE}",
                "why": "defines a mana ability",
            }
        ]
    )
    body_without_number = FakePassage(content=f"Mana Abilities\n\n{QUOTE} criteria")
    assert rule_covered(label, "605.1a", [body_without_number])
    assert quote_variants(f"605.1a {QUOTE}") == (f"605.1a {QUOTE}", QUOTE)
    assert quote_variants(QUOTE) == (QUOTE,)


def test_no_scored_rule_is_unreachable_in_any_chunk():
    """A rule nothing can match fails its question forever, invisibly.

    Not a judgement call like an inert disjunction — a scored rule whose quote
    lands in no chunk is always a defect, so it is asserted rather than
    reported.
    """
    labels = load_rules_support_labels()
    if labels is None:
        pytest.skip("no rules-support labels checked in")
    source = _pinned_source()
    if source is None:
        pytest.skip("the pinned rules document is not provisioned on this machine")
    bodies = [
        normalize(chunk.content)
        for chunk in DocumentChunker().chunk_comprehensive_rules(source)
    ]
    unreachable = {
        label.question_id: strictness_report(label, bodies)["rules_in_no_chunk"]
        for label in labels.labels
    }
    offenders = {key: value for key, value in unreachable.items() if value}
    assert not offenders, f"scored rules that no passage can ever contain: {offenders}"


def test_a_rule_sharing_a_chunk_with_a_required_rule_is_reported_as_inert():
    """The critic's finding, as a check rather than a paragraph.

    Two required rules in one chunk means the second cannot independently fail,
    so the conjunction is shorter than it looks — and an alternative sharing a
    chunk with a required rule makes the whole disjunction unfailable.
    """
    label = _label(
        required_rules=["605.1a", "605.5b"],
        sufficient_any_of=["605.2"],
        quoted_evidence=[
            {"rule": "605.1a", "quote": QUOTE, "why": "defines a mana ability"},
            {"rule": "605.5b", "quote": OTHER, "why": "when it may be activated"},
            {
                "rule": "605.2",
                "quote": f"{OTHER} remains a mana ability",
                "why": "a co-located alternative",
            },
        ],
    )
    one_chunk = [normalize(f"{QUOTE} ... {OTHER} remains a mana ability")]
    report = strictness_report(label, one_chunk)
    assert report["required_rules_that_cannot_independently_fail"] == ["605.5b"]
    assert report["disjunction_satisfied_by_a_required_rule"] == ["605.2"]
    assert report["disjunction_is_inert"] is True
    assert report["minimum_distinct_chunks"] == 1

    separate = [normalize(QUOTE), normalize(f"{OTHER} remains a mana ability")]
    spread = strictness_report(label, separate)
    assert spread["required_rules_that_cannot_independently_fail"] == []
    assert spread["minimum_distinct_chunks"] == 2


def test_a_near_miss_quote_is_checked_against_the_document_too():
    """A mistranscribed trap quote reports "not retrieved" for a check that
    could never have matched anything."""
    label_set = RulesSupportLabelSet.model_validate(
        {
            "label_set": "test",
            "status": "proposed",
            "derived_from": {
                "document": "comprehensive_rules",
                "effective_date": "2026-08-07",
                "content_sha256": "c" * 64,
            },
            "labelled_by": "test",
            "labelled_on": "2026-09-11",
            "independence": (
                "derived from the question and the pinned document only, with "
                "every retrieval artefact withheld"
            ),
            "labels": [
                json.loads(
                    _label(
                        near_miss_rules=[
                            {
                                "rule": "605.5b",
                                "why": "adjacent and quoted, but paraphrased",
                                "quote": (
                                    "a sentence that is definitely not present "
                                    "anywhere in the pinned rules document"
                                ),
                            }
                        ]
                    ).model_dump_json()
                )
            ],
        }
    )
    problems = quote_problems(label_set, f"preamble {QUOTE} criteria")
    assert len(problems) == 1
    assert "near miss" in problems[0]


def test_every_alternative_group_must_be_satisfied_not_just_one():
    """The schema defect the panel's critic found, as a check.

    Two independent propositions, each with a choice of citation, used to be
    encoded as one flat disjunction — so covering the easy proposition twice
    passed the label while the other went unestablished, with "one from each
    group" stated only in prose no code reads.
    """
    third = "the active player receives priority"
    label = _label(
        required_rules=[],
        sufficient_any_of=[["605.1a", "605.2"], ["605.5b"]],
        quoted_evidence=[
            {"rule": "605.1a", "quote": QUOTE, "why": "carries proposition one"},
            {"rule": "605.2", "quote": f"{QUOTE} and also", "why": "same, differently"},
            {"rule": "605.5b", "quote": OTHER, "why": "carries proposition two"},
        ],
    )
    both_from_one_group = support_verdict(
        label, [FakePassage(content=f"{QUOTE} and also")]
    )
    assert (
        both_from_one_group["supported"] is False
    ), "covering one proposition twice must not satisfy the other"
    assert both_from_one_group["alternative_groups_unsatisfied"] == [["605.5b"]]

    one_from_each = support_verdict(label, [FakePassage(content=f"{QUOTE}\n{OTHER}")])
    assert one_from_each["supported"] is True
    assert one_from_each["alternative_groups_unsatisfied"] == []
    assert one_from_each["alternative_groups"] == 2
    del third


def test_a_flat_alternative_list_is_lifted_into_one_group():
    """Earlier files keep their meaning rather than silently changing it."""
    flat = _label(
        sufficient_any_of=["605.5b"],
        quoted_evidence=[
            {"rule": "605.1a", "quote": QUOTE, "why": "defines a mana ability"},
            {"rule": "605.5b", "quote": OTHER, "why": "an alternative"},
        ],
    )
    assert flat.sufficient_any_of == [["605.5b"]]
    assert flat.alternative_rules == ["605.5b"]


def test_an_empty_alternative_group_is_refused():
    """It could never be satisfied, so it would fail every answer forever."""
    with pytest.raises(ValueError, match="empty alternative group"):
        _label(sufficient_any_of=[[]])
