"""Deterministic Build research assistant over native diagnostics and evidence.

This module makes no model calls. Simulator observations and tournament-list
alternatives remain separate so the latter are never presented as measured
causes or as proof of improvement.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from sabermetrics.deck_evidence import DeckEvidenceService

TEST_LABELS = {
    "opening": "Opening hand",
    "development": "Development",
    "commander_loss": "Commander recovery",
    "creature_wipe": "Creature wipe recovery",
    "finish": "Turn-seven finish",
}


def findings(diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn supplied native report values and limitations into readable facts."""
    tests = diagnostics.get("tests")
    if not isinstance(tests, dict):
        return []
    result: list[dict[str, Any]] = []
    for key, label in TEST_LABELS.items():
        test = tests.get(key)
        if not isinstance(test, dict):
            continue
        status = str(test.get("status") or "unsupported")
        metrics = test.get("metrics") if isinstance(test.get("metrics"), dict) else {}
        observed = []
        for metric_name, metric in metrics.items():
            if not isinstance(metric, dict):
                continue
            value = metric.get("value")
            if value is None:
                shown = "unmeasured"
            elif isinstance(value, (float, int)) and not isinstance(value, bool):
                shown = f"{value:g} {metric.get('unit', '')}".strip()
            else:
                continue
            denominator = metric.get("denominator")
            if isinstance(denominator, int) and denominator > 0:
                shown += f" ({denominator} games)"
            observed.append(
                {
                    "metric": str(metric_name),
                    "value": value,
                    "unit": str(metric.get("unit") or ""),
                    "summary": shown,
                    "numerator": metric.get("numerator"),
                    "denominator": denominator,
                    "ci95": metric.get("ci95"),
                }
            )
        limitations = [str(item) for item in test.get("limitations", []) if str(item)]
        evidence = [str(item) for item in test.get("evidence", []) if str(item)]
        result.append(
            {
                "id": key,
                "title": label,
                "status": status,
                "metrics": observed,
                "limitations": limitations,
                "evidence": evidence,
            }
        )
    return result


def recommendations(document: dict[str, Any], evidence: DeckEvidenceService,
                    diagnostics: dict[str, Any] | None = None,
                    window_days: int = 30, limit: int = 3) -> list[dict[str, Any]]:
    """Recommend swaps only for explicit, modeled failure signals.

    Suggestions preserve the target's role and current price ceiling. Unknown
    prices or unmapped metrics produce no recommendation rather than a guess.
    """
    cap = max(0, min(int(limit), 3))
    if not isinstance(diagnostics, dict):
        return []
    metric_roles: dict[str, tuple[str, ...]] = {
        "mean_missed_land_drops": ("land", "ramp"),
        "mean_first_authored_action_turn": ("ramp", "draw"),
    }
    targets: dict[str, set[str]] = {}
    tests = diagnostics.get("tests") if isinstance(diagnostics.get("tests"), dict) else {}
    for test_key, test in tests.items():
        if not isinstance(test, dict) or test.get("status") == "unsupported":
            continue
        metrics = test.get("metrics") if isinstance(test.get("metrics"), dict) else {}
        for raw_name, metric in metrics.items():
            name = str(raw_name).casefold()
            value = metric.get("value") if isinstance(metric, dict) else None
            roles = metric_roles.get(name)
            if not roles or not isinstance(value, (float, int)) or isinstance(value, bool):
                continue
            # These are visible scenario thresholds, not deck-quality grades.
            # Any missed land drop is an observation; first action after the
            # turn-five development checkpoint is a delayed-action observation.
            is_failure = (name == "mean_missed_land_drops" and value > 0) or (
                name == "mean_first_authored_action_turn" and value > 5
            )
            if is_failure and value > 0:
                targets.setdefault(str(test_key), set()).update(roles)
    role_priority = {role for roles in targets.values() for role in roles}
    if not role_priority:
        return []
    found: list[dict[str, Any]] = []
    used_incoming: set[str] = set()
    entries = [entry for entry in document.get("entries", [])
               if isinstance(entry, dict) and not entry.get("is_commander")
               and entry.get("oracle_id") and entry.get("id")]
    for entry in entries:
        if len(found) >= cap:
            break
        role = str(entry.get("role") or "").casefold()
        if role not in role_priority or role in {
            "removal",
            "counter",
            "free",
            "protection",
            "wincon",
        }:
            continue
        qty = max(1, int(entry.get("quantity") or 1))
        if qty < 1:
            continue
        alternatives = evidence.alternatives(document, str(entry["oracle_id"]),
                                             window_days, limit=50)
        denominator = alternatives.get("denominator", 0)
        rows = alternatives.get("alternatives", [])
        if not isinstance(rows, list):
            continue
        for card in rows:
            if not isinstance(card, dict) or not card.get("card_id"):
                continue
            incoming_oracle = str(card.get("oracle_id") or "")
            if not incoming_oracle or incoming_oracle in used_incoming:
                continue
            # Replacement candidates must be no more expensive than the card
            # being trimmed. Unknown prices remain ineligible under this cap.
            target_price, candidate_price = _price_pair(
                evidence, str(entry.get("card_id") or ""), str(card["card_id"])
            )
            if target_price is None or candidate_price is None or candidate_price > target_price:
                continue
            used_incoming.add(incoming_oracle)
            found.append({
                "entry_id": str(entry["id"]), "from_name": str(entry.get("name") or ""),
                "from_role": str(entry.get("role") or alternatives.get("role") or ""),
                "quantity": 1,
                "zone_id": entry.get("zone_id"), "card_id": str(card["card_id"]),
                "oracle_id": str(card.get("oracle_id") or ""),
                "name": str(card.get("name") or ""), "lists": int(card.get("lists") or 0),
                "rate": card.get("rate"), "denominator": int(denominator or 0),
                "window_days": int(alternatives.get("window_days") or window_days),
                "evidence_label": "Same-role inclusion in recorded tournament lists",
                "price_usd": candidate_price, "price_ceiling_usd": target_price,
                "finding_tests": [key for key, roles in targets.items() if role in roles],
                "limitation": "This evidence does not show the card improves this deck or its simulator findings.",
            })
            if len(found) >= cap:
                break
    return found


def _price_pair(
    evidence: DeckEvidenceService, target_id: str, candidate_id: str
) -> tuple[float | None, float | None]:
    """Read latest recorded prices, returning unknown if the price feed is absent."""
    if not target_id or not candidate_id:
        return None, None
    try:
        with closing(sqlite3.connect(evidence.db_path)) as conn:
            latest = conn.execute("SELECT MAX(snapshot_date) FROM card_prices").fetchone()[0]
            if not latest:
                return None, None
            rows = conn.execute(
                "SELECT card_id,price_usd FROM card_prices WHERE snapshot_date=? AND card_id IN (?,?)",
                (latest, target_id, candidate_id),
            ).fetchall()
    except Exception:
        return None, None
    prices = {str(row[0]): float(row[1]) for row in rows if row[1] is not None}
    return prices.get(target_id), prices.get(candidate_id)
