from copy import deepcopy

import pytest

from sabermetrics.assistant_claim_fragments import VERSION, fragment_units
from sabermetrics.assistant_workspace import (
    apply_workspace_event,
    catalog_card_record,
    create_workspace,
    reviewed_card_records,
    workspace_event,
)


def card(text="{2}: Draw a card."):
    return {
        "id": "engine",
        "name": "Engine",
        "oracle_id": "oracle",
        "mana_cost": "{U}",
        "oracle_text": text,
        "type_line": "Creature",
    }


def test_stale_generation_and_changed_source_do_not_inherit_claims():
    original = create_workspace("Draw creatures", 2, 1)
    record = catalog_card_record(card())
    assert record is not None
    first = apply_workspace_event(original, workspace_event([record]))
    first["cards"][0].update(
        status="reviewed",
        claims=[{"claim_id": "x", "text": "Old fit", "status": "supported"}],
        pinned=True,
    )
    next_request = create_workspace("Cheaper draw creatures", 2, 2, first)
    stale = workspace_event([record])
    stale["data"]["generation"] = 1
    assert apply_workspace_event(next_request, stale) == next_request
    changed = catalog_card_record(card("{3}: Draw two cards."))
    assert changed is not None
    current = apply_workspace_event(next_request, workspace_event([changed]))
    result = current["cards"][0]
    assert result["pinned"] and result["version"] == 2
    assert (
        result["claims"] == []
        and result["history"][0]["claims"][0]["status"] == "superseded"
    )
    assert original["cards"] == []  # immutable snapshots


def test_repeated_facts_do_not_erase_review_or_move_cards():
    workspace = create_workspace("Draw", 0, 1)
    a = catalog_card_record(card())
    b = catalog_card_record({**card(), "id": "other", "name": "Other"})
    assert a and b
    workspace = apply_workspace_event(workspace, workspace_event([a, b]))
    workspace["cards"][0].update(
        status="reviewed", claims=[{"text": "Known fact", "status": "supported"}]
    )
    prior = deepcopy(workspace)
    assert apply_workspace_event(workspace, workspace_event([a])) == prior
    workspace["steering"]["excluded_card_ids"] = ["other"]
    b["status"] = "reviewed"
    current = apply_workspace_event(workspace, workspace_event([b]))
    assert current["cards"][1]["status"] == "retrieved"


def test_incomplete_or_tampered_printed_records_abstain():
    assert catalog_card_record({**card(), "oracle_text_truncated": True}) is None
    record = catalog_card_record(card())
    assert record
    record["printed"]["oracle_text"] = "Wins immediately."
    workspace = create_workspace("Draw", 0, 1)
    assert apply_workspace_event(workspace, workspace_event([record]))["cards"] == []


def test_reviewed_claim_is_bound_to_exact_original_and_current_text():
    parts = ["Candidates", "Draw: Engine ({U}) — Pay {2} to draw a card."]
    units = fragment_units(parts, set())
    judgment = {
        **units[0],
        "verdict": "supported",
        "rendered_claim": units[0]["claim"],
        "citations": [],
    }
    receipt = {
        "claim_unit_version": VERSION,
        "claim_judgments": [judgment],
        "workspace_card_sources": {
            "engine": catalog_card_record(card())["source_sha256"]
        },
    }
    assert (
        reviewed_card_records(parts, parts, receipt, [card()])[0]["claims"][0]["status"]
        == "supported"
    )
    altered = [parts[0], parts[1] + " This is the best engine."]
    assert reviewed_card_records(altered, altered, receipt, [card()]) == []
    assert (
        reviewed_card_records(
            parts,
            ["Candidates", "Draw: Engine ({U}) — Different text."],
            receipt,
            [card()],
        )[0]["claims"][0]["status"]
        == "withdrawn"
    )


def test_full_previous_generation_leaves_capacity_for_new_cards():
    workspace = create_workspace("Old", 0, 1)
    records = [
        catalog_card_record({**card(), "id": str(i), "name": f"Engine {i}"})
        for i in range(24)
    ]
    workspace = apply_workspace_event(workspace, workspace_event(records))
    workspace["cards"][0]["pinned"] = True
    workspace = create_workspace("New", 0, 2, workspace)
    new = catalog_card_record({**card(), "id": "new", "name": "New engine"})
    workspace = apply_workspace_event(workspace, workspace_event([new]))
    assert len(workspace["cards"]) == 25
    assert workspace["cards"][-1]["card_id"] == "new"
    next_generation = create_workspace("Again", 0, 3, workspace)
    assert len(next_generation["cards"]) == 24
    assert next_generation["archived_omitted"] == 1
    assert any(c["card_id"] == "0" and c["pinned"] for c in next_generation["cards"])
    assert any(c["card_id"] == "new" for c in next_generation["cards"])


