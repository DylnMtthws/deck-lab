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
    assert len(args.allowed_type_groups) == 2


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


def test_canonical_noncreature_artifact_or_instant_filters_before_candidate_limit(
    prepared,
):
    _, path, owner, deck, _, _ = prepared
    for index in range(20):
        insert(
            path,
            f"creature-decoy-{index}",
            f"AAA Draw Creature {index}",
            "Artifact Creature — Golem",
            "{T}: Draw a card.",
        )
    insert(path, "allowed-rock", "ZZZ Mana Artifact", "Artifact", "{T}: Add {C}{C}.")
    insert(
        path,
        "allowed-instant",
        "ZZZ Imprint Instant",
        "Instant",
        "Counter target spell.",
    )
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    tools.research_preferences = [
        preference(
            "constraint",
            "noncreature artifacts or instants only",
            quote="Hard rule: noncreature artifacts or instants only.",
        )
    ]
    result = tools.execute(
        call("search_card_functions", {"function": "any", "limit": 20})
    )
    assert {card["id"] for card in result["cards"]} >= {
        "allowed-rock",
        "allowed-instant",
    }
    assert not any(card["id"].startswith("creature-decoy-") for card in result["cards"])
    for card in result["cards"]:
        kind = card["type_line"].lower()
        assert "instant" in kind or ("artifact" in kind and "creature" not in kind)


@pytest.mark.parametrize(
    "special",
    [
        preference(
            "card_type_exception", "one protective creature", scope="explicit_exception"
        ),
        preference(
            "constraint", "artifacts with counter abilities or cheap instants only"
        ),
    ],
)
def test_narrow_exceptions_and_unsupported_compounds_keep_retrieval_broad(special):
    before = [
        preference("constraint", "noncreature artifacts or instants only"),
        special,
    ]
    assert (
        _functional_preference_args(
            FunctionalSearchArgs(type_line="artifact"), before
        ).allowed_type_groups
        == []
    )


def test_rocks_are_mana_only_for_explicit_unambiguous_role_labels():
    from sabermetrics.assistant_research_task import DiscoveryListPlan

    assert (
        DiscoveryListPlan(
            label="cheap rocks",
            query="mana rock mana value 2 or less",
            request_quote="cheap rocks",
        ).function
        == "mana"
    )
    assert (
        DiscoveryListPlan(
            label="mana rocks", query="mana rocks", request_quote="mana rocks"
        ).function
        == "mana"
    )
    assert (
        DiscoveryListPlan(
            label="rocks and instants",
            query="rocks and instants",
            request_quote="rocks and instants",
        ).function
        == "any"
    )


@pytest.mark.parametrize(
    "rule",
    [
        "noncreature artifacts or enchantments only",
        "noncreature artifacts or permanents only",
        "noncreature artifacts or lands only",
    ],
)
def test_ambiguous_noncreature_modifier_scope_falls_back_without_narrowing(rule):
    args = _functional_preference_args(
        FunctionalSearchArgs(type_line="artifact"), [preference("constraint", rule)]
    )
    assert args.type_line == ""
    assert args.allowed_type_groups == []


@pytest.mark.parametrize(
    "rule",
    [
        "noncreature artifacts or instants only",
        "noncreature artifacts or sorceries only",
        "noncreature artifacts or noncreature enchantments only",
    ],
)
def test_explicit_or_nonpermanent_alternatives_keep_exact_noncreature_scope(rule):
    args = _functional_preference_args(
        FunctionalSearchArgs(), [preference("constraint", rule)]
    )
    assert len(args.allowed_type_groups) == 2
    assert args.allowed_type_groups[0].excluded_types == ["creature"]


def test_multiple_required_disjunctions_never_replace_an_earlier_hard_rule():
    rows = [
        preference("constraint", "artifacts or instants only"),
        preference("constraint", "creatures or enchantments only"),
    ]
    before = deepcopy(rows)
    for ordered in (rows, list(reversed(rows))):
        args = _functional_preference_args(
            FunctionalSearchArgs(type_line="creature"), ordered
        )
        assert args.type_line == ""
        assert args.allowed_type_groups == []
    assert rows == before


@pytest.mark.parametrize(
    "directive",
    [
        "Hard rule: creatures only for new recommendations",
        "Only creatures.",
        "Hard rule: only creatures for new recommendations.",
        "creatures only",
    ],
)
def test_required_constraint_only_projects_exact_canonical_type(directive):
    args = _functional_preference_args(
        FunctionalSearchArgs(type_line="planeswalker"),
        [preference("constraint", directive, quote=directive)],
    )
    assert args.type_line == "creature"


