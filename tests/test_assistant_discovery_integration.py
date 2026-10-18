"""Final discovery guarantees survive synthesis and mechanical source checking."""

import json

import pytest

from sabermetrics.assistant_discovery_contract import (
    DiscoveryLedger,
    candidate_from_card,
)
from sabermetrics.assistant_runtime import AssistantStep, run_turn
from sabermetrics.assistant_session_state import (
    SessionState,
    session_state_research,
    update_session_state,
)
from tests.test_assistant_runtime import Gateway

pytest_plugins = ["tests.test_assistant_experiments"]


def retained_message(cards, revision):
    state = update_session_state(
        SessionState(),
        ledger=DiscoveryLedger(
            candidates=[candidate_from_card(card) for card in cards]
        ),
        evidence={f"card:{card['id']}": json.dumps(card) for card in cards},
        revision=revision,
    )
    return {
        "role": "assistant",
        "content": "Previously retrieved printed facts.",
        "research": session_state_research(state),
    }


def manifest(ids):
    return {
        "groups": [{"group_id": "list_1", "claimed_count": len(ids), "card_ids": ids}]
    }


def test_mechanical_checker_cannot_remove_a_required_candidate_silently(
    prepared, monkeypatch
):
    service, path, owner, deck, document, _ = prepared
    cards = [
        {"id": f"incoming-{i}", "name": f"Incoming {i}", "type_line": "Artifact"}
        for i in range(2)
    ]
    answer = AssistantStep(
        content="Ranked recommendations: Incoming 0 and Incoming 1.",
        discovery_answer=manifest([card["id"] for card in cards]),
    )

    def remove_candidate(_checker, parts, *_args, **_kwargs):
        return ["Ranked recommendation: Incoming 0.", *parts[1:]], {
            "status": "corrected",
            "fulfillment": "full",
        }

    monkeypatch.setattr(
        "sabermetrics.assistant_runtime.review_answer", remove_candidate
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            retained_message(cards, document["revision"]),
            {"role": "user", "content": "List two candidates."},
        ],
        gateway=Gateway([answer] * 5),
        answer_review_gateway=object(),
        experiment_service=service,
    )
    assert result["status"] != "done" or "Incoming 1" in result["content"], result
    if "Incoming 1" not in result["content"]:
        assert not result["research"]["discovery_validation"]["complete"]


@pytest.mark.parametrize("invalid_id", ["already_in_deck", "not_retrieved"])
def test_uncounted_discovery_manifest_still_checks_membership_and_retrieval(
    prepared, invalid_id
):
    service, path, owner, deck, document, _ = prepared
    card = next(row for row in document["entries"] if not row["is_commander"])
    card = {
        "id": card["card_id"],
        "name": card["name"],
        "type_line": card.get("type_line") or "Artifact",
    }
    if invalid_id == "not_retrieved":
        card_id, name = "made-up-id", "Made Up Recommendation"
    else:
        card_id, name = card["id"], card["name"]
    answer = AssistantStep(
        content=f"I recommend {name} as an additional card.",
        discovery_answer=manifest([card_id]),
    )
    result = run_turn(
        path,
        owner,
        deck,
        [
            retained_message([card], document["revision"]),
            {"role": "user", "content": "Find new candidate cards."},
        ],
        gateway=Gateway([answer] * 5),
        experiment_service=service,
    )
    assert result["status"] == "incomplete", result
    assert not result["research"]["discovery_validation"]["complete"]
