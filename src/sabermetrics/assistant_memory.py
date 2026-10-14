"""Bounded, read-only research memory for the Build assistant.

Memory recalls what an owner said they wanted (saved assistant preferences)
and what past experiments actually measured for one deck. It never writes:
the database is opened with ``mode=ro`` so a read cannot create, migrate or
expire anything. Every recalled experiment is revalidated against the current
deck hash, because a measurement of an earlier list says nothing about the
list that exists now.

Large experiment payloads (frozen documents and native reports) never reach
Python: SQLite JSON functions extract only the compact fields used here, and
each extracted blob has a length ceiling. Comparisons stay descriptive. An
adopted alternative records a user choice, not an improvement, and no field
in this module states that one list beat another.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabermetrics.assistant_experiments import EXPERIMENT_EXPIRY_SECONDS
from sabermetrics.deck_simulation import candidate_from_document, user_edited_provenance

MEMORY_VERSION = "assistant-research-memory.v1"
MAX_LIMIT = 5
MAX_ALTERNATIVES = 3
MAX_REPLACEMENTS = 3
MAX_METRIC_ROWS = 6
MAX_PROTECTED_CARDS = 20
MAX_DECK_ENTRIES = 400
# A comparison or validation summary above this is skipped, never parsed.
MAX_SUMMARY_CHARS = 65_536
MAX_TEXT = 200
MAX_THEME = 300
MAX_MEMORY_CHARS = 24_000

NO_VERDICT = (
    "Recalled comparisons are descriptive differences only. No experiment here "
    "establishes that one list is better, and an adopted alternative records a "
    "user choice, not a measured improvement."
)
PREFERENCES_NOTE = (
    "Saved preferences are user-stated intent; theme and power level are unverified."
)

_EXPERIMENT_SQL = """
SELECT e.id, e.created_at, e.state,
       substr(json_extract(e.payload_json,'$.status'),1,40) AS status,
       json_extract(e.payload_json,'$.baseline.revision') AS baseline_revision,
       substr(json_extract(e.payload_json,'$.baseline.deck_sha256'),1,128) AS baseline_sha,
       substr(json_extract(e.payload_json,'$.adopted_alternative_id'),1,128) AS adopted_id,
       substr(json_extract(e.payload_json,'$.error'),1,200) AS error,
       substr(json_extract(e.payload_json,'$.expiry_reason'),1,80) AS expiry_reason,
       substr(json_extract(e.payload_json,'$.constraints.theme'),1,300) AS theme,
       substr(json_extract(e.payload_json,'$.constraints.power_level'),1,200) AS power_level,
       json_extract(e.payload_json,'$.constraints.budget') AS budget,
       json_array_length(e.payload_json,'$.constraints.protected_cards') AS protected_count,
       json_extract(e.payload_json,'$.settings.games') AS games,
       json_array_length(e.payload_json,'$.alternatives') AS alternative_count,
       length(e.payload_json) AS payload_chars
FROM assistant_experiments e
WHERE e.owner_id=? AND e.deck_id=? AND json_valid(e.payload_json)
ORDER BY e.created_at DESC, e.id DESC LIMIT ?
"""

_ALTERNATIVE_SQL = """
SELECT CAST(a.key AS INTEGER) AS position,
       substr(json_extract(a.value,'$.id'),1,128) AS id,
       substr(json_extract(a.value,'$.name'),1,200) AS name,
       substr(json_extract(a.value,'$.status'),1,40) AS status,
       substr(json_extract(a.value,'$.deck_sha256'),1,128) AS deck_sha256,
       substr(json_extract(a.value,'$.error'),1,200) AS error,
       substr(json_extract(a.value,'$.replacements'),1,4000) AS replacements,
       length(json_extract(a.value,'$.comparison')) AS comparison_chars,
       CASE WHEN length(json_extract(a.value,'$.comparison')) <= ?
            THEN json_extract(a.value,'$.comparison') END AS comparison,
       length(json_extract(a.value,'$.validation')) AS validation_chars,
       CASE WHEN length(json_extract(a.value,'$.validation')) <= ?
            THEN json_extract(a.value,'$.validation') END AS validation
