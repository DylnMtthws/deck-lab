"""Native reports compare only measured metrics under identical settings."""

import copy
import json

import pytest

from sabermetrics import deck_simulation as native
from sabermetrics.native_diagnostics_comparison import compare_reports


def report(value=0.5):
    return {
        "schema_version": "decklab.goldfish-diagnostics.v1",
        "settings": {
            "games": 200,
            "seed": 17,
            "engine_version": "engine",
            "settings_fingerprint": "stable",
        },
        "tests": {
            "opening": {
                "status": "supported",
                "metrics": {"kept": {"value": value, "unit": "probability"}},
            }
        },
    }


def test_same_settings_deltas_do_not_mutate_reports():
    after, before = report(0.75), report(0.5)
    frozen = copy.deepcopy(after)
    compared = compare_reports(after, before)
    assert compared["comparable"]
    assert compared["metric_deltas"]["opening"]["kept"]["delta"] == 0.25
    assert after == frozen
    assert "not statistical proof" in compared["note"]


@pytest.mark.parametrize(
    "field", ["engine_version", "settings_fingerprint", "games", "seed"]
)
def test_settings_mismatch_suppresses_all_deltas(field):
    before = report()
    before["settings"][field] = "different"
    result = compare_reports(report(), before)
    assert result["comparable"] is False
    assert result["metric_deltas"] == {}


@pytest.mark.parametrize("change", ["null", "unit", "status"])
def test_unmeasured_or_changed_metrics_have_no_gain(change):
    before = report()
    if change == "null":
        before["tests"]["opening"]["metrics"]["kept"]["value"] = None
    elif change == "unit":
        before["tests"]["opening"]["metrics"]["kept"]["unit"] = "count"
    else:
        before["tests"]["opening"]["status"] = "partial"
    metric = compare_reports(report(), before)["metric_deltas"]["opening"]["kept"]
    assert metric["delta"] is None
    assert metric["comparable"] is False
    if change == "null":
        assert metric["before"] is None


def row(key, previous=None, owner="owner", deck="deck", diagnostics=True):
    outcome = native.SimulationResult(
        candidate_id="candidate",
        deck_sha256="ab" * 32,
        simulation_input_sha256="cd" * 32,
        simulator_version="test",
        games=200,
        objective_turn=3,
        metric="goldfish_turns_to_assembly",
        measures="assembly",
        does_not_measure="strength",
        assembly=(),
        censored_fraction=1.0,
        inert_card_count=0,
        raw_document={"diagnostics": report()} if diagnostics else {},
    )
    return {
        "id": key,
        "deck_id": deck,
        "owner_id": owner,
        "status": "done",
        "revision": int(key),
        "deck_sha256": "ab" * 32,
        "result_json": json.dumps(
            {
                "snapshot": {"id": deck},
                "comparison_id": previous,
                "outcome": outcome.model_dump(mode="json"),
            }
        ),
    }


def install_rows(monkeypatch, rows):
    class Repo:
        def __init__(self, path):
            pass

        def get(self, key):
            return rows.get(key)

    monkeypatch.setattr(native.db, "DeckDocumentSimulationsRepo", Repo)


def test_two_successive_histories_include_previous_and_baseline(monkeypatch, tmp_path):
    rows = {"1": row("1"), "2": row("2", "1"), "3": row("3", "2")}
    install_rows(monkeypatch, rows)
    comparison = native._diagnostics_comparison(tmp_path, rows["3"], report(0.75))
    assert [entry["id"] for entry in comparison["history"]] == ["2", "1"]
    assert comparison["metric_deltas"]["opening"]["kept"]["delta"] == 0.25
    assert comparison["history_truncated"] is False


@pytest.mark.parametrize("change", ["owner", "deck", "legacy", "source"])
def test_history_isolation_and_legacy_absence(monkeypatch, tmp_path, change):
    current, prior = row("2", "1"), row("1")
    if change in {"owner", "deck"}:
        prior[f"{change}_id"] = "other"
    elif change == "legacy":
        prior = row("1", diagnostics=False)
    else:
        stored = json.loads(current["result_json"])
        stored.update(
            comparison_id=None,
            source_baseline_id="1",
            source_baseline_outcome=json.loads(prior["result_json"])["outcome"],
        )
        current["result_json"] = json.dumps(stored)
    install_rows(monkeypatch, {"1": prior})
    compared = native._diagnostics_comparison(tmp_path, current, report())
    assert compared["comparable"] is False
    assert "No comparable diagnostics baseline" in compared["reason"]
    assert compared["metric_deltas"] == {}


def test_history_bounded_and_cycle_safe(monkeypatch, tmp_path):
    rows = {str(i): row(str(i), str(i - 1) if i > 1 else None) for i in range(1, 8)}
    install_rows(monkeypatch, rows)
    compared = native._diagnostics_comparison(tmp_path, rows["7"], report())
    assert len(compared["history"]) == 5
    assert compared["history_truncated"] is True


