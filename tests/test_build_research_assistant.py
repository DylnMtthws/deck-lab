"""Deterministic findings and conservative Build replacement proposals."""

import sqlite3

from sabermetrics import db
from sabermetrics.build_research_assistant import findings, recommendations
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui import builder_routes
from tests.test_resimulate_delta import ISLAND, _client, _seed_deck


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


class RouteEvidence(Evidence):
    def alternatives(self, document, oracle_id, window_days, limit=8):
        if oracle_id != "target-ramp":
            return {"denominator": 100, "alternatives": []}
        return {
            "window_days": window_days,
            "denominator": 100,
            "role": "ramp",
            "alternatives": [{
                "oracle_id": "candidate-ramp", "card_id": "candidate",
                "name": "Recorded Ramp", "lists": 40, "rate": .4,
            }],
        }


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


def _assistant_deck(tmp_path):
    client, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO cards (id,oracle_id,name,type_line,color_identity,is_legal_commander,is_legal_in_99) VALUES(?,?,?,?,?,?,?)",
            [
                ("target", "target-ramp", "Current Ramp", "Artifact", "[]", 0, 1),
                ("candidate", "candidate-ramp", "Recorded Ramp", "Artifact", "[]", 0, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO card_prices(card_id,snapshot_date,price_usd) VALUES(?,?,?)",
            [("target", "2026-10-01", 5.0), ("candidate", "2026-10-01", 3.0)],
        )
    repo = DeckDocumentRepo(path)
    document = repo.get(owner, deck_id)
    island = next(item for item in document["entries"] if item.get("oracle_id") == ISLAND)
    result = repo.apply_commands(
        owner, deck_id, expected_revision=document["revision"],
        mutation_id="assistant-test-seed",
        commands=[
            {"type": "set_quantity", "entry_id": island["id"], "quantity": 96},
            {"type": "add_card", "card_id": "target", "quantity": 3,
             "zone_id": island["zone_id"], "role": "ramp"},
        ],
    )
    return client, path, owner, deck_id, result["revision"]


def _current_report(_path, _deck_id):
    return {
        "status": "done", "stale": False,
        "result": {"diagnostics": {"tests": {"development": {
            "status": "supported", "metrics": {
                "mean_missed_land_drops": {
                    "value": 1.0, "unit": "lands/game", "denominator": 2000,
                },
            }, "limitations": [], "evidence": [],
        }}}},
    }


def _patch_assistant(monkeypatch, *, stale=False):
    monkeypatch.setattr(builder_routes, "simulation_view", lambda path, deck: (
        {**_current_report(path, deck), "stale": stale}
    ))
    monkeypatch.setattr(
        builder_routes, "DeckEvidenceService", lambda path: RouteEvidence(path)
    )


def test_findings_keep_unknown_and_unmeasured_values_explicit():
    result = findings({"tests": {"opening": {
        "status": "partial", "metrics": {
            "keep_rate": {"value": None, "unit": "rate"},
            "kept": {"value": .65, "unit": "rate", "denominator": 20},
        }, "limitations": ["visible hand only"], "evidence": ["sample 1"],
    }}})
    assert result[0]["metrics"][0]["summary"] == "unmeasured"
    assert result[0]["metrics"][1]["summary"] == "65.0% (20 games)"
    assert result[0]["limitations"] == ["visible hand only"]


def test_findings_hide_conditional_finish_and_label_engine_observation():
    result = findings({"tests": {"finish": {
        "status": "partial", "metrics": {
            "positive_finish_proof_rate": {
                "value": .25, "unit": "proportion", "denominator": 2000,
            },
            "verified_finish_rate": {
                "value": 1, "unit": "proportion", "denominator": 500,
            },
            "engine_online_rate": {
                "value": .5, "unit": "proportion", "denominator": 2000,
            },
        }, "limitations": [], "evidence": [],
    }}})
    metrics = result[0]["metrics"]
    assert [item["metric"] for item in metrics] == [
        "Finish positively proven across all trials (lower bound)",
        "Observed engine event",
    ]
    assert [item["summary"] for item in metrics] == ["25.0% (2000 games)", "50.0% (2000 games)"]


def test_search_hints_are_user_directed_exploration_not_recommendations():
    from sabermetrics.build_research_assistant import search_hints

    result = search_hints([
        {"id": "development", "status": "partial"},
        {"id": "finish", "status": "unsupported"},
    ])
    assert result == [{
        "test": "development", "label": "Explore mana sources", "query": "text:add {C}",
    }]


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
            "mean_missed_land_drops": {"value": .3, "unit": "lands/game", "denominator": 2000},
        },
    }}}
    result = recommendations(document, Evidence(path), diagnostics, limit=20)
    assert len(result) == 1
    assert result[0]["entry_id"] == "ramp-slot"
    assert result[0]["card_id"] == "candidate"
    assert result[0]["quantity"] == 1
    assert result[0]["price_usd"] == 3


