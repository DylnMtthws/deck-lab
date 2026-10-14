"""Deterministic labeled evaluation of Build assistant intelligence.

A suite is versioned JSON of golden cases. Each case calls one existing API
(``research_memory``, ``retrieve_strategy_evidence`` or
``assess_deck_requirements``) and is checked against exact expectations that
were labeled before the run. The report is machine-readable: denominators per
kind and label, every failed check with expected and actual values,
abstentions, and latency. Nothing passes by default: an empty suite fails, a
case without checks is rejected, and a case whose database is unavailable is
reported as unavailable rather than skipped or passed.

Memory cases run in a hermetic scenario database. Scenarios declare decks,
saved preferences and experiments; experiment comparisons are produced by the
real native comparison and held-out validation functions, and memory then
reads them back through its read-only path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabermetrics import db
from sabermetrics.assistant_deck_intelligence import assess_deck_requirements
from sabermetrics.assistant_experiments import ExperimentService
from sabermetrics.assistant_memory import research_memory
from sabermetrics.assistant_store import AssistantStore
from sabermetrics.assistant_strategy_evidence import retrieve_strategy_evidence
from sabermetrics.deck_simulation import candidate_from_document, user_edited_provenance
from sabermetrics.goldfish_validation import compare_validation, validation_seed
from sabermetrics.native_diagnostics_comparison import compare_reports

SUITE_VERSION = "assistant-intelligence-eval.v1"
REPORT_VERSION = "assistant-intelligence-eval-report.v1"
KINDS = ("memory", "retrieval", "requirements")
OPERATORS = (
    "equals",
    "not_equals",
    "in",
    "not_in",
    "gte",
    "lte",
    "length",
    "exists",
    "any_contains",
    "none_contains",
)
MAX_CASES = 500
MAX_ACTUAL_CHARS = 400

_COMMANDER_ORACLE = "eval-commander-oracle"
_ISLAND_ORACLE = "eval-island-oracle"
_PRIOR_ORACLE = "eval-prior-oracle"

_SCENARIO_SCHEMA = """
CREATE TABLE deck_documents (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'Eval deck',
 revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE deck_zones (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
 name TEXT NOT NULL, sort_order INTEGER NOT NULL
);
CREATE TABLE deck_entries (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
 zone_id TEXT, card_id TEXT, oracle_id TEXT, name TEXT NOT NULL, quantity INTEGER NOT NULL,
 is_commander INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,
 role TEXT, color_identity TEXT
);
"""


class SuiteError(ValueError):
    """The suite file is malformed; no case was run."""


# --------------------------------------------------------------------------
# Suite loading


def load_suite(path: str | Path) -> dict[str, Any]:
    """Read and structurally validate a suite file.

    Raises:
        SuiteError: The file is unreadable, malformed, or declares bad cases.
    """
    try:
        raw = Path(path).read_bytes()
        suite = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"Suite is unreadable: {exc}") from exc
    validate_suite(suite)
    suite = dict(copy.deepcopy(suite))
    suite["_sha256"] = hashlib.sha256(raw).hexdigest()
    suite["_path"] = str(path)
    return suite


def validate_suite(suite: Any) -> None:
    """Reject malformed suites before any case runs.

    An empty case list is structurally valid here; ``run_suite`` reports it
    as a failed evaluation so the empty state is visible in the report.
    """
    if not isinstance(suite, dict) or suite.get("version") != SUITE_VERSION:
        raise SuiteError(f"Suite must be an object with version {SUITE_VERSION}.")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise SuiteError(f"Suite cases must be a list of at most {MAX_CASES}.")
    scenarios = suite.get("scenarios", {})
    if not isinstance(scenarios, dict):
        raise SuiteError("Suite scenarios must be an object.")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        where = f"case {index}"
        if not isinstance(case, dict):
            raise SuiteError(f"{where} must be an object.")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise SuiteError(f"{where} needs a unique non-empty id.")
        seen.add(case_id)
        where = f"case {case_id}"
        if case.get("kind") not in KINDS:
            raise SuiteError(f"{where} kind must be one of {', '.join(KINDS)}.")
        if not isinstance(case.get("capability"), str) or not case["capability"]:
            raise SuiteError(f"{where} needs a capability label.")
        if not isinstance(case.get("labels", {}), dict):
            raise SuiteError(f"{where} labels must be an object.")
        if not isinstance(case.get("expect_abstention"), bool):
            raise SuiteError(f"{where} must declare expect_abstention true or false.")
        if not isinstance(case.get("input"), dict):
            raise SuiteError(f"{where} input must be an object.")
        checks = case.get("checks")
        if not isinstance(checks, list) or not checks:
            raise SuiteError(f"{where} needs at least one exact check.")
        for check in checks:
            if (
                not isinstance(check, dict)
                or not isinstance(check.get("path"), str)
                or check.get("op") not in OPERATORS
                or "value" not in check
            ):
                raise SuiteError(
                    f"{where} checks need path, op ({', '.join(OPERATORS)}) and value."
                )
        if case["kind"] == "memory":
            scenario = case["input"].get("scenario")
            if scenario not in scenarios:
                raise SuiteError(f"{where} references unknown scenario {scenario!r}.")


# --------------------------------------------------------------------------
# Path lookup and checks

_MISSING = object()
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def lookup(value: Any, path: str) -> Any:
    """Resolve ``a.b[0].c``; return a sentinel when any step is missing."""
    current = value
    for name, index in _TOKEN.findall(path):
        if name:
            if not isinstance(current, dict) or name not in current:
                return _MISSING
            current = current[name]
        else:
            position = int(index)
            if not isinstance(current, list) or position >= len(current):
                return _MISSING
            current = current[position]
    return current


def _evaluate(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return (actual is not _MISSING) is bool(expected)
    if actual is _MISSING:
        return False
    if op == "equals":
        return bool(actual == expected and type(actual) is type(expected)) or (
            _is_number(actual) and _is_number(expected) and actual == expected
        )
    if op == "not_equals":
        return bool(actual != expected)
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op in {"gte", "lte"}:
        if not (_is_number(actual) and _is_number(expected)):
            return False
        return bool(actual >= expected if op == "gte" else actual <= expected)
    if op == "length":
        return isinstance(actual, (list, dict, str)) and len(actual) == expected
    if op in {"any_contains", "none_contains"}:
        if isinstance(actual, str):
            items = [actual]
        elif isinstance(actual, list):
            items = [
                json.dumps(item) if not isinstance(item, str) else item
                for item in actual
            ]
        else:
            return False
        found = any(str(expected).casefold() in item.casefold() for item in items)
        return found if op == "any_contains" else not found
    return False


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _shown(value: Any) -> Any:
    if value is _MISSING:
        return "<missing>"
    text = json.dumps(value, sort_keys=True, default=str)
    return value if len(text) <= MAX_ACTUAL_CHARS else text[:MAX_ACTUAL_CHARS] + "…"


# --------------------------------------------------------------------------
# Memory scenarios


def _deck_entries(deck: dict[str, Any], library_quantity: int) -> list[dict[str, Any]]:
    deck_id = deck["id"]
    return [
        {
            "id": f"{deck_id}-commander",
            "card_id": "eval-card-commander",
            "oracle_id": _COMMANDER_ORACLE,
            "name": "Eval Commander",
            "quantity": 1,
            "is_commander": 1,
            "zone_name": "Unsorted",
            "color_identity": "[]",
            "role": None,
        },
        {
            "id": f"{deck_id}-island",
            "card_id": "eval-card-island",
            "oracle_id": _ISLAND_ORACLE,
            "name": "Island",
            "quantity": library_quantity,
            "is_commander": 0,
            "zone_name": "Unsorted",
            "color_identity": "[]",
            "role": "land",
        },
    ]


def _hash(entries: list[dict[str, Any]]) -> str:
    return candidate_from_document(
        {"entries": entries}, user_edited_provenance()
    ).deck_sha256


def _prior_hash(deck: dict[str, Any]) -> str:
    entries = _deck_entries(deck, 98)
    entries.append(
        {
            "id": f"{deck['id']}-prior",
            "oracle_id": _PRIOR_ORACLE,
            "name": "Prior Card",
            "quantity": 1,
            "is_commander": 0,
            "zone_name": "Unsorted",
        }
    )
    return _hash(entries)


def _native_report(
    seed: int, value: float, *, engine: str, complete_coverage: bool
) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "games": 2000,
        "seed": seed,
        "engine_version": engine,
        "settings_fingerprint": f"eval-profile-{engine}",
        "policy": "eval-pilot",
        "effects_sha256": "eval-effects",
        "strategy_pack_sha256": "eval-pack",
    }
    coverage: dict[str, Any] = {"modeled_cards": 100, "inert_cards": 0}
    if complete_coverage:
        coverage.update(
            total_cards=100,
            card_coverage={
                "version": 1,
                "unit": "card_slots",
                "counts": {
                    "supported": 100,
                    "partial": 0,
                    "unsupported": 0,
                    "inactive": 0,
                },
                "cards": [{"slot": slot, "status": "supported"} for slot in range(100)],
            },
        )
    return {
        "schema_version": "native",
        "settings": settings,
        "coverage": coverage,
        "tests": {
            "opening": {
                "status": "supported",
                "metrics": {
                    "kept": {
                        "value": value,
                        "unit": "proportion",
                        "denominator": 2000,
                        "ci95": {
                            "low": max(0.0, value - 0.05),
                            "high": min(1.0, value + 0.05),
                        },
                    }
                },
            }
        },
    }


def _experiment_payload(
    spec: dict[str, Any], deck: dict[str, Any], current_hash: str
) -> dict[str, Any]:
    training_seed = int(spec.get("training_seed", 1234))
    held_out = validation_seed(training_seed)
    deck_entries = _deck_entries(deck, int(deck.get("library_quantity", 99)))
    baseline_hash = (
        current_hash if spec.get("baseline") == "current" else _prior_hash(deck)
    )
    baseline_value = float(spec.get("baseline_value", 0.5))
    # Coverage is shared by baseline and alternatives: differing coverage is
    # itself a reason the native comparison refuses to compare.
    complete = bool(spec.get("complete_coverage", False))
    base_train = _native_report(
        training_seed, baseline_value, engine="v1", complete_coverage=complete
    )
    base_valid = _native_report(
        held_out, baseline_value, engine="v1", complete_coverage=complete
    )
    alternatives = []
    for alt in spec.get("alternatives", []):
        status = alt.get("status", "done")
        outgoing = f"{deck['id']}-island"
        incoming = alt.get("incoming_card_id", f"eval-incoming-{alt['id']}")
        item: dict[str, Any] = {
            "id": alt["id"],
            "name": alt.get("name", alt["id"]),
            "status": status,
            "deck_sha256": (
                current_hash
                if alt.get("deck") == "current"
                else hashlib.sha256(f"eval-alt:{alt['id']}".encode()).hexdigest()
            ),
            "replacements": [
                {"outgoing_entry_id": outgoing, "incoming_card_id": incoming}
            ],
            "document": {
                "entries": [
                    {
                        "id": f"alternative-{outgoing}-{incoming}",
                        "name": alt.get("incoming_name", "Eval Incoming"),
                    }
                ]
            },
            "comparison": None,
            "validation": None,
            "error": alt.get("error", ""),
        }
        if status == "done":
            engine = (
                "v1" if alt.get("comparison", "comparable") == "comparable" else "v2"
            )
            value = float(alt.get("value", baseline_value))
            train = _native_report(
                training_seed, value, engine=engine, complete_coverage=complete
            )
            valid = _native_report(
                held_out, value, engine=engine, complete_coverage=complete
            )
            item["comparison"] = compare_reports(train, base_train)
            item["validation"] = compare_validation(
                base_valid, valid, training_seed=training_seed
            )
        alternatives.append(item)
    return {
        "id": spec["id"],
        "deck_id": deck["id"],
        "status": spec.get("status", "done"),
        "created_at": spec["created_at"],
        "baseline": {
            "revision": spec.get("baseline_revision", deck.get("revision", 0)),
            "deck_sha256": baseline_hash,
            "document": {
                "entries": [{"id": e["id"], "name": e["name"]} for e in deck_entries],
                "padding": "p" * int(spec.get("pad_chars", 0)),
            },
            "training_report": base_train,
            "validation_report": base_valid,
        },
        "constraints": spec.get("constraints", {}),
        "settings": {
            "games": 2000,
            "training_seed": training_seed,
            "validation_seed": held_out,
        },
        "alternatives": alternatives,
        "error": spec.get("error", ""),
        "adopted_alternative_id": spec.get("adopted"),
    }


def build_memory_scenario(path: Path, scenario: dict[str, Any]) -> None:
    """Materialize one hermetic scenario through the real store tables."""
    with db.connect(path) as conn:
        conn.executescript(_SCENARIO_SCHEMA)
        decks: dict[str, dict[str, Any]] = {}
        hashes: dict[str, str] = {}
        for deck in scenario.get("decks", []):
            decks[deck["id"]] = deck
            quantity = int(deck.get("library_quantity", 99))
            entries = _deck_entries(deck, quantity)
            conn.execute(
                "INSERT INTO deck_documents(id,owner_id,revision,updated_at) VALUES(?,?,?,?)",
                (
                    deck["id"],
                    deck["owner"],
                    int(deck.get("revision", 0)),
                    deck.get("updated_at", "2026-10-01T00:00:00+00:00"),
                ),
            )
            zone = f"{deck['id']}-zone"
            conn.execute(
                "INSERT INTO deck_zones(id,deck_id,name,sort_order) VALUES(?,?,?,0)",
                (zone, deck["id"], "Unsorted"),
            )
            for order, entry in enumerate(entries):
                conn.execute(
                    "INSERT INTO deck_entries(id,deck_id,zone_id,card_id,oracle_id,name,"
                    "quantity,is_commander,sort_order,role,color_identity) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry["id"],
                        deck["id"],
                        zone,
                        entry["card_id"],
                        entry["oracle_id"],
                        entry["name"],
                        entry["quantity"],
                        entry["is_commander"],
                        order,
                        entry["role"],
                        entry["color_identity"],
                    ),
                )
            try:
                hashes[deck["id"]] = _hash(entries)
            except ValueError:
                hashes[deck["id"]] = ""
        conn.commit()
    store = AssistantStore(path)
    for preference in scenario.get("preferences", []):
        store.settings(
            preference["owner"],
            preference["deck"],
            preference.get("mode", "suggest"),
            preference.get("constraints", {}),
        )
    ExperimentService(path)  # creates the real experiment table
    with db.connect(path) as conn:
        for spec in scenario.get("experiments", []):
            deck = decks[spec["deck"]]
            payload = _experiment_payload(spec, deck, hashes[deck["id"]] or "")
            conn.execute(
                "INSERT INTO assistant_experiments(id,owner_id,deck_id,created_at,state,payload_json) "
                "VALUES(?,?,?,?,?,?)",
                (
                    spec["id"],
                    spec["owner"],
                    deck["id"],
                    spec["created_at"],
                    spec.get("status", "done"),
                    json.dumps(payload),
                ),
            )
        conn.commit()


# --------------------------------------------------------------------------
# Runners


def _abstained(kind: str, result: dict[str, Any]) -> bool:
    if kind == "memory":
        return result.get("status") != "available"
    if kind == "retrieval":
        return result.get("status") in {"no_matches", "empty_corpus", "unavailable"}
    return result.get("specialized_status") == "unavailable"


def _retrieval_db(
    spec: dict[str, Any], workdir: Path, db_path: Path | None
) -> Path | None:
    corpus = spec.get("corpus", "bundled")
    if corpus == "database":
        return db_path if db_path is not None and db_path.is_file() else None
    path = (
        workdir
        / f"retrieval-{hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]}.db"
    )
    if not path.exists():
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE reference_chunks (id TEXT PRIMARY KEY, document TEXT, "
                "section TEXT, tier INTEGER, content TEXT)"
            )
            if corpus == "fixture":
                for chunk in spec.get("chunks", []):
                    conn.execute(
                        "INSERT INTO reference_chunks VALUES(?,?,?,?,?)",
                        (
                            chunk["id"],
                            chunk.get("document", ""),
                            chunk.get("section", ""),
                            int(chunk.get("tier", 2)),
                            chunk["content"],
                        ),
                    )
            conn.commit()
    return path


def _requirements_document(spec: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for index, card in enumerate([spec["commander"], *spec.get("library", [])]):
        entries.append(
            {
                "id": f"eval-entry-{index}",
                "name": card["name"],
                "oracle_id": card.get("oracle_id", f"eval-oracle-{index}"),
                "type_line": card.get("type_line", ""),
                "oracle_text": card.get("oracle_text", ""),
                "mana_value": card.get("cmc"),
                "cmc": card.get("cmc"),
                "quantity": int(card.get("quantity", 1)),
                "is_commander": 1 if index == 0 else 0,
                "zone_name": card.get("zone_name", "Unsorted"),
                "color_identity": card.get("color_identity", []),
            }
        )
    return {"entries": entries}


def _rank_of(result: dict[str, Any], relevant: list[str]) -> int | None:
    for position, excerpt in enumerate(result.get("excerpts", []), start=1):
        if excerpt.get("citation", {}).get("chunk_id") in relevant:
            return position
    return None


def _requirement_view(result: dict[str, Any]) -> dict[str, Any]:
    """Index requirement rows by name so checks can address them exactly."""
    view = copy.deepcopy(result)
    view["by_requirement"] = {
        row["requirement"]: row for row in result.get("requirements", [])
    }
    return view


def _run_case(
    case: dict[str, Any],
    suite: dict[str, Any],
    workdir: Path,
    db_path: Path | None,
    scenario_paths: dict[str, Path],
) -> tuple[str, dict[str, Any] | None, str]:
    """Return (outcome, result, detail); outcome is ok, unavailable or error."""
    spec = case["input"]
    kind = case["kind"]
    if kind == "memory":
        name = spec["scenario"]
        if name not in scenario_paths:
            path = workdir / f"scenario-{len(scenario_paths)}.db"
            build_memory_scenario(path, suite["scenarios"][name])
            scenario_paths[name] = path
        scenario = suite["scenarios"][name]
        now = datetime.fromisoformat(scenario.get("now", "2026-10-14T00:00:00+00:00"))
        return (
            "ok",
            research_memory(
                scenario_paths[name],
                spec["owner"],
                spec["deck"],
                spec.get("limit", 3),
                now=now,
            ),
            "",
        )
    if kind == "retrieval":
        corpus_path = _retrieval_db(spec, workdir, db_path)
        if corpus_path is None:
            return (
                "unavailable",
                None,
                "Retrieval database was not supplied or is missing.",
            )
        result = retrieve_strategy_evidence(
            corpus_path,
            spec["query"],
            commander=spec.get("commander", ""),
            limit=spec.get("limit", 4),
        )
        if spec.get("corpus") == "database" and result.get("status") == "unavailable":
            return (
                "unavailable",
                result,
                "Reference corpus is unreadable in the supplied database.",
            )
        return "ok", result, ""
    document = _requirements_document(spec)
    return (
        "ok",
        _requirement_view(
            assess_deck_requirements(document, spec.get("constraints", {}))
        ),
        "",
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def run_suite(
    suite: dict[str, Any],
    *,
    db_path: str | Path | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run every case and return a machine-readable measured report.

    Args:
        suite: A validated suite (see :func:`load_suite`).
        db_path: Optional application database for ``corpus: database`` cases.
        clock: Monotonic clock in seconds; injectable for tests.
    """
    validate_suite(suite)
    database = Path(db_path) if db_path is not None else None
    cases = suite["cases"]
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "suite_version": suite["version"],
        "suite_path": suite.get("_path"),
        "suite_sha256": suite.get("_sha256"),
        "generated_at": datetime.now(UTC).isoformat(),
        "database": {
            "path": str(database) if database else None,
            "status": (
                "not_supplied"
                if database is None
                else "available" if database.is_file() else "unavailable"
            ),
        },
        "status": "fail",
        "totals": {
            "cases": len(cases),
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "unavailable": 0,
            "checks": 0,
            "checks_passed": 0,
        },
        "by_kind": {},
        "by_capability": {},
        "by_label": {},
        "abstentions": {
            "expected": 0,
            "observed": 0,
            "correct": 0,
            "false_abstentions": [],
            "missed_abstentions": [],
        },
        "retrieval_ranking": {
            "denominator": 0,
            "hit_at_1": 0,
            "hit_at_k": 0,
            "mrr": None,
        },
        "latency_ms": {},
        "failures": [],
        "cases": [],
    }
    if not cases:
        report["reason"] = "Empty suite: no labeled cases were evaluated."
        return report
    latencies: dict[str, list[float]] = {kind: [] for kind in KINDS}
    reciprocal: list[float] = []
    scenario_paths: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="assistant-eval-") as directory:
        workdir = Path(directory)
        for case in cases:
            kind = case["kind"]
            started = clock()
            try:
                outcome, result, detail = _run_case(
                    case, suite, workdir, database, scenario_paths
                )
            except Exception as exc:  # reported per case, never swallowed
                outcome, result, detail = (
                    "error",
                    None,
                    f"{type(exc).__name__}: {exc}"[:500],
                )
            elapsed = round((clock() - started) * 1000, 3)
            latencies[kind].append(elapsed)
            record: dict[str, Any] = {
                "id": case["id"],
                "kind": kind,
                "capability": case["capability"],
                "labels": case.get("labels", {}),
                "outcome": outcome,
                "latency_ms": elapsed,
                "expect_abstention": case["expect_abstention"],
                "abstained": None,
                "checks_total": len(case["checks"]),
                "checks_passed": 0,
            }
            failed_checks: list[dict[str, Any]] = []
            if outcome == "ok" and result is not None:
                abstained = _abstained(kind, result)
                record["abstained"] = abstained
                if abstained != case["expect_abstention"]:
                    failed_checks.append(
                        {
                            "path": "<abstention>",
                            "op": "equals",
                            "expected": case["expect_abstention"],
                            "actual": abstained,
                        }
                    )
                for check in case["checks"]:
                    actual = lookup(result, check["path"])
                    if _evaluate(check["op"], actual, check["value"]):
                        record["checks_passed"] += 1
                    else:
                        failed_checks.append(
                            {
                                "path": check["path"],
                                "op": check["op"],
                                "expected": check["value"],
                                "actual": _shown(actual),
                            }
                        )
                budget = case.get("max_latency_ms")
                if _is_number(budget) and elapsed > budget:
                    failed_checks.append(
                        {
                            "path": "<latency_ms>",
                            "op": "lte",
                            "expected": budget,
                            "actual": elapsed,
                        }
                    )
                relevant = case["input"].get("relevant_chunk_ids")
                if kind == "retrieval" and isinstance(relevant, list) and relevant:
                    rank = _rank_of(result, relevant)
                    record["relevant_rank"] = rank
                    report["retrieval_ranking"]["denominator"] += 1
                    report["retrieval_ranking"]["hit_at_1"] += int(rank == 1)
                    report["retrieval_ranking"]["hit_at_k"] += int(rank is not None)
                    reciprocal.append(1 / rank if rank else 0.0)
                record["outcome"] = "passed" if not failed_checks else "failed"
            elif outcome == "error":
                record["detail"] = detail
            else:
                record["detail"] = detail
            totals = report["totals"]
            totals["checks"] += record["checks_total"]
            totals["checks_passed"] += record["checks_passed"]
            key = {
                "passed": "passed",
                "failed": "failed",
                "error": "errored",
                "unavailable": "unavailable",
            }[record["outcome"]]
            totals[key] += 1
            if record["abstained"] is not None:
                abst = report["abstentions"]
                abst["expected"] += int(case["expect_abstention"])
                abst["observed"] += int(record["abstained"])
                abst["correct"] += int(
                    record["abstained"] and case["expect_abstention"]
                )
                if record["abstained"] and not case["expect_abstention"]:
                    abst["false_abstentions"].append(case["id"])
                if case["expect_abstention"] and not record["abstained"]:
                    abst["missed_abstentions"].append(case["id"])
            buckets = [
                ("by_kind", kind),
                ("by_capability", case["capability"]),
                *[
                    ("by_label", f"{name}={value}")
                    for name, value in sorted(case.get("labels", {}).items())
                ],
            ]
            for group, name in buckets:
                bucket = report[group].setdefault(
                    name,
                    {
                        "denominator": 0,
                        "passed": 0,
                        "failed": 0,
                        "errored": 0,
                        "unavailable": 0,
                    },
                )
                bucket["denominator"] += 1
                bucket[key] += 1
            if record["outcome"] != "passed":
                report["failures"].append(
                    {
                        "id": case["id"],
                        "kind": kind,
                        "outcome": record["outcome"],
                        "detail": record.get("detail", ""),
                        "checks": failed_checks,
                    }
                )
            report["cases"].append(record)
    for group in ("by_kind", "by_capability", "by_label"):
        for bucket in report[group].values():
            bucket["pass_rate"] = _rate(bucket["passed"], bucket["denominator"])
    ranking = report["retrieval_ranking"]
    ranking["mrr"] = round(sum(reciprocal) / len(reciprocal), 4) if reciprocal else None
    report["latency_ms"] = {
        kind: {
            "count": len(values),
            "p50": _percentile(values, 0.5),
            "p95": _percentile(values, 0.95),
            "max": round(max(values), 3) if values else None,
        }
        for kind, values in latencies.items()
    }
    totals = report["totals"]
    totals["pass_rate"] = _rate(totals["passed"], totals["cases"])
    if totals["failed"] or totals["errored"]:
        report["status"] = "fail"
    elif totals["unavailable"]:
        report["status"] = "unavailable"
    else:
        report["status"] = "pass"
    return report


