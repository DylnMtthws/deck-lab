"""Run an independent authored challenge against a supplied pair evaluator.

The adapter takes (document, outgoing_card, incoming_card, constraints) and returns
{outcome, evidence, ...}. The corpus is a transparent rubric, not expert ratings.
Usage: python scripts/evaluate_swap_quality.py --adapter module:function
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "swap-quality"


def load_challenge():
    challenge = json.loads((ROOT / "challenge.json").read_text())
    decks = {
        name: json.loads((ROOT / f"{name}.json").read_text())
        for name in {c["deck"] for c in challenge["cases"]}
    }
    cards = {c["name"]: c for c in json.loads((ROOT / "catalog.json").read_text())}
    cards.update(
        {c["name"]: c for c in json.loads((ROOT / "challenge-cards.json").read_text())}
    )
    for doc in decks.values():
        cards.update({c["name"]: c for c in doc["entries"]})
    return challenge, decks, cards


def evaluate(adapter):
    challenge, decks, cards = load_challenge()
    rows = []
    for case in challenge["cases"]:
        doc = copy.deepcopy(decks[case["deck"]])
        baseline = copy.deepcopy(doc)
        outgoing = copy.deepcopy(
            cards.get(case["outgoing"], {"name": case["outgoing"]})
        )
        incoming = copy.deepcopy(
            cards.get(case["incoming"], {"name": case["incoming"]})
        )
        constraints = {
            k: case[k] for k in ("budget", "power_level", "theme") if k in case
        }
        result = adapter(doc, outgoing, incoming, constraints)
        outcome = result.get("outcome", result.get("status", "missing"))
        demonstrated = (
            result.get("demonstrated_upgrade") is True
            or outcome == "demonstrated_upgrade"
        )
        accepted = outcome in {"accepted", "quality_candidate"}
        has_evidence = bool(
            result.get("evidence") or result.get("reasons") or result.get("strengths")
        )
        passed = (
            outcome in case["acceptable_outcomes"]
            and not demonstrated
            and (not accepted or has_evidence)
            and doc == baseline
        )
        rows.append(
            {
                "id": case["id"],
                "passed": passed,
                "outcome": outcome,
                "acceptable_outcomes": case["acceptable_outcomes"],
                "false_demonstrated_upgrade": demonstrated,
                "evidence_present": has_evidence,
                "source_mutated": doc != baseline,
                "rubric": case["rubric"],
                "result": result,
            }
        )
    return {
        "version": challenge["version"],
        "validation": challenge["validation"],
        "cases": rows,
        "kpi": {
            "false_demonstrated_upgrade_labels": sum(
                r["false_demonstrated_upgrade"] for r in rows
            ),
            "cases_passed": sum(r["passed"] for r in rows),
            "cases_total": len(rows),
            "all_passed": all(r["passed"] for r in rows),
        },
    }


def functional_candidates_adapter(document, outgoing, incoming, constraints):
    """Expose the actual narrow functional matcher, without inventing rejection.

    Missing match is flagged as unestablished, not a judgment of bad card strength.
    This adapter cannot establish preference or budget compliance.
    """
    from sabermetrics.assistant_deck_intelligence import _features
    from sabermetrics.functional_candidates import functional_improvement

    evidence = functional_improvement(
        outgoing, incoming, _features(outgoing), _features(incoming)
    )
    if evidence:
        return {
            "outcome": "quality_candidate",
            "evidence": evidence,
            "demonstrated_upgrade": False,
            "unverified_constraints": constraints,
        }
    return {
        "outcome": "flagged",
        "evidence": [
            "The bounded equivalent-function matcher established no improvement."
        ],
        "demonstrated_upgrade": False,
        "unverified_constraints": constraints,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapter",
        required=True,
        help="Importable module:function returning outcome and evidence",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.adapter == "functional_candidates":
        adapter = functional_candidates_adapter
    else:
        module, name = args.adapter.rsplit(":", 1)
        adapter = getattr(importlib.import_module(module), name)
    report = evaluate(adapter)
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")
    raise SystemExit(0 if report["kpi"]["all_passed"] else 1)


if __name__ == "__main__":
    main()
