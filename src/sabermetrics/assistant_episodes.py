"""Owner-scoped reviewed investigation memory, never a new source of game facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabermetrics.assistant_candidate_recall import card_content
from sabermetrics.deck_evidence import _library_entries

VERSION = "assistant-episodes.v2"
MAX_EPISODE_CHARS = 12000
MAX_SCAN = 24


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def mechanical_identity(card: dict[str, Any]) -> str:
    facts = json.loads(card_content(card))
    for key in (
        "power",
        "toughness",
        "loyalty",
        "defense",
        "layout",
        "keywords",
        "card_faces",
        "color_identity",
        "oracle_text_truncated",
    ):
        value = bool(card.get(key)) if key == "oracle_text_truncated" else card.get(key)
        if key in {"card_faces", "keywords", "color_identity"} and isinstance(
            value, str
        ):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        facts[key] = value
    facts["mana_value"] = card.get("mana_value", card.get("cmc"))
    return _hash(facts)


def deck_evidence_hash(document: dict[str, Any]) -> str:
    """Include quantities, zones and printed mechanics, not editable UI metadata."""
    entries = [*_library_entries(document)]
    entries.extend(e for e in document.get("entries", []) if e.get("is_commander"))
    return _hash(
        sorted(
            [
                json.dumps(
                    {
                        "facts": mechanical_identity(e),
                        "quantity": e.get("quantity", 1),
                        "commander": bool(e.get("is_commander")),
                        "zone": e.get("zone_name"),
                    },
                    sort_keys=True,
                )
                for e in entries
            ]
        )
    )


def _catalog_identities(
    conn: sqlite3.Connection, ids: list[str], cache: dict[str, dict[str, Any] | None]
) -> dict[str, dict[str, Any] | None]:
    """Keep catalog and enriched lineage separate; never mask one with the other."""
    snapshots = (
        conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('assistant_card_fact_current','assistant_card_facts')"
        ).fetchone()[0]
        == 2
    )
    for cid in ids:
        if cid in cache:
            continue
        row = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
        if row is None:
            cache[cid] = None
            continue
        catalog = dict(row)
        identity: dict[str, Any] = {
            "catalog_sha256": mechanical_identity(catalog),
            "enriched_sha256": None,
            "snapshot_sha256": None,
        }
        if snapshots:
            enriched = conn.execute(
                "SELECT f.snapshot_hash, CASE WHEN length(f.payload)<=32000 THEN f.payload END FROM assistant_card_fact_current c JOIN assistant_card_facts f ON f.card_id=c.card_id AND f.snapshot_hash=c.snapshot_hash WHERE c.card_id=?",
                (cid,),
            ).fetchone()
            if enriched:
                if enriched[1] is None:
                    cache[cid] = None
                    continue
                facts = json.loads(enriched[1])
                if facts.get("oracle_id") != catalog.get("oracle_id") or facts.get(
                    "name"
                ) != catalog.get("name"):
                    cache[cid] = None
                    continue
                identity.update(
                    enriched_sha256=mechanical_identity(facts),
                    snapshot_sha256=enriched[0],
                )
        cache[cid] = identity
    return {cid: cache[cid] for cid in ids}


def _plan_attention(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded attention metadata, never repeated queries or evidence prose."""
    if not plan:
        return {}
    return {
        "summary_only": True,
        "plan_sha256": _hash(plan),
        **{
            key: str(plan.get(key) or "")[:80]
            for key in ("version", "plan_hash", "request_sha256")
        },
        "tasks": [
            {
                "id": str(task.get("id") or task.get("task_id") or "")[:80],
                "coverage": str(task.get("coverage") or "not_recorded")[:80],
                **{
                    key: str(task.get(key) or "")[:80]
                    for key in ("kind", "evidence_class")
                },
            }
            for task in (plan.get("tasks") or plan.get("results") or [])[:5]
        ],
    }


def _trim_narrative(
    episode: dict[str, Any], claim_limit: int, detail_limit: int
) -> None:
    """Only narrative is expendable; receipts and dependency guards are fixed."""
    fields = [(episode, "objective", min(500, claim_limit))]
    for hypothesis in episode["hypotheses"]:
        fields.extend(
            (hypothesis, key, limit)
            for key, limit in (
                ("claim", claim_limit),
                ("reason", min(240, detail_limit)),
                ("replacement", min(500, detail_limit)),
            )
        )
    for item, key, limit in fields:
        if len(item[key]) > limit:
            item[key] = item[key][:limit]
            item[f"{key}_truncated"] = True
            episode["narrative_compacted"] = True
    for hypothesis in episode["hypotheses"]:
        if hypothesis["claim_truncated"]:
            # The original review is a receipt, not a verdict on an excerpt.
            hypothesis["verdict"] = "unverified"
            hypothesis["unverified_remainder"] = True


