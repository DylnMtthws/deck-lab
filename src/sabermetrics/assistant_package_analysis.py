"""Joint ingredient/cut analysis over a bounded package, never an engine proof."""

from __future__ import annotations

import copy
from typing import Any

from sabermetrics.assistant_ability_clauses import ability_clauses, card_identity
from sabermetrics.assistant_evidence_packet import (
    build_recommendation_packet,
    compact_recommendation_packet,
)


def substitute_profiles(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Comparable printed dimensions without claiming equivalent effects."""
    return [
        {
            "identity": card_identity(card),
            "name": card.get("name"),
            "casting_cost": card.get("mana_cost"),
            "abilities": ability_clauses(card),
            "scope": "Printed dimensions only; effects need not be equivalent and no strict superiority follows.",
        }
        for card in cards[:20]
    ]


def _without(
    document: dict[str, Any], outgoing: list[dict[str, Any]]
) -> dict[str, Any]:
    result = copy.deepcopy(document)
    entries = result.get("entries", [])
    for old in outgoing:
        matched = False
        for index, entry in enumerate(entries):
            if entry.get("id") == old.get("id") and entry.get("id") is not None:
                matched = True
                quantity = int(entry.get("quantity", 1))
                cut = old.get("cut_quantity", 1)
                if (
                    isinstance(cut, bool)
                    or not isinstance(cut, int)
                    or not 1 <= cut <= quantity
                ):
                    raise ValueError("invalid cut quantity")
                if quantity > cut:
                    entry["quantity"] = quantity - cut
                else:
                    entries.pop(index)
                break
        if not matched:
            raise ValueError("outgoing must identify an actual current library entry")
    return result


def analyze_package(
    document: dict[str, Any],
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
    focus: str = "",
) -> dict[str, Any]:
    if not 1 <= len(incoming) <= 3 or len(incoming) != len(outgoing):
        raise ValueError(
            "package requires one to three distinct paired additions and cuts"
        )
    if len({card_identity(c) for c in incoming}) != len(incoming) or len(
        {c.get("id") for c in outgoing}
    ) != len(outgoing):
        raise ValueError("package additions and cuts must be distinct")
    after_cuts = _without(document, outgoing)
    joint = copy.deepcopy(after_cuts)
    for i, new in enumerate(incoming):
        joint.setdefault("entries", []).append(
            {
                **new,
                "id": f"package-member-{i}",
                "card_id": new.get("card_id") or new.get("id"),
                "is_commander": False,
                "quantity": 1,
                "zone_name": "Library",
            }
        )
    packets = [
        build_recommendation_packet(document, new, old, focus)
        for old, new in zip(outgoing, incoming)
    ]
    member_ids = {card_identity(c) for c in incoming}
    joint_packets = [
        build_recommendation_packet(joint, new, focus=focus) for new in incoming
    ]
    # Cost/loss facts describe each actual cut. Suitability must instead use
    # ALL cuts and additions, never the independent single-swap counterfactual.
    for packet, prospective, old in zip(packets, joint_packets, outgoing):
        for field in (
            "deck_support",
            "deck_sources",
            "prerequisites",
            "ability_compatibility",
            "unresolved_questions",
        ):
            packet[field] = prospective[field]
        packet["counterevidence"] = list(
            dict.fromkeys([*packet["counterevidence"], *prospective["counterevidence"]])
        )
        packet["coverage"] = {
            **prospective["coverage"],
            "lost_links_omitted": packet["coverage"]["lost_links_omitted"],
        }
        packet["changes"]["remaining_identity_quantity"] = sum(
            int(e.get("quantity", 1))
            for e in joint["entries"]
            if card_identity(e) == card_identity(old)
        )
        packet["changes"]["support_context"] = "complete_prospective_package"
    links = [
        edge
        for packet in joint_packets
        for edge in packet["deck_support"]
        if edge["provider_id"] in member_ids
        and edge["provider_id"] != edge["recipient_id"]
    ]
    losses = []
    for removed in incoming:
        partial = copy.deepcopy(joint)
        partial["entries"] = [
            e for e in partial["entries"] if card_identity(e) != card_identity(removed)
        ]
        lost = []
        for retained in incoming:
            if card_identity(retained) == card_identity(removed):
                continue
            before = next(
                p
                for p in joint_packets
                if card_identity(p["incoming"]) == card_identity(retained)
            )
            after = build_recommendation_packet(partial, retained, focus=focus)
            old_edges = {
                (e["provider_id"], e["recipient_id"], e["relationship"])
                for e in before["deck_support"]
            }
            new_edges = {
                (e["provider_id"], e["recipient_id"], e["relationship"])
                for e in after["deck_support"]
            }
            lost.extend([list(e) for e in sorted(old_edges - new_edges)])
        losses.append(
            {
                "missing_member": removed.get("name"),
                "lost_printed_support_links": lost[:12],
                "impact": "Potential ingredient support only; remaining engine execution is unverified.",
            }
        )
    return {
        "version": "assistant-package-analysis.v1",
        "replacement_packets": packets,
        "joint_member_packets": [
            compact_recommendation_packet(p) for p in joint_packets
        ],
        "shared_ingredient_links": links[:12],
        "lost_engine_links": [edge for p in packets for edge in p["lost_engine_links"]][
            :24
        ],
        "functional_dimensions": substitute_profiles(incoming),
        "interruption_points": [
            {
                "card": p["incoming"].get("name"),
                "activation_costs": p["activation_costs"],
                "prerequisites": p["prerequisites"],
                "counterevidence": p["counterevidence"],
            }
            for p in packets
        ],
        "missing_member_analysis": losses,
        "engine_execution": "unverified",
        "scope": "Prospective library composition only, not battlefield state, sustainable resource supply, legal loop, preview authorization or measured benefit.",
    }
