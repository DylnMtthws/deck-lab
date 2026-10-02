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

from sabermetrics.analytics.role_tagger import tag_card_roles
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.research import ResearchRepo

# Zone names that are not part of the 99. Same set as
# ``deck_documents._PRIVATE_PUBLIC_ZONES`` and the builder's PRIVATE_ZONE_NAMES.
_PRIVATE_LIBRARY_ZONES = frozenset(
    {"sideboard", "notes", "note", "maybeboard", "maybe", "draft", "considering"}
)
_MIN_UNPLAYED_DENOMINATOR = 10

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
    found = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(tournament_results)")
    }
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
            if (
                not isinstance(item, dict)
                or str(item.get("oracle_id") or "") != oracle_id
            ):
                continue
            reason = item.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
    return None


def _library_zone(name: str | None) -> bool:
    """True when a zone name counts as part of the deck library."""
    return str(name or "Unsorted").strip().casefold() not in _PRIVATE_LIBRARY_ZONES


def _zone_names(document: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for zone in document.get("zones") or []:
        if not isinstance(zone, dict) or not zone.get("id"):
            continue
        names[str(zone["id"])] = str(zone.get("name") or "")
    return names


def _entry_zone_name(entry: dict[str, Any], zones: dict[str, str]) -> str:
    named = entry.get("zone_name")
    if isinstance(named, str) and named.strip():
        return named
    zone_id = str(entry.get("zone_id") or "")
    if zone_id and zone_id in zones:
        return zones[zone_id]
    return "Unsorted"


def _library_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    zones = _zone_names(document)
    found: list[dict[str, Any]] = []
    for entry in document.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("is_commander"):
            continue
        if _library_zone(_entry_zone_name(entry, zones)):
            found.append(entry)
    return found


def _deck_oracle_ids(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for entry in document.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        oracle_id = str(entry.get("oracle_id") or "")
        if oracle_id:
            found.add(oracle_id)
    return found


def _entry_for_oracle(
    document: dict[str, Any], oracle_id: str
) -> dict[str, Any] | None:
    fallback: dict[str, Any] | None = None
    for entry in document.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("oracle_id") or "") != oracle_id:
            continue
        if str(entry.get("role") or "").strip():
            return entry
        if fallback is None:
            fallback = entry
    return fallback


def _parse_colors(value: Any) -> set[str]:
    parsed = value
    if isinstance(value, str):
        parsed = _loads(value)
    if not isinstance(parsed, list):
        return set()
    return {str(color) for color in parsed if str(color)}


def _inside_identity(card_colors: set[str], allowed: set[str]) -> bool:
    return card_colors.issubset(allowed)


def _is_legal_in_99(value: Any) -> bool:
    return value in (1, True)


def _stored_roles(value: Any) -> list[str] | None:
    parsed = value
    if isinstance(value, str):
        parsed = _loads(value)
    if not isinstance(parsed, list):
        return None
    roles = [str(item).strip() for item in parsed if str(item).strip()]
    return roles or None


def _card_roles(card: dict[str, Any]) -> list[str]:
    """Use a stored role list, otherwise the shared oracle-text tagger."""
    stored = _stored_roles(card.get("role_tags"))
    if stored:
        return stored
    tagged = tag_card_roles(
        {
            "name": str(card.get("name") or ""),
            "oracle_text": str(card.get("oracle_text") or ""),
            "type_line": str(card.get("type_line") or ""),
        }
    )
    return list(tagged.role_tags)


def _shares_role(roles: list[str], target: str) -> bool:
    wanted = target.casefold()
    return any(role.casefold() == wanted for role in roles)


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


def _cohort_sql(*, partner: bool, event_column: str | None, oracle_count: int) -> str:
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

    def meta_diff(
        self,
        document: dict[str, Any],
        window_days: int = 30,
        min_event_size: int | None = None,
        staple_threshold: float = 0.5,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Compare one deck's library with recorded lists for its commander.

        Args:
            document: A deck document dict (entries and zones).
            window_days: Research window. ``0`` is all recorded time.
            min_event_size: Smallest event that counts, when the research
                tables have an event-size column.
            staple_threshold: Minimum inclusion rate for a missing staple.
            limit: Maximum number of missing staples to return.

        Returns:
            Missing staples, library cards in no recorded list, and the same
            denominator, window, and event-size floor as :meth:`for_deck`.
            Below 10 recorded lists, ``unplayed`` is empty and
            ``too_few_lists`` is true.
        """
        window = _bounded_window(int(window_days))
        threshold = float(staple_threshold)
        capped = max(0, int(limit))
        start, end, _prior = ResearchRepo._scope(window)
        commanders = _commanders(document)
        library = _library_entries(document)
        library_oracles = {
            str(entry.get("oracle_id") or "")
            for entry in library
            if str(entry.get("oracle_id") or "")
        }
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            event_column, applied_floor = _resolve_floor(conn, min_event_size)
            known = _known(conn, commanders)
            counted: dict[str, dict[str, Any]] = {}
            denominator = 0
            allowed: set[str] = set()
            if known and commanders:
                denominator, counted = self._inclusion(
                    conn,
                    commanders=commanders,
                    start=start,
                    end=end,
                    event_column=event_column,
                    floor=applied_floor,
                )
                allowed = _allowed_colors(conn, commanders)
        if not known:
            denominator = 0
        too_few = denominator < _MIN_UNPLAYED_DENOMINATOR
        missing: list[dict[str, Any]] = []
        if known and denominator > 0:
            for oracle_id, card in counted.items():
                lists = int(card["lists"])
                rate = lists / denominator
                if rate < threshold or oracle_id in library_oracles:
                    continue
                if not _is_legal_in_99(card.get("is_legal_in_99")):
                    continue
                if not card.get("card_id"):
                    continue
                if not _inside_identity(
                    _parse_colors(card.get("color_identity")), allowed
                ):
                    continue
                missing.append(
                    {
                        "oracle_id": oracle_id,
                        "card_id": str(card["card_id"]),
                        "name": str(card.get("name") or ""),
                        "lists": lists,
                        "rate": rate,
                        "color_identity": card.get("color_identity"),
                        "oracle_text": card.get("oracle_text"),
                        "type_line": card.get("type_line"),
                        "role_tags": card.get("role_tags"),
                    }
                )
            missing.sort(
                key=lambda item: (-float(item["rate"]), str(item["name"]).casefold())
            )
            missing = missing[:capped]
            for item in missing:
                roles = _card_roles(item)
                item["role_guess"] = roles[0] if roles else ""
                for extra in (
                    "color_identity",
                    "oracle_text",
                    "type_line",
                    "role_tags",
                ):
                    item.pop(extra, None)
        unplayed: list[dict[str, Any]] = []
        if known and not too_few:
            for entry in library:
                oracle_id = str(entry.get("oracle_id") or "")
                if oracle_id and oracle_id in counted:
                    continue
                unplayed.append(
                    {
                        "oracle_id": oracle_id,
                        "name": str(entry.get("name") or ""),
                        "entry_id": str(entry.get("id") or ""),
                        "zone_id": str(entry.get("zone_id") or ""),
                        "role": str(entry.get("role") or "").strip(),
                    }
                )
            unplayed.sort(key=lambda item: item["name"].casefold())
        return {
            "window_days": window,
            "min_event_size": applied_floor,
            "denominator": denominator,
            "available": known and denominator > 0,
            "too_few_lists": too_few,
            "missing_staples": missing,
            "unplayed": unplayed,
        }

    def alternatives(
        self,
        document: dict[str, Any],
        oracle_id: str,
        window_days: int = 30,
        min_event_size: int | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Return same-role cards from recorded lists that can replace one entry.

        Args:
            document: A deck document dict.
            oracle_id: Oracle id of the entry being replaced.
            window_days: Research window. ``0`` is all recorded time.
            min_event_size: Smallest event that counts, when recorded.
            limit: Maximum number of alternatives.

        Returns:
            Alternatives that share the entry's builder role, are legal, sit
            inside the commander's color identity, and are not already in the
            deck. Each rate is paired with its list count. When the entry has
            no role, the result is ``{"needs_role": True}``.
        """
        entry = _entry_for_oracle(document, str(oracle_id or ""))
        role = str((entry or {}).get("role") or "").strip()
        if entry is None or not role:
            return {"needs_role": True}
        window = _bounded_window(int(window_days))
        capped = max(0, int(limit))
        start, end, _prior = ResearchRepo._scope(window)
        commanders = _commanders(document)
        in_deck = _deck_oracle_ids(document)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            event_column, applied_floor = _resolve_floor(conn, min_event_size)
            known = _known(conn, commanders)
            counted: dict[str, dict[str, Any]] = {}
            denominator = 0
            allowed: set[str] = set()
            if known and commanders:
                denominator, counted = self._inclusion(
                    conn,
                    commanders=commanders,
                    start=start,
                    end=end,
                    event_column=event_column,
                    floor=applied_floor,
                )
                allowed = _allowed_colors(conn, commanders)
        if not known:
            denominator = 0
        found: list[dict[str, Any]] = []
        if known and denominator > 0:
            ranked = sorted(
                counted.items(),
                key=lambda item: (
                    -int(item[1]["lists"]),
                    str(item[1].get("name") or "").casefold(),
                ),
            )
            for card_oracle, card in ranked:
                if len(found) >= capped:
                    break
                if card_oracle in in_deck:
                    continue
                if not _is_legal_in_99(card.get("is_legal_in_99")) or not card.get(
                    "card_id"
                ):
                    continue
                if not _inside_identity(
                    _parse_colors(card.get("color_identity")), allowed
                ):
                    continue
                if not _shares_role(_card_roles(card), role):
                    continue
                lists = int(card["lists"])
                found.append(
                    {
                        "oracle_id": card_oracle,
                        "card_id": str(card["card_id"]),
                        "name": str(card.get("name") or ""),
                        "lists": lists,
                        "rate": lists / denominator,
                    }
                )
        return {
            "window_days": window,
            "min_event_size": applied_floor,
            "denominator": denominator,
            "role": role,
            "alternatives": found,
        }

    def _inclusion(
        self,
        conn: sqlite3.Connection,
        *,
        commanders: list[dict[str, str]],
        start: str,
        end: str,
        event_column: str | None,
        floor: int | None,
    ) -> tuple[int, dict[str, dict[str, Any]]]:
        partner = len(commanders) == 2
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(cards)")}
        params: list[Any] = [start, end]
        if event_column is not None and floor is not None:
            params.append(floor)
        if partner:
            params.extend([commanders[0]["oracle_id"], commanders[1]["oracle_id"]])
        else:
            params.extend([commanders[0]["card_id"], commanders[0]["oracle_id"]])
        rows = conn.execute(
            _inclusion_sql(
                partner=partner,
                event_column=event_column if floor is not None else None,
                role_tags="role_tags" in columns,
            ),
            params,
        ).fetchall()
        denominator = 0
        cards: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["kind"] == "denominator":
                denominator = int(row["lists"] or 0)
                continue
            oracle_id = str(row["oracle_id"] or "")
            lists = int(row["lists"] or 0)
            if not oracle_id or lists <= 0:
                continue
            cards[oracle_id] = {
                "lists": lists,
                "card_id": row["card_id"],
                "name": row["name"],
                "color_identity": row["color_identity"],
                "is_legal_in_99": row["is_legal_in_99"],
                "oracle_text": row["oracle_text"],
                "type_line": row["type_line"],
                "role_tags": row["role_tags"],
            }
        return denominator, cards


def _resolve_floor(
    conn: sqlite3.Connection, min_event_size: int | None
) -> tuple[str | None, int | None]:
    event_column = _event_column(conn)
    if event_column is None:
        return None, None
    if min_event_size is None:
        return event_column, int(load_cedh_settings().meta.min_event_size)
    return event_column, max(0, int(min_event_size))


def _allowed_colors(
    conn: sqlite3.Connection, commanders: list[dict[str, str]]
) -> set[str]:
    keys: list[str] = []
    for commander in commanders:
        if commander["card_id"]:
            keys.append(commander["card_id"])
        if commander["oracle_id"]:
            keys.append(commander["oracle_id"])
    if not keys:
        return set()
    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT color_identity FROM cards WHERE id IN ({marks}) OR oracle_id IN ({marks})",
        [*keys, *keys],
    ).fetchall()
    allowed: set[str] = set()
    for row in rows:
        allowed |= _parse_colors(row["color_identity"])
    return allowed


def _inclusion_sql(*, partner: bool, event_column: str | None, role_tags: bool) -> str:
    event_join = _EVENT_JOIN if event_column else ""
    event_sql = _EVENT_PREDICATE[event_column] if event_column else ""
    commander_sql = _PARTNER_COMMANDER if partner else _SINGLE_COMMANDER
    role_sql = "MAX(c.role_tags) AS role_tags" if role_tags else "NULL AS role_tags"
    return f"""
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
        SELECT 'denominator' AS kind, NULL AS oracle_id, NULL AS card_id,
               NULL AS name, NULL AS color_identity, NULL AS is_legal_in_99,
               NULL AS oracle_text, NULL AS type_line, NULL AS role_tags,
               COUNT(*) AS lists
        FROM cohort
        UNION ALL
        SELECT 'card' AS kind, c.oracle_id AS oracle_id,
               MAX(CASE WHEN c.is_legal_in_99 = 1 THEN c.id END) AS card_id,
               MAX(c.name) AS name,
               MAX(c.color_identity) AS color_identity,
               MAX(c.is_legal_in_99) AS is_legal_in_99,
               MAX(c.oracle_text) AS oracle_text,
               MAX(c.type_line) AS type_line,
               {role_sql},
               COUNT(DISTINCT cohort.deck_id) AS lists
        FROM cohort
        JOIN deck_cards dc ON dc.deck_id = cohort.deck_id
        JOIN cards c ON c.id = dc.card_id
        WHERE COALESCE(dc.is_commander, 0) = 0
          AND c.oracle_id IS NOT NULL
          AND c.oracle_id != ''
        GROUP BY c.oracle_id
    """
