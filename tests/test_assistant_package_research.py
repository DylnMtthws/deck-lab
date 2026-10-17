"""Package composition binds model-selected indices to current canonical facts."""

import json
from types import SimpleNamespace

import pytest

from sabermetrics import db
from sabermetrics.assistant_package_research import compose_package
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.assistant_tools import AssistantTools
from sabermetrics.deck_documents import DeckDocumentRepo

pytest_plugins = ["tests.test_assistant_experiments"]


@pytest.fixture
def composition_case(prepared, monkeypatch):
    service, path, owner, deck, document, alternatives = prepared
    texts = [
        "Sacrifice another creature: Scry 1.",
        "Whenever a creature you control dies, draw a card.",
        "{T}: Add {C}.",
        "{T}: Draw a card.",
    ]
    with db.connect(path) as conn:
        for i, text in enumerate(texts):
            conn.execute(
                "UPDATE cards SET oracle_text=?,mana_cost='{2}',cmc=2 WHERE id=?",
                (text, f"incoming-{i}"),
            )
        conn.commit()
        cards = [
            dict(
                conn.execute(
                    "SELECT * FROM cards WHERE id=?", (f"incoming-{i}",)
                ).fetchone()
            )
            for i in range(4)
        ]
    old = alternatives[0]["replacements"][0]["outgoing_entry_id"]
    zone = next(c["zone_id"] for c in document["entries"] if c["id"] == old)
    document = DeckDocumentRepo(path).apply_commands(
        owner,
        deck,
        expected_revision=document["revision"],
        mutation_id="package-composition-fixture",
        commands=[
            {"type": "set_quantity", "entry_id": old, "quantity": 97},
            {
                "type": "add_card",
                "card_id": "incoming-2",
                "quantity": 1,
                "zone_id": zone,
            },
            {
                "type": "add_card",
                "card_id": "incoming-3",
                "quantity": 1,
                "zone_id": zone,
            },
        ],
    )
    monkeypatch.setattr(
        "sabermetrics.assistant_candidate_recall.recall_candidates",
        lambda *a, **k: SimpleNamespace(
            candidates=[SimpleNamespace(card=c) for c in cards]
        ),
    )
    return service, path, owner, deck, document


class Composer:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def generate(self, request):
        self.calls.append(request)
        state = json.loads(request.user)
        assert len(request.user.encode()) <= 24576
        assert {c["name"] for c in state["incoming"]} == {"Incoming 0", "Incoming 1"}
        choices = [
            {
                "incoming_index": i,
                "outgoing_index": i,
                "loss": "Loses this permanent's repeatable tap ability.",
            }
            for i in range(2)
        ]
        if self.failure == "index":
            choices[0]["incoming_index"] = 19
        if self.failure == "quote":
            choices[0]["incoming_quote"] = "A fabricated ability clause."
        if self.failure == "duplicate":
            choices[1]["outgoing_index"] = 0
        if self.failure == "blocked":
            choices = []
        return SimpleNamespace(
            value={
                "choices": choices,
                "shared_interaction": (
                    "The return works only when mana value is ≤"
                    if self.failure == "unfinished"
                    else (
                        "Synergy."
                        if self.failure == "short"
                        else (
                            "Incoming 2 (new) makes the package work."
                            if self.failure == "existing_as_new"
                            else "Sacrificing another creature can trigger the second card's death draw ability."
                        )
                    )
                ),
                "conditions": [
                    "Both permanents and another creature must be available."
                ],
                "blocker": "",
            }
        )


@pytest.mark.parametrize(
    "fault", ["empty", "missing_interaction", "missing_conditions", "one_pair"]
)
def test_provider_schema_requires_complete_package_or_explained_abstention(
    composition_case, fault
):
    from pydantic import ValidationError

    service, path, owner, deck, document = composition_case
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: False, experiment_service=service
    )
    composer = Composer()

    def generate(request):
        value = composer.generate(request).value
        if fault == "empty":
            value["choices"] = []
        elif fault == "missing_interaction":
            del value["shared_interaction"]
        elif fault == "missing_conditions":
            value["conditions"] = []
        else:
            value["choices"] = value["choices"][:1]
        # These failures must reach the gateway's existing schema repair path,
        # rather than first being discovered after a successful provider call.
        with pytest.raises(ValidationError):
            request.schema.model_validate(value)
        value.update(
            choices=[],
            shared_interaction="",
            conditions=[],
            blocker="The supplied pool lacks a supported shared interaction.",
        )
        request.schema.model_validate(value)
        return SimpleNamespace(value=value)

    result = compose_package(
        tools, document, "package", 2, SimpleNamespace(generate=generate)
    )
    assert result["reason"] == "The supplied pool lacks a supported shared interaction."
    assert not result["hypotheses"]


@pytest.mark.parametrize(
    "failure",
    [None, "index", "quote", "duplicate", "existing_as_new", "short", "unfinished"],
)
def test_composition_is_bound_to_distinct_source_indices_and_exact_quotes(
    composition_case, failure
):
    service, path, owner, deck, document = composition_case
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: False, experiment_service=service
    )
    composer = Composer(failure)
    result = compose_package(tools, document, "sacrifice and death draw", 2, composer)
    assert len(composer.calls) == 1
    assert bool(result["hypotheses"]) is (failure is None)
    if failure is None:
        hypothesis = result["hypotheses"][0]
        assert len(hypothesis["replacements"]) == 2
        assert len(hypothesis["source_citations"]) == 2
        pool = json.loads(composer.calls[0].user)
        for citation in hypothesis["source_citations"]:
            for side in ("incoming", "outgoing"):
                assert citation[side + "_quote"] == next(
                    c["oracle_text"] for c in pool[side] if c["name"] == citation[side]
                )
        assert hypothesis["joint_strategic_review"] == "not_performed"
    assert tools.proposals == [] and not tools.reviewed_proposals
    assert DeckDocumentRepo(path).get(owner, deck) == document


