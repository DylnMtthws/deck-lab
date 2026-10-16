import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics import db
from sabermetrics.assistant_candidate_recall import card_content, recall_candidates
from sabermetrics.assistant_deck_intelligence import assess_deck_requirements
from sabermetrics.assistant_strategy import StrategyContext
from sabermetrics.combo_catalog import import_combo_snapshot
from tests.test_assistant_deck_intelligence import prepared as _base_prepared
from tests.test_combo_catalog import snapshot

prepared = _base_prepared


def recall(prepared, focus="", constraints=None):
    path, document = prepared
    strategy = StrategyContext.model_validate(
        assess_deck_requirements(document, constraints)["strategy_context"]
    )
    return recall_candidates(path, document, strategy, focus)


def insert(path, cid, name, kind, text, cmc=2, colors="[]", legal=1, oid=None):
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,color_identity,cmc,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?)",
            (cid, oid or cid, name, kind, text, colors, cmc, legal),
        )
        conn.commit()


def test_expensive_commander_ingredient_survives_generic_cheap_card_crowding(prepared):
    path, document = prepared
    commander = next(c for c in document["entries"] if c.get("is_commander"))
    commander["oracle_text"] = "Other Dragons you control get +1/+1."
    for index in range(120):
        insert(
            path, f"generic-{index}", f"AAA {index}", "Artifact", "Draw a card.", cmc=0
        )
    insert(path, "dragon", "Zzz Dragon", "Creature — Dragon", "Flying", cmc=9)
    before = deepcopy(document)
    result = recall(prepared, "draw")
    dragon = next(c for c in result.candidates if c.card["id"] == "dragon")
    assert "commander:tribe:dragon" in dragon.channels
    assert result.channel_counts["function:draw"] == 24
    assert document == before


def test_illegal_printing_does_not_hide_legal_canonical_card(prepared):
    path, _ = prepared
    insert(
        path,
        "aaa-illegal",
        "Other Printing",
        "Artifact",
        "Draw a card.",
        legal=0,
        oid="reprinted",
    )
    insert(
        path, "zzz-legal", "Other Printing", "Artifact", "Draw a card.", oid="reprinted"
    )
    result = recall(prepared, "draw")
    assert any(c.card["id"] == "zzz-legal" for c in result.candidates)
    assert not any(
        c.card["id"] in {"foreign", "illegal", "aaa-illegal"} for c in result.candidates
    )


def test_missing_semantic_and_empirical_channels_are_explicit(prepared):
    result = recall(prepared, "draw")
    assert result.channel_status["semantic"] == "unavailable_no_card_vectors"
    assert result.channel_status["combination"] == "unavailable"
    assert result.channel_status["association"] == "unavailable_no_nonland_anchor"
    assert result.candidates


def test_unknown_commander_colors_never_become_colorless(prepared):
    _, document = prepared
    next(c for c in document["entries"] if c.get("is_commander"))[
        "color_identity"
    ] = None
    result = recall(prepared)
    assert not result.candidates
    assert result.channel_status["corpus"] == "unknown_commander_colors"


def test_sourced_combo_completion_enters_discovery_with_attribution(prepared):
    path, document = prepared
    data = snapshot()
    data["results"] = data["results"][:1]
    variant = data["results"][0]
    commander = next(c for c in document["entries"] if c.get("is_commander"))
    commander.update(
        oracle_id=variant["uses"][0]["card"]["oracleId"],
        name=variant["uses"][0]["card"]["name"],
        color_identity=["U", "R"],
    )
    missing = variant["uses"][1]["card"]
    insert(
        path,
        "combo-piece",
        missing["name"],
        "Creature — Elemental",
        "{U}: Gain an activated ability.",
        oid=missing["oracleId"],
    )
    import_combo_snapshot(path, data)
    result = recall(prepared)
    candidate = next(c for c in result.candidates if c.card["id"] == "combo-piece")
    assert "combination" in candidate.channels
    evidence = next(e for e in candidate.evidence if e["channel"] == "combination")
    assert evidence["citation"]["url"].endswith(variant["id"] + "/")
    assert evidence["status"] == "sourced_ingredients_not_executed"


def test_associations_feed_discovery_with_cohort_and_window(prepared, monkeypatch):
    _, document = prepared
    document["entries"].append(
        {
            "id": "anchor",
            "oracle_id": "anchor",
            "name": "Anchor",
            "type_line": "Artifact",
            "quantity": 1,
        }
    )
    calls = []

    def associations(path, document, oracle_id, **kwargs):
        calls.append(oracle_id)
        return {
            "status": "available",
            "anchor_oracle_id": oracle_id,
            "cohort_lists": 100,
            "anchor_lists": 30,
            "window_start": "2026-07-18",
            "window_end_exclusive": "2026-10-17",
            "source": "fixture cohort",
            "associations": [
                {"oracle_id": "protect", "adjusted_lift": 1.5, "pair_lists": 10}
            ],
        }

    monkeypatch.setattr(
        "sabermetrics.assistant_associations.research_card_associations", associations
    )
    result = recall(prepared)
    candidate = next(c for c in result.candidates if c.card["id"] == "protect")
    evidence = next(e for e in candidate.evidence if e["channel"] == "association")
    assert evidence["cohort_lists"] == 100
    assert evidence["window_start"] == "2026-07-18"
    assert calls == ["anchor"]


def test_draw_channel_preserves_permanents_and_late_curve(prepared):
    path, _ = prepared
    for index in range(50):
        insert(
            path, f"cantrip-{index}", f"AAA {index}", "Sorcery", "Draw a card.", cmc=1
        )
    insert(
        path,
        "late-engine",
        "Zzz Engine",
        "Enchantment",
        "At the beginning of your upkeep, draw two cards.",
        cmc=7,
    )
    assert any(
        c.card["id"] == "late-engine" and "function:draw" in c.channels
        for c in recall(prepared, "draw").candidates
    )


@pytest.mark.parametrize(
    "mutation", [None, "low_similarity", "model", "facts", "dimensions", "nonfinite"]
)
def test_semantic_vectors_require_current_facts_and_exact_encoder(
    prepared, monkeypatch, mutation
):
    path, _ = prepared
    with db.connect(path) as conn:
        row = dict(conn.execute("SELECT * FROM cards WHERE id='draw'").fetchone())
        digest = hashlib.sha256(card_content(row).encode()).hexdigest()
        conn.execute(
            "CREATE TABLE assistant_card_vectors(oracle_id TEXT,content_hash TEXT,embedding_model TEXT,embedding_json TEXT)"
        )
        conn.execute(
            "INSERT INTO assistant_card_vectors VALUES(?,?,?,?)",
            (
                "draw",
                "stale" if mutation == "facts" else digest,
                "old" if mutation == "model" else "test-model",
                json.dumps(
                    [1.0]
                    if mutation == "dimensions"
                    else (
                        [float("nan"), 0]
                        if mutation == "nonfinite"
                        else [0.4, 0.9165] if mutation == "low_similarity" else [1.0, 0]
                    )
                ),
            ),
        )
        conn.commit()
    monkeypatch.setattr(
        "sabermetrics.reference_layer.strategy_corpus.local_embedding_service",
        lambda: SimpleNamespace(_model_name="test-model", embed=lambda text: [1.0, 0]),
    )
    result = recall(prepared, "draw")
    candidate = next(c for c in result.candidates if c.card["id"] == "draw")
    assert ("semantic" in candidate.channels) is (mutation in (None, "low_similarity"))
    if mutation not in (None, "low_similarity"):
        assert any("Rejected 1" in note for note in result.limitations)
