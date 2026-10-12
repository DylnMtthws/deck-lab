"""Deterministic findings and conservative Build replacement proposals."""

import sqlite3

from sabermetrics.build_research_assistant import findings, recommendations


class Evidence:
    def __init__(self, path):
        self.db_path = path

    def alternatives(self, document, oracle_id, window_days, limit=8):
        if oracle_id == "target-ramp":
            return {
                "window_days": window_days,
                "denominator": 100,
                "alternatives": [
                    {"oracle_id": "candidate-ramp", "card_id": "candidate",
                     "name": "Recorded Ramp", "lists": 40, "rate": .4},
                    {"oracle_id": "expensive-ramp", "card_id": "expensive",
                     "name": "Expensive Ramp", "lists": 90, "rate": .9},
                ][:limit],
            }
        return {"denominator": 100, "alternatives": []}


def _price_db(tmp_path):
    path = tmp_path / "prices.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE card_prices(card_id TEXT, snapshot_date TEXT, price_usd REAL)")
        conn.executemany("INSERT INTO card_prices VALUES(?,?,?)", [
            ("target", "2026-10-01", 5.0),
            ("candidate", "2026-10-01", 3.0),
            ("expensive", "2026-10-01", 12.0),
        ])
    return path


def test_findings_keep_unknown_and_unmeasured_values_explicit():
    result = findings({"tests": {"opening": {
        "status": "partial", "metrics": {
            "keep_rate": {"value": None, "unit": "rate"},
            "kept": {"value": .65, "unit": "rate", "denominator": 20},
        }, "limitations": ["visible hand only"], "evidence": ["sample 1"],
    }}})
    assert result[0]["metrics"][0]["summary"] == "unmeasured"
    assert result[0]["metrics"][1]["summary"] == "0.65 rate (20 games)"
    assert result[0]["limitations"] == ["visible hand only"]


def test_recommendations_use_failure_role_and_price_ceiling(tmp_path):
    path = _price_db(tmp_path)
    document = {"entries": [
        {"id": "ramp-slot", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Current Ramp", "role": "ramp", "quantity": 11, "zone_id": "main"},
        {"id": "interaction-slot", "oracle_id": "target-removal", "card_id": "target",
         "name": "Removal", "role": "removal", "quantity": 1, "zone_id": "main"},
    ]}
    diagnostics = {"tests": {"development": {
        "status": "supported", "metrics": {
            "mean_missed_land_drops": {"value": .3, "unit": "lands/game"},
        },
    }}}
    result = recommendations(document, Evidence(path), diagnostics, limit=20)
    assert len(result) == 1
    assert result[0]["entry_id"] == "ramp-slot"
    assert result[0]["card_id"] == "candidate"
    assert result[0]["quantity"] == 1
    assert result[0]["price_usd"] == 3


def test_no_failure_signal_or_unknown_prices_means_no_swap(tmp_path):
    path = _price_db(tmp_path)
    document = {"entries": [
        {"id": "ramp-slot", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Current Ramp", "role": "ramp", "quantity": 1},
    ]}
    assert recommendations(document, Evidence(path), {"tests": {}}, limit=3) == []
