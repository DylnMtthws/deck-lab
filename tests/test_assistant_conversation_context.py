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
    assert set(PreferenceAssessment.model_json_schema()["required"]) == set(empty) - {
        "scoped_preferences"
    }
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


def test_current_request_quote_has_no_provider_regex_and_checks_meaningful_length():
    import pytest
    from pydantic import ValidationError

    from sabermetrics.assistant_conversation_context import PreferenceAssessment
    from sabermetrics.assistant_research_task import PlanningResearchTask

    field = PlanningResearchTask.model_json_schema()["properties"][
        "current_request_quote"
    ]
    assert "pattern" not in field and field["minLength"] == 1
    base = {
        "kind": "compare",
        "deliverables": ["Compare options"],
        "preference_assessment": {
            key: None for key in PreferenceAssessment.model_fields
        },
    }
    with pytest.raises(ValidationError):
        PlanningResearchTask.model_validate({**base, "current_request_quote": " " * 8})
    brief = PlanningResearchTask.model_validate({**base, "current_request_quote": "I"})
    with pytest.raises(ValueError, match="current user request"):
        brief.to_task("I want a meaningful comparison of these candidates.")
    short = PlanningResearchTask.model_validate({**base, "current_request_quote": "Hi"})
    assert short.to_task("Hi").current_request_quote == "Hi"


def test_explicit_core_and_compound_rule_survive_without_shared_row_cap():
    from sabermetrics.assistant_conversation_context import explicit_preferences

    request = (
        "Hard rule: noncreature artifacts or instants only. "
        "My six core cards are: Winter Orb; Static Orb; Dramatic Reversal; "
        "Isochron Scepter; Sol Ring; Skullclamp."
    )
    turns = [{"role": "user", "id": "u1", "content": request}]
    memory = merge_preferences([], explicit_preferences(request), turns)
    assert [r["value"] for r in memory if r["dimension"] == "core_card"] == [
        "Winter Orb",
        "Static Orb",
        "Dramatic Reversal",
        "Isochron Scepter",
        "Sol Ring",
        "Skullclamp",
    ]
    assert (
        next(r for r in memory if r["dimension"] == "constraint")["quote"]
        == "Hard rule: noncreature artifacts or instants only"
    )
    for i in range(12):
        quote = f"Exclude rejected card {i} from recommendations"
        turns.append({"role": "user", "id": f"e{i}", "content": quote})
        memory = merge_preferences(
            memory,
            [
                PreferenceUpdate(
                    dimension="excluded_card",
                    value=f"Card {i}",
                    strength="rejected",
                    quote=quote,
                )
            ],
            turns,
        )
    assert len(memory) == 19
    messages = turns + [
        {"role": "assistant", "research": {"conversation_preferences": memory}}
    ]
    assert load_preferences(messages) == memory


def test_legacy_poison_and_unexpressed_defaults_are_rejected():
    request = "Hard rule: noncreature artifacts or instants only. My core cards are: Winter Orb; Static Orb. Prefer graveyard value."
    bad = [
        PreferenceUpdate(
            dimension="card_type",
            value="creature",
            strength="required",
            quote="Hard rule: noncreature artifacts or instants only",
        ),
        PreferenceUpdate(
            dimension="excluded_card",
            value="Winter Orb",
            strength="required",
            quote="My core cards are: Winter Orb; Static Orb",
        ),
        PreferenceUpdate(
            dimension="draw_control",
            value="optional",
            strength="preferred",
            quote="Prefer graveyard value",
        ),
        PreferenceUpdate(
            dimension="goal",
            value="late-game draw density",
            strength="preferred",
            quote="Prefer graveyard value",
        ),
        PreferenceUpdate(
            dimension="mana",
            value="max 2",
            strength="required",
            quote="Prefer graveyard value",
        ),
    ]
    turns = [{"role": "user", "id": "u1", "content": request}]
    assert merge_preferences([], bad, turns) == []
    turns.append(
        {
            "role": "assistant",
            "research": {
                "conversation_preferences": [
                    dict(r.model_dump(), source_turn="u1") for r in bad
                ]
            },
        }
    )
    assert load_preferences(turns) == []


