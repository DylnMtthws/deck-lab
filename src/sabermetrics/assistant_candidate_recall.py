"""Independent bounded recall channels over legal canonical card facts.

Mirrors the generator's union-recall architecture without importing its runtime,
budget preferences, optimizer, or quadratic synergy matrix.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from sabermetrics.assistant_strategy import (
    EvidenceModel,
    StrategyContext,
    strategy_capabilities,
)
from sabermetrics.card_eligibility import supplemental_object_reason
from sabermetrics.combo_catalog import known_combinations
from sabermetrics.deck_evidence import _library_entries

CHANNEL_QUOTA = 24
MAX_CANONICAL_CARDS = 50_000
MAX_ASSOCIATION_ANCHORS = 2
FAMILIES = {
    "selection": r"\bscry\b|\bsurveil\b|look at the top",
    "draw": r"\bdraw\b",
    "mana": r"\badd\b|search your library[^.]*\bland\b",
    "interaction": r"counter target|destroy target|exile target",
    "protection": r"hexproof|indestructible|phases out",
    "lands": r"whenever a land|land[^.]*graveyard|additional land",
    "recursion": r"from your graveyard",
    "army": r"amass|the ring tempts you",
    "counters": r"\bcounters?\b",
}
_FAMILY_PATTERNS = {
    key: re.compile(value, re.IGNORECASE) for key, value in FAMILIES.items()
}
_STOP = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "and",
    "or",
    "you",
    "your",
    "for",
    "with",
    "deck",
    "improve",
    "better",
    "whenever",
    "card",
    "cards",
    "spell",
    "spells",
}


class RecallCandidate(EvidenceModel):
    card: dict[str, Any]
    channels: list[str]
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class RecallResult(EvidenceModel):
    version: Literal["assistant-recall.v1"] = "assistant-recall.v1"
    strategy_fingerprint: str
    corpus_cards: int
    corpus_truncated: bool
    candidates: list[RecallCandidate]
    channel_counts: dict[str, int]
    channel_status: dict[str, str]
    limitations: list[str]


def card_content(card: dict[str, Any]) -> str:
    """Canonical text identity shared by vector preparation and runtime checks."""
    return json.dumps(
        {
            key: card.get(key)
            for key in ("oracle_id", "name", "type_line", "oracle_text", "mana_cost")
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _colors(value: Any) -> set[str] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, list) or any(
        not isinstance(c, str) or c not in {"W", "U", "B", "R", "G"} for c in value
    ):
        return None
    return set(value)


def _tokens(text: str) -> list[str]:
    return [
        term
        for term in re.findall(r"[a-z][a-z0-9]+", text.lower())
        if term not in _STOP
    ]


def _lexical(cards: list[dict[str, Any]], query: str) -> dict[str, float]:
    terms = set(_tokens(query))
    if not terms:
        return {}
    counts = [
        Counter(
            _tokens(
                " ".join(
                    str(c.get(key) or "")
                    for key in ("name", "type_line", "oracle_text")
                )
            )
        )
        for c in cards
    ]
    average = sum(sum(c.values()) for c in counts) / max(1, len(cards))
    df = {t: sum(t in c for c in counts) for t in terms}
    scores = {}
    for card, row in zip(cards, counts):
        score = 0.0
        for term in terms & row.keys():
            frequency = row[term]
            score += (
                math.log(1 + (len(cards) - df[term] + 0.5) / (df[term] + 0.5))
                * frequency
                * 2.2
                / (
                    frequency
                    + 1.2 * (0.25 + 0.75 * sum(row.values()) / max(1, average))
                )
            )
        if score:
            scores[str(card["oracle_id"])] = score
    return scores


def _semantic(
    conn: sqlite3.Connection, cards: dict[str, dict[str, Any]], query: str
) -> tuple[list[tuple[str, float]], str, int]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='assistant_card_vectors' AND type='table'"
    ).fetchone():
        return [], "unavailable_no_card_vectors", 0
    from sabermetrics.reference_layer.strategy_corpus import local_embedding_service

    try:
        service = local_embedding_service()
        if service is None:
            return [], "unavailable_no_local_encoder", 0
        query_vector = list(service.embed(query))
        if not query_vector or not all(math.isfinite(float(v)) for v in query_vector):
            return [], "unavailable_invalid_query_vector", 0
        norm = math.sqrt(sum(float(v) ** 2 for v in query_vector))
        if norm == 0:
            return [], "unavailable_invalid_query_vector", 0
        scores, rejected = [], 0
        for oid, digest, model, raw in conn.execute(
            "SELECT oracle_id,content_hash,embedding_model,embedding_json FROM assistant_card_vectors LIMIT ?",
            (MAX_CANONICAL_CARDS + 1,),
        ):
            card = cards.get(oid)
            if card is None:
                continue
            try:
                vector = json.loads(raw)
                if (
                    model != service._model_name
                    or digest != hashlib.sha256(card_content(card).encode()).hexdigest()
                ):
                    raise ValueError("stale artifact")
                if (
                    not isinstance(vector, list)
                    or len(vector) != len(query_vector)
                    or any(
                        type(v) not in (int, float) or not math.isfinite(v)
                        for v in vector
                    )
                ):
                    raise ValueError("invalid vector")
                denominator = norm * math.sqrt(sum(v * v for v in vector))
                if not denominator:
                    raise ValueError("zero vector")
                cosine = (
                    sum(float(a) * b for a, b in zip(query_vector, vector))
                    / denominator
                )
                # Candidate recall has a separate acceptance review. An absolute
                # citation-retrieval threshold erased this entire channel on a
                # real commander corpus; retain the best positive neighbors.
                if cosine > 0:
                    scores.append((oid, round(cosine, 6)))
            except (ValueError, TypeError):
                rejected += 1
        return (
            sorted(scores, key=lambda row: (-row[1], row[0]))[:CHANNEL_QUOTA],
            "available",
            rejected,
        )
    except (ImportError, RuntimeError, OSError, ValueError, sqlite3.OperationalError):
        return [], "unavailable_invalid_or_incompatible_artifact", 0


def recall_candidates(
    path: str | Path, document: dict[str, Any], strategy: StrategyContext, focus: str
) -> RecallResult:
    """Retain each recall channel independently, then require separate swap review."""
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    allowed: set[str] = set()
    for commander in commanders:
        identity = _colors(commander.get("color_identity"))
        if identity is None:
            return RecallResult(
                strategy_fingerprint=strategy.fingerprint,
                corpus_cards=0,
                corpus_truncated=False,
                candidates=[],
                channel_counts={},
                channel_status={"corpus": "unknown_commander_colors"},
                limitations=[
                    "Commander color identity is unknown; no color-legal pool can be established."
                ],
            )
        allowed.update(identity)
    if not commanders:
        return RecallResult(
            strategy_fingerprint=strategy.fingerprint,
            corpus_cards=0,
            corpus_truncated=False,
            candidates=[],
            channel_counts={},
            channel_status={"corpus": "missing_commander"},
            limitations=["No commander supplied."],
        )
    present = {str(c.get("oracle_id")) for c in document.get("entries", [])}
    retained: dict[str, RecallCandidate] = {}
    channels: dict[str, int] = {}
    statuses = {"mechanical": "available", "lexical": "available"}
    limitations = [
        "Recall scores indicate discovery relevance, never mechanical synergy or a measured upgrade."
    ]
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,oracle_id,name,type_line,oracle_text,mana_cost,color_identity,cmc,is_legal_in_99 FROM cards WHERE is_legal_in_99=1 AND oracle_id IS NOT NULL AND length(trim(oracle_id))>0 GROUP BY oracle_id HAVING id=MIN(id) ORDER BY oracle_id LIMIT ?",
            (MAX_CANONICAL_CARDS + 1,),
        ).fetchall()
        truncated = len(rows) > MAX_CANONICAL_CARDS
        cards = []
        for row in rows[:MAX_CANONICAL_CARDS]:
            card = dict(row)
            identity = _colors(card.get("color_identity"))
            if (
                identity is None
                or not identity.issubset(allowed)
                or supplemental_object_reason(card)
            ):
                continue
            if (
                str(card["oracle_id"]) in present
                and "basic land" not in str(card.get("type_line") or "").lower()
            ):
                continue
            cards.append(card)
        by_oid = {str(c["oracle_id"]): c for c in cards}
        query = " ".join(
            [
                str(focus)[:320],
                str(strategy.preferences.get("theme") or "")[:320],
                *[
                    c.capability.replace(":", " ").replace("_", " ")
                    for c in strategy.contracts
                ],
            ]
        )
        lexical = _lexical(cards, query)

        def keep(
            channel: str,
            ids: list[str],
            evidence: dict[str, dict[str, Any]] | None = None,
        ) -> None:
            actual = 0
            for oid in ids[:CHANNEL_QUOTA]:
                if oid not in by_oid:
                    continue
                if oid not in retained:
                    retained[oid] = RecallCandidate(card=by_oid[oid], channels=[])
                candidate = retained[oid]
                if channel not in candidate.channels:
                    candidate.channels.append(channel)
                    actual += 1
                    if evidence and oid in evidence:
                        candidate.evidence.append({"channel": channel, **evidence[oid]})
            channels[channel] = actual

        def order(card: dict[str, Any]) -> tuple[Any, ...]:
            value = card.get("cmc")
            return (
                -lexical.get(str(card["oracle_id"]), 0),
                (
                    value
                    if isinstance(value, (int, float)) and math.isfinite(value)
                    else 100
                ),
                str(card.get("name")),
                str(card["oracle_id"]),
            )

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for card in cards:
            text = str(card.get("oracle_text") or "")
            for name, pattern in _FAMILY_PATTERNS.items():
                if pattern.search(text):
                    groups["function:" + name].append(card)
            if "land" in str(card.get("type_line") or "").split(" // ")[0].lower():
                groups["function:physical_land"].append(card)
            caps = strategy_capabilities(card)
            for contract in strategy.contracts:
                if contract.capability.startswith("cast:"):
                    from sabermetrics.cast_trigger_evidence import (
                        cast_trigger_compatibility,
                    )

                    commander = next(
                        c
                        for c in commanders
                        if str(c.get("name") or "") == contract.commander
                    )
                    matches = (
                        cast_trigger_compatibility(commander, card)["matches_trigger"]
                        is True
                    )
                else:
                    matches = contract.capability in caps
                if matches:
                    groups["commander:" + contract.capability].append(card)
        for name, group in sorted(groups.items()):
            ordered = sorted(group, key=order)
            # Keep function/type and curve diversity inside each quota: cheap
            # spells must not consume all draw-engine discovery slots.
            diverse: dict[str, dict[str, Any]] = {}
            buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for card in ordered:
                kind = str(card.get("type_line") or "").split("—")[0].lower()
                for typ in (
                    "creature",
                    "artifact",
                    "enchantment",
                    "instant",
                    "sorcery",
                    "land",
                    "planeswalker",
                ):
                    if typ in kind:
                        buckets[typ].append(card)
                value = card.get("cmc")
                if isinstance(value, (int, float)):
                    buckets[
                        "curve:"
                        + (
                            "early"
                            if value <= 2
                            else "middle" if value <= 4 else "late"
                        )
                    ].append(card)
            for bucket in sorted(buckets):
                for card in buckets[bucket][:2]:
                    diverse[str(card["oracle_id"])] = card
            for card in ordered:
                if len(diverse) >= CHANNEL_QUOTA:
                    break
                diverse[str(card["oracle_id"])] = card
            keep(name, list(diverse))
        keep(
            "lexical",
            sorted(lexical, key=lambda oid: (-lexical[oid], oid)),
            {oid: {"bm25": round(score, 4)} for oid, score in lexical.items()},
        )
        semantic, statuses["semantic"], rejected = _semantic(conn, by_oid, query)
        from sabermetrics.assistant_retrieval_context import recall_view_scores

        view_scores, view_receipt = recall_view_scores(by_oid, query, db_path=path)
        statuses["semantic_views"] = view_receipt["status"]
        keep(
            "semantic_views",
            sorted(view_scores, key=lambda oid: (-view_scores[oid], oid)),
            {oid: {"view_cosine": score} for oid, score in view_scores.items()},
        )
        keep(
            "semantic",
            [oid for oid, _ in semantic],
            {oid: {"cosine": score} for oid, score in semantic},
        )
        if rejected:
            limitations.append(
                f"Rejected {rejected} stale or incompatible card vectors."
            )

    catalog = known_combinations(path, document)
    statuses["combination"] = str(catalog["status"])
    combo_evidence = {}
    for line in catalog["matches"]:
        if (
            line["constraint_status"] != "eligible_source_ingredients"
            or line["unresolved_template_count"]
        ):
            continue
        for piece in line["cards"]:
            if piece["name"] in line["missing_cards"]:
                combo_evidence[piece["oracle_id"]] = {
                    "variant_id": line["variant_id"],
                    "citation": line["citation"],
                    "prerequisite_excerpt": line["prerequisite_excerpt"],
                    "prerequisites_truncated": line["prerequisites_truncated"],
                    "status": "sourced_ingredients_not_executed",
                }
    keep("combination", sorted(combo_evidence), combo_evidence)
    from sabermetrics.assistant_associations import research_card_associations

    library = [
        c
        for c in _library_entries(document)
        if c.get("oracle_id") and "land" not in str(c.get("type_line") or "").lower()
    ]
    supported = {
        s.oracle_id for contract in strategy.contracts for s in contract.support
    }
    anchors = sorted(
        library,
        key=lambda c: (str(c["oracle_id"]) not in supported, str(c["oracle_id"])),
    )[:MAX_ASSOCIATION_ANCHORS]
    association_evidence: dict[str, dict[str, Any]] = {}
    association_status = []

    def association_order(evidence: dict[str, Any]) -> tuple[int, float]:
        return (
            {"stable": 0, "insufficient": 1, "unstable": 2}.get(
                evidence.get("temporal_stability", {}).get("state"), 1
            ),
            -float(evidence["adjusted_lift"]),
        )

    for anchor in anchors:
        result = research_card_associations(
            Path(path), document, str(anchor["oracle_id"]), limit=8
        )
        association_status.append(result["status"])
        for item in result["associations"]:
            oid = str(item["oracle_id"])
            if oid in association_evidence and association_order(
                association_evidence[oid]
            ) <= association_order(item):
                continue
            association_evidence[oid] = {
                key: result.get(key)
                for key in (
                    "anchor_oracle_id",
                    "window_start",
                    "window_end_exclusive",
                    "min_event_size",
                    "cohort_lists",
                    "anchor_lists",
                    "source",
                    "source_freshness",
                    "temporal_diagnostics",
                )
            } | item
    statuses["association"] = (
        ",".join(association_status) or "unavailable_no_nonland_anchor"
    )
    keep(
        "association",
        sorted(
            association_evidence,
            key=lambda oid: (
                *association_order(association_evidence[oid]),
                oid,
            ),
        ),
        association_evidence,
    )
    if statuses["semantic"] != "available":
        limitations.append(
            "Card semantic retrieval is unavailable; independent mechanical and lexical channels remain active."
        )
    if truncated:
        limitations.append(
            "Canonical corpus exceeded the 50,000-card scan bound; recall is incomplete."
        )
    # No final global score truncation: each channel keeps its reserved quota.
    return RecallResult(
        strategy_fingerprint=strategy.fingerprint,
        corpus_cards=len(cards),
        corpus_truncated=truncated,
        candidates=list(retained.values()),
        channel_counts=channels,
        channel_status=statuses,
        limitations=limitations,
    )