def test_review_cannot_attach_to_new_printed_snapshot_with_same_name():
    parts = ["Candidates", "Draw: Engine ({U}) — Pay {2} to draw a card."]
    unit = fragment_units(parts, set())[0]
    receipt = {
        "claim_unit_version": VERSION,
        "workspace_card_sources": {
            "engine": catalog_card_record(card())["source_sha256"]
        },
        "claim_judgments": [
            {
                **unit,
                "verdict": "supported",
                "rendered_claim": unit["claim"],
                "citations": [],
            }
        ],
    }
    assert (
        reviewed_card_records(parts, parts, receipt, [card("{5}: Draw a card.")]) == []
    )


def test_partial_jobs_merge_only_for_same_generation_source_and_parent():
    record = catalog_card_record(card())
    assert record
    workspace = create_workspace("Draw", 0, 1)
    record.update(
        status="reviewed",
        claims_complete=False,
        claims=[
            {
                "claim_id": "a",
                "text": "Cost",
                "status": "supported",
                "part_sha256": "parent",
            }
        ],
    )
    workspace = apply_workspace_event(workspace, workspace_event([record]))
    second = {
        **record,
        "claims": [
            {
                "claim_id": "b",
                "text": "Effect",
                "status": "supported",
                "part_sha256": "parent",
            }
        ],
    }
    workspace = apply_workspace_event(workspace, workspace_event([second]))
    assert {c["claim_id"] for c in workspace["cards"][0]["claims"]} == {"a", "b"}
    new = create_workspace("Changed constraint", 0, 2, workspace)
    new = apply_workspace_event(new, workspace_event([second]))
    assert {c["claim_id"] for c in new["cards"][0]["claims"]} == {"b"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("color_identity", ["B"]),
        ("loyalty", "5"),
        ("defense", "7"),
        ("name", "Engine renamed"),
        ("id", "new-printing"),
        ("oracle_id", "new-oracle"),
        ("provenance", {"source": "updated catalog"}),
        ("mana_cost", "{B}"),
        ("mana_value", 2),
        ("cmc", 2),
        ("type_line", "Artifact"),
        ("oracle_text", "{3}: Draw a card."),
        ("layout", "transform"),
        ("power", "3"),
        ("toughness", "4"),
        *[
            ("card_faces", [{"name": "Back", key: value}])
            for key, value in [
                ("name", "New back"),
                ("mana_cost", "{R}"),
                ("type_line", "Battle"),
                ("oracle_text", "Draw two cards."),
                ("power", "2"),
                ("toughness", "3"),
                ("loyalty", "4"),
                ("defense", "5"),
                ("colors", ["R"]),
            ]
        ],
    ],
)
def test_every_reviewed_printed_field_change_invalidates_exact_manifest(field, value):
    original = {
        **card(),
        "color_identity": ["U"],
        "loyalty": "1",
        "defense": "2",
        "card_faces": [{"name": "Back", "oracle_text": "Draw a card."}],
    }
    record = catalog_card_record(original)
    parts = ["Candidates", "Draw: Engine ({U}) — Pay {2} to draw a card."]
    unit = fragment_units(parts, set())[0]
    receipt = {
        "claim_unit_version": VERSION,
        "workspace_card_sources": {"engine": record["source_sha256"]},
        "claim_judgments": [
            {
                **unit,
                "verdict": "supported",
                "rendered_claim": unit["claim"],
                "citations": [],
            }
        ],
    }
    assert reviewed_card_records(parts, parts, receipt, [original])
    if field == "card_faces":
        value = [{**original["card_faces"][0], **value[0]}]
    changed = {**original, field: value}
    changed_record = catalog_card_record(changed)
    assert changed_record["source_sha256"] != record["source_sha256"]
    assert reviewed_card_records(parts, parts, receipt, [changed]) == []
    workspace = apply_workspace_event(
        create_workspace("Draw", 0, 1), workspace_event([record])
    )
    workspace["cards"][0].update(status="reviewed", claims=[{"status": "supported"}])
    updated = apply_workspace_event(workspace, workspace_event([changed_record]))
    current = next(
        c for c in updated["cards"] if c["card_id"] == changed_record["card_id"]
    )
    assert current["status"] == "retrieved" and current["claims"] == []
    if field != "id":
        assert current["history"][-1]["claims"] == [{"status": "supported"}]
