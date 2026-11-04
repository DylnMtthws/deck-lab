import copy

import pytest

from sabermetrics.assistant_mechanical_trace import verify_trace


def card(text="Remove a charge counter from Widget: Add {C}.", **extra):
    return {
        "oracle_id": "widget",
        "name": "Widget",
        "oracle_text": text,
        "type_line": "Artifact",
        "mana_cost": "{1}",
        **extra,
    }


def obj(identity="widget", **extra):
    return {
        "id": identity,
        "card_id": "widget",
        "zone": "battlefield",
        "incarnation": 0,
        "counters": {"charge": 1},
        "tapped": False,
        "controlled_since_turn_start": True,
        "controller": "me",
        "owner": "me",
        **extra,
    }


def state(**extra):
    return {
        "no_external_effects": True,
        "player": "me",
        "active_player": "me",
        "priority_player": "me",
        "phase": "precombat_main",
        "stack_empty": True,
        "life": 20,
        "mana": dict.fromkeys("WUBRGC", 0),
        "objects": [obj()],
        **extra,
    }


def activate(c=None, **extra):
    c = c or card()
    return {
        "action": "activate",
        "source": "widget",
        "incarnation": 0,
        "ability_index": 0,
        "ability_quote": c["oracle_text"].splitlines()[0],
        **extra,
    }


def test_counter_cost_consumed_two_executions_not_infinite():
    initial = state(objects=[obj(counters={"charge": 2})])
    original = copy.deepcopy(initial)
    result = verify_trace([card()], initial, [activate(), activate()])
    assert result["status"] == "valid"
    assert result["final_state"]["mana"]["C"] == 2
    assert result["final_state"]["objects"][0]["counters"]["charge"] == 0
    assert "no loop" in result["reason"]
    assert initial == original
    failed = verify_trace([card()], initial, [activate()] * 3)
    assert failed["status"] == "invalid" and failed["failed_step"] == 2


def test_counter_renewal_separate_paid_ability():
    c = card(
        "Remove a charge counter from Widget: Add {C}.\n{2}: Put a charge counter on Widget."
    )
    initial = state(mana={**dict.fromkeys("WUBRGC", 0), "C": 2})
    renew = activate(
        c,
        ability_index=1,
        ability_quote=c["oracle_text"].splitlines()[1],
        mana_payment={"C": 2},
    )
    result = verify_trace([c], initial, [activate(c), renew, activate(c)])
    assert result["status"] == "valid" and result["final_state"]["mana"]["C"] == 2
    failed = verify_trace([c], state(), [activate(c), renew])
    assert failed["status"] == "invalid" and failed["failed_step"] == 1
    assert failed["final_state"]["mana"]["C"] == 1


def test_hand_return_resets_counters_incarnation_and_requires_paid_recast():
    c = card(
        "{0}: Return Widget to its owner's hand.\nRemove a charge counter from Widget: Add {C}."
    )
    bounce = activate(c, mana_payment={})
    cast = {
        "action": "cast",
        "source": "widget",
        "incarnation": 1,
        "mana_payment": {"C": 1},
    }
    result = verify_trace([c], state(), [bounce])
    returned = result["final_state"]["objects"][0]
    assert result["status"] == "valid" and returned["zone"] == "hand"
    assert returned["counters"] == {} and returned["incarnation"] == 1
    assert (
        verify_trace([c], state(), [bounce, activate(c, incarnation=1)])["status"]
        == "invalid"
    )
    assert verify_trace([c], state(), [bounce, cast])["status"] == "invalid"
    initial = state(mana={**dict.fromkeys("WUBRGC", 0), "C": 1})
    result = verify_trace([c], initial, [bounce, cast])
    assert result["status"] == "valid"
    assert result["final_state"]["objects"][0]["incarnation"] == 2
    assert result["final_state"]["objects"][0]["counters"] == {}
    counter = activate(
        c,
        incarnation=2,
        ability_index=1,
        ability_quote=c["oracle_text"].splitlines()[1],
    )
    assert verify_trace([c], initial, [bounce, cast, counter])["status"] == "invalid"
    assert (
        verify_trace([c], initial, [bounce, cast, activate(c)])["status"] == "invalid"
    )


def test_fodder_consumed_and_zone_change_clears_counters():
    c = card("Sacrifice another artifact: Gain 1 life.")
    initial = state(objects=[obj(), obj("fodder")])
    action = activate(c, sacrifice="fodder")
    result = verify_trace([c], initial, [action, action])
    assert result["status"] == "invalid" and result["failed_step"] == 1
    fodder = result["final_state"]["objects"][1]
    assert fodder["zone"] == "graveyard" and fodder["incarnation"] == 1
    assert fodder["counters"] == {} and result["final_state"]["life"] == 21


@pytest.mark.parametrize(
    "change",
    [
        {"ability_quote": "{0}: Add {C}."},
        {"ability_index": 1},
        {"incarnation": 1},
        {"source": "invented"},
    ],
)
def test_exact_ability_object_and_incarnation_binding(change):
    assert verify_trace([card()], state(), [activate(**change)])["status"] == "invalid"


