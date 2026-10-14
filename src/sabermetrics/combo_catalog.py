"""Validated offline Commander Spellbook evidence, never executable rules proof."""

# Schema validation exposes a uniform ValueError contract to the importer.
# ruff: noqa: TRY004
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sabermetrics.deck_evidence import _library_entries

SNAPSHOT_VERSION = "commander-spellbook.snapshot.v1"
MAX_VARIANTS = 2000
MAX_SNAPSHOT_BYTES = 16_000_000
MAX_VARIANT_BYTES = 100_000
MAX_OUTPUT_BYTES = 10_000
STATE_FIELDS = (
    "battlefieldCardState",
    "exileCardState",
    "graveyardCardState",
    "libraryCardState",
)
ZONES = {"H", "B", "G", "E", "L", "C"}


def parse_snapshot_json(raw: str) -> Any:
    """Reject duplicate keys/nonfinite constants instead of silently overwriting."""
    if len(raw.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds storage byte bound")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"Nonfinite JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Snapshot must contain finite JSON values") from exc


def _text(value: Any, field: str, maximum: int, *, nonempty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (nonempty and not value.strip())
    ):
        raise ValueError(f"Invalid {field}")
    return value


def _positive(value: Any, field: str, maximum: int = 100) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"Invalid {field}")
    return value


