import json

from sabermetrics.assistant_runtime import _encode_context, _retain_swap_context
from sabermetrics.card_economy import printed_cast_trigger_damage
from sabermetrics.functional_candidates import oracle_facts


def test_arithmetic_and_printed_damage_survive_context_eviction():
    old = {
        "name": "Discard Draw",
        "type_line": "Sorcery",
        "oracle_text": "You may discard a card. If you do, draw two cards.",
    }
    new = {"name": "Cantrip", "type_line": "Sorcery", "oracle_text": "Draw a card."}
    commander = {
        "name": "Engine",
        "oracle_text": "Whenever you cast a noncreature spell, put a +1/+1 counter on Engine and it deals 1 damage to each opponent.",
    }
    state = {
        "constraints": {},
        "commanders": [
            {
                "name": "Engine",
                "printed_cast_trigger_damage": printed_cast_trigger_damage(commander),
            }
        ],
    }
    _retain_swap_context(
        state,
        {
            "candidate_evidence": [
                {
                    "name": f"Cantrip {i}",
                    "incoming_oracle_facts": oracle_facts(new),
                    "outgoing_oracle_facts": oracle_facts(old),
                    "outgoing_card": old,
                }
                for i in range(3)
            ]
        },
    )
    encoded = _encode_context(
        state,
        "Compare the card flow and damage.",
        [{"role": "assistant", "content": "x" * 1200} for _ in range(10)],
    )
    assert len(encoded.encode()) <= 16000
    context = json.loads(encoded)["state"]
    assert (
        context["commanders"][0]["printed_cast_trigger_damage"][
            "printed_damage_per_opponent"
        ]
        == 1
    )
    for pair in context["swap_review"]["candidates"]:
        assert pair["incoming_card_economy"]["branches"][0]["net_hand_delta"] == 0
        assert [
            b["net_hand_delta"] for b in pair["outgoing_card_economy"]["branches"]
        ] == [0, -1]
