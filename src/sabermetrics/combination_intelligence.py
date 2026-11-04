"""Small executable interaction models, not a general Magic rules interpreter.

Discovery uses shared Oracle predicates. Replay accepts explicit resources and
zones, and only models authored, narrowly matched actions. A replay verifies that
model's transitions; it cannot establish a win or complete rules legality.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from sabermetrics.card_capabilities import oracle_text, relationships
from sabermetrics.deck_evidence import _library_entries

MAX_CARDS = 120
MAX_LINES = 12

# Declarative contracts describe the executable transitions and deliberately
# expose which setup costs the replay does not pay. New supported models must
# supply their own guarded transitions; relationships alone never execute.
ACTION_CONTRACTS: dict[str, list[dict[str, Any]]] = {
    "cast_damage_draw": [
        {
            "action": "cast",
            "actor": "spell",
            "from_zone": "hand",
            "to_zone": "stack",
            "timing": "instant priority or own sorcery main phase",
            "cost": "printed colored and generic mana",
            "requires": ["engine on battlefield", "Aura attached to engine"],
        },
        {
            "action": "resolve_cast_trigger",
            "actor": "engine",
            "event": "noncreature_cast",
            "produces": {"engine_counters": 1, "damage_each_opponent": 1},
        },
        {
            "action": "resolve_damage_draw_triggers",
            "actor": "Aura",
            "event": "engine damage to opponent",
            "choice": "accept each optional draw",
            "consumes": "one library card per opponent",
            "produces": "one hand card per opponent",
        },
        {
            "action": "resolve_spell",
            "actor": "spell",
            "support": "exact simple draw only; selection spells stop before resolution",
        },
    ],
    "ring_discard_draw": [
        {
            "action": "cast",
            "actor": "spell",
            "from_zone": "hand",
            "to_zone": "stack",
            "cost": "printed colored and generic mana",
            "timing": "instant priority or own sorcery main phase",
        },
        {
            "action": "resolve_spell",
            "actor": "spell",
            "produces": "optional simple draw followed by Ring temptation",
            "to_zone": "graveyard",
        },
        {
            "action": "resolve_ring_trigger",
            "actor": "engine",
            "choice": "accept discard",
            "consumes": "entire current hand and four library cards",
            "produces": {"hand_cards": 4},
        },
    ],
    "upkeep_ring_discard_draw": [
        {
            "action": "resolve_upkeep_trigger",
            "actor": "source",
            "timing": "beginning of controller upkeep",
            "requires": ["source and engine on battlefield"],
            "produces": "ring_tempt",
        },
        {
            "action": "decline_optional_ring_bearer_draw",
            "actor": "source",
            "choice": "decline life payment",
        },
        {
            "action": "resolve_ring_trigger",
            "actor": "engine",
            "choice": "accept discard",
            "consumes": "entire current hand and four library cards",
            "produces": {"hand_cards": 4},
        },
    ],
}


def _cost(card: dict[str, Any]) -> tuple[list[str], int] | None:
    cost = str(card.get("mana_cost") or "")
    symbols = re.findall(r"\{([^}]+)\}", cost)
    if not symbols or "".join("{" + s + "}" for s in symbols) != cost:
        return None
    if any(s not in "WUBRGC" and not s.isdigit() for s in symbols):
        return None
    return [s for s in symbols if not s.isdigit()], sum(
        int(s) for s in symbols if s.isdigit()
    )


def _spell_effects(card: dict[str, Any]) -> list[str] | None:
    # Exact narrow templates: targeted cantrips, selection and additional costs
    # deliberately abstain. Mana value alone never proves payable colored cost.
    kind = str(card.get("type_line") or "").lower()
    if not any(t in kind for t in ("instant", "sorcery")) or "//" in kind:
        return None
    text = re.sub(r"\s+", " ", oracle_text(card)).strip()
    templates = {
        "draw a card.": ["draw"],
        "draw a card. the ring tempts you.": ["draw", "ring_tempt"],
        "the ring tempts you.": ["ring_tempt"],
    }
    if text in {
        "scry 1.\ndraw a card.",
        "scry 1. draw a card.",
        "surveil 1.\ndraw a card.",
        "surveil 1. draw a card.",
        "draw three cards, then put two cards from your hand on top of your library in any order.",
        "look at the top three cards of your library, then put them back in any order. you may shuffle. draw a card.",
    }:
        return ["unsupported_resolution"]
    return templates.get(text)


def _damage_draw_mode(card: dict[str, Any]) -> str | None:
    text = re.sub(r"\s+", " ", oracle_text(card)).strip().removeprefix("flash ")
    if (
        text
        == "enchant creature whenever enchanted creature deals damage to an opponent, you may draw a card."
    ):
        return "attached_optional"
    if re.fullmatch(
        r'soulbond as long as [^.]+ is paired with another creature, each of those creatures has "whenever this creature deals damage to an opponent, draw a card\."',
        text,
    ):
        return "paired_mandatory"
    return None


def analyze_combinations(
    document: dict[str, Any], state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Discover bounded pairs/triples; optionally replay each from the same state.

    State keys: battlefield/hand (names), attachments ({aura: creature}), mana
    ({U: 1, ...}), library_count, hand_count, opponent_count, own_turn,
    main_phase, stack_empty, priority, no_interference, no_other_effects,
    choose_optional_draw, choose_discard. Missing assumptions reject replay.
    Card presence in the deck never supplies battlefield presence or mana.
    """
    entries = (
        _library_entries(document)
        + [
            e
            for e in document.get("entries", [])
            if isinstance(e, dict) and e.get("is_commander")
        ]
    )[:MAX_CARDS]
    lines: list[dict[str, Any]] = []
    for engine in entries:
        text = oracle_text(engine)
        vivi = bool(
            re.search(
                r"whenever you cast a noncreature spell, put a \+1/\+1 counter on [^.]+ and (?:it|[^.]+) deals 1 damage to each opponent",
                text,
            )
        )
        sauron = (
            "whenever the ring tempts you, you may discard your hand. if you do, draw four cards"
            in text
        )
        if not vivi and not sauron:
            continue
        for spell in entries:
            if (
                sauron
                and oracle_text(spell).strip()
                == "at the beginning of your upkeep, the ring tempts you.\nwhenever you choose a creature as your ring-bearer, you may pay 2 life. if you do, draw a card."
            ):
                lines.append(_line("upkeep_ring_discard_draw", engine, spell))
            effects = _spell_effects(spell)
            if effects is None or _cost(spell) is None:
                continue
            if vivi:
                for aura in entries:
                    if _damage_draw_mode(aura) is None:
                        continue
                    line = _line("cast_damage_draw", engine, spell, aura)
                    lines.append(line)
            if sauron and "ring_tempt" in effects:
                lines.append(_line("ring_discard_draw", engine, spell))
            if len(lines) >= MAX_LINES:
                break
        if len(lines) >= MAX_LINES:
            break
    lines = lines[:MAX_LINES]
    for line in lines:
        line["replay"] = (
            replay_combination(line, state)
            if state is not None
            else {"status": "not_run", "reason": "Explicit starting state required."}
        )
    return {
        "version": "combination-intelligence.v1",
        "status": "supported_lines" if lines else "abstained",
        "combinations": lines,
        "search": {
            "cards_examined": len(entries),
            "max_cards": MAX_CARDS,
            "max_lines": MAX_LINES,
            "discovered_search_truncated": len(lines) >= MAX_LINES
            or len(document.get("entries", [])) > MAX_CARDS,
            "source": "authored Oracle-shape templates; no external combo catalog queried",
        },
        "limitations": [
            "Discovered ingredients are inferred. Replay verifies only the bounded model under supplied assumptions, not full Magic legality.",
            "No infinite loop, win, deterministic draw order, or competitive strength is inferred.",
            "Only exact simple draw/Ring spells and the two supported trigger shapes are executable; other mechanics abstain.",
        ],
    }


