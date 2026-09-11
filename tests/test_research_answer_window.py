"""What ``top_k`` does, and what mechanic-013's pass is therefore evidence of.

mechanic-013 was written after mechanic-005's answer window was known to hide
March of Burgeoning Life, so it is informed by a development finding and its
pass is not independent evidence that the substrate generalizes. What its pass
CAN establish is narrower and mechanical: that the answer window, rather than
retrieval, is what hides the card. That claim holds only if the two plans
differ in nothing else and if ``top_k`` changes nothing but where the ranked
list is cut. Both halves are asserted here rather than asserted in prose.
"""

from __future__ import annotations

import pytest

from sabermetrics.assistant.eval.plans import load_hand_written_plans
from sabermetrics.substrate.models import CardFilters, CardSearchQuery

#: Fields that carry prose or naming rather than retrieval behaviour.
_PROSE = frozenset({"id", "note"})


def _retrieval_shape(step: dict[str, object]) -> dict[str, object]:
    """One step reduced to what actually reaches the retrieval pipeline."""
    return {key: value for key, value in step.items() if key not in _PROSE}


@pytest.fixture(scope="module")
def hand_written_plans():
    return {plan.question_id: plan for plan in load_hand_written_plans().plans}


def test_mechanic_013_differs_from_mechanic_005_only_in_the_answer_window(
    hand_written_plans,
):
    """The enumeration plan is the singular plan with a wider cut, and nothing else."""
    singular = hand_written_plans["mechanic-005"].plan.model_dump(mode="json")
    enumeration = hand_written_plans["mechanic-013"].plan.model_dump(mode="json")

    assert singular["context_id"] == enumeration["context_id"]
    assert len(singular["steps"]) == len(enumeration["steps"]) == 1
    narrow = _retrieval_shape(singular["steps"][0])
    wide = _retrieval_shape(enumeration["steps"][0])

    differing = {
        key for key in narrow.keys() | wide.keys() if narrow.get(key) != wide.get(key)
    }
    assert differing == {
        "query"
    }, f"plans differ outside the query: {sorted(differing)}"
    narrow_query = dict(narrow["query"])
    wide_query = dict(wide["query"])
    assert narrow_query.pop("top_k") == 10
    assert wide_query.pop("top_k") == 50
    assert narrow_query == wide_query, "the query differs in more than its bound"


def test_top_k_cuts_the_ranking_and_does_not_change_it(research_facade):
    """A narrower window is a prefix of a wider one over the same query.

    If ``top_k`` fed the candidate pools it would change which cards are
    compared and so could reorder the head of the list, and a card appearing
    only in the wider run would then implicate the bound without isolating it.
    It does not: the pools come from settings and ``top_k`` slices the final
    fusion, so the narrow result is exactly the wide result's prefix.
    """
    text = "Search your library for a creature card and put it onto the battlefield."
    filters = CardFilters(commander_legal=True)
    wide = research_facade.search(CardSearchQuery(text=text, filters=filters, top_k=12))
    narrow = research_facade.search(
        CardSearchQuery(text=text, filters=filters, top_k=4)
    )

    wide_ids = [hit.oracle_id for hit in wide.hits]
    narrow_ids = [hit.oracle_id for hit in narrow.hits]
    assert len(narrow_ids) == 4
    assert len(wide_ids) > len(narrow_ids), "the fixture is too small to show a cut"
    assert wide_ids[: len(narrow_ids)] == narrow_ids
    assert wide.eligible_cards == narrow.eligible_cards
