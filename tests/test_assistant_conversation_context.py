from sabermetrics.assistant_conversation_context import (
    PreferenceUpdate,
    load_preferences,
    merge_preferences,
    preference_context,
)
from sabermetrics.assistant_research_task import ResearchTask, reconcile_task
from sabermetrics.assistant_runtime import _conversation


def test_tool_noise_does_not_erase_earlier_user_preferences():
    messages = [
        {"role": "user", "content": "I prefer optional draw and multifunctional cards."}
    ]
    messages += [{"role": "tool", "content": "receipt"} for _ in range(30)]
    messages += [{"role": "user", "content": "All additions must be creatures."}]
    assert len(_conversation(messages)) == 2
    context = preference_context(messages)
    assert "optional draw" in context["user_statements"][0]["text"]


def test_preferences_require_actual_user_quote_and_persist_with_provenance():
    messages = [
        {
            "id": "u1",
            "role": "user",
            "content": "All additions must be creatures. Harvester is too much mana.",
        }
    ]
    updates = [
        PreferenceUpdate(
            dimension="card_type",
            value="creature",
            strength="required",
            quote="All additions must be creatures",
        ),
        PreferenceUpdate(
            dimension="mana",
            value="max 2",
            strength="required",
            quote="Everything must cost two or less",
        ),
        PreferenceUpdate(
            dimension="excluded_card",
            value="Harvester of Souls",
            strength="rejected",
            quote="Harvester is too much mana",
        ),
    ]
    memory = merge_preferences([], updates, messages)
    assert [m["dimension"] for m in memory] == ["card_type", "excluded_card"]
    assert all(m["source_turn"] == "u1" for m in memory)
    messages.append(
        {
            "role": "assistant",
            "presentation": {"research": {"conversation_preferences": memory}},
        }
    )
    assert load_preferences(messages) == memory


def test_discovery_capacity_does_not_create_swaps_or_cut_obligations():
    task, _ = reconcile_task(
        ResearchTask(kind="discover", available_slots=7, additions=7, cuts=7),
        "I have 7 slots left. Give me options.",
    )
    assert task.additions == task.cuts == 0
    assert task.available_slots == 7
    explicit, _ = reconcile_task(
        ResearchTask(kind="fill"), "Add two cards and cut two cards."
    )
    assert explicit.additions == explicit.cuts == 2
