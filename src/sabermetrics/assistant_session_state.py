"""Source-backed session persistence and protected context packing.

Runtime supplies retrieval/tool evidence, never generated assistant prose. Stored
quotes prove provenance only: findings retain their epistemic kind and uncertainty.
"""

from __future__ import annotations

import json
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


def protected_session_context(
    state: SessionState,
    preferences: list[dict[str, Any]],
    *,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Protect complete preference rows and candidate identities under pressure.

    Optional facts and findings are evicted explicitly, without truncating quotes.
    Runtime must insert this object directly after generic compaction. If mandatory
    identities/preferences exceed the requested budget, disclose it rather than
    silently dropping hard rules or core membership.
    """
    context: dict[str, Any] = {
        "preferences": [dict(row) for row in preferences],
        "session_findings": [finding.model_dump() for finding in state.findings],
        "discovery_candidates": [
            {
                "card_id": card.card_id,
                "name": card.name,
                "rank": card.rank,
                "in_deck": card.in_deck,
                "group_ids": card.group_ids,
                "source_facts": [fact.model_dump() for fact in card.source_facts],
            }
            for card in state.ledger.candidates
        ],
        "findings_omitted": 0,
        "candidate_facts_omitted": 0,
        "scope": "Evidence-backed session state; provenance is not proof. Rankings and groups are planning metadata. Refresh current deck membership; findings are not action authority.",
    }
    while len(json.dumps(context, ensure_ascii=False)) > max_chars:
        # Direct candidate findings duplicate the ledger. Remove their oldest
        # copies before losing any retrieved card mechanics.
        duplicate_index = next(
            (
                i
                for i, finding in enumerate(context["session_findings"])
                if finding["kind"] == "candidate"
            ),
            None,
        )
        if duplicate_index is not None:
            context["session_findings"].pop(duplicate_index)
            context["findings_omitted"] += 1
            continue
        # Prefer highly ranked candidates, retaining both their oracle text and
        # cheap mana/type facts. A low-ranked oracle paragraph does not displace
        # all top-ranked mechanics. Every removal keeps an explicit count.
        importance = {
            "oracle_text": 115,
            "mana_cost": 100,
            "mana_value": 95,
            "type_line": 90,
        }
        removable = [
            (importance.get(fact["kind"], 30) - card_index * 4, card_index, fact_index)
            for card_index, card in enumerate(context["discovery_candidates"])
            for fact_index, fact in enumerate(card["source_facts"])
        ]
        if removable:
            _, card_index, fact_index = min(removable)
            context["discovery_candidates"][card_index]["source_facts"].pop(fact_index)
            context["candidate_facts_omitted"] += 1
        elif context["session_findings"]:
            # Noncandidate findings are chronological; keep the newest evidence.
            context["session_findings"].pop(0)
            context["findings_omitted"] += 1
        else:
            context["mandatory_context_exceeds_budget"] = True
            break
    return context
