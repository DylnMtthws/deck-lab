"""Bounded coordinated proposals evaluated against the complete resulting deck."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from sabermetrics.assistant_candidate_recall import RecallResult, card_content
from sabermetrics.assistant_evidence_packet import build_recommendation_packet
from sabermetrics.assistant_package_analysis import analyze_package
from sabermetrics.assistant_strategy import (
    StrategyContext,
    assess_strategy_transition,
    commander_support,
    draw_routes,
)
from sabermetrics.deck_evidence import _library_entries
from sabermetrics.functional_candidates import oracle_facts


def recommend_packages(
    service: Any,
    document: dict[str, Any],
    constraints: dict[str, Any],
    assessment: dict[str, Any],
    recall: RecallResult,
    focus: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Offer at most two two-card packages with joint constraint/support receipts.

    Reserve proposals from distinct missing functions. The entire candidate is
    validated before review; individual members need not improve on their own.
    This is a local search, never an unbounded rebuild or a deck mutation.
    """
    from sabermetrics.assistant_deck_intelligence import (
        GENERIC_TARGETS,
        _catalog_line_present,
        _eligible_catalog_lines,
        _features,
        assess_deck_requirements,
    )

    strategy = StrategyContext.model_validate(assessment["strategy_context"])
    counts = {r["requirement"]: r["actual"] for r in assessment["requirements"]}
    targets = {r["requirement"]: r["target"] for r in assessment["requirements"]}
    gaps = {r for r in targets if counts[r] < targets[r]}
    requested = {r for r in gaps if r.replace("_", " ") in focus.lower()}
    if "mana" in focus.lower():
        requested |= gaps & {"ramp", "land"}
    if "card advantage" in focus.lower():
        requested |= gaps & {"draw"}
    catalog = _eligible_catalog_lines(service.db_path, document)
    library = _library_entries(document)
    from sabermetrics.assistant_engine_graph import (
        build_engine_graph,
        engine_loss_score,
    )

    engine_graph = build_engine_graph(document)
    outgoing = [
        c
        for c in library
        if c.get("card_id") not in constraints["protected_cards"]
        and str(c.get("oracle_text") or "").strip()
        and "//" not in str(c.get("type_line") or "")
        and "land" not in str(c.get("type_line") or "").lower()
    ]
    old_caps = {c["id"]: _features(c) for c in outgoing}
    # Preserve library order independence and prefer cards offering less modeled
    # commander support. Final transition validation is authoritative.
    outgoing.sort(
        key=lambda c: (
            engine_loss_score(engine_graph, c),
            len(commander_support(document, c, strategy)),
            len(old_caps[c["id"]] & gaps),
            str(c.get("name") or ""),
            str(c.get("oracle_id") or ""),
        )
    )
    outgoing = outgoing[:12]
    if len(outgoing) < 2:
        return [], []
    candidates = []
    for item in recall.candidates:
        card = item.card
        if "//" in str(card.get("type_line") or ""):
            continue
        caps = _features(card)
        gains = caps & gaps
        if "ramp" in gains and not caps & {"direct_mana_source", "land_ramp"}:
            gains.discard("ramp")
        if gains:
            candidates.append((item, caps, gains))
    candidates.sort(
        key=lambda row: (
            -len(row[2] & requested),
            -len(row[2] - set(GENERIC_TARGETS)),
            -len(row[2]),
            str(row[0].card["oracle_id"]),
        )
    )
    # Keep complementary functions before any combined ranking.
    retained = {}
    for role in sorted(gaps):
        for row in [r for r in candidates if role in r[2]][:2]:
            retained[row[0].card["oracle_id"]] = row
    pairs = list(combinations(list(retained.values())[:16], 2))
    pairs.sort(
        key=lambda pair: (
            -len((pair[0][2] | pair[1][2]) & requested),
            -len((pair[0][2] | pair[1][2]) - set(GENERIC_TARGETS)),
            -len(pair[0][2] | pair[1][2]),
            str(pair[0][0].card["oracle_id"]),
            str(pair[1][0].card["oracle_id"]),
        )
    )
    alternatives: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    attempts = 0
    for left, right in pairs[:12]:
        incoming = [left, right]
        # A package must contribute two different needed functions.
        if not left[2] - right[2] or not right[2] - left[2]:
            continue
        if requested and not (left[2] | right[2]) & requested:
            continue
        for cuts in combinations(outgoing, 2):
            added_counts: Counter[str] = Counter(
                role for _, caps, _ in incoming for role in caps
            )
            removed_counts: Counter[str] = Counter(
                role for old in cuts for role in old_caps[old["id"]]
            )
            if any(
                counts[r] + added_counts[r] - removed_counts[r]
                < min(counts[r], targets[r])
                for r in targets
            ):
                continue
            protected = {
                "free_cast_access",
                "triggered_mana_engine",
                "broad_damage_payoff",
                "nonland_mana_resource",
                "card_selection",
            }
            if any(added_counts[r] < removed_counts[r] for r in protected):
                continue
            replacements = [
                {"outgoing_entry_id": old["id"], "incoming_card_id": row[0].card["id"]}
                for old, row in zip(cuts, incoming)
            ]
            attempts += 1
            try:
                preview = service._candidate(document, replacements, constraints)
            except (ValueError, TypeError):
                if attempts >= 12:
                    return alternatives, evidence
                continue
            after = assess_deck_requirements(preview["document"], constraints)
            if any(
                card_content(
                    next(
                        c
                        for c in preview["document"]["entries"]
                        if c.get("card_id") == row[0].card["id"]
                    )
                )
                != card_content(row[0].card)
                for row in incoming
            ):
                if attempts >= 12:
                    return alternatives, evidence
                continue
            transition = assess_strategy_transition(
                strategy, StrategyContext.model_validate(after["strategy_context"])
            )
            after_counts = {
                r["requirement"]: r["actual"] for r in after["requirements"]
            }
            # Discovery can add requested requirements (e.g. recursion) that
            # the generic after-assessment does not list. Compare the same
            # feature predicates over the complete resulting library.
            after_features: Counter[str] = Counter()
            for entry in _library_entries(preview["document"]):
                for feature in _features(entry):
                    after_features[feature] += int(entry.get("quantity", 1))
            for requirement in targets:
                after_counts.setdefault(requirement, after_features[requirement])
            improved = sorted(r for r in gaps if after_counts[r] > counts[r])
            if (
                not transition["allowed"]
                or len(improved) < 2
                or any(
                    not _catalog_line_present(line, preview["document"])
                    for line in catalog
                )
            ):
                if attempts >= 12:
                    return alternatives, evidence
                continue
            replacement_pairs = [
                {
                    "incoming_oracle_facts": oracle_facts(row[0].card),
                    "outgoing_oracle_facts": oracle_facts(old),
                    "recall": row[0].model_dump(exclude={"card"}),
                    "recommendation_packet": build_recommendation_packet(
                        document, row[0].card, old, focus
                    ),
                    "draw_routes": {
                        "incoming": [r.model_dump() for r in draw_routes(row[0].card)],
                        "outgoing": [r.model_dump() for r in draw_routes(old)],
                    },
                }
                for old, row in zip(cuts, incoming)
            ]
            package_analysis = analyze_package(
                document, [row[0].card for row in incoming], list(cuts), focus
            )
            for pair, packet in zip(
                replacement_pairs, package_analysis["replacement_packets"]
            ):
                pair["recommendation_packet"] = packet
            alternatives.append(
                {
                    "name": "Test coordinated "
                    + " + ".join(row[0].card["name"] for row in incoming),
                    "rationale": "Joint ingredient proposal for "
                    + ", ".join(improved)
                    + "; review both cuts and additions together. No measured improvement claimed.",
                    "replacements": replacements,
                }
            )
            evidence.append(
                {
                    "recommendation_type": "coordinated_package",
                    "package_analysis": package_analysis,
                    "review_required": True,
                    "replacement_pairs": replacement_pairs,
                    "incoming_oracle_facts": replacement_pairs[0][
                        "incoming_oracle_facts"
                    ],
                    "outgoing_oracle_facts": replacement_pairs[0][
                        "outgoing_oracle_facts"
                    ],
                    "requirements": improved,
                    "strategy_transition": transition,
                    "semantic_verification": "unverified",
                    "fit_status": "inferred",
                    "joint_requirement_changes": [
                        {
                            "requirement": r,
                            "before": counts[r],
                            "after": after_counts[r],
                        }
                        for r in sorted(targets)
                    ],
                    "scope": "Joint legal ingredient proposal; engine execution and strategic benefit require review and measurement.",
                }
            )
            break
        if len(alternatives) == 2 or attempts >= 12:
            break
    return alternatives, evidence
