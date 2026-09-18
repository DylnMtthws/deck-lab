"""Two ways a rules plan can carry its own answer, and the checks for each."""

from __future__ import annotations

import pytest

from sabermetrics.assistant.eval.plans import (
    KEY_SPAN_WORDS,
    load_hand_written_plans,
    longest_shared_span,
    plans_quoting_the_key,
)
from sabermetrics.assistant.eval.rules_support import load_rules_support_labels
from sabermetrics.assistant.ir import PlanNamesARuleError, RulesLookupStep


def test_a_rule_number_in_a_lookup_is_refused_at_parse():
    with pytest.raises(PlanNamesARuleError):
        RulesLookupStep(id="lookup", question="What does rule 605.1a say about mana?")
    with pytest.raises(PlanNamesARuleError):
        RulesLookupStep(id="lookup", question="See 202.3 for mana value.")
    RulesLookupStep(
        id="lookup", question="What is the mana value of a spell on the stack?"
    )


def test_shared_span_counts_words_not_characters():
    assert longest_shared_span("a b c d", "x b c d y") == 3
    assert (
        longest_shared_span("Mana value of a spell", "the mana value of a spell") == 5
    )
    assert longest_shared_span("nothing here", "elsewhere entirely") == 0


def test_the_checked_in_rules_plans_do_not_quote_their_key():
    """The margin the threshold rests on, asserted rather than remembered."""
    labels = load_rules_support_labels()
    if labels is None:
        pytest.skip("no rules-support labels")
    plans = load_hand_written_plans()
    quotes = {
        label.question_id: [entry.quote for entry in label.quoted_evidence]
        for label in labels.labels
    }
    assert plans_quoting_the_key(plans, quotes) == {}
    # The margin is exactly one word, and this pins it: legitimate paraphrase
    # in the checked-in plans DOES share five-word runs with the key ("when a
    # spell is cast" is the asker's vocabulary and the rule's), and none
    # shares six. A future plan that lands here at six is quoting.
    at_five = plans_quoting_the_key(plans, quotes, span_words=KEY_SPAN_WORDS - 1)
    assert at_five, "no five-word overlap at all; re-measure the threshold's margin"


def test_a_lookup_pasting_the_key_is_reported():
    labels = load_rules_support_labels()
    if labels is None:
        pytest.skip("no rules-support labels")
    plans = load_hand_written_plans()
    label = labels.labels[0]
    quote = label.quoted_evidence[0].quote
    hand = plans.by_question_id[label.question_id]
    lookup = next(s for s in hand.plan.steps if isinstance(s, RulesLookupStep))
    pasted = lookup.model_copy(update={"question": quote[:400]})
    plan = hand.plan.model_copy(
        update={"steps": tuple(pasted if s is lookup else s for s in hand.plan.steps)}
    )
    tampered = plans.model_copy(
        update={
            "plans": tuple(
                h.model_copy(update={"plan": plan}) if h is hand else h
                for h in plans.plans
            )
        }
    )
    report = plans_quoting_the_key(tampered, {label.question_id: [quote]})
    assert report == {label.question_id: (quote,)}