def capture_episode(
    document: dict[str, Any],
    request: str,
    cards: dict[str, dict[str, Any]],
    selection: dict[str, Any] | None,
    review: dict[str, Any] | None,
    prompt_version: str,
    retrieval_plan: dict[str, Any] | None = None,
    claim_parts: list[str] | None = None,
    db_path: str | Path | None = None,
    _diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist only reviewed receipts; unknown/failed hypotheses stay negative leads."""
    started = time.monotonic()
    diagnostics = _diagnostics if _diagnostics is not None else {}
    diagnostics.update(
        input_sha256=_hash(
            {
                "deck": deck_evidence_hash(document),
                "request": request,
                "selection": selection,
                "review": review,
                "claim_parts": claim_parts,
            }
        ),
        deck_entries=len(document.get("entries", [])),
    )

    def skipped(reason: str, **details: Any) -> None:
        diagnostics.update(
            status="skipped",
            reason=reason,
            elapsed_seconds=round(time.monotonic() - started, 6),
            **details,
        )

    if not selection:
        skipped("missing_selection")
        return None
    if not review or not review.get("claim_judgments"):
        skipped("missing_review_judgments")
        return None
    if len(document.get("entries", [])) > 400:
        skipped("deck_entry_limit", deck_entries=len(document["entries"]))
        return None
    ids = list(
        dict.fromkeys(
            i for g in selection.get("groups", []) for i in g.get("card_ids", [])
        )
    )[:6]
    if not ids:
        skipped("no_selected_candidates")
        return None
    if any(i not in cards for i in ids):
        skipped(
            "missing_selected_evidence",
            missing_candidates=sum(i not in cards for i in ids),
        )
        return None
    judgments = review["claim_judgments"]
    dependencies = {
        str(c.get("card_id") or c.get("id")): c
        for c in [
            *_library_entries(document),
            *[e for e in document.get("entries", []) if e.get("is_commander")],
        ]
        if c.get("card_id") or c.get("id")
    }
    dependencies.update({i: cards[i] for i in ids})
    if db_path is None:
        skipped("catalog_recheck_unavailable")
        return None
    try:
        with sqlite3.connect(
            Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 0.25
            connection.set_progress_handler(
                lambda: int(time.monotonic() > deadline), 1000
            )
            connection.execute("BEGIN")
            identities = _catalog_identities(connection, sorted(dependencies), {})
        if not all(identities.values()):
            skipped(
                "dependency_evidence_unavailable",
                dependency_count=len(dependencies),
                missing_dependencies=sum(v is None for v in identities.values()),
            )
            return None
    except (sqlite3.Error, ValueError, TypeError) as exc:
        skipped("catalog_recheck_failed", error_class=type(exc).__name__)
        return None
    claims = [part for part in (claim_parts or [])[1:] if part.strip()]
    hypotheses = []
    for index, judgment in enumerate(judgments[:6]):
        claim_id = str(judgment.get("claim_id", ""))[:80]
        position = int(claim_id[1:]) if re.fullmatch(r"c\d+", claim_id) else index
        if review.get("claim_unit_version"):
            from sabermetrics.assistant_claim_fragments import reviewed_claim_text

            claim = reviewed_claim_text(review, judgment, claim_parts or [])
        else:
            claim = claims[position] if position < len(claims) else ""
        context_prefix = ""
        if claim and review.get("claim_unit_version") and claim_parts:
            parent = claim_parts[judgment["part_index"]]
            context_prefix = parent.split(" — ", 1)[0][:300] if " — " in parent else ""
        bindings = [
            i
            for i in ids
            if str(cards[i].get("name") or "").casefold()
            in (context_prefix + " " + claim).casefold()
        ]
        hypotheses.append(
            {
                "claim_id": claim_id,
                **(
                    {
                        "candidate_context": context_prefix,
                        "reviewed_part_sha256": judgment.get("part_sha256"),
                    }
                    if context_prefix
                    else {}
                ),
                "claim": claim,
                "claim_sha256": hashlib.sha256(claim.encode()).hexdigest(),
                "claim_truncated": False,
                "card_ids": bindings,
                "category": judgment.get("category", "unknown"),
                "verdict": (
                    judgment.get("verdict")
                    if claim
                    and judgment.get("verdict")
                    in {"supported", "contradicted", "unverified"}
                    else "unverified"
                ),
                "reviewed_verdict": judgment.get("verdict", "unverified"),
                "unverified_remainder": bool(judgment.get("unverified_remainder"))
                or not claim,
                "reason": str(judgment.get("reason", "")),
                "reason_truncated": False,
                "correction_applied": judgment.get("correction_applied") is True,
                "replacement": str(judgment.get("replacement") or ""),
                "replacement_sha256": hashlib.sha256(
                    str(judgment.get("replacement") or "").encode()
                ).hexdigest(),
                "replacement_truncated": False,
                "evidence": [
                    {
                        "source_index": c.get("source_index"),
                        "quote_sha256": _hash(c.get("quote")),
                        "source_sha256": (
                            (review.get("source_sha256") or [])[c["source_index"]]
                            if isinstance(c.get("source_index"), int)
                            and 0
                            <= c["source_index"]
                            < len(review.get("source_sha256") or [])
                            else None
                        ),
                    }
                    for c in judgment.get("citations", [])[:4]
                ],
            }
        )
    episode = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "prompt_version": prompt_version,
        "deck_evidence_sha256": deck_evidence_hash(document),
        "objective": request,
        "objective_truncated": False,
        "narrative_compacted": bool(retrieval_plan),
        "retrieval_plan": _plan_attention(retrieval_plan or {}),
        "card_fingerprints": [
            {
                "card_id": i,
                "facts_sha256": _hash(identities[i]),
                "supplied_fact_sha256": mechanical_identity(cards[i]),
            }
            for i in ids
        ],
        "dependency_card_ids": sorted(dependencies),
        "dependency_sha256": _hash(identities),
        "dependency_scope": "Separately captured current catalog mechanics and enriched snapshot lineage; supplied saved-entry text remains separately bound by deck_evidence_sha256.",
        "review_kind": "automated_source_review_not_expert_certification",
        "hypotheses": hypotheses,
        "rules_source_hashes": list(review.get("source_sha256", []))[:20],
        "reuse_scope": "Investigation leads only. Recheck facts, rules and suitability; adoption is not evidence of strength.",
    }
    episode["investigation_sha256"] = _hash(
        {
            "deck": episode["deck_evidence_sha256"],
            "objective": request,
            "cards": episode["card_fingerprints"],
            "claims": [h["claim_sha256"] for h in hypotheses],
        }
    )
    # Keep useful narrative even at the smallest budget. A hash-only receipt
    # must not consume recall slots ahead of substantive investigations.
    for claim_limit, detail_limit in (
        (1800, 500),
        (900, 240),
        (450, 120),
        (180, 60),
    ):
        _trim_narrative(episode, claim_limit, detail_limit)
        size = len(json.dumps(episode))
        if size <= MAX_EPISODE_CHARS:
            diagnostics.update(
                status="captured",
                reason="reviewed_receipt_attached",
                episode_chars=size,
                dependency_count=len(dependencies),
                hypothesis_count=len(hypotheses),
                narrative_compacted=episode["narrative_compacted"],
                investigation_sha256=episode["investigation_sha256"],
                elapsed_seconds=round(time.monotonic() - started, 6),
            )
            return episode
    skipped(
        "required_receipt_exceeds_budget",
        minimum_receipt_chars=size,
        maximum_episode_chars=MAX_EPISODE_CHARS,
        dependency_count=len(dependencies),
    )
    return None


def capture_episode_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return observable capture outcome while preserving the legacy API."""
    diagnostics: dict[str, Any] = {}
    kwargs = {**kwargs, "_diagnostics": diagnostics}
    episode = capture_episode(*args, **kwargs)
    return {
        **diagnostics,
        "episode": episode,
        "attachment_status": "attached" if episode is not None else "not_attached",
        "persistence_status": (
            "pending_store" if episode is not None else "not_applicable"
        ),
    }


def recall_episodes(
    path: str | Path,
    owner: str,
    deck_id: str,
    document: dict[str, Any],
    *,
    query: str = "",
    prompt_version: str = "",
    limit: int = 3,
) -> dict[str, Any]:
    """Bounded read-only recall; all returned facts are rehashed from current catalog."""
    if not 1 <= limit <= 5:
        raise ValueError("episode limit must be between one and five")
    result: dict[str, Any] = {
        "version": VERSION,
        "status": "no_history",
        "items": [],
        "skipped_stale": 0,
        "skip_reasons": {},
        "scan_limit": MAX_SCAN,
        "scope": "Owner/deck investigation leads, not reusable factual advice. Rules and strategic judgments always require a new check.",
    }
    if str(document.get("id")) != deck_id:
        return {**result, "status": "not_found"}
    try:
        with sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=250")
            deadline = time.monotonic() + 0.25
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute("BEGIN")
            if not conn.execute(
                "SELECT 1 FROM deck_documents WHERE id=? AND owner_id=?",
                (deck_id, owner),
            ).fetchone():
                return {**result, "status": "not_found"}
            rows = conn.execute(
                "SELECT json_extract(m.value,'$.research.reviewed_episode') FROM build_assistant_sessions s, json_each(s.payload_json,'$.messages') m WHERE s.owner_id=? AND s.deck_id=? AND json_valid(s.payload_json) AND json_extract(m.value,'$.role')='assistant' AND json_extract(m.value,'$.research.reviewed_episode') IS NOT NULL AND length(json_extract(m.value,'$.research.reviewed_episode'))<=? ORDER BY CAST(m.key AS INTEGER) DESC LIMIT ?",
                (owner, deck_id, MAX_EPISODE_CHARS, MAX_SCAN),
            ).fetchall()
            fingerprint = deck_evidence_hash(document)
            candidates = []
            seen = set()
            card_cache: dict[str, dict[str, Any] | None] = {}
            for (raw,) in rows:
                try:
                    ep = json.loads(raw)
                    age = (
                        datetime.now(UTC) - datetime.fromisoformat(ep["created_at"])
                    ).total_seconds()
                    reasons = []
                    if ep.get("version") != VERSION:
                        reasons.append("episode_version")
                    if ep.get("deck_evidence_sha256") != fingerprint:
                        reasons.append("deck_facts_changed")
                    if not 0 <= age <= 30 * 86400:
                        reasons.append("age_outside_window")
                    if prompt_version and ep.get("prompt_version") != prompt_version:
                        reasons.append("prompt_version")
                    refs = ep.get("card_fingerprints", [])
                    if not 0 < len(refs) <= 6:
                        reasons.append("candidate_dependencies_invalid")
                    dependency_ids = ep.get("dependency_card_ids", [])
                    if (
                        not isinstance(dependency_ids, list)
                        or not 1 <= len(dependency_ids) <= 406
                    ):
                        reasons.append("dependency_list_invalid")
                    else:
                        dependency_facts = _catalog_identities(
                            conn, dependency_ids, card_cache
                        )
                        if not all(dependency_facts.values()):
                            reasons.append("dependency_evidence_unavailable")
                        elif _hash(dependency_facts) != ep.get("dependency_sha256"):
                            reasons.append("dependency_facts_changed")
                    for ref in refs[:6]:
                        state = card_cache.get(ref["card_id"])
                        if not state or _hash(state) != ref.get("facts_sha256"):
                            reasons.append("candidate_facts_changed")
                    if reasons:
                        result["skipped_stale"] += 1
                        for reason in set(reasons):
                            result["skip_reasons"][reason] = (
                                result["skip_reasons"].get(reason, 0) + 1
                            )
                        continue
                    for hypothesis in ep.get("hypotheses", []):
                        if hypothesis.get("claim_truncated"):
                            # Older v2 receipts lack a full-claim hash. Keep the
                            # history, but never present their prefix as reviewed.
                            hypothesis.setdefault(
                                "reviewed_verdict",
                                hypothesis.get("verdict", "unverified"),
                            )
                            hypothesis["verdict"] = "unverified"
                            hypothesis["unverified_remainder"] = True
                    signature = ep.get("investigation_sha256")
                    if not signature or signature in seen:
                        reason = (
                            "missing_investigation_identity"
                            if not signature
                            else "duplicate_investigation"
                        )
                        result["skip_reasons"][reason] = (
                            result["skip_reasons"].get(reason, 0) + 1
                        )
                        continue
                    seen.add(signature)
                    words = set(re.findall(r"[a-z]{4,}", query.lower()))
                    score = len(
                        words
                        & set(re.findall(r"[a-z]{4,}", ep.get("objective", "").lower()))
                    )
                    candidates.append((score, ep))
                except (ValueError, TypeError, KeyError, AttributeError):
                    result["skipped_stale"] += 1
                    result["skip_reasons"]["invalid_receipt"] = (
                        result["skip_reasons"].get("invalid_receipt", 0) + 1
                    )
            candidates.sort(key=lambda row: -row[0])
            result["items"] = [
                {
                    **ep,
                    "current_catalog_facts_rechecked": True,
                    "rules_freshness": "requires_revalidation",
                    "strategic_validity": "requires_revalidation",
                }
                for _, ep in candidates[:limit]
            ]
            if result["items"]:
                result["status"] = "available"
    except sqlite3.Error:
        result["status"] = "unavailable"
        result["skip_reasons"]["database_unavailable_or_deadline"] = 1
    return result
