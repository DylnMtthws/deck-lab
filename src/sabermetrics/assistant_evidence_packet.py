"""Shared local recommendation evidence, independent of prose and action authority."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from typing import Any, Literal, TypedDict

from sabermetrics.assistant_ability_clauses import (
    ability_clauses,
    bounded_compatibility,
    card_identity,
)
from sabermetrics.assistant_engine_graph import card_engine_links, local_support_edges
from sabermetrics.assistant_strategy import strategy_capabilities
from sabermetrics.deck_evidence import _library_entries

VERSION: Literal["assistant-recommendation-packet.v1"] = (
    "assistant-recommendation-packet.v1"
)


class RecommendationPacket(TypedDict):
    version: Literal["assistant-recommendation-packet.v1"]
    deck_revision: Any
    focus: str
    incoming: dict[str, Any]
    outgoing: dict[str, Any] | None
    changes: dict[str, Any]
    casting_cost: str | None
    activation_costs: list[str]
    clauses: dict[str, Any]
    ability_compatibility: dict[str, Any]
    deck_support: list[dict[str, Any]]
    deck_sources: list[dict[str, Any]]
    prerequisites: list[dict[str, Any]]
    lost_roles: list[str]
    lost_engine_links: list[dict[str, Any]]
    lost_engine_sources: list[dict[str, Any]]
    counterevidence: list[str]
    unresolved_questions: list[str]
    coverage: dict[str, Any]
    scope: str


def _source(card: dict[str, Any]) -> dict[str, Any]:
    return {
        key: card.get(key)
        for key in (
            "id",
            "entry_id",
            "quantity",
            "card_id",
            "oracle_id",
            "name",
            "mana_cost",
            "mana_value",
            "type_line",
            "oracle_text",
            "card_faces",
            "layout",
            "oracle_text_truncated",
            "provenance",
            "source",
            "source_id",
            "source_version",
            "source_hash",
            "oracle_text_hash",
            "power",
            "toughness",
        )
    }


@lru_cache(maxsize=4)
def _old_links(serialized_entries: str) -> list[dict[str, Any]]:
    """Content-keyed old graph: bounded cache, no stale revision-only reuse."""
    entries = json.loads(serialized_entries)
    document = {"entries": entries}
    links = [
        edge
        for recipient in entries
        for edge in local_support_edges(recipient, entries)
    ]
    for entry in entries:
        links.extend(
            {**link.model_dump(), "provider_id": card_identity(entry)}
            for link in card_engine_links(document, entry)
        )
    return links


def _remaining_after_cut(
    entries: list[dict[str, Any]], outgoing: dict | None
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    """Default is one physical card; inventory quantity is not the cut count."""
    change = {
        "requested_cut_quantity": 0,
        "removed_quantity": 0,
        "outgoing_entry_id": None,
        "remaining_identity_quantity": 0,
        "status": "no_cut",
    }
    if outgoing is None:
        return entries, change, None
    count = outgoing.get("cut_quantity", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        change.update(requested_cut_quantity=count, status="invalid_cut_quantity")
        return entries, change, None
    entry_id = outgoing.get("entry_id") or outgoing.get("id")
    matches = [
        i
        for i, c in enumerate(entries)
        if entry_id and (c.get("id") == entry_id or c.get("entry_id") == entry_id)
    ]
    if not matches and not outgoing.get("entry_id"):
        matches = [
            i
            for i, c in enumerate(entries)
            if card_identity(c) == card_identity(outgoing)
        ]
    # An explicit Oracle/entry identity disagreement is unresolved, never a
    # reason to silently remove a different entry with a matching broad role.
    if (
        matches
        and outgoing.get("oracle_id")
        and entries[matches[0]].get("oracle_id") != outgoing["oracle_id"]
    ):
        matches = []
    resolved = entries[matches[0]] if matches else None
    remaining = list(entries)
    change.update(requested_cut_quantity=count, status="unresolved_outgoing_entry")
    if matches:
        i = matches[0]
        original = entries[i]
        quantity = int(original.get("quantity") or 1)
        removed = min(count, quantity)
        if removed == quantity:
            remaining.pop(i)
        else:
            remaining[i] = {**original, "quantity": quantity - removed}
        change.update(
            removed_quantity=removed,
            outgoing_entry_id=original.get("entry_id") or original.get("id"),
            status="resolved" if removed == count else "partial_quantity",
        )
    change["remaining_identity_quantity"] = sum(
        int(c.get("quantity") or 1)
        for c in remaining
        if card_identity(c) == card_identity(resolved or outgoing)
    )
    return remaining, change, resolved


def build_recommendation_packet(
    document: dict, incoming: dict, outgoing: dict | None = None, focus: str = ""
) -> dict:
    """Build from current library/command zone only, excluding a proposed cut.

    Pure local computation; source text is retained exactly, never shortened.
    Unknown prerequisites are detection gaps, not assertions of absent resources.
    """
    library = _library_entries(document)
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    entries = [*commanders, *library]
    remaining, changes, resolved_outgoing = _remaining_after_cut(entries, outgoing)
    changes["incoming_quantity"] = incoming.get("quantity", 1)
    clauses = ability_clauses(incoming)
    compatibility = bounded_compatibility(incoming, remaining)
    support = local_support_edges(incoming, remaining, compatibility)
    lost = []
    identity_removed = (
        outgoing is not None
        and changes["removed_quantity"] > 0
        and changes["remaining_identity_quantity"] == 0
    )
    if identity_removed and resolved_outgoing is not None:
        # Only the first candidate pays for the old graph. Full source content,
        # entry identity and quantities form the key, so same-revision edits miss.
        graph_entries = [
            {**_source(c), "is_commander": bool(c.get("is_commander"))} for c in entries
        ]
        old_links = _old_links(json.dumps(graph_entries, sort_keys=True, default=str))
        lost = [
            deepcopy(edge)
            for edge in old_links
            if edge.get("provider_id") == card_identity(resolved_outgoing)
            or edge.get("recipient_id") == card_identity(resolved_outgoing)
        ]
    questions = [
        "Are casting/activation resources, legal targets and timing actually available?",
        "Can consumed resources be replenished for further uses? No loop is established.",
    ]
    questions.extend(clauses["unknowns"])
    if changes["status"] not in {"resolved", "no_cut"}:
        questions.append(
            "Outgoing entry or requested cut quantity is unresolved; cut-dependent evidence is incomplete."
        )
    prerequisites = [
        {
            "dimension": r["dimension"],
            "required": r["required"],
            "source_quote": r["candidate_quote"],
            "status": "printed_support_only" if r["providers"] else "unknown",
            "conditions": r["conditions"],
        }
        for r in compatibility["requirements"]
    ]
    for clause in clauses["clauses"]:
        if clause["unknowns"]:
            prerequisites.append(
                {
                    "dimension": "clause_coverage",
                    "required": ", ".join(clause["unknowns"]),
                    "source_quote": clause["source_quote"],
                    "status": "unknown",
                    "conditions": "Unparsed restrictions cannot be discharged by printed ingredients.",
                }
            )
    counter = []
    for clause in clauses["clauses"]:
        if any("sacrifice" in c.lower() for c in clause["costs"]):
            counter.append(
                "Sacrifice consumes a permanent independently of any hand-card gain; no recursion refund is assumed."
            )
        if clause["targets"] or clause["timing"]:
            counter.append(
                "Printed targeting and timing clauses remain prerequisites, not discharged by deck ingredients."
            )
        questions.extend(clause["unknowns"])
    if lost:
        counter.append(
            "The proposed cut removes existing printed support links; incoming ingredients do not establish equivalent replacement."
        )
    packet: RecommendationPacket = {
        "version": VERSION,
        "deck_revision": document.get("revision"),
        "focus": focus,
        "incoming": _source(incoming),
        "outgoing": (
            _source(resolved_outgoing or outgoing) if outgoing is not None else None
        ),
        "changes": changes,
        "casting_cost": incoming.get("mana_cost"),
        "activation_costs": [cost for c in clauses["clauses"] for cost in c["costs"]],
        "clauses": clauses,
        "ability_compatibility": compatibility,
        "deck_support": support[:12],
        "deck_sources": [
            _source(c)
            for c in remaining
            if card_identity(c) in {e["provider_id"] for e in support[:12]}
        ],
        "prerequisites": prerequisites,
        "lost_roles": (
            sorted(strategy_capabilities(resolved_outgoing))
            if identity_removed and resolved_outgoing is not None
            else []
        ),
        "lost_engine_links": lost[:16],
        "lost_engine_sources": [
            _source(c)
            for c in entries
            if card_identity(c)
            in {
                str(edge.get(key) or "")
                for edge in lost[:16]
                for key in ("provider_id", "recipient_id", "commander_oracle_id")
            }
        ],
        "counterevidence": list(dict.fromkeys(counter)),
        "unresolved_questions": list(dict.fromkeys(questions)),
        "coverage": {
            "library_entries_scanned": len(library),
            "commanders_scanned": len(commanders),
            "clauses": clauses["coverage"],
            "support_edges_omitted": max(0, len(support) - 12),
            "lost_links_omitted": max(0, len(lost) - 16),
            "rules": "unverified",
        },
        "scope": "Bounded printed library/command-zone evidence; no board availability, legal action trace, sustainable supply, loop proof, measured improvement or edit authority.",
    }
    return dict(packet)


def compact_recommendation_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Small reference view; exact Oracle records remain in the full packet/card."""
    return {
        "version": packet["version"],
        "view": "compact",
        "deck_revision": packet["deck_revision"],
        "changes": packet["changes"],
        "outgoing_oracle_id": (packet.get("outgoing") or {}).get("oracle_id"),
        "lost_roles": packet["lost_roles"][:4],
        "lost_engine_links": [
            {
                key: edge[key]
                for key in (
                    "provider_id",
                    "provider_oracle_id",
                    "provider",
                    "recipient_id",
                    "recipient",
                    "commander_oracle_id",
                    "commander",
                    "relationship",
                    "status",
                )
                if key in edge
            }
            for edge in packet["lost_engine_links"][:3]
        ],
        "unresolved_questions": packet["unresolved_questions"][:3],
        "oracle_id": packet["incoming"]["oracle_id"],
        "casting_cost": packet["casting_cost"],
        "activation_costs": packet["activation_costs"][:3],
        "support": [
            {"provider": e["provider"], "relationship": e["relationship"]}
            for e in packet["deck_support"][:3]
        ],
        "unknown_prerequisites": [
            {"dimension": p["dimension"], "required": p["required"]}
            for p in packet["prerequisites"]
            if p["status"] == "unknown"
        ][:3],
        "coverage": packet["coverage"],
        "counterevidence": packet["counterevidence"][:2],
        "omitted": {
            "support": packet["coverage"]["support_edges_omitted"]
            + max(0, len(packet["deck_support"]) - 3),
            "lost_roles": max(0, len(packet["lost_roles"]) - 4),
            "lost_engine_links": packet["coverage"]["lost_links_omitted"]
            + max(0, len(packet["lost_engine_links"]) - 3),
            "counterevidence": max(0, len(packet["counterevidence"]) - 2),
            "unresolved_questions": max(0, len(packet["unresolved_questions"]) - 3),
            "activations": max(0, len(packet["activation_costs"]) - 3),
            "unknown_prerequisites": max(
                0, sum(p["status"] == "unknown" for p in packet["prerequisites"]) - 3
            ),
        },
        "rules": "unverified; ingredients never prove a loop",
    }