def test_no_missed_land_observation_does_not_infer_failure_from_other_metrics(tmp_path):
    path = _price_db(tmp_path)
    document = {"entries": [
        {"id": "ramp-slot", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Current Ramp", "role": "ramp", "quantity": 1},
    ]}
    diagnostics = {"tests": {"development": {
        "status": "supported", "metrics": {
            "mean_first_authored_action_turn": {"value": 5, "unit": "turn", "denominator": 2000},
        },
    }}}
    assert recommendations(document, Evidence(path), diagnostics, limit=3) == []


def test_sideboard_is_excluded_and_outgoing_and_incoming_are_unique(tmp_path):
    path = _price_db(tmp_path)
    document = {"zones": [{"id": "private-zone", "name": "Sideboard"}], "entries": [
        {"id": "side", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Sideboard Ramp", "role": "ramp", "quantity": 1,
         "zone_id": "private-zone"},
        {"id": "main-a", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Main Ramp", "role": "ramp", "quantity": 2,
         "zone_name": "Main"},
        {"id": "main-b", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Duplicate Main Ramp", "role": "ramp", "quantity": 1,
         "zone_name": "Main"},
    ]}
    diagnostics = {"tests": {"development": {
        "status": "supported", "metrics": {
            "mean_missed_land_drops": {"value": 1, "unit": "lands/game", "denominator": 2000},
        },
    }}}
    result = recommendations(document, Evidence(path), diagnostics, limit=3)
    assert [item["entry_id"] for item in result] == ["main-a"]
    assert result[0]["quantity"] == 1


def test_unassigned_import_role_can_be_inferred_for_a_mainboard_basic(tmp_path):
    path = _price_db(tmp_path)
    document = {"entries": [
        {"id": "island", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Island", "type_line": "Basic Land — Island", "quantity": 11,
         "zone_name": "mainboard"},
    ]}
    diagnostics = {"tests": {"development": {
        "status": "supported", "metrics": {
            "mean_missed_land_drops": {"value": 1, "unit": "lands/game", "denominator": 2000},
        },
    }}}
    result = recommendations(document, Evidence(path), diagnostics, limit=3)
    assert len(result) == 1
    assert result[0]["from_role"] == "land"
    assert result[0]["quantity"] == 1


def test_unknown_prices_prevent_recommendation(tmp_path):
    path = _price_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM card_prices WHERE card_id='candidate'")
    document = {"entries": [
        {"id": "ramp-slot", "oracle_id": "target-ramp", "card_id": "target",
         "name": "Current Ramp", "role": "ramp", "quantity": 1},
    ]}
    diagnostics = {"tests": {"development": {
        "status": "supported", "metrics": {
            "mean_missed_land_drops": {"value": 1, "unit": "lands/game", "denominator": 2000},
        },
    }}}
    assert recommendations(document, Evidence(path), diagnostics, limit=3) == []


def test_assistant_routes_owner_revision_cap_duplicates_and_one_copy_apply(tmp_path, monkeypatch):
    client, path, owner, deck_id, revision = _assistant_deck(tmp_path)
    _patch_assistant(monkeypatch)
    response = client.get(f"/api/decks/{deck_id}/research-assistant")
    assert response.status_code == 200
    assert response.json["diagnostics_state"] == "current"
    assert response.json["recommendations"][0]["entry_id"]
    entry_id = response.json["recommendations"][0]["entry_id"]
    choice = {"entry_id": entry_id, "card_id": "candidate"}

    too_many = client.post(f"/api/decks/{deck_id}/research-assistant/apply", json={
        "expected_revision": revision, "replacements": [choice] * 4,
    })
    assert too_many.status_code == 400
    duplicate = client.post(f"/api/decks/{deck_id}/research-assistant/apply", json={
        "expected_revision": revision, "replacements": [choice, choice],
    })
    assert duplicate.status_code == 400
    stale_revision = client.post(f"/api/decks/{deck_id}/research-assistant/apply", json={
        "expected_revision": revision - 1, "replacements": [choice],
    })
    assert stale_revision.status_code == 409

    applied = client.post(f"/api/decks/{deck_id}/research-assistant/apply", json={
        "expected_revision": revision, "replacements": [choice],
    })
    assert applied.status_code == 200
    result = applied.json["document"]
    assert result["validation"]["total_count"] == 100
    target = next(item for item in result["entries"] if item.get("oracle_id") == "target-ramp")
    candidate = next(item for item in result["entries"] if item.get("oracle_id") == "candidate-ramp")
    assert target["quantity"] == 2
    assert candidate["quantity"] == 1

    other = db.UsersRepo(path).create(
        email="other-assistant@example.test", display_name="Other", role="user", status="active"
    )
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.get(f"/api/decks/{deck_id}/research-assistant").status_code == 404


def test_apply_refuses_when_native_report_is_stale(tmp_path, monkeypatch):
    client, _path, _owner, deck_id, revision = _assistant_deck(tmp_path)
    _patch_assistant(monkeypatch, stale=True)
    response = client.post(f"/api/decks/{deck_id}/research-assistant/apply", json={
        "expected_revision": revision,
        "replacements": [{"entry_id": "any", "card_id": "candidate"}],
    })
    assert response.status_code == 409
    assert response.json["error"] == "stale_recommendations"


def test_apply_refuses_lineage_limit_before_mutating_deck(tmp_path, monkeypatch):
    client, path, owner, deck_id, revision = _assistant_deck(tmp_path)
    _patch_assistant(monkeypatch)

    def reject_lineage(_path, _document):
        raise ValueError("This deck already has three replacements from its frozen baseline.")

    monkeypatch.setattr(builder_routes, "prepare_simulation", reject_lineage)
    entry_id = client.get(f"/api/decks/{deck_id}/research-assistant").json["recommendations"][0]["entry_id"]
    response = client.post(f"/api/decks/{deck_id}/research-assistant/apply", json={
        "expected_revision": revision,
        "replacements": [{"entry_id": entry_id, "card_id": "candidate"}],
    })
    assert response.status_code == 409
    assert response.json["error"] == "replacement_limit"
    assert DeckDocumentRepo(path).get(owner, deck_id)["revision"] == revision


def test_recommendations_clamp_to_three_and_deduplicate_incoming(tmp_path):
    path = tmp_path / "prices.db"
    outgoing = [f"out-{index}" for index in range(5)]
    incoming = [f"in-{index}" for index in range(5)]
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE card_prices(card_id TEXT, snapshot_date TEXT, price_usd REAL)")
        conn.executemany(
            "INSERT INTO card_prices VALUES(?,?,?)",
            [(item, "2026-10-01", 3.0) for item in outgoing]
            + [(item, "2026-10-01", 1.0) for item in incoming]
            + [("in-shared", "2026-10-01", 1.0)],
        )

    class ManyEvidence(Evidence):
        def alternatives(self, document, oracle_id, window_days, limit=8):
            index = int(oracle_id.split("-")[-1])
            selected = "in-shared" if index == 1 else incoming[index]
            alternatives = [{
                "card_id": selected, "oracle_id": "oracle-" + selected,
                "name": selected, "lists": 10, "rate": .1,
            }]
            if index == 2:
                alternatives.insert(0, {
                    "card_id": "in-shared", "oracle_id": "oracle-in-shared",
                    "name": "in-shared", "lists": 50, "rate": .5,
                })
            return {"window_days": window_days, "denominator": 100,
                    "role": "land", "alternatives": alternatives[:limit]}

    document = {"entries": [
        {"id": f"slot-{index}", "oracle_id": f"oracle-{index}",
         "card_id": f"out-{index}", "name": f"Land {index}",
         "type_line": "Land", "role": "land", "quantity": 1,
         "zone_name": "Main"}
        for index in range(5)
    ]}
    diagnostics = {"tests": {"development": {
        "status": "supported", "metrics": {
            "mean_missed_land_drops": {"value": .5, "unit": "lands/game", "denominator": 100},
        },
    }}}
    result = recommendations(document, ManyEvidence(path), diagnostics, limit=99)
    assert len(result) == 3
    assert len({item["entry_id"] for item in result}) == 3
    assert len({item["oracle_id"] for item in result}) == 3
    assert [item["entry_id"] for item in result] == ["slot-0", "slot-1", "slot-2"]
