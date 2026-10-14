"""Tests for scripts/benchmark_rules_engines.py.

The harness must judge engines against hand-derived expectations. It must
catch divergence in outcome, state, trace order and statistics. It must
reject partial, malformed, mismatched or self-measured receipts, and it must
never report a result for an engine that is not installed.
"""

from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import math
import os
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_rules_engines.py"
FIXTURES = ROOT / "tests" / "fixtures" / "rules_engine_scenarios.json"

_spec = importlib.util.spec_from_file_location("benchmark_rules_engines", SCRIPT)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
sys.modules["benchmark_rules_engines"] = bench
_spec.loader.exec_module(bench)


@pytest.fixture(scope="module")
def fixtures():
    return bench.load_fixtures(FIXTURES)


@pytest.fixture(scope="module")
def scenarios(fixtures):
    return {s["id"]: s for s in fixtures["scenarios"]}


@pytest.fixture(scope="module")
def reference_report(fixtures):
    return bench.run_benchmark(fixtures, FIXTURES, [bench.ReferenceAdapter()], repeat=2)


def _receipt(scenario, engine="reference"):
    return bench.ReferenceAdapter().execute(
        scenario, bench.scenario_sha256(scenario)
    ) | ({"engine": engine} if engine != "reference" else {})


# -- fixtures -----------------------------------------------------------------


def test_fixture_covers_vivi_and_sauron_with_both_polarities(fixtures):
    by = {(s["deck_context"], s["polarity"], s["kind"]) for s in fixtures["scenarios"]}
    for ctx in ("vivi", "sauron"):
        for polarity in ("positive", "negative"):
            assert (ctx, polarity, "rules_state") in by
            assert (ctx, polarity, "resource_probe") in by
    assert len(fixtures["scenarios"]) >= 25


def test_every_scenario_is_sourced_and_cards_have_oracle_provenance(fixtures):
    for scenario in fixtures["scenarios"]:
        assert scenario["provenance"]
        assert scenario["expected_derivation"].strip()
    for name, card in fixtures["card_provenance"].items():
        assert card["oracle_id"] and card["scryfall_uri"].startswith(
            "https://scryfall.com/"
        )
        assert card["oracle_text"] or "Land" in card["type_line"], name


def _bad(tmp_path, mutate):
    doc = json.loads(FIXTURES.read_text())
    mutate(doc)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(bench.FixtureError):
        bench.load_fixtures(path)


def test_fixture_loader_rejects_unsourced_scenario(tmp_path):
    _bad(tmp_path, lambda d: d["scenarios"][0].update(provenance=[]))
    _bad(tmp_path, lambda d: d["scenarios"][0].update(provenance=["made_up"]))


def test_fixture_loader_rejects_illegal_expectation_without_reason(tmp_path):
    _bad(tmp_path, lambda d: d["scenarios"][0]["expected"].pop("reason_code"))


def test_fixture_loader_rejects_probability_that_disagrees_with_exact_model(tmp_path):
    def mutate(doc):
        for s in doc["scenarios"]:
            if s["expected"].get("statistic"):
                s["expected"]["statistic"]["probability"] += 0.01

    _bad(tmp_path, mutate)


def test_fixture_loader_rejects_duplicates_and_unasserted_negative(tmp_path):
    _bad(tmp_path, lambda d: d["scenarios"].append(d["scenarios"][0]))

    def mutate(doc):
        s = next(
            x for x in doc["scenarios"] if x["id"] == "sauron_own_spell_does_not_amass"
        )
        s["expected"].pop("trace_absent")

    _bad(tmp_path, mutate)


def test_fixture_loader_rejects_card_without_provenance(tmp_path):
    def mutate(doc):
        doc["scenarios"][0]["initial_state"]["players"]["you"]["hand"].append(
            "Black Lotus"
        )

    _bad(tmp_path, mutate)


# -- exact probabilities --------------------------------------------------------


