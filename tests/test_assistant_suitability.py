import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_answer_review import (
    evidence_sources,
    resolve_review_cards,
    review_answer,
)
from sabermetrics.assistant_functional_search import (
    FunctionalSearchArgs,
    functional_search,
)
from sabermetrics.assistant_strategy_benchmark import Suite, run_benchmark
from sabermetrics.assistant_suitability import (
    candidate_suitability,
    deck_support_index,
    render_fit_reason,
    synthesis_fit_context,
)
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_deck_intelligence import prepared as _base_prepared

prepared = _base_prepared


def profile(text, **overrides):
    return candidate_suitability(
        {"name": "Test", "oracle_text": text, "type_line": "Creature", **overrides},
        deck_support_index({"entries": []}),
    )


@pytest.mark.parametrize(
    "text",
    [
        "{T}: Draw X cards.",
        "{T}: You may draw a card.",
        "{T}: Draw a card for each artifact you control.",
        "{T}: Target player draws a card.",
        "{T}: Draw a card, then discard a card at random.",
        "{T}: If you control an artifact, draw a card.",
        "{T}: Draw a card. Exile a card from your hand.",
    ],
)
def test_complex_hand_accounting_abstains(text):
    assert profile(text)["resource_accounting"]["status"] == "unknown"


def test_sacrifice_draw_does_not_claim_net_card_advantage():
    result = profile("Pay 1 life, Sacrifice another creature: Draw a card.")
    accounting = result["resource_accounting"]
    assert accounting["net_hand_delta_per_activation"] == 1
    assert accounting["permanent_sacrifice_cost"] is True
    assert result["rules_interactions"]["status"] == "unverified"
    assert any("Life payment" in s for s in result["strategic_judgment"]["awkwardness"])


def test_source_coverage_and_sideboard_cannot_manufacture_support():
    document = {
        "entries": [
            {
                "name": "Sideboard token maker",
                "zone_name": "Sideboard",
                "oracle_text": "Create a 1/1 Soldier creature token.",
            }
        ]
    }
    result = candidate_suitability(
        {
            "name": "Outlet",
            "type_line": "Creature",
            "oracle_text": "Sacrifice another creature: Draw a card.",
        },
        deck_support_index(document),
    )
    assert all(
        row["detected_sources"] == 0 for row in result["strategic_judgment"]["support"]
    )
    assert result["coverage"]["library_entries_scanned"] == 0


def test_self_only_counters_cannot_replenish_another_card():
    document = {
        "entries": [
            {
                "name": "Self only",
                "zone_name": "Library",
                "type_line": "Creature",
                "oracle_text": "{1}: Put a +1/+1 counter on this creature.",
            }
        ]
    }
    assert deck_support_index(document)["groups"]["counters"] == []


def test_counter_types_are_not_interchangeable():
    index = deck_support_index(
        {
            "entries": [
                {
                    "name": "Plus counters",
                    "zone_name": "Library",
                    "type_line": "Creature",
                    "oracle_text": "Put a +1/+1 counter on target creature.",
                }
            ]
        }
    )
    result = candidate_suitability(
        {
            "name": "Charge draw",
            "oracle_text": "Remove a charge counter from this artifact: Draw a card.",
        },
        index,
    )
    counters = next(
        r
        for r in result["strategic_judgment"]["support"]
        if r["dimension"] == "counters"
    )
    assert counters["detected_sources"] == 0


def test_zombie_fodder_does_not_establish_elf_death_support():
    index = deck_support_index(
        {
            "entries": [
                {
                    "name": "Zombie Maker",
                    "zone_name": "Library",
                    "type_line": "Creature — Human",
                    "oracle_text": "Create a 2/2 Zombie creature token.",
                }
            ]
        }
    )
    result = candidate_suitability(
        {
            "name": "Elf draw",
            "oracle_text": "Whenever this creature or another Elf you control dies, you may pay 1 life. If you do, draw a card.",
        },
        index,
    )
    fodder = next(
        r for r in result["strategic_judgment"]["support"] if r["dimension"] == "fodder"
    )
    assert fodder["cards"] == []
    assert "Zombie Maker" not in render_fit_reason(result)


def test_truncated_or_multiface_card_never_has_certified_fit():
    for fields in ({"oracle_text_truncated": True}, {"card_faces": [{"name": "Back"}]}):
        result = profile("{T}: Draw a card.", **fields)
        assert result["resource_accounting"]["status"] == "unknown"
        assert result["strategic_judgment"]["status"] == "unverified"


