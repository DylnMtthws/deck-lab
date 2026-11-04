"""Trusted local adapters for shared strategy evidence; no model or network IO.

Current means selected by the local snapshot adapter, never externally verified
latest. Supplied mechanics must agree with that snapshot before admitting a
conditional interaction. Neither this adapter nor its evidence ranks candidates.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from sabermetrics.assistant_card_facts import card_facts
from sabermetrics.assistant_mana_evidence import (
    KINNAN_ORACLE_ID,
    build_mana_evidence,
    card_source_hash,
)
from sabermetrics.assistant_rules import retrieve_rules

_MECHANICS = (
    "oracle_id",
    "name",
    "mana_cost",
    "type_line",
    "layout",
    "oracle_text",
    "card_faces",
)


_CACHE_WATCHER = "_strategy_snapshot_watcher"


class _SnapshotWatcher:
    """data_version requires repeated reads on the same connection."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve(strict=True)
        self.identity = self._identity()
        self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            self.version = self.generation()
        except Exception:
            self.connection.close()
            raise

    def _identity(self):
        stat = self.path.stat()
        return (str(self.path), stat.st_dev, stat.st_ino)

    def generation(self):
        if self._identity() != self.identity:
            raise OSError("Snapshot database replaced")
        version = self.connection.execute("PRAGMA data_version").fetchone()[0]
        if self._identity() != self.identity:
            raise OSError("Snapshot database replaced")
        return version


def close_strategy_cache(cache: dict) -> None:
    """Release the scoped watcher and discard every fact bound to it."""
    watcher = cache.pop(_CACHE_WATCHER, None)
    try:
        if watcher is not None:
            watcher.connection.close()
    finally:
        cache.clear()


def _cache_generation(path: Path, cache: dict) -> tuple[_SnapshotWatcher, int] | None:
    try:
        watcher = cache.get(_CACHE_WATCHER)
        resolved = Path(path).resolve(strict=True)
        if watcher is not None:
            if resolved == watcher.path and watcher.generation() == watcher.version:
                return watcher, watcher.version
        close_strategy_cache(cache)
        watcher = _SnapshotWatcher(resolved)
        cache[_CACHE_WATCHER] = watcher
        return watcher, watcher.version
    except (OSError, sqlite3.Error):
        close_strategy_cache(cache)
        return None


def _generation_unchanged(
    path: Path, cache: dict, generation: tuple[_SnapshotWatcher, int] | None
) -> bool:
    if generation is None:
        close_strategy_cache(cache)
        return False
    try:
        watcher, version = generation
        valid = (
            watcher is not None
            and cache.get(_CACHE_WATCHER) is watcher
            and Path(path).resolve(strict=True) == watcher.path
            and watcher.generation() == version
        )
    except (OSError, sqlite3.Error, TypeError):
        valid = False
    if not valid:
        close_strategy_cache(cache)
    return valid


def snapshot_card(
    path: Path, supplied: dict[str, Any], cache: dict | None = None
) -> dict[str, Any]:
    owned = cache is None
    cache = {} if cache is None else cache
    try:
        generation = _cache_generation(path, cache)
        card = (
            _snapshot_card(path, supplied, cache) if generation else deepcopy(supplied)
        )
        if not _generation_unchanged(path, cache, generation):
            card.update(
                snapshot_unknowns=["snapshot_generation_unproven"],
                is_current=False,
                oracle_text_complete=False,
            )
        return card
    finally:
        if owned:
            close_strategy_cache(cache)