FROM assistant_experiments e, json_each(e.payload_json,'$.alternatives') a
WHERE e.id=? AND e.owner_id=?
ORDER BY CAST(a.key AS INTEGER) LIMIT ?
"""


def _connect_read_only(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise sqlite3.OperationalError("database file is unavailable")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")
    return conn


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age_days(value: Any, now: datetime) -> int | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds() // 86400))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _current_deck(
    conn: sqlite3.Connection, owner_id: str, deck_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, revision, updated_at FROM deck_documents WHERE id=? AND owner_id=?",
        (deck_id, owner_id),
    ).fetchone()
    if row is None:
        return None
    entries = [
        dict(entry)
        for entry in conn.execute(
            "SELECT e.card_id, e.oracle_id, e.name, e.quantity, e.is_commander, e.role, "
            "e.color_identity, COALESCE(z.name,'Unsorted') AS zone_name "
            "FROM deck_entries e LEFT JOIN deck_zones z ON z.id=e.zone_id "
            "WHERE e.deck_id=? ORDER BY e.id LIMIT ?",
            (deck_id, MAX_DECK_ENTRIES + 1),
        ).fetchall()
    ]
    deck: dict[str, Any] = {
        "deck_id": deck_id,
        "revision": row["revision"],
        "updated_at": _text(row["updated_at"], 40),
        "deck_sha256": None,
        "hash_status": "available",
    }
    if len(entries) > MAX_DECK_ENTRIES:
        deck["hash_status"] = "unavailable: deck has too many entries to verify"
    else:
        try:
            deck["deck_sha256"] = candidate_from_document(
                {"id": deck_id, "entries": entries}, user_edited_provenance()
            ).deck_sha256
        except ValueError as exc:
            # Pydantic validation errors are ValueErrors as well.
            first = str(exc).splitlines()[0] if str(exc) else "invalid list"
            deck["hash_status"] = (
                "unavailable: the current list is not a commander plus 99 cards "
                f"({first[:120]})"
            )
    deck["_card_names"] = {
        str(entry["card_id"]): _text(entry["name"])
        for entry in entries
        if entry.get("card_id")
    }
    return deck


def _preferences(
    conn: sqlite3.Connection, owner_id: str, deck_id: str, card_names: dict[str, Any]
) -> dict[str, Any]:
    if not _table_exists(conn, "build_assistant_sessions"):
        return {"status": "no_history", "note": PREFERENCES_NOTE}
    row = conn.execute(
        "SELECT substr(json_extract(payload_json,'$.mode'),1,40) AS mode, "
        "substr(json_extract(payload_json,'$.constraints'),1,8000) AS constraints, "
        "json_extract(payload_json,'$.updated_at') AS updated_at "
        "FROM build_assistant_sessions WHERE deck_id=? AND owner_id=? "
        "AND json_valid(payload_json)",
        (deck_id, owner_id),
    ).fetchone()
    if row is None:
        return {"status": "no_history", "note": PREFERENCES_NOTE}
    try:
        raw = json.loads(row["constraints"] or "{}")
    except json.JSONDecodeError:
        return {
            "status": "unavailable",
            "reason": "Saved preferences are oversized or malformed.",
            "note": PREFERENCES_NOTE,
        }
    raw = raw if isinstance(raw, dict) else {}
    protected_raw = raw.get("protected_cards")
    protected = [
        str(card)[:100]
        for card in (protected_raw if isinstance(protected_raw, list) else [])
    ]
    constraints = {
        "budget": _number(raw.get("budget")),
        "power_level": _text(raw.get("power_level")) or "",
        "theme": _text(raw.get("theme"), MAX_THEME) or "",
        "protected_cards": [
            {
                "card_id": card,
                "name": card_names.get(card),
                "in_current_deck": card in card_names,
            }
            for card in protected[:MAX_PROTECTED_CARDS]
        ],
        "protected_cards_total": len(protected),
    }
    mode = row["mode"] if row["mode"] in {"suggest", "automate"} else None
    stated = bool(
        constraints["budget"] is not None
        or constraints["power_level"]
        or constraints["theme"]
        or protected
        or mode == "automate"
    )
    updated = _number(row["updated_at"])
    return {
        # A conversation row is created on first view, so its presence alone
        # is not a preference the user stated.
        "status": "saved" if stated else "default",
        "mode": mode,
        "constraints": constraints,
        "updated_at": (
            datetime.fromtimestamp(updated, UTC).isoformat()
            if updated is not None and 0 < updated < 1e11
            else None
        ),
        "verification": "user_stated_unverified",
        "note": PREFERENCES_NOTE,
    }


def _entry_names(
    conn: sqlite3.Connection,
    owner_id: str,
    experiment_id: str,
    path: str,
    ids: list[str],
) -> dict[str, str | None]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT substr(json_extract(d.value,'$.id'),1,300) AS id, "
        "substr(json_extract(d.value,'$.name'),1,200) AS name "
        f"FROM assistant_experiments e, json_each(e.payload_json, ?) d "
        f"WHERE e.id=? AND e.owner_id=? AND json_extract(d.value,'$.id') IN ({marks}) "
        "LIMIT ?",
        (path, experiment_id, owner_id, *ids, len(ids)),
    ).fetchall()
    return {str(row["id"]): row["name"] for row in rows}


def _metric_rows(
    comparison: dict[str, Any], validation: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], int, bool]:
    held_out: dict[tuple[str, str], dict[str, Any]] = {}
    if isinstance(validation, dict):
        nested = validation.get("comparison")
        deltas = nested.get("metric_deltas") if isinstance(nested, dict) else None
        for test, metrics in (deltas if isinstance(deltas, dict) else {}).items():
            for name, metric in (metrics if isinstance(metrics, dict) else {}).items():
                if isinstance(metric, dict):
                    held_out[(str(test), str(name))] = metric
    rows: list[dict[str, Any]] = []
    deltas = comparison.get("metric_deltas")
    for test, metrics in (deltas if isinstance(deltas, dict) else {}).items():
        for name, metric in (metrics if isinstance(metrics, dict) else {}).items():
            if not isinstance(metric, dict):
                continue
            check = held_out.get((str(test), str(name)), {})
            rows.append(
                {
                    "test": str(test)[:80],
                    "metric": str(name)[:80],
                    "unit": _text(metric.get("unit"), 40),
                    "before": _number(metric.get("before")),
                    "after": _number(metric.get("after")),
                    "delta": (
                        _number(metric.get("delta"))
                        if metric.get("comparable") is True
                        else None
                    ),
                    "comparable": metric.get("comparable") is True,
                    "held_out_validated": check.get("validated") is True,
                    "reason": _text(
                        metric.get("reason") or check.get("unvalidated_reason"), 240
                    )
                    or "",
                }
            )
    rows.sort(key=lambda row: (row["test"], row["metric"]))
    # Decide from all actual metrics before bounding the presentation rows.
    validated = any(row["held_out_validated"] and row["comparable"] for row in rows)
    return rows[:MAX_METRIC_ROWS], len(rows), validated


def _load_summary(raw: Any, chars: Any) -> tuple[dict[str, Any] | None, str]:
    if chars is None:
        return None, "absent"
    if raw is None:
        return None, "oversized"
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None, "malformed"
    return (value, "present") if isinstance(value, dict) else (None, "malformed")


def _alternative(
    row: sqlite3.Row,
    current_sha: str | None,
    adopted_id: str | None,
    outgoing_names: dict[str, str | None],
    incoming_names: dict[str, str | None],
) -> dict[str, Any]:
    try:
        replacements_raw = json.loads(row["replacements"] or "[]")
    except json.JSONDecodeError:
        replacements_raw = []
    replacements = []
    for item in (replacements_raw if isinstance(replacements_raw, list) else [])[
        :MAX_REPLACEMENTS
    ]:
        if not isinstance(item, dict):
            continue
        outgoing = str(item.get("outgoing_entry_id") or "")[:128]
        incoming = str(item.get("incoming_card_id") or "")[:128]
        replacements.append(
            {
                "outgoing_entry_id": outgoing,
                "outgoing_name": outgoing_names.get(outgoing),
                "incoming_card_id": incoming,
                "incoming_name": incoming_names.get(
                    f"alternative-{outgoing}-{incoming}"
                ),
            }
        )
    comparison, comparison_state = _load_summary(
        row["comparison"], row["comparison_chars"]
    )
    validation, validation_state = _load_summary(
        row["validation"], row["validation_chars"]
    )
    status = str(row["status"] or "unknown")
    result: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "status": status,
        "adopted": bool(adopted_id) and adopted_id == row["id"],
        "matches_current_deck": bool(current_sha) and row["deck_sha256"] == current_sha,
        "replacements": replacements,
        "measured_outcome": "not_measured",
        "comparison_reason": "",
        "metrics": [],
        "metrics_total": 0,
        "verdict": "none",
    }
    if status != "done":
        result["comparison_reason"] = (
            _text(row["error"], 200)
            or f"Alternative status is {status}; no completed measurement exists."
        )
        return result
    if comparison is None:
        result["measured_outcome"] = "unavailable"
        result["comparison_reason"] = (
            f"Stored comparison is {comparison_state}; it was not read."
        )
        return result
    if comparison.get("comparable") is not True:
        result["measured_outcome"] = "noncomparable"
        result["comparison_reason"] = (
            _text(comparison.get("reason"), 240) or "Comparison is not comparable."
        )
        return result
    rows, total, any_validated = _metric_rows(comparison, validation)
    result["metrics"] = rows
    result["metrics_total"] = total
    if validation is None:
        result["measured_outcome"] = "descriptive_training_only"
        result["comparison_reason"] = (
            f"Held-out validation is {validation_state}; training deltas are descriptive only."
        )
    elif any_validated:
        result["measured_outcome"] = "descriptive_held_out"
    else:
        result["measured_outcome"] = "descriptive_unvalidated"
        result["comparison_reason"] = (
            "No metric passed held-out validation checks; differences are descriptive only."
        )
    return result


def _experiment(
    conn: sqlite3.Connection,
    owner_id: str,
    row: sqlite3.Row,
    deck: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    status = str(row["status"] or row["state"] or "unknown")
    created = _parse_time(row["created_at"])
    if status in {"queued", "running"} and (
        created is None or (now - created).total_seconds() > EXPERIMENT_EXPIRY_SECONDS
    ):
        # Reported as expired without writing; the experiment service owns expiry.
        status = "interrupted_expired"
    current_sha = deck["deck_sha256"]
    baseline_sha = row["baseline_sha"]
    revision_same = row["baseline_revision"] == deck["revision"]
    if not current_sha or not baseline_sha:
        freshness = "unverifiable"
    elif baseline_sha != current_sha:
        freshness = "stale"
    else:
        freshness = "current" if revision_same else "same_content_new_revision"
    alternatives_rows = conn.execute(
        _ALTERNATIVE_SQL,
        (
            MAX_SUMMARY_CHARS,
            MAX_SUMMARY_CHARS,
            row["id"],
            owner_id,
            MAX_ALTERNATIVES,
        ),
    ).fetchall()
    outgoing_ids: list[str] = []
    for alternative in alternatives_rows:
        try:
            items = json.loads(alternative["replacements"] or "[]")
        except json.JSONDecodeError:
            items = []
        for item in (items if isinstance(items, list) else [])[:MAX_REPLACEMENTS]:
            if isinstance(item, dict) and item.get("outgoing_entry_id"):
                outgoing_ids.append(str(item["outgoing_entry_id"])[:128])
    outgoing_names = _entry_names(
        conn,
        owner_id,
        row["id"],
        "$.baseline.document.entries",
        sorted(set(outgoing_ids)),
    )
    alternatives = []
    for alternative in alternatives_rows:
        try:
            items = json.loads(alternative["replacements"] or "[]")
        except json.JSONDecodeError:
            items = []
        added_ids = [
            f"alternative-{str(item.get('outgoing_entry_id') or '')[:128]}-"
            f"{str(item.get('incoming_card_id') or '')[:128]}"
            for item in (items if isinstance(items, list) else [])[:MAX_REPLACEMENTS]
            if isinstance(item, dict)
        ]
        incoming_names = _entry_names(
            conn,
            owner_id,
            row["id"],
            f"$.alternatives[{int(alternative['position'])}].document.entries",
            added_ids,
        )
        alternatives.append(
            _alternative(
                alternative,
                current_sha,
                row["adopted_id"],
                outgoing_names,
                incoming_names,
            )
        )
    warnings = []
    if freshness == "stale":
        warnings.append(
            f"Measured against revision {row['baseline_revision']}; the deck list has "
            "changed since, so these measurements do not describe the current list."
        )
    elif freshness == "unverifiable":
        warnings.append(
            "Freshness cannot be verified against the current list; treat as historical."
        )
    if any(item["measured_outcome"] == "noncomparable" for item in alternatives):
        warnings.append(
            "At least one alternative is not comparable to its baseline; no difference is reported for it."
        )
    if row["adopted_id"]:
        warnings.append(
            "An alternative was adopted. Adoption is a user choice, not evidence of improvement."
        )
    return {
        "id": row["id"],
        "created_at": _text(row["created_at"], 40),
        "age_days": _age_days(row["created_at"], now),
        "status": status,
        "error": _text(row["error"], 200) or "",
        "baseline_revision": row["baseline_revision"],
        "freshness": freshness,
        "applies_to_current_deck": freshness
        in {"current", "same_content_new_revision"},
        "constraints": {
            "theme": row["theme"] or "",
            "power_level": row["power_level"] or "",
            "budget": _number(row["budget"]),
            "protected_cards_total": int(row["protected_count"] or 0),
            "verification": "user_stated_unverified",
        },
        "games": _number(row["games"]),
        "alternatives": alternatives,
        "alternatives_total": int(row["alternative_count"] or 0),
        "adopted_alternative_id": row["adopted_id"],
        "payload_chars": int(row["payload_chars"] or 0),
        "warnings": warnings,
    }


def _shrink(result: dict[str, Any]) -> None:
    """Drop metric detail, then oldest experiments, until the bound holds."""
    if len(json.dumps(result)) <= MAX_MEMORY_CHARS:
        return
    for experiment in result["experiments"]["items"]:
        for alternative in experiment["alternatives"]:
            alternative["metrics"] = alternative["metrics"][:2]
    while len(json.dumps(result)) > MAX_MEMORY_CHARS and result["experiments"]["items"]:
        result["experiments"]["items"].pop()
        result["experiments"]["truncated"] = True
    result["experiments"]["returned"] = len(result["experiments"]["items"])


def research_memory(
    db_path: str | Path,
    owner_id: str,
    deck_id: str,
    limit: int = 3,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recall saved preferences and recent experiments for one owned deck.

    Args:
        db_path: SQLite database; opened read-only and never created.
        owner_id: Requesting owner. Another owner's deck reads as not found.
        deck_id: Deck whose memory is recalled.
        limit: Most recent experiments to return, 1..5.
        now: Clock override for deterministic ages and expiry.

    Returns:
        A bounded dict with ``status`` of ``available``, ``no_history``,
        ``not_found`` or ``unavailable``. Experiments carry ``freshness`` and
        ``measured_outcome``; neither ever states an improvement.

    Raises:
        ValueError: ``limit`` is outside 1..5.
    """
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_LIMIT
    ):
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    now = now or datetime.now(UTC)
    base: dict[str, Any] = {
        "version": MEMORY_VERSION,
        "status": "unavailable",
        "deck_id": str(deck_id)[:128],
        "improvement_claims": "none",
        "limitations": [NO_VERDICT],
    }
    try:
        conn = _connect_read_only(db_path)
    except sqlite3.Error:
        base["reason"] = "Research memory database is unavailable."
        return base
    try:
        if not _table_exists(conn, "deck_documents"):
            base["reason"] = "Deck storage is unavailable."
            return base
        deck = _current_deck(conn, owner_id, deck_id)
        if deck is None:
            # Missing and foreign decks are indistinguishable by design.
            base.update(status="not_found", reason="Deck not found.")
            return base
        card_names = deck.pop("_card_names")
        preferences = _preferences(conn, owner_id, deck_id, card_names)
        experiments: dict[str, Any] = {
            "status": "no_history",
            "returned": 0,
            "total_for_deck": 0,
            "items": [],
        }
        if _table_exists(conn, "assistant_experiments"):
            experiments["total_for_deck"] = conn.execute(
                "SELECT COUNT(*) FROM assistant_experiments WHERE owner_id=? AND deck_id=?",
                (owner_id, deck_id),
            ).fetchone()[0]
            rows = conn.execute(_EXPERIMENT_SQL, (owner_id, deck_id, limit)).fetchall()
            experiments["items"] = [
                _experiment(conn, owner_id, row, deck, now) for row in rows
            ]
            experiments["returned"] = len(experiments["items"])
            if experiments["items"]:
                experiments["status"] = "available"
            elif experiments["total_for_deck"]:
                experiments["status"] = "unavailable"
                experiments["reason"] = "Stored experiments are malformed."
    except sqlite3.Error:
        base["reason"] = "Research memory could not be read."
        return base
    finally:
        conn.close()
    warnings: list[str] = []
    if deck["deck_sha256"] is None:
        warnings.append(
            "The current list cannot be hashed, so no experiment can be confirmed current."
        )
    for item in experiments["items"]:
        warnings.extend(item["warnings"])
    result = {
        **base,
        "status": (
            "available"
            if experiments["items"] or preferences["status"] == "saved"
            else "no_history"
        ),
        "deck": deck,
        "preferences": preferences,
        "experiments": experiments,
        "warnings": list(dict.fromkeys(warnings))[:10],
    }
    _shrink(result)
    return result