def test_two_color_probability_matches_direct_formula():
    p = bench.exact_probe_probability(
        {
            "name": "two_color_untapped",
            "color_a": 95,
            "color_b": 4,
            "deck": 99,
            "turns": 3,
            "cmc": 3,
        }
    )
    assert p == pytest.approx(1 - math.comb(95, 10) / math.comb(99, 10))
    below = bench.exact_probe_probability(
        {
            "name": "two_color_untapped",
            "color_a": 95,
            "color_b": 4,
            "deck": 99,
            "turns": 2,
            "cmc": 3,
        }
    )
    assert below == 0.0


def _brute_tapped(lands, deck, turns, cmc):
    hits = total = 0
    for positions in itertools.combinations(range(deck), lands):
        order = [0] * deck
        for i in positions:
            order[i] = 1
        seen, played, ok = sum(order[:7]), 0, False
        for turn in range(1, turns + 1):
            if played >= cmc:
                ok = True
                break
            seen += order[6 + turn]
            if seen > played:
                played += 1
        hits += ok
        total += 1
    return hits / total


@pytest.mark.parametrize(
    ("lands", "deck", "turns", "cmc"), [(5, 14, 6, 3), (8, 16, 7, 4)]
)
def test_tapped_land_dp_matches_brute_force_enumeration(lands, deck, turns, cmc):
    dp = bench.exact_probe_probability(
        {
            "name": "tapped_lands_count",
            "lands": lands,
            "deck": deck,
            "turns": turns,
            "cmc": cmc,
        }
    )
    assert dp == pytest.approx(_brute_tapped(lands, deck, turns, cmc), abs=1e-12)


# -- reference kernel -----------------------------------------------------------


def test_reference_matches_every_supported_scenario(reference_report):
    verdicts = {r["scenario_id"]: r["verdict"] for r in reference_report["results"]}
    assert verdicts.pop("sauron_ward_counters_unpaid_bolt") == "unsupported"
    assert set(verdicts.values()) == {"match"}, {
        k: v for k, v in verdicts.items() if v != "match"
    }
    bench.validate_report(reference_report, bench.load_fixtures(FIXTURES))


def test_unsupported_names_the_exact_missing_capability(reference_report):
    ward = next(
        r
        for r in reference_report["results"]
        if r["scenario_id"] == "sauron_ward_counters_unpaid_bolt"
    )
    assert ward["receipt"]["missing_capabilities"] == ["ward_sacrifice_cost"]
    assert ward["measurement"] is None


def test_kernel_raises_unsupported_for_ward_even_without_precheck(scenarios):
    receipt = _receipt(scenarios["sauron_ward_counters_unpaid_bolt"])
    assert receipt["status"] == "unsupported"
    assert receipt["missing_capabilities"] == ["ward_sacrifice_cost"]


def test_kernel_reports_unknown_card_as_unsupported(scenarios):
    scenario = copy.deepcopy(scenarios["land_drop_limit"])
    scenario["initial_state"]["players"]["you"]["hand"].append("Black Lotus")
    receipt = _receipt(scenario)
    assert receipt["missing_capabilities"] == ["card_not_implemented:Black Lotus"]


def test_illegal_action_rolls_state_back(scenarios):
    receipt = _receipt(scenarios["vivi_zero_power_cannot_pay"])
    assert receipt["outcome"] == "illegal"
    assert receipt["final_state"]["players"]["you"]["hand"] == ["Lightning Bolt"]
    assert receipt["final_state"]["stack"] == []
    assert not any(e["event"] == "cast" for e in receipt["trace"])


def test_bowmasters_brainstorm_builds_one_four_power_army(scenarios):
    state = _receipt(scenarios["sauron_bowmasters_punish_brainstorm"])["final_state"]
    armies = [p for p in state["battlefield"] if p["name"] == "Orc Army"]
    assert len(armies) == 1 and armies[0]["power"] == 4
    assert state["players"]["opp"]["life"] == 37


def test_missing_choice_is_an_error_not_a_guess(scenarios):
    scenario = copy.deepcopy(scenarios["sauron_birthday_escape_wheel"])
    scenario["actions"][1]["choices"].pop("sauron_discard")
    receipt = _receipt(scenario)
    assert receipt["status"] == "error"
    assert "sauron_discard" in receipt["error"]


