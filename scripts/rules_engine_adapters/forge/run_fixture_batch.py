#!/usr/bin/env python3
"""Run every rules scenario in tests/fixtures/rules_engine_scenarios.json
through Forge in ONE JVM, convert each result to a rules-engine-receipt.v1,
and judge it with the benchmark harness's own validate_receipt / evaluate /
cross_engine against the harness's ReferenceKernel.

This is the single-JVM equivalent of ``benchmark_rules_engines.py --engines
forge``: same receipts, same judge, but one engine start instead of one per
scenario. Timings here are NOT harness measurements and are not reported as
such. The harness module is imported read-only.

Usage: python -I run_fixture_batch.py [--out PATH] [--no-auth-gate]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import benchmark_rules_engines as bench
import forge_adapter as fa
import harness_contract as hc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=bench.DEFAULT_FIXTURES)
    parser.add_argument(
        "--out", type=Path, default=fa.ROOT / "forge-fixture-batch.json"
    )
    parser.add_argument("--no-auth-gate", action="store_true")
    args = parser.parse_args(argv)

    doc = bench.load_fixtures(args.fixtures)
    version = hc.engine_version()
    results: dict[str, dict[str, Any]] = {}
    to_run: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for scenario in doc["scenarios"]:
        payload = {"contract": hc.CONTRACT, "scenario": scenario}
        if (
            scenario["kind"] != "rules_state"
            or set(scenario["required_capabilities"]) - hc.CAPABILITIES
        ):
            results[scenario["id"]] = {
                "receipt": hc.run_contract(payload, require_auth=True)
            }
            continue
        try:
            to_run.append((scenario, hc.translate(scenario)))
        except hc.Untranslatable:
            results[scenario["id"]] = {"receipt": hc.run_contract(payload)}
    adapter_receipts = fa.run_batch(
        [r for _, r in to_run], require_auth=not args.no_auth_gate
    )
    for (scenario, request), ar in zip(to_run, adapter_receipts):
        results[scenario["id"]] = {
            "receipt": hc.receipt_from_adapter(scenario, request, ar, version),
            "jvm": ar.get("measured"),
        }

    reference = bench.ReferenceAdapter()
    report = []
    counts: dict[str, int] = {}
    for scenario in doc["scenarios"]:
        sha = bench.scenario_sha256(scenario)
        receipt = results[scenario["id"]]["receipt"]
        try:
            bench.validate_receipt(receipt, "forge", scenario, sha)
            verdict, mismatches, _ = bench.evaluate(scenario, receipt)
        except bench.ReceiptError as exc:
            verdict, mismatches = "rejected_receipt", [str(exc)]
        cross = None
        if receipt.get("status") == "completed":
            ref = reference.execute(scenario, sha)
            if ref["status"] == "completed":
                cross = bench.cross_engine(
                    scenario, {"forge": receipt, "reference": ref}
                )[0]
        counts[verdict] = counts.get(verdict, 0) + 1
        report.append(
            {
                "scenario_id": scenario["id"],
                "polarity": scenario["polarity"],
                "verdict": verdict,
                "mismatches": mismatches,
                "forge_vs_reference": cross,
                "receipt": receipt,
            }
        )
    out = {
        "schema": "forge-fixture-batch.v1",
        "fixtures": str(args.fixtures),
        "fixtures_sha256": bench.file_sha256(args.fixtures),
        "engine_version": version,
        "verdicts": counts,
        "jvm": next((v.get("jvm") for v in results.values() if v.get("jvm")), None),
        "results": report,
    }
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(json.dumps(counts, sort_keys=True))
    for r in report:
        tail = (
            ""
            if r["verdict"] in ("match", "unsupported")
            else f" {r['mismatches'][:3]}"
        )
        agree = (
            ""
            if r["forge_vs_reference"] is None
            else (
                " ref=agree"
                if r["forge_vs_reference"]["agree"]
                else f" ref=DIFF{r['forge_vs_reference']['differences'][:3]}"
            )
        )
        print(f"{r['scenario_id']}: {r['verdict']}{agree}{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