def test_schema_mismatch_has_no_deltas():
    before = report()
    before["schema_version"] = "old"
    assert compare_reports(report(), before)["metric_deltas"] == {}


def test_view_attaches_comparison_without_mutating_raw_document(monkeypatch, tmp_path):
    current = row("1")
    install_rows(monkeypatch, {})
    monkeypatch.setattr(
        native.db.DeckDocumentSimulationsRepo,
        "latest",
        lambda self, deck: current,
        raising=False,
    )
    monkeypatch.setattr(native, "_load_document", lambda path, deck: None)
    monkeypatch.setattr(native, "_baseline_result", lambda *args: None)
    outcome = native.parse_stored_result(current["result_json"])
    frozen = copy.deepcopy(outcome.raw_document)
    monkeypatch.setattr(native, "parse_stored_result", lambda raw: outcome)
    view = native.simulation_view(tmp_path, "deck")
    assert view["result"]["diagnostics"]["tests"] == frozen["diagnostics"]["tests"]
    assert view["result"]["diagnostics"]["comparison"]["comparable"] is False
    assert outcome.raw_document == frozen


def test_changed_coverage_suppresses_descriptive_gain():
    before, after = report(0.5), report(0.9)
    before["coverage"] = {"modeled_cards": 30, "unauthored_cards": 69}
    after["coverage"] = {"modeled_cards": 31, "unauthored_cards": 68}
    result = compare_reports(after, before)
    assert not result["comparable"]
    assert result["metric_deltas"] == {}
    assert "coverage changed" in result["reason"]


def audited_coverage(rows):
    return {
        "total_cards": 2,
        "modeled_cards": 2,
        "unauthored_cards": 0,
        "unauthored_names": [],
        "assumptions": ["same assumptions"],
        "card_coverage": {
            "version": 1,
            "unit": "card_slots",
            "counts": {"supported": 1, "partial": 1, "unsupported": 0},
            "cards": rows,
        },
    }


def test_basic_land_name_and_slot_changes_keep_comparison_descriptive():
    before, after = report(), report(0.6)
    before["coverage"] = audited_coverage(
        [
            {
                "name": "Island",
                "slot": 0,
                "status": "supported",
                "supported_effects": ["blue"],
            },
            {"name": "Island", "slot": 1, "status": "partial", "limitations": ["tap"]},
        ]
    )
    after["coverage"] = audited_coverage(
        [
            {
                "name": "Mountain",
                "slot": 9,
                "status": "supported",
                "supported_effects": ["blue"],
            },
            {
                "name": "Mountain",
                "slot": 8,
                "status": "partial",
                "limitations": ["tap"],
            },
        ]
    )
    frozen = copy.deepcopy((before, after))
    result = compare_reports(after, before)
    assert result["comparable"]
    assert result["metric_deltas"]["opening"]["kept"]["delta"] == pytest.approx(0.1)
    assert (before, after) == frozen


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cov: cov["card_coverage"].update(version=2),
        lambda cov: cov["card_coverage"]["counts"].update(supported=2),
        lambda cov: cov.update(total_cards=3),
        lambda cov: cov.update(unauthored_names=["Unknown"]),
        lambda cov: cov.update(assumptions=["different"]),
        lambda cov: cov["card_coverage"]["cards"][0].update(status="partial"),
        lambda cov: cov["card_coverage"]["cards"][0].update(supported_effects=["red"]),
        lambda cov: cov["card_coverage"]["cards"][0].update(limitations=["new"]),
        lambda cov: cov["card_coverage"]["cards"][0].update(future_field=True),
    ],
)
def test_meaningful_audit_changes_block_comparison(mutate):
    before, after = report(), report(0.9)
    rows = [
        {
            "name": "Island",
            "slot": 0,
            "status": "supported",
            "supported_effects": ["blue"],
        },
        {"name": "Island", "slot": 1, "status": "partial", "limitations": ["tap"]},
    ]
    before["coverage"] = audited_coverage(copy.deepcopy(rows))
    after["coverage"] = audited_coverage(copy.deepcopy(rows))
    mutate(after["coverage"])
    result = compare_reports(after, before)
    assert not result["comparable"]
    assert result["metric_deltas"] == {}


def test_card_audit_is_quantity_aware_multiset():
    before, after = report(), report()
    before["coverage"] = audited_coverage(
        [{"name": "Island", "slot": 0, "status": "supported"}]
    )
    after["coverage"] = audited_coverage(
        [
            {"name": "Mountain", "slot": 1, "status": "supported"},
            {"name": "Mountain", "slot": 2, "status": "supported"},
        ]
    )
    assert not compare_reports(after, before)["comparable"]
