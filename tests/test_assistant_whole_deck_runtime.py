"""Whole-deck explanations preserve printed coverage without tool-selection calls."""

import json

import pytest

from sabermetrics import db
from sabermetrics.assistant_answer_review import AnswerReviewDecision
from sabermetrics.assistant_request_planner import RequestPlan
from sabermetrics.assistant_runtime import AssistantAnswerStep, run_turn
from tests.test_assistant_request_planner import plan
from tests.test_assistant_runtime import Gateway

pytest_plugins = ["tests.test_assistant_experiments"]


def explain_plan(request, *, empirical=False):
    raw = plan(request, empirical=empirical).model_dump()
    raw["research_task"]["kind"] = "explain"
    return RequestPlan.model_validate(raw)


def complete_sources(path, winning_id=None):
    from sabermetrics.assistant_card_facts import import_snapshot

    with db.connect(path) as conn:
        conn.execute("UPDATE cards SET oracle_text='{T}: Add {G}.', mana_cost='{G}'")
        if winning_id:
            conn.execute(
                "UPDATE cards SET oracle_text='You win the game.' WHERE id=?",
                (winning_id,),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT id,oracle_id,name,type_line,oracle_text,mana_cost FROM cards"
        ).fetchall()
    source = path.parent / "whole-deck-snapshot.json"
    source.write_text(json.dumps([{"object": "card", **dict(row)} for row in rows]))
    import_snapshot(path, source)


def test_whole_deck_explanation_skips_orchestration_and_keeps_review(prepared):
    service, path, owner, deck, _, _ = prepared
    complete_sources(path)
    question = "Review this deck and explain its win condition."
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="The supplied ingredients do not establish a complete winning sequence.",
                findings=[
                    "Printed mana production is a resource, not a lethal payoff."
                ],
            ),
        ]
    )
    reviewer = Gateway(
        [
            AnswerReviewDecision(
                fulfillment="partial", unresolved=["No lethal payoff is established."]
            )
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        answer_review_gateway=reviewer,
        experiment_service=service,
    )
    assert len(gateway.requests) == 2
    assert gateway.requests[1].schema is AssistantAnswerStep
    wire = json.loads(gateway.requests[1].user)
    assert wire["deck_evidence"]
    assert "resource renewal" in gateway.requests[1].system.lower()
    assert len(reviewer.requests) == 1
    assert not any(
        a.get("type") == "tool_started" and a.get("name") != "check_answer"
        for a in result["actions"]
    )
    assert result["answer_review"]["fulfillment"] == "partial"
    assert result["status"] == "incomplete"
    assert "not establish a complete winning sequence" in result["content"]
    assert json.loads(reviewer.requests[0].user)["research_context"][
        "deck_review_coverage"
    ]


def test_whole_deck_overflow_retains_normal_tool_route(prepared, monkeypatch):
    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr(
        "sabermetrics.assistant_deck_review.build_deck_review_packet",
        lambda *a, **kw: (None, [], {"status": "fallback", "reason": "packet_limit"}),
    )
    question = "Review this deck's win condition."
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="The inspected list has not established a winning sequence.",
                findings=["Printed ingredients alone do not establish lethal."],
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        experiment_service=service,
    )
    assert any(a.get("name") == "inspect_deck" for a in result["actions"])
    assert "deck_evidence" not in json.loads(gateway.requests[1].user)
    assert any(
        s.get("reason") == "packet_limit"
        for s in result["research"]["runtime"]["stages"]
    )


def test_ordinary_review_runs_with_165_seconds_remaining(prepared, monkeypatch):
    service, path, owner, deck, _, _ = prepared
    clock = [1000.0]
    monkeypatch.setattr(
        "sabermetrics.assistant_runtime.time.monotonic", lambda: clock[0]
    )

    class SlowGateway(Gateway):
        def generate(self, request):
            clock[0] += 315.0
            return super().generate(request)

    gateway = SlowGateway(
        [
            AssistantAnswerStep(
                content="The supplied deck records do not establish lethal damage.",
                findings=["Resource availability remains unknown."],
            )
        ]
    )
    reviewer = Gateway([AnswerReviewDecision(fulfillment="unknown")])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Explain the current list."}],
        gateway=gateway,
        answer_review_gateway=reviewer,
        experiment_service=service,
    )
    assert len(reviewer.requests) == 1
    assert result["answer_review"]["fulfillment"] == "unknown"


def test_engine_payload_never_truncates_preferences_or_sources():
    from sabermetrics.assistant_runtime import _deck_review_context

    packet = {"rows": [[i, "Complete " + "ability " * 10] for i in range(100)]}
    state = {
        "constraints": {"protected_card_ids": list(range(30))},
        "conversation_context": {
            "preferences": [{"quote": "keep " + str(i)} for i in range(20)]
        },
    }
    encoded = _deck_review_context(packet, state, "Review this deck.")
    assert encoded is not None
    wire = json.loads(encoded)
    assert wire["deck_evidence"] == packet
    assert wire["constraints"] == state["constraints"]
    assert wire["conversation_context"] == state["conversation_context"]
    assert (
        _deck_review_context({"oracle_text": "界" * 17000}, {}, "Review this deck.")
        is None
    )


