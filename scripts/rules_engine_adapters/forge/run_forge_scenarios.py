#!/usr/bin/env python3
"""Run scenarios.json through Forge in one JVM and check Forge against
the hand-derived Oracle expectations.

The expectations never reach the engine. A failed check means Forge and the
Oracle-derived expectation disagree -- it is reported, not repaired.

Usage: python -I run_forge_scenarios.py [--out PATH] [--only ID ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge_adapter

SCENARIOS = Path(__file__).resolve().parent / "scenarios.json"


def _project(actual: Any, want: Any) -> Any:
    if isinstance(want, dict) and isinstance(actual, dict):
        return {k: _project(actual.get(k, "<absent>"), v) for k, v in want.items()}
    return actual


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True)


def check(expect: dict[str, Any], observed: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, want: Any, got: Any) -> None:
        checks.append(
            {"check": name, "expected": want, "observed": got, "ok": want == got}
        )

    for key in ("outcome", "illegal_at", "reason_code", "active_player"):
        if key in expect:
            add(key, expect[key], observed.get(key))
    by_label = {p["label"]: p for p in observed["players"]}
    for label, want in expect.get("players", {}).items():
        got = by_label[label]
        for key, val in want.items():
            if key == "battlefield":
                keys = sorted({k for e in val for k in e})
                exp = sorted(
                    _canon({k: e.get(k, "<absent>") for k in keys}) for e in val
                )
                have = sorted(
                    _canon({k: e.get(k, "<absent>") for k in keys})
                    for e in got["battlefield"]
                )
                add(f"{label}.battlefield", exp, have)
            elif key in ("life", "lost", "mana_pool", "commander_casts"):
                add(f"{label}.{key}", val, got[key])
            else:  # zones
                add(
                    f"{label}.{key}",
                    val if key == "library" else sorted(val),
                    got["zones"][key],
                )
    trace = observed["trace"]
    if expect.get("trace"):
        types = {e["event"] for e in expect["trace"]}
        filtered = [
            _project(e, w)
            for e, w in zip([t for t in trace if t["event"] in types], expect["trace"])
        ]
        count = sum(1 for t in trace if t["event"] in types)
        add("trace.count(" + ",".join(sorted(types)) + ")", len(expect["trace"]), count)
        add("trace.sequence", expect["trace"], filtered)
    for absent in expect.get("trace_absent", []):
        hits = [t for t in trace if _project(t, absent) == absent]
        add(f"trace_absent {_canon(absent)}", [], hits)
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=forge_adapter.ROOT / "forge-result.json"
    )
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--no-auth-gate", action="store_true")
    args = parser.parse_args(argv)

    spec = json.loads(SCENARIOS.read_text())
    items = [
        s
        for s in spec["scenarios"]
        if not args.only or s["request"]["scenario_id"] in args.only
    ]
    receipts = forge_adapter.run_batch(
        [s["request"] for s in items], require_auth=not args.no_auth_gate
    )
    results = []
    for item, receipt in zip(items, receipts):
        entry: dict[str, Any] = {
            "scenario_id": item["request"]["scenario_id"],
            "status": receipt["status"],
            "expectation_basis": item["expect"].get("basis"),
        }
        if receipt["status"] != "executed":
            entry["detail"] = {k: receipt.get(k) for k in ("reason", "problems")}
            bridge = receipt.get("bridge") or {}
            entry["detail"].update(
                {
                    k: bridge.get(k)
                    for k in ("error", "unknown_cards", "injection_mismatches")
                    if k in bridge
                }
            )
        if receipt["status"] == "executed":
            entry["checks"] = check(item["expect"], receipt["observed"])
            entry["agrees_with_oracle_expectation"] = all(
                c["ok"] for c in entry["checks"]
            )
        results.append(entry)
    out = {
        "schema": "forge-scenario-run.v1",
        "summary": {
            "scenarios": len(results),
            "executed": sum(r["status"] == "executed" for r in results),
            "agree": sum(
                bool(r.get("agrees_with_oracle_expectation")) for r in results
            ),
            "disagree": sum(
                r.get("agrees_with_oracle_expectation") is False for r in results
            ),
        },
        "results": results,
        "receipts": receipts,
    }
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps(out["summary"]))
    for r in results:
        bad = [c for c in r.get("checks", []) if not c["ok"]]
        print(
            r["scenario_id"],
            r["status"],
            "agree" if r.get("agrees_with_oracle_expectation") else bad or "",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
