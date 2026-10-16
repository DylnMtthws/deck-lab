"""Bounded generator-derived requirements and read-only replacement previews.

Supported shapes are adapted from generator strategy-plan.v1, not a rules
interpreter. Numeric targets are authored starting points, not power estimates.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sabermetrics.assistant_experiments import ExperimentService
from sabermetrics.assistant_strategy import (
    StrategyContext,
    assess_strategy_transition,
    build_strategy_context,
    commander_support,
    draw_routes,
)
from sabermetrics.card_capabilities import (
    casting_cost_basis,
    engine_features,
    own_draw_text,
    relationships,
    supported_plan,
)
from sabermetrics.cast_trigger_evidence import document_cast_trigger_evidence
from sabermetrics.combination_intelligence import (
    analyze_combinations,
    summarize_combinations,
)
from sabermetrics.combo_catalog import known_combinations
from sabermetrics.deck_evidence import _card_roles, _library_entries
from sabermetrics.functional_candidates import (
    basic_land_color_tradeoff,
    functional_improvement,
    oracle_facts,
    strategic_tradeoff,
)

GENERIC_TARGETS = {"land": 37, "ramp": 10, "draw": 12, "removal": 12, "protection": 4}
LAND_TARGETS = {
    "land_ramp": 3,
    "landfall_cards": 2,
    "landfall_board": 2,
    "extra_land_play": 1,
    "land_recursion": 1,
}
NOTE = "Text predicates and authored targets indicate ingredients, not verified mechanics, wins, theme fit, or competitive strength."


def _text(card: dict[str, Any]) -> str:
    return re.sub(r"\([^)]*\)", "", str(card.get("oracle_text") or "")).lower()


def _mana_conditions(card: dict[str, Any]) -> list[str]:
    """Expose prerequisites; a token's reminder ability is not the card's own."""
    text = _text(card)
    conditions: list[str] = []
    if "treasure" in text and re.search(
        r"whenever [^.\n]*(?:attacks|combat damage)", text
    ):
        conditions.append("combat_required")
        if "equipped creature" in text:
            conditions.append("equipped_creature_required")
    if "treasure" in text and re.search(
        r"whenever [^.\n]*(?:dies|sacrifice|cast)", text
    ):
        conditions.append("trigger_required")
    if (
        "add " in text
        and "power" in text
        and ("equal to" in text or re.search(r"where x is [^.,]*power", text))
    ):
        conditions.append("power_required")
    if "add " in text and "suspend" in text:
        conditions.append("suspend_delay_required")
    if "add " in text and "discard a land card" in text:
        conditions.append("entry_resource_cost_required")
    if "add " in text and "multikicker" in text:
        conditions.append("kicker_investment_required")
    if "add " in text and ("imprint" in text or "exiled card" in text):
        conditions.append("exiled_card_required")
    if "add " in text and "{X}" in str(card.get("mana_cost") or "").upper():
        conditions.append("variable_mana_investment_required")
    if "add " in text and "activate only" in text:
        conditions.append("activation_restriction")
    if "add " in text and re.search(r"\{t\}[^:\n]*,|, [^:\n]*\{t\}", text):
        conditions.append("additional_activation_cost")
    return conditions


def _features(card: dict[str, Any]) -> set[str]:
    text = _text(card)
    types = str(card.get("type_line") or "").split(" // ")[0].lower()
    # Discovery tags include reminder text and self-protecting keywords. Build
    # requirements need scoped effects, not every keyword on the card.
    result = set(_card_roles({**card, "oracle_text": text, "role_tags": None}))
    if "basic land" in types:
        # Intrinsic basic mana abilities survive removal of reminder text.
        result.update({"land", "mana_resource", "direct_mana_source"})
    if re.search(r"\bwhenever\b", text):
        result.add("triggered_ability")
    if "draw" in result and not own_draw_text(text):
        result.discard("draw")
    protection = (
        bool(
            re.search(
                r"(?:target (?:creature|permanent)|(?:creatures|permanents) you control|equipped creature|enchanted creature)[^.\n]*(?:hexproof|indestructible|shroud|protection from)",
                text,
            )
        )
        or bool(
            re.search(
                r"target (?:creature|permanent)[^.\n]*\.[ \n]*(?:it|that creature|that permanent) phases out",
                text,
            )
        )
        or bool(
            re.search(
                r"counter target[^.\n]*that targets[^.\n]*(?:you|permanent|creature)",
                text,
            )
        )
    )
    if re.search(r"counter target[^.\n]*spell", text):
        result.add("removal")
    if "you may cast" in text and "without paying" in text:
        result.add("free_cast_access")
    result.discard("protection")
    if protection:
        result.add("protection")
    if "treasure" in text and re.search(
        r"whenever[^.\n]*(?:cast|copy|dies|sacrifice|discard)", text
    ):
        result.add("triggered_mana_engine")
    if "add " in text or "ramp" in result:
        result.add("mana_resource")
        if "land" not in types:
            result.add("nonland_mana_resource")
    if re.search(
        r"look at the top [^.\n]*cards?[^\n]*?put [^.\n]*into your hand", text
    ):
        result.add("card_selection")
    # Explicit self selection; opponent/target-player and each-player effects
    # do not establish access to our own library.
    if re.search(r"(?:^|[.\n]|, then)\s*(?:you )?(?:scry|surveil) [1-9][0-9]*\b", text):
        result.add("card_selection")
    if re.search(
        r"look at the top [^.\n]*cards? of your library[^.\n]*put them back in any order",
        text,
    ):
        result.add("card_selection")
    if _mana_conditions(card):
        result.add("conditional_mana")
        # A combat-triggered Treasure is an engine payoff, not baseline ramp.
        if _mana_conditions(card):
            result.discard("ramp")
    if re.search(
        r"(?:^|\n)\{t\}: add (?:\{[cwubrg]\}|one mana)", text
    ) and not _mana_conditions(card):
        result.add("direct_mana_source")
    # Shared broad discovery tags classify some graveyard returns as removal.
    # Do not count that clause as interaction against an opposing board.
    if "from your graveyard" in text and not re.search(
        r"destroy |exile target|counter target|return target[^.\n]*to (?:its|their) owner",
        text,
    ):
        result.discard("removal")
    # Do not infer front-face functionality from a different face.
    if "//" in str(card.get("type_line") or ""):
        return result
    cmc = card.get("cmc", card.get("mana_value"))
    if (
        types
        and "creature" not in types
        and "land" not in types
        and cmc is not None
        and float(cmc) <= 1
    ):
        result.add("cheap_noncreature_cast")
    if re.search(
        r"whenever [^.\n]*deals (?:noncombat )?damage to an opponent[^.\n]*draw (?:a|that many) cards?",
        text,
    ):
        result.add("opponent_damage_draw")
    if (
        "search your library" in text
        and re.search(r"(?:land|forest|island) card", text)
        and "onto the battlefield" in text
        and "land" not in types
        and "sacrifice a land" not in text
        and "same name" not in text
    ):
        result.add("land_ramp")
    if (
        re.search(r"you may play (?:an additional|two additional) lands?", text)
        and "this turn" not in text
    ):
        result.add("extra_land_play")
    if "graveyard_land_access" in engine_features(card):
        result.add("land_recursion")
    for clause in re.split(r"[.\n]", text):
        if "whenever a land" not in clause:
            continue
        clause = clause[clause.index("whenever a land") :]
        if re.search(r"draw (?:a|one|two|three|x) cards?|investigate", clause):
            result.add("landfall_cards")
        if "create" in clause and (
            "creature token" in clause or "token creature" in clause
        ):
            result.add("landfall_board")
    # Preserve explicit broad damage payoffs when filling generic infrastructure.
    # This is a scoped Oracle predicate, not a claim that the card wins a game.
    if re.search(
        r"deals? [^.\n]*damage[^.\n]*? to (?:each|all) (?:other )?(?:opponents?|players?|creatures?)",
        text,
    ):
        result.add("broad_damage_payoff")
    result.update(engine_features(card))
    for tribe in ("dragon", "vampire", "goblin", "elf", "zombie", "merfolk"):
        if "creature" in types and re.search(r"\b" + tribe + r"\b", types):
            result.add("creature_type:" + tribe)
    return result


def assess_deck_requirements(
    document: dict[str, Any], constraints: dict[str, Any] | None = None
) -> dict[str, Any]:
    constraints = constraints or {}
    commanders = [e for e in document.get("entries", []) if e.get("is_commander")]
    text = "\n".join(_text(e) for e in commanders)
    intent = str(constraints.get("theme") or "").lower()
    archetype = "general"
    specific: dict[str, int] = {}
    if "landfall" in intent or "whenever a land" in text:
        archetype, specific = "landfall", LAND_TARGETS
    elif (
        "whenever you cast a noncreature spell" in text
        and "damage to each opponent" in text
    ):
        archetype = "cast_damage"
        specific = {"cheap_noncreature_cast": 10}
        if "curiosity" in intent or "damage draw" in intent:
            archetype = "cast_damage_draw"
            specific["opponent_damage_draw"] = 2
    if not specific:
        archetype, specific = supported_plan(commanders)
    targets = {**GENERIC_TARGETS, **specific}
    counts: Counter[str] = Counter()
    for entry in _library_entries(document):
        for feature in _features(entry):
            counts[feature] += int(entry.get("quantity", 1))
    # Commanders only satisfy specialized requirements, never baseline land/draw counts.
    for entry in commanders:
        counts.update(_features(entry) & specific.keys())
    rows = [
        {
            "requirement": role,
            "actual": counts[role],
            "target": target,
            "gap": max(0, target - counts[role]),
            "basis": (
                "generator-supported-shape"
                if role in specific
                else "generic-role-tag-starter-target"
            ),
        }
        for role, target in targets.items()
    ]
    combinations = analyze_combinations(document)
    report: dict[str, Any] = {
        "combination_analysis": summarize_combinations(combinations),
        "version": "assistant-requirements.v1",
        "archetype": archetype,
        "specialized_status": "supported_shapes" if specific else "unavailable",
        "requirements": rows,
        "commander_relationships": [
            {"name": c.get("name"), "relationships": relationships(c)}
            for c in commanders
        ],
        "relationship_status": "inferred" if specific else "unknown",
        "engine_links": [
            {
                "requirement": role,
                "status": "inferred",
                "basis": "Supported ingredient predicate; compatibility is not a verified interaction.",
            }
            for role in specific
        ],
        "limitations": [
            NOTE,
            "Generic targets are not tuned for every power level or commander.",
        ],
    }
    report["strategy_context"] = build_strategy_context(
        document, constraints, report
    ).model_dump()
    return report


def _identity(raw: Any) -> set[str]:
    return set(json.loads(raw) if isinstance(raw, str) else raw or [])


def _eligible_catalog_lines(
    path: str | Path, document: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        line
        for line in known_combinations(path, document, limit=4)["matches"]
        if line["ingredient_match"] == "all_named_cards_present"
        and line["unresolved_template_count"] == 0
        and line["constraint_status"] == "eligible_source_ingredients"
    ]


def _catalog_preservation_penalty(
    entry: dict[str, Any], lines: list[dict[str, Any]]
) -> int:
    """Number of eligible source claims affected by cutting this canonical card."""
    oid = str(entry.get("oracle_id") or "").lower()
    return sum(
        any(piece["oracle_id"] == oid for piece in line["cards"]) for line in lines
    )


def _catalog_line_present(line: dict[str, Any], document: dict[str, Any]) -> bool:
    entries = _library_entries(document) + [
        e
        for e in document.get("entries", [])
        if isinstance(e, dict) and e.get("is_commander")
    ]
    quantities: Counter[str] = Counter()
    commanders = set()
    for entry in entries:
        oid = str(entry.get("oracle_id") or "").lower()
        quantities[oid] += int(entry.get("quantity", 1))
        if entry.get("is_commander"):
            commanders.add(oid)
    for card in line["cards"]:
        if quantities[card["oracle_id"]] < card["quantity"]:
            return False
    required_commanders = {
        u["name"] for u in line["required_zones"] if u["must_be_commander"]
    }
    return all(
        c["oracle_id"] in commanders
        for c in line["cards"]
        if c["name"] in required_commanders
    )


def recommend_replacements(
    path: str | Path,
    document: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    focus: str = "",
    candidate_limit: int = 3,
    *,
    include_packages: bool = False,
) -> dict[str, Any]:
    """Return bounded isolated proposals; optionally include coordinated packages.

    Final experiment validation remains authoritative. Search is bounded and
    deterministic; insufficient valid candidates are reported, never padded.
    """
    candidate_limit = max(1, min(12, int(candidate_limit)))
    service = ExperimentService(path)
    constraints = service._constraints(constraints or {})
    assessment = assess_deck_requirements(document, constraints)
    gaps = {
        row["requirement"]: row["gap"]
        for row in assessment["requirements"]
        if row["gap"]
    }
    focus = focus[:160].lower()
    if "recovery" in focus or "recursion" in focus:
        actual = sum(
            int(e.get("quantity", 1))
            for e in _library_entries(document)
            if "recursion" in _features(e)
        )
        assessment["requirements"].append(
            {
                "requirement": "recursion",
                "actual": actual,
                "target": 3,
                "gap": max(0, 3 - actual),
                "basis": "requested-recovery-starter-target",
            }
        )
        if actual < 3:
            gaps["recursion"] = 3 - actual
        focus += " recursion"
    requested = {
        role
        for role in {row["requirement"] for row in assessment["requirements"]}
        if role.replace("_", " ") in focus
    }
    if "mana" in focus:
        requested.update({"land", "ramp"})
    if any(
        term in focus for term in ("card advantage", "card selection", "consistency")
    ):
        requested.update({"draw", "card_selection"})
    entries = _library_entries(document)
    features = {e["id"]: _features(e) for e in entries}
    cast_interactions = {
        e["id"]: document_cast_trigger_evidence(document, e) for e in entries
    }
    counts = {r["requirement"]: r["actual"] for r in assessment["requirements"]}
    targets = {r["requirement"]: r["target"] for r in assessment["requirements"]}
    outgoing = [
        e
        for e in entries
        if "//" not in str(e.get("type_line") or "")
        and (
            str(e.get("oracle_text") or "").strip()
            or "basic land" in str(e.get("type_line") or "").lower()
        )
        and e.get("card_id") not in constraints["protected_cards"]
        and ("land" not in features[e["id"]] or counts["land"] > targets["land"])
    ]
    combination_lines = analyze_combinations(document)["combinations"]
    catalog_lines = _eligible_catalog_lines(path, document)
    outgoing.sort(
        key=lambda e: (
            sum(
                any(
                    (
                        piece.get("oracle_id")
                        and piece.get("oracle_id") == e.get("oracle_id")
                    )
                    or (
                        not piece.get("oracle_id")
                        and piece.get("name") == e.get("name")
                    )
                    for piece in line["evidence"]
                )
                for line in combination_lines
            )
            + _catalog_preservation_penalty(e, catalog_lines),
            sum(role in gaps for role in features[e["id"]]),
            str(e.get("name") or "").casefold(),
            str(e.get("oracle_id") or ""),
            e["id"],
        )
    )
    from sabermetrics.assistant_candidate_recall import card_content, recall_candidates

    strategy = StrategyContext.model_validate(assessment["strategy_context"])
    recall = recall_candidates(path, document, strategy, focus)
    pool = {item.card["oracle_id"]: item.card for item in recall.candidates}
    recall_evidence = {
        item.card["oracle_id"]: item.model_dump(exclude={"card"})
        for item in recall.candidates
    }
    support = {
        oid: commander_support(document, card, strategy) for oid, card in pool.items()
    }
    old_support = {
        old["id"]: commander_support(document, old, strategy) for old in outgoing
    }
    improvements = {}
    tradeoffs = {}
    ranked = []
    for card in pool.values():
        caps = _features(card)
        gains = caps & gaps.keys()
        # Broad discovery tags (e.g. a conditional Treasure payoff) cannot
        # establish baseline ramp. Prefer the narrower supported source shapes.
        if "ramp" in gains and not caps & {"direct_mana_source", "land_ramp"}:
            gains.discard("ramp")
        for old in outgoing:
            benefit = functional_improvement(old, card, features[old["id"]], caps)
            tradeoff = strategic_tradeoff(
                old, card, features[old["id"]], caps
            ) or basic_land_color_tradeoff(document, old, card)
            if not _catalog_preservation_penalty(old, catalog_lines):
                if benefit:
                    improvements[(old["id"], card["oracle_id"])] = benefit
                elif tradeoff and (
                    not requested
                    or features[old["id"]] & caps & requested
                    or (
                        tradeoff["kind"] == "mana_color_tradeoff"
                        and bool(requested & {"land", "ramp", "draw", "card_selection"})
                    )
                ):
                    tradeoffs[(old["id"], card["oracle_id"])] = tradeoff
        engine_candidate = any(
            support[card["oracle_id"]] - old_support[old["id"]] for old in outgoing
        )
        sourced_candidate = bool(
            set(recall_evidence[card["oracle_id"]]["channels"])
            & {"combination", "association"}
        )
        if (
            not gains
            and not engine_candidate
            and not sourced_candidate
            and not any(
                key[1] == card["oracle_id"] for key in (*improvements, *tradeoffs)
            )
        ):
            continue
        ranked.append((card, caps, gains))
    ranked.sort(
        key=lambda item: (
            -2
            * int(
                any(key[1] == item[0]["oracle_id"] for key in improvements)
                or any(
                    key[1] == item[0]["oracle_id"]
                    and value["kind"] == "mana_color_tradeoff"
                    for key, value in tradeoffs.items()
                )
            )
            - int(bool(item[2] & requested)),
            -len(item[1] & requested),
            -len(item[2] & requested),
            -int(bool(support[item[0]["oracle_id"]])) if not requested else 0,
            (
                -int("combination" in recall_evidence[item[0]["oracle_id"]]["channels"])
                if not requested
                else 0
            ),
            -len(item[2]),
            int(
                "land" in item[2]
                and "basic land" not in str(item[0].get("type_line") or "").lower()
            ),
            int(
                "land" in item[2]
                and not (
                    "basic land" in str(item[0].get("type_line") or "").lower()
                    or re.search(
                        r"(?:^|\n)\{t\}: add (?:\{[wubrg]\}|(?:one mana|a mana) of any color)",
                        _text(item[0]),
                    )
                )
            ),
            int("ramp" in requested and "direct_mana_source" not in item[1]),
            -len(item[1] & set(targets) - set(GENERIC_TARGETS)),
            casting_cost_basis(item[0])[0],
            item[0]["name"],
        )
    )
    alternatives: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    used_roles: set[str] = set()
    used_oracles: set[str] = set()
    used_outgoing: set[str] = set()
    rejected: Counter[str] = Counter()
    attempts = 0
    # First pass seeks different functions, second allows distinct options for one gap.
    for diverse in (True, False):
        for card, caps, gains in ranked:
            if len(alternatives) == candidate_limit or attempts >= 60:
                break
            if card["oracle_id"] in used_oracles or (
                diverse
                and not gains - used_roles
                and not support[card["oracle_id"]]
                and not any(
                    key[1] == card["oracle_id"] for key in (*improvements, *tradeoffs)
                )
            ):
                continue
            incoming_cast_interactions = document_cast_trigger_evidence(document, card)
            incoming_triggers = {
                item.get("commander")
                for item in incoming_cast_interactions
                if item.get("matches_trigger") is True
            }
            pair_outgoing = sorted(
                outgoing,
                key=lambda old: (
                    -2 * int((old["id"], card["oracle_id"]) in improvements)
                    - int((old["id"], card["oracle_id"]) in tradeoffs),
                    old["id"] in used_outgoing,
                    sum(
                        item.get("matches_trigger") is True
                        and item.get("commander") not in incoming_triggers
                        for item in cast_interactions[old["id"]]
                    ),
                    len(features[old["id"]] - caps),
                    -len(features[old["id"]] & caps),
                ),
            )
            for old in pair_outgoing[:8]:
                improvement = improvements.get((old["id"], card["oracle_id"]))
                tradeoff = tradeoffs.get((old["id"], card["oracle_id"]))
                contextual = improvement or tradeoff
                engine_gains = support[card["oracle_id"]] - old_support[old["id"]]
                if not contextual and engine_gains:
                    contextual = {
                        "kind": "commander_support",
                        "benefit": "Adds a detected commander engine ingredient; full strategic fit requires review",
                    }
                if (
                    not contextual
                    and "combination" in recall_evidence[card["oracle_id"]]["channels"]
                ):
                    contextual = {
                        "kind": "sourced_combo_completion",
                        "benefit": "Adds a missing named ingredient from an attributed combination; prerequisites and execution remain unverified",
                    }
                net_gains = gains - features[old["id"]]
                # Changing one mana source into another does not fill a missing
                # ramp slot merely because one has costs our baseline excludes.
                if "nonland_mana_resource" in features[old["id"]]:
                    net_gains.discard("ramp")
                if (
                    (not net_gains and not contextual)
                    or (
                        requested
                        and not net_gains & requested
                        and not (contextual and features[old["id"]] & caps & requested)
                        and not (
                            tradeoff
                            and tradeoff["kind"] == "mana_color_tradeoff"
                            and requested & {"land", "ramp", "draw", "card_selection"}
                        )
                    )
                    or (diverse and not net_gains - used_roles and not contextual)
                ):
                    continue
                lost = features[old["id"]] - caps
                protected_engine_features = (
                    set(targets) - set(GENERIC_TARGETS)
                    | set(LAND_TARGETS)
                    | {
                        "cheap_noncreature_cast",
                        "opponent_damage_draw",
                        "broad_damage_payoff",
                        "mana_resource",
                        "nonland_mana_resource",
                        "free_cast_access",
                        "triggered_mana_engine",
                        "triggered_ability",
                        "card_selection",
                    }
                )
                if "land" in features[old["id"]] and counts["land"] > targets["land"]:
                    protected_engine_features.discard("mana_resource")
                if lost & protected_engine_features:
                    continue
                if any(
                    role in targets and counts[role] <= targets[role] for role in lost
                ):
                    continue
                replacements = [
                    {"outgoing_entry_id": old["id"], "incoming_card_id": card["id"]}
                ]
                attempts += 1
                try:
                    preview = service._candidate(document, replacements, constraints)
                except (ValueError, TypeError) as exc:
                    rejected[str(exc)[:160]] += 1
                    if attempts >= 60:
                        break
                    continue
                after_assessment = assess_deck_requirements(
                    preview["document"], constraints
                )
                actual_incoming = next(
                    c
                    for c in preview["document"]["entries"]
                    if c.get("card_id") == card["id"]
                )
                if card_content(actual_incoming) != card_content(card):
                    rejected["Canonical card facts changed during discovery"] += 1
                    continue
                transition = assess_strategy_transition(
                    strategy,
                    StrategyContext.model_validate(
                        after_assessment["strategy_context"]
                    ),
                )
                if not transition["allowed"]:
                    rejected["Detected commander support would decrease"] += 1
                    continue
                roles = sorted(net_gains)
                reason = f"Replace {old['name']} with {card['name']} to address {', '.join(r.replace('_', ' ') for r in roles)}. Text-based ingredient fit; measure the isolated alternative before adoption."
                if contextual:
                    reason = f"Test {card['name']} over {old['name']}: {contextual['benefit']}. Review additional abilities and measure the isolated swap."
                alternatives.append(
                    {
                        "name": f"Test {card['name']}",
                        "rationale": reason[:2000],
                        "replacements": replacements,
                    }
                )
                candidate_combinations = analyze_combinations(preview["document"])
                candidate_line_keys = {
                    (line["kind"], tuple(line["cards"]))
                    for line in candidate_combinations["combinations"]
                }
                lost_lines = [
                    {
                        "kind": line["kind"],
                        "cards": line["cards"],
                        "status": "inferred_ingredient_line_lost",
                    }
                    for line in combination_lines
                    if (line["kind"], tuple(line["cards"])) not in candidate_line_keys
                ]
                evidence.append(
                    {
                        "recommendation_type": (
                            "functional_improvement"
                            if improvement
                            else (
                                "strategic_tradeoff"
                                if tradeoff
                                else (
                                    "commander_engine_ingredient"
                                    if engine_gains
                                    else (
                                        "sourced_combo_completion"
                                        if contextual
                                        else "exploratory_infrastructure"
                                    )
                                )
                            )
                        ),
                        "review_required": True,
                        "recall": recall_evidence[card["oracle_id"]],
                        "strategy_transition": transition,
                        "commander_support_gained": sorted(engine_gains),
                        "draw_routes": {
                            "incoming": [r.model_dump() for r in draw_routes(card)],
                            "outgoing": [r.model_dump() for r in draw_routes(old)],
                        },
                        "functional_improvement": improvement,
                        "strategic_tradeoff": tradeoff,
                        "incoming_oracle_facts": oracle_facts(card),
                        "outgoing_oracle_facts": oracle_facts(old),
                        "card_id": card["id"],
                        "name": card["name"],
                        "type_line": card.get("type_line"),
                        "mana_cost": card.get("mana_cost"),
                        "commander_cast_interactions": incoming_cast_interactions,
                        "outgoing_card": {
                            "entry_id": old["id"],
                            "name": old["name"],
                            "type_line": old.get("type_line"),
                            "mana_cost": old.get("mana_cost"),
                            "features": sorted(features[old["id"]]),
                            "commander_cast_interactions": cast_interactions[old["id"]],
                            "roles_lost": sorted(lost),
                            "oracle_excerpt": str(old.get("oracle_text") or "")[:360],
                        },
                        "requirements": roles,
                        "conditions": _mana_conditions(card),
                        "relationships": relationships(card),
                        "fit_status": "inferred",
                        "casting_cost_basis": casting_cost_basis(card)[1],
                        "net_role_gain": len(net_gains),
                        "synergy_ingredients": sorted(caps & protected_engine_features),
                        "oracle_excerpt": str(card.get("oracle_text") or "")[:360],
                        "source": "Imported Oracle text and generator-derived supported predicates",
                        "price_total_usd": preview["price_total_usd"],
                        "semantic_verification": "unverified",
                        "combination_lines_lost": lost_lines,
                        "sourced_combination_lines_lost": [
                            {
                                "variant_id": line["variant_id"],
                                "cards": [c["name"] for c in line["cards"]],
                                "citation": line["citation"],
                                "execution_verified": False,
                                "status": "sourced_ingredient_line_lost",
                            }
                            for line in catalog_lines
                            if not _catalog_line_present(line, preview["document"])
                        ],
                        "sourced_preservation_scope": "At most four constraint-compatible source claims with all named cards and no unresolved templates; ranking preference, not verified execution or power.",
                        "combination_loss_scope": "Bounded discovered lines only; truncated searches are not comprehensive.",
                        "combination_preservation": "Ranking preference; loss of an inferred line is disclosed, not a prohibition or proof of lost win probability.",
                    }
                )
                used_outgoing.add(old["id"])
                used_oracles.add(card["oracle_id"])
                used_roles.update(net_gains)
                break
    if include_packages and candidate_limit >= 3:
        from sabermetrics.assistant_packages import recommend_packages

        packages, package_evidence = recommend_packages(
            service, document, constraints, assessment, recall, focus
        )
        if packages:
            retained_singles = max(0, candidate_limit - len(packages))
            singles = list(
                zip(alternatives[:retained_singles], evidence[:retained_singles])
            )
            coordinated = list(zip(packages, package_evidence))
            # Interleave before the bounded review and final three-result cut;
            # appending packages last would silently starve their reserved slots.
            combined = []
            for index in range(max(len(singles), len(coordinated))):
                if index < len(singles):
                    combined.append(singles[index])
                if index < len(coordinated):
                    combined.append(coordinated[index])
            alternatives = [item[0] for item in combined]
            evidence = [item[1] for item in combined]
    return {
        "status": (
            "ready"
            if len(alternatives) == candidate_limit
            else "insufficient_candidates"
        ),
        "alternatives": alternatives,
        "candidate_evidence": evidence,
        "assessment": assessment,
        "strategy_context": strategy.model_dump(),
        "recall": recall.model_dump(exclude={"candidates"}),
        "requires_semantic_review": bool(alternatives),
        "rejections": dict(rejected.most_common(3)),
        "constraints": {k: constraints[k] for k in ("budget", "power_level", "theme")},
        "limitations": [
            NOTE,
            "Power and theme are recorded preferences, not certified filters; review before adoption.",
            "Conditional alternate-cost cards are not penalized by printed mana value; effective costs and card-specific strategic value remain unknown.",
            "Independent recall channels retain bounded quotas before 60 single-swap validation attempts; no source deck was changed.",
        ],
    }
