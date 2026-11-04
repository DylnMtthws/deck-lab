import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from sabermetrics.combo_catalog import (
    MAX_OUTPUT_BYTES,
    import_combo_snapshot,
    known_combinations,
    parse_snapshot_json,
)


def snapshot():
    return json.loads(
        (
            Path(__file__).parent / "fixtures" / "combo_catalog_reference.json"
        ).read_text()
    )


def document(row, *, commander=False):
    return {
        "entries": [
            {
                "name": u["card"]["name"],
                "oracle_id": u["card"]["oracleId"],
                "quantity": u["quantity"],
                "is_commander": commander and u["mustBeCommander"],
            }
            for u in row["uses"]
        ]
    }


def test_real_source_identity_match_preserves_unverified_claims_and_states(tmp_path):
    data = snapshot()
    path = tmp_path / "cards.db"
    receipt = import_combo_snapshot(path, data)
    assert receipt["imported_variants"] == 3
    assert receipt["skipped_variants"] == 0
    doc = document(data["results"][0])
    original = deepcopy(doc)
    result = known_combinations(path, doc)
    assert doc == original
    assert result["status"] == "matched"
    row = next(
        r for r in result["matches"] if r["variant_id"] == data["results"][0]["id"]
    )
    assert row["execution_verified"] is False
    assert row["status"] == "sourced_claim_not_executed"
    assert row["citation"]["snapshot_sha256"] == receipt["snapshot_sha256"]
    assert (
        row["required_zones"][0]["zones"]
        == data["results"][0]["uses"][0]["zoneLocations"]
    )
    assert "state_requirements" in row["required_zones"][0]


def test_quantity_near_complete_and_commander_gate(tmp_path):
    data = snapshot()
    data["results"] = data["results"][:1]
    row = data["results"][0]
    row["uses"][0]["quantity"] = 3
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    doc = document(row)
    doc["entries"][0]["quantity"] = 2
    match = known_combinations(path, doc)["matches"][0]
    assert match["ingredient_match"] == "missing_card"
    assert match["missing_cards"] == {row["uses"][0]["card"]["name"]: 1}
    doc["entries"][0]["quantity"] = 1
    assert known_combinations(path, doc)["status"] == "no_match"
    data["retrieved_at"] = "2026-10-15T00:00:00+00:00"
    row["uses"][0]["mustBeCommander"] = True
    import_combo_snapshot(path, data)
    doc = document(row)
    assert known_combinations(path, doc)["status"] == "no_match"
    doc["entries"][0]["is_commander"] = True
    assert known_combinations(path, doc)["status"] == "matched"


def test_template_never_resolved_by_named_card_presence(tmp_path):
    data = snapshot()
    data["results"] = data["results"][-1:]
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    result = known_combinations(path, document(data["results"][0]))["matches"][0]
    assert result["ingredient_match"] == "unresolved_template"
    assert result["unresolved_template_count"] == 1
    assert result["unresolved_templates"][0]["name"] == "Permanent Castable for {C}"
    assert result["execution_verified"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(snapshot_version="unknown"),
        lambda d: d.update(retrieved_at="2026-10-14"),
        lambda d: d.update(retrieved_at="bad"),
        lambda d: d["results"].append(deepcopy(d["results"][0])),
        lambda d: d["results"][0]["uses"].__setitem__(0, "wrong"),
        lambda d: d["results"][0]["uses"][0]["card"].update(oracleId="-" * 36),
        lambda d: d["results"][0]["uses"][0].update(quantity=True),
        lambda d: d["results"][0]["uses"][0].update(mustBeCommander="false"),
        lambda d: d["results"][0]["uses"][0].update(zoneLocations="B"),
        lambda d: d["results"][0]["uses"][0].update(zoneLocations=["INVALID"]),
        lambda d: d["results"][0]["uses"][0].update(battlefieldCardState=[]),
        lambda d: d["results"][0].update(description=None),
        lambda d: d["results"][0].update(requires=[{}]),
        lambda d: d["results"][0].update(produces=[{}]),
        lambda d: d["results"][0].update(spoiler="false"),
        lambda d: d["results"][0].update(manaNeeded=float("nan")),
    ],
)
def test_malformed_snapshot_rejected_before_database_change(tmp_path, mutation):
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, snapshot())
    before = path.read_bytes()
    data = snapshot()
    mutation(data)
    with pytest.raises(ValueError):
        import_combo_snapshot(path, data)
    assert path.read_bytes() == before