def _date(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("Timezone-aware retrieved_at required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid retrieved_at") from exc
    if parsed.tzinfo is None or parsed.year < 2000:
        raise ValueError("Timezone-aware retrieved_at from year 2000 onward required")
    try:
        return parsed.astimezone(UTC).isoformat()
    except (OverflowError, ValueError) as exc:
        raise ValueError("retrieved_at outside representable UTC range") from exc


def _requirement(use: Any, *, template: bool = False) -> dict[str, Any]:
    if not isinstance(use, dict):
        raise ValueError("Card/template requirement must be an object")
    key = "template" if template else "card"
    obj = use.get(key)
    if not isinstance(obj, dict):
        raise ValueError(f"Missing {key} object")
    normalized: dict[str, Any] = {
        "quantity": _positive(use.get("quantity"), "quantity"),
        key: {"name": _text(obj.get("name"), f"{key} name", 200, nonempty=True)},
    }
    if template:
        normalized[key]["id"] = _positive(obj.get("id"), "template id", 10_000_000)
    else:
        oid = obj.get("oracleId")
        if not isinstance(oid, str):
            raise ValueError("Canonical Oracle UUID string required")
        try:
            parsed = UUID(oid) if isinstance(oid, str) else None
        except ValueError as exc:
            raise ValueError("Invalid Oracle UUID") from exc
        if parsed is None or str(parsed) != oid.lower() or parsed.int == 0:
            raise ValueError("Canonical nonzero Oracle UUID required")
        normalized[key]["oracleId"] = str(parsed)
    zones = use.get("zoneLocations")
    if (
        not isinstance(zones, list)
        or len(zones) > 6
        or any(not isinstance(z, str) or z not in ZONES for z in zones)
        or len(zones) != len(set(zones))
    ):
        raise ValueError("Invalid required zones")
    commander = use.get("mustBeCommander")
    if type(commander) is not bool:
        raise ValueError("mustBeCommander must be boolean")
    normalized.update(zoneLocations=zones, mustBeCommander=commander)
    for field in STATE_FIELDS:
        normalized[field] = _text(use.get(field), field, 10_000)
    return normalized


def _identity(value: Any) -> str | None:
    # Spellbook represents a known colorless identity with C, not a mana pip.
    if value == "C":
        return ""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or any(c not in "WUBRG" for c in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Invalid source color identity")
    return "".join(c for c in "WUBRG" if c in value)


def _commander_colors(entries: list[dict[str, Any]]) -> set[str] | None:
    commanders = [e for e in entries if e.get("is_commander")]
    if not commanders:
        return None
    colors: set[str] = set()
    for card in commanders:
        raw = card.get("color_identity")
        if isinstance(raw, str):
            try:
                raw = parse_snapshot_json(raw)
            except ValueError:
                return None
        if not isinstance(raw, list) or any(
            not isinstance(c, str) or c not in "WUBRG" or len(c) != 1 for c in raw
        ):
            return None
        colors.update(raw)
    return colors


def _variant(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Variant must be an object")
    identifier = _text(row.get("id"), "variant id", 80, nonempty=True)
    if not re.fullmatch(r"[0-9]+(?:--?[0-9]+)*", identifier):
        raise ValueError("Invalid variant identity")
    status = _text(row.get("status"), "variant status", 20, nonempty=True)
    if type(row.get("spoiler")) is not bool:
        raise ValueError("spoiler must be boolean")
    uses, templates, produces = (
        row.get("uses"),
        row.get("requires"),
        row.get("produces"),
    )
    if not isinstance(uses, list) or not 1 <= len(uses) <= 20:
        raise ValueError("Each variant requires 1..20 named card requirements")
    if (
        not isinstance(templates, list)
        or len(templates) > 20
        or not isinstance(produces, list)
        or len(produces) > 30
    ):
        raise ValueError("Invalid bounded template or outcome list")
    result = {
        "id": identifier,
        "status": status,
        "spoiler": row["spoiler"],
        "uses": [_requirement(u) for u in uses],
        "requires": [_requirement(t, template=True) for t in templates],
        "produces": [],
    }
    result["identity"] = _identity(row.get("identity"))
    legality = row.get("legalities")
    if legality is not None and not isinstance(legality, dict):
        raise ValueError("Source legalities must be an object")
    commander_legal = legality.get("commander") if legality is not None else None
    if commander_legal is not None and type(commander_legal) is not bool:
        raise ValueError("Source Commander legality must be boolean or unknown")
    result["legalities"] = {"commander": commander_legal}
    canonical_names: dict[str, str] = {}
    for use in result["uses"]:
        oid, name = use["card"]["oracleId"], use["card"]["name"]
        if (oid in canonical_names and canonical_names[oid] != name) or (
            name in canonical_names.values() and canonical_names.get(oid) != name
        ):
            raise ValueError("Inconsistent canonical card names")
        canonical_names[oid] = name
    for outcome in produces:
        if not isinstance(outcome, dict) or not isinstance(
            outcome.get("feature"), dict
        ):
            raise ValueError("Outcome requires a feature object")
        result["produces"].append(
            {
                "feature": {
                    "name": _text(
                        outcome["feature"].get("name"),
                        "outcome name",
                        300,
                        nonempty=True,
                    )
                },
                "quantity": _positive(
                    outcome.get("quantity"), "outcome quantity", 1_000_000
                ),
            }
        )
    for field in (
        "manaNeeded",
        "easyPrerequisites",
        "notablePrerequisites",
        "description",
    ):
        result[field] = _text(row.get(field), field, 50_000)
    if len(_json(row).encode()) > MAX_VARIANT_BYTES:
        raise ValueError("Variant exceeds storage byte bound")
    return result


def import_combo_snapshot(path: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate completely before atomically replacing only newer source rows."""
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("snapshot_version") != SNAPSHOT_VERSION
    ):
        raise ValueError(f"snapshot_version must be {SNAPSHOT_VERSION}")
    retrieved = _date(snapshot.get("retrieved_at"))
    source_url = snapshot.get(
        "source_url", "https://backend.commanderspellbook.com/variants/"
    )
    if (
        not isinstance(source_url, str)
        or len(source_url) > 1000
        or not source_url.startswith("https://backend.commanderspellbook.com/variants/")
    ):
        raise ValueError("Official variants source_url required")
    rows = snapshot.get("results")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_VARIANTS:
        raise ValueError("Snapshot requires 1..2000 variants")
    raw_snapshot = _json(snapshot)
    if len(raw_snapshot.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds storage byte bound")
    normalized = [_variant(row) for row in rows]
    if len({row["id"] for row in normalized}) != len(normalized):
        raise ValueError("Duplicate variant identity")
    digest = hashlib.sha256(raw_snapshot.encode()).hexdigest()
    imported = skipped_older = skipped_same = skipped_ineligible = 0
    with sqlite3.connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "CREATE TABLE IF NOT EXISTS assistant_combo_snapshots (sha256 TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL, imported_at TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS assistant_combo_variants (id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        con.execute(
            "INSERT OR IGNORE INTO assistant_combo_snapshots VALUES (?,?,?,?)",
            (digest, retrieved, datetime.now(UTC).isoformat(), raw_snapshot),
        )
        for row in normalized:
            identifier, payload = row["id"], _json(row)
            old = con.execute(
                "SELECT retrieved_at,payload,snapshot_sha256 FROM assistant_combo_variants WHERE id=?",
                (identifier,),
            ).fetchone()
            if old and datetime.fromisoformat(_date(old[0])) > datetime.fromisoformat(
                retrieved
            ):
                skipped_older += 1
                continue
            if old and _date(old[0]) == retrieved:
                if old[1] != payload:
                    old_projection = parse_snapshot_json(old[1])
                    new_projection = dict(row)
                    for key in ("identity", "legalities"):
                        old_projection.pop(key, None)
                        new_projection.pop(key, None)
                    if old[2] == digest and old_projection == new_projection:
                        con.execute(
                            "UPDATE assistant_combo_variants SET payload=? WHERE id=?",
                            (payload, identifier),
                        )
                        imported += int(row["status"] == "OK" and not row["spoiler"])
                        skipped_ineligible += int(
                            row["status"] != "OK" or row["spoiler"]
                        )
                        continue
                    raise ValueError(
                        "Conflicting variant payload for same retrieval time"
                    )
                skipped_same += 1
                continue
            if row["status"] != "OK" or row["spoiler"]:
                # Keep a newer inactive row as a tombstone so stale imports can
                # neither reactivate it nor leave an obsolete old claim visible.
                skipped_ineligible += 1
            else:
                imported += 1
            con.execute(
                "INSERT INTO assistant_combo_variants VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET snapshot_sha256=excluded.snapshot_sha256,retrieved_at=excluded.retrieved_at,payload=excluded.payload",
                (identifier, digest, retrieved, payload),
            )
    return {
        "imported_variants": imported,
        "skipped_variants": skipped_older + skipped_same + skipped_ineligible,
        "skipped_older": skipped_older,
        "skipped_same": skipped_same,
        "skipped_ineligible": skipped_ineligible,
        "validated_variants": len(normalized),
        "snapshot_sha256": digest,
        "retrieved_at": retrieved,
        "snapshot_version": SNAPSHOT_VERSION,
    }


def _state_excerpt(requirement: dict[str, Any]) -> dict[str, Any]:
    states = {key: requirement[key][:60] for key in STATE_FIELDS if requirement[key]}
    return {
        "zones": requirement["zoneLocations"],
        "must_be_commander": requirement["mustBeCommander"],
        "state_requirements": states,
        "state_requirements_truncated": any(
            len(requirement[k]) > 60 for k in STATE_FIELDS
        ),
    }


def known_combinations(
    path: str | Path, document: dict[str, Any], *, limit: int = 4
) -> dict[str, Any]:
    """Read only local source evidence; match identity/quantity, never game state."""
    if type(limit) is not int:
        raise ValueError("Integer result limit required")
    limit = min(max(limit, 1), 4)
    entries = _library_entries(document) + [
        c
        for c in document.get("entries", [])
        if isinstance(c, dict) and c.get("is_commander")
    ]
    deck_colors = _commander_colors(entries)
    owned: Counter[str] = Counter()
    commanders = set()
    for card in entries:
        oid, quantity = card.get("oracle_id"), card.get("quantity", 1)
        if not isinstance(oid, str):
            continue
        if type(quantity) is not int or not 0 <= quantity <= 100:
            raise ValueError("Invalid deck card quantity")
        owned[oid.lower()] += quantity
        if card.get("is_commander"):
            commanders.add(oid.lower())
    base = {
        "version": "combo-catalog.v1",
        "execution_verified": False,
        "scope": "Canonical ingredient matching only; source prerequisites, zones, costs and outcomes are unverified external claims.",
        "matches": [],
        "absence_is_not_proof_of_no_combinations": True,
    }
    try:
        with sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True
        ) as con:
            con.execute("PRAGMA query_only=ON")
            rows = con.execute(
                "SELECT id,snapshot_sha256,retrieved_at,payload FROM assistant_combo_variants ORDER BY id LIMIT ?",
                (MAX_VARIANTS + 1,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc) and "unable to open" not in str(exc):
            raise
        return {
            **base,
            "status": "unavailable",
            "reason": "No imported combination corpus.",
        }
    matches = []
    for identifier, digest, retrieved, raw in rows[:MAX_VARIANTS]:
        try:
            variant = _variant(parse_snapshot_json(raw))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid stored combo variant {identifier}") from exc
        if (
            variant["id"] != identifier
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("Stored combo provenance inconsistent")
        _date(retrieved)
        if variant["status"] != "OK" or variant["spoiler"]:
            continue
        if variant["legalities"]["commander"] is False:
            continue
        if (
            deck_colors is not None
            and variant["identity"] is not None
            and not set(variant["identity"]).issubset(deck_colors)
        ):
            continue
        constraint_status = (
            "eligible_source_ingredients"
            if deck_colors is not None
            and variant["identity"] is not None
            and variant["legalities"]["commander"] is True
            else "sourced_constraint_unknown"
        )
        requirements: Counter[str] = Counter()
        names: dict[str, str] = {}
        commander_missing = False
        for use in variant["uses"]:
            oid = use["card"]["oracleId"]
            requirements[oid] += use["quantity"]
            names[oid] = use["card"]["name"]
            commander_missing |= use["mustBeCommander"] and oid not in commanders
        missing = {
            names[oid]: n - owned[oid]
            for oid, n in requirements.items()
            if owned[oid] < n
        }
        if (
            not any(owned[o] for o in requirements)
            or sum(missing.values()) > 1
            or commander_missing
        ):
            continue
        present_distinct = sum(owned[o] > 0 for o in requirements)
        commander_linked = any(o in commanders for o in requirements)
        if missing and present_distinct < 2 and not commander_linked:
            continue
        templates = variant["requires"]
        prerequisites = "\n".join(
            variant[k]
            for k in ("manaNeeded", "easyPrerequisites", "notablePrerequisites")
        ).strip()
        description = variant["description"]
        matches.append(
            {
                "variant_id": identifier,
                "status": "sourced_claim_not_executed",
                "constraint_status": constraint_status,
                "source_color_identity": variant["identity"],
                "source_commander_legal": variant["legalities"]["commander"],
                "commander_ingredient_present": commander_linked,
                "missing_cards_are_recommendations": False,
                "ingredient_match": (
                    "missing_card"
                    if missing
                    else (
                        "unresolved_template"
                        if templates
                        else "all_named_cards_present"
                    )
                ),
                "missing_cards": missing,
                "cards": [
                    {"name": names[o], "oracle_id": o, "quantity": n}
                    for o, n in requirements.items()
                ],
                "unresolved_template_count": len(templates),
                "unresolved_templates": [
                    {
                        "name": t["template"]["name"][:100],
                        "quantity": t["quantity"],
                        **_state_excerpt(t),
                    }
                    for t in templates[:3]
                ],
                "templates_omitted_count": max(0, len(templates) - 3),
                "required_zones": [
                    {"name": u["card"]["name"], **_state_excerpt(u)}
                    for u in variant["uses"]
                ],
                "prerequisite_excerpt": prerequisites[:300],
                "prerequisites_truncated": len(prerequisites) > 300,
                "step_excerpt": description[:500],
                "steps_truncated": len(description) > 500,
                "claimed_outcomes": [
                    c["feature"]["name"][:100] for c in variant["produces"][:4]
                ],
                "outcomes_omitted_count": max(0, len(variant["produces"]) - 4),
                "outcome_excerpts_truncated": any(
                    len(c["feature"]["name"]) > 100 for c in variant["produces"][:4]
                ),
                "citation": {
                    "source": "Commander Spellbook",
                    "url": f"https://commanderspellbook.com/combo/{identifier}/",
                    "retrieved_at": retrieved,
                    "snapshot_sha256": digest,
                    "freshness": "not_verified_current",
                },
                "execution_verified": False,
            }
        )
    matches.sort(
        key=lambda m: (
            m["constraint_status"] != "eligible_source_ingredients",
            sum(m["missing_cards"].values()),
            not m["commander_ingredient_present"],
            m["unresolved_template_count"],
            len(m["cards"]),
            m["variant_id"],
        )
    )
    result: dict[str, Any] = {
        **base,
        "status": "matched" if matches else "no_match",
        "matches": [],
        "omitted_match_count": len(matches),
        "catalog_scan_truncated": len(rows) > MAX_VARIANTS,
        "corpus_variants_examined": min(len(rows), MAX_VARIANTS),
        "output_byte_budget": MAX_OUTPUT_BYTES,
        "output_budget_truncated": False,
    }
    for match in matches[:limit]:
        candidate = {
            **result,
            "matches": result["matches"] + [match],
            "omitted_match_count": len(matches) - len(result["matches"]) - 1,
        }
        if len(_json(candidate).encode()) > MAX_OUTPUT_BYTES:
            result["output_budget_truncated"] = True
            continue
        result = candidate
    return result
