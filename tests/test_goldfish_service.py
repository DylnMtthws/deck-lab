import copy

import pytest

from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.goldfish_experiments import GoldfishExperimentsRepo
from sabermetrics.goldfish_service import (
    execute_experiment,
    run_settings,
    simulator_request,
    validate_result,
)
from tests.test_resimulate_delta import _client, _seed_deck


def test_actual_artifact_reproduces_results_and_preserves_unknowns(tmp_path):
    _, path, owner = _client(tmp_path)
    deck_id = _seed_deck(path, owner)
    document = DeckDocumentRepo(path).get(owner, deck_id)
    settings = run_settings({"plan_id": "vivi-affordable", "games": 20, "seed": 7})
    repo = GoldfishExperimentsRepo(path)
    results = []
    for _ in range(2):
        row = repo.create(owner, document, settings)
        execute_experiment(str(path), owner, row["id"])
        stored = repo.get(owner, row["id"])
        assert stored["status"] == "done", stored["error"]
        results.append(stored["result"])
    assert results[0] == results[1]
    assert results[0]["tests"]["finish"]["status"] == "unsupported"
    assert results[0]["tests"]["finish"]["metrics"]["deck_finish_probability"] is None
    assert set(results[0]["tests"]) == {
        "opening",
        "turns",
        "commander",
        "wipe",
        "finish",
    }


def test_request_never_sends_private_zones_to_engine(tmp_path):
    _, path, owner = _client(tmp_path)
    deck = _seed_deck(path, owner)
    document = DeckDocumentRepo(path).get(owner, deck)
    extra = copy.deepcopy(document["entries"][-1])
    extra.update(
        name="Private sideboard card", oracle_id="private", zone_name="Sideboard"
    )
    document["entries"].append(extra)
    settings = run_settings({"plan_id": "vivi-affordable", "games": 20, "seed": 1})
    payload = simulator_request(document, settings)
    assert not any(card["oracle_id"] == "private" for card in payload["deck"]["cards"])


@pytest.mark.parametrize(
    "status,missing", [("pass", []), ("partial", []), ("unknown", [])]
)
def test_malformed_coverage_cannot_become_success(status, missing):
    result = {
        "engine_version": "test",
        "coverage": {},
        "scenarios": [
            {"id": name, "status": status, "metrics": {}, "unknown_metrics": missing}
            for name in (
                "opening",
                "development",
                "commander_loss",
                "creature_wipe",
                "finish",
            )
        ],
    }
    with pytest.raises(ValueError):
        validate_result(result)
