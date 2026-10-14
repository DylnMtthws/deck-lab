"""``rules-engine-adapter.v1`` bridge for Forge (scripts/benchmark_rules_engines.py).

``capabilities`` prints a ``rules-engine-adapter-capabilities.v1`` document.
``run`` reads ``{"contract": "rules-engine-adapter.v1", "scenario": {...}}``
and prints exactly one ``rules-engine-receipt.v1`` document.

Translation is mechanical in both directions:

* fixture scenario -> bridge request: ``you``/``opp`` become Forge players 0/1,
  permanents become GameState card specs, target strings become lookups;
* Forge snapshot/trace -> receipt: the ReferenceKernel final-state shape and
  trace vocabulary, filled only from what Forge reported.

The receipt carries no timings or resources (the harness measures the child).
Forge provenance, the game log and every scripted decision go to a side file
under ``forge-logs/``. If a scenario needs something the bridge cannot
express faithfully, the receipt is ``unsupported`` and names it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import forge_adapter as fa

CONTRACT = "rules-engine-adapter.v1"
RECEIPT_SCHEMA = "rules-engine-receipt.v1"
CAPABILITIES_SCHEMA = "rules-engine-adapter-capabilities.v1"
PROBE_CAPABILITY = "resource_probe.commander_land_only.v1"

# Capabilities = rules Forge implements AND the bridge can drive/observe.
CAPABILITIES = frozenset(
    {
        "mana_abilities",
        "land_drop",
        "cast_spell",
        "timing_restrictions",
        "stack_and_triggers",
        "counters",
        "damage",
        "static_pt",
        "activation_limits",
        "commander_tax",
        "amass",
        "ring_tempts",
        "draw_discard",
        "library_order",
        "counterspell",
        "state_based_actions",
        "ward_sacrifice_cost",
    }
)

PLAYER_KEYS = {
    "life",
    "hand",
    "library",
    "graveyard",
    "command",
    "commanders",
    "commander_casts",
}
PERM_KEYS = {"name", "controller", "tapped", "counters", "damage"}
COUNTER_CODES = {"+1/+1": "P1P1", "-1/-1": "M1M1"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def scenario_sha256(scenario: dict[str, Any]) -> str:
    return (
        "sha256:" + hashlib.sha256(canonical_json(scenario).encode("utf-8")).hexdigest()
    )


def engine_version() -> str:
    build = (
        (fa.FORGE_DIR / "build.txt").read_text().strip()
        if fa.FORGE_DIR.exists()
        else "?"
    )
    bridge = fa._sha256_file(fa.BRIDGE_SOURCE)[:12]
    return f"forge-2.0.15 (build {build}; bridge {bridge})"


def capabilities_doc() -> dict[str, Any]:
    return {
        "schema_version": CAPABILITIES_SCHEMA,
        "engine": "forge",
        "engine_version": engine_version(),
        "capabilities": sorted(CAPABILITIES),
    }


class Untranslatable(Exception):
    """The scenario uses a feature the bridge cannot express faithfully."""


def _index(label: str) -> int:
    if label not in ("you", "opp"):
        raise Untranslatable(f"player_label:{label}")
    return 0 if label == "you" else 1


def _target(text: str) -> dict[str, Any]:
    kind, _, rest = text.partition(":")
    if kind == "player":
        return {"player": _index(rest)}
    if kind == "perm":
        name, _, ctrl = rest.partition("@")
        out: dict[str, Any] = {"card": name}
        if ctrl:
            out["controller"] = _index(ctrl)
        return out
    if kind == "stack":
        return {"stack_card": rest}
    raise Untranslatable(f"target_syntax:{text}")


def _perm_spec(perm: dict[str, Any], commanders: set[str]) -> str:
    extra = set(perm) - PERM_KEYS
    if extra:
        raise Untranslatable(f"battlefield_key:{min(extra)}")
    parts = [perm["name"]]
    if perm.get("tapped"):
        parts.append("Tapped")
    counters = {k: int(v) for k, v in perm.get("counters", {}).items() if int(v)}
    if counters:
        codes = []
        for kind, n in sorted(counters.items()):
            if kind not in COUNTER_CODES:
                raise Untranslatable(f"counter_kind:{kind}")
            codes.append(f"{COUNTER_CODES[kind]}={n}")
        parts.append("Counters:" + ",".join(codes))
    if int(perm.get("damage", 0)):
        parts.append(f"Damage:{int(perm['damage'])}")
    if perm["name"] in commanders:
        parts.append("IsCommander")
    return "|".join(parts)


def translate(scenario: dict[str, Any]) -> dict[str, Any]:
    """Fixture rules scenario -> bridge request. Raises Untranslatable."""
    state = scenario["initial_state"]
    extra = set(state) - {"active_player", "turn", "players", "battlefield"}
    if extra:
        raise Untranslatable(f"initial_state_key:{min(extra)}")
    if set(state["players"]) != {"you", "opp"}:
        raise Untranslatable("players_other_than_you_opp")
    players: list[dict[str, Any]] = []
    bowmasters_ctrl: str | None = None
    for label in ("you", "opp"):
        raw = state["players"][label]
        unknown = set(raw) - PLAYER_KEYS
        if unknown:
            raise Untranslatable(f"player_key:{min(unknown)}")
        commanders = set(raw.get("commanders", raw.get("command", [])))
        zones: dict[str, list[str]] = {}
        for zone in ("hand", "library", "graveyard"):
            if raw.get(zone):
                zones[zone] = list(raw[zone])
        if raw.get("command"):
            zones["command"] = [
                f"{c}|IsCommander" if c in commanders else c for c in raw["command"]
            ]
        battlefield = [
            _perm_spec(p, commanders)
            for p in state.get("battlefield", [])
            if p["controller"] == label
        ]
        if battlefield:
            zones["battlefield"] = battlefield
        for p in state.get("battlefield", []):
            if p["name"] == "Orcish Bowmasters":
                bowmasters_ctrl = p["controller"]
        players.append(
            {
                "label": label,
                "life": int(raw.get("life", 40)),
                "zones": zones,
                "commander_casts": dict(raw.get("commander_casts", {})),
            }
        )
    actions = []
    choice_keys = {
        "put_back_top_first",
        "ring_bearer",
        "sauron_discard",
        "bowmasters_target",
    }
    for index, action in enumerate(scenario["actions"]):
        op = action.get("op")
        if op not in fa.BRIDGE_OPS:
            raise Untranslatable(f"op:{op}")
        out: dict[str, Any] = {"op": op}
        if "player" in action:
            out["player"] = _index(action["player"])
        for key in ("card", "produce", "count", "from"):
            if key in action:
                out[key] = action[key]
        if "targets" in action:
            out["targets"] = [_target(t) for t in action["targets"]]
        choices = dict(action.get("choices", {}))
        unknown = set(choices) - choice_keys
        if unknown:
            raise Untranslatable(f"choice:{min(unknown)}")
        if "bowmasters_target" in choices:
            choices["bowmasters_target_resolved"] = _target(
                choices.pop("bowmasters_target")
            )
        if choices:
            out["choices"] = choices
        unknown_keys = set(action) - {
            "op",
            "player",
            "card",
            "produce",
            "count",
            "from",
            "targets",
            "choices",
        }
        if unknown_keys:
            raise Untranslatable(f"action[{index}].{min(unknown_keys)}")
        actions.append(out)
    request: dict[str, Any] = {
        "scenario_id": scenario["id"],
        "turn": int(state.get("turn", 1)),
        "active_player": _index(state.get("active_player", "you")),
        "players": players,
        "actions": actions,
    }
    if bowmasters_ctrl is not None:
        # Kernel default for Bowmasters' "any target": the other player's face.
        other = "opp" if bowmasters_ctrl == "you" else "you"
        request["initial_choices"] = {
            "bowmasters_target_resolved": {"player": _index(other)}
        }
    return request


def _perm(card: dict[str, Any]) -> dict[str, Any]:
    name = card["name"]
    if card.get("token") and name.endswith(" Token"):
        name = name[: -len(" Token")]
    entry: dict[str, Any] = {
        "name": name,
        "controller": card["controller"],
        "tapped": card["tapped"],
        "token": card["token"],
        "counters": {k: v for k, v in sorted(card.get("counters", {}).items()) if v},
    }
    if "power" in card:
        entry["power"], entry["toughness"] = card["power"], card["toughness"]
    return entry


def final_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Forge snapshot -> ReferenceKernel.final_state() shape. Pure reshaping."""
    players = {}
    battlefield = []
    for p in snapshot["players"]:
        zones = p["zones"]
        players[p["label"]] = {
            "life": p["life"],
            "hand": sorted(c["name"] for c in zones["hand"]),
            "library": [c["name"] for c in zones["library"]],
            "graveyard": sorted(c["name"] for c in zones["graveyard"]),
            "command": sorted(c["name"] for c in zones["command"]),
            "mana_pool": {k: v for k, v in sorted(p["mana_pool"].items()) if v},
            "commander_casts": {
                k: v for k, v in sorted(p["commander_casts"].items()) if v
            },
            # CR 701.54: the Ring's level is the number of times it has tempted
            # you, capped at four abilities.
            "ring_level": min(4, p["ring_tempted_count"]),
            "ring_bearer": p["ring_bearer"],
            "lost": p["lost"],
        }
        battlefield.extend(_perm(c) for c in zones["battlefield"])
    battlefield.sort(key=lambda e: (e["controller"], e["name"], canonical_json(e)))
    stack = []
    # Forge's stack iterates top-first; the kernel lists bottom -> top.
    for item in reversed(snapshot["stack"]):
        stack.append(
            f"{item['source']}:trigger" if item["is_trigger"] else item["source"]
        )
    return {
        "active_player": snapshot["active_player"],
        "turn": snapshot["turn"],
        "players": players,
        "battlefield": battlefield,
        "stack": stack,
    }


