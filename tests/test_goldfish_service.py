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


def test_actual_engine_proportions_are_compared_as_percentage_points(tmp_path):
    from sabermetrics.goldfish_service import experiment_view
    from tests.test_goldfish_experiments import OWNER, SETTINGS, _document

    repo = GoldfishExperimentsRepo(tmp_path / "comparisons.db")
    document = _document()
    parent = repo.create(OWNER, document, SETTINGS)

    def result(n):
        return {
            "tests": {
                "opening": {
                    "status": "partial",
                    "metrics": {
                        "keep_without_mulligan": {
                            "value": n / 100,
                            "numerator": n,
                            "denominator": 100,
                            "ci_low": 0.1,
                            "ci_high": 0.9,
                        }
                    },
                }
            }
        }

    repo.finish(OWNER, parent["id"], result(40))
    child = repo.create(OWNER, document, SETTINGS, parent_id=parent["id"])
    repo.finish(OWNER, child["id"], result(55))
    view = experiment_view(repo.get(OWNER, child["id"]), document, repo)
    assert len(view["comparisons"]) == 2
    for comparison in view["comparisons"]:
        assert comparison["deltas"][0]["delta"] == 15.0
        assert comparison["deltas"][0]["before"] == 40.0
        assert comparison["deltas"][0]["after"] == 55.0
