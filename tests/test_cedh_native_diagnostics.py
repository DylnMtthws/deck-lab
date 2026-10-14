"""The QA diagnostics profile is opt-in and held to a strict wire contract."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from sabermetrics.cedh.builder import build_candidate
from sabermetrics.cedh.settings import SimulatorSettings, load_cedh_settings
from sabermetrics.cedh.simulator import (
    HTTP_RESULT_SCHEMA_ID,
    HttpSimulatorClient,
    NotSimulated,
    SimulationResult,
    build_simulator,
)
from sabermetrics.cedh.wire import (
    DIAGNOSTICS_GAMES,
    DIAGNOSTICS_SCENARIO_ID,
    DIAGNOSTICS_SEED,
    DIAGNOSTICS_TURN,
    REQUEST_SCHEMA_ID,
    candidate_document,
    simulation_request,
)
from tests.test_cedh_simulator import (
    _result_headers,
    _result_payload,
)


@pytest.fixture
def candidate(kinnan_pack, cedh_cards):
    return build_candidate(kinnan_pack, cedh_cards)


def test_diagnostics_flag_defaults_off_and_parses_explicit_values(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("CEDH_SIMULATOR_DIAGNOSTICS", raising=False)
    config = tmp_path / "cedh.yaml"
    config.write_text("simulator:\n  diagnostics_enabled: true\n", encoding="utf-8")
    assert load_cedh_settings(config).simulator.diagnostics_enabled is False

    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CEDH_SIMULATOR_DIAGNOSTICS", value)
        assert (
            load_cedh_settings(tmp_path / "missing.yaml").simulator.diagnostics_enabled
            is True
        )
    for value in ("0", "false", "NO", "off", ""):
        monkeypatch.setenv("CEDH_SIMULATOR_DIAGNOSTICS", value)
        assert (
            load_cedh_settings(tmp_path / "missing.yaml").simulator.diagnostics_enabled
            is False
        )


def test_invalid_diagnostics_flag_fails_configuration_safely(monkeypatch, tmp_path):
    monkeypatch.setenv("CEDH_SIMULATOR_DIAGNOSTICS", "sometimes")
    with pytest.raises(ValueError, match="CEDH_SIMULATOR_DIAGNOSTICS"):
        load_cedh_settings(tmp_path / "missing.yaml")


def test_diagnostics_schema_is_packaged_and_vendored_identically():
    packaged = files("sabermetrics.cedh").joinpath(
        "contracts", "goldfish-diagnostics.v1.schema.json"
    )
    vendored = (
        Path(__file__).resolve().parents[1]
        / "fixtures/cedh/contracts/goldfish-diagnostics.v1.schema.json"
    )
    assert packaged.read_bytes() == vendored.read_bytes()


def test_wire_profile_is_fixed_and_default_request_is_unchanged(candidate):
    legacy = simulation_request(candidate, games=20000, turn=3)
    assert legacy == {
        "schema_version": REQUEST_SCHEMA_ID,
        "candidate": candidate_document(candidate),
        "games": 20000,
        "turn": 3,
        "seed": int(candidate.deck_sha256[:16], 16),
        "scenario": "goldfish_assembly.v1",
        "sweep": False,
        "ablate": [],
    }

    qa = simulation_request(candidate, games=999, turn=1, diagnostics=True)
    assert qa["scenario"] == DIAGNOSTICS_SCENARIO_ID
    assert qa["games"] == DIAGNOSTICS_GAMES == 2000
    assert qa["seed"] == DIAGNOSTICS_SEED == 20261012
    assert qa["turn"] == DIAGNOSTICS_TURN == 7
    assert qa["sweep"] is False and qa["ablate"] == []
    assert {key: qa[key] for key in legacy} == {
        **legacy,
        "games": 2000,
        "turn": 7,
        "seed": 20261012,
        "scenario": DIAGNOSTICS_SCENARIO_ID,
    }


def _diagnostics_payload():
    test = {
        "status": "unsupported",
        "metrics": {},
        "limitations": ["Not exercised in this contract fixture."],
        "evidence": ["Synthetic schema test only."],
    }
    return {
        "schema_version": "decklab.goldfish-diagnostics.v1",
        "settings": {
            "games": DIAGNOSTICS_GAMES,
            "seed": DIAGNOSTICS_SEED,
            "checkpoints": {
                "development_turn": 5,
                "wipe_turn": 5,
                "finish_turn": 7,
            },
            "engine_version": "fixture-engine",
            "settings_fingerprint": "test-settings-fingerprint",
        },
        "coverage": {
            "total_cards": 100,
            "modeled_cards": 0,
            "inert_cards": 0,
            "unauthored_cards": 100,
            "assumptions": ["Synthetic test data."],
        },
        "tests": {
            key: dict(test)
            for key in (
                "opening",
                "development",
                "commander_loss",
                "creature_wipe",
                "finish",
            )
        },
    }


def _diagnostic_response(candidate, *, diagnostics=True):
    payload = _result_payload(candidate)
    payload["simulation"].update(
        games=DIAGNOSTICS_GAMES,
        seed=DIAGNOSTICS_SEED,
        objective_turn=DIAGNOSTICS_TURN,
        requested_scenario={"id": "goldfish_diagnostics", "version": "1.0.0"},
    )
    if diagnostics:
        payload["diagnostics"] = _diagnostics_payload()
    headers = _result_headers()
    headers["X-Sim-Result-Schema"] = HTTP_RESULT_SCHEMA_ID
    return payload, headers


def test_opening_dependency_and_table_contract_accepts_only_known_shape():
    from jsonschema import ValidationError

    from sabermetrics.cedh.simulator import _diagnostics_result_validator

    payload = _diagnostics_payload()
    payload["settings"]["table_context"] = {
        "opponents": 0,
        "opponent_colors": 0,
        "on_the_play": True,
        "life_floor": 10,
    }
    proof = {
        "version": 1,
        "scope": "native_opening_policy",
        "algorithm": "opening-policy-dependencies.v2",
        "dependency_accounting_complete": True,
        "dependency_fingerprint": "a" * 64,
    }
    payload["tests"]["opening"]["comparison_coverage"] = proof
    _diagnostics_result_validator().validate(payload)
    proof["dependency_fingerprint"] = "unknown"
    with pytest.raises(ValidationError):
        _diagnostics_result_validator().validate(payload)


def test_qa_http_client_sends_exact_profile_and_requires_valid_report(candidate):
    seen = {}
    payload, headers = _diagnostic_response(candidate)

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=payload, headers=headers)

    client = HttpSimulatorClient(
        "http://sim",
        diagnostics=True,
        transport=httpx.MockTransport(handler),
    )
    result = client.simulate(candidate)
    assert isinstance(result, SimulationResult), getattr(result, "detail", "")
    assert seen["body"] == simulation_request(
        candidate, games=20000, turn=3, diagnostics=True
    )
    assert result.raw_document["diagnostics"] == payload["diagnostics"]


@pytest.mark.parametrize(
    "malformation",
    [
        "missing",
        "missing_test",
        "bad_status",
        "missing_metric_unit",
        "wrong_seed",
        "wrong_checkpoint",
        "wrong_scenario",
    ],
)
def test_qa_http_client_rejects_missing_or_malformed_diagnostics(
    candidate, malformation
):
    payload, headers = _diagnostic_response(candidate)
    if malformation == "missing":
        del payload["diagnostics"]
    elif malformation == "missing_test":
        del payload["diagnostics"]["tests"]["finish"]
    elif malformation == "bad_status":
        payload["diagnostics"]["tests"]["finish"]["status"] = "complete"
    elif malformation == "missing_metric_unit":
        payload["diagnostics"]["tests"]["opening"]["metrics"]["rate"] = {"value": 0.5}
    elif malformation == "wrong_seed":
        payload["diagnostics"]["settings"]["seed"] = 99
    elif malformation == "wrong_checkpoint":
        payload["diagnostics"]["settings"]["checkpoints"]["finish_turn"] = 8
    elif malformation == "wrong_scenario":
        payload["simulation"]["requested_scenario"]["id"] = "goldfish_assembly"

    client = HttpSimulatorClient(
        "http://sim",
        diagnostics=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, headers=headers)
        ),
    )
    result = client.simulate(candidate)
    assert isinstance(result, NotSimulated)
    assert result.reason == "contract_violation"


def test_legacy_request_rejects_unsolicited_diagnostics(candidate):
    payload, headers = _diagnostic_response(candidate)
    client = HttpSimulatorClient(
        "http://sim",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, headers=headers)
        ),
    )
    result = client.simulate(candidate)
    assert isinstance(result, NotSimulated)
    assert result.reason == "contract_violation"


def test_settings_enable_diagnostics_only_for_http_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("CEDH_SIMULATOR_URL", "http://sim")
    monkeypatch.setenv("CEDH_SIMULATOR_DIAGNOSTICS", "true")
    settings = load_cedh_settings(tmp_path / "missing.yaml").simulator
    client = build_simulator(settings)
    assert isinstance(client, HttpSimulatorClient)
    assert client._diagnostics is True
    settings = SimulatorSettings(mode="http", url="http://sim")
    assert build_simulator(settings)._diagnostics is False
    settings = SimulatorSettings(mode="subprocess", diagnostics_enabled=True)
    assert not isinstance(build_simulator(settings), HttpSimulatorClient)


def test_explicit_independent_diagnostics_seed_is_sent_and_checked(candidate):
    payload, headers = _diagnostic_response(candidate)
    payload["diagnostics"]["settings"]["seed"] = 123456
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=payload, headers=headers)

    client = HttpSimulatorClient(
        "http://sim",
        diagnostics=True,
        diagnostics_seed=123456,
        transport=httpx.MockTransport(handler),
    )
    assert isinstance(client.simulate(candidate), SimulationResult)
    assert seen["seed"] == 123456