def test_protected_cut_cannot_be_selected_and_no_provider_call_occurs(composition_case):
    service, path, owner, deck, document = composition_case
    tools = AssistantTools(
        path,
        owner,
        deck,
        "suggest",
        {"protected_cards": ["incoming-2"]},
        lambda: False,
        experiment_service=service,
    )
    composer = Composer()
    result = compose_package(tools, document, "package", 2, composer)
    assert result["status"] == "insufficient_evidence"
    assert not composer.calls


@pytest.mark.parametrize("owned", [False, True])
def test_runtime_routes_coordinated_brief_to_structured_package_without_extra_chat(
    composition_case,
    monkeypatch,
    owned,
):
    service, path, owner, deck, document = composition_case
    calls = []
    closed = []

    class Chat:
        provider = "test"
        model_id = "test"

        def close(self):
            closed.append("conversation")

        def generate(self, request):
            calls.append(request)
            return SimpleNamespace(
                value=AssistantStep.model_validate(
                    {
                        "research_task": {
                            "additions": 2,
                            "cuts": 2,
                            "coordinated": True,
                        },
                        "tool_calls": [
                            {
                                "name": "find_replacements",
                                "arguments": {"focus": "package"},
                            }
                        ],
                    }
                )
            )

    composer = Composer()
    composer.close = lambda: closed.append("package")
    built = []

    def build(settings, **kwargs):
        built.append(settings.model)
        if len(built) == 1:
            assert settings.model.max_validation_retries == 0
            return Chat()
        assert settings.model.max_retries == 0
        assert settings.model.max_validation_retries == 1
        assert settings.model.timeout_seconds == 60
        return composer

    monkeypatch.setattr("sabermetrics.assistant_runtime.build_gateway", build)
    result = run_turn(
        path,
        owner,
        deck,
        [
            {
                "role": "user",
                "content": "Two additions and two distinct cuts as a package. Suggestions only.",
            }
        ],
        gateway=None if owned else Chat(),
        package_research_gateway=None if owned else composer,
        answer_review_gateway=SimpleNamespace(
            generate=lambda request: SimpleNamespace(
                value={"patches": [], "unresolved": []}
            )
        ),
        experiment_service=service,
    )
    assert len(calls) == 1 and len(composer.calls) == 1
    assert closed == (["package", "conversation"] if owned else [])
    assert len(built) == (2 if owned else 0)
    assert result["status"] == "done"
    assert result["research"]["task"]["counts_complete"]
    assert "Incoming 0" in result["content"] and "Incoming 1" in result["content"]
    assert "not passed joint strategic review" in result["content"]
    assert result["proposals"] == [] and service.list_for_deck(owner, deck) == []
    assert DeckDocumentRepo(path).get(owner, deck) == document


def test_package_primitive_runs_at_most_once_per_turn(composition_case):
    service, path, owner, deck, _document = composition_case
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: False, experiment_service=service
    )
    composer = Composer()
    tools.package_gateway = composer
    first = tools.research_package("first", 2)
    second = tools.research_package("rephrased", 2)
    assert len(composer.calls) == 1 and second["already_completed"]
    assert first["hypotheses"] == second["hypotheses"]


@pytest.mark.parametrize("kind", ["provider", "schema", "cost"])
def test_failed_provider_degrades_to_specific_blocker(composition_case, kind):
    from sabermetrics.cedh.errors import ModelProviderError
    from sabermetrics.errors import LLMCostCeilingExceeded

    service, path, owner, deck, document = composition_case
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: False, experiment_service=service
    )

    def generate(request):
        if kind == "provider":
            raise ModelProviderError("private provider detail")
        if kind == "cost":
            raise LLMCostCeilingExceeded("Ceiling")
        return SimpleNamespace(value={"choices": [{"loss": "x" * 600}]})

    result = compose_package(
        tools, document, "package", 2, SimpleNamespace(generate=generate)
    )
    assert result["status"] == "insufficient_evidence" and not result["hypotheses"]
    assert "private" not in result["reason"]


def test_blocked_auto_route_never_reuses_an_unvalidated_chat_answer(composition_case):
    service, path, owner, deck, _ = composition_case

    class Chat:
        provider = "test"
        model_id = "test"

        def generate(self, request):
            return SimpleNamespace(
                value=AssistantStep.model_validate(
                    {
                        "content": "Unvalidated narrative claiming an existing card is new.",
                        "research_task": {
                            "additions": 2,
                            "cuts": 2,
                            "coordinated": True,
                        },
                    }
                )
            )

    result = run_turn(
        path,
        owner,
        deck,
        [],
        gateway=Chat(),
        package_research_gateway=Composer("blocked"),
        experiment_service=service,
    )
    assert result["status"] == "incomplete"
    assert "Unvalidated narrative" not in result["content"]
    assert "could not support" in result["content"]
    assert not result["proposals"]


def test_cancellation_during_composition_cannot_emit_a_hypothesis(composition_case):
    from sabermetrics.assistant_tools import ToolStopped

    service, path, owner, deck, document = composition_case
    stopped = [False]
    tools = AssistantTools(
        path, owner, deck, "suggest", {}, lambda: stopped[0], experiment_service=service
    )
    composer = Composer()

    def generate(request):
        result = composer.generate(request)
        stopped[0] = True
        return result

    with pytest.raises(ToolStopped):
        compose_package(
            tools, document, "package", 2, SimpleNamespace(generate=generate)
        )
    assert not tools.proposals
