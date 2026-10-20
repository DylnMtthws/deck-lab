"""Bounded public recorded-list composition retrieval, never deck strength.

Only the supplied current document is read in memory. Neighbors come exclusively
from Research's public cohort and deck_cards; private saved decks are not queried.
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from sabermetrics.analytics.oracle_patterns import ROLE_PATTERNS
from sabermetrics.assistant_associations import MAX_COHORT
from sabermetrics.deck_evidence import (
    _cohort_cte,
    _commanders,
    _known,
    _library_entries,
    _resolve_floor,
)
from sabermetrics.research import ResearchRepo

MAX_MEMBERSHIPS = 250_000
MAX_LIST_CARDS = 1000
QUERY_SECONDS = 2.0


def _basic(card: dict[str, Any]) -> bool:
    kind = str(card.get("type_line") or "").lower()
    return "basic" in kind and "land" in kind


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def _profile(
    ids: set[str], facts: dict[str, dict[str, Any]], role_cache: dict[str, list[str]]
) -> dict[str, Any]:
    covered = {
        oid
        for oid in ids
        if oid in role_cache or str(facts.get(oid, {}).get("oracle_text") or "").strip()
    }
    for oid in covered:
        if oid not in role_cache:
            text = str(facts[oid]["oracle_text"])
            role_cache[oid] = [
                role
                for role, patterns in ROLE_PATTERNS.items()
                if any(pattern.search(text) for pattern in patterns)
            ]
    roles = Counter(role for oid in covered for role in role_cache[oid])
    return {
        "status": (
            "available"
            if covered == ids and ids
            else "partial" if covered else "unavailable"
        ),
        "composition_denominator": len(ids),
        "canonical_text_cards": len(covered),
        "missing_canonical_text_cards": len(ids - covered),
        "role_counts": dict(sorted(roles.items())),
        "role_rates": {
            role: count / len(covered) for role, count in sorted(roles.items())
        },
        "rate_denominator": len(covered),
    }


def _role_similarity(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    # Partial text coverage is reported but cannot bias the retrieval ranking.
    if a["status"] != "available" or b["status"] != "available":
        return None
    left, right = a["role_rates"], b["role_rates"]
    roles = left.keys() | right.keys()
    denominator = sum(max(left.get(r, 0), right.get(r, 0)) for r in roles)
    return (
        sum(min(left.get(r, 0), right.get(r, 0)) for r in roles) / denominator
        if denominator
        else None
    )


def comparable_decks(path: Path, document: dict, *, window_days=90, limit=3) -> dict:
    """Return up to three diverse public neighbors in one read-only snapshot.

    Composition uses distinct nonbasic, noncommander Oracle identities, not
    quantities. Jaccard overlap is the main retrieval measure; complete canonical
    text coverage permits a 15% heuristic role-profile component. No model calls.
    """
    if window_days not in (0, 7, 30, 90, 365) or not 1 <= limit <= 3:
        raise ValueError("Unsupported window or result limit")
    start, end, _ = ResearchRepo._scope(window_days)
    commanders = _commanders(document)
    result: dict[str, Any] = {
        "status": "unavailable",
        "window_days": window_days,
        "window_start": start,
        "window_end_exclusive": end,
        "min_event_size": None,
        "cohort_lists": 0,
        "comparable_decks": [],
        "source": "local public research_results and deck_cards snapshot",
        "source_freshness": "unknown",
        "maximum_cohort_lists": MAX_COHORT,
        "similarity_method": "distinct Oracle Jaccard; 0.85 overlap + 0.15 role similarity only with complete canonical text coverage",
        "diversity_method": "distinct compositions; greedy relevance minus 0.2 maximum Jaccard to earlier selected neighbors",
        "limitations": [
            "Composition resemblance is not optimized deck strength, legality, synergy or measured improvement.",
            "Only public recorded lists in the exact commander/partner, date and event-size cohort are compared; no private saved deck documents are read.",
            "Quantities, printings, basic lands and commander slots do not contribute to similarity. Missing Oracle identities cannot be compared.",
            "Role profiles are bounded printed-text heuristics, not a rules engine; partial coverage does not contribute to ranking.",
        ],
    }
    deadline = time.monotonic() + QUERY_SECONDS

    def check_deadline():
        if time.monotonic() > deadline:
            raise sqlite3.OperationalError("interrupted")

    try:
        with closing(
            sqlite3.connect(
                Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1
            )
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute("BEGIN")
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
            cohort = conn.execute(
                cte + " SELECT deck_id FROM cohort LIMIT ?",
                [*params, MAX_COHORT + 1],
            ).fetchall()
            n = len(cohort)
            result["cohort_lists"] = n
            if n > MAX_COHORT:
                result.update(
                    status="cohort_too_large", cohort_count_is_lower_bound=True
                )
                return result
            columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
            text_column = "c.oracle_text" if "oracle_text" in columns else "NULL"
            # Group only the requested identities once, rather than repeating
            # MIN(id) for every printing. Keep the same canonical minimum id;
            # document text and user role labels are never profile evidence.
            canonical = f"""SELECT c.id AS card_id,c.oracle_id,c.name,c.type_line,{text_column} AS oracle_text
                FROM cards c WHERE c.id IN (
                    SELECT MIN(id) FROM cards WHERE oracle_id IN ({{marks}})
                    GROUP BY oracle_id
                )"""
            current = {
                str(c.get("oracle_id")): c
                for c in _library_entries(document)
                if c.get("oracle_id") and c.get("quantity", 1) != 0
            }
            if len(current) > MAX_LIST_CARDS:
                result["status"] = "composition_too_large"
                return result
            facts: dict[str, dict[str, Any]] = {}
            if current:
                marks = ",".join("?" for _ in current)
                facts.update(
                    (row["oracle_id"], dict(row))
                    for row in conn.execute(
                        canonical.format(marks=marks), list(current)
                    )
                )
            commander_ids = {c["oracle_id"] for c in commanders if c["oracle_id"]}
            target = {
                oid
                for oid, card in current.items()
                if oid not in commander_ids and not _basic(facts.get(oid, card))
            }
            result["current_composition"] = {
                "oracle_ids": sorted(target),
                "distinct_cards": len(target),
                "entries_without_oracle_id": sum(
                    not c.get("oracle_id") for c in _library_entries(document)
                ),
            }
            if not target:
                result["status"] = "empty_comparable_library"
                return result
            # Reuse the bounded cohort and resolve each public printing once.
            # Streaming memberships avoids repeating large cards-row lookups
            # and sorting every deck/card pair in SQLite. Count distinct pairs
            # before commander exclusion, exactly as the former DISTINCT query.
            cohort_marks = ",".join("?" for _ in cohort) or "NULL"
            deck_ids = [row["deck_id"] for row in cohort]
            public_cards = dict(
                conn.execute(
                    f"""SELECT c.id,c.oracle_id FROM cards c
                    WHERE c.id IN (
                        SELECT dc.card_id FROM deck_cards dc
                        WHERE dc.deck_id IN ({cohort_marks})
                          AND dc.is_commander=0 AND dc.quantity>0
                    )
                      AND c.oracle_id IS NOT NULL AND c.oracle_id!=''
                      AND lower(COALESCE(c.type_line,'')) NOT LIKE '%basic%land%'
                    """,
                    deck_ids,
                )
            )
            memberships: dict[Any, set[str]] = defaultdict(set)
            membership_count = 0
            for row in conn.execute(
                f"""SELECT deck_id,card_id FROM deck_cards
                    WHERE deck_id IN ({cohort_marks})
                      AND is_commander=0 AND quantity>0""",
                deck_ids,
            ):
                oid = public_cards.get(row["card_id"])
                if oid is None or oid in memberships[row["deck_id"]]:
                    continue
                memberships[row["deck_id"]].add(oid)
                membership_count += 1
                if membership_count > MAX_MEMBERSHIPS:
                    result["status"] = "composition_too_large"
                    return result
            decks: dict[str, set[str]] = defaultdict(set)
            for deck_id, ids in memberships.items():
                decks[str(deck_id)].update(ids - commander_ids)
            if any(len(ids) > MAX_LIST_CARDS for ids in decks.values()):
                result["status"] = "composition_too_large"
                return result
            all_ids = set().union(*decks.values()) if decks else set()
            # Fetch only cards in these public lists, in bounded SQLite batches.
            missing = sorted(all_ids - facts.keys())
            for offset in range(0, len(missing), 500):
                batch = missing[offset : offset + 500]
                marks = ",".join("?" for _ in batch)
                facts.update(
                    (row["oracle_id"], dict(row))
                    for row in conn.execute(canonical.format(marks=marks), batch)
                )
            role_cache: dict[str, list[str]] = {}
            target_profile = _profile(target, facts, role_cache)
            result["current_composition"]["role_profile"] = target_profile
            candidates: list[dict[str, Any]] = []
            seen: set[frozenset[str]] = set()
            for deck_id, ids in sorted(decks.items()):
                check_deadline()
                composition = frozenset(ids)
                if composition in seen:
                    continue
                seen.add(composition)
                common = target & ids
                if not common:
                    continue
                profile = _profile(ids, facts, role_cache)
                overlap = _overlap(target, ids)
                role_similarity = _role_similarity(target_profile, profile)
                score = (
                    overlap
                    if role_similarity is None
                    else 0.85 * overlap + 0.15 * role_similarity
                )
                candidates.append(
                    {
                        "deck_id": deck_id,
                        "source_kind": "public_research_list",
                        "overlap": overlap,
                        "shared_cards": len(common),
                        "current_denominator": len(target),
                        "neighbor_denominator": len(ids),
                        "union_denominator": len(target | ids),
                        "shared_oracle_ids": sorted(common),
                        "neighbor_only_oracle_ids": sorted(ids - target),
                        "current_only_oracle_ids": sorted(target - ids),
                        "composition": [
                            facts.get(oid, {"oracle_id": oid}) for oid in sorted(ids)
                        ],
                        "role_profile": profile,
                        "role_similarity": role_similarity,
                        "retrieval_similarity": score,
                        "_ids": ids,
                    }
                )
            result["eligible_distinct_compositions"] = len(candidates)
            selected: list[dict[str, Any]] = []
            while candidates and len(selected) < limit:
                check_deadline()
                for candidate in candidates:
                    redundancy = max(
                        (_overlap(candidate["_ids"], s["_ids"]) for s in selected),
                        default=0.0,
                    )
                    candidate["maximum_overlap_with_prior_neighbors"] = redundancy
                    candidate["diversity_adjusted_retrieval_score"] = (
                        candidate["retrieval_similarity"] - 0.2 * redundancy
                    )
                chosen = min(
                    candidates,
                    key=lambda c: (
                        -c["diversity_adjusted_retrieval_score"],
                        -c["overlap"],
                        c["deck_id"],
                    ),
                )
                selected.append(chosen)
                candidates.remove(chosen)
            for item in selected:
                item.pop("_ids")
            check_deadline()
            result["comparable_decks"] = selected
            result["status"] = "available" if selected else "no_comparable_decks"
    except sqlite3.Error as exc:
        result["status"] = (
            "query_budget_exceeded" if "interrupted" in str(exc) else "unavailable"
        )
        result["comparable_decks"] = []
        result["limitations"].append(
            "No complete retrieval was available; absence of neighbors is not evidence of absence."
        )
    return result