@pytest.mark.parametrize(
    "scope,empirical", [("candidate_discovery", False), ("unknown", True)]
)
def test_discovery_and_measurement_do_not_enter_printed_deck_lane(
    prepared, monkeypatch, scope, empirical
):
    from tests.test_assistant_runtime import call, step

    service, path, owner, deck, _, _ = prepared

    def forbidden(*a, **kw):
        raise AssertionError("This request must keep orchestration tools")

    monkeypatch.setattr(
        "sabermetrics.assistant_deck_review.build_deck_review_packet", forbidden
    )
    question = (
        "Review this deck using actual measurements."
        if empirical
        else "Find additional creatures for this deck."
    )
    raw = explain_plan(question, empirical=empirical).model_dump()
    raw["research_task"]["evidence_scope"] = scope
    gateway = Gateway(
        [
            RequestPlan.model_validate(raw),
            step(call("inspect_deck")),
            AssistantAnswerStep(
                content="The requested additional evidence is not established.",
                findings=[
                    "Printed records do not establish measured results or additional candidates."
                ],
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        experiment_service=service,
    )
    assert "deck_review_evidence" not in result.get("research", {})
    assert len(gateway.requests) == 3


def test_failed_engine_review_keeps_explanation_with_uncertainty(prepared):
    from sabermetrics.cedh.errors import ModelProviderError

    service, path, owner, deck, _, _ = prepared
    complete_sources(path)
    question = "Review this deck's win conditions."
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="The deck can produce mana but no lethal payoff is established.",
                findings=[
                    "The printed resource abilities remain usable only when their costs are met."
                ],
            ),
        ]
    )
    reviewer = Gateway([ModelProviderError("offline failure")])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        answer_review_gateway=reviewer,
        experiment_service=service,
    )
    assert result["status"] == "incomplete"
    assert result["answer_review"]["status"] == "unverified"
    assert "printed resource abilities" in result["content"]
    assert "unverified" in result["content"]


def test_global_review_receives_unnamed_win_payoff(prepared):
    service, path, owner, deck, document, _ = prepared
    winning_id = next(
        c["card_id"] for c in document["entries"] if not c["is_commander"]
    )
    complete_sources(path, winning_id)
    question = "Review this deck and identify its win condition."
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="No printed win-the-game payoff is established.",
                findings=[
                    "The supplied source coverage still needs semantic assessment."
                ],
            ),
        ]
    )
    reviewer = Gateway(
        [
            AnswerReviewDecision(
                fulfillment="unknown",
                unresolved=["A supplied card explicitly wins the game."],
            )
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        answer_review_gateway=reviewer,
        experiment_service=service,
    )
    wire = json.loads(reviewer.requests[0].user)
    assert any("You win the game." in s for s in wire["sources"])
    assert result["answer_review"]["omitted_sources"] == 0
    assert result["status"] == "incomplete"


def test_dedicated_context_bypasses_generic_constraint_limit(prepared):
    service, path, owner, deck, _, _ = prepared
    complete_sources(path)
    question = "Review this deck and its win condition."
    constraints = {
        "excluded_candidates": ["excluded-" + str(i).zfill(36) for i in range(550)]
    }
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="The available printed resources do not establish lethal.",
                findings=["Current battlefield availability remains unknown."],
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        constraints=constraints,
        experiment_service=service,
    )
    assert len(gateway.requests) == 2
    assert json.loads(gateway.requests[1].user)["constraints"] == constraints
    assert result["status"] != "budget_exhausted"


def test_recoverable_rule_helper_failure_resumes_tools(prepared, monkeypatch):
    service, path, owner, deck, _, _ = prepared
    complete_sources(path)

    def unavailable(*args, **kwargs):
        raise OSError("offline failure must not leak")

    monkeypatch.setattr(
        "sabermetrics.assistant_engine_review.engine_rule_evidence", unavailable
    )
    question = "Review this deck and its win condition."
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="The inspected list remains incomplete as a winning sequence.",
                findings=["A local evidence supplement was unavailable."],
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        experiment_service=service,
    )
    assert any(a.get("name") == "inspect_deck" for a in result["actions"])
    assert "deck_evidence" not in json.loads(gateway.requests[1].user)
    assert result["reason"] != "turn_failed"