def test_date_order_idempotence_and_conflict_roll_back_atomically(tmp_path):
    path = tmp_path / "cards.db"
    data = snapshot()
    import_combo_snapshot(path, data)
    same = import_combo_snapshot(path, data)
    assert same["imported_variants"] == 0 and same["skipped_same"] == 3
    older = deepcopy(data)
    older["retrieved_at"] = "2026-10-13T23:00:00-04:00"
    receipt = import_combo_snapshot(path, older)
    assert receipt["imported_variants"] == 0 and receipt["skipped_older"] == 3
    conflict = deepcopy(data)
    conflict["results"][-1]["description"] = "changed"
    with sqlite3.connect(path) as con:
        before = con.execute(
            "SELECT count(*) FROM assistant_combo_snapshots"
        ).fetchone()
    with pytest.raises(ValueError, match="same retrieval time"):
        import_combo_snapshot(path, conflict)
    with sqlite3.connect(path) as con:
        assert (
            con.execute("SELECT count(*) FROM assistant_combo_snapshots").fetchone()
            == before
        )


def test_new_ineligible_source_tombstones_old_claim(tmp_path):
    path = tmp_path / "cards.db"
    data = snapshot()
    data["results"] = data["results"][:1]
    doc = document(data["results"][0])
    import_combo_snapshot(path, data)
    data["retrieved_at"] = "2026-10-15T00:00:00Z"
    data["results"][0]["spoiler"] = True
    receipt = import_combo_snapshot(path, data)
    assert receipt["imported_variants"] == 0 and receipt["skipped_ineligible"] == 1
    assert known_combinations(path, doc)["status"] == "no_match"


def test_read_only_missing_database_never_created(tmp_path):
    path = tmp_path / "absent.db"
    assert known_combinations(path, {"entries": []})["status"] == "unavailable"
    assert not path.exists()


def test_sideboard_identity_does_not_match_and_names_do_not_substitute(tmp_path):
    path = tmp_path / "cards.db"
    data = snapshot()
    data["results"] = data["results"][:1]
    import_combo_snapshot(path, data)
    doc = document(data["results"][0])
    for entry in doc["entries"]:
        entry["zone_name"] = "Sideboard"
    assert known_combinations(path, doc)["status"] == "no_match"
    for entry in doc["entries"]:
        entry["zone_name"] = "Main"
        entry["oracle_id"] = "00000000-0000-0000-0000-000000000001"
    assert known_combinations(path, doc)["status"] == "no_match"


def test_large_excerpts_and_four_match_byte_budget(tmp_path):
    data = snapshot()
    base = data["results"][0]
    data["results"] = []
    for i in range(6):
        row = deepcopy(base)
        row["id"] = str(i + 1)
        row["description"] = "Step " * 400
        row["easyPrerequisites"] = "Need " * 200
        row["uses"][0]["battlefieldCardState"] = "State " * 100
        data["results"].append(row)
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    result = known_combinations(path, document(base), limit=20)
    assert len(result["matches"]) == 4
    assert result["omitted_match_count"] == 2
    assert (
        len(json.dumps(result, separators=(",", ":"), sort_keys=True).encode())
        <= MAX_OUTPUT_BYTES
    )
    assert all(
        r["steps_truncated"]
        and r["prerequisites_truncated"]
        and r["required_zones"][0]["state_requirements_truncated"]
        for r in result["matches"]
    )


