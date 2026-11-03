#!/usr/bin/env python3
"""Offline, mechanic-stratified extraction benchmark for the literal compiler.

Runs the existing ``compile_deck_facts`` and ``discover_connections`` unchanged
over AI-authored DEVELOPMENT fixtures and scores per-dimension precision and
represented-clause coverage. There are no model, provider or network calls and
no semantic scorer: every comparison is exact equality over canonical tokens
derived from existing compiler fields.

PYTHONPATH=src TZ=UTC python scripts/benchmark_inference_extraction.py \
    [--cases PATH] [--output PATH | --check PATH]

Scoring contract (documented again in the report's ``scoring`` block):

* A label binds one exact source span: record SHA-256 (the compiler's canonical
  JSON hash), oracle-text SHA-256, offsets and quote. Any mismatch, malformed
  label, unknown dimension or uncovered source text is a strict error.
* Only an interpreted compiler fact produces an extracted value. Abstention
  (uninterpreted/withheld) is ``unknown``: never TP, never FP.
* TP = extracted equals gold. Wrong value = FP and FN. Unknown counts in the
  coverage denominator only. precision = TP/(TP+FP), coverage = TP/gold; an
  empty denominator yields ``null``, never a fabricated 1.0.
* Every dimension an interpreted fact emits is either scored against gold or
  reported as ``unjudged`` (gold explicitly ``null``, or no gold key). An
  unjudged prediction is never counted TP and blocks
  ``all_scored_dimensions_correct``. Fact fields no dimension reads are listed
  per label as ``unjudged_fact_fields`` so nothing the compiler emits is
  silently ignored.
* ``all_scored_dimensions_correct`` is a PARTIAL verdict: every scored
  dimension matched and none was unjudged. It is not full clause semantic
  correctness; unread fact fields and condition prose are never judged
  (``unread_fact_content`` flags them) and nothing in this report claims a
  clause is semantically correct as a whole.
* Labels whose clause the compiler does not represent remain in every
  denominator. A clause whose text is missing from compiler output is
  ``dropped`` and reported as a defect.

Fixtures are AI-authored development diagnostics, not independent or expert
labels, and the report measures no live inference quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sabermetrics.assistant_inference_facts import VERSION as COMPILER_VERSION
from sabermetrics.assistant_inference_facts import compile_deck_facts
from sabermetrics.assistant_inference_search import VERSION as SEARCH_VERSION
from sabermetrics.assistant_inference_search import discover_connections

SCHEMA = "inference-extraction-benchmark.v2"
SCORING_VERSION = 2
CASES_SCHEMA = "inference-extraction-development-cases.v2"
EVAL_DIR = Path("docs/evaluations/research-viability-20261103")
DEFAULT_CASES = EVAL_DIR / "extraction-development-cases.json"
DEFAULT_RESULTS = EVAL_DIR / "extraction-results.json"
FUEL_CAVEAT = "Finite fuel (hand, library, mana, tap capacity) unverified"

DIMENSIONS = (
    "scope",
    "trigger",
    "actor",
    "recipient",
    "combat",
    "multiplicity",
    "quantity",
    "costs",
    "restrictions",
    "zone_moves",
    "output_events",
    "finite_fuel",
)
LIST_DIMENSIONS = set(DIMENSIONS) - {"scope", "finite_fuel"}
V1_EXPECTATIONS = {"interpret", "abstain"}
LABEL_FIELDS = {
    "label_id",
    "source_ref",
    "record_sha256",
    "text_sha256",
    "start",
    "end",
    "quote",
    "mechanisms",
    "v1_expectation",
    "gold",
    "note",
}
JOIN_KINDS = {"event", "resource"}


# v1 totals are kept for audit only. They are NOT comparable with v2 and are
# not a valid complete precision claim: v1 never compared output events and
# ignored extracted dimensions that had no gold key.
SCORING_HISTORY = [
    {
        "scoring_version": 1,
        "status": "superseded_invalid_as_complete_precision_claim",
        "report_sha256": "5d68b8414709364f67219a4fce0336079931dc9528dbb3717459058440e84616",
        "cases_sha256": "1935d804bfc65187a2546c75c019c16579124afa6c0a02c49c86a8850e032d9c",
        "compiler_version": "assistant-inference-facts-v1",
        "search_version": "assistant-inference-search.v1",
        "totals": {
            "labels": 40,
            "gold_values": 245,
            "tp": 139,
            "fp": 0,
            "unknown": 106,
            "precision": 1.0,
            "coverage": 0.5673,
            "interpreted": 18,
            "fully_correct": 16,
        },
        "field_correction": "Raw v1 'fully_correct' is kept verbatim as a historical claim. It meant only that every supplied gold key matched; it never judged unread fact fields, so it overstated scope. v2 renames the verdict all_scored_dimensions_correct and does not claim full clause semantic correctness.",
        "blindspot": "Alternate-provider review R1 replaced both compiled explore outputs with an invented event; every v1 total, dimension, mechanism score and clause verdict was unchanged. v1 had no output-event dimension and only iterated supplied gold keys.",
    }
]
SCORING_CHANGE = "v2 adds the output_events dimension, scores every emitted dimension or reports it unjudged, adds output_events gold to all labels and revises 8 label values (see the cases file label_revisions). The compiler and search are unchanged, so every v1->v2 difference is a scoring/label change, not a compiler change."


class BenchmarkError(ValueError):
    """Malformed fixtures or source bindings; the benchmark refuses to score."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def canonical(value: Any) -> str:
    """Same canonical JSON as the compiler's source hash."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_ref(kind: str, record: dict) -> str:
    if kind == "card":
        return "card:" + str(
            record.get("oracle_id") or record.get("card_id") or record.get("id")
        )
    return "rule:" + str(record.get("source_id") or record.get("id"))


def record_text(record: dict) -> Any:
    return record.get("oracle_text", record.get("text", ""))


# --------------------------------------------------------------------------
# Fact -> dimension tokens. None means the fact does not represent the
# dimension (counted unknown); a list/bool is the compiler's claim.
# --------------------------------------------------------------------------


def _sorted_unique(values) -> list:
    return sorted({v for v in values})


def extract_dimensions(fact: dict, kind: str) -> dict[str, Any]:
    inputs, outputs = fact["inputs"], fact["outputs"]
    events = inputs + outputs
    combat = [
        "trigger:combat_only" if row.get("combat_only") else "trigger:any_damage"
        for row in inputs
        if row.get("event") == "damage" and "combat_only" in row
    ] + [
        "output:combat" if row["combat"] else "output:noncombat"
        for row in outputs
        if row.get("event") == "damage" and "combat" in row
    ]
    multiplicity = [c for c in fact["conditions"] if c.startswith("Once per")]
    recipients = _sorted_unique(r["recipient"] for r in outputs if "recipient" in r)
    fuel = any(
        "no renewable fuel" in c or "finite supplied fuel" in c
        for c in fact["conditions"]
    )
    return {
        # Card facts claim a printed, unconditional ability of this kind.
        "scope": fact["kind"],
        "trigger": (
            _sorted_unique(f"{r.get('event')}:{r.get('subject')}" for r in inputs)
            if fact["kind"] == "triggered" and inputs
            else None
        ),
        "actor": _sorted_unique(str(r.get("actor")) for r in outputs),
        "recipient": recipients or None,
        "combat": _sorted_unique(combat) or None,
        "multiplicity": multiplicity or None,
        "quantity": _sorted_unique(
            f"{r['event']}:{r['quantity']}" for r in events if "quantity" in r
        ),
        "costs": [{k: c[k] for k in sorted(c)} for c in fact["costs"]],
        # The compiler has no restriction field; an interpreted fact claims a
        # full-scope match, so silently dropped restrictions score as FP.
        "restrictions": [],
        "zone_moves": _sorted_unique(
            f"{r['event']}:{r['from_zone']}->{r['to_zone']}"
            for r in events
            if "from_zone" in r and "to_zone" in r
        ),
        # An interpreted fact claims its complete effect list, so a missing,
        # extra or renamed output event is wrong, not unknown.
        "output_events": _sorted_unique(
            f"{r.get('event')}:{r.get('subject')}" for r in outputs
        ),
        "finite_fuel": True if fuel else None,
    }


# Row fields each dimension reads. Anything else an interpreted fact emits is
# disclosed per label as unjudged rather than silently ignored.
_READ_ROW_FIELDS = {
    "inputs": {"quantity", "from_zone", "to_zone", "combat_only"},
    "outputs": {
        "event",
        "subject",
        "actor",
        "recipient",
        "quantity",
        "from_zone",
        "to_zone",
        "combat",
    },
}
_READ_FACT_FIELDS = {"kind", "costs", "inputs", "outputs", "conditions"}
# Provenance and binding fields: checked by the source binding, not scored.
_BINDING_FACT_FIELDS = {
    "fact_id",
    "source_id",
    "source_sha256",
    "start",
    "end",
    "quote",
}


def unjudged_fact_fields(fact: dict) -> dict[str, list[str]]:
    """Fact content no scored dimension reads (disclosed, never scored)."""
    fields = set()
    for side, read in _READ_ROW_FIELDS.items():
        read = set(read)
        if side == "inputs" and fact["kind"] == "triggered":
            read |= {"event", "subject"}
        for row in fact[side]:
            fields |= {f"{side}[].{k}" for k in row if k not in read}
    fields |= {k for k in fact if k not in _READ_FACT_FIELDS | _BINDING_FACT_FIELDS}
    conditions = [
        c
        for c in fact["conditions"]
        if not c.startswith("Once per")
        and "no renewable fuel" not in c
        and "finite supplied fuel" not in c
    ]
    return {"fields": sorted(fields), "conditions": conditions}


def _normalize_gold(dimension: str, value: Any) -> Any:
    if value is None:
        return None
    if dimension == "costs":
        return [{k: c[k] for k in sorted(c)} for c in value]
    if dimension in LIST_DIMENSIONS and dimension != "multiplicity":
        return sorted(value, key=canonical)
    return value


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _validate_gold(prefix: str, gold: Any, errors: list[str]) -> None:
    if not isinstance(gold, dict) or not gold:
        errors.append(f"{prefix}: gold must be a non-empty object")
        return
    for dimension, value in gold.items():
        if dimension not in DIMENSIONS:
            errors.append(f"{prefix}: unknown dimension {dimension!r}")
        elif value is None and dimension != "scope":
            continue  # explicitly unjudged by the label author
        elif dimension == "scope" and not (isinstance(value, str) and value):
            errors.append(f"{prefix}: scope must be a non-empty string")
        elif dimension == "finite_fuel" and value is not True:
            errors.append(
                f"{prefix}: finite_fuel may only be labelled true (absence unscored)"
            )
        elif dimension == "costs":
            if not isinstance(value, list) or not all(
                isinstance(c, dict) and isinstance(c.get("kind"), str) for c in value
            ):
                errors.append(f"{prefix}: costs must be a list of objects with kind")
        elif dimension in LIST_DIMENSIONS and not (
            isinstance(value, list) and all(isinstance(v, str) and v for v in value)
        ):
            errors.append(f"{prefix}: {dimension} must be a list of strings")


def validate_cases(document: Any) -> list[str]:
    """Return every strict fixture error; empty means scorable."""
    errors: list[str] = []
    if not isinstance(document, dict) or document.get("schema") != CASES_SCHEMA:
        return [f"cases schema must be {CASES_SCHEMA}"]
    authorship = document.get("authorship")
    if (
        not isinstance(authorship, dict)
        or authorship.get("independent") is not False
        or authorship.get("expert_reviewed") is not False
        or authorship.get("split") != "development"
    ):
        errors.append(
            "authorship must declare independent=false, expert_reviewed=false, split=development"
        )
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["cases must be a non-empty list"]
    case_ids: set[str] = set()
    label_ids: set[str] = set()
    for case in cases:
        cid = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(cid, str) or cid in case_ids:
            errors.append(f"invalid or duplicate case_id {cid!r}")
            continue
        case_ids.add(cid)
        records: dict[tuple[str, str], tuple[str, dict]] = {}
        refs: dict[str, list[str]] = defaultdict(list)
        for kind, key in (("card", "cards"), ("rule", "rules")):
            values = case.get(key, [])
            if not isinstance(values, list):
                errors.append(f"{cid}: {key} must be a list")
                continue
            for record in values:
                if not isinstance(record, dict) or not isinstance(
                    record_text(record), str
                ):
                    errors.append(f"{cid}: {key} record needs string oracle text")
                    continue
                ref, digest = record_ref(kind, record), sha256(canonical(record))
                records[(ref, digest)] = (kind, record)
                refs[ref].append(digest)
        covered: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
        labels = case.get("labels")
        if not isinstance(labels, list) or not labels:
            errors.append(f"{cid}: labels must be a non-empty list")
            labels = []
        for label in labels:
            lid = label.get("label_id") if isinstance(label, dict) else None
            prefix = f"{cid}/{lid}"
            if not isinstance(lid, str) or lid in label_ids:
                errors.append(f"{prefix}: invalid or duplicate label_id")
                continue
            label_ids.add(lid)
            extra = set(label) - LABEL_FIELDS
            missing = LABEL_FIELDS - {"note"} - set(label)
            if extra or missing:
                errors.append(
                    f"{prefix}: fields extra={sorted(extra)} missing={sorted(missing)}"
                )
                continue
            bad_types = [
                name
                for name, kinds in (
                    ("source_ref", str),
                    ("record_sha256", str),
                    ("text_sha256", str),
                    ("quote", str),
                    ("v1_expectation", str),
                    ("mechanisms", list),
                    ("gold", dict),
                )
                if not isinstance(label[name], kinds)
            ]
            if bad_types:
                errors.append(f"{prefix}: wrong field types {bad_types}")
                continue
            key = (label["source_ref"], label["record_sha256"])
            if key not in records:
                errors.append(
                    f"{prefix}: source mismatch, no record {label['source_ref']} with "
                    f"sha256 {label['record_sha256'][:12]} (supplied: "
                    f"{[d[:12] for d in refs.get(label['source_ref'], [])]})"
                )
                continue
            text = record_text(records[key][1])
            if label["text_sha256"] != sha256(text):
                errors.append(f"{prefix}: oracle text sha256 mismatch")
            start, end = label["start"], label["end"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(text)
                or text[start:end] != label["quote"]
            ):
                errors.append(f"{prefix}: quote does not equal source text[start:end]")
                continue
            if label["v1_expectation"] not in V1_EXPECTATIONS:
                errors.append(f"{prefix}: v1_expectation must be interpret|abstain")
            if not (
                isinstance(label["mechanisms"], list)
                and label["mechanisms"]
                and all(isinstance(m, str) and m for m in label["mechanisms"])
            ):
                errors.append(f"{prefix}: mechanisms must be non-empty strings")
            _validate_gold(prefix, label["gold"], errors)
            covered[key].append((start, end))
        # Every non-whitespace character of every record is labelled exactly
        # once, so no unsupported clause can disappear from the denominator.
        for key, (kind, record) in records.items():
            text = record_text(record)
            mask = [0] * len(text)
            for start, end in covered.get(key, []):
                for i in range(start, end):
                    mask[i] += 1
            if any(m > 1 for m in mask):
                errors.append(f"{cid}: overlapping labels on {key[0]}")
            if any(m == 0 and not ch.isspace() for m, ch in zip(mask, text)):
                errors.append(f"{cid}: unlabelled source text on {key[0]}")
        if case.get("connections") is not None and not isinstance(
            case["connections"], dict
        ):
            errors.append(f"{cid}: connections must be an object")
            continue
        connections = case.get("connections")
        if connections is not None:
            gaps = connections.get("dependency_gaps", [])
            if not isinstance(gaps, list) or not all(isinstance(g, str) for g in gaps):
                errors.append(f"{cid}: dependency_gaps must be a list of strings")
            for group in ("expected", "forbidden"):
                rows = connections.get(group, [])
                if not isinstance(rows, list):
                    errors.append(f"{cid}: {group} connections must be a list")
                    continue
                for row in rows:
                    # Type-check every endpoint before any hash membership
                    # test: an unhashable list or dict must be a strict
                    # BenchmarkError, never a raw TypeError.
                    if (
                        not isinstance(row, dict)
                        or not all(
                            isinstance(row.get(k), str)
                            for k in ("from", "to", "join_kind")
                        )
                        or row["from"] not in refs
                        or row["to"] not in refs
                        or row["join_kind"] not in JOIN_KINDS
                    ):
                        errors.append(f"{cid}: malformed {group} connection {row!r}")
            if not isinstance(connections.get("expect_cycle", False), bool):
                errors.append(f"{cid}: expect_cycle must be boolean")
    return errors


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _empty_counts() -> dict[str, int]:
    return {"gold": 0, "tp": 0, "fp": 0, "fn": 0, "unknown": 0}


def _finish(counts: dict[str, int]) -> dict[str, Any]:
    predicted = counts["tp"] + counts["fp"]
    return {
        **counts,
        "precision": _ratio(counts["tp"], predicted),
        "precision_denominator": predicted,
        "coverage": _ratio(counts["tp"], counts["gold"]),
        "coverage_denominator": counts["gold"],
    }


def run_case(case: dict) -> dict[str, Any]:
    cards = case.get("cards", [])
    rules = case.get("rules", [])
    bundle = compile_deck_facts(cards, {"sources": rules} if rules else None)
    search = discover_connections(bundle)
    by_sha = defaultdict(list)
    for source in bundle["sources"]:
        by_sha[source["source_sha256"]].append(source)
    facts = {f["fact_id"]: f for f in bundle["facts"]}
    kinds = {
        sha256(canonical(r)): k
        for k, values in (("card", cards), ("rule", rules))
        for r in values
    }
    labels_out = []
    matched_clauses: set[str] = set()
    errors: list[str] = []
    for label in case["labels"]:
        sources = by_sha.get(label["record_sha256"], [])
        row: dict[str, Any] = {
            "label_id": label["label_id"],
            "source_ref": label["source_ref"],
            "mechanisms": label["mechanisms"],
            "v1_expectation": label["v1_expectation"],
            "dimensions": {},
        }
        if len(sources) != 1:
            errors.append(
                f"{case['case_id']}/{label['label_id']}: compiler source binding "
                f"count {len(sources)} for record sha256"
            )
            continue
        source = sources[0]
        clause = next(
            (
                c
                for c in source["clauses"]
                if c["start"] == label["start"] and c["end"] == label["end"]
            ),
            None,
        )
        retained = (
            source["oracle_text"][label["start"] : label["end"]] == label["quote"]
        )
        fact = None
        if clause is None:
            row["clause_status"] = "segmentation_mismatch" if retained else "dropped"
            row["compiler_issues"] = []
        else:
            matched_clauses.add(clause["clause_id"])
            if clause["text"] != label["quote"] or not retained:
                row["clause_status"] = "dropped"
            elif clause["parse_status"] == "interpreted":
                row["clause_status"] = "interpreted"
                fact = facts[clause["fact_ids"][0]]
            else:
                row["clause_status"] = "preserved_unknown"
            row["compiler_issues"] = clause["issues"]
        extracted = (
            extract_dimensions(fact, kinds[label["record_sha256"]]) if fact else {}
        )
        row["unjudged"] = {}
        for dimension, gold in sorted(label["gold"].items()):
            gold = _normalize_gold(dimension, gold)
            value = extracted.get(dimension)
            if gold is None:
                # Explicitly unjudged gold: disclosed, never scored.
                if value is not None:
                    row["unjudged"][dimension] = {
                        "reason": "gold_explicitly_null",
                        "extracted": value,
                    }
                continue
            if fact is None or value is None:
                outcome = "unknown"
            elif value == gold:
                outcome = "tp"
            else:
                outcome = "wrong"
            row["dimensions"][dimension] = {
                "outcome": outcome,
                "gold": gold,
                "extracted": value,
            }
        for dimension in DIMENSIONS:
            value = extracted.get(dimension)
            if value is not None and dimension not in label["gold"]:
                row["unjudged"][dimension] = {
                    "reason": "no_gold_label",
                    "extracted": value,
                }
        row["unjudged_fact_fields"] = unjudged_fact_fields(fact) if fact else None
        row["unread_fact_content"] = (
            bool(
                row["unjudged_fact_fields"]["fields"]
                or row["unjudged_fact_fields"]["conditions"]
            )
            if fact
            else None
        )
        # Partial verdict only: every emitted dimension was scored and matched
        # and none was unjudged. Unread fact fields are never judged, so this is
        # not full semantic correctness of the clause.
        row["all_scored_dimensions_correct"] = (
            bool(row["dimensions"])
            and not row["unjudged"]
            and all(d["outcome"] == "tp" for d in row["dimensions"].values())
        )
        labels_out.append(row)
    unlabeled = [
        {
            "source_id": s["source_id"],
            "clause_id": c["clause_id"],
            "parse_status": c["parse_status"],
            "fact_ids": c["fact_ids"],
        }
        for s in bundle["sources"]
        for c in s["clauses"]
        if c["clause_id"] not in matched_clauses and c["text"].strip()
    ]
    return {
        "case_id": case["case_id"],
        "labels": labels_out,
        "unlabeled_compiler_clauses": unlabeled,
        "compiler_coverage": {
            k: bundle["coverage"][k]
            for k in (
                "complete",
                "clauses",
                "interpreted",
                "uninterpreted",
                "withheld",
                "facts",
                "unresolved_rules",
            )
        },
        "search": _score_search(case, bundle, search),
        "binding_errors": errors,
    }


def _score_search(case: dict, bundle: dict, search: dict) -> dict[str, Any] | None:
    spec = case.get("connections")
    if spec is None:
        return None
    sid_to_ref = {}
    for kind, key in (("card", "cards"), ("rule", "rules")):
        for record in case.get(key, []):
            digest = sha256(canonical(record))
            for source in bundle["sources"]:
                if source["source_sha256"] == digest:
                    sid_to_ref[source["source_id"]] = record_ref(kind, record)
    fact_ref = {f["fact_id"]: sid_to_ref.get(f["source_id"]) for f in bundle["facts"]}
    found = {
        (
            fact_ref[link["from_fact_id"]],
            fact_ref[link["to_fact_id"]],
            link["join_kind"],
        )
        for candidate in search["candidates"]
        for link in candidate["links"]
    }
    expected = {(r["from"], r["to"], r["join_kind"]) for r in spec.get("expected", [])}
    forbidden = {
        (r["from"], r["to"], r["join_kind"]) for r in spec.get("forbidden", [])
    }
    cycles = [c for c in search["candidates"] if c["cycle_detected"]]
    gaps = sorted({g["required_rule"] for g in search["dependency_gaps"]})
    return {
        "links": {
            "tp": len(expected & found),
            "fn": len(expected - found),
            "fp": len(forbidden & found),
            "expected_denominator": len(expected),
            "forbidden_denominator": len(forbidden),
            "unlabeled_found": len(found - expected - forbidden),
            "missing": sorted(map(list, expected - found)),
            "forbidden_found": sorted(map(list, forbidden & found)),
        },
        "cycle_expected": spec.get("expect_cycle", False),
        "cycle_detected": bool(cycles),
        "cycles_without_fuel_caveat": sum(
            FUEL_CAVEAT not in c["unresolved"] for c in cycles
        ),
        "dependency_gaps_expected": sorted(spec.get("dependency_gaps", [])),
        "dependency_gaps_found": gaps,
        "unverified_hypotheses_only": all(
            c["verified"] is False and c["status"] == "hypothesis"
            for c in search["candidates"]
        ),
        "scope_complete_claimed": search["coverage"]["scope_complete"],
    }


def _aggregate(case_results: list[dict]) -> dict[str, Any]:
    per_dimension = {d: _empty_counts() for d in DIMENSIONS}
    per_mechanism: dict[str, dict[str, int]] = defaultdict(Counter)
    per_mechanism_dims: dict[str, dict[str, int]] = defaultdict(_empty_counts)
    clause_status = Counter()
    unjudged: dict[str, Counter] = defaultdict(Counter)
    unjudged_fields: Counter = Counter()
    unjudged_conditions: Counter = Counter()
    findings = []
    for case in case_results:
        for label in case["labels"]:
            clause_status[label["clause_status"]] += 1
            for mechanism in label["mechanisms"]:
                stats = per_mechanism[mechanism]
                stats["labels"] += 1
                stats[label["clause_status"]] += 1
                stats["all_scored_dimensions_correct"] += label[
                    "all_scored_dimensions_correct"
                ]
            where = f"{case['case_id']}/{label['label_id']}"
            for dimension, row in label["dimensions"].items():
                buckets = [per_dimension[dimension]] + [
                    per_mechanism_dims[m] for m in label["mechanisms"]
                ]
                for counts in buckets:
                    counts["gold"] += 1
                    if row["outcome"] == "tp":
                        counts["tp"] += 1
                    elif row["outcome"] == "wrong":
                        counts["fp"] += 1
                        counts["fn"] += 1
                    else:
                        counts["unknown"] += 1
                if row["outcome"] == "wrong":
                    findings.append(
                        {
                            "kind": "wrong_extraction",
                            "where": where,
                            "dimension": dimension,
                            "gold": row["gold"],
                            "extracted": row["extracted"],
                        }
                    )
            for dimension, row in sorted(label["unjudged"].items()):
                unjudged[dimension][row["reason"]] += 1
                for m in label["mechanisms"]:
                    per_mechanism[m]["unjudged_predictions"] += 1
                findings.append(
                    {
                        "kind": "unjudged_prediction",
                        "where": where,
                        "dimension": dimension,
                        **row,
                    }
                )
            if label["unjudged_fact_fields"]:
                for field in label["unjudged_fact_fields"]["fields"]:
                    unjudged_fields[field] += 1
                for condition in label["unjudged_fact_fields"]["conditions"]:
                    unjudged_conditions[condition] += 1
            interpreted = label["clause_status"] == "interpreted"
            if label["clause_status"] in {"dropped", "segmentation_mismatch"}:
                findings.append({"kind": label["clause_status"], "where": where})
            elif label["v1_expectation"] == "interpret" and not interpreted:
                findings.append(
                    {
                        "kind": "expected_supported_abstained",
                        "where": where,
                        "compiler_issues": label["compiler_issues"],
                    }
                )
            elif label["v1_expectation"] == "abstain" and interpreted:
                findings.append({"kind": "unexpected_interpretation", "where": where})
        for clause in case["unlabeled_compiler_clauses"]:
            findings.append(
                {
                    "kind": "unlabeled_compiler_clause",
                    "where": case["case_id"],
                    **clause,
                }
            )
        search = case["search"]
        if search:
            for link in search["links"]["missing"]:
                findings.append(
                    {
                        "kind": "expected_connection_missing",
                        "where": case["case_id"],
                        "link": link,
                    }
                )
            for link in search["links"]["forbidden_found"]:
                findings.append(
                    {
                        "kind": "forbidden_connection_found",
                        "where": case["case_id"],
                        "link": link,
                    }
                )
            if search["cycle_expected"] != search["cycle_detected"]:
                findings.append(
                    {"kind": "cycle_expectation_mismatch", "where": case["case_id"]}
                )
            if search["cycles_without_fuel_caveat"]:
                findings.append(
                    {"kind": "cycle_missing_fuel_caveat", "where": case["case_id"]}
                )
            if search["dependency_gaps_expected"] != search["dependency_gaps_found"]:
                findings.append(
                    {
                        "kind": "dependency_gap_mismatch",
                        "where": case["case_id"],
                        "expected": search["dependency_gaps_expected"],
                        "found": search["dependency_gaps_found"],
                    }
                )
    totals = _empty_counts()
    for counts in per_dimension.values():
        for key in totals:
            totals[key] += counts[key]
    labels = sum(clause_status.values())
    scored_correct = [
        l
        for c in case_results
        for l in c["labels"]
        if l["all_scored_dimensions_correct"]
    ]
    links = Counter()
    for case in case_results:
        if case["search"]:
            for key in (
                "tp",
                "fn",
                "fp",
                "expected_denominator",
                "forbidden_denominator",
                "unlabeled_found",
            ):
                links[key] += case["search"]["links"][key]
    return {
        "dimensions": {d: _finish(c) for d, c in per_dimension.items()},
        "all_dimensions": _finish(totals),
        "clauses": {
            "labels": labels,
            "status": dict(sorted(clause_status.items())),
            "all_scored_dimensions_correct": len(scored_correct),
            "all_scored_dimensions_correct_with_unread_fact_content": sum(
                bool(l["unread_fact_content"]) for l in scored_correct
            ),
            "full_clause_semantic_correctness_judged": False,
            "represented_clause_coverage": _ratio(clause_status["interpreted"], labels),
            "all_scored_dimensions_correct_clause_coverage": _ratio(
                len(scored_correct), labels
            ),
            "denominator": labels,
        },
        "mechanisms": {
            m: {
                **{
                    k: per_mechanism[m].get(k, 0)
                    for k in (
                        "labels",
                        "interpreted",
                        "preserved_unknown",
                        "dropped",
                        "segmentation_mismatch",
                        "all_scored_dimensions_correct",
                        "unjudged_predictions",
                    )
                },
                "dimensions": _finish(per_mechanism_dims[m]),
            }
            for m in sorted(per_mechanism)
        },
        "unjudged": {
            "predictions_by_dimension": {
                d: dict(sorted(c.items())) for d, c in sorted(unjudged.items())
            },
            "predictions": sum(sum(c.values()) for c in unjudged.values()),
            "fact_fields_on_interpreted_labels": dict(sorted(unjudged_fields.items())),
            "conditions_on_interpreted_labels": dict(
                sorted(unjudged_conditions.items())
            ),
        },
        "search_links": {
            **dict(links),
            "precision_over_labelled": _ratio(links["tp"], links["tp"] + links["fp"]),
            "coverage": _ratio(links["tp"], links["expected_denominator"]),
        },
        "findings_for_coordinator": findings,
    }


def run_benchmark(document: dict, *, cases_sha256: str | None = None) -> dict:
    """Validate strictly, then score; raises BenchmarkError on any binding error."""
    errors = validate_cases(document)
    if errors:
        raise BenchmarkError(errors)
    results = [run_case(c) for c in document["cases"]]
    binding = [e for r in results for e in r["binding_errors"]]
    if binding:
        raise BenchmarkError(binding)
    for result in results:
        del result["binding_errors"]
    return {
        "schema": SCHEMA,
        "cases_schema": CASES_SCHEMA,
        "cases_sha256": cases_sha256 or sha256(canonical(document)),
        "compiler_version": COMPILER_VERSION,
        "search_version": SEARCH_VERSION,
        "claims": {
            "label_authorship": document["authorship"],
            "independent_expert_labels": False,
            "human_expert_review": False,
            "live_inference_quality_measured": False,
            "model_or_network_calls": 0,
            "semantic_scorer": None,
            "full_clause_semantic_correctness_judged": False,
            "scoring_change_not_compiler_change": SCORING_CHANGE,
            "interpretation": "Development diagnostic of literal extraction over AI-authored fixtures; not evidence of held-out generalization, recommendation quality or rules correctness.",
        },
        "limitations": [
            "Gold values were written by the same AI that read the compiler grammar and use its token vocabulary; precision is optimistic and not independent.",
            "Synthetic renamed cards; no real Oracle corpus sample, so coverage is per fixture, not per deck population.",
            "Dimensions the compiler never represents (e.g. death multiplicity, sacrifice fuel) count as unknown, not FP; restrictions and output_events treat an interpreted fact as a complete claim.",
            "output_events scores structured event rows only. Effects the compiler keeps solely as condition prose (pump, gain flying, explore reveal and +1/+1 counter) score wrong because search cannot join on them; they are listed under summary.unjudged.conditions_on_interpreted_labels, so 'wrong' here means 'not emitted as an event', not 'lost entirely'.",
            "Fact fields no dimension reads (input actor/recipient/source_types, output condition/optional, required_rules) are disclosed per label as unjudged_fact_fields, not scored; required_rules is judged only through search dependency gaps.",
            "Search scoring checks labelled source-pair links only; unlabelled links are counted, not judged.",
            "Hashes and exact tokens establish binding, not entailment or rules correctness.",
        ],
        "scoring": {
            "dimensions": list(DIMENSIONS),
            "tp": "interpreted fact value equals gold token set",
            "fp": "interpreted fact value differs from gold (also counted fn)",
            "fn": "wrong interpreted value",
            "unknown": "compiler abstained or does not represent the dimension; never tp/fp",
            "precision": "tp/(tp+fp); null when denominator is 0",
            "coverage": "tp/gold; abstentions and unrepresented clauses stay in the denominator",
            "restrictions": "interpreted facts claim full scope, so they assert no restrictions",
            "finite_fuel": "only true is labelled; compiler emits true or does not represent it",
            "output_events": "sorted '<event>:<subject>' of output rows; missing, extra or renamed events score wrong",
            "unjudged": "an emitted dimension with null or absent gold is reported, never tp/fp, and blocks all_scored_dimensions_correct",
            "all_scored_dimensions_correct": "partial verdict: every scored dimension is tp and none is unjudged; unread fact fields and condition prose (unread_fact_content) are not judged, so this is not full clause semantic correctness",
        },
        "scoring_version": SCORING_VERSION,
        "scoring_history": SCORING_HISTORY,
        "summary": _aggregate(results),
        "cases": results,
    }


def _encode(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--output", type=Path)
    group.add_argument(
        "--check", type=Path, help="fail unless regenerated report matches"
    )
    args = parser.parse_args(argv)
    raw = args.cases.read_text(encoding="utf-8")
    try:
        report = run_benchmark(json.loads(raw), cases_sha256=sha256(raw))
    except BenchmarkError as exc:
        for error in exc.errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    encoded = _encode(report)
    if args.check:
        if args.check.read_text(encoding="utf-8") != encoded:
            print(f"error: {args.check} is stale or not reproducible", file=sys.stderr)
            return 1
        return 0
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
