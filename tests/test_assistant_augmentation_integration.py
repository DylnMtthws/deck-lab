"""New augmentation paths must reach the existing guarded production tools."""

from sabermetrics.assistant_tools import TOOL_ADAPTER, AssistantTools

pytest_plugins = ["tests.test_assistant_experiments"]


def test_strategy_cache_restores_active_review_evidence_and_is_not_mutable(
    prepared, monkeypatch
):
    import sabermetrics.assistant_strategy_evidence as module

    _svc, path, owner, deck, _document, _alts = prepared

    def retrieve(path, query, **kwargs):
        return {
            "status": "available",
            "query": query,
            "excerpts": [{"excerpt": query, "citation": {"chunk_id": query}}],
        }

    monkeypatch.setattr(module, "retrieve_strategy_evidence", retrieve)
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)

    def execute(query):
        return tools.execute(
            {"name": "retrieve_strategy_evidence", "arguments": {"query": query}}
        )

    execute("alpha")
    execute("beta")
    result = execute("alpha")
    assert result["cache_hit"] and tools.strategy_evidence["query"] == "alpha"
    result["excerpts"][0]["excerpt"] = "tampered"
    assert execute("alpha")["excerpts"][0]["excerpt"] == "alpha"
    assert all("evidence" not in row for row in result["planned_evidence"])
    assert result["retrieval_reads"] == 1


def test_comparable_public_deck_tool_receives_current_guarded_document(
    prepared, monkeypatch
):
    import sabermetrics.assistant_comparable_decks as module

    _svc, path, owner, deck, document, _alts = prepared
    seen = []

    def comparable(path, current, **kwargs):
        seen.append((current["id"], current["revision"], kwargs))
        return {"status": "no_matches", "comparable_decks": []}

    monkeypatch.setattr(module, "comparable_decks", comparable)
    tools = AssistantTools(path, owner, deck, "suggest", {}, lambda: False)
    call = {
        "name": "research_comparable_decks",
        "arguments": {"window_days": 30, "limit": 2},
    }
    assert TOOL_ADAPTER.validate_python(call).name == "research_comparable_decks"
    assert tools.execute(call)["status"] == "no_matches"
    assert seen == [(deck, document["revision"], {"window_days": 30, "limit": 2})]
