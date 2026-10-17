"""Consulted printed card facts survive realistic context pressure.

Live QA: card_details succeeded, then the 16,000-character context evicted the
exact Oracle receipts while newer rules/graph state remained, and the model
compared the wrong costs and claimed the receipts were absent.
"""

import json

from sabermetrics import db
from sabermetrics.assistant_runtime import (
    AssistantStep,
    _encode_context,
    _retain_card_context,
    run_turn,
)
from tests.test_assistant_runtime import Gateway, call, step

pytest_plugins = ["tests.test_assistant_experiments"]

LIMIT = 16000
FILLER = "Whenever a qualifying permanent enters, evaluate the linked engine. "


def _text(seed: str, length: int) -> str:
    return (seed + " " + FILLER * (length // len(FILLER) + 1))[:length]


def card_payload(
    index: int,
    *,
    oracle_id: str | None = None,
    name: str | None = None,
    mana_cost: str | None = "{1}{U}",
    cmc: float | None = 2.0,
    type_line: str = "Instant",
    oracle_text: str | None = None,
    truncation: dict | None = None,
) -> dict:
    """Shape of AssistantTools card_details: card_facts + tool enrichment."""
    oracle_id = oracle_id or f"oracle-{index}"
    return {
        "card": {
            "id": f"card-{index}",
            "oracle_id": oracle_id,
            "name": name or f"Probe Card {index}",
            "layout": "normal",
            "mana_cost": mana_cost,
            "cmc": cmc,
            "type_line": type_line,
            "oracle_text": (
                oracle_text
                if oracle_text is not None
                else f"UNIQUE-TEXT-{index}: Counter target spell unless its controller pays {{3}}."
            ),
            "image_uri": None,
            "is_legal_in_99": 1,
            "roles": json.dumps(["interaction"]),
        },
        "status": "canonical_snapshot",
        "provenance": {
            "content_hash": f"sha-{index}",
            "version": "scryfall-oracle.2026-10-01",
            "observed_at": "2026-10-01T00:00:00+00:00",
            "retrieved_at": "2026-10-01T01:00:00+00:00",
            "source": "scryfall_snapshot",
            "date_status": "dated_snapshot",
            "freshness": "not_verified_current",
            "catalog_differences": [],
        },
        "legalities": {"values": {"commander": "legal"}, "observed_at": "2026-10-01"},
        "prices": None,
        "rulings": [
            {"published_at": "2024-01-01", "comment": _text(f"ruling {index}", 900)}
            for _ in range(8)
        ],
        "rulings_total": 8,
        "limitations": [
            "Snapshot evidence may be stale; no live freshness verification."
        ],
        "truncation": truncation or {"characters": 0, "items": 0},
        "commander_cast_interactions": [
            {
                "commander": "Test Commander",
                "matches_trigger": True,
                "trigger_category": "noncreature_spell",
                "reason": _text("noncreature spell cast", 400),
            }
        ]
        * 4,
        "card_economy": {"notes": [_text("economy", 600)] * 6},
        "capability_relationships": [
            {"kind": "enables", "detail": _text("rel", 500)} for _ in range(10)
        ],
        "rules_evidence": {
            "excerpts": [
                {"text": _text("CR 601.2", 1200), "citation": {"chunk_id": f"r{i}"}}
                for i in range(3)
            ]
        },
    }


def heavy_state() -> dict:
    """Large but ordinary turn state: deck facts, graph, rules and strategy."""

    def long(seed):
        return _text(seed, 1150)

    return {
        "mode": "suggest",
        "grounded_deck_facts": {
            "version": "deck-facts.v3",
            "features": [
                {"name": f"feature-{i}", "count": i, "examples": long(f"facts {i}")}
                for i in range(12)
            ],
        },
        "engine_graph": {
            "version": "engine-graph.v2",
            "links": [{"edge": i, "why": long(f"graph {i}")} for i in range(12)],
        },
        "rules_evidence": {
            "excerpts": [
                {"text": long(f"rule {i}"), "citation": {"chunk_id": f"c{i}"}}
                for i in range(4)
            ]
        },
        "strategy_context": {"notes": [long(f"strategy {i}") for i in range(6)]},
        "constraints": {"protected_cards": ["card-keep"]},
        "deck_id": "deck-1",
        "revision": 3,
        "validation": {"ok": True},
        "commanders": [
            {
                "name": "Test Commander",
                "card_id": "cmd-1",
                "oracle_id": "cmd-oracle",
                "mana_cost": "{1}{U}{R}",
                "type_line": "Legendary Creature — Wizard",
                "oracle_text": long("Whenever you cast a noncreature spell"),
            }
        ],
        "tool_budget": 8,
        "model_call_budget": 5,
        "workflow": {"completed_tools": ["card_details"] * 4},
    }


def heavy_transcript(card_receipts: list[dict]) -> list:
    """Card receipts first, then later large receipts that crowd them out."""
    later = [
        {
            "type": "tool_result",
            "name": "retrieve_rules",
            "ok": True,
            "data": {
                "excerpts": [
                    {"text": _text(f"later rule {n}.{i}", 1150)} for i in range(8)
                ]
            },
            "role": "tool",
        }
        for n in range(6)
    ]
    receipts = [
        {
            "role": "tool",
            "type": "tool_result",
            "name": "card_details",
            "ok": True,
            "result": {"name": "card_details", "data": data},
        }
        for data in card_receipts
    ]
    later = [{"role": "tool", "type": "tool_result", "result": item} for item in later]
    return [{"role": "user", "content": "Compare these two."}, *receipts, *later]


def decoded_cards(encoded: str) -> dict[str, dict]:
    payload = json.loads(encoded)
    rows = payload["state"].get("consulted_cards") or []
    return {row["card"]["oracle_id"]: row for row in rows}


def test_exact_two_card_costs_and_text_survive_large_state_and_receipts():
    state = heavy_state()
    a = card_payload(
        1,
        mana_cost="{X}{U}{U}",
        cmc=2.0,
        oracle_text="ALPHA: Counter target spell with mana value X.",
    )
    b = card_payload(
        2,
        mana_cost="{2}{R}",
        cmc=3.0,
        type_line="Sorcery",
        oracle_text="BETA: Exile the top three cards. You may play them this turn.",
    )
    _retain_card_context(state, a)
    _retain_card_context(state, b)
    encoded = _encode_context(
        state, "Is Alpha cheaper than Beta?", heavy_transcript([a, b])
    )

    assert len(encoded) <= LIMIT
    cards = decoded_cards(encoded)
    assert cards["oracle-1"]["card"]["mana_cost"] == "{X}{U}{U}"
    assert cards["oracle-1"]["card"]["oracle_text"] == a["card"]["oracle_text"]
    assert cards["oracle-1"]["card"]["type_line"] == "Instant"
    assert cards["oracle-2"]["card"]["mana_cost"] == "{2}{R}"
    assert cards["oracle-2"]["card"]["cmc"] == 3.0
    assert cards["oracle-2"]["card"]["oracle_text"] == b["card"]["oracle_text"]
    assert cards["oracle-2"]["card"]["id"] == "card-2"
    payload = json.loads(encoded)
    # The user request is pinned verbatim and the commander is never sacrificed.
    assert payload["current_user_request"] == "Is Alpha cheaper than Beta?"
    assert payload["state"]["commanders"][0]["mana_cost"] == "{1}{U}{R}"
    # Provenance travels with each retained card.
    assert cards["oracle-1"]["provenance"]["source"] == "scryfall_snapshot"
    assert cards["oracle-1"]["provenance"]["content_hash"] == "sha-1"


def test_four_long_cards_fit_or_supplemental_omission_is_explicit():
    state = heavy_state()
    payloads = [
        card_payload(i, oracle_text=f"CARD{i}-START " + _text(f"card {i}", 3900))
        for i in range(4)
    ]
    for data in payloads:
        _retain_card_context(state, data)
    original = {
        key: state[key]
        for key in (
            "engine_graph",
            "rules_evidence",
            "strategy_context",
            "grounded_deck_facts",
        )
    }
    encoded = _encode_context(state, "Rank these four.", heavy_transcript(payloads))

    assert len(encoded) <= LIMIT
    payload = json.loads(encoded)
    cards = decoded_cards(encoded)
    assert set(cards) == {f"oracle-{i}" for i in range(4)}
    for i in range(4):
        text = cards[f"oracle-{i}"]["card"]["oracle_text"]
        assert text.startswith(f"CARD{i}-START ")
        assert (
            payload["state"]["consulted_cards"][i]["card"]["oracle_id"] == f"oracle-{i}"
        )
    missing = [key for key in original if key not in payload["state"]]
    if missing:
        # Dropped supplemental evidence must be disclosed, never silent.
        assert set(missing) <= set(payload.get("state_evidence_omitted", []))
        assert "not evidence of absence" in payload["state_evidence_note"]


def test_repeated_identity_replaces_and_does_not_crowd_out_other_cards():
    state = heavy_state()
    for i in range(8):
        _retain_card_context(state, card_payload(i))
    # A different printing of the same Oracle card, with a corrected cost.
    reprint = card_payload(0, mana_cost="{U}", cmc=1.0)
    reprint["card"]["id"] = "card-0-reprint"
    for _ in range(3):
        _retain_card_context(state, reprint)

    rows = state["consulted_cards"]
    identities = [row["card"]["oracle_id"] for row in rows]
    assert sorted(identities) == [f"oracle-{i}" for i in range(8)]
    assert len(identities) == len(set(identities))
    latest = next(row for row in rows if row["card"]["oracle_id"] == "oracle-0")
    assert latest["card"]["mana_cost"] == "{U}"
    assert latest["card"]["id"] == "card-0-reprint"

    # A ninth distinct identity evicts the least recently consulted (oracle-1),
    # not the refreshed oracle-0.
    _retain_card_context(state, card_payload(8))
    identities = {row["card"]["oracle_id"] for row in state["consulted_cards"]}
    assert identities == {"oracle-0", *[f"oracle-{i}" for i in range(2, 9)]}
    assert state.get("consulted_cards_omitted", 0) >= 1


def _claims_complete(row: dict) -> bool:
    flat = json.dumps(row)
    return '"complete": true' in flat.lower() or '"truncated": false' in flat.lower()


def test_source_truncation_is_never_presented_as_complete():
    state = heavy_state()
    cut = _text("SOURCE-CUT", 4000)  # _bounded_response cuts strings at 4000
    data = card_payload(
        7,
        oracle_text=cut,
        truncation={
            "characters": 2210,
            "items": 3,
            "notice": "Evidence truncated for tool budget, including possible Oracle text; consult stored source for complete text.",
        },
    )
    _retain_card_context(state, data)
    row = state["consulted_cards"][-1]
    # Upstream truncation is carried with the card, not dropped.
    assert row["source_truncation"]["characters"] == 2210
    # Context clipping is a second, separately disclosed truncation.
    assert row["card"]["oracle_text"] != cut
    assert cut.startswith(row["card"]["oracle_text"])
    assert "oracle_text" in row["context_truncated_fields"]
    assert not _claims_complete(row)

    encoded_row = decoded_cards(_encode_context(state, "q", []))["oracle-7"]
    assert encoded_row["source_truncation"]["characters"] == 2210
    assert "oracle_text" in encoded_row["context_truncated_fields"]


def test_untruncated_short_text_is_not_marked_clipped():
    state = {}
    _retain_card_context(state, card_payload(8, oracle_text="Draw a card."))
    row = state["consulted_cards"][0]
    assert row["card"]["oracle_text"] == "Draw a card."
    assert row["context_truncated_fields"] == []


def test_unknown_cost_stays_unknown():
    state = {}
    _retain_card_context(
        state, card_payload(9, mana_cost=None, cmc=None, type_line="Land")
    )
    row = decoded_cards(_encode_context(state, "cost?", []))["oracle-9"]
    assert row["card"]["mana_cost"] is None
    assert row["card"]["cmc"] is None


def test_failed_or_ambiguous_sources_do_not_manufacture_card_facts():
    state = {"consulted_cards": []}
    _retain_card_context(state, {"card_id": "nope", "status": "missing"})
    _retain_card_context(
        state,
        {
            "status": "ambiguous",
            "requested_reference": "Twin Name",
            "note": "Exact name identifies multiple Oracle cards; use a catalog card ID.",
        },
    )
    # Bounded fallback shell with no identity is not a card.
    _retain_card_context(state, {"card": {"id": "", "oracle_id": "", "name": ""}})
    assert state["consulted_cards"] == []
    assert not state.get("consulted_cards_omitted")


def test_tool_loop_retains_consulted_cards_across_later_steps(prepared):
    service, path, owner, deck_id, _document, _alternatives = prepared
    long_a = "ALPHA-PRINTED " + _text("alpha", 2500)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET mana_cost=?, cmc=?, type_line=?, oracle_text=? WHERE id='incoming-0'",
            ("{X}{U}{U}", 2, "Instant", long_a),
        )
        conn.execute(
            "UPDATE cards SET mana_cost=NULL, cmc=NULL, type_line=?, oracle_text=? WHERE id='incoming-1'",
            ("Artifact", "BETA-PRINTED {T}: Add one mana of any color."),
        )
        conn.commit()
    gateway = Gateway(
        [
            step(
                call("card_details", {"card_id": "incoming-0"}),
                call("card_details", {"card_id": "Incoming 1"}),
                call("card_details", {"card_id": "no-such-card-anywhere"}),
            ),
            step(
                call("retrieve_rules", {"query": "casting spells cost X"}),
                call("inspect_deck", {}),
                call("retrieve_deck_facts", {"query": "mana rocks"}),
            ),
            AssistantStep.model_validate({"content": "Alpha costs {X}{U}{U}."}),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck_id,
        [{"role": "user", "content": "Compare Incoming 0 and Incoming 1 costs."}],
        gateway=gateway,
        experiment_service=service,
    )
    assert result["status"] == "done", result
    card_receipts = [
        a
        for a in result["actions"]
        if a.get("type") == "tool_result" and a["name"] == "card_details"
    ]
    assert [r["ok"] for r in card_receipts[:2]] == [True, True]

    # Every request after the card_details step, including the final one,
    # carries both cards even after newer receipts arrive.
    for request in gateway.requests[1:]:
        assert len(request.user) <= LIMIT
        cards = decoded_cards(request.user)
        assert set(cards) == {"incoming-oracle-0", "incoming-oracle-1"}
        alpha = cards["incoming-oracle-0"]["card"]
        assert alpha["mana_cost"] == "{X}{U}{U}"
        assert alpha["type_line"] == "Instant"
        assert alpha["oracle_text"].startswith("ALPHA-PRINTED ")
        assert long_a.startswith(alpha["oracle_text"])
        if alpha["oracle_text"] != long_a:
            assert (
                "oracle_text" in cards["incoming-oracle-0"]["context_truncated_fields"]
            )
        beta = cards["incoming-oracle-1"]["card"]
        assert beta["mana_cost"] is None and beta["cmc"] is None
        assert beta["oracle_text"] == "BETA-PRINTED {T}: Add one mana of any color."


def test_supplemental_shortening_retains_all_feature_counts():
    state = heavy_state()
    _retain_card_context(state, card_payload(1))
    encoded = _encode_context(state, "Which detected features are present?", [])
    payload = json.loads(encoded)
    actual = payload["state"]["grounded_deck_facts"]["features"]
    assert [(row["name"], row["count"]) for row in actual] == [
        (f"feature-{i}", i) for i in range(12)
    ]
    assert payload.get("state_evidence_shortened")
    assert "grounded_deck_facts" not in payload.get("state_evidence_omitted", [])
    assert "not evidence of absence" in payload["state_evidence_note"]


def test_consulted_facts_survive_generic_state_key_limit():
    state = {f"future-state-{i}": i for i in range(35)}
    _retain_card_context(state, card_payload(1))
    cards = decoded_cards(_encode_context(state, "What does this card cost?", []))
    assert cards["oracle-1"]["card"]["mana_cost"] == "{1}{U}"


def test_future_feature_categories_survive_generic_list_limit_with_and_without_pressure():
    features = [
        {"feature": f"future-{i}", "detected_quantity": i, "examples": ["A"]}
        for i in range(25)
    ]
    for pressure in (False, True):
        state = heavy_state() if pressure else {}
        state["grounded_deck_facts"] = {"features": features}
        _retain_card_context(state, card_payload(1))
        context = json.loads(_encode_context(state, "Compare all categories", []))
        actual = context["state"]["grounded_deck_facts"]["features"]
        assert [(row["feature"], row["detected_quantity"]) for row in actual] == [
            (f"future-{i}", i) for i in range(25)
        ]
