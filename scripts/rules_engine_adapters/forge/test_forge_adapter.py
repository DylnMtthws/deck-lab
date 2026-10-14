"""JVM-free tests for the Forge adapter's validation, gate and protocol checks.

The stand-in "engine" here is a tiny Python script; these tests only prove the
adapter refuses malformed or partial bridge output. They prove nothing about
Forge. Run: python -m pytest scripts/rules_engine_adapters/forge -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge_adapter as fa


def _request(sid: str = "s1") -> dict[str, Any]:
    return {
        "scenario_id": sid,
        "players": [
            {"life": 40, "zones": {"hand": ["Dark Ritual"], "battlefield": ["Swamp"]}},
            {"life": 40, "zones": {}},
        ],
        "actions": [{"op": "cast", "player": 0, "card": "Dark Ritual"}],
    }


def test_validate_rejects_bad_zone_and_action() -> None:
    req = _request()
    req["players"][0]["zones"]["stack"] = ["X"]
    req["actions"].append({"op": "attack"})
    problems = fa.validate_request(req)
    assert any("stack" in p for p in problems)
    assert any("attack" in p for p in problems)


def test_validate_rejects_single_player() -> None:
    req = _request()
    req["players"] = req["players"][:1]
    assert fa.validate_request(req)


def test_auth_gate_blocks_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fa, "AUTH_FILE", tmp_path / "absent")
    receipt = fa.run_scenario(_request())
    assert receipt["status"] == "not_run"
    assert "bridge" not in receipt and "observed" not in receipt


def test_duplicate_ids_rejected() -> None:
    receipts = fa.run_batch([_request("a"), _request("a")], require_auth=False)
    assert {r["status"] for r in receipts} == {"invalid_scenario"}


def _fake_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout_lines: list[str]
) -> None:
    script = tmp_path / "fake_engine.py"
    script.write_text(
        "import sys\nsys.stdin.read()\n"
        + "".join(f"print({line!r})\n" for line in stdout_lines)
    )
    monkeypatch.setattr(fa, "java_command", lambda: [sys.executable, "-I", str(script)])
    monkeypatch.setattr(fa, "ensure_bridge_compiled", lambda: tmp_path)
    monkeypatch.setattr(fa, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(fa, "FORGE_HOME", tmp_path / "home")
    monkeypatch.setattr(fa, "BUILD_DIR", tmp_path / "build")


@pytest.mark.skipif(not fa.FORGE_JAR.exists(), reason="Forge release not present")
def test_non_json_stdout_is_protocol_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_engine(monkeypatch, tmp_path, ["Forge says hello"])
    receipt = fa.run_scenario(_request(), require_auth=False)
    assert receipt["status"] == "bridge_protocol_error"
    assert "observed" not in receipt


@pytest.mark.skipif(not fa.FORGE_JAR.exists(), reason="Forge release not present")
def test_missing_or_foreign_receipt_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrong_schema = json.dumps(
        {"schema": "other", "scenario_id": "s1", "status": "executed"}
    )
    _fake_engine(monkeypatch, tmp_path, [wrong_schema])
    receipt = fa.run_scenario(_request(), require_auth=False)
    assert receipt["status"] == "bridge_protocol_error"

    other_id = json.dumps(
        {"schema": "forge-bridge-receipt.v2", "scenario_id": "zz", "status": "executed"}
    )
    _fake_engine(monkeypatch, tmp_path, [other_id])
    receipt = fa.run_scenario(_request(), require_auth=False)
    assert receipt["status"] == "bridge_protocol_error"


@pytest.mark.skipif(not fa.FORGE_JAR.exists(), reason="Forge release not present")
def test_engine_error_status_passes_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    line = json.dumps(
        {
            "schema": "forge-bridge-receipt.v2",
            "scenario_id": "s1",
            "status": "unknown_cards",
            "unknown_cards": ["Dark Ritual"],
        }
    )
    _fake_engine(monkeypatch, tmp_path, [line])
    receipt = fa.run_scenario(_request(), require_auth=False)
    assert receipt["status"] == "unknown_cards"
    assert "observed" not in receipt


@pytest.mark.skipif(not fa.FORGE_JAR.exists(), reason="Forge release not present")
def test_script_provenance_reads_release_scripts() -> None:
    prov = fa._script_provenance(["Dark Ritual", "No Such Card Xyz"])
    assert prov["Dark Ritual"]["script"].endswith("d/dark_ritual.txt")
    assert "Produced$ B | Amount$ 3" in prov["Dark Ritual"]["text"]
    assert prov["No Such Card Xyz"]["script"] is None


# --- rules-engine-adapter.v1 contract layer (no JVM) -------------------------

import harness_contract as hc

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
import benchmark_rules_engines as bench


def _fixture(sid: str) -> dict[str, Any]:
    doc = json.loads(bench.DEFAULT_FIXTURES.read_text())
    return next(s for s in doc["scenarios"] if s["id"] == sid)


def test_scenario_hash_matches_harness() -> None:
    scenario = _fixture("vivi_once_per_turn")
    assert hc.scenario_sha256(scenario) == bench.scenario_sha256(scenario)


def test_every_fixture_rules_scenario_translates() -> None:
    doc = json.loads(bench.DEFAULT_FIXTURES.read_text())
    for scenario in doc["scenarios"]:
        if scenario["kind"] == "rules_state":
            request = hc.translate(scenario)
            assert not fa.validate_request(request), scenario["id"]


def test_translate_maps_players_targets_and_counters() -> None:
    req = hc.translate(_fixture("vivi_trigger_resolves_when_spell_countered"))
    assert [p["label"] for p in req["players"]] == ["you", "opp"]
    counter = req["actions"][3]
    assert counter["player"] == 1 and counter["targets"] == [
        {"stack_card": "Lightning Bolt"}
    ]
    req = hc.translate(_fixture("vivi_only_during_your_turn"))
    assert "Vivi Ornitier|Counters:P1P1=2" in req["players"][0]["zones"]["battlefield"]
    assert req["active_player"] == 1


def test_untranslatable_feature_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = json.loads(json.dumps(_fixture("vivi_once_per_turn")))
    scenario["actions"][0]["choices"] = {"some_new_choice": 1}
    monkeypatch.setattr(
        fa, "run_scenario", lambda *a, **k: pytest.fail("engine must not run")
    )
    receipt = hc.run_contract({"contract": hc.CONTRACT, "scenario": scenario})
    assert receipt["status"] == "unsupported"
    assert receipt["missing_capabilities"] == [
        "bridge_translation:choice:some_new_choice"
    ]
    bench.validate_receipt(receipt, "forge", scenario, bench.scenario_sha256(scenario))


def test_probe_scenario_is_unsupported() -> None:
    scenario = _fixture("probe_vivi_volcanic_turn2_never")
    receipt = hc.run_contract({"contract": hc.CONTRACT, "scenario": scenario})
    assert receipt["status"] == "unsupported"
    bench.validate_receipt(receipt, "forge", scenario, bench.scenario_sha256(scenario))


def _snapshot() -> dict[str, Any]:
    def player(label: str, **kw: Any) -> dict[str, Any]:
        base = {
            "label": label,
            "life": 40,
            "lost": False,
            "ring_tempted_count": 0,
            "ring_bearer": None,
            "mana_pool": {},
            "commander_casts": {},
            "zones": {
                z: []
                for z in (
                    "hand",
                    "library",
                    "battlefield",
                    "graveyard",
                    "exile",
                    "command",
                )
            },
        }
        base.update(kw)
        return base

    you = player("you", mana_pool={"B": 3, "U": 0})
    you["zones"]["battlefield"] = [
        {
            "name": "Orc Army Token",
            "forge_id": 9,
            "controller": "you",
            "tapped": False,
            "token": True,
            "power": 1,
            "toughness": 1,
            "damage": 0,
            "counters": {"+1/+1": 1},
        }
    ]
    you["zones"]["library"] = [
        {"name": "B", "forge_id": 2},
        {"name": "A", "forge_id": 3},
    ]
    return {
        "players": [you, player("opp")],
        "turn": 1,
        "active_player": "you",
        "stack": [
            {"source": "Vivi Ornitier", "is_trigger": True, "description": "x"},
            {"source": "Dark Ritual", "is_trigger": False, "description": "y"},
        ],
    }


def test_final_state_shape_and_token_name() -> None:
    state = hc.final_state(_snapshot())
    assert state["players"]["you"]["mana_pool"] == {"B": 3}
    assert state["players"]["you"]["library"] == ["B", "A"]  # top-first preserved
    assert state["battlefield"][0]["name"] == "Orc Army"
    assert state["stack"] == ["Dark Ritual", "Vivi Ornitier:trigger"]  # bottom -> top


def test_trace_put_back_merges_top_first() -> None:
    trace = hc.normalize_trace(
        [
            {"event": "put_back_card", "player": "you", "card": "Swamp", "origin": "o"},
            {
                "event": "put_back_card",
                "player": "you",
                "card": "Mountain",
                "origin": "o",
            },
            {
                "event": "token_created",
                "card": "Orc Army Token",
                "controller": "you",
                "origin": "o",
            },
        ]
    )
    assert trace[0]["event"] == "put_back" and trace[0]["cards"] == [
        "Mountain",
        "Swamp",
    ]
    assert trace[1]["name"] == "Orc Army" and [e["seq"] for e in trace] == [0, 1]


def test_converted_receipt_passes_harness_validation() -> None:
    scenario = _fixture("vivi_once_per_turn")
    request = hc.translate(scenario)
    adapter_receipt = {
        "status": "executed",
        "bridge": {
            "final_state": _snapshot(),
            "game_over": False,
            "trace": [
                {
                    "event": "illegal",
                    "action_index": 3,
                    "reason_code": "activation_limit",
                    "origin": "o",
                }
            ],
            "steps": [
                {
                    "index": 3,
                    "outcome": "illegal",
                    "reason_code": "activation_limit",
                    "detail": "Vivi Ornitier",
                    "reason_source": "forge:x",
                }
            ],
        },
    }
    receipt = hc.receipt_from_adapter(
        scenario, request, adapter_receipt, "test-version"
    )
    bench.validate_receipt(receipt, "forge", scenario, bench.scenario_sha256(scenario))
    assert receipt["outcome"] == "illegal" and receipt["illegal_at"] == 3
    assert not {"measured", "provenance", "timing"} & set(receipt)


def test_loss_events_pass_through_normalization() -> None:
    trace = hc.normalize_trace(
        [
            {
                "event": "turn",
                "active": "opp",
                "turn": 2,
                "origin": "forge_event:GameEventTurnBegan",
            },
            {
                "event": "draw_empty",
                "player": "opp",
                "origin": "forge_state:PlayerOutcome.lossState=Milled",
            },
            {
                "event": "sba_loses",
                "player": "opp",
                "loss_reason": "Milled",
                "origin": "forge_state:Player.hasLost",
            },
        ]
    )
    assert [e["event"] for e in trace] == ["turn", "draw_empty", "sba_loses"]
    scenario = _fixture("draw_step_from_empty_library_loses")
    assert bench.compare_trace(scenario["expected"]["trace"], trace) == []


def test_bridge_source_routes_scripted_colours_and_losses() -> None:
    # Guards the two fixes from the final review against regression (no JVM).
    src = fa.BRIDGE_SOURCE.read_text()
    assert "PENDING_COLORS" in src and "PENDING_COLOR " not in src
    assert "reportLosses(game);" in src and "GameEventTurnBegan" in src
