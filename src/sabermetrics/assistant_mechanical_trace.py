"""Finite closed-world trace checker, deliberately NOT a Magic rules engine.

Only unconditional activated abilities on simple artifacts/creatures are parsed:
fixed mana, self counters, life gain, self bounce; costs are fixed mana, tap,
self/another sacrifice, and self counter removal. Casting those permanents from
hand pays the printed cost. Everything else abstains, including triggers, static
effects, targeting, haste, delayed returns, once/turn restrictions and loop proofs.

State must explicitly assert no external effects and supply resources and object
identity. This validates a supplied finite scenario, never the actual game state
or an infinite combo. No model, network, or simulator calls.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

from sabermetrics.assistant_ability_clauses import ability_clauses, card_identity

VERSION = "assistant-mechanical-trace.v1"
COLORS = "WUBRGC"


class _Unsupported(Exception):
    pass


class _Invalid(Exception):
    pass


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _mana(text: str) -> tuple[dict[str, int], int]:
    if not re.fullmatch(r"(?:\{(?:[WUBRGC]|\d+)\})+", text):
        raise _Unsupported("Only fixed printed mana costs are supported")
    colored = dict.fromkeys(COLORS, 0)
    generic = 0
    for symbol in re.findall(r"\{([^}]+)\}", text):
        if symbol.isdigit():
            generic += int(symbol)
        else:
            colored[symbol] += 1
    return colored, generic


def _count(text: str) -> int:
    return 1 if text in {"a", "an", "one"} else int(text)


def _parse(card: dict[str, Any], quote: str) -> tuple[list[tuple], tuple]:
    name = re.escape(str(card["name"]))
    self_ref = rf"(?:{name}|this (?:creature|artifact|permanent))"
    if quote.count(":") != 1:
        raise _Unsupported("Only unconditional activated abilities are supported")
    cost, effect = quote.split(":")
    costs: list[tuple] = []
    for item in cost.strip().split(", "):
        if re.fullmatch(r"(?:\{(?:[WUBRGC]|\d+)\})+", item):
            costs.append(("mana", item))
        elif item == "{T}":
            costs.append(("tap",))
        elif re.fullmatch(rf"Sacrifice {self_ref}", item, re.IGNORECASE):
            costs.append(("sacrifice_self",))
        elif m := re.fullmatch(
            r"Sacrifice another (creature|artifact)", item, re.IGNORECASE
        ):
            costs.append(("sacrifice_other", m[1].lower()))
        elif m := re.fullmatch(
            rf"Remove (a|an|one|[1-9]\d*) ([\w+/-]+) counters? from {self_ref}",
            item,
            re.IGNORECASE,
        ):
            if m[2].lower() != "charge":
                raise _Unsupported("Only charge counters have supported semantics")
            costs.append(("remove_counter", m[2].lower(), _count(m[1].lower())))
        else:
            raise _Unsupported("Unparsed exact ability cost: " + item)
    effect = effect.strip()
    result: tuple
    if m := re.fullmatch(r"Add ((?:\{[WUBRGC]\})+)\.", effect):
        result = ("mana", m[1])
    elif m := re.fullmatch(r"(?:You gain|Gain) ([1-9]\d*) life\.", effect):
        result = ("life", int(m[1]))
    elif m := re.fullmatch(
        rf"Put (a|an|one|[1-9]\d*) ([\w+/-]+) counters? on {self_ref}\.",
        effect,
        re.IGNORECASE,
    ):
        if m[2].lower() != "charge":
            raise _Unsupported("Only charge counters have supported semantics")
        result = ("counter", m[2].lower(), _count(m[1].lower()))
    elif re.fullmatch(
        rf"Return {self_ref} to (?:its owner's|your) hand\.", effect, re.IGNORECASE
    ):
        result = ("hand",)
    else:
        raise _Unsupported("Unparsed exact ability effect: " + effect)
    if result[0] in {"counter", "hand"} and any(
        c[0] == "sacrifice_self" for c in costs
    ):
        raise _Unsupported(
            "Self effects across a cost's zone change are outside subset"
        )
    return costs, result


def _pay(state: dict[str, Any], text: str, payment: Any) -> None:
    colored, generic = _mana(text)
    if payment is None and generic == 0:
        payment = colored
    if (
        not isinstance(payment, dict)
        or set(payment) - set(COLORS)
        or any(not _integer(v) for v in payment.values())
    ):
        raise _Unsupported("Supply explicit mana_payment for generic costs")
    paid = {c: payment.get(c, 0) for c in COLORS}
    if (
        any(paid[c] < colored[c] for c in COLORS)
        or sum(paid.values()) != sum(colored.values()) + generic
    ):
        raise _Invalid("Mana payment does not match the exact printed cost")
    if any(paid[c] > state["mana"][c] for c in COLORS):
        raise _Invalid("Insufficient initial/produced mana")
    for c in COLORS:
        state["mana"][c] -= paid[c]


def _move(obj: dict[str, Any], zone: str) -> None:
    obj.update(
        zone=zone,
        counters={},
        incarnation=obj["incarnation"] + 1,
        tapped=False,
        controlled_since_turn_start=False,
    )


def verify_trace(
    cards: list[dict[str, Any]],
    initial_state: dict[str, Any],
    actions: list[dict[str, Any]],
    *,
    check_current: Callable[[], Any] | None = None,
    max_actions: int = 32,
) -> dict[str, Any]:
    """Return status, reason, steps and final_state for at most 32 actions.

    State keys: no_external_effects=True, player, active_player, priority_player,
    phase ('precombat_main'/'postcombat_main'), stack_empty=True, life, mana
    (all WUBRGC keys), objects (id, card_id, zone, incarnation, counters, tapped,
    controlled_since_turn_start, controller, owner). All objects must be supplied;
    their cards must have complete text containing only supported abilities.

    Actions: {action:'activate', source, incarnation, ability_index, ability_quote,
    mana_payment?, sacrifice?}, or {action:'cast', source, incarnation,
    mana_payment?}. No caller-declared effects are executed. Indices follow
    ability_clauses. Failed steps are atomic; final_state is the last valid prefix.
    Guard exceptions propagate. Valid means ONLY finite subset execution.
    """
    guard = check_current or (lambda: None)
    guard()
    state = copy.deepcopy(initial_state)
    steps: list[dict[str, Any]] = []
    status, reason = (
        "valid",
        "Finite supported trace executed; no loop or future-state proof",
    )
    try:
        if len(actions) > min(32, max_actions) or not actions or len(cards) > 32:
            raise _Unsupported("Trace requires 1–32 bounded actions")
        required = {
            "no_external_effects",
            "player",
            "active_player",
            "priority_player",
            "phase",
            "stack_empty",
            "life",
            "mana",
            "objects",
        }
        if set(state) != required or state["no_external_effects"] is not True:
            raise _Unsupported("Explicit closed-world initial state is required")
        if (
            state["stack_empty"] is not True
            or state["phase"] not in {"precombat_main", "postcombat_main"}
            or state["player"] != state["active_player"]
            or state["player"] != state["priority_player"]
        ):
            raise _Unsupported("Only own main-phase empty-stack priority is supported")
        if (
            not _integer(state["life"])
            or state["life"] == 0
            or not isinstance(state["mana"], dict)
            or set(state["mana"]) != set(COLORS)
            or not all(_integer(v) for v in state["mana"].values())
        ):
            raise _Unsupported(
                "Explicit nonnegative life and all six mana resources required"
            )
        catalog = {card_identity(c): c for c in cards}
        if len(catalog) != len(cards):
            raise _Unsupported("Ambiguous card identities")
        parsed: dict[str, list[tuple[list[tuple], tuple]]] = {}
        for cid, card in catalog.items():
            decomposition = ability_clauses(card)
            if (
                decomposition["coverage"] == "unknown"
                or card.get("layout") == "token"
                or not card.get("name")
                or not re.fullmatch(
                    r"(?:Artifact Creature|Artifact|Creature)(?: — [^\n]+)?",
                    str(card.get("type_line") or ""),
                )
            ):
                raise _Unsupported(
                    "Only complete simple artifact/creature cards supported"
                )
            if "Creature" in card["type_line"] and (
                not str(card.get("toughness") or "").isdigit()
                or int(card["toughness"]) <= 0
            ):
                raise _Unsupported(
                    "Creature requires explicit positive printed toughness"
                )
            rows = decomposition["clauses"]
            parsed[cid] = [_parse(card, r["source_quote"]) for r in rows]
            if not rows:
                raise _Unsupported("Missing complete ability text")
        if not isinstance(state["objects"], list) or len(state["objects"]) > 32:
            raise _Unsupported("Objects must be an explicit list")
        ids = set()
        for obj in state["objects"]:
            if set(obj) != {
                "id",
                "card_id",
                "zone",
                "incarnation",
                "counters",
                "tapped",
                "controlled_since_turn_start",
                "controller",
                "owner",
            }:
                raise _Unsupported(
                    "Explicit zones/counters/incarnations/control required"
                )
            if (
                obj["id"] in ids
                or obj["card_id"] not in catalog
                or obj["zone"] not in {"battlefield", "hand", "graveyard", "exile"}
                or not _integer(obj["incarnation"])
            ):
                raise _Unsupported("Ambiguous object identity or unsupported zone")
            ids.add(obj["id"])
            if (
                obj["controller"] != state["player"]
                or obj["owner"] != state["player"]
                or type(obj["tapped"]) is not bool
                or type(obj["controlled_since_turn_start"]) is not bool
                or not isinstance(obj["counters"], dict)
                or not all(_integer(v) for v in obj["counters"].values())
            ):
                raise _Unsupported("Only explicitly owned/controlled objects supported")
            if set(obj["counters"]) - {"charge"}:
                raise _Unsupported("Only charge counters have supported semantics")
            if obj["zone"] != "battlefield" and (obj["counters"] or obj["tapped"]):
                raise _Unsupported("Nonbattlefield counters/tapping are outside subset")
        for index, action in enumerate(actions):
            guard()
            pending = copy.deepcopy(state)
            objects = {o["id"]: o for o in pending["objects"]}
            if set(action) - {
                "action",
                "source",
                "incarnation",
                "ability_index",
                "ability_quote",
                "mana_payment",
                "sacrifice",
            }:
                raise _Unsupported(
                    "Caller-declared effects/state changes are not execution"
                )
            if action.get("action") not in {"activate", "cast"}:
                raise _Unsupported("Only bound activation and recast actions supported")
            obj = objects.get(action.get("source"))
            if obj is None or action.get("incarnation") != obj["incarnation"]:
                raise _Invalid("Missing object or stale incarnation binding")
            card = catalog[obj["card_id"]]
            if action["action"] == "cast":
                if set(action) - {"action", "source", "incarnation", "mana_payment"}:
                    raise _Unsupported("Unexpected cast fields")
                if obj["zone"] != "hand":
                    raise _Invalid("Recast requires this incarnation in hand")
                _pay(
                    pending,
                    str(card.get("mana_cost") or ""),
                    action.get("mana_payment"),
                )
                _move(obj, "battlefield")
            else:
                ability_index = action.get("ability_index")
                rows = ability_clauses(card)["clauses"]
                if (
                    type(ability_index) is not int
                    or not 0 <= ability_index < len(rows)
                    or action.get("ability_quote")
                    != rows[ability_index]["source_quote"]
                ):
                    raise _Invalid("Exact printed ability binding required")
                if obj["zone"] != "battlefield":
                    raise _Invalid("Activation requires a battlefield object")
                costs, effect = parsed[obj["card_id"]][ability_index]
                if (
                    len([c for c in costs if c[0] == "mana"]) > 1
                    or len([c for c in costs if c[0].startswith("sacrifice")]) > 1
                ):
                    raise _Unsupported("Compound resource selections outside subset")
                if "sacrifice" in action and not any(
                    c[0] == "sacrifice_other" for c in costs
                ):
                    raise _Unsupported("Extraneous sacrifice selection")
                if "mana_payment" in action and not any(c[0] == "mana" for c in costs):
                    raise _Unsupported("Extraneous mana payment")
                # Pay non-zone costs first; sacrifice cannot erase counters to
                # evade a separate counter-removal cost on this incarnation.
                for cost in sorted(costs, key=lambda c: c[0].startswith("sacrifice")):
                    kind = cost[0]
                    if kind == "mana":
                        _pay(pending, cost[1], action.get("mana_payment"))
                    elif kind == "tap":
                        if obj["tapped"] or (
                            "Creature" in card["type_line"]
                            and not obj["controlled_since_turn_start"]
                        ):
                            raise _Invalid(
                                "Tap cost unavailable: tapped or summoning sick"
                            )
                        obj["tapped"] = True
                    elif kind == "remove_counter":
                        if obj["counters"].get(cost[1], 0) < cost[2]:
                            raise _Invalid("Insufficient counters on this incarnation")
                        obj["counters"][cost[1]] -= cost[2]
                    elif kind == "sacrifice_self":
                        _move(obj, "graveyard")
                    elif kind == "sacrifice_other":
                        fodder = objects.get(action.get("sacrifice"))
                        if (
                            fodder is None
                            or fodder is obj
                            or fodder["zone"] != "battlefield"
                            or cost[1]
                            not in catalog[fodder["card_id"]]["type_line"].lower()
                        ):
                            raise _Invalid(
                                "Another eligible unconsumed permanent is required"
                            )
                        _move(fodder, "graveyard")
                if effect[0] == "mana":
                    for color in re.findall(r"\{([WUBRGC])\}", effect[1]):
                        pending["mana"][color] += 1
                elif effect[0] == "life":
                    pending["life"] += effect[1]
                elif effect[0] in {"counter", "hand"}:
                    if (
                        obj["zone"] == "battlefield"
                        and obj["incarnation"] == action["incarnation"]
                    ):
                        if effect[0] == "counter":
                            obj["counters"][effect[1]] = (
                                obj["counters"].get(effect[1], 0) + effect[2]
                            )
                        else:
                            _move(obj, "hand")
            guard()
            state = pending
            steps.append(
                {
                    "index": index,
                    "action": action["action"],
                    "source": obj["id"],
                    "incarnation": obj["incarnation"],
                    "status": "valid",
                }
            )
    except (_Unsupported, _Invalid) as exc:
        status = "unsupported" if isinstance(exc, _Unsupported) else "invalid"
        reason = str(exc)
    guard()
    return {
        "version": VERSION,
        "status": status,
        "reason": reason,
        "steps": steps,
        "failed_step": len(steps) if status != "valid" else None,
        "final_state": state,
        "scope": "Finite closed-world supported subset only; no general rules, survival, strategy, or infinite-loop proof.",
    }