def exit_code(report: dict[str, Any]) -> int:
    """0 only when every case passed; 2 when only unavailability blocked it."""
    if report.get("status") == "pass":
        return 0
    if report.get("status") == "unavailable":
        return 2
    return 1


def summary_lines(report: dict[str, Any]) -> list[str]:
    """Short human summary; the JSON report is the record."""
    totals = report["totals"]
    lines = [
        f"status={report['status']} cases={totals['cases']} passed={totals['passed']} "
        f"failed={totals['failed']} errored={totals['errored']} "
        f"unavailable={totals['unavailable']} checks={totals['checks_passed']}/{totals['checks']}"
    ]
    if report.get("reason"):
        lines.append(report["reason"])
    for kind, bucket in sorted(report["by_kind"].items()):
        lines.append(
            f"  {kind}: {bucket['passed']}/{bucket['denominator']} "
            f"(p95 {report['latency_ms'][kind]['p95']} ms)"
        )
    abst = report["abstentions"]
    lines.append(
        f"  abstentions: expected={abst['expected']} observed={abst['observed']} "
        f"correct={abst['correct']}"
    )
    ranking = report["retrieval_ranking"]
    if ranking["denominator"]:
        lines.append(
            f"  retrieval: hit@1={ranking['hit_at_1']}/{ranking['denominator']} "
            f"mrr={ranking['mrr']}"
        )
    for failure in report["failures"]:
        lines.append(
            f"  FAIL {failure['id']} ({failure['outcome']}) {failure['detail']}"
        )
        for check in failure["checks"]:
            lines.append(
                f"    {check['path']} {check['op']} expected={json.dumps(check['expected'])} "
                f"actual={json.dumps(check['actual'], default=str)}"
            )
    return lines