def test_suitability_ranks_before_shortlist_limit(prepared):
    path, document = prepared
    for i in range(20):
        insert(
            path,
            f"loot-{i}",
            f"AAA Looter {i}",
            "Creature",
            "{T}: Draw a card, then discard a card.",
        )
    insert(path, "gain", "Z Genuine Draw", "Creature", "{T}: Draw a card.")
    before = copy.deepcopy(document)
    result = functional_search(
        path,
        document,
        FunctionalSearchArgs(query="draw", type_line="Creature", limit=1),
    )
    assert result["cards"][0]["id"] == "gain"
    assert document == before


def test_review_refreshes_every_discussed_candidate_not_just_last_eight(prepared):
    path, document = prepared
    candidates = []
    for i in range(14):
        cid, name = f"receipt-{i}", f"Named Source {i}"
        insert(
            path, cid, name, "Creature", "{1}, Sacrifice another creature: Draw a card."
        )
        candidates.append({"card_id": cid, "name": name})
    parts = ["; ".join(c["name"] for c in candidates)]
    refreshed = resolve_review_cards(path, document, parts, [], candidates)
    sources = evidence_sources(document, [], parts, additional_cards=refreshed)
    assert sum(s.startswith("Card: Named Source") for s in sources) == 14
    assert not any("Named Source" in label for label in sources.omitted)
    assert all(c.get("provenance") for c in refreshed)


def test_validation_and_literal_refresh_replace_truncated_receipts(prepared):
    path, document = prepared
    insert(
        path,
        "receipt",
        "Exact Source",
        "Creature",
        "{2}, {T}, Sacrifice another creature: Draw two cards.",
    )
    document["validation"] = {
        "legal": False,
        "total_cards": 93,
        "missing_library_cards": 7,
    }
    parts = ["Exact Source is in an incomplete 93-card deck."]
    stale = {
        "id": "receipt",
        "name": "Exact Source",
        "oracle_text": "Draw two",
        "oracle_text_truncated": True,
    }
    fresh = resolve_review_cards(path, document, parts, [stale])
    sources = evidence_sources(
        document,
        [
            {
                "type": "tool_result",
                "ok": True,
                "name": "card_details",
                "data": {"card": stale},
            }
        ],
        parts,
        additional_cards=fresh,
    )
    assert any('"missing_library_cards": 7' in source for source in sources)
    assert any(
        "{2}, {T}, Sacrifice another creature: Draw two cards." in s for s in sources
    )
    assert not any("excerpt; omissions unknown" in s for s in sources)


def test_reviewer_missing_evidence_stays_unverified():
    gateway = SimpleNamespace(
        generate=lambda _: SimpleNamespace(
            value={
                "fulfillment": "complete",
                "patches": [],
                "unresolved": ["The source does not establish the proposed loop."],
            }
        )
    )
    _, receipt = review_answer(
        gateway,
        ["Infinite draw."],
        ["An actual printed ingredient."],
        "Explain the loop",
    )
    assert receipt["status"] == "unverified"
    assert receipt["fulfillment"] == "complete"


def test_development_benchmark_has_denominators_and_no_live_quality_claim():
    suite = Suite.model_validate_json(
        Path("tests/fixtures/assistant_strategic_eval.json").read_text()
    )
    report = run_benchmark(suite)
    assert report["status"] == "pass"
    assert report["candidate_recall"]["denominator"] > 0
    assert report["recommendation_quality_pairs"]["denominator"] > 0
    assert report["latency"]["end_to_end_answer_seconds"] is None
    assert report["cost"]["configured_usd"] == 0
    with pytest.raises(ValueError, match="Empty split"):
        run_benchmark(suite, split="nonexistent")


def test_actual_mana_sources_are_potential_not_casting_probability():
    index = deck_support_index(
        {
            "entries": [
                {
                    "name": "Swamp",
                    "quantity": 3,
                    "type_line": "Basic Land — Swamp",
                    "zone_name": "Library",
                },
                {
                    "name": "Wrong zone",
                    "quantity": 20,
                    "type_line": "Basic Land — Island",
                    "zone_name": "Maybeboard",
                },
            ]
        }
    )
    result = candidate_suitability(
        {
            "name": "Draw",
            "oracle_text": "{B}, {T}: Draw a card.",
            "mana_cost": "{1}{B}",
        },
        index,
    )
    assert result["mana_context"]["detected_potential_color_sources"]["B"] == 3
    assert result["mana_context"]["detected_potential_color_sources"]["U"] == 0
    assert result["mana_context"]["status"] == "potential_sources_only"