# -- divergence detection -------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario_id", "mutate"),
    [
        (
            "vivi_dictate_incinerate_trigger_order",
            lambda e: e["final_state"]["players"]["opp"].update(life=17),
        ),
        (
            "vivi_dictate_incinerate_trigger_order",
            lambda e: e["trace"].insert(2, e["trace"].pop(6)),
        ),
        ("vivi_once_per_turn", lambda e: e.update(illegal_at=2)),
        ("vivi_once_per_turn", lambda e: e.update(reason_code="timing")),
        ("vivi_zero_power_cannot_pay", lambda e: e.update(outcome="legal")),
        (
            "sauron_opponent_spells_amass_one_army",
            lambda e: e["final_state"]["battlefield"].append(
                dict(e["final_state"]["battlefield"][2])
            ),
        ),
        (
            "sauron_own_spell_does_not_amass",
            lambda e: e["trace_absent"].append({"event": "damage"}),
        ),
        (
            "vivi_grow_then_activate_brainstorm",
            lambda e: e["final_state"]["players"]["you"].update(
                library=["Island", "Mountain", "Counterspell", "Incinerate"]
            ),
        ),
    ],
)
def test_wrong_expectations_are_reported_divergent(scenarios, scenario_id, mutate):
    scenario = copy.deepcopy(scenarios[scenario_id])
    receipt = _receipt(scenario)
    mutate(scenario["expected"])
    verdict, mismatches, _ = bench.evaluate(scenario, receipt)
    assert verdict == "divergent" and mismatches


def test_statistic_outside_tolerance_is_divergent(scenarios):
    scenario = copy.deepcopy(scenarios["probe_vivi_95_island_4_mountain_turn3"])
    receipt = _receipt(scenario)
    receipt["final_state"]["commander_cast_count"] = int(0.45 * 4000)
    verdict, mismatches, stats = bench.evaluate(scenario, receipt)
    assert verdict == "divergent" and abs(stats["z"]) > 4.5
    assert "statistic" in mismatches[0]


def test_cross_engine_divergence_is_detected(scenarios):
    scenario = scenarios["vivi_dictate_incinerate_trigger_order"]
    a = _receipt(scenario)
    b = copy.deepcopy(a)
    b["final_state"]["players"]["opp"]["life"] = 15
    [pair] = bench.cross_engine(scenario, {"a": a, "b": b})
    assert not pair["agree"] and "final_state.players differs" in pair["differences"]


# -- receipt validation ---------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(wall_ms=1.0), "measured by the harness"),
        (lambda r: r.update(peak_rss_bytes=10), "measured by the harness"),
        (lambda r: r.pop("trace"), "lacks trace"),
        (lambda r: r.update(trace=[]), "needs final_state and trace"),
        (lambda r: r.update(final_state={}), "needs final_state and trace"),
        (
            lambda r: r.update(scenario_sha256="sha256:" + "1" * 64),
            "different scenario",
        ),
        (lambda r: r.update(engine="xmage"), "is not"),
        (lambda r: r.update(schema_version="v0"), "schema_version"),
        (lambda r: r.update(status="passed"), "unknown status"),
        (lambda r: r.update(outcome="won"), "unknown outcome"),
        (lambda r: r.update(illegal_at=0), "only allowed with an illegal"),
        (lambda r: r.update(engine_version=""), "engine_version"),
        (lambda r: r.update(trace=["cast"]), "not an event object"),
        (lambda r: r.pop("scenario_id"), "missing fields"),
    ],
)
def test_malformed_or_fake_rules_receipts_are_rejected(scenarios, mutate, message):
    scenario = scenarios["vivi_dictate_incinerate_trigger_order"]
    receipt = _receipt(scenario)
    mutate(receipt)
    with pytest.raises(bench.ReceiptError, match=message):
        bench.validate_receipt(
            receipt, "reference", scenario, bench.scenario_sha256(scenario)
        )