def test_duplicate_json_keys_and_nonfinite_values_do_not_silently_parse():
    for raw in (
        '{"results":[],"results":[1]}',
        '{"number":NaN}',
        '{"number":Infinity}',
    ):
        with pytest.raises(ValueError):
            parse_snapshot_json(raw)


def test_corrupt_stored_payload_raises_instead_of_false_absence(tmp_path):
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, snapshot())
    with sqlite3.connect(path) as con:
        con.execute("UPDATE assistant_combo_variants SET payload='{}'")
    with pytest.raises(ValueError, match="Invalid stored"):
        known_combinations(path, {"entries": []})


def test_three_card_combination_requires_all_canonical_ingredients(tmp_path):
    data = snapshot()
    row = data["results"][0]
    third = deepcopy(data["results"][-1]["uses"][0])
    row["uses"].append(third)
    data["results"] = [row]
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    doc = document(row)
    before = path.read_bytes()
    result = known_combinations(path, doc)
    assert len(result["matches"][0]["cards"]) == 3
    assert result["matches"][0]["ingredient_match"] == "all_named_cards_present"
    assert path.read_bytes() == before
    doc["entries"].pop()
    assert (
        known_combinations(path, doc)["matches"][0]["ingredient_match"]
        == "missing_card"
    )
    doc["entries"].pop()
    assert known_combinations(path, doc)["status"] == "no_match"


def test_equivalent_timezone_same_time_cannot_override(tmp_path):
    data = snapshot()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    data["retrieved_at"] = "2026-10-14T08:00:00-04:00"
    result = import_combo_snapshot(path, data)
    assert result["skipped_same"] == 3
    assert result["imported_variants"] == 0


def test_storage_bounds_and_uuid_nil_rejected_without_creating_db(tmp_path):
    path = tmp_path / "cards.db"
    data = snapshot()
    data["results"][0]["uses"][0]["card"][
        "oracleId"
    ] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError):
        import_combo_snapshot(path, data)
    assert not path.exists()
    data = snapshot()
    data["results"][0]["description"] = "x" * 50001
    with pytest.raises(ValueError):
        import_combo_snapshot(path, data)
    assert not path.exists()


def test_cli_raw_api_response_requires_explicit_retrieval_provenance(
    tmp_path, monkeypatch, capsys
):
    import runpy
    import sys

    source = tmp_path / "raw.json"
    db = tmp_path / "cards.db"
    data = snapshot()
    data.pop("snapshot_version")
    data.pop("retrieved_at")
    source.write_text(json.dumps(data))
    script = Path(__file__).parent.parent / "scripts" / "import_combo_catalog.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(source), "--db-path", str(db)])
    with pytest.raises(SystemExit):
        runpy.run_path(str(script), run_name="__main__")
    assert not db.exists()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            str(source),
            "--db-path",
            str(db),
            "--retrieved-at",
            "2026-10-14T12:00:00Z",
        ],
    )
    runpy.run_path(str(script), run_name="__main__")
    assert json.loads(capsys.readouterr().out)["imported_variants"] == 3


def constraint_references():
    directory = Path(__file__).parent / "fixtures"
    return json.loads(
        (directory / "combo_catalog_constraints_reference.json").read_text()
    ), json.loads((directory / "combo_catalog_decks_reference.json").read_text())


def test_actual_vivi_keeps_quicksilver_and_excludes_wrong_identity_near_claims(
    tmp_path,
):
    data, decks = constraint_references()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    result = known_combinations(path, decks["affordable-vivi"])
    assert result["matches"][0]["variant_id"] == "1369-6586"
    assert result["matches"][0]["ingredient_match"] == "all_named_cards_present"
    assert result["matches"][0]["constraint_status"] == "eligible_source_ingredients"
    assert all(
        r["variant_id"] not in {"4499-6586", "4116-6586", "513-5034--46"}
        for r in result["matches"]
    )
    assert result["matches"][1]["variant_id"] == "1409-6586"
    assert result["matches"][1]["missing_cards_are_recommendations"] is False


