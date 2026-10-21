"""Owned and injected workflow routing without paid provider calls."""

import json
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_deck_inference import (
    HypothesisVerification,
    SourceFirstDiscovery,
)
from sabermetrics.assistant_runtime import AssistantAnswerStep, run_turn
from sabermetrics.cedh.model_gateway import ReasoningMode
from tests.test_assistant_runtime import Gateway
from tests.test_assistant_whole_deck_runtime import complete_sources, explain_plan

pytest_plugins = ["tests.test_assistant_experiments"]


class InferenceGateway:
    provider = "test"
    model_id = "bounded-test-model"

    def __init__(self, question):
        self.question = question
        self.requests = []
        self.closed = False

    def close(self):
        self.closed = True

    def generate(self, request):
        self.requests.append(request)
        data = json.loads(request.user)
        if request.schema is SourceFirstDiscovery:
            assert "candidates" not in data
            entries = data["context"]["deck_entries"]["rows"]
            assert all(type(row[1]) is int and row[1] > 0 for row in entries)
            assert any(row[2] is True for row in entries)
            return SimpleNamespace(
                value=SourceFirstDiscovery.model_validate(
                    {
                        "plans": [
                            {
                                "summary": "Printed mana production could help pay a separate cost, conditional on access.",
                                "source_ids": [data["raw_inventory"][0][0]],
                                "assumptions": [
                                    "The source must be usable and all tap prerequisites must hold."
                                ],
                                "resource_renewal": "No untap or renewable use is established by this printed ability.",
                                "stopping_conditions": "The next use needs untapping and all timing conditions again.",
                            }
                        ],
                        "coverage_gaps": [
                            "A principal winning plan is not established by mana production alone."
                        ],
                    }
                )
            )
        assert request.schema is HypothesisVerification
        return SimpleNamespace(
            value=HypothesisVerification.model_validate(
                {
                    "assessments": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "source_ids": candidate["source_ids"],
                            "verdict": "conditional",
                            "explanation": "The printed mana ability produces its stated output if its cost and conditions can be satisfied.",
                            "conditions": [],
                            "resource_renewal": "No additional untap or renewal is established.",
                            "stopping_conditions": "An exhausted source cannot be assumed available for another activation.",
                            "strategic_tradeoff": "Ability access and competing mana demands remain unobserved.",
                        }
                        for candidate in data["candidates"]
                    ]
                }
            )
        )


@pytest.mark.parametrize("owned", [False, True])
def test_whole_deck_routes_to_two_inference_calls_without_old_synthesis(
    prepared, monkeypatch, owned
):
    service, path, owner, deck, _document, _ = prepared
    complete_sources(path)
    question = "Review this deck and explain its win condition."
    inference = InferenceGateway(question)
    profiles = []
    planner = Gateway([explain_plan(question)])
    reviewer = Gateway([])

    class Routed:
        provider = "test"
        model_id = "typed-mock"

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return (
                inference
                if request.schema in {SourceFirstDiscovery, HypothesisVerification}
                else planner
            ).generate(request)

    routed = Routed()

    def build(settings, **kwargs):
        profiles.append(settings.model)
        return inference if settings.model.timeout_seconds == 25 else planner

    monkeypatch.setattr("sabermetrics.assistant_runtime.build_gateway", build)
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": question}],
        gateway=None if owned else routed,
        answer_review_gateway=reviewer,
        experiment_service=service,
        request_planner=True,
        whole_deck_reviews=True,
        deck_inference=True,
    )
    assert len(planner.requests) == 1
    assert [r.schema for r in inference.requests] == [
        SourceFirstDiscovery,
        HypothesisVerification,
    ]
    assert not reviewer.requests
    assert all(r.schema is not AssistantAnswerStep for r in inference.requests)
    assert result["status"] == "incomplete"
    assert result["reason"] == "answer_partially_fulfilled"
    receipt = result["research"]["deck_inference"]
    assert receipt["status"] == "reviewed_partial"
    assert receipt["semantic_proof"] is False
    assert result["answer_review"]["fulfillment"] == "partial"
    assert "source must be usable" in result["content"]
    assert not any(a.get("type") == "tool_started" for a in result["actions"])
    assert receipt["source_manifest"]
    if owned:
        assert [(p.timeout_seconds, p.max_retries) for p in profiles] == [
            (60, 2),
            (25, 0),
        ]
        assert profiles[1].max_validation_retries == 0
        assert profiles[1].mode_profile(ReasoningMode.EXPLAIN).max_output_tokens == 2048
        assert inference.closed
    else:
        assert len(routed.requests) == 3
        assert not inference.closed