def test_role_specific_mana_and_narrow_instant_exception_keep_global_rule():
    from sabermetrics.assistant_conversation_context import explicit_preferences

    request = "Hard rule: permanents only in the 7 slots—no instants or sorceries."
    turns = [{"role": "user", "id": "u1", "content": request}]
    memory = merge_preferences([], explicit_preferences(request), turns)
    change = "Allow one protective instant as an exception. Prefer imprint candidates with mana value 2 or less."
    turns.append({"role": "user", "id": "u2", "content": change})
    scoped = PreferenceUpdate(
        dimension="mana",
        value="max 2",
        scope="imprint candidates",
        strength="preferred",
        quote="Prefer imprint candidates with mana value 2 or less",
    )
    memory = merge_preferences(memory, [*explicit_preferences(change), scoped], turns)
    assert any(
        r["quote"] == request[:-1] and r["strength"] == "required" for r in memory
    )
    assert any(
        r["operation"] == "relax" and "one protective instant" in r["quote"]
        for r in memory
    )
    mana = [r for r in memory if r["dimension"] == "mana"]
    assert len(mana) == 1 and mana[0]["scope"] == "imprint candidates"


def test_mana_rejection_does_not_establish_numeric_ceiling():
    request = "Harvester is too much mana."
    update = PreferenceUpdate(
        dimension="mana", value="max 2", strength="required", quote=request
    )
    assert merge_preferences([], [update], [{"role": "user", "content": request}]) == []


def test_structured_findings_require_exact_source_quotes_and_ignore_assistant_prose():
    from sabermetrics.assistant_conversation_context import (
        SessionFinding,
        load_session_findings,
        merge_session_findings,
    )

    source = "Latest goldfish sample: 100 games; turn-five success 23 games."
    finding = SessionFinding(
        kind="empirical",
        claim="The supplied 100-game sample reports 23 turn-five successes.",
        evidence=[{"reference": "tool:goldfish:1", "quote": source}],
    )
    assert merge_session_findings([], [finding], evidence={}) == []
    assert (
        merge_session_findings(
            [], [finding], evidence={"tool:goldfish:1": "No measured result supplied."}
        )
        == []
    )
    memory = merge_session_findings(
        [], [finding], evidence={"tool:goldfish:1": source}, source_turn="u7"
    )
    messages = [{"role": "assistant", "content": "An invented success rate is 99%."}]
    assert load_session_findings(messages) == []
    messages.append({"role": "assistant", "research": {"session_findings": memory}})
    assert preference_context(messages)["session_findings"] == memory
    assert memory[0]["source_turn"] == "u7"


def test_newer_preference_wins_when_old_quote_is_reintroduced():
    old = "Prefer draw I can choose to use"
    new = "Allow any draw; relax my optional draw preference"
    turns = [
        {"role": "user", "id": "u1", "content": old},
        {"role": "user", "id": "u2", "content": new},
    ]
    earlier = PreferenceUpdate(
        dimension="draw_control", value="optional", strength="preferred", quote=old
    )
    later = PreferenceUpdate(
        dimension="draw_control",
        value="any",
        strength="preferred",
        operation="relax",
        quote=new,
    )
    memory = merge_preferences([], [later], turns)
    assert merge_preferences(memory, [earlier], turns) == memory


def test_soft_mana_preference_does_not_silently_replace_hard_ceiling():
    hard = "All recommendation mana costs must be at most 3."
    soft = "I prefer mana value 2 or less for draw engines."
    turns = [
        {"role": "user", "id": "u1", "content": hard},
        {"role": "user", "id": "u2", "content": soft},
    ]
    updates = [
        PreferenceUpdate(
            dimension="mana", value="max 3", strength="required", quote=hard
        ),
        PreferenceUpdate(
            dimension="mana", value="max 2", strength="preferred", quote=soft
        ),
    ]
    memory = merge_preferences([], updates, turns)
    assert {r["strength"] for r in memory} == {"required", "preferred"}