def _snapshot_card(
    path: Path, supplied: dict[str, Any], cache: dict | None = None
) -> dict[str, Any]:
    """Keep canonical facts and provenance, abstaining on omissions/disagreement."""
    cid = supplied.get("card_id") or supplied.get("id")
    cache = {} if cache is None else cache
    cache_key = ("card", str(cid))
    if cache_key not in cache:
        cache[cache_key] = card_facts(path, str(cid)) if cid else {"status": "missing"}
    data = cache[cache_key]
    card = deepcopy(data.get("card") or supplied)
    unknowns = []
    if data.get("status") != "canonical_snapshot":
        unknowns.append("canonical_snapshot_unavailable")
    if any((data.get("truncation") or {}).get(k, 0) for k in ("characters", "items")):
        unknowns.append("canonical_snapshot_truncated")
    if supplied.get("oracle_text_truncated"):
        unknowns.append("supplied_mechanics_truncated")
    for key in _MECHANICS:
        if (
            key in supplied
            and supplied[key] is not None
            and supplied[key] != card.get(key)
        ):
            # An absent face array and an empty array both describe a single face.
            if key == "card_faces" and not supplied[key] and not card.get(key):
                continue
            unknowns.append("supplied_mechanics_mismatch:" + key)
    card.update(
        provenance=deepcopy(data.get("provenance") or {}),
        snapshot_status=data.get("status"),
        snapshot_unknowns=unknowns,
        freshness="unknown",
        current_scope="active_local_snapshot_only",
        is_current=not unknowns,
        oracle_text_complete=not unknowns and isinstance(card.get("oracle_text"), str),
        oracle_text_truncated="canonical_snapshot_truncated" in unknowns,
    )
    card["source_sha256"] = card_source_hash(card)
    return card


def strategy_context(
    path: Path,
    document: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    cache: dict | None = None,
) -> dict[str, Any]:
    owned = cache is None
    cache = {} if cache is None else cache
    try:
        if not any(
            e.get("is_commander") and e.get("oracle_id") == KINNAN_ORACLE_ID
            for e in document.get("entries", [])
        ):
            return _strategy_context(path, document, cards, cache=cache)
        generation = _cache_generation(path, cache)
        if generation:
            context = _strategy_context(path, document, cards, cache=cache)
            if _generation_unchanged(path, cache, generation):
                return context
        return {
            "status": "unknown",
            "cards": [],
            "rules": [],
            "rule_lookups": [],
            "mana_evidence": {"rows": []},
            "unknowns": ["snapshot_generation_unproven"],
        }
    finally:
        if owned:
            close_strategy_cache(cache)


def _strategy_context(
    path: Path,
    document: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    cache: dict | None = None,
) -> dict[str, Any]:
    """Read complete local card facts and exact bounded mana dependencies once.

    Rules and commander facts are shared rather than repeated in each source's
    mana row. Missing dependencies and unsupported mechanics remain explicit.
    """
    empty = {
        "cards": [],
        "rules": [],
        "rule_lookups": [],
        "mana_evidence": {"rows": []},
        "unknowns": [],
    }
    if not any(
        e.get("is_commander") and e.get("oracle_id") == KINNAN_ORACLE_ID
        for e in document.get("entries", [])
    ):
        return {**empty, "status": "not_applicable"}
    cache = {} if cache is None else cache
    supplied: dict[str, dict[str, Any]] = {}
    conflicts = set()
    compared = (
        *_MECHANICS,
        "oracle_text_truncated",
        "oracle_text_complete",
        "truncation",
    )
    # Inspect evidence before collapsing IDs; preserve first-seen card ordering.
    for card in [
        *cards,
        *(e for e in document.get("entries", []) if e.get("is_commander")),
    ]:
        cid = card.get("card_id") or card.get("id")
        if not cid:
            continue
        cid = str(cid)
        if cid in supplied:
            previous = supplied[cid]
            if any(
                previous.get(key) is not None
                and card.get(key) is not None
                and previous[key] != card[key]
                for key in compared
            ):
                conflicts.add(cid)
            supplied[cid] = {
                **card,
                **{k: v for k, v in previous.items() if v is not None},
            }
        else:
            supplied[cid] = card
    if len(supplied) > 16:
        return {
            **empty,
            "status": "unknown",
            "unknowns": ["source_card_count_exceeds_16"],
            "source_card_count": len(supplied),
        }
    records = []
    for cid, card in supplied.items():
        record = _snapshot_card(path, card, cache)
        if cid in conflicts:
            record["snapshot_unknowns"].append("conflicting_supplied_mechanics")
            record.update(is_current=False, oracle_text_complete=False)
            record["source_sha256"] = card_source_hash(record)
        records.append(record)
    kinnan = [c for c in records if c.get("oracle_id") == KINNAN_ORACLE_ID]
    hits = []
    lookups = []
    if kinnan:
        # Separate exact lookups prevent reference expansion/rulings from evicting
        # either dependency. Rulings get their own bounded, Kinnan-only query.
        for query, scope, limit in (
            ("CR 106.12", [], 1),
            ("CR 106.12a", [], 1),
            ("restrictions riders additional mana", kinnan[:1], 6),
        ):
            key = ("rules", query)
            if key not in cache:
                cache[key] = retrieve_rules(
                    path, query, cards=scope, limit=limit, deadline_seconds=0.2
                ).model_dump()
            evidence = cache[key]
            lookups.append({k: v for k, v in evidence.items() if k != "hits"})
            for hit in evidence["hits"]:
                if (
                    hit["source_id"] not in {"CR 106.12", "CR 106.12a"}
                    and hit["kind"] != "card_ruling"
                ):
                    continue
                bound = deepcopy(hit)
                complete = not hit.get("text_truncated") and hashlib.sha256(
                    hit["text"].encode()
                ).hexdigest() == hit.get("content_sha256")
                bound.update(
                    complete=complete,
                    is_current=complete,
                    freshness="unknown",
                    current_scope="active_local_snapshot_only",
                )
                if bound not in hits:
                    hits.append(bound)
    # DeckDocumentRepo exposes SQLite boolean columns as 0/1. Normalize that
    # trusted local representation without accepting strings or arbitrary flags.
    bound_document = {
        **document,
        "entries": [
            {
                **e,
                "is_commander": e.get("is_commander") is True
                or (type(e.get("is_commander")) is int and e["is_commander"] == 1),
            }
            for e in document.get("entries", [])
        ],
    }
    mana = build_mana_evidence(bound_document, records, {"hits": hits})
    return {
        "scope": "Source-bound conditional evidence; no ranking, pruning or resource availability. Current means active local snapshot; external freshness remains unknown.",
        "cards": records,
        "rules": hits,
        "rule_lookups": lookups,
        "mana_evidence": mana,
        "unknowns": [],
    }