@pytest.mark.parametrize(
    "unrelated",
    [
        preference("constraint", "mana value two or less"),
        preference("constraint", "use mana or life for costs"),
        preference("constraint", "sacrifice a creature or pay life"),
        preference("mana", "two or less"),
    ],
)
def test_unrelated_constraints_and_or_prose_do_not_clear_required_creature_filter(
    unrelated,
):
    required = preference(
        "constraint", "Hard rule: creatures only for new recommendations"
    )
    for rows in ([required, unrelated], [unrelated, required]):
        args = _functional_preference_args(FunctionalSearchArgs(), rows)
        assert args.type_line == "creature"


@pytest.mark.parametrize(
    "updates",
    [
        {"scope": "sacrifice fodder"},
        {"strength": "preferred"},
        {"polarity": "exclude"},
        {"operation": "relax"},
    ],
)
def test_single_type_constraint_projection_requires_general_required_positive_rule(
    updates,
):
    args = _functional_preference_args(
        FunctionalSearchArgs(), [preference("constraint", "creatures only", **updates)]
    )
    assert args.type_line == ""


def test_constraint_only_creature_rule_excludes_planeswalker_before_result_limit(
    prepared,
):
    _, path, owner, deck, _, _ = prepared
    for index in range(12):
        insert(
            path,
            f"walker-decoy-{index}",
            f"AAA Walker {index}",
            "Planeswalker",
            "+1: Draw a card.",
        )
    insert(
        path,
        "new-creature",
        "ZZZ Creature Candidate",
        "Creature",
        "When this creature enters, draw a card.",
    )
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    tools.research_preferences = [
        preference(
            "constraint",
            "Hard rule: creatures only for new recommendations",
            quote="Hard rule: creatures only for new recommendations",
        )
    ]
    result = tools.execute(
        call("search_card_functions", {"function": "draw", "limit": 1})
    )
    assert [card["id"] for card in result["cards"]] == ["new-creature"]


def test_constraint_only_single_type_keeps_narrow_exception_fallback():
    args = _functional_preference_args(
        FunctionalSearchArgs(),
        [
            preference(
                "constraint", "Hard rule: creatures only for new recommendations"
            ),
            preference(
                "card_type_exception",
                "one protective instant",
                scope="explicit_exception",
            ),
        ],
    )
    assert args.type_line == ""


@pytest.mark.parametrize(
    "second",
    [
        preference("constraint", "artifacts only"),
        preference("card_type", "artifact"),
    ],
)
def test_distinct_required_single_types_never_replace_an_earlier_constraint(second):
    first = preference(
        "constraint", "Hard rule: creatures only for new recommendations"
    )
    for rows in ([first, second], [second, first]):
        before = deepcopy(rows)
        args = _functional_preference_args(FunctionalSearchArgs(), rows)
        assert args.type_line == ""
        assert args.allowed_type_groups == []
        assert rows == before


@pytest.mark.parametrize(
    "comparison", ["less", "more", "fewer", "greater", "higher", "lower"]
)
def test_type_led_numeric_comparison_does_not_clear_required_type(comparison):
    rows = [
        preference("constraint", "creatures only"),
        preference("constraint", f"creatures with mana value two or {comparison}"),
    ]
    assert (
        _functional_preference_args(FunctionalSearchArgs(), rows).type_line
        == "creature"
    )


def test_type_alternatives_inside_unsupported_wrappers_keep_broad_fallback():
    for wrapper in [
        "Must be a creature or artifact",
        "Must be a creature with mana value two or less or an artifact",
    ]:
        rows = [preference("card_type", "creature"), preference("constraint", wrapper)]
        args = _functional_preference_args(
            FunctionalSearchArgs(type_line="creature"), rows
        )
        assert args.type_line == ""
        assert args.allowed_type_groups == []


def test_required_permanent_projection_remains_supported():
    assert (
        _functional_preference_args(
            FunctionalSearchArgs(), [preference("constraint", "Permanents only")]
        ).type_line
        == "permanent"
    )


def test_required_single_type_and_disjunction_never_drop_either_rule():
    first = preference("constraint", "creatures only")
    second = preference("constraint", "artifacts or enchantments")
    for rows in ([first, second], [second, first]):
        before = deepcopy(rows)
        args = _functional_preference_args(FunctionalSearchArgs(), rows)
        assert args.type_line == ""
        assert args.allowed_type_groups == []
        assert rows == before
