"""Source-backed session persistence and protected context packing.

Runtime supplies retrieval/tool evidence, never generated assistant prose. Stored
quotes prove provenance only: findings retain their epistemic kind and uncertainty.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sabermetrics.assistant_conversation_context import (
    SESSION_FINDINGS_LIMIT,
    FindingEvidence,
    SessionFinding,
    merge_session_findings,
)
from sabermetrics.assistant_discovery_contract import (
    DiscoveryLedger,
    merge_discovery_ledger,
)

SnapshotQuote = Annotated[str, Field(min_length=1, max_length=2000)]


class SessionState(BaseModel):
    """Bounded research state with only the evidence fragments it actually uses."""

    model_config = ConfigDict(extra="forbid")
    version: int = 1
    revision: int | None = None
    findings: list[SessionFinding] = Field(
        default_factory=list, max_length=SESSION_FINDINGS_LIMIT
    )
    ledger: DiscoveryLedger = Field(default_factory=DiscoveryLedger)
    source_quotes: dict[str, Annotated[list[SnapshotQuote], Field(max_length=160)]] = (
        Field(default_factory=dict, max_length=168)
    )

    @property
    def discovery_ledger(self) -> DiscoveryLedger:
        """Expose the ledger under the runtime integration name."""
        return self.ledger


def evidence_text(value: Any) -> str:
    """Expose exact scalar values from tool data without JSON escaping artifacts."""
    if isinstance(value, dict):
        return "\n".join(f"{k}: {evidence_text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(evidence_text(v) for v in value)
    return str(value) if value is not None else ""


def _contains(source: str, quote: str) -> bool:
    if quote in source:
        return True
    try:
        decoded = json.loads(source)
    except (ValueError, TypeError):
        return False
    return quote in evidence_text(decoded)


def _ground_ledger(
    ledger: DiscoveryLedger, evidence: dict[str, str]
) -> DiscoveryLedger:
    candidates = []
    for card in ledger.candidates:
        facts = [
            fact
            for fact in card.source_facts
            if fact.source_id in evidence
            and _contains(evidence[fact.source_id], fact.value)
        ]
        # The source must bind both identity and facts to the same actual card.
        identity_source = f"card:{card.card_id}"
        if (
            not facts
            or identity_source not in evidence
            or not _contains(evidence[identity_source], card.name)
            or any(fact.source_id != identity_source for fact in facts)
        ):
            continue
        # Ranking and group assignments are planning metadata. Extra semantic
        # annotations are not promoted to source facts just because a card exists.
        candidates.append(
            card.model_copy(
                update={
                    "source_facts": facts,
                    "citations": list(dict.fromkeys(f.source_id for f in facts)),
                    "function_relevance": [],
                    "conditions": [],
                    "costs": [],
                }
            )
        )
    return DiscoveryLedger(candidates=candidates)


def _snapshots(state: SessionState) -> dict[str, list[str]]:
    quotes: dict[str, list[str]] = {}
    for finding in state.findings:
        for support in finding.evidence:
            quotes.setdefault(support.reference, []).append(support.quote)
    for card in state.ledger.candidates:
        source_id = f"card:{card.card_id}"
        quotes.setdefault(source_id, []).append(card.name)
        for fact in card.source_facts:
            quotes.setdefault(fact.source_id, []).append(fact.value)
    return {key: list(dict.fromkeys(values)) for key, values in quotes.items()}


def load_session_state(messages: list[dict[str, Any]]) -> SessionState:
    """Read the latest persisted envelope; never mine assistant answer prose.

    Old standalone findings/ledgers without source snapshots are deliberately not
    imported. They can be admitted again after runtime retrieves their sources.
    """
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        research = message.get("research") or (message.get("presentation") or {}).get(
            "research", {}
        )
        if not isinstance(research, dict) or "session_state" not in research:
            continue
        try:
            state = SessionState.model_validate(research["session_state"])
        except (TypeError, ValidationError):
            return SessionState()
        if state.version != 1:
            return SessionState()
        evidence = {key: "\n".join(rows) for key, rows in state.source_quotes.items()}
        return update_session_state(
            SessionState(revision=state.revision),
            findings=state.findings,
            ledger=state.ledger,
            evidence=evidence,
        )
    return SessionState()


def update_session_state(
    previous: SessionState,
    *,
    findings: list[SessionFinding] | None = None,
    ledger: DiscoveryLedger | None = None,
    evidence: dict[str, str],
    source_turn: str | None = None,
    in_deck_ids: set[str] | None = None,
    revision: int | None = None,
) -> SessionState:
    """Admit only exact source-backed updates and refresh current membership.

    Evidence values may be raw text, evidence_text(tool_data), or serialized JSON.
    Quote matching does not certify claim entailment or Magic legality. Callers
    must supply actual retrieval data; model-created source maps are not trusted.
    """
    valid_findings = [
        finding
        for finding in findings or []
        if all(
            item.reference in evidence
            and _contains(evidence[item.reference], item.quote)
            for item in finding.evidence
        )
    ]
    normalized_evidence = dict(evidence)
    for finding in valid_findings:
        for item in finding.evidence:
            normalized_evidence[item.reference] += "\n" + item.quote
    inherited_findings = [
        finding
        for finding in previous.findings
        if not (
            finding.kind == "empirical"
            and revision is not None
            and previous.revision != revision
        )
    ]
    merged_findings = merge_session_findings(
        [finding.model_dump() for finding in inherited_findings],
        valid_findings,
        evidence=normalized_evidence,
        source_turn=source_turn,
    )
    merged_ledger = merge_discovery_ledger(
        previous.ledger, _ground_ledger(ledger or DiscoveryLedger(), evidence)
    )
    if in_deck_ids is not None:
        merged_ledger = DiscoveryLedger(
            candidates=[
                card.model_copy(update={"in_deck": card.card_id in in_deck_ids})
                for card in merged_ledger.candidates
            ]
        )
    # Carry direct retrieved scalar facts, never summaries generated by a model.
    automatic = []
    for card in merged_ledger.candidates:
        for fact in card.source_facts:
            if fact.source_id not in evidence or fact.kind not in {
                "oracle_text",
                "mana_cost",
                "mana_value",
                "type_line",
            }:
                continue
            source = evidence[fact.source_id]
            try:
                source = evidence_text(json.loads(source))
            except (ValueError, TypeError):
                pass
            index = source.find(fact.value)
            if index < 0:
                continue
            quote = source[
                max(0, index - 24) : max(index + min(len(fact.value), 500), index + 8)
            ]
            quote = quote[:600]
            if len(quote) < 8:
                continue
            claim = f"{card.name} — {fact.kind}: {fact.value}"[:500]
            automatic.append(
                SessionFinding(
                    kind="candidate",
                    claim=claim,
                    evidence=[FindingEvidence(reference=fact.source_id, quote=quote)],
                    source_turn=source_turn,
                ).model_dump()
            )
    by_claim = {(row["kind"], row["claim"]): row for row in merged_findings}
    for row in automatic:
        by_claim.setdefault((row["kind"], row["claim"]), row)
    noncandidate = [row for row in by_claim.values() if row["kind"] != "candidate"]
    candidate = [row for row in by_claim.values() if row["kind"] == "candidate"]
    capacity = max(0, SESSION_FINDINGS_LIMIT - len(noncandidate))
    merged_findings = noncandidate[-SESSION_FINDINGS_LIMIT:] + (
        candidate[-capacity:] if capacity else []
    )
    state = SessionState(
        findings=[SessionFinding.model_validate(row) for row in merged_findings],
        ledger=merged_ledger,
        revision=revision if revision is not None else previous.revision,
    )
    return state.model_copy(update={"source_quotes": _snapshots(state)})


def session_state_research(state: SessionState) -> dict[str, Any]:
    """Return fields to merge into result.research and presentation.research."""
    return {
        "session_state": state.model_dump(),
        "session_findings": [finding.model_dump() for finding in state.findings],
        "discovery_ledger": state.ledger.model_dump(),
    }


def compact_preferences(preferences: list[dict[str, Any]]) -> dict[str, Any]:
    """Share quote/provenance storage without losing any original row field."""
    evidence: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    evidence_indices: dict[str, int] = {}
    for preference in preferences:
        support: dict[str, Any] = {
            key: preference[key]
            for key in ("quote", "source_turn")
            if key in preference
        }
        signature = json.dumps(support, sort_keys=True, ensure_ascii=False)
        if signature not in evidence_indices:
            evidence_indices[signature] = len(evidence)
            evidence.append(support)
        rows.append(
            {
                "fields": {
                    key: value
                    for key, value in preference.items()
                    if key not in {"quote", "source_turn"}
                },
                "evidence": evidence_indices[signature],
            }
        )
    return {"evidence": evidence, "rows": rows}


def expand_preferences(compact: dict[str, Any]) -> list[dict[str, Any]]:
    """Reverse compact_preferences exactly, including absent provenance fields."""
    return [
        {**row["fields"], **compact["evidence"][row["evidence"]]}
        for row in compact["rows"]
    ]


def _balanced_fact_ids(ledger: DiscoveryLedger, limit: int = 20) -> set[str]:
    buckets: dict[str, list[str]] = {}
    for card in sorted(ledger.candidates, key=lambda row: (row.rank, row.card_id)):
        for group in card.group_ids or ["unassigned"]:
            buckets.setdefault(group, []).append(card.card_id)
    selected: set[str] = set()
    while len(selected) < limit and any(buckets.values()):
        for bucket in buckets.values():
            while bucket and bucket[0] in selected:
                bucket.pop(0)
            if bucket and len(selected) < limit:
                selected.add(bucket.pop(0))
    return selected


def _oracle_excerpt(text: str, limit: int) -> str:
    """Return complete sentences/lines only; never a partial activation cost."""
    if len(text) <= limit:
        return text
    ends = [
        match.end()
        for match in re.finditer(r"[.!?](?:\s|$)|\n", text)
        if match.end() <= limit
    ]
    return text[: ends[-1]].rstrip() if ends else ""


def protected_session_context(
    state: SessionState,
    preferences: list[dict[str, Any]],
    *,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Pack shared preference evidence and lean printed fields, preserving identity.

    Group-balanced top candidates receive printed fields. Oracle excerpts are
    explicitly marked; full facts remain in the persisted ledger. Omitted fields
    and findings are counted. Mandatory preferences/identities are never removed.
    Insert directly after generic compaction to preserve this packing contract.
    """
    selected = _balanced_fact_ids(state.ledger)
    role_buckets: dict[str, list[str]] = {}
    for item in sorted(
        state.ledger.candidates, key=lambda row: (row.rank, row.card_id)
    ):
        for role in item.group_ids or ["unassigned"]:
            role_buckets.setdefault(role, []).append(item.card_id)
    group_heads = {bucket[0] for bucket in role_buckets.values() if bucket}
    within_role_rank: dict[str, int] = {}
    for bucket in role_buckets.values():
        for position, card_id in enumerate(bucket):
            within_role_rank[card_id] = min(
                position, within_role_rank.get(card_id, position)
            )
    candidates: list[dict[str, Any]] = []
    omitted_count = 0
    for card in state.ledger.candidates:
        printed: dict[str, str] = {}
        omitted = []
        excerpted = []
        for fact in card.source_facts:
            if (
                card.card_id not in selected
                or fact.kind
                not in {"mana_cost", "mana_value", "type_line", "oracle_text"}
                or fact.kind in printed
            ):
                omitted.append(fact.kind)
                omitted_count += 1
                continue
            value = fact.value
            if (
                fact.kind == "oracle_text"
                and card.card_id not in group_heads
                and len(value) > 160
            ):
                value = _oracle_excerpt(value, 160)
                if not value:
                    omitted.append(fact.kind)
                    omitted_count += 1
                    continue
                excerpted.append(fact.kind)
            printed[fact.kind] = value
        candidates.append(
            {
                "card_id": card.card_id,
                "name": card.name,
                "rank": card.rank,
                "in_deck": card.in_deck,
                "group_ids": card.group_ids,
                "printed_fields": printed,
                "excerpted_fields": excerpted,
                "omitted_fields": omitted,
            }
        )
    context: dict[str, Any] = {
        "preferences": compact_preferences(preferences),
        "session_findings": [
            finding.model_dump()
            for finding in state.findings
            if finding.kind != "candidate"
        ],
        "discovery_candidates": candidates,
        "findings_omitted": sum(
            finding.kind == "candidate" for finding in state.findings
        ),
        "candidate_facts_omitted": omitted_count,
        "card_fact_citation": "Each candidate's printed_fields cite card:<card_id>. excerpted_fields are partial text; omitted_fields were removed. Full retrieved facts remain in the session ledger.",
        "scope": "Preference rows retain every original field; evidence indices refer to the shared quote/source table. Rankings/groups are planning metadata. Provenance is not proof or action authority. Do not characterize costs/conditions/repeatability from excerpted or omitted text; retrieve card_details.",
    }
    while len(json.dumps(context, ensure_ascii=False)) > max_chars:
        # Preserve complete head mechanics, including trailing activation limits.
        # Empty annotation keys can go before any actual evidence is discarded.
        if any(
            not card.get(key) and key in card
            for card in candidates
            for key in ("excerpted_fields", "omitted_fields")
        ):
            for packed_card in candidates:
                for key in ("excerpted_fields", "omitted_fields"):
                    if not packed_card.get(key):
                        packed_card.pop(key, None)
            continue
        importance = {
            "oracle_text": 115,
            "mana_cost": 100,
            "mana_value": 95,
            "type_line": 90,
        }
        removable = [
            (
                importance.get(kind, 30)
                - within_role_rank.get(card["card_id"], index) * 4
                + (
                    1000
                    if card["card_id"] in group_heads
                    and kind in {"oracle_text", "mana_cost", "mana_value"}
                    else 0
                ),
                index,
                kind,
            )
            for index, card in enumerate(candidates)
            for kind in card["printed_fields"]
            if not (
                card["card_id"] in group_heads
                and kind in {"oracle_text", "mana_cost", "mana_value"}
            )
        ]
        if removable:
            _, index, kind = min(removable)
            packed_card = candidates[index]
            del packed_card["printed_fields"][kind]
            packed_card.setdefault("omitted_fields", []).append(kind)
            if kind in packed_card.get("excerpted_fields", []):
                packed_card["excerpted_fields"].remove(kind)
            context["candidate_facts_omitted"] += 1
        elif context["session_findings"]:
            context["session_findings"].pop(0)
            context["findings_omitted"] += 1
        else:
            # Only after other facts are gone, a head may retain complete first
            # and final sentences with an explicit gap. This keeps full initial
            # activation costs and trailing restrictions instead of a 64-char cut.
            shortened = False
            for packed_card in reversed(candidates):
                text = packed_card["printed_fields"].get("oracle_text", "")
                if packed_card[
                    "card_id"
                ] not in group_heads or "oracle_text" in packed_card.get(
                    "excerpted_fields", []
                ):
                    continue
                sentences = re.split(r"(?<=[.!?])\s+|\n", text.strip())
                if len(sentences) < 2:
                    continue
                excerpt = sentences[0] + "\n[middle text omitted]\n" + sentences[-1]
                if len(excerpt) >= len(text):
                    continue
                packed_card["printed_fields"]["oracle_text"] = excerpt
                packed_card.setdefault("excerpted_fields", []).append("oracle_text")
                shortened = True
                break
            if shortened:
                continue
            context["mandatory_context_exceeds_budget"] = True
            break
    return context