def test_partial_unsupported_error_and_probe_receipts_are_rejected(scenarios):
    probe = scenarios["probe_vivi_volcanic_turn3_always"]
    sha = bench.scenario_sha256(probe)
    good = _receipt(probe)
    bench.validate_receipt(good, "reference", probe, sha)
    for mutate in (
        lambda r: r["final_state"].update(commander_cast_count=501),
        lambda r: r["final_state"].update(games=499),
        lambda r: r["final_state"].update(commander_cast_count=True),
        lambda r: r["final_state"].pop("lands_mean"),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        with pytest.raises(bench.ReceiptError):
            bench.validate_receipt(bad, "reference", probe, sha)
    base = {k: good[k] for k in bench._COMMON}
    with pytest.raises(bench.ReceiptError, match="missing_capabilities"):
        bench.validate_receipt(
            base | {"status": "unsupported"}, "reference", probe, sha
        )
    with pytest.raises(bench.ReceiptError, match="error message"):
        bench.validate_receipt(base | {"status": "error"}, "reference", probe, sha)
    illegal = _receipt(scenarios["vivi_once_per_turn"])
    illegal.pop("illegal_at")
    with pytest.raises(bench.ReceiptError, match="illegal_at"):
        bench.validate_receipt(
            illegal,
            "reference",
            scenarios["vivi_once_per_turn"],
            bench.scenario_sha256(scenarios["vivi_once_per_turn"]),
        )
    with pytest.raises(bench.ReceiptError, match="not an object"):
        bench.validate_receipt([], "reference", probe, sha)


# -- report validation (fake benchmark claims) ------------------------------------


@pytest.fixture(scope="module")
def xmage_report(fixtures, tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PATH", str(tmp_path_factory.mktemp("empty_path")))
        mp.delenv("SABER_XMAGE_HOME", raising=False)
        mp.delenv("SABER_XMAGE_BRIDGE", raising=False)
        mp.delenv("SABER_XMAGE_JAVA_HOME", raising=False)
        mp.delenv("JAVA_HOME", raising=False)
        adapters = bench.build_adapters(["reference", "xmage"], None)
    return bench.run_benchmark(fixtures, FIXTURES, adapters, repeat=1)


def test_unavailable_engine_reports_exact_reasons_and_no_results(
    fixtures, xmage_report
):
    report = copy.deepcopy(xmage_report)
    xmage = next(e for e in report["engines"] if e["name"] == "xmage")
    assert not xmage["available"] and xmage["version"] is None
    assert any(r.startswith("no java executable") for r in xmage["reasons"])
    assert any("SABER_XMAGE_HOME" in r for r in xmage["reasons"])
    assert any("bridge command" in r for r in xmage["reasons"])
    rows = [r for r in report["results"] if r["engine"] == "xmage"]
    assert rows and all(
        r["verdict"] == "unavailable"
        and r["receipt"] is None
        and r["measurement"] is None
        for r in rows
    )
    assert "xmage" in report["recommendation"]["unmeasured"]
    assert "xmage" not in report["recommendation"]["measured"]
    bench.validate_report(report, fixtures)


@pytest.mark.parametrize(
    "tamper",
    [
        "fake_xmage_match",
        "measurement_on_unsupported",
        "foreign_measurement",
        "flipped_verdict",
        "summary",
        "recommend_unavailable",
        "scenario_hash",
        "available_without_version",
    ],
)
def test_report_validation_rejects_fabricated_claims(fixtures, xmage_report, tamper):
    report = copy.deepcopy(xmage_report)
    bench.validate_report(report, fixtures)
    ref = [r for r in report["results"] if r["engine"] == "reference"]
    row = next(
        r for r in ref if r["scenario_id"] == "vivi_dictate_incinerate_trigger_order"
    )
    if tamper == "fake_xmage_match":
        fake = next(r for r in report["results"] if r["engine"] == "xmage")
        fake.update(
            verdict="match",
            receipt=copy.deepcopy(row["receipt"]) | {"engine": "xmage"},
            measurement=copy.deepcopy(row["measurement"]),
        )
    elif tamper == "measurement_on_unsupported":
        ward = next(r for r in ref if r["verdict"] == "unsupported")
        ward["measurement"] = copy.deepcopy(row["measurement"])
    elif tamper == "foreign_measurement":
        row["measurement"]["measured_by"] = "engine"
    elif tamper == "flipped_verdict":
        row["receipt"]["final_state"]["players"]["opp"]["life"] = 15
    elif tamper == "summary":
        report["summary"]["reference"]["match"] += 1
    elif tamper == "recommend_unavailable":
        report["recommendation"]["measured"]["xmage"] = {"judged": 29}
    elif tamper == "scenario_hash":
        row["scenario_sha256"] = "sha256:" + "2" * 64
    elif tamper == "available_without_version":
        report["engines"][0]["version"] = None
    with pytest.raises(bench.ReportError):
        bench.validate_report(report, fixtures)


# -- external adapter contract (test-double bridges) -------------------------------

_BRIDGE = textwrap.dedent(
    """
    import importlib.util, json, sys
    spec = importlib.util.spec_from_file_location("b", {script!r})
    b = importlib.util.module_from_spec(spec); sys.modules["b"] = b; spec.loader.exec_module(b)
    MODE = {mode!r}
    if sys.argv[1] == "capabilities":
        caps = sorted(b.REFERENCE_CAPABILITIES) if MODE != "badcaps" else "all"
        print(json.dumps({{"schema_version": b.CAPABILITIES_SCHEMA, "engine": "test-double",
                          "engine_version": "double-1", "capabilities": caps}}))
        sys.exit(0)
    request = json.loads(sys.stdin.read())
    assert request["contract"] == b.ADAPTER_CONTRACT
    scenario = request["scenario"]
    receipt = b.ReferenceAdapter().execute(scenario, b.scenario_sha256(scenario))
    receipt["engine"] = "test-double"; receipt["engine_version"] = "double-1"
    if MODE == "selftimed":
        receipt["wall_ms"] = 0.001
    if MODE == "garbage":
        print("not json"); sys.exit(3)
    print(json.dumps(receipt))
    """
)


def _bridge(tmp_path, mode):
    path = tmp_path / f"bridge_{mode}.py"
    path.write_text(_BRIDGE.format(script=str(SCRIPT), mode=mode))
    return bench.ExternalCommandAdapter("test-double", [sys.executable, str(path)], [])


def _only(fixtures, ids):
    return {
        **fixtures,
        "scenarios": [s for s in fixtures["scenarios"] if s["id"] in ids],
    }


IDS = {"vivi_dictate_incinerate_trigger_order", "probe_rejects_98_slots"}


def test_conforming_bridge_is_measured_by_the_harness(fixtures, tmp_path):
    report = bench.run_benchmark(
        _only(fixtures, IDS), FIXTURES, [_bridge(tmp_path, "ok")], repeat=2
    )
    assert {r["verdict"] for r in report["results"]} == {"match"}
    for row in report["results"]:
        m = row["measurement"]
        assert m["measured_by"] == "harness" and m["repeats"] == 2
        assert m["wall_ms"]["min"] > 0 and m["peak_rss_bytes_max"] > 0
    bench.validate_report(report, fixtures)


def test_self_timed_bridge_receipt_is_rejected(fixtures, tmp_path):
    report = bench.run_benchmark(
        _only(fixtures, IDS), FIXTURES, [_bridge(tmp_path, "selftimed")], repeat=1
    )
    assert {r["verdict"] for r in report["results"]} == {"rejected_receipt"}
    assert all(r["measurement"] is None for r in report["results"])
    assert bench.exit_code(report) == 1


def test_garbage_bridge_output_is_an_error(fixtures, tmp_path):
    report = bench.run_benchmark(
        _only(fixtures, IDS), FIXTURES, [_bridge(tmp_path, "garbage")], repeat=1
    )
    assert {r["verdict"] for r in report["results"]} == {"error"}
    assert "unparseable receipt" in report["results"][0]["mismatches"][0]


def test_bridge_with_bad_capabilities_is_unavailable(fixtures, tmp_path):
    avail = _bridge(tmp_path, "badcaps").probe()
    assert not avail.available
    assert "bridge capabilities call failed" in avail.reasons[0]


# -- native adapter ------------------------------------------------------------


def _fake_cs(tmp_path, body):
    path = tmp_path / "cs"
    path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def test_native_malformed_output_is_an_error_not_a_result(fixtures, tmp_path):
    cs = _fake_cs(
        tmp_path,
        """
        import json, sys
        if sys.argv[1] == "--version":
            print("fake 0"); sys.exit(0)
        req = json.load(open(sys.argv[2]))
        print(json.dumps({"schema_version": "resource-probe-result.v1",
              "scenario": "commander-land-engine-only.v1",
              "deck_identity": req["deck_identity"], "simulation_input_sha256": "x",
              "games": req["games"], "turns": req["turns"], "commander_cast_count": req["games"],
              "lands_mean": 3.0, "commander_draws_mean": 0.0, "cast_samples": [1],
              "assumptions": ["a"]}))
        """,
    )
    report = bench.run_benchmark(
        _only(fixtures, {"probe_vivi_volcanic_turn3_always"}),
        FIXTURES,
        [bench.NativeProbeAdapter(cs)],
        repeat=1,
    )
    [row] = report["results"]
    assert row["verdict"] == "error"
    assert "cast_samples disagree" in row["mismatches"][0]


def test_native_rules_scenarios_are_unsupported_with_reason(fixtures, tmp_path):
    cs = _fake_cs(tmp_path, "import sys\nprint('fake 0')\n")
    report = bench.run_benchmark(
        _only(fixtures, {"vivi_dictate_incinerate_trigger_order"}),
        FIXTURES,
        [bench.NativeProbeAdapter(cs)],
        repeat=1,
    )
    [row] = report["results"]
    assert row["verdict"] == "unsupported"
    assert "no state-injection entry point" in row["receipt"]["detail"]
    assert "stack_and_triggers" in row["receipt"]["missing_capabilities"]


def test_native_unconfigured_is_unavailable():
    avail = bench.NativeProbeAdapter(None).probe()
    assert not avail.available and "CEDH_SIMULATOR_BIN" in avail.reasons[0]


@pytest.mark.simulator
@pytest.mark.skipif(
    not os.environ.get("CEDH_SIMULATOR_BIN"), reason="needs CEDH_SIMULATOR_BIN"
)
def test_real_native_binary_matches_every_probe(fixtures):
    adapter = bench.NativeProbeAdapter(Path(os.environ["CEDH_SIMULATOR_BIN"]))
    report = bench.run_benchmark(
        fixtures, FIXTURES, [bench.ReferenceAdapter(), adapter], repeat=2
    )
    native = [r for r in report["results"] if r["engine"] == "native"]
    probes = [r for r in native if r["scenario_id"].startswith("probe_")]
    assert probes and {r["verdict"] for r in probes} == {"match"}
    assert {r["verdict"] for r in native if r not in probes} == {"unsupported"}
    assert all(r["measurement"]["peak_rss_bytes_max"] > 0 for r in probes)
    assert all(pair["agree"] for pair in report["cross_engine"])
    bench.validate_report(report, fixtures)


# -- CLI ----------------------------------------------------------------------


def test_cli_writes_a_report_that_revalidates_and_rejects_tampering(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = bench.main(
        [
            "--engines",
            "reference",
            "--repeat",
            "1",
            "--out",
            str(out),
            "--scenario",
            "land_drop_limit",
            "--scenario",
            "vivi_once_per_turn",
        ]
    )
    assert code == 0
    assert bench.main(["--validate-report", str(out)]) == 0
    doc = json.loads(out.read_text())
    doc["results"][0]["verdict"] = "divergent"
    out.write_text(json.dumps(doc))
    assert bench.main(["--validate-report", str(out)]) == 2
    assert "invalid report" in capsys.readouterr().err


def test_java_preflight_uses_engine_jdk_home_and_reports_only_the_missing_bridge(
    tmp_path, monkeypatch
):
    jdk = tmp_path / "jdk"
    (jdk / "bin").mkdir(parents=True)
    java = jdk / "bin" / "java"
    java.write_text("#!/bin/sh\necho 'openjdk version \"17\"' >&2\n")
    java.chmod(0o755)
    monkeypatch.setenv("SABER_FORGE_JAVA_HOME", str(jdk))
    monkeypatch.setenv("SABER_FORGE_HOME", str(tmp_path))
    monkeypatch.delenv("SABER_FORGE_BRIDGE", raising=False)
    [adapter] = bench.build_adapters(["forge"], None)
    avail = adapter.probe()
    assert not avail.available
    assert avail.reasons == [
        "no rules-engine-adapter.v1 bridge command configured for forge"
    ]
