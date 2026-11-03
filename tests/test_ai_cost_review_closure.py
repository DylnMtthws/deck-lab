"""Nested tools consume the same durable allowance as their parent."""

from dataclasses import replace

import pytest

from sabermetrics.ai_cost_control import Authority, BudgetDenied, bind_turn
from sabermetrics.assistant_tools import AssistantTools

pytest_plugins = ("tests.test_assistant_cost_web",)


@pytest.mark.parametrize("limit", [3, 5])
def test_nested_prepare_improvement_obeys_shared_tool_counter(web, monkeypatch, limit):
    from sabermetrics import assistant_deck_facts

    _client, path, owner, deck, original_control = web
    control = Authority(
        original_control.dsn + f".limit-{limit}",
        replace(original_control.config, tools=limit),
        local=True,
    )
    tid = control.begin_turn("main", owner, "nested-review", "review")
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    original = tools._execute
    nested = []

    def instrument(call, document):
        if call.name == "prepare_improvement":
            return original(call, document)
        nested.append(call.name)
        return {}

    monkeypatch.setattr(tools, "_execute", instrument)
    monkeypatch.setattr(assistant_deck_facts, "build_deck_facts", lambda doc: {})
    monkeypatch.setattr(assistant_deck_facts, "summarize_deck_facts", lambda facts: {})
    with bind_turn(control, tid, "review"):
        if limit == 3:
            with pytest.raises(BudgetDenied, match="tool limit"):
                tools.execute(
                    {"name": "prepare_improvement", "arguments": {"focus": "draw"}}
                )
        else:
            tools.execute(
                {"name": "prepare_improvement", "arguments": {"focus": "draw"}}
            )
    assert (
        nested
        == [
            "retrieve_strategy_evidence",
            "assess_deck_requirements",
            "find_replacements",
            "retrieve_rules",
        ][: limit - 1]
    )
    assert control.status()["turns"][tid]["tools"] == limit
