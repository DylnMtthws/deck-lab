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


def test_fixed_assessment_requires_every_dimension_and_canonical_draw_control():
    import pytest
    from pydantic import ValidationError

    from sabermetrics.assistant_conversation_context import PreferenceAssessment

    empty = {key: None for key in PreferenceAssessment.model_fields}
    assert set(PreferenceAssessment.model_json_schema()["required"]) == set(empty)
    assert (
        PreferenceAssessment.model_validate(empty).updates("No new preference here.")
        == []
    )
    missing = dict(empty)
    missing.pop("multifunctionality")
    with pytest.raises(ValidationError):
        PreferenceAssessment.model_validate(missing)
    invalid = {
        **empty,
        "draw_control": {
            "value": "voluntary_activation_preferred",
            "strength": "preferred",
            "quote": "I prefer controllable drawing",
        },
    }
    with pytest.raises(ValidationError):
        PreferenceAssessment.model_validate(invalid)
    invalid["draw_control"] = [invalid["draw_control"]] * 3
    with pytest.raises(ValidationError):
        PreferenceAssessment.model_validate(invalid)


def test_fixed_assessment_flattens_only_current_user_quote_evidence():
    from sabermetrics.assistant_conversation_context import PreferenceAssessment

    assessment = PreferenceAssessment.model_validate(
        {
            "goal": {
                "value": "late-game draw density",
                "strength": "preferred",
                "quote": "Prioritize late-game draw density",
            },
            "multifunctionality": {
                "value": "multiple useful roles",
                "strength": "preferred",
                "quote": "cards with several useful roles",
            },
            "draw_control": {
                "value": "optional",
                "strength": "preferred",
                "quote": "I prefer draw I can choose to use",
            },
            "card_type": None,
            "mana": {
                "value": "max 2",
                "strength": "required",
                "quote": "An obsolete earlier user constraint",
            },
            "excluded_cards": None,
        }
    )
    request = "Prioritize late-game draw density and cards with several useful roles. I prefer draw I can choose to use. Compare existing candidates."
    updates = assessment.updates(request)
    assert [row.dimension for row in updates] == [
        "goal",
        "multifunctionality",
        "draw_control",
    ]
    assert updates[-1].value == "optional"
    assert len({row.dimension for row in updates}) == len(updates)


def test_current_brief_rejects_historical_quote_instead_of_renewing_old_task():
    import pytest

    from sabermetrics.assistant_conversation_context import PreferenceAssessment
    from sabermetrics.assistant_research_task import PlanningResearchTask

    brief = PlanningResearchTask.model_validate(
        {
            "kind": "compare",
            "current_request_quote": "Create two initial lists",
            "deliverables": ["Compare actual candidates under the new priorities"],
            "preference_assessment": {
                key: None for key in PreferenceAssessment.model_fields
            },
        }
    )
    with pytest.raises(ValueError, match="current user request"):
        brief.to_task("Compare the candidates using my changed priorities.")
