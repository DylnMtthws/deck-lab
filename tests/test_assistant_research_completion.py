"""Research can continue past a partial recommendation, without action authority."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_research_task import ResearchTask, progress
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.deck_documents import DeckDocumentRepo

pytest_plugins = ["tests.test_assistant_experiments"]


@pytest.fixture
def package_prepared(prepared):
    service, path, owner, deck, document, alternatives = prepared
    old = alternatives[0]["replacements"][0]["outgoing_entry_id"]
    zone = next(c["zone_id"] for c in document["entries"] if c["id"] == old)
    document = DeckDocumentRepo(path).apply_commands(
        owner,
        deck,
        expected_revision=document["revision"],
        mutation_id="two-cut-fixture",
        commands=[
            {"type": "set_quantity", "entry_id": old, "quantity": 98},
            {
                "type": "add_card",
                "card_id": "incoming-3",
                "quantity": 1,
                "zone_id": zone,
            },
        ],
    )
    return service, path, owner, deck, document, alternatives


class Gateway:
    provider = "test"
    model_id = "typed-test"

    def __init__(self, steps):
        self.steps = iter(steps)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        value = next(self.steps)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(value=value(request) if callable(value) else value)


def test_two_single_swaps_never_complete_a_two_card_package():
    task = ResearchTask(additions=2, cuts=2, coordinated=True)
    plans = [
        {
            "replacements": [
                {"incoming_card_id": f"new-{i}", "outgoing_entry_id": f"old-{i}"}
            ]
        }
        for i in range(2)
    ]
    result = progress(task, plans)
    assert result["missing"] == {"additions": 1, "cuts": 1}
    assert not result["counts_complete"]
    assert progress(task, [{"replacements": [p["replacements"][0] for p in plans]}])[
        "counts_complete"
    ]


def hypothesis(package_prepared):
    _, _, _, _, document, alternatives = package_prepared
    proposal = deepcopy(alternatives[0])
    second_cut = next(
        c
        for c in document["entries"]
        if not c["is_commander"]
        and c["id"] != proposal["replacements"][0]["outgoing_entry_id"]
    )
    proposal["replacements"].append(
        {"outgoing_entry_id": second_cut["id"], "incoming_card_id": "incoming-1"}
    )
    return {
        "proposal": proposal,
        "shared_interaction": "Two catalog-backed ingredients; interaction remains a research inference.",
        "conditions": ["Both permanents must be available."],
        "losses": [
            {
                "outgoing_entry_id": p["outgoing_entry_id"],
                "explanation": "Loses this current card and its functions.",
            }
            for p in proposal["replacements"]
        ],
    }


def test_premature_final_continues_to_constraint_checked_hypothesis(package_prepared):
    service, path, owner, deck, document, _ = package_prepared
    research = hypothesis(package_prepared)

    def finish(request):
        state = json.loads(request.user)["state"]
        assert state["research_task"]["missing"] == {"additions": 2, "cuts": 2}
        return AssistantStep.model_validate(
            {
                "content": "Two-card research hypothesis, not a validated upgrade.",
                "research_hypotheses": [research],
            }
        )

    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "content": "Only one candidate; choose another focus.",
                    "research_task": {"additions": 2, "cuts": 2, "coordinated": True},
                }
            ),
            finish,
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            {
                "role": "user",
                "content": "Research two additions and two distinct cuts as a package. Do not simulate.",
            }
        ],
        gateway=gateway,
        experiment_service=service,
    )
    assert len(gateway.requests) == 2
    assert result["research"]["task"]["counts_complete"]
    assert result["research"]["hypotheses"][0]["status"] == "research_hypothesis"
    assert result["proposals"] == []
    assert "not passed joint strategic review" in result["content"]
    assert service.list_for_deck(owner, deck) == []
    assert DeckDocumentRepo(path).get(owner, deck) == document


@pytest.mark.parametrize("invalid", ["invented", "duplicate_cut", "protected"])
def test_invalid_hypothesis_never_completes_or_becomes_actionable(
    package_prepared, invalid
):
    service, path, owner, deck, document, _ = package_prepared
    research = hypothesis(package_prepared)
    constraints = {}
    pairs = research["proposal"]["replacements"]
    if invalid == "invented":
        pairs[0]["incoming_card_id"] = "invented-anchor-id"
    elif invalid == "duplicate_cut":
        pairs[1]["outgoing_entry_id"] = pairs[0]["outgoing_entry_id"]
        research["losses"] = research["losses"][:1]
    else:
        constraints["protected_cards"] = [
            next(
                c["card_id"]
                for c in document["entries"]
                if c["id"] == pairs[0]["outgoing_entry_id"]
            )
        ]
    final = AssistantStep.model_validate(
        {
            "content": "Unsupported package draft.",
            "research_task": {"additions": 2, "cuts": 2, "coordinated": True},
            "research_hypotheses": [research],
        }
    )
    gateway = Gateway([final, final])
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Two additions and two cuts."}],
        constraints=constraints,
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "incomplete"
    assert result["reason"] == "research_incomplete"
    assert not result["research"]["task"]["counts_complete"]
    assert result["research"]["hypotheses"] == []
    assert not result["proposals"]
    assert DeckDocumentRepo(path).get(owner, deck) == document


def test_research_brief_cannot_be_reduced_to_hide_unfinished_work(package_prepared):
    service, path, owner, deck, _, _ = package_prepared
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {"additions": 2, "cuts": 2},
                    "content": "The requested research is partially complete.",
                }
            ),
            AssistantStep.model_validate(
                {
                    "research_task": {"additions": 0, "cuts": 0},
                    "content": "The requested research is complete.",
                }
            ),
        ]
    )
    result = run_turn(
        path, owner, deck, [], gateway=gateway, experiment_service=service
    )
    assert result["research"]["task"]["additions"] == 2
    assert result["status"] == "incomplete"


def test_research_brief_survives_context_eviction():
    from sabermetrics.assistant_runtime import _encode_context

    task = progress(ResearchTask(additions=2, cuts=2, coordinated=True), [])
    state = {
        **{f"key{i}": i for i in range(40)},
        "constraints": {},
        "research_task": task,
    }
    context = json.loads(
        _encode_context(state, "Two additions", [{"content": "x" * 10000}] * 4)
    )
    assert context["state"]["research_task"] == task


@pytest.mark.parametrize(
    "prompt,counts",
    [
        ("Two additions and two distinct cuts.", (2, 2)),
        ("Research a coherent two-card addition and two cuts.", (2, 2)),
        ("Give four swaps.", (4, 4)),
        ("Add 3 cards and remove two cards.", (3, 2)),
        ("Compare two cards without recommending swaps.", (0, 0)),
        ("Do not suggest two additions.", (0, 0)),
        ("Does Lotus Petal add one mana of any color?", (0, 0)),
        ("Can I remove two counters?", (0, 0)),
        ("Find three replacements for Sol Ring.", (3, 0)),
    ],
)
def test_explicit_cardinalities_cannot_be_silently_relaxed(prompt, counts):
    from sabermetrics.assistant_research_task import reconcile_task

    task, _ = reconcile_task(ResearchTask(), prompt)
    assert (task.additions, task.cuts) == counts


def test_explicit_package_cannot_be_relaxed_to_independent_alternatives():
    from sabermetrics.assistant_research_task import reconcile_task

    task, disagreements = reconcile_task(
        ResearchTask(additions=2, cuts=2, coordinated=False),
        "Research two additions and two distinct cuts as a package.",
    )
    assert task.coordinated and "coordinated" in disagreements
    alternatives = [
        {"replacements": [{"incoming_card_id": f"n{i}", "outgoing_entry_id": f"o{i}"}]}
        for i in range(2)
    ]
    assert not progress(task, alternatives)["counts_complete"]


def test_independent_choices_count_distinct_cards_without_claiming_a_joint_package():
    plans = [
        {"replacements": [{"incoming_card_id": f"n{i}", "outgoing_entry_id": f"o{i}"}]}
        for i in range(3)
    ]
    result = progress(ResearchTask(cuts=3, coordinated=False), plans)
    assert result["counts_complete"]
    assert not result["deliverables_evaluated"]


@pytest.mark.parametrize("tool", ["find_replacements", "start_experiment"])
def test_completed_reviewed_or_tested_package_counts_without_preview(
    package_prepared, monkeypatch, tool
):
    from sabermetrics.assistant_tools import AssistantTools

    service, path, owner, deck, _, _ = package_prepared
    proposal = hypothesis(package_prepared)["proposal"]

    def execute(self, call):
        if tool == "find_replacements":
            self.recommendations["reviewed"] = [proposal]
        else:
            self.experiments_started += 1
        return {
            "status": "reviewed" if tool == "find_replacements" else "completed",
            "alternatives": [proposal],
            "recommendation_id": "reviewed",
            "experiment_ready": True,
            "requires_semantic_review": False,
        }

    monkeypatch.setattr(AssistantTools, "execute", execute)
    args = (
        {"focus": "package"}
        if tool == "find_replacements"
        else {"alternatives": [proposal]}
    )
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {"additions": 2, "cuts": 2, "coordinated": True},
                    "tool_calls": [{"name": tool, "arguments": args}],
                }
            ),
            AssistantStep.model_validate(
                {"content": "The completed package receipt is available."}
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    assert len(gateway.requests) == 2
    assert result["status"] == "done"
    assert result["research"]["task"]["counts_complete"]
    assert "not all supported" not in result["content"]


def test_failed_continuation_keeps_partial_answer(package_prepared):
    from sabermetrics.cedh.errors import ModelProviderError

    service, path, owner, deck, _, _ = package_prepared
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {"additions": 2, "cuts": 2},
                    "content": "A useful partial audit.",
                }
            ),
            ModelProviderError("provider overloaded"),
        ]
    )
    result = run_turn(
        path, owner, deck, [], gateway=gateway, experiment_service=service
    )
    assert result["status"] == "incomplete"
    assert "useful partial audit" in result["content"]


def test_partial_fallback_preserves_subsequent_experiment_receipt(
    package_prepared, monkeypatch
):
    from sabermetrics.assistant_tools import AssistantTools
    from sabermetrics.cedh.errors import ModelProviderError

    service, path, owner, deck, _, _ = package_prepared
    proposal = hypothesis(package_prepared)["proposal"]

    def execute(self, call):
        self.experiments_started += 1
        return {"status": "completed", "alternatives": [proposal]}

    monkeypatch.setattr(AssistantTools, "execute", execute)
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "research_task": {"additions": 2, "cuts": 2, "coordinated": True},
                    "content": "Useful partial audit before follow-up.",
                }
            ),
            AssistantStep.model_validate(
                {
                    "tool_calls": [
                        {
                            "name": "start_experiment",
                            "arguments": {"alternatives": [proposal]},
                        }
                    ]
                }
            ),
            ModelProviderError("provider overloaded"),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [],
        mode="automate",
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "incomplete" and result["reason"] == "answer_incomplete"
    assert result["research"]["task"]["counts_complete"]
    assert "Useful partial audit" in result["content"]
    assert "Subsequent confirmed actions" in result["content"]
    assert "An isolated experiment was started" in result["content"]


def test_runtime_protects_numeric_experiment_result_parts(
    package_prepared, monkeypatch
):
    from sabermetrics.assistant_tools import AssistantTools

    service, path, owner, deck, _, _ = package_prepared
    proposal = hypothesis(package_prepared)["proposal"]
    protected = []

    def execute(self, call):
        self.experiments_started += 1
        return {"status": "completed", "alternatives": [proposal]}

    def check(gateway, parts, sources, current_request, **kwargs):
        protected.append(kwargs["protected_parts"])
        return parts, {"status": "no_supported_correction"}

    monkeypatch.setattr(AssistantTools, "execute", execute)
    monkeypatch.setattr("sabermetrics.assistant_runtime.review_answer", check)
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "tool_calls": [
                        {
                            "name": "start_experiment",
                            "arguments": {"alternatives": [proposal]},
                        }
                    ]
                }
            ),
            AssistantStep.model_validate(
                {"content": "Averaged a turn-4.2 win across 500 games."}
            ),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [],
        mode="automate",
        gateway=gateway,
        answer_review_gateway=object(),
        experiment_service=service,
    )
    assert protected == [{0}]
    assert result["content"] == "Averaged a turn-4.2 win across 500 games."


def test_late_partial_answer_does_not_start_a_continuation_without_research_room(
    package_prepared,
):
    service, path, owner, deck, _, _ = package_prepared
    reads = [
        AssistantStep.model_validate(
            {
                "research_task": {"additions": 2, "cuts": 2},
                "tool_calls": [
                    {"name": "inspect_deck", "arguments": {"query": f"topic {i}"}}
                ],
            }
        )
        for i in range(3)
    ]
    gateway = Gateway(
        [
            *reads,
            AssistantStep.model_validate({"content": "A useful late partial answer."}),
        ]
    )
    result = run_turn(
        path, owner, deck, [], gateway=gateway, experiment_service=service
    )
    assert len(gateway.requests) == 4
    assert result["status"] == "incomplete"
    assert "useful late partial answer" in result["content"]


@pytest.mark.parametrize("cancel", [False, True])
def test_final_source_check_updates_text_and_presentation_without_actions(
    package_prepared, cancel
):
    from sabermetrics import db
    from sabermetrics.assistant_answer_review import AnswerReview

    service, path, owner, deck, document, _ = package_prepared
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET oracle_text=? WHERE id='incoming-0'",
            ("You may play that card until the end of your next turn.",),
        )
        conn.commit()
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "tool_calls": [
                        {"name": "card_details", "arguments": {"card_id": "incoming-0"}}
                    ]
                }
            ),
            AssistantStep.model_validate(
                {"content": "Incoming 0 permits play until your next turn."}
            ),
        ]
    )
    stopped = [False]
    calls = []

    def generate(request):
        calls.append(request)
        payload = json.loads(request.user)
        source = next(
            i
            for i, text in enumerate(payload["sources"])
            if "until the end of your next turn" in text
        )
        stopped[0] = cancel
        return SimpleNamespace(
            value=AnswerReview.model_validate(
                {
                    "patches": [
                        {
                            "part_index": 0,
                            "claim": "until your next turn",
                            "replacement": "until the end of your next turn",
                            "citations": [
                                {
                                    "source_index": source,
                                    "quote": "until the end of your next turn",
                                }
                            ],
                        }
                    ]
                }
            )
        )

    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "What is the exact deadline?"}],
        gateway=gateway,
        answer_review_gateway=SimpleNamespace(generate=generate),
        experiment_service=service,
        should_stop=lambda: stopped[0],
    )
    assert len(calls) == 1
    if cancel:
        assert result["status"] == "stopped"
    else:
        # The local deadline correction is supported, while this fixture's
        # mandatory commander source has no Oracle text. Whole-answer review
        # must remain unverified rather than hide that missing evidence.
        assert result["answer_review"]["status"] == "unverified"
        assert result["answer_review"]["patches"]
        assert result["answer_review"]["omitted_sources"] > 0
        assert "until the end of your next turn" in result["content"]
        assert result["content"].startswith(result["presentation"]["reply"]["lead"])
        assert "remain unverified" in result["content"]
    assert result["proposals"] == []
    assert DeckDocumentRepo(path).get(owner, deck) == document