def _line(
    kind: str,
    engine: dict[str, Any],
    spell: dict[str, Any],
    aura: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cards = [engine, spell] + ([aura] if aura else [])
    contracts = deepcopy(ACTION_CONTRACTS[kind])
    prerequisites = [
        "engine on battlefield",
        "enough library cards",
        "no interference or other relevant effects",
    ]
    if kind == "upkeep_ring_discard_draw":
        prerequisites += [
            "upkeep source on battlefield",
            "own beginning of upkeep",
            "discard accepted",
            "source optional life-payment draw declined",
        ]
    else:
        prerequisites += [
            "spell in hand",
            "payable printed mana cost",
            "priority and timing",
        ]
        if aura and _damage_draw_mode(aura) == "paired_mandatory":
            prerequisites += ["damage-draw creature paired with engine"]
            contracts[0]["requires"] = [
                "engine and damage-draw source on battlefield",
                "source paired with engine",
            ]
            contracts[2]["actor"] = "paired damage-draw source"
            contracts[2]["choice"] = "mandatory draws"
        elif aura:
            prerequisites += ["Aura attached to engine", "optional draws accepted"]
        else:
            prerequisites += ["optional discard accepted"]
    return {
        "kind": kind,
        "status": "inferred",
        "action_contracts": contracts,
        "setup_status": "assumed established; setup casting, attachment and access are not replayed",
        "cards": [c.get("name") for c in cards],
        "prerequisites": prerequisites,
        "evidence": [
            {
                "name": c.get("name"),
                "oracle_id": c.get("oracle_id"),
                "oracle_excerpt": str(c.get("oracle_text") or "")[:600],
                "source": "supplied imported Oracle text",
                "relationships": relationships(c),
            }
            for c in cards
        ],
        "model_cards": cards,
    }


def replay_combination(line: dict[str, Any], initial: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(initial, dict):
        return {
            "status": "rejected",
            "reason": "Starting state must be an object.",
            "trace": [],
            "win_verified": False,
        }
    state = deepcopy(initial)
    trace: list[dict[str, Any]] = []

    def reject(reason: str) -> dict[str, Any]:
        return {
            "status": "rejected",
            "reason": reason,
            "trace": [],
            "win_verified": False,
        }

    for flag in ("priority", "stack_empty", "no_interference", "no_other_effects"):
        if state.get(flag) is not True:
            return reject(f"Explicit {flag}=true assumption required.")
    cards = line.get("model_cards")
    if (
        not isinstance(cards, list)
        or len(cards) not in (2, 3)
        or any(not isinstance(c, dict) for c in cards)
    ):
        return reject("Unsupported combination payload.")
    engine, spell, *auras = cards
    discovered = analyze_combinations({"entries": cards})["combinations"]
    if not any(
        c["kind"] == line.get("kind") and c["cards"] == line.get("cards")
        for c in discovered
    ):
        return reject("Combination does not match supported Oracle predicates.")
    for key in ("battlefield", "hand", "graveyard"):
        values = state.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 200
            or any(not isinstance(v, str) for v in values)
        ):
            return reject(f"Invalid {key} zone.")
    if not isinstance(state.get("paired_with", {}), dict):
        return reject("Invalid paired_with state.")
    if not isinstance(state.get("attachments", {}), dict) or not isinstance(
        state.get("mana", {}), dict
    ):
        return reject("Invalid attachments or mana pool.")
    ename, sname = engine.get("name"), spell.get("name")
    if line["kind"] == "upkeep_ring_discard_draw":
        return _replay_upkeep_ring(engine, spell, state)
    if ename not in state.get("battlefield", []):
        return reject("Engine is not on battlefield.")
    if sname not in state.get("hand", []):
        return reject("Spell is not in hand.")
    if "sorcery" in str(spell.get("type_line") or "").lower() and not (
        state.get("own_turn") is True and state.get("main_phase") is True
    ):
        return reject("Sorcery requires own main phase with empty stack.")
    effects, cost = _spell_effects(spell), _cost(spell)
    if effects is None or cost is None:
        return reject("Unsupported effect or casting cost.")
    if any(
        not isinstance(state.get(k), int) or isinstance(state[k], bool) or state[k] < 0
        for k in ("library_count", "hand_count")
    ):
        return reject("Nonnegative integer library_count and hand_count required.")
    if state["hand_count"] < len(state.get("hand", [])):
        return reject("hand_count contradicts named hand cards.")
    mana = state.get("mana", {})
    if any(
        k not in "WUBRGC"
        or len(k) != 1
        or not isinstance(v, int)
        or isinstance(v, bool)
        or v < 0
        for k, v in mana.items()
    ):
        return reject("Invalid mana pool.")
    mana = dict(mana)
    for color in cost[0]:
        if mana.get(color, 0) < 1:
            return reject("Missing colored mana.")
        mana[color] -= 1
    if sum(mana.values()) < cost[1]:
        return reject("Insufficient generic mana.")
    remaining = cost[1]
    for color in sorted(mana):
        paid = min(remaining, mana[color])
        mana[color] -= paid
        remaining -= paid
    draws = effects.count("draw")
    if line["kind"] == "cast_damage_draw":
        if not auras or auras[0].get("name") not in state.get("battlefield", []):
            return reject("Required damage-draw source is not on battlefield.")
        draw_mode = _damage_draw_mode(auras[0])
        if (
            draw_mode == "attached_optional"
            and state.get("attachments", {}).get(auras[0].get("name")) != ename
        ):
            return reject("Required Aura is not attached to engine.")
        if (
            draw_mode == "paired_mandatory"
            and state.get("paired_with", {}).get(auras[0].get("name")) != ename
        ):
            return reject("Damage-draw creature is not paired with engine.")
        opponents = state.get("opponent_count")
        if (
            not isinstance(opponents, int)
            or isinstance(opponents, bool)
            or opponents < 1
            or opponents > 6
        ):
            return reject("Explicit opponent_count from 1 to 6 required.")
        if (
            draw_mode == "attached_optional"
            and state.get("choose_optional_draw") is not True
        ):
            return reject("Optional draw choice required.")
        draws += opponents
    else:
        if state.get("choose_discard") is not True:
            return reject("Optional discard choice required.")
        draws += 4
    if state["library_count"] < draws:
        return reject(
            "Insufficient library for all modeled draws; no successful outcome claimed."
        )
    for key in ("engine_counters", "damage_each_opponent"):
        value = state.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return reject(f"Invalid {key}.")
    state["mana"] = mana
    state["hand"].remove(sname)
    state["hand_count"] -= 1
    trace.append(
        {
            "action": "cast",
            "card": sname,
            "from": "hand",
            "to": "stack",
            "mana_after": mana.copy(),
        }
    )
    if line["kind"] == "cast_damage_draw":
        state["engine_counters"] = state.get("engine_counters", 0) + 1
        state["damage_each_opponent"] = state.get("damage_each_opponent", 0) + 1
        trace.append(
            {
                "action": "resolve_cast_trigger",
                "source": ename,
                "counter_delta": 1,
                "damage_each_opponent": 1,
            }
        )
        state["hand_count"] += opponents
        state["library_count"] -= opponents
        trace.append(
            {
                "action": "resolve_damage_draw_triggers",
                "source": auras[0].get("name"),
                "cards_drawn": opponents,
            }
        )
    if "unsupported_resolution" in effects:
        return {
            "status": "verified_prefix_only",
            "trace": trace,
            "final_resources": {
                k: state.get(k, 0)
                for k in (
                    "mana",
                    "hand_count",
                    "library_count",
                    "engine_counters",
                    "damage_each_opponent",
                )
            },
            "stack": [sname],
            "unresolved": "Spell resolution is outside the bounded model; cast and triggered effects only.",
            "win_verified": False,
        }
    for effect in effects:
        if effect == "draw":
            state["hand_count"] += 1
            state["library_count"] -= 1
            trace.append({"action": "spell_draw", "cards_drawn": 1})
        else:
            trace.append(
                {
                    "action": "ring_tempts",
                    "source": sname,
                    "ring_bearer_choice": "outside bounded model",
                }
            )
    state.setdefault("graveyard", []).append(sname)
    trace.append(
        {"action": "finish_spell", "card": sname, "from": "stack", "to": "graveyard"}
    )
    if line["kind"] == "ring_discard_draw":
        discarded = state["hand_count"]
        state.setdefault("graveyard", []).extend(state["hand"])
        state["hand"] = []
        state["hand_count"] = 4
        state["library_count"] -= 4
        trace.append(
            {
                "action": "resolve_ring_trigger",
                "source": ename,
                "cards_discarded": discarded,
                "cards_drawn": 4,
            }
        )
    return {
        "status": "verified_within_bounded_model",
        "trace": trace,
        "final_resources": {
            k: state.get(k, 0)
            for k in (
                "mana",
                "hand_count",
                "library_count",
                "graveyard",
                "engine_counters",
                "damage_each_opponent",
            )
        },
        "engine_counters_delta": 1 if line["kind"] == "cast_damage_draw" else 0,
        "damage_each_opponent_delta": 1 if line["kind"] == "cast_damage_draw" else 0,
        "win_verified": False,
        "assumptions": {
            k: v
            for k, v in initial.items()
            if k not in ("hand", "battlefield", "mana", "attachments")
        },
    }


def _replay_upkeep_ring(
    engine: dict[str, Any], source: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    def reject(reason: str) -> dict[str, Any]:
        return {
            "status": "rejected",
            "reason": reason,
            "trace": [],
            "win_verified": False,
        }

    if any(c.get("name") not in state.get("battlefield", []) for c in (engine, source)):
        return reject("Both upkeep source and Sauron engine must be on battlefield.")
    if (
        state.get("own_turn") is not True
        or state.get("beginning_of_upkeep") is not True
    ):
        return reject("Explicit own beginning_of_upkeep required.")
    if (
        state.get("choose_discard") is not True
        or state.get("decline_ring_bearer_draw") is not True
    ):
        return reject(
            "Choose Sauron discard and explicitly decline upkeep source's optional life-payment draw."
        )
    for key in ("hand_count", "library_count"):
        if (
            not isinstance(state.get(key), int)
            or isinstance(state[key], bool)
            or state[key] < 0
        ):
            return reject(f"Nonnegative integer {key} required.")
    if state["hand_count"] < len(state.get("hand", [])):
        return reject("hand_count contradicts named hand cards.")
    if state["library_count"] < 4:
        return reject("Insufficient library for four draws.")
    graveyard = list(state.get("graveyard", [])) + state.get("hand", [])
    return {
        "status": "verified_within_bounded_model",
        "trace": [
            {
                "action": "resolve_upkeep_trigger",
                "source": source.get("name"),
                "event": "ring_tempts",
            },
            {
                "action": "decline_optional_ring_bearer_draw",
                "source": source.get("name"),
            },
            {
                "action": "resolve_ring_trigger",
                "source": engine.get("name"),
                "cards_discarded": state["hand_count"],
                "cards_drawn": 4,
            },
        ],
        "final_resources": {
            "hand_count": 4,
            "library_count": state["library_count"] - 4,
            "graveyard": graveyard,
            "unnamed_cards_discarded": state["hand_count"] - len(state.get("hand", [])),
        },
        "win_verified": False,
        "scope": "Upkeep reactions only; permanents assumed already established, Ring-bearer selection has no other modeled consequences.",
    }


def summarize_combinations(report: dict[str, Any]) -> dict[str, Any]:
    """Compact evidence-deduplicated context; never a comprehensive combo list."""
    selected: list[dict[str, Any]] = []
    seen_kinds: set[tuple[str, str]] = set()
    for line in report["combinations"]:
        family = (line["kind"], line["cards"][-1])
        if family not in seen_kinds and len(selected) < 4:
            selected.append(line)
            seen_kinds.add(family)
    for line in report["combinations"]:
        if len(selected) < 4 and line not in selected:
            selected.append(line)
    evidence: dict[str, dict[str, Any]] = {}
    compact = []
    contracts: dict[str, list[dict[str, Any]]] = {}
    for line in selected:
        references = []
        for piece in line["evidence"]:
            key = str(piece.get("oracle_id") or piece.get("name"))
            references.append(key)
            evidence.setdefault(
                key,
                {
                    "name": piece["name"],
                    "oracle_id": piece.get("oracle_id"),
                    "oracle_excerpt": piece["oracle_excerpt"][:240],
                    "source": piece["source"],
                },
            )
        mode = (
            _damage_draw_mode(line["model_cards"][-1])
            if line["kind"] == "cast_damage_draw"
            else None
        )
        contract_ref = line["kind"] + (":" + str(mode) if mode else "")
        contracts.setdefault(contract_ref, deepcopy(line["action_contracts"]))
        compact.append(
            {
                "prerequisites": list(line["prerequisites"]),
                "action_contract_ref": contract_ref,
                "combination_index": report["combinations"].index(line),
                "kind": line["kind"],
                "status": line["status"],
                "cards": line["cards"],
                "evidence_refs": references,
                "actions": [a["action"] for a in line["action_contracts"]],
                "setup_status": line["setup_status"],
                "replay": line["replay"],
            }
        )
    return {
        "version": report["version"],
        "status": report["status"],
        "total_discovered_count": len(report["combinations"]),
        "omitted_count": len(report["combinations"]) - len(compact),
        "combinations": compact,
        "evidence": evidence,
        "action_contracts": contracts,
        "search": report["search"],
        "limitations": report["limitations"],
    }
