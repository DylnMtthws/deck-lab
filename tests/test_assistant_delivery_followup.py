"""Context delivery: deck composition and reviewed plan IDs survive eviction."""

import json
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_deck_facts import library_composition
from sabermetrics.assistant_runtime import (
    AssistantStep,
    _encode_context,
    _reviewed_plans,
    run_turn,
)
from sabermetrics.deck_documents import DeckDocumentRepo

pytest_plugins = ["tests.test_assistant_experiments"]


def _entry(entry_id, name, type_line, quantity=1, mana_value=0, **extra):
    return {
        "id": entry_id,
        "card_id": "card-" + entry_id,
        "oracle_id": "oracle-" + entry_id,
        "name": name,
        "type_line": type_line,
        "quantity": quantity,
        "mana_value": mana_value,
        **extra,
    }


DOCUMENT = {
    "revision": 7,
    "entries": [
        _entry(
            "cmd",
            "Kinnan, Bonder Prodigy",
            "Legendary Creature",
            1,
            2,
            is_commander=True,
        ),
        _entry("isl", "Island", "Basic Land — Island", 12),
        _entry("for", "Forest", "Basic Land — Forest", 9),
        _entry("mdfc", "Sink into Stupor", "Instant // Land", 1, 3),
        _entry("spl", "Turntimber Symbiosis", "Sorcery // Land", 2, 7),
        _entry("rock", "Sol Ring", "Artifact", 1, 1),
        _entry("side", "Field of Ruin", "Land", 4, 0, zone_name="Sideboard"),
        _entry("cmdland", "Command Tower", "Land", 1, 0, is_commander=True),
    ],
}


def test_composition_counts_quantities_and_excludes_commander_and_sideboard():
    composition = library_composition(DOCUMENT)
    assert composition["front_land_count"] == 21  # 12 Island + 9 Forest
    assert composition["land_back_face_count"] == 3  # never folded into lands
    assert composition["library_card_count"] == 25
    assert composition["mana_curve"]["0"] == 21
    assert "not establish" in composition["land_count_note"]
    assert "colored-source" in composition["land_count_note"]
    assert "colored_source_count" not in composition


def test_composition_survives_source_receipt_eviction():
    state = {
        "constraints": {},
        "deck_composition": {"revision": 7, **library_composition(DOCUMENT)},
        **{f"filler-{i}": "y" * 1100 for i in range(8)},
        "engine_graph": {"edges": ["z" * 1100] * 12},
    }
    inspect = {
        "role": "tool",
        "name": "inspect_deck",
        "result": {"ok": True, "data": {"front_land_count": 21, "x": "q" * 9000}},
    }
    transcript = [inspect] + [
        {"role": "tool", "name": "retrieve_rules", "result": {"data": "r" * 9000}}
        for _ in range(6)
    ]
    context = json.loads(_encode_context(state, "How many lands?", transcript))
    # The inspect_deck receipt itself was evicted; the pinned summary was not.
    assert '"front_land_count": 21' not in json.dumps(context["conversation"])
    pinned = context["state"]["deck_composition"]
    assert pinned["front_land_count"] == 21
    assert pinned["land_back_face_count"] == 3
    assert pinned["revision"] == 7


ALT = {
    "name": "Swap Sol Ring",
    "rationale": "Reviewed.",
    "replacements": [{"outgoing_entry_id": "rock", "incoming_card_id": "card-new"}],
}


def _receipt(**overrides):
    return {
        "recommendation_id": "rec-1",
        "revision": 7,
        "experiment_ready": True,
        "alternatives": [ALT],
        "candidate_evidence": [{"card_id": "card-new", "name": "Mana Vault"}],
        **overrides,
    }


def test_reviewed_plans_keep_exact_typed_ids_and_names_separately():
    plans = _reviewed_plans(_receipt(), DOCUMENT)
    pair = plans["plans"][0]["replacements"][0]
    assert pair == {
        "outgoing_entry_id": "rock",
        "outgoing_name": "Sol Ring",
        "incoming_card_id": "card-new",
        "incoming_name": "Mana Vault",
    }
    assert plans["recommendation_id"] == "rec-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"experiment_ready": False},
        {"requires_semantic_review": True},
        {"recommendation_id": None},
        {"alternatives": []},
        {"alternatives": [{"name": "bad", "replacements": []}]},
        {"alternatives": [{**ALT, "replacements": [{"outgoing_entry_id": ""}]}]},
    ],
)
def test_pending_empty_or_invalid_plans_are_not_promoted(overrides):
    assert _reviewed_plans(_receipt(**overrides), DOCUMENT) is None


