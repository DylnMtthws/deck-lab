"""Tournament inclusion and stored candidate reasons for one deck document.

Counts follow :class:`sabermetrics.research.ResearchRepo`: the same date window
and the same distinct recorded-list denominator. A rate is returned only with
that denominator. This module does not call a model and does not invent text.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.research import ResearchRepo

# Column names that mean "how many players were in the event", if a research
# table actually has one. The local snapshot usually does not: event size is
# applied at ingest and then dropped. Names are a fixed allowlist, never input.
_EVENT_COLUMNS: tuple[str, ...] = (
    "player_count",
    "event_size",
    "players",
    "num_players",
)
_EVENT_JOIN = "JOIN tournament_results raw ON raw.id = tr.id"
_EVENT_PREDICATE = {
    "player_count": "AND raw.player_count >= ?",
    "event_size": "AND raw.event_size >= ?",
    "players": "AND raw.players >= ?",
    "num_players": "AND raw.num_players >= ?",
}
_SINGLE_COMMANDER = (
    "AND (tr.commander_id = ? OR tr.commander_id IN "
    "(SELECT id FROM cards WHERE oracle_id = ?))"
)
_PARTNER_COMMANDER = """AND EXISTS (
    SELECT 1 FROM deck_cards partner_a
    JOIN cards partner_card_a ON partner_card_a.id = partner_a.card_id
    WHERE partner_a.deck_id = tr.deck_id
      AND partner_a.is_commander = 1
      AND partner_card_a.oracle_id = ?
) AND EXISTS (
    SELECT 1 FROM deck_cards partner_b
    JOIN cards partner_card_b ON partner_card_b.id = partner_b.card_id
    WHERE partner_b.deck_id = tr.deck_id
      AND partner_b.is_commander = 1
      AND partner_card_b.oracle_id = ?
)"""


def _bounded_window(window_days: int) -> int:
    """Match the clamp ``ResearchRepo`` applies before :meth:`ResearchRepo._scope`."""
    return 0 if window_days == 0 else max(7, min(window_days, 365))


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _commanders(document: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for entry in document.get("entries") or []:
        if not isinstance(entry, dict) or not entry.get("is_commander"):
            continue
        card_id = str(entry.get("card_id") or "")
        oracle_id = str(entry.get("oracle_id") or "")
        if not card_id and not oracle_id:
            continue
        found.append(
            {
                "card_id": card_id,
                "oracle_id": oracle_id,
                "name": str(entry.get("name") or ""),
            }
        )
    return found


def _oracle_ids(document: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for entry in document.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        oracle_id = str(entry.get("oracle_id") or "")
        if oracle_id and oracle_id not in seen:
            seen.append(oracle_id)
    return seen


def _event_column(conn: sqlite3.Connection) -> str | None:
    found = {str(row[1]) for row in conn.execute("PRAGMA table_info(tournament_results)")}
    for name in _EVENT_COLUMNS:
        if name in found:
            return name
    return None


def _known(conn: sqlite3.Connection, commanders: list[dict[str, str]]) -> bool:
    """True when every commander is a row in the research commander catalog."""
    if not commanders or len(commanders) > 2:
        return False
    clauses: list[str] = []
    params: list[str] = []
    for commander in commanders:
        clauses.append("(id = ? OR oracle_id = ?)")
        params.extend([commander["card_id"], commander["oracle_id"]])
    rows = conn.execute(
        f"SELECT id, oracle_id FROM research_commanders WHERE {' OR '.join(clauses)}",
        params,
    ).fetchall()

    def matches(commander: dict[str, str], row: sqlite3.Row) -> bool:
        if commander["card_id"] and row["id"] == commander["card_id"]:
            return True
        return bool(
            commander["oracle_id"] and row["oracle_id"] == commander["oracle_id"]
        )

    return all(any(matches(commander, row) for row in rows) for commander in commanders)


def _card_reason(explanation: Any, oracle_id: str) -> str | None:
    """Return per-card text when the stored explanation actually has some.

    :class:`sabermetrics.cedh.responses.DeckExplanation` is deck-level prose
    (game plan, weaknesses, key oracle ids). It has no per-card reason, so a
    document that is only that model yields ``None``. A stored object that
    also carries a per-card string map is reported as stored, not rewritten.
    """
    if not isinstance(explanation, dict):
        return None
    for key in ("card_reasons", "reasons"):
        mapped = explanation.get(key)
        if not isinstance(mapped, dict):
            continue
        value = mapped.get(oracle_id)
        if isinstance(value, str) and value.strip():
            return value.strip()
    cards = explanation.get("cards")
    if isinstance(cards, list):
        for item in cards:
            if not isinstance(item, dict) or str(item.get("oracle_id") or "") != oracle_id:
                continue
            reason = item.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
    return None


def _priority(value: Any) -> float:
    if value is None or value is True or value is False:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _explanations(
    conn: sqlite3.Connection, document: dict[str, Any], deck_oracles: set[str]
) -> dict[str, dict[str, Any]]:
    if document.get("source_kind") != "candidate":
        return {}
    source_id = str(document.get("source_id") or "")
    if not source_id:
        return {}
    row = conn.execute(
        "SELECT candidate_json, explanation_json FROM cedh_candidates WHERE candidate_id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        return {}
    candidate = _loads(row["candidate_json"])
    if not isinstance(candidate, dict):
        return {}
    library = candidate.get("cards")
    if not isinstance(library, list):
        library = candidate.get("library")
    if not isinstance(library, list):
        return {}
    explanation = _loads(row["explanation_json"])
    out: dict[str, dict[str, Any]] = {}
    for item in library:
        if not isinstance(item, dict):
            continue
        oracle_id = str(item.get("oracle_id") or "")
        if not oracle_id or oracle_id not in deck_oracles or oracle_id in out:
            continue
        out[oracle_id] = {
            "role": str(item.get("role") or ""),
            "source": str(item.get("source") or ""),
            "priority": _priority(item.get("priority")),
            "reason": _card_reason(explanation, oracle_id),
        }
    return out


def _cohort_sql(
    *, partner: bool, event_column: str | None, oracle_count: int
) -> str:
    event_join = _EVENT_JOIN if event_column else ""
    event_sql = _EVENT_PREDICATE[event_column] if event_column else ""
    commander_sql = _PARTNER_COMMANDER if partner else _SINGLE_COMMANDER
    cohort = f"""
        WITH cohort AS (
            SELECT DISTINCT tr.deck_id AS deck_id
            FROM research_results tr
            {event_join}
            WHERE tr.deck_id IS NOT NULL
              AND tr.tournament_date >= ?
              AND tr.tournament_date < ?
              {event_sql}
              {commander_sql}
              AND EXISTS (
                SELECT 1 FROM deck_cards present WHERE present.deck_id = tr.deck_id
              )
        )
        SELECT 'denominator' AS kind, NULL AS oracle_id, COUNT(*) AS lists
        FROM cohort
    """
    if oracle_count <= 0:
        return cohort
    marks = ",".join("?" for _ in range(oracle_count))
    return cohort + f"""
        UNION ALL
        SELECT 'card' AS kind, c.oracle_id AS oracle_id,
               COUNT(DISTINCT cohort.deck_id) AS lists
        FROM cohort
        JOIN deck_cards dc ON dc.deck_id = cohort.deck_id
        JOIN cards c ON c.id = dc.card_id
        WHERE c.oracle_id IN ({marks})
        GROUP BY c.oracle_id
    """


class DeckEvidenceService:
    """Read inclusion counts and stored candidate reasons for one deck."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def for_deck(
        self,
        document: dict[str, Any],
        window_days: int = 30,
        min_event_size: int | None = None,
    ) -> dict[str, Any]:
        """Return inclusion counts and, for candidate decks, stored reasons.

        Args:
            document: A deck document dict (entries, source_kind, source_id).
            window_days: Research window. ``0`` is all recorded time; other
                values use the same bounds as :class:`ResearchRepo`.
            min_event_size: Smallest event that counts, when the research
                tables have an event-size column. ``None`` selects the cEDH
                settings default. When the tables have no such column the
                floor is not applied and the result reports ``None``.

        Returns:
            Commander ids, window, floor, denominator, availability, per-card
            list counts for cards that appear at least once, and explanations
            for candidate decks.
        """
        window = _bounded_window(int(window_days))
        start, end, _prior = ResearchRepo._scope(window)
        commanders = _commanders(document)
        oracle_ids = _oracle_ids(document)
        commander_ids = [item["card_id"] or item["oracle_id"] for item in commanders]
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            event_column = _event_column(conn)
            if event_column is None:
                applied_floor: int | None = None
            elif min_event_size is None:
                applied_floor = int(load_cedh_settings().meta.min_event_size)
            else:
                applied_floor = max(0, int(min_event_size))
            explanations = _explanations(conn, document, set(oracle_ids))
            known = _known(conn, commanders)
            cards: dict[str, dict[str, float | int]] = {}
            denominator = 0
            if known and commanders:
                denominator, cards = self._counts(
                    conn,
                    commanders=commanders,
                    oracle_ids=oracle_ids,
                    start=start,
                    end=end,
                    event_column=event_column,
                    floor=applied_floor,
                )
        available = known and denominator > 0
        if not available:
            cards = {}
        return {
            "commander_ids": commander_ids,
            "window_days": window,
            "min_event_size": applied_floor,
            "denominator": denominator if known else 0,
            "available": available,
            "cards": cards,
            "explanations": explanations,
        }

    def _counts(
        self,
        conn: sqlite3.Connection,
        *,
        commanders: list[dict[str, str]],
        oracle_ids: list[str],
        start: str,
        end: str,
        event_column: str | None,
        floor: int | None,
    ) -> tuple[int, dict[str, dict[str, float | int]]]:
        partner = len(commanders) == 2
        params: list[Any] = [start, end]
        if event_column is not None and floor is not None:
            params.append(floor)
        if partner:
            params.extend([commanders[0]["oracle_id"], commanders[1]["oracle_id"]])
        else:
            params.extend([commanders[0]["card_id"], commanders[0]["oracle_id"]])
        params.extend(oracle_ids)
        rows = conn.execute(
            _cohort_sql(
                partner=partner,
                event_column=event_column if floor is not None else None,
                oracle_count=len(oracle_ids),
            ),
            params,
        ).fetchall()
        denominator = 0
        cards: dict[str, dict[str, float | int]] = {}
        for row in rows:
            if row["kind"] == "denominator":
                denominator = int(row["lists"] or 0)
                continue
            oracle_id = str(row["oracle_id"] or "")
            lists = int(row["lists"] or 0)
            if not oracle_id or lists <= 0 or denominator <= 0:
                continue
            if oracle_id not in set(oracle_ids):
                continue
            cards[oracle_id] = {"lists": lists, "rate": lists / denominator}
        return denominator, cards
