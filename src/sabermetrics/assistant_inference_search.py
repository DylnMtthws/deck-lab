"""Offline, bounded connection hypotheses over source-bound compiler facts.

This is a trace enumerator, not a Magic simulator. Even exact field matches do
not establish payment, trigger resolution, resource renewability, or timing.
Unknown constraints remain obligations; explicit mismatches do not join. There
is no global cache and no semantic/provider calls.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from copy import deepcopy
from hashlib import sha256
from typing import Any

VERSION = "assistant-inference-search.v1"
_UNKNOWN = (None, "", "unknown", "unspecified")
_LIMITS = {"max_expansions": 4096, "max_depth": 16, "max_candidates": 128}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _known(value: Any) -> bool:
    return value not in _UNKNOWN and value != [] and value != {}


def _event(row: dict) -> str | None:
    # Costs may use either the event vocabulary or an explicit kind. Never
    # extract an event from prose, or alias graveyard movement to discard.
    value = row.get("event", row.get("kind"))
    return value if isinstance(value, str) else None


def _binding(bundle: dict) -> tuple[list[dict], dict[str, dict], list[dict]]:
    sources: dict[str, dict] = {}
    invalid: list[dict] = []
    rows = bundle.get("sources", [])
    source_counts = Counter(s.get("source_id") for s in rows if isinstance(s, dict))
    for source in rows:
        if not isinstance(source, dict):
            continue
        sid = source.get("source_id")
        oracle = source.get("oracle_text")
        copied = source.get("copied_fields")
        if (
            not isinstance(sid, str)
            or source_counts[sid] != 1
            or not isinstance(oracle, str)
            or not isinstance(copied, dict)
            or source.get("source_sha256") != _digest(copied)
            or oracle != copied.get("oracle_text", copied.get("text"))
            or source.get("type_line", "") != copied.get("type_line", "")
            or source.get("name", "") != copied.get("name", "")
            or (
                not sid.startswith("rule:")
                and source.get("card_id")
                != (
                    copied.get("oracle_id") or copied.get("card_id") or copied.get("id")
                )
            )
        ):
            invalid.append({"source_id": sid, "reason": "invalid_source_binding"})
            continue
        sources[sid] = source
    facts = bundle.get("facts", [])
    counts = Counter(f.get("fact_id") for f in facts if isinstance(f, dict))
    admitted = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fid, sid = fact.get("fact_id"), fact.get("source_id")
        source = sources.get(sid) if isinstance(sid, str) else None
        start, end = fact.get("start"), fact.get("end")
        reason = None
        if not isinstance(fid, str) or not fid or counts[fid] != 1:
            reason = "invalid_or_duplicate_fact_id"
        elif source is None or fact.get("source_sha256") != source["source_sha256"]:
            reason = "stale_or_missing_source"
        elif (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(source["oracle_text"])
            or source["oracle_text"][start:end] != fact.get("quote")
        ):
            reason = "invalid_fact_span"
        elif (
            fact.get("quote_sha256", sha256(fact["quote"].encode()).hexdigest())
            != sha256(fact["quote"].encode()).hexdigest()
        ):
            reason = "invalid_quote_hash"
        elif (
            fact.get("status") != "bounded_interpretation"
            or fact.get("kind")
            not in {"activated", "triggered", "rule_definition", "spell"}
            or any(
                not isinstance(fact.get(key), list)
                or any(not isinstance(item, dict) for item in fact[key])
                for key in ("inputs", "outputs", "costs")
            )
            or any(
                not isinstance(fact.get(key), list)
                or any(not isinstance(item, str) for item in fact[key])
                for key in ("conditions", "required_rules")
            )
        ):
            reason = "invalid_fact_shape"
        else:
            # A manifest must bind the fact to an enclosing, exact clause too.
            clauses = source.get("clauses", [])
            enclosing = [
                c
                for c in clauses
                if isinstance(c, dict)
                and isinstance(c.get("start"), int)
                and isinstance(c.get("end"), int)
                and 0
                <= c["start"]
                <= start
                < end
                <= c["end"]
                <= len(source["oracle_text"])
                and c.get("text") == source["oracle_text"][c["start"] : c["end"]]
                and fid in c.get("fact_ids", [])
                and c.get("parse_status") in {"interpreted", "bounded_interpretation"}
                and not c.get("issues")
            ]
            if not enclosing:
                reason = "fact_missing_from_clause_manifest"
        if reason:
            invalid.append({"fact_id": fid, "source_id": sid, "reason": reason})
        else:
            admitted.append(fact)
    return sorted(admitted, key=lambda f: f["fact_id"]), sources, invalid


def _types(event: dict, sources: dict[str, dict]) -> Any:
    sid = event.get("source_id")
    source = sources.get(sid) if isinstance(sid, str) else None
    if source and source.get("type_line"):
        # Literal source types/subtypes only; no card-name or Oracle guessing.
        return re.findall(r"[\w]+", source["type_line"])
    if _known(event.get("source_types")):
        return event["source_types"]
    return None


def _type_tokens(value: Any) -> set[str]:
    values = [value] if isinstance(value, str) else value
    return {
        token.casefold() for item in values for token in re.findall(r"[\w]+", str(item))
    }


def _type_match(actual: Any, wanted: Any) -> tuple[bool, list[str]]:
    actual_tokens = _type_tokens(actual)
    alternatives = (
        re.split(r"\s+and/or\s+", wanted) if isinstance(wanted, str) else [wanted]
    )
    for alternative in alternatives:
        wanted_tokens = _type_tokens(alternative)
        if wanted_tokens.issubset(actual_tokens):
            return True, []
        # Recognize only a filter spelling obtained from an actually supplied
        # type token. This never invents a subtype for an unknown source.
        unmatched = wanted_tokens - actual_tokens
        if unmatched and all(
            any(w == a + "s" for a in actual_tokens) for w in unmatched
        ):
            return True, [
                "Plural subtype filter binding conditional on supplied source type: "
                + _canonical(alternative)
            ]
    return False, []


def _role_binding(field: str, actual: Any, wanted: Any) -> bool:
    """Recognize role variables, not asserted identities or guessed subtypes."""
    if not isinstance(actual, str) or not isinstance(wanted, str):
        return False
    if field == "actor":
        return actual in {
            "self",
            "creature controller",
            "permanent controller",
        } or wanted in {
            "self",
            "creature controller",
            "permanent controller",
        }
    if field == "subject":
        return actual in {"self", "revealed top card", "top card"} or wanted in {
            "self",
            "exploring creature",
            "exploring permanent",
            "one or more sources you control",
        }
    if field == "recipient":
        return actual == "each opponent" and wanted == "a player"
    return False


def _compare(output: dict, required: dict, sources: dict[str, dict], *, hand=False):
    assumptions: list[str] = []
    issues: list[str] = []
    fields = set(output) | set(required) | {"actor", "subject"}
    fields -= {
        "event",
        "kind",
        "quantity",
        "conditions",
        "condition",
        "source_types",
        "combat_only",
    }
    for field in sorted(fields):
        actual, wanted = output.get(field), required.get(field)
        if hand and field == "from_zone":
            actual = output.get("to_zone")
        if hand and field == "to_zone":
            continue  # Production's destination is the cost's source, not destination.
        if not _known(wanted):
            if field in {"actor", "subject"} or _known(actual):
                issues.append(f"{field} constraint unspecified")
            continue
        if not _known(actual):
            assumptions.append(
                f"Require {field}={_canonical(wanted)}; emitted value unknown"
            )
        elif actual != wanted:
            if (
                field == "subject"
                and isinstance(actual, str)
                and actual in {"land", "land card"}
                and wanted == "card"
            ):
                continue
            if _role_binding(field, actual, wanted):
                assumptions.append(
                    f"Unresolved {field} binding: {_canonical(actual)} to {_canonical(wanted)}"
                )
                continue
            return None
    wanted_types, actual_types = required.get("source_types"), _types(output, sources)
    if _known(wanted_types):
        if not _known(actual_types):
            assumptions.append(
                f"Require source_types={_canonical(wanted_types)}; subtype unknown"
            )
        else:
            matches, type_conditions = _type_match(actual_types, wanted_types)
            if not matches:
                return None
            assumptions.extend(type_conditions)
    if required.get("combat_only") is True:
        if output.get("combat") is False:
            return None
        if output.get("combat") is not True:
            assumptions.append("Require combat damage; combat provenance unknown")
    for field in ("quantity",):
        if not _known(output.get(field)) or not _known(required.get(field)):
            issues.append("quantity unknown; no multiplicity or balance inferred")
        elif output[field] != required[field]:
            assumptions.append(
                "quantity compatibility unresolved; per-card/batch scope preserved"
            )
    assumptions.extend(output.get("conditions", []))
    assumptions.extend(required.get("conditions", []))
    assumptions.extend(
        str(row["condition"])
        for row in (output, required)
        if _known(row.get("condition"))
    )
    if hand:
        assumptions.append(
            "Produced hand card conditionally available for discard; payment unverified"
        )
    return _unique(assumptions), _unique(issues)


def _requirements(fact: dict) -> Iterator[tuple[int, dict, bool]]:
    for index, item in enumerate(fact["inputs"]):
        yield index, item, False
    for index, item in enumerate(fact["costs"]):
        yield index, item, True


def _emissions(fact: dict) -> Iterator[tuple[int, dict, bool]]:
    for index, item in enumerate(fact["outputs"]):
        yield index, item, False
    # Payment events themselves can trigger abilities, but only under the
    # explicit joint payment assumption, never as an actually paid cost.
    for index, item in enumerate(fact["costs"]):
        if _event(item) in {"discard", "sacrifice"}:
            matching = [i for i in fact["inputs"] if _event(i) == _event(item)]
            # Compiler inputs preserve the actor/zones of each printed cost.
            # Keep the raw cost in the ledger and use its explicit event here.
            yield index, matching[0] if len(matching) == 1 else item, True


def _contradictions(conditions: list[str]) -> list[str]:
    # Deliberately tiny syntax check. All other condition relationships remain
    # unknown, including mutually exclusive English conditions.
    normalized = {s.strip().casefold() for s in conditions}
    pairs = [s for s in sorted(normalized) if "not " + s in normalized]
    return [
        f"Conflicting shared conditions remain unresolved: {s} / not {s}" for s in pairs
    ]


def _ledger(fact: dict, occurrence: int) -> list[dict]:
    rows = []
    shared = list(fact["conditions"])
    for direction, items in (
        ("require_input", fact["inputs"]),
        ("consume", fact["costs"]),
        ("produce", fact["outputs"]),
    ):
        for index, item in enumerate(items):
            limits = ["timing_and_interruptions_unresolved"]
            event = _event(item)
            if (
                event in {"draw", "explore", "put_into_hand"}
                or item.get("from_zone") == "library"
            ):
                limits.append("finite_library_unverified")
            if event in {"mana", "tap", "printed_symbols"} or item.get("resource") in {
                "mana",
                "tap",
            }:
                limits.append("finite_mana_or_tap_capacity_unverified")
            if event in {"discard", "sacrifice"}:
                limits.append("finite_hand_or_permanent_fuel_unverified")
            local_conditions = list(item.get("conditions", []))
            if _known(item.get("condition")):
                local_conditions.append(str(item["condition"]))
            rows.append(
                {
                    "fact_id": fact["fact_id"],
                    "occurrence": occurrence,
                    "direction": direction,
                    "index": index,
                    "resource": deepcopy(item),
                    "status": "conditional",
                    "assumptions": list(shared),
                    "conditions": local_conditions,
                    "limits": limits,
                }
            )
    return rows


def discover_connections(
    bundle: dict,
    *,
    max_expansions: int = 256,
    max_depth: int = 5,
    max_candidates: int = 12,
    check_current: Callable[[], Any] | None = None,
) -> dict:
    """Enumerate deterministic short hypotheses without changing ``bundle``.

    Depth counts proposed links. Expansions count indexed join attempts, even
    rejected ones. States and branches share that budget; output has a separate
    cap. ``check_current`` is called at validation/search boundaries: explicit
    False cancels; a callback exception propagates (e.g. caller cancellation).
    User limits have hard safety ceilings recorded in coverage. Unexplored
    counts are lower bounds, never declarations of exhaustive discovery.
    """
    requested = dict(
        max_expansions=max_expansions,
        max_depth=max_depth,
        max_candidates=max_candidates,
    )
    for key, value in requested.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    limits = {key: min(value, _LIMITS[key]) for key, value in requested.items()}
    fingerprint = _digest(
        {
            "version": bundle.get("version"),
            "sources": bundle.get("sources", []),
            "facts": bundle.get("facts", []),
        }
    )
    coverage: dict[str, Any] = {
        "expansions": 0,
        "states_visited": 0,
        "scope_complete": False,
        "capped": False,
        "cancelled": False,
        "unexplored": [],
        "requested_limits": requested,
        "limits": limits,
        "compiler_coverage": deepcopy(bundle.get("coverage", {})),
    }
    result: dict[str, Any] = {
        "version": VERSION,
        "manifest_fingerprint": fingerprint,
        "candidates": [],
        "coverage": coverage,
        "dependency_gaps": [],
    }

    def current() -> bool:
        if check_current is not None and check_current() is False:
            coverage["cancelled"] = True
            return False
        return True

    def unexplored(reason: str) -> None:
        coverage["capped"] = True
        if not any(row["reason"] == reason for row in coverage["unexplored"]):
            coverage["unexplored"].append({"reason": reason, "count_lower_bound": 1})

    if not current():
        unexplored("cancelled")
        return result
    facts, sources, invalid = _binding(bundle)
    coverage["invalid_bindings"] = invalid
    coverage["valid_facts"] = len(facts)
    # Indexing is linear in the supplied manifest. A resource signature is a
    # separate join family, never an alias between event names.
    index: dict[tuple[str, str], list[tuple[dict, int, dict, bool]]] = defaultdict(list)
    rule_names = {f["source_id"] for f in facts if f["kind"] == "rule_definition"}
    rule_names.update(f["fact_id"] for f in facts if f["kind"] == "rule_definition")
    rule_names.update(
        _event(i) for f in facts if f["kind"] == "rule_definition" for i in f["inputs"]
    )
    # Presence of a matching event name cannot discharge ambiguity explicitly
    # retained by the compiler (for example two incompatible rule editions).
    rule_names.difference_update(bundle.get("coverage", {}).get("unresolved_rules", []))
    for fact in facts:
        for rule in fact["required_rules"]:
            if rule not in rule_names:
                result["dependency_gaps"].append(
                    {
                        "required_rule": rule,
                        "target_fact_id": fact["fact_id"],
                        "target_source_id": fact["source_id"],
                        "status": "unresolved",
                    }
                )
        for input_index, item, cost in _requirements(fact):
            event = _event(item)
            if event:
                index[("event", event)].append((fact, input_index, item, cost))
            if (
                (cost or fact["kind"] == "activated")
                and event == "discard"
                and item.get("from_zone") == "hand"
            ):
                index[("resource", "hand_card")].append((fact, input_index, item, cost))
    coverage["indexed_requirements"] = sum(map(len, index.values()))
    seen: set[str] = set()

    def edges(fact: dict):
        for output_index, output, output_cost in _emissions(fact):
            event = _event(output)
            keys = [("event", event)] if event and event != "activate" else []
            if event == "put_into_hand" and output.get("to_zone") == "hand":
                keys.append(("resource", "hand_card"))
            for key in keys:
                for target, input_index, required, input_cost in index.get(key, ()):
                    if output_cost and target["fact_id"] == fact["fact_id"]:
                        # A payment event is part of this occurrence's AND
                        # requirements, not fuel for its own next activation.
                        continue
                    yield target, output_index, output, output_cost, input_index, required, input_cost, key[
                        0
                    ] == "resource"

    def emit(path: list[dict], links: list[dict], assumptions: list[str], cycle: bool):
        if not links:
            return
        payload = {
            "manifest_fingerprint": fingerprint,
            "links": links,
            "path": [f["fact_id"] for f in path],
        }
        candidate_id = "connection_" + _digest(payload)[:24]
        if candidate_id in seen:
            return
        seen.add(candidate_id)
        all_assumptions = _unique(
            assumptions + [c for f in path for c in f["conditions"]]
        )
        unresolved = [
            "Timing, targets, interruptions, and state changes unmodeled",
            "All costs and hyperedge inputs are joint conditional requirements; no payment established",
            "Output branches remain conditional alternatives; ledger does not establish joint production",
        ]
        unresolved.extend(_contradictions(all_assumptions))
        unresolved.extend(
            f"Condition compatibility unverified: {c}" for c in all_assumptions
        )
        for fact in path:
            unresolved.extend(
                f"Required rule unresolved: {r}"
                for r in fact["required_rules"]
                if r not in rule_names
            )
        if cycle:
            unresolved.extend(
                [
                    "Cycle is not a renewability proof",
                    "Finite fuel (hand, library, mana, tap capacity) unverified",
                    "Unsupported cycle semantics unresolved",
                ]
            )
        trace = [
            row
            for occurrence, fact in enumerate(path)
            for row in _ledger(fact, occurrence)
        ]
        for row in trace:
            row["assumptions"] = list(all_assumptions)
        result["candidates"].append(
            {
                "candidate_id": candidate_id,
                "manifest_fingerprint": fingerprint,
                "fact_ids": _unique([f["fact_id"] for f in path]),
                "source_ids": _unique([f["source_id"] for f in path]),
                "links": deepcopy(links),
                "assumptions": all_assumptions,
                "resource_trace": trace,
                "unresolved": _unique(unresolved),
                "status": "hypothesis",
                "cycle_detected": cycle,
                "verified": False,
            }
        )

    def stopped() -> bool:
        if not current():
            unexplored("cancelled")
            return True
        if len(result["candidates"]) >= limits["max_candidates"]:
            unexplored("max_candidates")
            return True
        if coverage["expansions"] >= limits["max_expansions"]:
            unexplored("max_expansions")
            return True
        return False

    def walk(path: list[dict], links: list[dict], assumptions: list[str]):
        coverage["states_visited"] += 1
        extended = False
        for target, oi, output, oc, ii, required, ic, hand in edges(path[-1]):
            if stopped():
                return
            if len(links) >= limits["max_depth"]:
                unexplored("max_depth")
                break
            coverage["expansions"] += 1
            match = _compare(output, required, sources, hand=hand)
            if match is None:
                continue
            conditions, issues = match
            conditions += path[-1]["conditions"] + target["conditions"]
            if path[-1]["kind"] in {"activated", "spell"} or oc:
                conditions.append(
                    f"Explicitly pay all costs of {path[-1]['fact_id']}; activation/cast only conditional"
                )
            if target["kind"] in {"activated", "spell"} or ic:
                conditions.append(
                    f"Explicitly pay all costs of {target['fact_id']}; activation/cast only conditional"
                )
            # The joined requirement is not a substitute for any other input
            # or cost. Every one is retained verbatim in the resource ledger.
            for ri, requirement, is_cost in _requirements(target):
                if ri == ii and is_cost == ic:
                    continue
                conditions.append(
                    f"Joint {'cost' if is_cost else 'input'} {target['fact_id']}:{ri} required: {_canonical(requirement)}"
                )
            link = {
                "from_fact_id": path[-1]["fact_id"],
                "to_fact_id": target["fact_id"],
                "output_index": oi,
                "input_index": ii,
                "status": "proposed",
                "output_kind": "cost" if oc else "output",
                "input_kind": "cost" if ic else "input",
                "join_kind": "resource" if hand else "event",
                "conditions": _unique(conditions),
                "issues": issues,
            }
            extended = True
            next_path, next_links = path + [target], links + [link]
            shared = _unique(assumptions + conditions)
            if any(f["fact_id"] == target["fact_id"] for f in path):
                emit(next_path, next_links, shared, True)
            else:
                walk(next_path, next_links, shared)
            if stopped():
                return
        if not extended:
            emit(path, links, assumptions, False)

    for fact in facts:
        if stopped():
            break
        seed = list(fact["conditions"])
        seed.extend(
            f"Initial joint input {fact['fact_id']}:{i} required: {_canonical(item)}"
            for i, item in enumerate(fact["inputs"])
        )
        if fact["kind"] in {"activated", "spell"} or fact["costs"]:
            seed.append(
                f"Explicitly pay all costs of {fact['fact_id']}; activation/cast only conditional"
            )
        walk([fact], [], seed)
    return result