def strategy_wire_context(context: dict[str, Any]) -> dict[str, Any]:
    """Only derived rows and source bindings travel beside complete sources."""
    mana = deepcopy(context["mana_evidence"])
    conditions: list[str] = []
    claims: list[str] = []
    for row in mana["rows"]:
        for key in (
            "rules",
            "commander_oracle_text",
            "commander_casting_cost",
            "source_oracle_text",
            "source_casting_cost",
        ):
            row.pop(key, None)
        for key, vocabulary in (
            ("conditions", conditions),
            ("admitted_claims", claims),
        ):
            refs = []
            for value in row.pop(key):
                if value not in vocabulary:
                    vocabulary.append(value)
                refs.append(vocabulary.index(value))
            row[key + "_refs"] = refs
    mana.update(conditions=conditions, admitted_claims=claims)
    return {
        "scope": context.get("scope", context.get("status")),
        "mana_evidence": mana,
        "card_refs": [
            {
                **{
                    k: c.get(k)
                    for k in (
                        "id",
                        "oracle_id",
                        "source_sha256",
                        "snapshot_unknowns",
                        "freshness",
                    )
                },
                "snapshot_sha256": c["provenance"].get("content_hash"),
            }
            for c in context["cards"]
        ],
        "rule_refs": [
            {
                k: h.get(k)
                for k in (
                    "kind",
                    "source_id",
                    "source_url",
                    "source",
                    "version",
                    "content_sha256",
                    "freshness",
                )
            }
            for h in context["rules"]
        ],
        "unknowns": context["unknowns"],
    }