def test_outside_named_card_keeps_required_lookup(prepared, monkeypatch):
    from tests.test_assistant_conversation_runtime import seed_card

    service, path, owner, deck, _, _ = prepared
    seed_card(
        path,
        "outside-payoff",
        "Outside Payoff",
        "Whenever you draw a card, each opponent loses 1 life.",
    )
    question = "Review this deck and explain whether Outside Payoff contributes to its win condition."
    raw = explain_plan(question).model_dump()
    raw["research_task"]["named_cards"] = ["Outside Payoff"]

    def forbidden(*a, **kw):
        raise AssertionError("Outside source still needs normal research")

    monkeypatch.setattr(
        "sabermetrics.assistant_deck_review.build_deck_review_packet", forbidden
    )
    gateway = Gateway(
        [
            RequestPlan.model_validate(raw),
            AssistantAnswerStep(
                content="The outside payoff is a possible addition, not an existing deck ingredient.",
                findings=[
                    "Its printed trigger loses life when its controller draws a card."
                ],
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        experiment_service=service,
    )
    assert any(
        a.get("name") == "card_details" and a.get("type") == "tool_result"
        for a in result["actions"]
    )
    assert "Outside Payoff" in gateway.requests[1].user
    assert "deck_evidence" not in json.loads(gateway.requests[1].user)


def test_dedicated_context_preserves_retained_card_evidence():
    from sabermetrics.assistant_runtime import _deck_review_context

    state = {
        "consulted_cards": [
            {
                "card": {
                    "id": "outside",
                    "name": "Outside Payoff",
                    "oracle_text": "Whenever you draw a card, each opponent loses 1 life.",
                }
            }
        ]
    }
    assert (
        json.loads(_deck_review_context({"entries": []}, state, "Review this deck."))[
            "consulted_cards"
        ]
        == state["consulted_cards"]
    )


def test_packet_currentness_checks_do_not_reload_full_document_per_card(
    prepared, monkeypatch
):
    from sabermetrics.assistant_tools import AssistantTools

    service, path, owner, deck, _, _ = prepared
    checks = []
    original = AssistantTools.check

    def counted(self):
        checks.append(True)
        return original(self)

    def packet(*args, check_current, **kwargs):
        for _ in range(100):
            check_current()
        return None, {}, {"status": "fallback", "reason": "bounded_probe"}

    monkeypatch.setattr(AssistantTools, "check", counted)
    monkeypatch.setattr(
        "sabermetrics.assistant_deck_review.build_deck_review_packet", packet
    )
    question = "Review this deck and its win condition."
    gateway = Gateway(
        [
            explain_plan(question),
            AssistantAnswerStep(
                content="The inspected list does not establish a winning sequence.",
                findings=["The required payoff remains unknown."],
            ),
        ]
    )
    run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=gateway,
        request_planner=True,
        whole_deck_reviews=True,
        experiment_service=service,
    )
    assert len(checks) < 15


def test_engine_wire_deduplicates_metadata_without_losing_rule_evidence():
    from copy import deepcopy

    from sabermetrics.assistant_runtime import _deck_review_context

    hit = {
        "source_id": "CR 701.44a",
        "version": "snapshot",
        "content_sha256": "a" * 64,
        "source_url": "https://example.invalid/rules",
        "text": "Complete rule body with all conditions.",
        "text_truncated": False,
    }
    source = {k: v for k, v in hit.items() if k != "text"}
    rules = {
        "hits": [hit],
        "engine_evidence_dependencies": {
            "actions": [
                {
                    "term": "explore",
                    "status": "candidate_evidence",
                    "sources": [source],
                    "semantics_verified": False,
                }
            ]
        },
        "engine_evidence_omissions": [{**source, "reason": "incompatible_snapshot"}],
        "engine_lookups": [
            {
                "query": "explore",
                "status": "available",
                "query_failures": [],
                "elapsed_seconds": 3,
            }
        ],
    }
    original = deepcopy(rules)
    wire = json.loads(
        _deck_review_context(
            {"entries": []}, {"rules_evidence": rules}, "Review this deck."
        )
    )["rules_evidence"]
    assert rules == original
    assert wire["hits"] == rules["hits"]
    action = wire["engine_evidence_dependencies"]["actions"][0]
    assert action["source_refs"] == [0]
    assert action["semantics_verified"] is False
    assert wire["engine_evidence_omissions"][0]["reason"] == "incompatible_snapshot"
    assert wire["engine_lookups"][0]["query_failures"] == []


def test_global_review_keeps_rules_after_sixth_hit():
    from sabermetrics.assistant_answer_review import (
        SourceEvidence,
        whole_deck_evidence_sources,
    )

    hits = [
        {
            "source_id": "CR " + str(i),
            "text": "Complete relevant rule " + str(i) + ".",
            "text_truncated": False,
        }
        for i in range(8)
    ]
    sources = whole_deck_evidence_sources(SourceEvidence(), [], {"hits": hits})
    assert all(any(h["text"] in s for s in sources) for h in hits)