@pytest.mark.parametrize("deck_name", ["high-power-vivi", "high-power-sauron"])
def test_actual_high_power_decks_match_three_card_claims(tmp_path, deck_name):
    data, decks = constraint_references()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    result = known_combinations(path, decks[deck_name])
    assert any(
        len(r["cards"]) == 3 and r["ingredient_match"] == "all_named_cards_present"
        for r in result["matches"]
    )
    assert all(
        r["constraint_status"] == "eligible_source_ingredients"
        for r in result["matches"]
    )


def test_affordable_sauron_does_not_get_sol_ring_only_near_combo(tmp_path):
    data, decks = constraint_references()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    result = known_combinations(path, decks["affordable-sauron"])
    assert result["status"] == "no_match"
    assert result["absence_is_not_proof_of_no_combinations"] is True


def test_legality_false_excluded_unknown_constraints_are_not_eligible(tmp_path):
    data, decks = constraint_references()
    data["results"] = data["results"][:1]
    data["results"][0]["legalities"]["commander"] = False
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    assert known_combinations(path, decks["affordable-vivi"])["status"] == "no_match"
    data["retrieved_at"] = "2026-10-15T00:00:00Z"
    data["results"][0].pop("identity")
    data["results"][0].pop("legalities")
    import_combo_snapshot(path, data)
    row = known_combinations(path, decks["affordable-vivi"])["matches"][0]
    assert row["constraint_status"] == "sourced_constraint_unknown"
    from sabermetrics.assistant_deck_intelligence import _eligible_catalog_lines

    assert _eligible_catalog_lines(path, decks["affordable-vivi"]) == []


def test_external_preservation_uses_canonical_identity_and_quantity(tmp_path):
    from sabermetrics.assistant_deck_intelligence import (
        _catalog_line_present,
        _eligible_catalog_lines,
    )

    data, decks = constraint_references()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    doc = decks["affordable-vivi"]
    line = _eligible_catalog_lines(path, doc)[0]
    assert {c["name"] for c in line["cards"]} == {
        "Vivi Ornitier",
        "Quicksilver Elemental",
    }
    assert _catalog_line_present(line, doc)
    for entry in doc["entries"]:
        if entry["name"] == "Quicksilver Elemental":
            entry["name"] = "Localized alias"
    assert _catalog_line_present(line, doc)
    for entry in doc["entries"]:
        if entry["name"] == "Localized alias":
            entry["quantity"] = 0
    assert not _catalog_line_present(line, doc)


@pytest.mark.parametrize(
    "field,value",
    [("identity", "URR"), ("identity", "BAG"), ("legalities", {"commander": "true"})],
)
def test_malformed_identity_and_legality_are_rejected(tmp_path, field, value):
    data = snapshot()
    data["results"][0][field] = value
    with pytest.raises(ValueError):
        import_combo_snapshot(tmp_path / "cards.db", data)


def test_model_receipt_keeps_catalog_matches_at_maximum_real_excerpts(tmp_path):
    from sabermetrics.assistant_runtime import _model_receipt

    data, decks = constraint_references()
    for row in data["results"]:
        row["description"] = "source step " * 300
        row["easyPrerequisites"] = "source prerequisite " * 100
        for use in row["uses"]:
            for key in (
                "battlefieldCardState",
                "exileCardState",
                "graveyardCardState",
                "libraryCardState",
            ):
                use[key] = "source state " * 40
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    for doc in decks.values():
        result = known_combinations(path, doc)
        receipt = _model_receipt({"name": "known_deck_combinations", "data": result})
        assert "matches" in receipt["data"]
        assert len(json.dumps(receipt, ensure_ascii=False)) <= 12000
        assert [m["variant_id"] for m in receipt["data"]["matches"]] == [
            m["variant_id"] for m in result["matches"]
        ]


