"""Read-only commander-conditioned card associations, never causal synergy.

Reuse Research's exact cohort; collapse printings, quantities and repeated list
observations. Shrink conditional inclusion toward the cohort baseline so rare
coincidences and ubiquitous staples do not dominate discovery.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from sabermetrics.deck_evidence import (
    _cohort_cte,
    _commanders,
    _known,
    _library_entries,
    _resolve_floor,
)
from sabermetrics.research import ResearchRepo

MAX_COHORT = 2000
MIN_COHORT = 10
MIN_SUPPORT = 5
PRIOR_LISTS = 5


def research_card_associations(
    path: Path,
    document: dict[str, Any],
    oracle_id: str,
    *,
    window_days: int = 90,
    limit: int = 6,
) -> dict[str, Any]:
    """Find positive associations for one actual library card, within two seconds.

    The model cannot select an unrelated global anchor or use private saved decks
    as training data. No synthetic associations or external calls on empty data.
    """
    if window_days not in (0, 7, 30, 90, 365) or not 1 <= limit <= 8:
        raise ValueError("Unsupported window or result limit")
    library = {str(c.get("oracle_id")) for c in _library_entries(document)}
    if not oracle_id or oracle_id not in library:
        raise ValueError("Anchor must be an Oracle ID from the current library")
    start, end, _ = ResearchRepo._scope(window_days)
    commanders = _commanders(document)
    result: dict[str, Any] = {
        "status": "unavailable",
        "anchor_oracle_id": oracle_id,
        "window_days": window_days,
        "window_start": start,
        "window_end_exclusive": end,
        "min_event_size": None,
        "cohort_lists": 0,
        "anchor_lists": 0,
        "associations": [],
        "minimum_cohort_lists": MIN_COHORT,
        "minimum_pair_lists": MIN_SUPPORT,
        "prior_lists": PRIOR_LISTS,
        "source": "local research_results and deck_cards snapshot",
        "source_freshness": "unknown",
        "limitations": [
            "Recorded co-inclusion is association, not mechanical synergy, causality, a combo or measured improvement.",
            "Submitted lists are a selected sample; repeated pilots, archetypes and historical bans can confound associations.",
            "Basic lands and commander slots are excluded. No absence claim follows from a missing or filtered association.",
            "Candidates require separate current legality, color, Oracle-text and swap validation before recommendation.",
        ],
    }
    deadline = time.monotonic() + 2
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            # Keep the count and membership reads on one consistent snapshot.
            conn.execute("BEGIN")
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if not _known(conn, commanders):
                result["status"] = "unknown_commander"
                return result
            event_column, floor = _resolve_floor(conn, None)
            result["min_event_size"] = floor
            if floor is None:
                result["limitations"].append(
                    "Event-size floor is unavailable in this local snapshot; none was applied."
                )
            params: list[Any] = [start, end]
            if event_column is not None and floor is not None:
                params.append(floor)
            if len(commanders) == 2:
                params.extend(c["oracle_id"] for c in commanders)
            else:
                params.extend([commanders[0]["card_id"], commanders[0]["oracle_id"]])
            cte = _cohort_cte(
                partner=len(commanders) == 2,
                event_column=event_column if floor is not None else None,
            )
            n = conn.execute(
                cte + " SELECT COUNT(*) FROM (SELECT deck_id FROM cohort LIMIT ?)",
                [*params, MAX_COHORT + 1],
            ).fetchone()[0]
            result["cohort_lists"] = n
            if n > MAX_COHORT:
                result["status"] = "cohort_too_large"
                result["cohort_count_is_lower_bound"] = True
                result["limitations"].append(
                    "Narrow the date window; no truncated-cohort associations were calculated."
                )
                return result
            if n < MIN_COHORT:
                result["status"] = "insufficient_sample"
                return result
            # No all-pairs matrix: one anchor and at most 2,000 distinct lists.
            rows = conn.execute(
                cte
                + """,
                membership AS (
                    SELECT DISTINCT cohort.deck_id, c.oracle_id
                    FROM cohort JOIN deck_cards dc ON dc.deck_id=cohort.deck_id
                    JOIN cards c ON c.id=dc.card_id
                    WHERE dc.is_commander=0 AND dc.quantity>0
                      AND c.oracle_id IS NOT NULL AND c.oracle_id!=''
                      AND lower(COALESCE(c.type_line,'')) NOT LIKE '%basic%land%'
                ), anchors AS (SELECT deck_id FROM membership WHERE oracle_id=?)
                SELECT m.oracle_id, COUNT(*) AS baseline_lists,
                       COUNT(a.deck_id) AS pair_lists,
                       (SELECT COUNT(*) FROM anchors) AS anchor_lists
                FROM membership m LEFT JOIN anchors a ON a.deck_id=m.deck_id
                GROUP BY m.oracle_id
            """,
                [*params, oracle_id],
            ).fetchall()
            anchor_n = next((int(r["anchor_lists"]) for r in rows), 0)
            result["aggregate_sha256"] = hashlib.sha256(
                json.dumps(
                    {
                        "window": [start, end],
                        "floor": floor,
                        "commanders": commanders,
                        "cohort_lists": n,
                        "counts": sorted(tuple(row) for row in rows),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            result["aggregate_hash_scope"] = (
                "Cohort and per-Oracle aggregate counts, not a full corpus identity or freshness certification."
            )
            result["anchor_lists"] = anchor_n
            if anchor_n < MIN_SUPPORT:
                result["status"] = "insufficient_anchor_sample"
                return result
            ranked = []
            for row in rows:
                oid, count, pair = (
                    row["oracle_id"],
                    row["baseline_lists"],
                    row["pair_lists"],
                )
                if oid == oracle_id or pair < MIN_SUPPORT:
                    continue
                # Shrinkage preserves the sign of the raw association. Compare
                # integer cross-products before floating division: an anchor in
                # every list must not produce lift 1.0000000000000002 leads.
                if pair * n <= anchor_n * count:
                    continue
                baseline = count / n
                adjusted = (pair + PRIOR_LISTS * baseline) / (anchor_n + PRIOR_LISTS)
                lift = adjusted / baseline
                if lift <= 1:
                    continue
                ranked.append(
                    {
                        "oracle_id": oid,
                        "baseline_lists": count,
                        "pair_lists": pair,
                        "conditional_denominator": anchor_n,
                        "cohort_denominator": n,
                        "baseline_rate": baseline,
                        "conditional_rate": pair / anchor_n,
                        "adjusted_conditional_rate": adjusted,
                        "adjusted_lift": lift,
                        "already_in_library": oid in library,
                    }
                )
            ranked.sort(
                key=lambda r: (-r["adjusted_lift"], -r["pair_lists"], r["oracle_id"])
            )
            result["matched_associations"] = len(ranked)
            result["associations_truncated"] = len(ranked) > limit
            for item in ranked[:limit]:
                card = conn.execute(
                    "SELECT id,name FROM cards WHERE oracle_id=? ORDER BY id LIMIT 1",
                    (item["oracle_id"],),
                ).fetchone()
                item.update(card_id=card["id"], name=card["name"])
            result["associations"] = ranked[:limit]
            result["status"] = "available" if ranked else "no_supported_associations"
    except sqlite3.Error as exc:
        result["status"] = (
            "query_budget_exceeded" if "interrupted" in str(exc) else "unavailable"
        )
        result["associations"] = []
        result["limitations"].append(
            "No complete association query was available; do not infer an empty metagame."
        )
    return result
