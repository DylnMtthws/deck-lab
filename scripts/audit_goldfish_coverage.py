"""Audit frozen fixture diagnostics without converting definitions into execution proof.

Usage: PYTHONPATH=src python scripts/audit_goldfish_coverage.py REPORT_DIR --output audit.json
REPORT_DIR contains <fixture-id>.json native envelopes or diagnostic reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ("supported", "partial", "unsupported", "inactive")


def audit_report(document: dict, evidence: dict | None = None, *, report_sha256: str | None = None) -> dict:
    report = document.get("diagnostics", document)
    coverage = report.get("coverage", {})
    detail = coverage.get("card_coverage", {})
    rows = detail.get("cards", [])
    if detail.get("version") != 1 or detail.get("unit") != "card_slots":
        raise ValueError("A version-one card-slot coverage audit is required")
    total = coverage.get("total_cards")
    if type(total) is not int or len(rows) != total:
        raise ValueError("Card audit must contain every simulator slot")
    if sorted(row["slot"] for row in rows) != list(range(total)):
        raise ValueError("Card audit slots must be unique and contiguous")
    counts = Counter(row["status"] for row in rows)
    if set(counts) - set(STATUSES):
        raise ValueError("Unknown card coverage status")
    exact_counts = {status: counts[status] for status in STATUSES}
    if exact_counts != detail.get("counts"):
        raise ValueError("Declared coverage counts disagree with card rows")
    definitions = Counter(effect for row in rows for effect in row.get("supported_effects", []))
    limitations = Counter(limit for row in rows for limit in row.get("limitations", []))
    unsupported = Counter(row["name"] for row in rows if row["status"] == "unsupported")
    gate_failures = []
    effective_counts = dict(exact_counts)
    sources = evidence.get("sources", []) if isinstance(evidence, dict) else []
    bound = bool(sources) and isinstance(report_sha256, str) and len(report_sha256) == 64 and evidence.get("report_sha256") == report_sha256
    for source in sources:
        path = Path(source.get("path", ""))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.get("sha256"):
            bound = False
    for row in rows:
        if row["status"] != "supported":
            continue
        claim = evidence.get("claims", {}).get(str(row["slot"]), {}) if isinstance(evidence, dict) else {}
        kinds = set()
        for test in claim.get("tests", []):
            path = Path(test.get("evidence_path", ""))
            if (test.get("outcome") == "passed" and path.is_file()
                and hashlib.sha256(path.read_bytes()).hexdigest() == test.get("sha256")):
                kinds.add(test.get("kind"))
        if not bound or not {"positive", "negative_or_interaction"}.issubset(kinds):
            gate_failures.append({"slot": row["slot"], "name": row["name"],
                "reason": "Fully supported claim lacks report/source hash binding and passing positive plus negative/interaction evidence."})
            effective_counts["supported"] -= 1
            effective_counts["partial"] += 1
    scenarios = {}
    failed_slots = {failure["slot"] for failure in gate_failures}
    for name, test in sorted(report.get("tests", {}).items()):
        samples = test.get("samples", [])
        scenarios[name] = {
            "status": test.get("status"),
            "metrics": {key: {"measured": value.get("value") is not None,
                              "value": value.get("value"),
                              "unit": value.get("unit"),
                              "numerator": value.get("numerator"),
                              "denominator": value.get("denominator")}
                        for key, value in sorted(test.get("metrics", {}).items())},
            "sample_count": len(samples),
            "sampled_turn_rows": sum(len(sample.get("turns", [])) for sample in samples),
            "sampled_disruptions_exercised": sum(sample.get("disruption_exercised") is True for sample in samples),
            "sampled_recoveries_observed": sum(sample.get("recovery_observed") is True for sample in samples),
            "sampled_finish_witnesses": sum(bool(sample.get("finish_witness")) for sample in samples),
            "trace_samples": samples,
            "effect_execution_proof": "unavailable: these samples do not identify every executed ability",
            "limitations": test.get("limitations", []),
        }
    return {
        "total_card_slots": total,
        "native_settings": report.get("settings", {}),
        "card_status_counts": exact_counts,
        "effective_status_counts": effective_counts,
        "support_release_gate": {"passed": not gate_failures, "failures": gate_failures,
            "scope": "Frozen evidence integrity and required regression categories; passing is not independent proof of all Magic rules."},
        "executable_definition_slot_counts": dict(sorted(definitions.items())),
        "remaining_limitation_slot_counts": dict(sorted(limitations.items())),
        "unsupported_card_slot_counts": dict(sorted(unsupported.items())),
        "unsupported_categories": {"no_executable_definition": counts["unsupported"]},
        "priority": [
            {"priority": 1, "scope": "unsupported card slots", "slot_count": counts["unsupported"],
             "reason": "No executable definition; investigate deck engine dependencies before implementation."},
            {"priority": 2, "scope": "partial card slots", "slot_count": counts["partial"],
             "reason": "Audit remaining limitations and scenario dependencies; definitions alone do not certify complete abilities."},
            {"priority": 3, "scope": "inactive card slots", "slot_count": counts["inactive"],
             "reason": "Revisit when adding opponent, combat or interaction scenarios."},
        ],
        "coverage_gap_queue": [
            {"priority": 1 if row["status"] == "unsupported" else 2 if row["status"] == "partial" or row["slot"] in failed_slots else 3,
             "kind": "engine_coverage", "slot": row["slot"], "name": row["name"],
             "status": "partial" if row["slot"] in failed_slots else row["status"],
             "limitations": row.get("limitations", []), "support_evidence_missing": row["slot"] in failed_slots}
            for row in sorted(rows, key=lambda row: ({"unsupported": 1, "partial": 2, "inactive": 3, "supported": 4}[row["status"]], row["slot"]))
            if row["status"] != "supported" or row["slot"] in failed_slots
        ] + [{"priority": 2, "kind": "pilot_policy", "scope": "opening",
              "slot_count": None,
              "reason": "Bounded visible-hand keep policy is not an optimal strategy pilot; profile features do not establish complete commander strategy. This issue is independent of per-card support.",
              "native_opening_limitations": report.get("tests", {}).get("opening", {}).get("limitations", [])}],
        "cards": rows,
        "scenarios": scenarios,
        "ability_inventory": {
            "status": "unavailable",
            "reason": "Card-slot executable labels are not a complete Oracle ability inventory. No semantic ability count is inferred.",
        },
    }


def audit_fixtures(report_dir: Path, manifest_path: Path, evidence_dir: Path | None = None,
                   oracle_dir: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text())
    decks = {}
    for fixture in manifest["decks"]:
        name = fixture["id"]
        path = report_dir / f"{name}.json"
        raw = path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        evidence_path = evidence_dir / f"{name}.json" if evidence_dir else None
        evidence = json.loads(evidence_path.read_text()) if evidence_path and evidence_path.is_file() else None
        audit = audit_report(json.loads(raw), evidence, report_sha256=source_hash)
        expected = fixture["mainboard_count"] + fixture["commander_count"]
        if audit["total_card_slots"] != expected:
            raise ValueError(f"{name}: expected {expected} playable card slots")
        audit["source_sha256"] = source_hash
        audit["excluded_sideboard_slots"] = fixture["sideboard_count"]
        oracle_path = oracle_dir / f"{name}-document.json" if oracle_dir else None
        if oracle_path and oracle_path.is_file():
            oracle_raw = oracle_path.read_bytes()
            oracle_document = json.loads(oracle_raw)
            entries = {entry["name"]: entry for entry in oracle_document.get("entries", [])
                       if entry.get("zone_name", "Unsorted") == "Unsorted"}
            # Native lists use the printed front name for split/MDFC cards.
            # Only accept an unambiguous exact front-name match.
            fronts = {}
            for full_name, entry in entries.items():
                front = full_name.split(" // ", 1)[0]
                fronts.setdefault(front, []).append(entry)
            inventory, missing = [], []
            for row in audit["cards"]:
                entry = entries.get(row["name"])
                if entry is None and len(fronts.get(row["name"], [])) == 1:
                    entry = fronts[row["name"]][0]
                if entry is None:
                    missing.append({"slot": row["slot"], "name": row["name"]})
                    continue
                clauses = [line.strip() for line in entry.get("oracle_text", "").splitlines() if line.strip()]
                inventory.append({"slot": row["slot"], "name": row["name"],
                    "oracle_id": entry.get("oracle_id"), "oracle_clauses": [
                        {"index": index, "text": clause, "mapping_status": "unverified"}
                        for index, clause in enumerate(clauses)],
                    "executable_labels": row.get("supported_effects", [])})
            audit["ability_inventory"] = {
                "status": "raw_oracle_clauses_with_unverified_mapping",
                "unit": "nonempty_oracle_text_lines_per_card_slot",
                "reason": "Raw clauses may contain multiple abilities, reminder text or coupled effects. No supported ability count is inferred from paragraphs or executable labels.",
                "source_sha256": hashlib.sha256(oracle_raw).hexdigest(),
                "slots_with_oracle_record": len(inventory),
                "clause_count": sum(len(row["oracle_clauses"]) for row in inventory),
                "verified_mapping_count": 0,
                "missing_oracle_slots": missing, "cards": inventory,
            }
        decks[name] = audit
    return {"schema_version": "decklab.coverage-audit.v1",
            "scope": "Frozen native definitions, scenario measurements and bounded trace samples; no full-rules or ability completeness claim.",
            "decks": decks,
            "totals": {status: sum(deck["card_status_counts"][status] for deck in decks.values()) for status in STATUSES}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "fixtures/goldfish/manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--oracle-dir", type=Path)
    parser.add_argument("--baseline-audit", type=Path)
    args = parser.parse_args()
    result = audit_fixtures(args.report_dir, args.manifest, args.evidence_dir, args.oracle_dir)
    if args.baseline_audit:
        baseline = json.loads(args.baseline_audit.read_text())
        if baseline.get("schema_version") != result["schema_version"] or set(baseline["decks"]) != set(result["decks"]):
            raise ValueError("Baseline must audit the same fixture set and schema")
        changes = {}
        for name, current in result["decks"].items():
            previous = baseline["decks"][name]
            old_rows = {row["slot"]: row for row in previous["cards"]}
            changes[name] = {
                "before_source_sha256": previous["source_sha256"],
                "after_source_sha256": current["source_sha256"],
                "status_count_deltas": {status: current["card_status_counts"][status] - previous["card_status_counts"][status] for status in STATUSES},
                "changed_card_slots": [{"slot": row["slot"], "before_name": old_rows.get(row["slot"], {}).get("name"),
                    "after_name": row["name"], "before_status": old_rows.get(row["slot"], {}).get("status"), "after_status": row["status"]}
                    for row in current["cards"] if old_rows.get(row["slot"], {}).get("status") != row["status"] or old_rows.get(row["slot"], {}).get("name") != row["name"]],
            }
        result["baseline_inventory_comparison"] = {
            "scope": "Definition coverage inventory changes only; no metric performance improvement, complete ability support or winner is inferred.",
            "decks": changes,
            "total_status_count_deltas": {status: result["totals"][status] - baseline["totals"][status] for status in STATUSES},
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["totals"], sort_keys=True))
    if any(not deck["support_release_gate"]["passed"] for deck in result["decks"].values()):
        raise SystemExit("Support release gate failed; see artifact failures and effective partial counts.")


if __name__ == "__main__":
    main()
