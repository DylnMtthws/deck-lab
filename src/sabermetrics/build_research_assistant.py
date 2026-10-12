"""Deterministic Build research assistant over native diagnostics and evidence.

This module makes no model calls. Simulator observations and tournament-list
alternatives remain separate so the latter are never presented as measured
causes or as proof of improvement.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from sabermetrics.deck_evidence import (
    DeckEvidenceService,
    _card_roles,
    _library_entries,
)

TEST_LABELS = {
    "opening": "Opening hand",
    "development": "Development",
    "commander_loss": "Commander recovery",
    "creature_wipe": "Creature wipe recovery",
    "finish": "Turn-seven finish",
}
METRIC_LABELS = {
    "qualified_opening_rate": "Hands passing the modeled keep rule",
    "mean_kept_cards": "Average cards kept",
    "mean_unused_available_mana": "Unused modeled mana capacity at turn 5",
    "mean_cards_remaining": "Cards remaining at turn 5",
    "authored_forward_action_rate": "Played an action with modeled effects",
    "mean_first_authored_action_turn": "First modeled action turn",
    "engine_online_rate": "Observed engine event",
    "disruption_exercised_rate": "Runs where removal occurred",
    "followup_turn_reached_rate": "Follow-up turn observed after removal",
    "recovery_authored_action_rate": "Played a modeled action on recovery turn",
    "recovery_board_rate": "Added a modeled nonland permanent",
    "recovery_draw_rate": "Drew cards from a modeled effect",
    "independent_engine_recovery_rate": "Independent engine restored",
    "commander_dependent_hand_cards": "Cards requiring the commander",
    "verified_finish_rate": "Finish proven among evaluated runs (conditional)",
    "positive_finish_proof_rate": "Finish positively proven across all trials (lower bound)",
    "finish_evaluated_rate": "Runs with verified finish evaluation",
    "mean_missed_land_drops": "Average missed land drops",
}

_ROLE_MAP = {
    "land": "land",
    "ramp": "ramp",
    "fixing": "ramp",
    "draw": "draw",
    "removal": "removal",
    "board_wipe": "removal",
    "protection": "protection",
    "tutor": "tutor",
    "recursion": "recursion",
    "wincon": "wincon",
    "threat": "wincon",
}


def _builder_role(entry: dict[str, Any]) -> str:
    explicit = str(entry.get("role") or "").strip().casefold()
    if explicit:
        return explicit
    for tag in _card_roles(entry):
        mapped = _ROLE_MAP.get(str(tag).casefold())
        if mapped:
            return mapped
    return ""


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
        metric_names = list(metrics)
        if metrics.get("positive_finish_proof_rate"):
            metric_names = [name for name in metric_names if name != "verified_finish_rate"]
        for metric_name in metric_names:
            metric = metrics[metric_name]
            if not isinstance(metric, dict):
                continue
            value = metric.get("value")
            if value is None:
                shown = "unmeasured"
            elif isinstance(value, (float, int)) and not isinstance(value, bool):
                unit = str(metric.get("unit") or "")
                if unit in {"proportion", "rate", "probability"} and 0 <= value <= 1:
                    shown = f"{value * 100:.1f}%"
                else:
                    shown = f"{value:g} {unit}".strip()
            else:
                continue
            denominator = metric.get("denominator")
            if isinstance(denominator, int) and denominator > 0:
                shown += f" ({denominator} games)"
            observed.append(
                {
                    "metric": METRIC_LABELS.get(str(metric_name), str(metric_name).replace("_", " ")),
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
            denominator = metric.get("denominator")
            if not isinstance(denominator, int) or isinstance(denominator, bool) or denominator < 1:
                continue
            # Any missed land drop is a scenario observation, not a deck grade.
            actionable_signal = name == "mean_missed_land_drops" and value > 0
            if actionable_signal:
                targets.setdefault(str(test_key), set()).update(roles)
    role_priority = {role for roles in targets.values() for role in roles}
    if not role_priority:
        return []
    found: list[dict[str, Any]] = []
    used_incoming: set[str] = set()
    used_outgoing: set[str] = set()
    entries = [entry for entry in _library_entries(document)
               if entry.get("oracle_id") and entry.get("id")]
    evidence_document = dict(document)
    evidence_document["entries"] = [dict(entry) for entry in document.get("entries", [])
                                   if isinstance(entry, dict)]
    enriched = {str(entry.get("id")): entry for entry in evidence_document["entries"]}
    for entry in entries:
        target = enriched.get(str(entry["id"]))
        if target is not None:
            target["role"] = _builder_role(entry)
    for entry in entries:
        if len(found) >= cap:
            break
        if str(entry["oracle_id"]) in used_outgoing:
            continue
        role = _builder_role(entry)
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
        alternatives = evidence.alternatives(evidence_document, str(entry["oracle_id"]),
                                             window_days, limit=50)
        denominator = alternatives.get("denominator", 0)
        if not isinstance(denominator, int) or denominator < 1:
            continue
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
                "from_role": role or str(alternatives.get("role") or ""),
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
            used_outgoing.add(str(entry["oracle_id"]))
            break
    return found


def search_hints(facts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Offer user-directed card-text searches when evidence cannot select swaps."""
    templates = {
        "opening": ("Explore mana sources", "text:add {C}"),
        "development": ("Explore mana sources", "text:add {C}"),
        "commander_loss": ("Explore draw and recursion", "text:draw"),
        "creature_wipe": ("Explore draw and recovery", "text:draw"),
        "finish": ("Explore modeled damage effects", "text:damage"),
    }
    seen: set[str] = set()
    result = []
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("status") == "unsupported":
            continue
        hint = templates.get(str(fact.get("id") or ""))
        if hint and hint[1] not in seen:
            seen.add(hint[1])
            result.append({"test": str(fact.get("id") or ""), "label": hint[0], "query": hint[1]})
    return result


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