def normalize_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forge-derived events -> kernel vocabulary; consecutive put-backs merge."""
    out: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if pending:
            # Forge moves the chosen cards one at a time, each onto the top,
            # so the last one moved is the top card.
            out.append(
                {
                    "event": "put_back",
                    "player": pending[0]["player"],
                    "cards": [e["card"] for e in reversed(pending)],
                    "origin": pending[0]["origin"],
                }
            )
            pending.clear()

    # Forge names token cards "<name> Token"; report the token's name wherever
    # a token that Forge created in this scenario is referenced.
    tokens = {e["card"] for e in trace if e["event"] == "token_created"}

    def detoken(name: Any) -> Any:
        if isinstance(name, str) and name in tokens and name.endswith(" Token"):
            return name[: -len(" Token")]
        if isinstance(name, str) and name.startswith("perm:") and name[5:] in tokens:
            return "perm:" + detoken(name[5:])
        return name

    for event in trace:
        if event["event"] == "put_back_card":
            pending.append(event)
            continue
        flush()
        e = dict(event)
        for key in ("target", "source", "card"):
            if key in e and e["event"] != "token_created":
                e[key] = detoken(e[key])
        if e["event"] == "token_created":
            e["name"] = e.pop("card")
            e["name"] = e["name"].removesuffix(" Token")
        out.append(e)
    flush()
    return [{"seq": n, **e} for n, e in enumerate(out)]


def _receipt(scenario: dict[str, Any], version: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "engine": "forge",
        "engine_version": version,
        "scenario_id": scenario["id"],
        "scenario_sha256": scenario_sha256(scenario),
        **fields,
    }


def run_contract(
    payload: dict[str, Any], *, require_auth: bool = True
) -> dict[str, Any]:
    scenario = payload.get("scenario")
    version = engine_version()
    if (
        payload.get("contract") != CONTRACT
        or not isinstance(scenario, dict)
        or "id" not in scenario
    ):
        bad = (
            scenario
            if isinstance(scenario, dict) and "id" in scenario
            else {"id": "<unknown>"}
        )
        return _receipt(
            bad,
            version,
            status="error",
            error="payload is not a rules-engine-adapter.v1 request",
        )
    if scenario.get("kind") != "rules_state":
        return _receipt(
            scenario,
            version,
            status="unsupported",
            missing_capabilities=[PROBE_CAPABILITY],
            detail="the Forge bridge runs injected rules states only, not land-only deck probes",
        )
    missing = sorted(set(scenario.get("required_capabilities", [])) - CAPABILITIES)
    if missing:
        return _receipt(
            scenario,
            version,
            status="unsupported",
            missing_capabilities=missing,
            detail="declared before execution",
        )
    try:
        request = translate(scenario)
    except Untranslatable as exc:
        return _receipt(
            scenario,
            version,
            status="unsupported",
            missing_capabilities=[f"bridge_translation:{exc}"],
            detail="scenario feature the bridge cannot express faithfully",
        )
    adapter_receipt = fa.run_scenario(request, require_auth=require_auth)
    return receipt_from_adapter(scenario, request, adapter_receipt, version)


def receipt_from_adapter(
    scenario: dict[str, Any],
    request: dict[str, Any],
    adapter_receipt: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    """Forge adapter receipt -> rules-engine-receipt.v1 (and the side file)."""
    side = _write_side_file(scenario, request, adapter_receipt)
    status = adapter_receipt["status"]
    if status == "unknown_cards":
        names = adapter_receipt["bridge"].get("unknown_cards", [])
        return _receipt(
            scenario,
            version,
            status="unsupported",
            missing_capabilities=[f"forge_card_script:{n}" for n in names],
            detail=f"Forge has no script for these cards; see {side}",
        )
    if status != "executed":
        bridge = adapter_receipt.get("bridge") or {}
        why = (
            bridge.get("error")
            or bridge.get("injection_mismatches")
            or adapter_receipt.get("reason")
        )
        return _receipt(
            scenario,
            version,
            status="error",
            error=f"forge {status}: {why}; see {side}",
        )
    bridge = adapter_receipt["bridge"]
    steps = bridge["steps"]
    illegal = next((s for s in steps if s["outcome"] == "illegal"), None)
    fields: dict[str, Any] = {
        "status": "completed",
        "outcome": "illegal" if illegal else "legal",
        "final_state": final_state(bridge["final_state"]),
        "trace": normalize_trace(bridge["trace"]),
    }
    if illegal:
        fields["illegal_at"] = illegal["index"]
        fields["reason_code"] = illegal["reason_code"]
        fields["detail"] = f"{illegal['detail']} [{illegal['reason_source']}]"
    elif bridge.get("game_over"):
        fields["detail"] = "Forge reports the game is over"
    return _receipt(scenario, version, **fields)


def _write_side_file(
    scenario: dict[str, Any], request: dict[str, Any], receipt: dict[str, Any]
) -> Path:
    fa.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = fa.LOG_DIR / f"{scenario['id']}.{scenario_sha256(scenario)[7:19]}.forge.json"
    path.write_text(
        json.dumps(
            {"bridge_request": request, "adapter_receipt": receipt},
            indent=1,
            sort_keys=True,
        )
    )
    return path
