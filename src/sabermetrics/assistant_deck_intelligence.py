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

from sabermetrics import db
from sabermetrics.assistant_experiments import ExperimentService
from sabermetrics.deck_evidence import _card_roles, _library_entries

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
    result.discard("protection")
    if protection:
        result.add("protection")
    if _mana_conditions(card):
        result.add("conditional_mana")
        # A combat-triggered Treasure is an engine payoff, not baseline ramp.
        if "combat_required" in _mana_conditions(card):
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
    if re.search(r"(?:play|return)[^.\n]*land(?: cards?)? from your graveyard", text):
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
    return {
        "version": "assistant-requirements.v1",
        "archetype": archetype,
        "specialized_status": "supported_shapes" if specific else "unavailable",
        "requirements": rows,
        "limitations": [
            NOTE,
            "Generic targets are not tuned for every power level or commander.",
        ],
    }


def _identity(raw: Any) -> set[str]:
    return set(json.loads(raw) if isinstance(raw, str) else raw or [])


def recommend_replacements(
    path: str | Path,
    document: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    focus: str = "",
) -> dict[str, Any]:
    """Return up to three distinct one-swap proposals; never mutate or queue.

    Final experiment validation remains authoritative. Search is bounded and
    deterministic; insufficient valid candidates are reported, never padded.
    """
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
    requested = {role for role in gaps if role.replace("_", " ") in focus}
    if "mana" in focus:
        requested.update(set(gaps) & {"land", "ramp"})
    if "card advantage" in focus:
        requested.update(set(gaps) & {"draw"})
    colors: set[str] = set()
    for entry in document.get("entries", []):
        if entry.get("is_commander"):
            colors.update(_identity(entry.get("color_identity")))
    present = {e.get("oracle_id") for e in document.get("entries", [])}
    entries = _library_entries(document)
    features = {e["id"]: _features(e) for e in entries}
    counts = {r["requirement"]: r["actual"] for r in assessment["requirements"]}
    targets = {r["requirement"]: r["target"] for r in assessment["requirements"]}
    outgoing = [
        e
        for e in entries
        if e.get("card_id") not in constraints["protected_cards"]
        and ("land" not in features[e["id"]] or counts["land"] > targets["land"])
    ]
    outgoing.sort(
        key=lambda e: (
            sum(role in gaps for role in features[e["id"]]),
            -float(e.get("mana_value") or 0),
            e["id"],
        )
    )
    # Fetch per functional family, keeping discovery bounded and diverse.
    patterns = [
        "%draw%",
        "%add %",
        "%counter target%",
        "%destroy target%",
        "%hexproof%",
        "%indestructible%",
        "%phases out%",
        "%whenever a land%",
        "%land%graveyard%",
        "%additional land%",
        "%search your library%",
        "%from your graveyard%",
    ]
    pool: dict[str, dict[str, Any]] = {}
    with db.connect(path) as conn:
        families = [("oracle_text", pattern) for pattern in patterns] + [
            ("type_line", "%Land%")
        ]
        color_slots = ",".join("?" for _ in colors) or "''"
        for column, pattern in families:
            rows = conn.execute(
                f"SELECT * FROM cards WHERE is_legal_in_99=1 AND oracle_id IS NOT NULL AND {column} LIKE ? AND NOT EXISTS (SELECT 1 FROM json_each(COALESCE(NULLIF(color_identity,''),'[]')) WHERE value NOT IN ({color_slots})) GROUP BY oracle_id HAVING id=MIN(id) ORDER BY cmc IS NULL, cmc, name, id LIMIT 100",
                (pattern, *sorted(colors)),
            ).fetchall()
            for row in rows:
                card = dict(row)
                if (
                    card["oracle_id"] not in present
                    or "basic land" in str(card.get("type_line") or "").lower()
                ) and _identity(card.get("color_identity")).issubset(colors):
                    pool.setdefault(card["oracle_id"], card)
    ranked = []
    for card in pool.values():
        caps = _features(card)
        gains = caps & gaps.keys()
        if not gains:
            continue
        ranked.append((card, caps, gains))
    ranked.sort(
        key=lambda item: (
            -len(item[2] & requested),
            -len(item[2]),
            int("ramp" in requested and "direct_mana_source" not in item[1]),
            float(item[0].get("cmc") or 0),
            item[0]["name"],
        )
    )
    alternatives: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    used_roles: set[str] = set()
    used_oracles: set[str] = set()
    rejected: Counter[str] = Counter()
    attempts = 0
    # First pass seeks different functions, second allows distinct options for one gap.
    for diverse in (True, False):
        for card, caps, gains in ranked:
            if len(alternatives) == 3 or attempts >= 60:
                break
            if card["oracle_id"] in used_oracles or (
                diverse and not gains - used_roles
            ):
                continue
            for old in outgoing[:8]:
                net_gains = gains - features[old["id"]]
                if (
                    not net_gains
                    or (requested and not net_gains & requested)
                    or (diverse and not net_gains - used_roles)
                ):
                    continue
                lost = features[old["id"]] - caps
                engine_features = set(LAND_TARGETS) | {
                    "cheap_noncreature_cast",
                    "opponent_damage_draw",
                }
                if lost & engine_features:
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
                roles = sorted(net_gains)
                reason = f"Replace {old['name']} with {card['name']} to address {', '.join(r.replace('_', ' ') for r in roles)}. Text-based ingredient fit; measure the isolated alternative before adoption."
                alternatives.append(
                    {
                        "name": f"Test {card['name']}",
                        "rationale": reason[:2000],
                        "replacements": replacements,
                    }
                )
                evidence.append(
                    {
                        "card_id": card["id"],
                        "name": card["name"],
                        "requirements": roles,
                        "conditions": _mana_conditions(card),
                        "oracle_excerpt": str(card.get("oracle_text") or "")[:360],
                        "source": "Imported Oracle text and generator-derived supported predicates",
                        "price_total_usd": preview["price_total_usd"],
                        "semantic_verification": "unverified",
                    }
                )
                used_oracles.add(card["oracle_id"])
                used_roles.update(net_gains)
                break
    return {
        "status": "ready" if len(alternatives) == 3 else "insufficient_candidates",
        "alternatives": alternatives,
        "candidate_evidence": evidence,
        "assessment": assessment,
        "requires_semantic_review": bool(
            constraints["theme"] or constraints["power_level"]
        ),
        "rejections": dict(rejected.most_common(3)),
        "constraints": {k: constraints[k] for k in ("budget", "power_level", "theme")},
        "limitations": [
            NOTE,
            "Power and theme are recorded preferences, not certified filters; review before adoption.",
            "Outgoing ordering uses printed mana value, not effective casting cost; delve, alternate costs, and card-specific strategic value need review.",
            "Search and preview are bounded to 100 records per family and 60 validation attempts; no source deck was changed.",
        ],
    }