def test_support_explanation_and_pinned_summary_survive_history_eviction():
    from sabermetrics.assistant_runtime import _encode_context

    index = deck_support_index(
        {
            "entries": [
                {
                    "name": "Soldier Maker",
                    "zone_name": "Library",
                    "type_line": "Creature",
                    "oracle_text": "Create a 1/1 Soldier creature token.",
                }
            ]
        }
    )
    card = {
        "id": "draw-source",
        "name": "Draw Source",
        "type_line": "Creature",
        "oracle_text": "Sacrifice another creature: Draw a card.",
    }
    card["suitability"] = candidate_suitability(card, index)
    explanation = render_fit_reason(card["suitability"])
    assert "Soldier Maker" in explanation and "Sacrifice consumes" in explanation
    summary = synthesis_fit_context([card])
    assert "oracle_text" not in json.dumps(summary)
    state = {"candidate_fit": summary}
    history = [{"role": "user", "content": ["x" * 1200] * 12} for _ in range(10)]
    packed = json.loads(_encode_context(state, "Recommend draw", history))
    assert packed["history_truncated"] is True
    assert packed["state"]["candidate_fit"]["cards"][0]["support"]["fodder"] == [
        "Soldier Maker"
    ]


def test_optional_fit_summaries_cannot_break_followup_context_budget():
    from sabermetrics.assistant_runtime import _encode_context

    source_text = "Complete Oracle source " * 360
    state = {
        "session_evidence": {
            "discovery_candidates": [
                {"card_id": "source", "printed_fields": {"oracle_text": source_text}}
            ]
        },
        "conversation_context": {
            "preferences": [
                {"dimension": "rule", "quote": str(i) * 1800, "source_turn": i}
                for i in range(3)
            ]
        },
        "prior_conclusions": ["Important conclusion " * 55 for _ in range(7)],
        "candidate_fit": {
            "cards": [
                {"card_id": str(i), "support": "Optional support " * 30}
                for i in range(8)
            ],
            "omitted": 0,
        },
    }
    packed = json.loads(_encode_context(state, "Seven additional draw creatures", []))
    assert len(json.dumps(packed, separators=(",", ":"))) <= 24000
    assert (
        packed["state"]["session_evidence"]["discovery_candidates"][0][
            "printed_fields"
        ]["oracle_text"]
        == source_text
    )
    assert packed["state"]["candidate_fit"]["omitted"] > 0


def test_review_preserves_printed_body_and_literal_face_text():
    text = "{T}: Draw a card.\nActivate only once each turn."
    sources = evidence_sources(
        {
            "entries": [
                {
                    "name": "Body",
                    "type_line": "Creature",
                    "power": "0",
                    "toughness": "0",
                    "oracle_text": "Body enters with X +1/+1 counters.",
                    "card_faces": [{"name": "Body", "oracle_text": text}],
                }
            ]
        },
        [],
        ["Body survives after returning."],
    )
    source = next(s for s in sources if s.startswith("Card: Body"))
    assert '"power": "0"' in source and '"toughness": "0"' in source
    assert text in source


def test_artifact_tap_warning_does_not_impose_creature_control_requirement():
    result = profile("{T}: Draw a card.", type_line="Artifact")
    assert "continuous control" not in " ".join(
        result["strategic_judgment"]["awkwardness"]
    )
    assert "untapped source" in " ".join(result["strategic_judgment"]["awkwardness"])


def test_one_token_provider_does_not_promote_resource_consumption_over_independent_draw(
    prepared,
):
    path, document = prepared
    next(c for c in document["entries"] if c.get("is_commander"))[
        "oracle_text"
    ] = "When this creature enters, create a 1/1 Construct artifact creature token."
    insert(
        path,
        "consume",
        "AAA Sacrifice Draw",
        "Artifact",
        "{1}, Sacrifice a creature: Draw a card.",
    )
    insert(path, "independent", "ZZZ Independent Draw", "Artifact", "{1}: Draw a card.")
    result = functional_search(
        path,
        document,
        FunctionalSearchArgs(query="repeatable draw", type_line="Artifact", limit=1),
    )
    assert result["cards"][0]["id"] == "independent"