@pytest.mark.parametrize(
    "change",
    [
        {"effect": {"mana": {"C": 100}}},
        {"state_change": {"counters": 20}},
        {"action": "renew_counter"},
        {"action": "return_to_battlefield"},
        {"action": "repeat_forever"},
    ],
)
def test_arbitrary_declared_state_change_unsupported(change):
    assert (
        verify_trace([card()], state(), [activate(**change)])["status"] == "unsupported"
    )


@pytest.mark.parametrize(
    "text",
    [
        "{T}: Add {C}. Activate only once each turn.",
        "{T}: Add {C}. Activate only as a sorcery.",
        "{T}: Add {C}.\nHaste",
        "{T}: Add {C}.\nWhen Widget dies, return it to the battlefield.",
        "Sacrifice Widget: Return it to the battlefield at the beginning of the next end step.",
        "{T}: Add one mana of any color.",
        "{T}: Untap target artifact.",
        "Remove a +1/+1 counter from Widget: Add {C}.",
    ],
)
def test_restrictions_triggers_delayed_returns_haste_and_unknown_semantics_abstain(
    text,
):
    c = card(text)
    assert verify_trace([c], state(), [activate(c)])["status"] == "unsupported"


def test_tap_cost_and_summoning_sickness_explicit():
    c = card("{T}: Add {C}.", type_line="Artifact Creature — Construct", toughness="1")
    assert verify_trace([c], state(), [activate(c)])["status"] == "valid"
    assert verify_trace([c], state(), [activate(c), activate(c)])["status"] == "invalid"
    sick = state(objects=[obj(controlled_since_turn_start=False)])
    assert verify_trace([c], sick, [activate(c)])["status"] == "invalid"
    artifact = card("{T}: Add {C}.")
    assert verify_trace([artifact], sick, [activate(artifact)])["status"] == "valid"


@pytest.mark.parametrize(
    "key", ["mana", "objects", "phase", "no_external_effects", "stack_empty"]
)
def test_initial_state_must_be_explicit(key):
    initial = state()
    del initial[key]
    assert verify_trace([card()], initial, [activate()])["status"] == "unsupported"


@pytest.mark.parametrize(
    "extra", [{"damage": 1}, {"token": True}, {"counters": {"stun": 1}}]
)
def test_unmodeled_state_is_not_silently_ignored(extra):
    initial = state(objects=[obj(**extra)])
    assert verify_trace([card()], initial, [activate()])["status"] == "unsupported"


def test_colored_and_generic_payment_exact_and_atomic():
    c = card("{U}{1}: Gain 2 life.")
    initial = state(mana={**dict.fromkeys("WUBRGC", 0), "U": 1, "C": 1})
    assert verify_trace([c], initial, [activate(c)])["status"] == "unsupported"
    assert (
        verify_trace([c], initial, [activate(c, mana_payment={"C": 2})])["status"]
        == "invalid"
    )
    result = verify_trace([c], initial, [activate(c, mana_payment={"U": 1, "C": 1})])
    assert result["status"] == "valid" and result["final_state"]["life"] == 22
    assert sum(result["final_state"]["mana"].values()) == 0


@pytest.mark.parametrize("when", [1, 2, 3, 4])
def test_cancellation_exceptions_propagate(when):
    count = 0

    def guard():
        nonlocal count
        count += 1
        if count == when:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        verify_trace([card()], state(), [activate()], check_current=guard)


def test_action_bound_and_wrong_zone():
    assert verify_trace([card()], state(), [activate()] * 33)["status"] == "unsupported"
    assert (
        verify_trace(
            [card()], state(objects=[obj(zone="hand", counters={})]), [activate()]
        )["status"]
        == "invalid"
    )


def test_costs_dont_refill_each_other_and_failure_is_atomic():
    c = card(
        "{1}, Remove a charge counter from Widget: Put a charge counter on Widget."
    )
    initial = state(
        objects=[obj(counters={})], mana={**dict.fromkeys("WUBRGC", 0), "C": 1}
    )
    result = verify_trace([c], initial, [activate(c, mana_payment={"C": 1})])
    assert result["status"] == "invalid" and result["final_state"] == initial


def test_sacrifice_self_consumes_exact_object():
    c = card("Sacrifice Widget: Add {C}.")
    result = verify_trace([c], state(), [activate(c)])
    assert result["status"] == "valid"
    assert result["final_state"]["mana"]["C"] == 1
    assert result["final_state"]["objects"][0]["zone"] == "graveyard"
    assert result["final_state"]["objects"][0]["counters"] == {}
    assert (
        verify_trace([c], state(), [activate(c), activate(c, incarnation=1)])["status"]
        == "invalid"
    )


def test_self_effect_after_sacrifice_abstains():
    c = card("Sacrifice Widget: Return Widget to its owner's hand.")
    assert verify_trace([c], state(), [activate(c)])["status"] == "unsupported"