def test_plans_are_bounded_and_survive_key_cap_and_broad_receipts():
    many = [
        {**ALT, "name": f"P{i}", "replacements": [ALT["replacements"][0]] * 3}
        for i in range(3)
    ]
    plans = _reviewed_plans(_receipt(alternatives=many + [ALT]), DOCUMENT)
    assert len(plans["plans"]) == 3
    workflow = {f"k{i}": i for i in range(40)}
    workflow["reviewed_plans"] = plans  # past the 32-key cap
    state = {"constraints": {}, "workflow": workflow}
    broad = [
        {"role": "tool", "name": "assess", "result": {"data": "b" * 9000}}
        for _ in range(8)
    ]
    context = json.loads(_encode_context(state, "Preview the first plan", broad))
    kept = context["state"]["workflow"]["reviewed_plans"]
    assert kept == plans
    assert all(len(plan["replacements"]) == 3 for plan in kept["plans"])


def test_plan_omission_is_explicit_under_extreme_pressure():
    plans = _reviewed_plans(_receipt(), DOCUMENT)

    def state(size):
        return {
            "constraints": {},
            "workflow": {"reviewed_plans": plans},
            "consulted_cards": [{"oracle_text": "w" * 1100} for _ in range(12)],
            "consulted_cards_omitted": "o" * 1100,
            "commanders": [{"oracle_text": "c" * size}],
        }

    # Largest pinned state that fits only once the plans are dropped.
    budget = 16000 - len(json.dumps(plans)) + 50
    size = max(
        n
        for n in range(0, 1200, 10)
        if len(_encode_context({**state(n), "workflow": {}}, "x", [])) <= budget
    )
    assert 0 < size < 1190  # calibrated inside the range, not at an edge
    context = json.loads(_encode_context(state(size), "x", []))
    workflow = context["state"]["workflow"]
    assert "reviewed_plans" not in workflow
    assert workflow["reviewed_plans_omitted"]["plan_count"] == 1
    assert "Never pass names" in workflow["reviewed_plans_omitted"]["note"]


class Gateway:
    provider = "test"
    model_id = "typed-mock-model"

    def __init__(self, steps):
        self.steps = iter(steps)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        value = next(self.steps)
        if callable(value):
            value = value(request)
        return SimpleNamespace(value=value)


@pytest.mark.parametrize("discovery_tool", ["find_replacements", "prepare_improvement"])
def test_run_turn_previews_from_retained_ids_after_broad_receipts(
    prepared, monkeypatch, discovery_tool
):
    service, path, owner, deck_id, document, alternatives = prepared
    alternative = alternatives[0]
    import sabermetrics.assistant_deck_intelligence as intelligence
    import sabermetrics.assistant_swap_review as swap_review

    monkeypatch.setattr(
        intelligence,
        "recommend_replacements",
        lambda *a, **k: {
            "alternatives": [alternative],
            "candidate_evidence": [],
            "requires_semantic_review": True,
        },
    )
    monkeypatch.setattr(
        swap_review,
        "review_candidates",
        lambda *a, **k: {
            "status": "reviewed",
            "alternatives": [alternative],
            "candidate_evidence": [],
        },
    )

    def preview_from_state(request):
        state = json.loads(request.user)["state"]
        plan = state["workflow"]["reviewed_plans"]["plans"][0]
        pairs = [
            {
                "outgoing_entry_id": pair["outgoing_entry_id"],
                "incoming_card_id": pair["incoming_card_id"],
            }
            for pair in plan["replacements"]
        ]
        assert state["deck_composition"]["revision"] == document["revision"]
        return AssistantStep.model_validate(
            {
                "tool_calls": [
                    {
                        "name": "propose_replacements",
                        "arguments": {
                            "proposal": {
                                "name": plan["name"],
                                "rationale": "Reviewed plan.",
                                "replacements": pairs,
                            }
                        },
                    }
                ]
            }
        )

    broad = {"name": "inspect_deck", "arguments": {"query": ""}}
    gateway = Gateway(
        [
            AssistantStep.model_validate(
                {
                    "tool_calls": [
                        {"name": discovery_tool, "arguments": {"focus": "mana"}}
                    ]
                }
            ),
            AssistantStep.model_validate({"tool_calls": [broad, broad, broad]}),
            preview_from_state,
            AssistantStep.model_validate({"content": "Preview ready; unmeasured."}),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Suggest a swap and preview it."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done"
    discovery = next(
        a["data"]
        for a in result["actions"]
        if a.get("type") == "tool_result" and a.get("name") == discovery_tool
    )
    assert discovery["experiment_ready"] is True
    assert discovery["requires_semantic_review"] is False
    if discovery_tool == "prepare_improvement":
        assert discovery["plan_status"] == "ready_for_preview"
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["expected_revision"] == document["revision"]
    assert DeckDocumentRepo(path).get(owner, deck_id) == document
    assert "build-conversation.v32" == gateway.requests[0].prompt_version
