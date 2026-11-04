"""Literal bounded plans and their actual local retrieval adapter."""

import json

import pytest

from sabermetrics.assistant_retrieval_plan import (
    compile_retrieval_plan,
    run_retrieval_plan,
)
from sabermetrics.reference_layer.strategy_corpus import ingest_text


def test_compiler_is_deterministic_bounded_and_json_serializable():
    request = "Compare sacrifice draw versus combat draw: rules and loop prerequisites?"
    plan = compile_retrieval_plan(request)
    assert plan == json.loads(json.dumps(compile_retrieval_plan(request)))
    assert [task["kind"] for task in plan["tasks"]] == [
        "discovery",
        "prerequisites",
        "drawbacks",
        "comparison",
        "rules",
    ]
    assert all(len(task["query"]) <= 320 for task in plan["tasks"])
    assert len(compile_retrieval_plan("find draw cards")["tasks"]) == 1
    for invalid in ("", "x" * 4001):
        with pytest.raises(ValueError):
            compile_retrieval_plan(invalid)


def test_runner_uses_corpus_and_never_uses_authored_text_as_rules(tmp_path):
    db = tmp_path / "db"
    ingest_text(
        db,
        "Draw rules prerequisites and timing.",
        document="Original",
        source_url=None,
        version="v1",
        permission="original",
        source_kind="authored_guidance",
    )
    plan = compile_retrieval_plan("compare draw rules")
    calls = []
    result = run_retrieval_plan(
        db,
        plan,
        check_current=lambda: calls.append(True),
        cards=[
            {
                "name": "Example",
                "oracle_text": "Draw two cards.",
                "type_line": "Sorcery",
            }
        ],
    )
    assert len(calls) == 6
    assert result["results"][0]["evidence"]["excerpts"]
    assert result["results"][-1]["evidence"]["excerpts"] == []
    assert (
        result["card_views"]["results"][0]["card_views"]["views"]["printed_effect"][
            "evidence_class"
        ]
        == "supplied_source"
    )
    plan["tasks"][0]["limit"] = 100
    with pytest.raises(ValueError):
        run_retrieval_plan(db, plan)


def test_runner_propagates_cancel_or_revision_guards_before_reads(tmp_path):
    def stale():
        raise RuntimeError("revision changed")

    with pytest.raises(RuntimeError, match="revision changed"):
        run_retrieval_plan(
            tmp_path / "absent", compile_retrieval_plan("draw"), check_current=stale
        )


def test_runner_shares_strategy_reads_but_rules_are_a_separate_channel(
    tmp_path, monkeypatch
):
    import sabermetrics.assistant_strategy_evidence as retrieval

    calls = []

    def read(*args, **kwargs):
        calls.append(kwargs.get("source_kinds"))
        return {"excerpts": [], "status": "no_matches"}

    monkeypatch.setattr(retrieval, "retrieve_strategy_evidence", read)
    result = run_retrieval_plan(
        tmp_path / "db", compile_retrieval_plan("compare draw rules and prerequisites")
    )
    assert len(result["results"]) == 5
    assert result["retrieval_reads"] == 2
    assert calls == [None, {"official_rules"}]


def test_rules_task_returns_only_official_rules(tmp_path):
    db = tmp_path / "db"
    for title, kind in [
        ("Original strategy", "authored_guidance"),
        ("Official snapshot", "official_rules"),
    ]:
        ingest_text(
            db,
            "Draw rules timing and prerequisites.",
            document=title,
            source_url=None,
            version="v1",
            permission="test supplied fixture",
            source_kind=kind,
        )
    result = run_retrieval_plan(db, compile_retrieval_plan("draw rules timing"))
    rules = next(row for row in result["results"] if row["kind"] == "rules")["evidence"]
    assert rules["excerpts"]
    assert all(row["source_kind"] == "official_rules" for row in rules["excerpts"])


def test_aspects_use_distinct_passages_from_one_shared_read(tmp_path):
    path = tmp_path / "aspects.db"
    for title, text in [
        ("Setup", "Sacrifice draw setup requires creature fodder."),
        ("Drawback", "Sacrifice draw drawback is vulnerability to graveyard exile."),
        ("Other", "Sacrifice draw offers another alternative."),
    ]:
        ingest_text(
            path,
            text,
            document=title,
            source_url=None,
            version="v1",
            permission="original",
            source_kind="authored_guidance",
        )
    result = run_retrieval_plan(
        path, compile_retrieval_plan("Compare sacrifice draw"), limit=3
    )
    by_kind = {row["kind"]: row for row in result["results"]}
    assert (
        "setup"
        in by_kind["prerequisites"]["evidence"]["excerpts"][0]["excerpt"].lower()
    )
    assert (
        "drawback" in by_kind["drawbacks"]["evidence"]["excerpts"][0]["excerpt"].lower()
    )
    assert result["retrieval_reads"] == 1
    assert (
        by_kind["prerequisites"]["source_indices"]
        != by_kind["drawbacks"]["source_indices"]
    )
