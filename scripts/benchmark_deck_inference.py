#!/usr/bin/env python3
"""Offline development probes: no provider calls or recommendation-quality claim.

PYTHONPATH=src python scripts/benchmark_deck_inference.py --output /tmp/report.json
Optional --input JSON expects {cards, rules, request, context}; it measures actual
wire construction using mocked semantic failures, never real model performance.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sabermetrics.assistant_deck_inference import clear_compilation_cache, research_deck
from sabermetrics.assistant_inference_facts import compile_deck_facts
from sabermetrics.assistant_inference_search import discover_connections
from sabermetrics.cedh.errors import ModelProviderError


def _card(identity, name, text):
    return {
        "oracle_id": identity,
        "id": identity,
        "name": name,
        "type_line": "Creature — Pirate",
        "mana_cost": "{R}",
        "oracle_text": text,
    }


def development_cases():
    cards = [
        _card(
            "outlet",
            "Copper Sail",
            "Discard a card: This creature gains flying until end of turn.",
        ),
        _card(
            "payoff",
            "Amber Spark",
            "Whenever you discard a card, this creature deals 1 damage to each opponent.",
        ),
        _card(
            "conversion",
            "Violet Navigator",
            "Whenever one or more Pirates you control deal damage to a player, Violet Navigator explores.",
        ),
    ]
    rule = {
        "kind": "rule",
        "source": "Explicitly synthetic development source",
        "source_id": "development-explore",
        "version": "development-v1",
        "complete": True,
        "text": "Certain spells and abilities instruct a permanent to explore. To do so, that permanent’s controller reveals the top card of their library. If a land card is revealed this way, that player puts that card into their hand. Otherwise, that player puts a +1/+1 counter on the exploring permanent and may put the revealed card into their graveyard.",
    }
    return [
        {
            "id": "renamed_conditional_renewal",
            "cards": cards,
            "rules": {"sources": [rule]},
            "expect_cycle": True,
        },
        {
            "id": "combat_filter_counterexample",
            "cards": [
                cards[1],
                _card(
                    "combat",
                    "Lapis Signal",
                    "Whenever one or more Pirates you control deal combat damage to a player, draw a card.",
                ),
            ],
            "rules": {},
            "expect_cycle": False,
            "expect_no_connections": True,
        },
        {
            "id": "trailing_restriction",
            "cards": [
                _card(
                    "limited",
                    "Silver Scope",
                    "Discard a card: Draw a card.\n Activate only once each turn.",
                )
            ],
            "rules": {},
            "expect_cycle": False,
            "expect_facts": 0,
        },
        {
            "id": "unknown_progression_stays_raw",
            "cards": [
                _card("progression", "New Progression", "LEVEL 1-4\n{T}: Add {G}.")
            ],
            "rules": {},
            "expect_cycle": False,
            "expect_facts": 0,
        },
    ]


class OfflineCapture:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        raise ModelProviderError(
            "Deliberate offline semantic abstention; no paid call."
        )


def run_case(case):
    begin = time.perf_counter()
    bundle = compile_deck_facts(case["cards"], case.get("rules", {}))
    search = discover_connections(bundle)
    cycle = any(c["cycle_detected"] for c in search["candidates"])
    checks = {
        "all_candidates_unverified": all(
            c["verified"] is False and c["status"] == "hypothesis"
            for c in search["candidates"]
        ),
        "no_complete_search_claim": search["coverage"]["scope_complete"] is False,
    }
    if "expect_cycle" in case:
        checks["expected_conditional_cycle_presence"] = cycle == case["expect_cycle"]
    if case.get("expect_no_connections"):
        checks["explicit_mismatch_rejected"] = not search["candidates"]
    if "expect_facts" in case:
        checks["expected_abstention"] = len(bundle["facts"]) == case["expect_facts"]
    return {
        "id": case["id"],
        "checks": checks,
        "passed": all(checks.values()),
        "facts": len(bundle["facts"]),
        "candidates": len(search["candidates"]),
        "cpu_ms": round((time.perf_counter() - begin) * 1000, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "schema": "deck-inference-development-benchmark.v1",
        "network_calls": 0,
        "independent_expert_labels": False,
        "recommendation_quality_measured": False,
        "provider_latency_measured": False,
        "cases": [run_case(c) for c in development_cases()],
    }
    if args.input:
        captured = json.loads(args.input.read_text())
        clear_compilation_cache()
        runs = []
        for _ in range(2):
            gateway = OfflineCapture()
            _, receipt = research_deck(
                gateway,
                captured["cards"],
                captured.get("rules", {}),
                captured["request"],
                context=captured.get("context"),
            )
            runs.append(
                {
                    "compilation_cache_hit": receipt["compilation_cache_hit"],
                    "preparation_seconds": receipt["preparation_seconds"],
                    "payload_utf8_bytes": receipt.get("payload_utf8_bytes", {}),
                    "constructed_requests": len(gateway.requests),
                    "limitations": receipt["limitations"],
                }
            )
        report["captured_input_offline_runs"] = runs
    report["passed"] = all(c["passed"] for c in report["cases"])
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
