"""Bounded local source preparation for whole-deck engine explanations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sabermetrics.assistant_rules import WIZARDS_HOSTS, retrieve_rules

ENGINE_EXPLANATION_CONTRACT = (
    "Explain enablers, costs and required zones -> payoffs and actual recipients -> "
    "resource renewal -> stopping bounds and finite versus infinite repetition -> "
    "timing, current state and opponent intervention -> lethal shortfall -> "
    "alternate independent finish versus support. Supported vocabulary coverage never "
    "certifies a loop or current resource availability. Retrieved sources are "
    "candidate evidence, never an interaction verdict; missing semantics stay unknown."
)

_MAX_HITS = 8
_MAX_CHARS = 12_000
# Deliberately lexical obligations, not a classifier or a catalogue of engines.
# Ordinary verbs such as draw, play and counter do not trigger broad cautions.
_ACTIONS = {
    "explore": r"explor(?:e|es|ed|ing)",
    "connive": r"conniv(?:e|es|ed|ing)",
    "dredge": r"dredg(?:e|es|ed|ing)",
    "surveil": r"surveil(?:s|ed|ing)?",
    "discover": r"discover(?:s|ed|ing)?",
    "proliferate": r"proliferat(?:e|es|ed|ing)",
    "amass": r"amass(?:es|ed|ing)?",
    "incubate": r"incubat(?:e|es|ed|ing)",
    "mill": r"mill(?:s|ed|ing)?",
    "scry": r"scr(?:y|ies|ied|ying)",
    "adapt": r"adapt(?:s|ed|ing)?",
    "populate": r"populat(?:e|es|ed|ing)",
    "investigate": r"investigat(?:e|es|ed|ing)",
    "manifest": r"manifest(?:s|ed|ing)?",
    "cloak": r"cloak(?:s|ed|ing)?",
    "venture": r"ventur(?:e|es|ed|ing)",
    "learn": r"learn(?:s|ed|ing)?",
    "fight": r"fight(?:s|ing)?|fought",
    "monstrosity": r"monstrosity",
}
_PATTERNS = {
    k: re.compile(r"\b(?:" + v + r")\b", re.IGNORECASE) for k, v in _ACTIONS.items()
}
_IDENTITY = (
    "kind",
    "source_id",
    "content_sha256",
    "source_url",
    "version",
    "source",
    "card_oracle_id",
)
_SCOPE = (
    "Lexical candidate sources for the explicit supported action vocabulary only; "
    "other printed mechanics may be unsupported and remain unknown. "
    "Full rule text matching a term does not "
    "prove entailment, complete mechanic semantics, loop validity, current "
    "resources, or lethal damage. Missing or capped evidence remains unknown."
)
_SNAPSHOT_IDENTITY = ("source_url", "source_version", "content_sha256")


def _source(hit: dict[str, Any]) -> dict[str, Any]:
    return {k: hit.get(k) for k in (*_IDENTITY, "text_truncated")}


def _rejection(hit: dict[str, Any]) -> str | None:
    text = hit.get("text")
    if hit.get("text_truncated") is not False:
        return "truncated_or_unspecified_completeness"
    if not isinstance(text, str) or not text.strip():
        return "missing_text"
    if hashlib.sha256(text.encode()).hexdigest() != hit.get("content_sha256"):
        return "content_hash_mismatch"
    if not all(hit.get(k) for k in ("source_id", "version", "source")):
        return "missing_identity_or_provenance"
    try:
        parsed = urlsplit(str(hit.get("source_url") or ""))
        official = (
            parsed.scheme == "https"
            and parsed.hostname in WIZARDS_HOSTS
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        official = False
    if hit.get("kind") == "rule":
        return None if official else "unofficial_rule_source"
    if hit.get("kind") == "card_ruling":
        # Preserve attributed stored rulings without upgrading them to official
        # rules or using them to satisfy a general mechanic-text obligation.
        return None if hit.get("card_oracle_id") else "unscoped_ruling"
    return "unsupported_source_kind"


def _matches(hit: dict[str, Any], term: str) -> bool:
    text = hit["text"].strip()
    # A complete heading is still not a complete statement of an action.
    body = re.sub(r"^\d{3}\.\d+[a-z]?\.?\s+", "", text)
    if body.strip(". \t\n").lower() in _ACTIONS:
        return False
    return hit.get("kind") == "rule" and bool(_PATTERNS[term].search(text))


def _compatible(record: dict[str, Any], selected: dict[str, Any] | None) -> bool:
    hit, snapshot = record["hit"], record["snapshot"]
    if hit["kind"] != "rule":
        return True
    # Rule text hashes identify sections, not editions. The reader's separate
    # snapshot hash identifies the whole imported file; never infer it from hits.
    if (
        selected is None
        or not _complete_snapshot(selected)
        or not _complete_snapshot(snapshot)
    ):
        return False
    return (hit.get("source_url"), hit.get("version")) == (
        selected.get("source_url"),
        selected.get("source_version"),
    ) and (all(snapshot.get(k) == selected.get(k) for k in _SNAPSHOT_IDENTITY))


def _complete_snapshot(snapshot: Any) -> bool:
    return isinstance(snapshot, dict) and all(
        isinstance(snapshot.get(k), str) and snapshot[k].strip()
        for k in _SNAPSHOT_IDENTITY
    )


def engine_rule_evidence(
    db_path: Path,
    cards: list[dict[str, Any]],
    existing: dict[str, Any],
    *,
    check_current: Callable[[], Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge complete local sources with at most two printed-mechanic lookups.

    Commander obligations precede remaining cards, then printed occurrence
    order breaks ties. Scan all supplied Oracle text (including faces), never
    names, tags or inferred roles. Retain at most eight hits / 12,000 text chars.
    ``check_current`` runs before/after each lookup and before return, and its
    exceptions propagate. Inputs are not mutated. No network or model calls.
    """
    started = time.monotonic()
    guard = check_current or (lambda: None)
    guard()
    ordered = sorted(cards, key=lambda c: not bool(c.get("is_commander")))
    obligations: dict[str, list[dict[str, Any]]] = {}
    incomplete_cards = []
    for index, card in enumerate(ordered):
        faces = card.get("card_faces") or []
        parts = [card, *(f for f in faces if isinstance(f, dict))]
        required_parts = parts[1:] if faces else [card]
        if any(p.get("oracle_text_truncated") for p in parts) or any(
            not p.get("oracle_text") for p in required_parts
        ):
            incomplete_cards.append(card.get("oracle_id") or card.get("name") or index)
        text = "\n".join(str(p.get("oracle_text") or "") for p in parts)
        # Self references to a name containing an action are identity, not action.
        for name in [p.get("name") for p in parts]:
            if name:
                text = re.sub(
                    r"(?<!\w)" + re.escape(str(name)) + r"(?!\w)",
                    " ",
                    text,
                    flags=re.IGNORECASE,
                )
        occurrences = sorted(
            (
                (m.start(), term)
                for term, pattern in _PATTERNS.items()
                if (m := pattern.search(text))
            ),
        )
        for _, term in occurrences:
            obligations.setdefault(term, []).append(card)

    omissions: list[dict[str, Any]] = []
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    snapshots = []
    lookups = []
    limitations = list(existing.get("limitations", []))
    selected_snapshot = existing.get("rules_snapshot")
    oracle_ids = {str(c["oracle_id"]) for c in cards if c.get("oracle_id")}

    def add_hits(result: dict[str, Any], origin: str) -> None:
        snapshot = result.get("rules_snapshot")
        if snapshot and snapshot not in snapshots:
            snapshots.append(snapshot)
        for supplied in result.get("hits", []):
            hit = dict(supplied)
            reason = _rejection(hit)
            if (
                not reason
                and hit.get("kind") == "card_ruling"
                and str(hit.get("card_oracle_id")) not in oracle_ids
            ):
                reason = "ruling_outside_supplied_cards"
            if reason:
                omissions.append({**_source(hit), "origin": origin, "reason": reason})
                continue
            key = tuple(hit.get(k) for k in _IDENTITY)
            previous = unique.get(key)
            if previous and not _compatible(previous, selected_snapshot):
                omissions.append(
                    {
                        **_source(previous["hit"]),
                        "origin": previous["origin"],
                        "reason": "incompatible_rules_snapshot",
                    }
                )
            if previous is None or not _compatible(previous, selected_snapshot):
                unique[key] = {"hit": hit, "snapshot": snapshot, "origin": origin}

    add_hits(existing, "existing")
    queried: set[str] = set()
    for _ in range(2):
        # Only existing full rules can avoid a lookup; a ruling is not the
        # general action definition. This is lexical coverage, never entailment.
        # Reconsider earlier obligations if a lookup changes the snapshot.
        pending_term = next(
            (
                t
                for t in obligations
                if t not in queried
                and not any(
                    _compatible(record, selected_snapshot)
                    and _matches(record["hit"], t)
                    for record in unique.values()
                )
            ),
            None,
        )
        if pending_term is None:
            break
        term = pending_term
        relevant_cards = obligations[term]
        guard()
        query_started = time.monotonic()
        queried.add(term)
        # Each query is an actual printed term, not a bundle of generic cautions.
        # Scope concept seeds and rulings to cards actually printing that term.
        scoped: dict[str, dict[str, Any]] = {}
        for card in relevant_cards:
            identity = str(card.get("oracle_id") or card.get("name") or "")
            if identity:
                scoped.setdefault(identity, card)
        try:
            result = retrieve_rules(
                db_path,
                term,
                cards=list(scoped.values())[:16],
                limit=6,
                deadline_seconds=0.35,
            ).model_dump()
        except (OSError, ValueError, sqlite3.Error) as exc:
            result = {
                "status": "unavailable",
                "hits": [],
                "rulings_status": "unavailable",
                "limitations": [],
                "query_failures": [
                    {"stage": "engine_lookup", "exception_type": type(exc).__name__}
                ],
            }
        finally:
            guard()
        if _complete_snapshot(result.get("rules_snapshot")):
            selected_snapshot = result["rules_snapshot"]
        add_hits(result, term)
        limitations.extend(result.get("limitations", []))
        lookups.append(
            {
                "query": term,
                "status": result["status"],
                "elapsed_seconds": round(time.monotonic() - query_started, 6),
                "rulings_status": result.get("rulings_status", "unavailable"),
                "rules_snapshot": result.get("rules_snapshot"),
                "limitations": result.get("limitations", []),
                "query_failures": result.get("query_failures", []),
                "cards_scoped": len(list(scoped)[:16]),
                "cards_omitted": max(0, len(scoped) - 16),
            }
        )

    candidates = []
    for record in unique.values():
        hit = record["hit"]
        if not _compatible(record, selected_snapshot):
            omissions.append(
                {
                    **_source(hit),
                    "origin": record["origin"],
                    "reason": "incompatible_rules_snapshot",
                }
            )
            continue
        candidates.append(hit)
    # Mechanic sources get the budget first, commander terms first. Stable input
    # order retains all other existing/fresh candidates when space permits.
    terms = list(obligations)
    candidates.sort(
        key=lambda h: next(
            (i for i, t in enumerate(terms) if _matches(h, t)), len(terms)
        )
    )
    retained: list[dict[str, Any]] = []
    chars = 0
    for hit in candidates:
        if len(retained) >= _MAX_HITS or chars + len(hit["text"]) > _MAX_CHARS:
            omissions.append({**_source(hit), "reason": "total_source_budget"})
            continue
        retained.append(hit)
        chars += len(hit["text"])

    coverage = []
    for term in terms:
        matched = [_source(h) for h in retained if _matches(h, term)]
        coverage.append(
            {
                "term": term,
                "commander": any(c.get("is_commander") for c in obligations[term]),
                "status": "candidate_evidence" if matched else "unknown",
                "reason": (
                    "full_text_term_match_only"
                    if matched
                    else (
                        "total_source_budget"
                        if any(_matches(h, term) for h in candidates)
                        else (
                            "no_retained_complete_matching_rule"
                            if term in queried
                            else "lookup_budget_omitted"
                        )
                    )
                ),
                "sources": matched,
                "semantics_verified": False,
            }
        )
    queries_omitted = sum(c["reason"] == "lookup_budget_omitted" for c in coverage)
    if omissions:
        limitations.append(
            "Rejected or capped sources were withheld; inspect engine evidence omissions."
        )
    if incomplete_cards:
        limitations.append(
            "Some supplied Oracle text is missing or truncated; printed obligations are incomplete."
        )
    limitations.append(_SCOPE)
    dependencies = {
        "actions": coverage,
        "scope": _SCOPE,
        "coverage_scope": "supported_action_vocabulary_only",
        "supported_action_vocabulary": list(_ACTIONS),
        "supported_vocabulary_scan_complete": not incomplete_cards,
        "printed_scope_complete": False,
        "potential_unsupported_mechanics": "unknown",
        "oracle_text_incomplete": incomplete_cards,
        "all_terms_have_candidate_sources": bool(coverage)
        and all(c["sources"] for c in coverage),
        "semantics_verified": False,
    }
    merged = dict(existing)
    merged.update(
        {
            "hits": retained,
            "status": (
                "available"
                if retained
                else (
                    "unavailable"
                    if any(q["status"] == "unavailable" for q in lookups)
                    or existing.get("status") == "unavailable"
                    else "no_matches"
                )
            ),
            "rules_snapshot": selected_snapshot,
            "limitations": list(dict.fromkeys(limitations)),
            "hits_omitted": existing.get("hits_omitted", 0) + len(omissions),
            "engine_evidence_dependencies": dependencies,
            "engine_evidence_omissions": omissions,
            "engine_lookups": lookups,
        }
    )
    receipt = {
        "stage": "engine_rule_evidence",
        "version": "assistant-engine-evidence.v1",
        "status": merged["status"],
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "lookup_count": len(lookups),
        "queries": lookups,
        "queries_omitted": queries_omitted,
        "hits_retained": len(retained),
        "text_chars_retained": chars,
        "hits_omitted": merged["hits_omitted"],
        "omissions": omissions,
        "sources": [_source(h) for h in retained],
        "rules_snapshots": snapshots,
        "evidence_dependencies": dependencies,
        "limitations": merged["limitations"],
        "scope": _SCOPE,
    }
    guard()
    return merged, receipt