def test_same_snapshot_digest_reprojects_legacy_normalized_metadata_safely(tmp_path):
    data = snapshot()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    with sqlite3.connect(path) as con:
        for identifier, raw in con.execute(
            "SELECT id,payload FROM assistant_combo_variants"
        ).fetchall():
            row = json.loads(raw)
            row.pop("identity")
            row.pop("legalities")
            con.execute(
                "UPDATE assistant_combo_variants SET payload=? WHERE id=?",
                (json.dumps(row, sort_keys=True, separators=(",", ":")), identifier),
            )
    receipt = import_combo_snapshot(path, data)
    assert receipt["imported_variants"] == 3
    with sqlite3.connect(path) as con:
        assert all(
            "identity" in json.loads(r[0])
            for r in con.execute("SELECT payload FROM assistant_combo_variants")
        )


def test_real_quicksilver_receives_preservation_ranking_penalty(tmp_path):
    from sabermetrics.assistant_deck_intelligence import (
        _catalog_preservation_penalty,
        _eligible_catalog_lines,
    )

    data, decks = constraint_references()
    path = tmp_path / "cards.db"
    import_combo_snapshot(path, data)
    doc = decks["affordable-vivi"]
    lines = _eligible_catalog_lines(path, doc)
    quicksilver = next(
        c for c in doc["entries"] if c["name"] == "Quicksilver Elemental"
    )
    sol_ring = next(c for c in doc["entries"] if c["name"] == "Sol Ring")
    assert _catalog_preservation_penalty(quicksilver, lines) == 1
    assert _catalog_preservation_penalty(sol_ring, lines) == 0
    assert (
        min(
            [quicksilver, sol_ring],
            key=lambda c: _catalog_preservation_penalty(c, lines),
        )["name"]
        == "Sol Ring"
    )


def test_real_colorless_spellbook_row_imports_in_mixed_snapshot(tmp_path):
    colorless = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "combo_catalog_colorless_reference.json"
        ).read_text()
    )
    mixed = snapshot()
    mixed["results"].extend(colorless["results"])
    path = tmp_path / "cards.db"
    receipt = import_combo_snapshot(path, mixed)
    assert receipt["imported_variants"] == 4
    assert receipt["skipped_variants"] == 0
    assert receipt["validated_variants"] == 4
    source = colorless["results"][0]
    doc = document(source)
    # A synthetic commander isolates the known empty color-identity contract;
    # this test does not assert a complete deck's legality or executable combo.
    doc["entries"].append(
        {
            "name": "Synthetic colorless commander",
            "oracle_id": "00000000-0000-0000-0000-000000000001",
            "is_commander": True,
            "quantity": 1,
            "color_identity": "[]",
        }
    )
    match = next(
        m
        for m in known_combinations(path, doc)["matches"]
        if m["variant_id"] == source["id"]
    )
    assert match["source_color_identity"] == ""
    assert match["constraint_status"] == "eligible_source_ingredients"
    assert match["ingredient_match"] == "all_named_cards_present"
    assert match["execution_verified"] is False
    doc["entries"][-1]["color_identity"] = '["U", "R"]'
    assert (
        next(
            m
            for m in known_combinations(path, doc)["matches"]
            if m["variant_id"] == source["id"]
        )["constraint_status"]
        == "eligible_source_ingredients"
    )
    doc["entries"][-1].pop("color_identity")
    assert (
        next(
            m
            for m in known_combinations(path, doc)["matches"]
            if m["variant_id"] == source["id"]
        )["constraint_status"]
        == "sourced_constraint_unknown"
    )


def test_colorless_symbol_is_not_a_colored_identity_member(tmp_path):
    data = snapshot()
    data["results"][0]["identity"] = "CU"
    with pytest.raises(ValueError, match="color identity"):
        import_combo_snapshot(tmp_path / "cards.db", data)
