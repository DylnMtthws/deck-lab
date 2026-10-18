"""Scoped research intent must not become an unsupported catalog hard filter."""

from copy import deepcopy

import pytest

from sabermetrics.assistant_functional_search import FunctionalSearchArgs
from sabermetrics.assistant_tools import AssistantTools, _functional_preference_args
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_experiments import prepared as _base_prepared
from tests.test_assistant_runtime import call

prepared = _base_prepared


def preference(dimension, value, **kwargs):
    return {
        "dimension": dimension,
        "value": value,
        "strength": "required",
        "scope": "recommendations",
        "operation": "set",
        "polarity": "include",
        **kwargs,
    }


def test_role_scoped_preferences_do_not_constrain_general_retrieval():
    rows = [
        preference("card_type", "creature", scope="sacrifice fodder"),
        preference("mana", "max 2", scope="imprint candidates"),
        preference("draw_control", "optional", scope="death triggers"),
    ]
    args = _functional_preference_args(FunctionalSearchArgs(), rows)
    assert args.type_line == ""
    assert args.mana_max is None
    assert args.optionality == "any"


@pytest.mark.parametrize("scope", ["recommendations", "general"])
def test_explicit_global_any_clears_inherited_type(scope):
    rows = [
        preference("card_type", "creature"),
        preference("card_type", "any", operation="relax", scope=scope),
    ]
    assert (
        _functional_preference_args(
            FunctionalSearchArgs(type_line="creature"), rows
        ).type_line
        == ""
    )


@pytest.mark.parametrize(
    "compound",
    [
        preference("card_type", "creature", quote="Creatures or artifacts only"),
        preference("constraint", "creatures or instants only"),
    ],
)
def test_disjunction_does_not_collapse_to_a_single_type(compound):
    args = _functional_preference_args(
        FunctionalSearchArgs(type_line="creature"),
        [compound, preference("card_type", "creature")],
    )
    assert args.type_line == ""


def test_narrow_exception_preserves_ledger_and_allows_exception_retrieval(
    prepared, monkeypatch
):
    _, path, owner, deck, _, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    rows = [
        preference("card_type", "permanent", quote="Permanents only"),
        preference(
            "constraint",
            "one protective instant",
            operation="relax",
            scope="explicit_exception",
            quote="Allow one protective instant as an exception",
        ),
        preference("mana", "max 2", scope="imprint candidates"),
    ]
    before = deepcopy(rows)
    tools.research_preferences = rows
    received = []
    monkeypatch.setattr(
        "sabermetrics.assistant_tools.functional_search",
        lambda path, document, args: received.append(args) or {"cards": []},
    )
    result = tools.execute(call("search_card_functions", {"type_line": "permanent"}))
    assert received[0].type_line == ""
    assert received[0].mana_max is None
    assert result["research_preferences"] == before
    assert rows == before


def test_core_membership_skips_new_candidates_without_becoming_rejection(prepared):
    _, path, owner, deck, _, _ = prepared
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    rows = []
    for index in range(10):
        name = f"AAA Core Engine {index}"
        insert(path, f"core-{index}", name, "Artifact", "{T}: Draw a card.")
        rows.append(preference("core_card", name))
    insert(path, "candidate", "ZZZ New Engine", "Artifact", "{T}: Draw a card.")
    tools.research_preferences = deepcopy(rows)
    result = tools.execute(call("search_card_functions", {"limit": 1}))
    assert [card["name"] for card in result["cards"]] == ["ZZZ New Engine"]
    assert result["research_preferences"] == rows
    assert all(
        row["dimension"] == "core_card" for row in result["research_preferences"]
    )
    # Deliberate inspection of existing/core cards still permits those names.
    args = _functional_preference_args(
        FunctionalSearchArgs(include_existing=True), rows
    )
    assert args.excluded_names == []


def test_retained_exclusions_are_not_truncated_at_model_argument_cap():
    rows = [
        preference("excluded_card", f"Rejected {index}", strength="rejected")
        for index in range(12)
    ]
    args = _functional_preference_args(FunctionalSearchArgs(), rows)
    assert args.excluded_names == [row["value"] for row in rows]


def test_negative_or_relaxed_type_is_never_turned_into_a_positive_filter():
    for row in [
        preference("card_type", "creature", polarity="exclude"),
        preference("card_type", "creature", operation="relax"),
        preference("card_type", "noncreature"),
    ]:
        assert (
            _functional_preference_args(FunctionalSearchArgs(), [row]).type_line == ""
        )
        assert (
            _functional_preference_args(
                FunctionalSearchArgs(type_line="creature"), [row]
            ).type_line
            == ""
        )