def selection_strategy_context(context: dict[str, Any]) -> dict[str, Any]:
    """Compact selector projection of locally bound, complete helper results.

    Printed candidate/commander sources already travel on the selection wire.
    Hashes, quotes, offsets and full provenance remain in the unchanged context
    used by review. Only admitted rows may carry derived interaction conclusions.
    Unknown source identities and every conditional assumption remain explicit.
    """
    conditions: list[str] = []
    ability_profiles: list[dict[str, Any]] = []
    rows = []
    statuses: list[str] = []
    unknowns: list[str] = []
    commander_id = None
    for row in context.get("mana_evidence", {}).get("rows", []):
        commander = row.get("commander") or {}
        commander_id = commander.get("card_id") or commander_id
        for condition in row.get("conditions", []):
            if condition not in conditions:
                conditions.append(condition)
        projected = {
            "source_id": row["source"]["card_id"],
            "status": row["status"],
            "unknowns": row["unknowns"],
        }
        if row["status"] == "bounded_conditional_evidence" and row.get(
            "admitted_claims"
        ):
            abilities = [
                {
                    "activation_cost": ability["activation_cost"],
                    "source_mana": {
                        "quantity": ability["source_mana"]["quantity"],
                        "type_options": ability["source_mana"]["type_options"],
                        "restrictions": [
                            c["kind"] for c in ability["source_mana"]["constraints"]
                        ],
                        "spell_benefit": (
                            {
                                k: v
                                for k, v in ability["source_mana"][
                                    "spell_benefit"
                                ].items()
                                if k != "quote"
                            }
                            if ability["source_mana"]["spell_benefit"]
                            else None
                        ),
                    },
                }
                for ability in row["abilities"]
            ]
            projected["ability_refs"] = []
            for ability in abilities:
                if ability not in ability_profiles:
                    ability_profiles.append(ability)
                projected["ability_refs"].append(ability_profiles.index(ability))
        if row["status"] not in statuses:
            statuses.append(row["status"])
        for unknown in row["unknowns"]:
            if unknown not in unknowns:
                unknowns.append(unknown)
        rows.append(
            [
                projected["source_id"],
                statuses.index(row["status"]),
                [unknowns.index(u) for u in row["unknowns"]],
                projected.get("ability_refs", []),
                [conditions.index(c) for c in row.get("conditions", [])],
            ]
        )
    return {
        "scope": "Complete source-bound local helper; no availability or superiority. Unknown rows admit no claims; freshness unknown.",
        "commander_id": commander_id,
        "conditions": conditions,
        "row_fields": [
            "source_id",
            "status_ref",
            "unknown_refs",
            "ability_refs",
            "condition_refs",
        ],
        "statuses": statuses,
        "unknown_vocabulary": unknowns,
        "ability_fields": [
            "activation_cost",
            "quantity",
            "type_options",
            "restrictions",
            "spell_benefit",
        ],
        "ability_profiles": [
            [
                a["activation_cost"],
                *[
                    a["source_mana"][k]
                    for k in (
                        "quantity",
                        "type_options",
                        "restrictions",
                        "spell_benefit",
                    )
                ],
            ]
            for a in ability_profiles
        ],
        "bounded_rows_only": {
            "kinnan_extra_mana": {
                "quantity": 1,
                "type_choice": "One type actually produced by this source activation.",
                "inherits_source_spending_restriction": False,
                "inherits_source_spell_benefit": False,
            },
            "commander_outlet": "Printed {5}{G}{U} outlet; payment/availability unverified.",
        },
        "rows": rows,
        "rule_sources": [
            {"ref": h["source_id"], "text": h["text"]}
            for h in context.get("rules", [])
            if h.get("complete") and h.get("is_current")
        ],
        "unknowns": context.get("unknowns", []),
    }


def expand_review_sources(sources: list[str], context: dict[str, Any]) -> None:
    """Append the complete source basis without replacing list metadata/indices."""
    from sabermetrics.assistant_answer_review import _card_source

    for card in context["cards"]:
        # A disagreeing snapshot is abstention evidence, not permission to replace
        # the reviewed printed source or add a competing source of the same name.
        if card["snapshot_unknowns"]:
            continue
        source = _card_source(card)
        if not any(s.split("\n", 1)[0] == source.split("\n", 1)[0] for s in sources):
            sources.append(source)
    for hit in context["rules"]:
        prefix = "Ruling source: " if hit["kind"] == "card_ruling" else "Rule source: "
        source = (
            prefix
            + hit["source_id"]
            + "\n"
            + hit["text"]
            + "\nProvenance: "
            + json.dumps(
                {k: v for k, v in hit.items() if k != "text"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        existing = next(
            (
                i
                for i, s in enumerate(sources)
                if s.startswith(("Rule source: ", "Ruling source: "))
                and hit["text"] in s
            ),
            None,
        )
        if existing is None:
            sources.append(source)
        else:
            sources[existing] = source
