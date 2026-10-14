"""Benchmark Magic rules engines on fixed Vivi/Sauron scenarios.

Usage:
    python scripts/benchmark_rules_engines.py \
        [--fixtures tests/fixtures/rules_engine_scenarios.json] \
        [--engines reference,native,forge,xmage] [--repeat 3] \
        [--native-bin PATH] [--scenario ID ...] [--out report.json]
    python scripts/benchmark_rules_engines.py --validate-report report.json

Every scenario carries an expected outcome. The expected outcome is written
by hand from oracle text, Scryfall rulings, the Comprehensive Rules or an
upstream engine's own test. No engine's output is used to write it. Each
engine's result is checked against that expectation, and engines that both
completed a scenario are compared with each other.

Engines and how they are reached:

* ``reference``: ``ReferenceKernel``, a small rules kernel in this file. It
  implements only the capabilities it declares. A scenario that needs
  anything else is reported ``unsupported``, naming the missing capability.
* ``native``: ``commander_simulator``. Its only state-controlled entry point
  is ``cs --resource-request``, which covers land-and-commander-only probes.
  Every other scenario is ``unsupported``, with the reason stated.
* ``forge`` / ``xmage``: external engines reached through the
  ``rules-engine-adapter.v1`` bridge contract (``ExternalCommandAdapter``).
  If the Java runtime, the engine install or the bridge command is missing,
  the engine is reported ``unavailable`` with every missing piece listed. No
  result is produced for it.

The harness takes all timing and resource measurements itself. An adapter
receipt that reports its own timings is rejected, and so is a partial,
malformed or mismatched receipt. A saved report can be checked again with
``--validate-report``; it fails if any verdict differs from one recomputed
from the receipts.

Exit codes: 0 when every completed result matches its expectation; 1 when
any result diverges, errors or replays differently; 2 when the fixtures or
the report are invalid.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import platform
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURE_SCHEMA = "rules-engine-scenarios.v1"
RECEIPT_SCHEMA = "rules-engine-receipt.v1"
REPORT_SCHEMA = "rules-engine-benchmark-report.v1"
ADAPTER_CONTRACT = "rules-engine-adapter.v1"
CAPABILITIES_SCHEMA = "rules-engine-adapter-capabilities.v1"
KERNEL_VERSION = "reference-kernel.v1"
PROBE_CAPABILITY = "resource_probe.commander_land_only.v1"

DEFAULT_FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "rules_engine_scenarios.json"
)

OUTCOMES = frozenset({"legal", "illegal", "rejected"})
RECEIPT_STATUSES = frozenset({"completed", "unsupported", "error"})
VERDICTS = frozenset(
    {
        "match",
        "divergent",
        "unsupported",
        "unavailable",
        "error",
        "rejected_receipt",
        "nondeterministic",
    }
)
COLORS = "WUBRG"
# A stochastic expectation is checked with a normal approximation to the
# binomial. A 4.5-sigma threshold makes random failures uncommon under that
# approximation, but does not eliminate them; investigate failures before
# attributing them to a rules or probability error.
DEFAULT_Z_MAX = 4.5


class FixtureError(ValueError):
    """The scenario file is malformed or internally inconsistent."""


class ReceiptError(ValueError):
    """An adapter receipt is malformed, partial or self-measured."""


class ReportError(ValueError):
    """A benchmark report fails re-validation."""


class IllegalAction(Exception):
    """A scripted action is illegal under the rules the kernel implements."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class Unsupported(Exception):
    """The scenario needs a capability the kernel does not implement."""

    def __init__(self, capability: str) -> None:
        super().__init__(capability)
        self.capability = capability


class ScenarioError(Exception):
    """The scenario itself is incomplete (for example, a missing choice)."""


# --------------------------------------------------------------------------
# Canonical hashing
# --------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text used for every hash in this module.

    Args:
        value: Any JSON-serialisable value.

    Returns:
        Compact, key-sorted JSON.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    """Return ``sha256:<hex>`` of UTF-8 text.

    Args:
        text: Text to hash.

    Returns:
        The prefixed hex digest.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def scenario_sha256(scenario: dict[str, Any]) -> str:
    """Hash a scenario so a receipt can be bound to it.

    Args:
        scenario: One scenario object from the fixture file.

    Returns:
        ``sha256:<hex>`` of the scenario's canonical JSON.
    """
    return sha256_text(canonical_json(scenario))


def file_sha256(path: Path) -> str:
    """Hash a file's bytes.

    Args:
        path: File to hash.

    Returns:
        ``sha256:<hex>``.
    """
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------

_SCENARIO_KEYS = {
    "id",
    "kind",
    "title",
    "deck_context",
    "polarity",
    "required_capabilities",
    "provenance",
    "expected",
    "expected_derivation",
}
_RULES_KEYS = _SCENARIO_KEYS | {"initial_state", "actions"}
_PROBE_KEYS = _SCENARIO_KEYS | {"probe"}


def load_fixtures(path: Path) -> dict[str, Any]:
    """Load and structurally validate the scenario fixture file.

    Args:
        path: Path to ``rules_engine_scenarios.json``.

    Returns:
        The parsed fixture document.

    Raises:
        FixtureError: If any scenario is malformed, unsourced or duplicated.
    """
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read fixtures: {exc}") from exc
    if doc.get("schema_version") != FIXTURE_SCHEMA:
        raise FixtureError("fixture schema_version must be " + FIXTURE_SCHEMA)
    sources = doc.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise FixtureError("fixtures need a non-empty sources map")
    for source_id, source in sources.items():
        if (
            not isinstance(source, dict)
            or not source.get("url")
            or not source.get("kind")
        ):
            raise FixtureError(f"source {source_id} needs url and kind")
    cards = doc.get("card_provenance")
    if not isinstance(cards, dict) or not cards:
        raise FixtureError("fixtures need card_provenance")
    for name, card in cards.items():
        for key in ("oracle_id", "oracle_text", "scryfall_uri", "retrieved_at"):
            if key not in card:
                raise FixtureError(f"card_provenance[{name}] lacks {key}")
    scenarios = doc.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise FixtureError("fixtures need a non-empty scenarios list")
    seen: set[str] = set()
    for scenario in scenarios:
        _validate_scenario(scenario, sources, cards)
        if scenario["id"] in seen:
            raise FixtureError(f"duplicate scenario id {scenario['id']}")
        seen.add(scenario["id"])
    return dict(doc)


def _validate_scenario(
    scenario: dict[str, Any], sources: dict[str, Any], cards: dict[str, Any]
) -> None:
    sid = scenario.get("id", "<missing id>")
    kind = str(scenario.get("kind"))
    allowed = {"rules_state": _RULES_KEYS, "resource_probe": _PROBE_KEYS}.get(kind)
    if allowed is None:
        raise FixtureError(f"{sid}: unknown kind {kind!r}")
    keys = set(scenario)
    if keys != allowed:
        raise FixtureError(
            f"{sid}: keys differ; missing={sorted(allowed - keys)} "
            f"extra={sorted(keys - allowed)}"
        )
    if scenario["polarity"] not in {"positive", "negative"}:
        raise FixtureError(f"{sid}: polarity must be positive or negative")
    if not scenario["provenance"]:
        raise FixtureError(f"{sid}: provenance is required")
    for ref in scenario["provenance"]:
        if ref not in sources:
            raise FixtureError(f"{sid}: unknown provenance source {ref}")
    if not scenario["required_capabilities"]:
        raise FixtureError(f"{sid}: required_capabilities is empty")
    if not str(scenario["expected_derivation"]).strip():
        raise FixtureError(f"{sid}: expected_derivation is empty")
    expected = scenario["expected"]
    if expected.get("outcome") not in OUTCOMES:
        raise FixtureError(f"{sid}: expected.outcome must be one of {sorted(OUTCOMES)}")
    if expected["outcome"] == "illegal" and (
        not isinstance(expected.get("illegal_at"), int)
        or not expected.get("reason_code")
    ):
        raise FixtureError(f"{sid}: illegal outcome needs illegal_at and reason_code")
    if expected["outcome"] == "rejected" and not expected.get("detail_contains"):
        raise FixtureError(f"{sid}: rejected outcome needs detail_contains")
    if (
        kind == "rules_state"
        and scenario["polarity"] == "negative"
        and expected["outcome"] == "legal"
        and not expected.get("trace_absent")
    ):
        raise FixtureError(
            f"{sid}: a negative legal scenario must assert trace_absent events"
        )
    if kind == "rules_state":
        for name in _scenario_card_names(scenario):
            if name not in cards and name != "Orc Army":
                raise FixtureError(f"{sid}: card {name!r} lacks card_provenance")
        if not isinstance(scenario["actions"], list) or not scenario["actions"]:
            raise FixtureError(f"{sid}: actions must be non-empty")
        if expected["outcome"] == "illegal" and not (
            0 <= expected["illegal_at"] < len(scenario["actions"])
        ):
            raise FixtureError(f"{sid}: illegal_at is outside the action list")
        if expected["outcome"] == "legal" and not expected.get("trace"):
            raise FixtureError(f"{sid}: a legal rules scenario needs an expected trace")
    else:
        _validate_probe_spec(sid, scenario)


def _validate_probe_spec(sid: str, scenario: dict[str, Any]) -> None:
    probe = scenario["probe"]
    for key in ("slots", "commander_cost", "turns", "games", "seed"):
        if key not in probe:
            raise FixtureError(f"{sid}: probe lacks {key}")
    expected = scenario["expected"]
    stat = expected.get("statistic")
    if stat is not None:
        if not 0.0 <= float(stat.get("probability", -1)) <= 1.0:
            raise FixtureError(f"{sid}: statistic.probability must be in [0, 1]")
        derived = exact_probe_probability(stat.get("exact_model", {}))
        if derived is None or abs(derived - float(stat["probability"])) > 1e-12:
            raise FixtureError(
                f"{sid}: statistic.probability {stat['probability']} does not "
                f"match its exact_model ({derived})"
            )


def _scenario_card_names(scenario: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    state = scenario["initial_state"]
    for perm in state.get("battlefield", []):
        names.add(perm["name"])
    for player in state.get("players", {}).values():
        for zone in ("hand", "library", "graveyard", "command"):
            names.update(player.get(zone, []))
    for action in scenario["actions"]:
        if "card" in action:
            names.add(action["card"])
    return names


# --------------------------------------------------------------------------
# Exact probabilities for land-only probes (independent of every engine)
# --------------------------------------------------------------------------


def exact_probe_probability(model: dict[str, Any]) -> float | None:
    """Compute the exact probability a land-only probe expects.

    Two closed models are supported. ``two_color_untapped`` covers a deck
    made only of untapped lands of two mono colours, where the commander
    needs one pip of each colour plus generic mana, and the player always
    plays a missing colour when one is in hand. It is cast by turn ``T``
    exactly when both colours are among the first ``7 + T`` cards and
    ``T >= cmc``. ``tapped_lands_count`` covers ``L`` identical tapped lands
    that each make every needed colour; the commander is cast by turn ``T``
    when at least ``cmc`` land drops landed by turn ``T - 1``. That case is
    computed by an exact dynamic program over the draw order.

    Args:
        model: ``{"name": ..., ...}`` parameters for one of the two models.

    Returns:
        The exact probability, or None if the model is unknown.
    """
    name = model.get("name")
    if name == "two_color_untapped":
        a, b, deck = int(model["color_a"]), int(model["color_b"]), int(model["deck"])
        turns, cmc = int(model["turns"]), int(model["cmc"])
        if a + b != deck:
            return None
        if turns < cmc:
            return 0.0
        seen = min(deck, 7 + turns)
        total = math.comb(deck, seen)
        return 1.0 - math.comb(a, seen) / total - math.comb(b, seen) / total
    if name == "tapped_lands_count":
        lands, deck = int(model["lands"]), int(model["deck"])
        turns, cmc = int(model["turns"]), int(model["cmc"])
        return _tapped_land_dp(lands, deck, turns, cmc)
    return None


def _tapped_land_dp(lands: int, deck: int, turns: int, cmc: int) -> float:
    # State: (lands drawn so far, land drops made so far) -> probability.
    # The opening seven is hypergeometric. Each turn draws one card and then
    # plays a land if an unplayed one is in hand.
    dist: dict[tuple[int, int], float] = {}
    for k in range(min(7, lands) + 1):
        p = math.comb(lands, k) * math.comb(deck - lands, 7 - k) / math.comb(deck, 7)
        if p:
            dist[(k, 0)] = p
    drawn = 7
    hit = 0.0
    for turn in range(1, turns + 1):
        nxt: dict[tuple[int, int], float] = {}
        for (seen_lands, played), p in dist.items():
            remaining = deck - drawn
            land_p = (lands - seen_lands) / remaining if remaining else 0.0
            for is_land, q in ((True, land_p), (False, 1.0 - land_p)):
                if q == 0.0:
                    continue
                s = seen_lands + (1 if is_land else 0)
                # Lands that were playable before this turn are untapped now;
                # the cast check reads drops made on earlier turns only.
                if played >= cmc:
                    hit += p * q
                    continue
                new_played = played + (1 if s > played else 0)
                key = (s, new_played)
                nxt[key] = nxt.get(key, 0.0) + p * q
        dist = nxt
        drawn += 1
    return hit


# --------------------------------------------------------------------------
# Reference rules kernel
# --------------------------------------------------------------------------

# The kernel implements exactly these capabilities. A scenario that needs any
# other capability is reported unsupported before it runs.
REFERENCE_CAPABILITIES = frozenset(
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
        PROBE_CAPABILITY,
    }
)

# Card behaviour keyed by exact oracle name. Oracle text lives in the fixture
# file with Scryfall provenance; this table is the kernel's implementation of
# that text, limited to the abilities named in each comment.
KERNEL_CARDS: dict[str, dict[str, Any]] = {
    "Island": {"types": {"Land"}, "produces": "U"},
    "Mountain": {"types": {"Land"}, "produces": "R"},
    "Volcanic Island": {"types": {"Land"}, "produces": "UR"},
    # "This land enters tapped. {T}: Add {U}, {B}, or {R}."
    "Crumbling Necropolis": {"types": {"Land"}, "produces": "UBR", "etb_tapped": True},
    # "{T}: Add {C}{C}."
    "Sol Ring": {"types": {"Artifact"}, "cost": "{1}", "tap_mana": {"C": 2}},
    # "{0}: Add X mana in any combination of {U} and/or {R}, where X is Vivi
    # Ornitier's power. Activate only during your turn and only once each
    # turn. Whenever you cast a noncreature spell, put a +1/+1 counter on
    # Vivi Ornitier and it deals 1 damage to each opponent."
    "Vivi Ornitier": {
        "types": {"Creature"},
        "subtypes": {"Wizard"},
        "legendary": True,
        "cost": "{1}{U}{R}",
        "pt": (0, 3),
    },
    # Implemented: "Whenever an opponent casts a spell, amass Orcs 1." and
    # "Whenever the Ring tempts you, you may discard your hand. If you do,
    # draw four cards." NOT implemented: Ward (raises Unsupported) and the
    # Army combat-damage trigger (no combat in the kernel).
    "Sauron, the Dark Lord": {
        "types": {"Creature"},
        "subtypes": {"Avatar", "Horror"},
        "legendary": True,
        "cost": "{3}{U}{B}{R}",
        "pt": (7, 6),
    },
    # ETB and "whenever an opponent draws a card except the first one they
    # draw in each of their draw steps" -> 1 damage to any target, amass Orcs
    # 1. The kernel has no draw step, so every opponent draw triggers.
    "Orcish Bowmasters": {
        "types": {"Creature"},
        "subtypes": {"Orc", "Archer"},
        "cost": "{1}{B}",
        "pt": (1, 1),
        "flash": True,
    },
    "Ornithopter": {"types": {"Artifact", "Creature"}, "cost": "{0}", "pt": (0, 2)},
    # "Creatures you control get +2/+2."
    "Dictate of Heliod": {"types": {"Enchantment"}, "cost": "{3}{W}{W}", "flash": True},
    "Lightning Bolt": {"types": {"Instant"}, "cost": "{R}", "effect": "damage3"},
    "Incinerate": {"types": {"Instant"}, "cost": "{1}{R}", "effect": "damage3"},
    "Brainstorm": {"types": {"Instant"}, "cost": "{U}", "effect": "brainstorm"},
    "Birthday Escape": {"types": {"Sorcery"}, "cost": "{U}", "effect": "birthday"},
    "Counterspell": {"types": {"Instant"}, "cost": "{U}{U}", "effect": "counter"},
}


def parse_cost(cost: str) -> tuple[int, Counter[str]]:
    """Split a mana cost like ``{3}{U}{B}{R}`` into generic and coloured parts.

    Args:
        cost: Mana cost in Scryfall brace notation.

    Returns:
        ``(generic, pips)``.

    Raises:
        Unsupported: For hybrid, Phyrexian, X or snow symbols.
    """
    generic = 0
    pips: Counter[str] = Counter()
    for symbol in re.findall(r"\{([^}]+)\}", cost):
        if symbol.isdigit():
            generic += int(symbol)
        elif symbol in COLORS or symbol == "C":
            pips[symbol] += 1
        else:
            raise Unsupported(f"mana_symbol:{symbol}")
    return generic, pips


def _other(player: str) -> str:
    return "opp" if player == "you" else "you"


@dataclass
class Perm:
    """A permanent on the battlefield."""

    pid: int
    name: str
    controller: str
    tapped: bool = False
    counters: Counter[str] = field(default_factory=Counter)
    token: bool = False
    damage: int = 0
    subtypes: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    base_pt: tuple[int, int] | None = None


class ReferenceKernel:
    """A small, explicit rules kernel used as the reference engine.

    It is not a general Magic engine. It implements two players, one main
    phase per turn, an explicit stack with triggered abilities, mana
    abilities, commander tax, amass, the Ring tempting you and state-based
    actions, for the cards in ``KERNEL_CARDS`` only.
    """

    def __init__(self, initial_state: dict[str, Any]) -> None:
        """Build a game state from a scenario's ``initial_state``.

        Args:
            initial_state: The fixture's initial state object.

        Raises:
            Unsupported: If the state names a card the kernel does not implement.
        """
        self.trace: list[dict[str, Any]] = []
        self.next_id = 1
        self.active = initial_state.get("active_player", "you")
        self.turn = int(initial_state.get("turn", 1))
        self.players: dict[str, dict[str, Any]] = {}
        for name in ("you", "opp"):
            raw = initial_state["players"][name]
            for zone in ("hand", "library", "graveyard", "command"):
                for card in raw.get(zone, []):
                    self._card(card)
            self.players[name] = {
                "life": int(raw.get("life", 40)),
                "hand": list(raw.get("hand", [])),
                "library": list(raw.get("library", [])),
                "graveyard": list(raw.get("graveyard", [])),
                "command": list(raw.get("command", [])),
                "commanders": set(raw.get("commanders", raw.get("command", []))),
                "commander_casts": Counter(raw.get("commander_casts", {})),
                "pool": Counter(),
                "land_played": False,
                "ring_level": 0,
                "ring_bearer": None,
                "lost": False,
                "drew_from_empty": False,
            }
        self.battlefield: list[Perm] = []
        for raw in initial_state.get("battlefield", []):
            perm = self._make_perm(raw["name"], raw["controller"])
            perm.tapped = bool(raw.get("tapped", False))
            perm.counters.update(raw.get("counters", {}))
            perm.damage = int(raw.get("damage", 0))
            self.battlefield.append(perm)
        self.stack: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.activations: Counter[int] = Counter()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _card(name: str) -> dict[str, Any]:
        card = KERNEL_CARDS.get(name)
        if card is None:
            raise Unsupported(f"card_not_implemented:{name}")
        return card

    def _make_perm(self, name: str, controller: str, token: bool = False) -> Perm:
        if token:
            card: dict[str, Any] = {
                "types": {"Creature"},
                "subtypes": set(),
                "pt": (0, 0),
            }
        else:
            card = self._card(name)
        perm = Perm(
            pid=self.next_id,
            name=name,
            controller=controller,
            token=token,
            subtypes=set(card.get("subtypes", set())),
            types=set(card["types"]),
            base_pt=card.get("pt"),
        )
        self.next_id += 1
        return perm

    def _log(self, event: str, **fields: Any) -> None:
        self.trace.append({"seq": len(self.trace), "event": event, **fields})

    def power_toughness(self, perm: Perm) -> tuple[int, int]:
        """Return a creature's current power and toughness.

        Args:
            perm: The permanent.

        Returns:
            ``(power, toughness)`` after counters and static effects.
        """
        if perm.base_pt is None:
            return (0, 0)
        p, t = perm.base_pt
        bonus = perm.counters.get("+1/+1", 0)
        dictates = sum(
            1
            for other in self.battlefield
            if other.name == "Dictate of Heliod" and other.controller == perm.controller
        )
        return (p + bonus + 2 * dictates, t + bonus + 2 * dictates)

    def _find_perm(self, name: str, controller: str | None = None) -> Perm:
        for perm in self.battlefield:
            if perm.name == name and (
                controller is None or perm.controller == controller
            ):
                return perm
        raise IllegalAction("no_such_permanent", name)

    # -- actions -----------------------------------------------------------

    def apply(self, index: int, action: dict[str, Any]) -> None:
        """Apply one scripted action, all-or-nothing.

        Args:
            index: Position of the action in the scenario.
            action: The action object.

        Raises:
            IllegalAction: The action is illegal; state is left unchanged.
            Unsupported: The action needs an unimplemented capability.
            ScenarioError: The action is underspecified.
        """
        snapshot = copy.deepcopy(self.__dict__)
        try:
            op = action.get("op")
            handler: Callable[[dict[str, Any]], None] | None = {
                "play_land": self._play_land,
                "activate_mana": self._activate_mana,
                "cast": self._cast,
                "resolve": self._resolve_all,
                "move_to_command_zone": self._to_command,
                "advance_turn": self._advance_turn,
            }.get(op or "")
            if handler is None:
                raise ScenarioError(f"unknown op {op!r} at action {index}")
            handler(action)
        except IllegalAction as exc:
            trace_len = len(snapshot["trace"])
            self.__dict__.update(snapshot)
            self.trace = self.trace[:trace_len]
            self._log("illegal", action_index=index, reason_code=exc.code)
            raise

    def _play_land(self, action: dict[str, Any]) -> None:
        player, name = action["player"], action["card"]
        state = self.players[player]
        if "Land" not in self._card(name)["types"]:
            raise IllegalAction("not_a_land", name)
        if name not in state["hand"]:
            raise IllegalAction("card_not_in_zone", name)
        if player != self.active:
            raise IllegalAction("not_your_turn", name)
        if self.stack:
            raise IllegalAction("stack_not_empty", name)
        if state["land_played"]:
            raise IllegalAction("land_drop_used", name)
        state["hand"].remove(name)
        perm = self._make_perm(name, player)
        perm.tapped = bool(self._card(name).get("etb_tapped"))
        self.battlefield.append(perm)
        state["land_played"] = True
        self._log("play_land", player=player, card=name, tapped=perm.tapped)

    def _activate_mana(self, action: dict[str, Any]) -> None:
        player, name = action["player"], action["card"]
        produce = action.get("produce", {})
        count = int(action.get("count", 1))
        state = self.players[player]
        if name == "Vivi Ornitier":
            perm = self._find_perm(name, player)
            if player != self.active:
                raise IllegalAction("not_your_turn", name)
            if self.activations[perm.pid] >= 1:
                raise IllegalAction("activation_limit", name)
            power = max(0, self.power_toughness(perm)[0])
            if not isinstance(produce, dict) or set(produce) - {"U", "R"}:
                raise IllegalAction("invalid_mana_choice", "Vivi makes only U and/or R")
            if sum(int(v) for v in produce.values()) != power:
                raise IllegalAction(
                    "invalid_mana_choice",
                    f"X is {power}, chose {sum(produce.values())}",
                )
            self.activations[perm.pid] += 1
            state["pool"].update({k: int(v) for k, v in produce.items() if int(v)})
            self._log(
                "mana",
                player=player,
                source=name,
                produced={k: int(v) for k, v in sorted(produce.items()) if int(v)},
            )
            return
        card = self._card(name)
        untapped = [
            p
            for p in self.battlefield
            if p.name == name and p.controller == player and not p.tapped
        ]
        if len(untapped) < count:
            raise IllegalAction("no_untapped_source", f"{name} x{count}")
        for perm in untapped[:count]:
            if "tap_mana" in card:
                made = dict(card["tap_mana"])
            else:
                color = str(produce)
                if color not in card.get("produces", ""):
                    raise IllegalAction(
                        "invalid_mana_choice", f"{name} cannot make {color}"
                    )
                made = {color: 1}
            perm.tapped = True
            state["pool"].update(made)
            self._log("mana", player=player, source=name, produced=made)

    def _pay(self, player: str, generic: int, pips: Counter[str]) -> dict[str, int]:
        pool = self.players[player]["pool"]
        paid: Counter[str] = Counter()
        for color, n in pips.items():
            if pool[color] < n:
                raise IllegalAction("unpayable_cost", f"missing {color}")
            paid[color] += n
        left = pool - paid
        need = generic
        for color in "C" + COLORS:
            take = min(need, left[color])
            paid[color] += take
            need -= take
        if need:
            raise IllegalAction("unpayable_cost", f"missing {need} generic")
        pool.subtract(paid)
        for color in list(pool):
            if pool[color] <= 0:
                del pool[color]
        return {k: v for k, v in sorted(paid.items()) if v}

    def _resolve_target(self, target: str, caster: str) -> dict[str, Any]:
        kind, _, rest = target.partition(":")
        if kind == "player" and rest in self.players:
            return {"kind": "player", "player": rest}
        if kind == "perm":
            name, _, controller = rest.partition("@")
            perm = self._find_perm(name, controller or None)
            if perm.name == "Sauron, the Dark Lord" and perm.controller != caster:
                # Ward -- "Sacrifice a legendary artifact or legendary
                # creature." The kernel has no ward cost payment.
                raise Unsupported("ward_sacrifice_cost")
            return {"kind": "perm", "pid": perm.pid, "name": perm.name}
        if kind == "stack":
            for item in reversed(self.stack):
                if item["kind"] == "spell" and item["card"] == rest:
                    return {"kind": "stack", "sid": item["sid"], "name": rest}
            raise IllegalAction("illegal_target", target)
        raise IllegalAction("illegal_target", target)

    def _cast(self, action: dict[str, Any]) -> None:
        player, name = action["player"], action["card"]
        zone = action.get("from", "hand")
        state = self.players[player]
        card = self._card(name)
        if "Land" in card["types"]:
            raise IllegalAction("not_castable", name)
        if name not in state[zone]:
            raise IllegalAction("card_not_in_zone", f"{name} in {zone}")
        if zone == "command" and name not in state["commanders"]:
            raise IllegalAction("not_a_commander", name)
        instant_speed = "Instant" in card["types"] or card.get("flash")
        if not instant_speed and (player != self.active or self.stack):
            raise IllegalAction("timing", f"{name} is sorcery-speed")
        effect = card.get("effect")
        targets = [self._resolve_target(t, player) for t in action.get("targets", [])]
        if effect in {"damage3", "counter"} and len(targets) != 1:
            raise IllegalAction("illegal_target", f"{name} needs one target")
        if effect == "counter" and targets[0]["kind"] != "stack":
            raise IllegalAction("illegal_target", "Counterspell targets a spell")
        if effect == "damage3" and targets[0]["kind"] == "stack":
            raise IllegalAction("illegal_target", "damage needs a player or permanent")
        generic, pips = parse_cost(card["cost"])
        tax = 0
        if zone == "command":
            # CR 903.8: {2} more for each previous cast from the command zone.
            tax = 2 * state["commander_casts"][name]
        paid = self._pay(player, generic + tax, pips)
        state[zone].remove(name)
        if zone == "command":
            state["commander_casts"][name] += 1
        sid = self.next_id
        self.next_id += 1
        self.stack.append(
            {
                "kind": "spell",
                "sid": sid,
                "card": name,
                "controller": player,
                "targets": targets,
                "choices": dict(action.get("choices", {})),
            }
        )
        self._log("cast", player=player, card=name, zone=zone, paid=paid, tax=tax)
        noncreature = "Creature" not in card["types"]
        for perm in self.battlefield:
            if (
                perm.name == "Vivi Ornitier"
                and perm.controller == player
                and noncreature
            ):
                self._queue("vivi_cast", perm, {})
            if perm.name == "Sauron, the Dark Lord" and perm.controller != player:
                self._queue("sauron_amass", perm, {})
        self._flush_triggers(action.get("choices", {}))

    def _queue(self, kind: str, source: Perm, choices: dict[str, Any]) -> None:
        self.pending.append(
            {
                "kind": "trigger",
                "trigger": kind,
                "source_pid": source.pid,
                "source": source.name,
                "controller": source.controller,
                "choices": dict(choices),
            }
        )

    def _flush_triggers(self, choices: dict[str, Any]) -> None:
        # CR 603.3b: active player's triggers go on the stack first, so the
        # nonactive player's resolve first.
        order = [self.active, _other(self.active)]
        for player in order:
            for trig in [t for t in self.pending if t["controller"] == player]:
                trig["sid"] = self.next_id
                self.next_id += 1
                trig["choices"] = {**choices, **trig["choices"]}
                self.stack.append(trig)
                self._log(
                    "trigger",
                    source=trig["source"],
                    trigger=trig["trigger"],
                    controller=player,
                )
        self.pending = []

    def _resolve_all(self, action: dict[str, Any]) -> None:
        if not self.stack:
            raise IllegalAction("stack_empty")
        op_choices = dict(action.get("choices", {}))
        while self.stack:
            item = self.stack.pop()
            choices = {**op_choices, **item.get("choices", {})}
            if item["kind"] == "spell":
                self._resolve_spell(item, choices)
            else:
                self._resolve_trigger(item, choices)
            self._state_based_actions()
            self._flush_triggers(choices)

    def _target_legal(self, target: dict[str, Any]) -> bool:
        if target["kind"] == "player":
            return not self.players[target["player"]]["lost"]
        if target["kind"] == "perm":
            return any(p.pid == target["pid"] for p in self.battlefield)
        return any(i.get("sid") == target["sid"] for i in self.stack)

    def _resolve_spell(self, item: dict[str, Any], choices: dict[str, Any]) -> None:
        name, player = item["card"], item["controller"]
        card = self._card(name)
        targets = item["targets"]
        if targets and not any(self._target_legal(t) for t in targets):
            self._log("fizzle", card=name)
            self.players[player]["graveyard"].append(name)
            return
        self._log("resolve", item="spell", card=name)
        effect = card.get("effect")
        if effect == "damage3":
            self._damage(name, targets[0], 3)
        elif effect == "counter":
            sid = targets[0]["sid"]
            countered = next(i for i in self.stack if i.get("sid") == sid)
            self.stack.remove(countered)
            self.players[countered["controller"]]["graveyard"].append(countered["card"])
            self._log("countered", card=countered["card"])
        elif effect == "brainstorm":
            self._draw(player, 3)
            put_back = choices.get("put_back_top_first")
            hand = self.players[player]["hand"]
            if not isinstance(put_back, list) or len(put_back) != 2:
                raise ScenarioError(
                    "Brainstorm needs choices.put_back_top_first (2 cards)"
                )
            remaining = Counter(hand)
            for card_name in put_back:
                if remaining[card_name] <= 0:
                    raise IllegalAction("invalid_choice", f"{card_name} not in hand")
                remaining[card_name] -= 1
            for card_name in reversed(put_back):
                hand.remove(card_name)
                self.players[player]["library"].insert(0, card_name)
            self._log("put_back", player=player, cards=list(put_back))
        elif effect == "birthday":
            self._draw(player, 1)
            self._ring_tempts(player, choices)
        if effect is None:
            perm = self._make_perm(name, player)
            self.battlefield.append(perm)
            self._log("enters", card=name, controller=player)
            if name == "Orcish Bowmasters":
                self._queue("bowmasters", perm, {})
        else:
            self.players[player]["graveyard"].append(name)

    def _resolve_trigger(self, item: dict[str, Any], choices: dict[str, Any]) -> None:
        self._log("resolve", item="trigger", source=item["source"])
        controller = item["controller"]
        source = next(
            (p for p in self.battlefield if p.pid == item["source_pid"]), None
        )
        kind = item["trigger"]
        if kind == "vivi_cast":
            if source is not None:
                source.counters["+1/+1"] += 1
                self._log("counters", target=source.name, kind="+1/+1", n=1)
            # It deals damage even if it left (last known information).
            self._damage(
                item["source"], {"kind": "player", "player": _other(controller)}, 1
            )
        elif kind == "sauron_amass":
            self._amass(controller, 1)
        elif kind == "sauron_ring":
            decision = choices.get("sauron_discard")
            if not isinstance(decision, bool):
                raise ScenarioError(
                    "Sauron's tempt trigger needs choices.sauron_discard"
                )
            if decision:
                hand = self.players[controller]["hand"]
                for card in sorted(hand):
                    self._log("discard", player=controller, card=card)
                self.players[controller]["graveyard"].extend(sorted(hand))
                hand.clear()
                self._draw(controller, 4)
            else:
                self._log("declined", source=item["source"])
        elif kind == "bowmasters":
            target_text = choices.get(
                "bowmasters_target", "player:" + _other(controller)
            )
            target = self._resolve_target(target_text, controller)
            if self._target_legal(target):
                self._damage(item["source"], target, 1)
                self._amass(controller, 1)
            else:
                self._log("fizzle", card=item["source"])

    def _damage(self, source: str, target: dict[str, Any], amount: int) -> None:
        if target["kind"] == "player":
            self.players[target["player"]]["life"] -= amount
            self._log(
                "damage",
                source=source,
                target="player:" + target["player"],
                amount=amount,
            )
            return
        perm = next(p for p in self.battlefield if p.pid == target["pid"])
        perm.damage += amount
        self._log("damage", source=source, target="perm:" + perm.name, amount=amount)

    def _amass(self, player: str, n: int) -> None:
        armies = [
            p
            for p in self.battlefield
            if p.controller == player and "Army" in p.subtypes
        ]
        if not armies:
            token = self._make_perm("Orc Army", player, token=True)
            token.subtypes = {"Orc", "Army"}
            self.battlefield.append(token)
            self._log(
                "token_created",
                name="Orc Army",
                controller=player,
                power=0,
                toughness=0,
            )
            armies = [token]
        army = armies[0]
        army.subtypes.add("Orc")
        army.counters["+1/+1"] += n
        self._log("counters", target=army.name, kind="+1/+1", n=n)

    def _draw(self, player: str, n: int) -> None:
        state = self.players[player]
        for _ in range(n):
            if not state["library"]:
                state["drew_from_empty"] = True
                self._log("draw_empty", player=player)
                continue
            card = state["library"].pop(0)
            state["hand"].append(card)
            self._log("draw", player=player, card=card)
            for perm in self.battlefield:
                if perm.name == "Orcish Bowmasters" and perm.controller != player:
                    self._queue("bowmasters", perm, {})

    def _ring_tempts(self, player: str, choices: dict[str, Any]) -> None:
        state = self.players[player]
        state["ring_level"] = min(4, state["ring_level"] + 1)
        creatures = [
            p
            for p in self.battlefield
            if p.controller == player and "Creature" in p.types
        ]
        bearer = None
        if creatures:
            wanted = choices.get("ring_bearer")
            if wanted is None and len(creatures) == 1:
                wanted = creatures[0].name
            if wanted not in {c.name for c in creatures}:
                raise ScenarioError(
                    "the Ring tempts you: choices.ring_bearer is required"
                )
            bearer = wanted
        state["ring_bearer"] = bearer
        self._log(
            "ring_tempts", player=player, level=state["ring_level"], bearer=bearer
        )
        for perm in self.battlefield:
            if perm.name == "Sauron, the Dark Lord" and perm.controller == player:
                self._queue("sauron_ring", perm, {})

    def _state_based_actions(self) -> None:
        changed = True
        while changed:
            changed = False
            for perm in list(self.battlefield):
                if "Creature" not in perm.types:
                    continue
                _, toughness = self.power_toughness(perm)
                if toughness <= 0 or perm.damage >= toughness:
                    self.battlefield.remove(perm)
                    if not perm.token:
                        self.players[perm.controller]["graveyard"].append(perm.name)
                    self._log("sba_dies", card=perm.name)
                    changed = True
            for name, state in self.players.items():
                if not state["lost"] and (
                    state["life"] <= 0 or state["drew_from_empty"]
                ):
                    state["lost"] = True
                    self._log("sba_loses", player=name)

    def _to_command(self, action: dict[str, Any]) -> None:
        name = action["card"]
        perm = self._find_perm(name)
        owner = perm.controller
        if name not in self.players[owner]["commanders"]:
            raise IllegalAction("not_a_commander", name)
        self.battlefield.remove(perm)
        self.players[owner]["command"].append(name)
        self._log("zone", card=name, to="command")

    def _advance_turn(self, action: dict[str, Any]) -> None:
        if self.stack:
            raise IllegalAction("stack_not_empty")
        for state in self.players.values():
            state["pool"] = Counter()
            state["land_played"] = False
        self.active = _other(self.active)
        self.turn += 1
        self.activations = Counter()
        for perm in self.battlefield:
            perm.damage = 0
            if perm.controller == self.active:
                perm.tapped = False
        self._log("turn", active=self.active, turn=self.turn)
        # CR 504.1: the new active player draws in the draw step. Drawing
        # from an empty library loses at the next state-based check (704.5b).
        # Only turn 1 of a two-player game skips the draw (103.8a), and
        # advance_turn never produces turn 1.
        self._draw(self.active, 1)
        self._state_based_actions()
        self._flush_triggers({})

    # -- output ------------------------------------------------------------

    def final_state(self) -> dict[str, Any]:
        """Return the normalized final state compared against expectations.

        Returns:
            Zones per player (hand and graveyard sorted, library top-first),
            the battlefield sorted by controller and name, and the stack.
        """
        players = {}
        for name, state in self.players.items():
            players[name] = {
                "life": state["life"],
                "hand": sorted(state["hand"]),
                "library": list(state["library"]),
                "graveyard": sorted(state["graveyard"]),
                "command": sorted(state["command"]),
                "mana_pool": {k: v for k, v in sorted(state["pool"].items()) if v},
                "commander_casts": dict(sorted(state["commander_casts"].items())),
                "ring_level": state["ring_level"],
                "ring_bearer": state["ring_bearer"],
                "lost": state["lost"],
            }
        battlefield = []
        for perm in self.battlefield:
            entry: dict[str, Any] = {
                "name": perm.name,
                "controller": perm.controller,
                "tapped": perm.tapped,
                "token": perm.token,
                "counters": {k: v for k, v in sorted(perm.counters.items()) if v},
            }
            if "Creature" in perm.types:
                entry["power"], entry["toughness"] = self.power_toughness(perm)
            battlefield.append(entry)
        battlefield.sort(key=lambda e: (e["controller"], e["name"], canonical_json(e)))
        return {
            "active_player": self.active,
            "turn": self.turn,
            "players": players,
            "battlefield": battlefield,
            "stack": [
                i.get("card") or f"{i['source']}:{i['trigger']}" for i in self.stack
            ],
        }


# --------------------------------------------------------------------------
# Land-only probe: request compilation and an independent Python reference
# --------------------------------------------------------------------------


def compile_probe_request(probe: dict[str, Any]) -> dict[str, Any]:
    """Compile a fixture probe into a ``resource-probe-request.v1`` document.

    Slot ids are ``<id>#<n>``; ``deck_identity`` is the SHA-256 of the
    compact JSON array of ids, the identity ``cs`` recomputes.

    Args:
        probe: The scenario's ``probe`` object.

    Returns:
        The request document.
    """
    cards = []
    for slot in probe["slots"]:
        for n in range(1, int(slot["count"]) + 1):
            cards.append(
                {
                    "id": f"{slot['id']}#{n}",
                    "land": bool(slot["land"]),
                    "colors": int(slot["colors"]),
                    "tapped": bool(slot["tapped"]),
                }
            )
    ids = json.dumps(
        [c["id"] for c in cards], separators=(",", ":"), ensure_ascii=False
    )
    identity = "sha256:" + hashlib.sha256(ids.encode()).hexdigest()
    if probe.get("corrupt_identity"):
        identity = "sha256:" + "0" * 64
    return {
        "schema_version": "resource-probe-request.v1",
        "cards": cards,
        "commander_cost": probe["commander_cost"],
        "extra_land_plays": int(probe.get("extra_land_plays", 0)),
        "landfall_draw": int(probe.get("landfall_draw", 0)),
        "games": int(probe["games"]),
        "turns": int(probe["turns"]),
        "seed": int(probe["seed"]),
        "deck_identity": identity,
    }


def _payable(cost: dict[str, Any], lands: list[int]) -> bool:
    """Whether untapped one-mana lands (colour masks) can pay ``cost``."""
    pips = [i for i, n in enumerate(cost["pips"]) for _ in range(int(n))]
    if len(lands) < len(pips) + int(cost["generic"]):
        return False

    def assign(k: int, used: frozenset[int]) -> bool:
        if k == len(pips):
            return True
        bit = 1 << pips[k]
        return any(
            i not in used and lands[i] & bit and assign(k + 1, used | {i})
            for i in range(len(lands))
        )

    return assign(0, frozenset())


def reference_probe(request: dict[str, Any]) -> dict[str, Any]:
    """Run a land-only probe with an independent Python implementation.

    It follows the request's declared semantics: opening seven, a draw on
    turn one, one land per turn, and a missing commander colour played
    first. It shares no code or RNG with ``cs``. Results are therefore
    comparable with ``cs`` only statistically, except for decks where the
    shuffle cannot matter.

    Args:
        request: A compiled ``resource-probe-request.v1`` document.

    Returns:
        ``{"outcome": "legal", "final_state": ...}`` or a rejection.
    """
    problem = _probe_request_problem(request)
    if problem:
        return {"outcome": "rejected", "detail": problem}
    cards = request["cards"]
    cost = request["commander_cost"]
    cast = 0
    lands_total = 0
    for game in range(request["games"]):
        rng = random.Random(f"{request['seed']}:{game}")
        order = list(range(len(cards)))
        rng.shuffle(order)
        hand = order[:7]
        library = order[7:]
        board: list[tuple[int, bool]] = []  # (colour mask, untapped)
        done = False
        for _turn in range(request["turns"]):
            board = [(mask, True) for mask, _ in board]
            if library:
                hand.append(library.pop(0))
            if not done and _payable(cost, [m for m, up in board if up]):
                done = True
            land_ids = [i for i in hand if cards[i]["land"]]
            if land_ids:
                have = 0
                for mask, _ in board:
                    have |= mask
                missing = sum(1 << c for c, n in enumerate(cost["pips"]) if n) & ~have

                def score(i: int, missing: int = missing) -> tuple[int, int]:
                    return (
                        bool(cards[i]["colors"] & missing),
                        not cards[i]["tapped"],
                    )

                pick = max(land_ids, key=score)
                hand.remove(pick)
                board.append((cards[pick]["colors"], not cards[pick]["tapped"]))
            if not done and _payable(cost, [m for m, up in board if up]):
                done = True
        cast += done
        lands_total += len(board)
    return {
        "outcome": "legal",
        "final_state": {
            "games": request["games"],
            "turns": request["turns"],
            "commander_cast_count": cast,
            "lands_mean": lands_total / request["games"],
        },
    }


def _probe_request_problem(request: dict[str, Any]) -> str | None:
    cards = request.get("cards")
    if not isinstance(cards, list) or len(cards) != 99:
        return "99 card slots required"
    for card in cards:
        if not card["land"] and (card["tapped"] or card["colors"]):
            return "nonland source in land-only scenario"
    ids = json.dumps(
        [c["id"] for c in cards], separators=(",", ":"), ensure_ascii=False
    )
    if request["deck_identity"] != "sha256:" + hashlib.sha256(ids.encode()).hexdigest():
        return "deck identity mismatch"
    return None


# --------------------------------------------------------------------------
# Adapter contract
# --------------------------------------------------------------------------


@dataclass
class Availability:
    """Whether an engine can run here, and exactly why not if it cannot."""

    available: bool
    version: str | None
    reasons: list[str]
    capabilities: list[str]
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form stored in reports.

        Returns:
            A plain dict.
        """
        return {
            "available": self.available,
            "version": self.version,
            "reasons": list(self.reasons),
            "capabilities": sorted(self.capabilities),
            "provenance": self.provenance,
        }


@dataclass
class RawRun:
    """One adapter execution: its receipt and what the harness measured."""

    receipt: dict[str, Any]
    wall_ns: int
    cpu_ns: int
    peak_rss_bytes: int | None
    python_heap_peak_bytes: int | None


class EngineAdapter:
    """Base class: one engine reachable from the benchmark harness."""

    name = "abstract"

    def probe(self) -> Availability:
        """Report availability, version and capabilities.

        Returns:
            The engine's availability.
        """
        raise NotImplementedError

    def run(self, scenario: dict[str, Any], sha: str) -> RawRun:
        """Run one scenario. The harness, not the engine, measures it.

        Args:
            scenario: The scenario object.
            sha: The scenario's ``scenario_sha256``.

        Returns:
            The receipt and the measurements.
        """
        raise NotImplementedError


def _receipt(
    engine: str, version: str, scenario: dict[str, Any], sha: str, **fields: Any
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "engine": engine,
        "engine_version": version,
        "scenario_id": scenario["id"],
        "scenario_sha256": sha,
        **fields,
    }


class ReferenceAdapter(EngineAdapter):
    """In-process adapter for ``ReferenceKernel`` and ``reference_probe``."""

    name = "reference"

    def __init__(self) -> None:
        """Pin the version to the hash of this file."""
        self.version = f"{KERNEL_VERSION}+{file_sha256(Path(__file__))[7:19]}"
        self._heap_peak: dict[str, int] = {}

    def probe(self) -> Availability:
        """The reference kernel is always available.

        Returns:
            Availability with the kernel's declared capabilities.
        """
        return Availability(
            True,
            self.version,
            [],
            sorted(REFERENCE_CAPABILITIES),
            {"implementation": "scripts/benchmark_rules_engines.py:ReferenceKernel"},
        )

    def execute(self, scenario: dict[str, Any], sha: str) -> dict[str, Any]:
        """Execute a scenario and return a receipt, with no measurement.

        Args:
            scenario: The scenario object.
            sha: Its ``scenario_sha256``.

        Returns:
            A ``rules-engine-receipt.v1`` document.
        """
        if scenario["kind"] == "resource_probe":
            result = reference_probe(compile_probe_request(scenario["probe"]))
            if result["outcome"] == "rejected":
                return _receipt(
                    self.name,
                    self.version,
                    scenario,
                    sha,
                    status="completed",
                    outcome="rejected",
                    reason_code="request_rejected",
                    detail=result["detail"],
                    final_state={},
                    trace=[],
                )
            return _receipt(
                self.name,
                self.version,
                scenario,
                sha,
                status="completed",
                outcome="legal",
                final_state=result["final_state"],
                trace=[],
            )
        try:
            kernel = ReferenceKernel(scenario["initial_state"])
            for index, action in enumerate(scenario["actions"]):
                try:
                    kernel.apply(index, action)
                except IllegalAction as exc:
                    return _receipt(
                        self.name,
                        self.version,
                        scenario,
                        sha,
                        status="completed",
                        outcome="illegal",
                        illegal_at=index,
                        reason_code=exc.code,
                        detail=exc.detail,
                        final_state=kernel.final_state(),
                        trace=kernel.trace,
                    )
        except Unsupported as exc:
            return _receipt(
                self.name,
                self.version,
                scenario,
                sha,
                status="unsupported",
                missing_capabilities=[exc.capability],
                detail="raised during execution",
            )
        except ScenarioError as exc:
            return _receipt(
                self.name, self.version, scenario, sha, status="error", error=str(exc)
            )
        return _receipt(
            self.name,
            self.version,
            scenario,
            sha,
            status="completed",
            outcome="legal",
            final_state=kernel.final_state(),
            trace=kernel.trace,
        )

    def run(self, scenario: dict[str, Any], sha: str) -> RawRun:
        """Run in-process. Wall and CPU time come from this process.

        Args:
            scenario: The scenario object.
            sha: Its ``scenario_sha256``.

        Returns:
            The receipt and measurements. The heap peak comes from one
            separate ``tracemalloc`` pass per scenario, so tracing does not
            inflate the timing.
        """
        wall0, cpu0 = time.perf_counter_ns(), time.process_time_ns()
        receipt = self.execute(scenario, sha)
        wall, cpu = time.perf_counter_ns() - wall0, time.process_time_ns() - cpu0
        if sha not in self._heap_peak:
            tracemalloc.start()
            try:
                self.execute(scenario, sha)
                self._heap_peak[sha] = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        return RawRun(receipt, wall, cpu, None, self._heap_peak[sha])


def _run_measured(
    argv: list[str], stdin_bytes: bytes = b"", timeout: float = 120.0
) -> tuple[int, bytes, bytes, int, int, int]:
    """Run a child and read its own rusage with ``os.wait4``.

    Args:
        argv: Command line.
        stdin_bytes: Bytes written to the child's stdin.
        timeout: Seconds before the child is killed.

    Returns:
        ``(exit_code, stdout, stderr, wall_ns, cpu_ns, peak_rss_bytes)``.
        The CPU and RSS figures belong to this one child, not to every child
        the process has run.
    """
    with (
        tempfile.TemporaryFile() as fin,
        tempfile.TemporaryFile() as fout,
        tempfile.TemporaryFile() as ferr,
    ):
        fin.write(stdin_bytes)
        fin.seek(0)
        wall0 = time.perf_counter_ns()
        proc = subprocess.Popen(argv, stdin=fin, stdout=fout, stderr=ferr)
        deadline = time.monotonic() + timeout
        while True:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > deadline:
                proc.kill()
                pid, status, usage = os.wait4(proc.pid, 0)
                break
            time.sleep(0.0005)
        wall = time.perf_counter_ns() - wall0
        proc.returncode = os.waitstatus_to_exitcode(status)
        fout.seek(0)
        ferr.seek(0)
        out, err = fout.read(), ferr.read()
    cpu = int((usage.ru_utime + usage.ru_stime) * 1e9)
    # macOS reports ru_maxrss in bytes, Linux in KiB.
    rss = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    return proc.returncode, out, err, wall, cpu, int(rss)


_NATIVE_RESULT_KEYS = {
    "schema_version",
    "scenario",
    "deck_identity",
    "simulation_input_sha256",
    "games",
    "turns",
    "commander_cast_count",
    "lands_mean",
    "commander_draws_mean",
    "cast_samples",
    "assumptions",
}


class NativeProbeAdapter(EngineAdapter):
    """``commander_simulator`` through ``cs --resource-request`` only."""

    name = "native"
    NO_STATE_ENTRY = (
        "commander_simulator has no state-injection entry point: the only "
        "controlled scenario CLI is --resource-request "
        "(resource-probe-request.v1: 99 land/blank slots and one commander "
        "cost; src/io/resource_probe_io.cpp). Whole-game goldfish runs "
        "(--request) cannot set a board, hand, stack or opponent action."
    )

    def __init__(self, binary: Path | None) -> None:
        """Bind to a ``cs`` binary.

        Args:
            binary: Path to the ``cs`` executable, or None if not configured.
        """
        self.binary = binary
        self._availability: Availability | None = None

    def probe(self) -> Availability:
        """Check the binary exists and answers ``--version``.

        Returns:
            Availability, including the binary's hash for provenance.
        """
        if self._availability is not None:
            return self._availability
        if self.binary is None:
            self._availability = Availability(
                False,
                None,
                [
                    "no cs binary configured (pass --native-bin or set CEDH_SIMULATOR_BIN)"
                ],
                [],
                {},
            )
            return self._availability
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            self._availability = Availability(
                False, None, [f"cs binary not executable: {self.binary}"], [], {}
            )
            return self._availability
        result = subprocess.run(
            [str(self.binary), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            self._availability = Availability(
                False, None, [f"cs --version exited {result.returncode}"], [], {}
            )
            return self._availability
        provenance: dict[str, Any] = {
            "binary": str(self.binary),
            "binary_sha256": file_sha256(self.binary),
        }
        repo = _git_root(self.binary)
        if repo:
            provenance["source_repo"] = str(repo)
            provenance["source_head"] = _git(repo, "rev-parse", "HEAD")
            provenance["source_dirty_paths"] = len(
                [x for x in _git(repo, "status", "--porcelain").splitlines() if x]
            )
        self._availability = Availability(
            True, result.stdout.strip(), [], [PROBE_CAPABILITY], provenance
        )
        return self._availability

    def run(self, scenario: dict[str, Any], sha: str) -> RawRun:
        """Run one probe scenario as a child process.

        Args:
            scenario: A ``resource_probe`` scenario.
            sha: Its ``scenario_sha256``.

        Returns:
            The receipt and the child's own wall, CPU and peak RSS.
        """
        assert self.binary is not None
        version = self.probe().version or "unknown"
        request = compile_probe_request(scenario["probe"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request))
            code, out, err, wall, cpu, rss = _run_measured(
                [str(self.binary), "--resource-request", str(path)]
            )
        stderr = err.decode(errors="replace").strip()
        if code == 2 and stderr.startswith("error: resource request rejected:"):
            receipt = _receipt(
                self.name,
                version,
                scenario,
                sha,
                status="completed",
                outcome="rejected",
                reason_code="request_rejected",
                detail=stderr.split(":", 2)[2].strip(),
                final_state={},
                trace=[],
            )
        elif code != 0:
            receipt = _receipt(
                self.name,
                version,
                scenario,
                sha,
                status="error",
                error=f"exit {code}: {stderr[:500]}",
            )
        else:
            try:
                receipt = self._receipt_from_output(
                    scenario, sha, version, request, out
                )
            except (ValueError, KeyError) as exc:
                receipt = _receipt(
                    self.name,
                    version,
                    scenario,
                    sha,
                    status="error",
                    error=f"malformed native result: {exc}",
                )
        return RawRun(receipt, wall, cpu, rss, None)

    def _receipt_from_output(
        self,
        scenario: dict[str, Any],
        sha: str,
        version: str,
        request: dict[str, Any],
        out: bytes,
    ) -> dict[str, Any]:
        doc = json.loads(out)
        if set(doc) != _NATIVE_RESULT_KEYS:
            raise ValueError(
                f"result keys differ: {sorted(set(doc) ^ _NATIVE_RESULT_KEYS)}"
            )
        if doc["schema_version"] != "resource-probe-result.v1":
            raise ValueError("unexpected result schema")
        if doc["deck_identity"] != request["deck_identity"]:
            raise ValueError("deck_identity was not echoed")
        samples = doc["cast_samples"]
        if len(samples) != doc["games"] or sum(samples) != doc["commander_cast_count"]:
            raise ValueError("cast_samples disagree with commander_cast_count")
        if doc["games"] != request["games"] or doc["turns"] != request["turns"]:
            raise ValueError("games/turns not echoed")
        return _receipt(
            self.name,
            version,
            scenario,
            sha,
            status="completed",
            outcome="legal",
            final_state={
                "games": doc["games"],
                "turns": doc["turns"],
                "commander_cast_count": doc["commander_cast_count"],
                "lands_mean": doc["lands_mean"],
                "commander_draws_mean": doc["commander_draws_mean"],
                "cast_samples_sha256": sha256_text(canonical_json(samples)),
                "simulation_input_sha256": doc["simulation_input_sha256"],
            },
            trace=[],
        )


def _git_root(path: Path) -> Path | None:
    for parent in path.resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return result.stdout.strip()


class ExternalCommandAdapter(EngineAdapter):
    """An engine reached through the ``rules-engine-adapter.v1`` bridge.

    The bridge is a command. ``<cmd> capabilities`` prints a
    ``rules-engine-adapter-capabilities.v1`` document. ``<cmd> run`` reads
    one scenario as JSON on stdin and prints exactly one
    ``rules-engine-receipt.v1`` document on stdout. The bridge must not
    report timings: the harness measures the child process, and
    ``validate_receipt`` rejects any extra field.
    """

    def __init__(
        self, name: str, command: list[str] | None, preflight: list[str]
    ) -> None:
        """Bind to a bridge command.

        Args:
            name: Engine name; it must match the receipt's ``engine``.
            command: The bridge argv prefix, or None if none is configured.
            preflight: Reasons the engine cannot run, found before the
                bridge is contacted (missing Java, missing install).
        """
        self.name = name
        self.command = command
        self.preflight = preflight
        self._availability: Availability | None = None

    def probe(self) -> Availability:
        """Ask the bridge for its capabilities, after preflight checks pass.

        Returns:
            Availability. ``reasons`` lists every missing piece.
        """
        if self._availability is not None:
            return self._availability
        reasons = list(self.preflight)
        if not self.command:
            reasons.append(
                f"no {ADAPTER_CONTRACT} bridge command configured for {self.name}"
            )
        if reasons:
            self._availability = Availability(False, None, reasons, [], {})
            return self._availability
        assert self.command is not None
        code, out, err, *_ = _run_measured([*self.command, "capabilities"], timeout=60)
        try:
            doc = json.loads(out)
            if (
                code != 0
                or doc.get("schema_version") != CAPABILITIES_SCHEMA
                or doc.get("engine") != self.name
                or not isinstance(doc.get("engine_version"), str)
                or not isinstance(doc.get("capabilities"), list)
            ):
                raise ValueError("bad capabilities document")
        except ValueError as exc:
            self._availability = Availability(
                False,
                None,
                [
                    f"bridge capabilities call failed: {exc}; exit {code}; "
                    f"stderr {err.decode(errors='replace')[:200]}"
                ],
                [],
                {},
            )
            return self._availability
        self._availability = Availability(
            True,
            doc["engine_version"],
            [],
            list(doc["capabilities"]),
            {"bridge": self.command},
        )
        return self._availability

    def run(self, scenario: dict[str, Any], sha: str) -> RawRun:
        """Send one scenario to the bridge and measure the child process.

        Args:
            scenario: The scenario object.
            sha: Its ``scenario_sha256``.

        Returns:
            The bridge's receipt, unvalidated, and the measurements. If the
            output is not JSON the receipt is an ``error``.
        """
        assert self.command is not None
        payload = canonical_json({"contract": ADAPTER_CONTRACT, "scenario": scenario})
        code, out, err, wall, cpu, rss = _run_measured(
            [*self.command, "run"], payload.encode()
        )
        try:
            receipt = json.loads(out)
            if not isinstance(receipt, dict):
                raise TypeError("receipt is not an object")
        except (TypeError, ValueError) as exc:
            receipt = _receipt(
                self.name,
                self.probe().version or "unknown",
                scenario,
                sha,
                status="error",
                error=f"bridge exit {code}; unparseable receipt: {exc}; "
                f"stderr {err.decode(errors='replace')[:200]}",
            )
        return RawRun(receipt, wall, cpu, rss, None)


def java_preflight(home_env: str, label: str, java_home_env: str) -> list[str]:
    """Find what is missing before a Java engine bridge can be used.

    The JDK is ``$<java_home_env>/bin/java``, else ``$JAVA_HOME/bin/java``,
    else ``java`` on PATH.

    Args:
        home_env: Environment variable naming the engine install directory.
        label: Human name of the engine.
        java_home_env: Engine-specific JDK home variable.

    Returns:
        Missing pieces, each stated exactly. Empty when all are present.
    """
    reasons = []
    jdk = os.environ.get(java_home_env) or os.environ.get("JAVA_HOME")
    java = str(Path(jdk) / "bin" / "java") if jdk else shutil.which("java")
    if java is None:
        reasons.append(
            f"no java executable ({java_home_env}, JAVA_HOME unset; none on PATH)"
        )
    elif not os.access(java, os.X_OK):
        reasons.append(f"{java} is not executable")
    else:
        result = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=20, check=False
        )
        if result.returncode != 0:
            first = (result.stderr or result.stdout).strip().splitlines()[:1]
            reasons.append(
                f"java -version exited {result.returncode}: {first[0] if first else ''}"
            )
    home = os.environ.get(home_env)
    if not home:
        reasons.append(f"{home_env} is not set ({label} install directory)")
    elif not Path(home).is_dir():
        reasons.append(f"{home_env}={home} is not a directory")
    return reasons


def build_adapters(
    names: Iterable[str], native_bin: Path | None
) -> list[EngineAdapter]:
    """Construct the requested adapters.

    Args:
        names: Engine names: ``reference``, ``native``, ``forge``, ``xmage``.
        native_bin: Path to ``cs``, if configured.

    Returns:
        Adapters in the order requested.

    Raises:
        FixtureError: For an unknown engine name.
    """
    adapters: list[EngineAdapter] = []
    for name in names:
        if name == "reference":
            adapters.append(ReferenceAdapter())
        elif name == "native":
            adapters.append(NativeProbeAdapter(native_bin))
        elif name in {"forge", "xmage"}:
            upper = name.upper()
            bridge = os.environ.get(f"SABER_{upper}_BRIDGE")
            adapters.append(
                ExternalCommandAdapter(
                    name,
                    shlex.split(bridge) if bridge else None,
                    java_preflight(
                        f"SABER_{upper}_HOME", name, f"SABER_{upper}_JAVA_HOME"
                    ),
                )
            )
        else:
            raise FixtureError(f"unknown engine {name}")
    return adapters


# --------------------------------------------------------------------------
# Receipt validation and comparison
# --------------------------------------------------------------------------

_COMMON = {
    "schema_version",
    "engine",
    "engine_version",
    "scenario_id",
    "scenario_sha256",
    "status",
}
_ALLOWED = {
    "completed": _COMMON
    | {"outcome", "illegal_at", "reason_code", "detail", "final_state", "trace"},
    "unsupported": _COMMON | {"missing_capabilities", "detail"},
    "error": _COMMON | {"error"},
}


def validate_receipt(
    receipt: Any, engine: str, scenario: dict[str, Any], sha: str
) -> dict[str, Any]:
    """Reject malformed, partial, mismatched or self-measured receipts.

    Args:
        receipt: The receipt from an adapter.
        engine: The adapter's name.
        scenario: The scenario it claims to answer.
        sha: The harness's ``scenario_sha256`` for that scenario.

    Returns:
        The receipt, unchanged, if valid.

    Raises:
        ReceiptError: With the first problem found.
    """
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt is not an object")
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise ReceiptError("wrong or missing schema_version")
    status = receipt.get("status")
    if status not in RECEIPT_STATUSES:
        raise ReceiptError(f"unknown status {status!r}")
    allowed = _ALLOWED[status]
    extra = set(receipt) - allowed
    if extra:
        raise ReceiptError(
            f"fields not allowed for status {status}: {sorted(extra)} "
            "(timings and resources are measured by the harness only)"
        )
    missing = _COMMON - set(receipt)
    if missing:
        raise ReceiptError(f"missing fields {sorted(missing)}")
    if receipt["engine"] != engine:
        raise ReceiptError(f"engine {receipt['engine']!r} is not {engine!r}")
    if not isinstance(receipt["engine_version"], str) or not receipt["engine_version"]:
        raise ReceiptError("engine_version must be a non-empty string")
    if receipt["scenario_id"] != scenario["id"]:
        raise ReceiptError("scenario_id mismatch")
    if receipt["scenario_sha256"] != sha:
        raise ReceiptError(
            "scenario_sha256 mismatch: receipt is for a different scenario"
        )
    if status == "unsupported":
        caps = receipt.get("missing_capabilities")
        if (
            not isinstance(caps, list)
            or not caps
            or not all(isinstance(c, str) and c for c in caps)
        ):
            raise ReceiptError("unsupported needs non-empty missing_capabilities")
        return receipt
    if status == "error":
        if not isinstance(receipt.get("error"), str) or not receipt["error"]:
            raise ReceiptError("error needs an error message")
        return receipt
    for key in ("outcome", "final_state", "trace"):
        if key not in receipt:
            raise ReceiptError(f"completed receipt lacks {key}")
    if receipt["outcome"] not in OUTCOMES:
        raise ReceiptError(f"unknown outcome {receipt['outcome']!r}")
    if not isinstance(receipt["final_state"], dict) or not isinstance(
        receipt["trace"], list
    ):
        raise ReceiptError("final_state must be an object and trace a list")
    if receipt["outcome"] == "illegal":
        if not isinstance(receipt.get("illegal_at"), int) or not receipt.get(
            "reason_code"
        ):
            raise ReceiptError("illegal outcome needs illegal_at and reason_code")
    elif "illegal_at" in receipt:
        raise ReceiptError("illegal_at is only allowed with an illegal outcome")
    if receipt["outcome"] == "rejected" and not receipt.get("detail"):
        raise ReceiptError("rejected outcome needs detail")
    if scenario["kind"] == "rules_state":
        if receipt["outcome"] == "rejected":
            raise ReceiptError("a rules scenario cannot be rejected as a request")
        if not receipt["final_state"] or not receipt["trace"]:
            raise ReceiptError("a completed rules scenario needs final_state and trace")
        for n, event in enumerate(receipt["trace"]):
            if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                raise ReceiptError(f"trace[{n}] is not an event object")
    elif receipt["outcome"] == "legal":
        state = receipt["final_state"]
        for key in ("games", "commander_cast_count", "lands_mean"):
            if not isinstance(state.get(key), (int, float)) or isinstance(
                state.get(key), bool
            ):
                raise ReceiptError(f"probe final_state lacks numeric {key}")
        if state["games"] != scenario["probe"]["games"]:
            raise ReceiptError("probe games differ from the scenario")
        if not 0 <= state["commander_cast_count"] <= state["games"]:
            raise ReceiptError("commander_cast_count outside [0, games]")
    return receipt


def _project(actual: Any, expected: Any) -> Any:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {k: _project(actual.get(k, "<absent>"), v) for k, v in expected.items()}
    return actual


def compare_state(
    expected: dict[str, Any], actual: dict[str, Any], path: str = ""
) -> list[str]:
    """Compare a partial expected state with an actual one.

    Only keys present in ``expected`` are checked. ``battlefield`` is compared
    as a multiset after projecting each actual permanent onto the expected
    permanents' keys, so it must list every permanent.

    Args:
        expected: Partial expected state.
        actual: Full normalized state from an engine.
        path: Prefix for messages.

    Returns:
        One message per difference.
    """
    diffs: list[str] = []
    for key, want in expected.items():
        where = f"{path}.{key}" if path else key
        if key not in actual:
            diffs.append(f"{where}: missing")
            continue
        have = actual[key]
        if key == "battlefield" and isinstance(want, list):
            keys = sorted({k for entry in want for k in entry})
            got = sorted(
                canonical_json({k: e.get(k, "<absent>") for k in keys}) for e in have
            )
            exp = sorted(
                canonical_json({k: e.get(k, "<absent>") for k in keys}) for e in want
            )
            if got != exp:
                diffs.append(f"{where}: expected {exp} got {got}")
        elif (
            isinstance(want, dict)
            and isinstance(have, dict)
            and key
            not in {
                "mana_pool",
                "counters",
                "commander_casts",
            }
        ):
            diffs.extend(compare_state(want, have, where))
        elif isinstance(want, float) or isinstance(have, float):
            if (
                not isinstance(have, (int, float))
                or abs(float(have) - float(want)) > 1e-9
            ):
                diffs.append(f"{where}: expected {want!r} got {have!r}")
        elif have != want:
            diffs.append(f"{where}: expected {want!r} got {have!r}")
    return diffs


def compare_trace(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> list[str]:
    """Compare ordered expected events with an engine trace.

    The actual trace is filtered to the event types that appear in
    ``expected`` and projected onto each expected event's keys. The two
    sequences must then be equal, so order, count and content all matter.

    Args:
        expected: Ordered expected events (partial objects).
        actual: The engine's full trace.

    Returns:
        Differences (empty when equal).
    """
    types = {e["event"] for e in expected}
    filtered = [e for e in actual if e.get("event") in types]
    if len(filtered) != len(expected):
        return [
            f"trace: expected {len(expected)} events of {sorted(types)}, got "
            f"{len(filtered)}: {[canonical_json(e) for e in filtered]}"
        ]
    diffs = []
    for n, (want, have) in enumerate(zip(expected, filtered, strict=True)):
        projected = _project(have, want)
        if projected != want:
            diffs.append(
                f"trace[{n}]: expected {canonical_json(want)} got {canonical_json(projected)}"
            )
    return diffs


def binomial_z(successes: int, n: int, p: float) -> float:
    """Return the z-score of ``successes`` out of ``n`` against probability ``p``.

    Args:
        successes: Observed count.
        n: Trials.
        p: Expected probability.

    Returns:
        The z-score; ``inf`` if ``p`` is 0 or 1 and the count disagrees.
    """
    sd = math.sqrt(n * p * (1 - p))
    if sd == 0:
        return 0.0 if successes == round(n * p) else math.inf
    return (successes - n * p) / sd


def evaluate(
    scenario: dict[str, Any], receipt: dict[str, Any]
) -> tuple[str, list[str], dict[str, Any]]:
    """Judge a validated receipt against the scenario's expectation.

    Args:
        scenario: The scenario.
        receipt: A receipt that passed ``validate_receipt``.

    Returns:
        ``(verdict, mismatches, statistics)``.
    """
    status = receipt["status"]
    if status == "unsupported":
        return "unsupported", [], {}
    if status == "error":
        return "error", [receipt["error"]], {}
    expected = scenario["expected"]
    mismatches: list[str] = []
    stats: dict[str, Any] = {}
    if receipt["outcome"] != expected["outcome"]:
        mismatches.append(
            f"outcome: expected {expected['outcome']} got {receipt['outcome']}"
        )
    if expected["outcome"] == "illegal":
        for key in ("illegal_at", "reason_code"):
            if receipt.get(key) != expected[key]:
                mismatches.append(
                    f"{key}: expected {expected[key]!r} got {receipt.get(key)!r}"
                )
    if expected["outcome"] == "rejected" and expected["detail_contains"] not in str(
        receipt.get("detail", "")
    ):
        mismatches.append(
            f"detail: expected to contain {expected['detail_contains']!r}, "
            f"got {receipt.get('detail')!r}"
        )
    mismatches.extend(
        compare_state(expected.get("final_state", {}), receipt["final_state"])
    )
    if expected.get("trace"):
        mismatches.extend(compare_trace(expected["trace"], receipt["trace"]))
    for absent in expected.get("trace_absent", []):
        for event in receipt["trace"]:
            if _project(event, absent) == absent:
                mismatches.append(
                    f"trace_absent: found forbidden event {canonical_json(event)}"
                )
    stat = expected.get("statistic")
    if stat and receipt["outcome"] == "legal":
        n = receipt["final_state"]["games"]
        k = receipt["final_state"]["commander_cast_count"]
        z = binomial_z(k, n, float(stat["probability"]))
        z_max = float(stat.get("z_max", DEFAULT_Z_MAX))
        stats = {
            "observed": k / n,
            "expected": stat["probability"],
            "z": z,
            "z_max": z_max,
        }
        if not abs(z) <= z_max:
            mismatches.append(
                f"statistic: {k}/{n} = {k / n:.4f} vs exact {stat['probability']:.4f}, "
                f"|z| = {abs(z):.2f} > {z_max}"
            )
    return ("match" if not mismatches else "divergent"), mismatches, stats


def cross_engine(
    scenario: dict[str, Any], receipts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Compare every pair of engines that completed the scenario.

    Rules scenarios compare outcome, reason, illegal index and every
    final-state key both engines report. Probe scenarios compare exact
    counts when the shuffle cannot matter; otherwise they use a
    two-proportion z-test.

    Args:
        scenario: The scenario.
        receipts: Engine name -> completed receipt.

    Returns:
        One record per engine pair.
    """
    names = sorted(receipts)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ra, rb = receipts[a], receipts[b]
            diffs = []
            for key in ("outcome", "illegal_at", "reason_code"):
                if ra.get(key) != rb.get(key):
                    diffs.append(f"{key}: {a}={ra.get(key)!r} {b}={rb.get(key)!r}")
            sa, sb = ra["final_state"], rb["final_state"]
            if scenario["kind"] == "resource_probe" and sa and sb:
                stat = scenario["expected"].get("statistic")
                if stat:
                    n1, n2 = sa["games"], sb["games"]
                    p1, p2 = (
                        sa["commander_cast_count"] / n1,
                        sb["commander_cast_count"] / n2,
                    )
                    pooled = (
                        sa["commander_cast_count"] + sb["commander_cast_count"]
                    ) / (n1 + n2)
                    sd = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
                    z = 0.0 if sd == 0 else (p1 - p2) / sd
                    if abs(z) > float(stat.get("z_max", DEFAULT_Z_MAX)):
                        diffs.append(
                            f"commander_cast rate: {a}={p1:.4f} {b}={p2:.4f} z={z:.2f}"
                        )
                else:
                    for key in ("commander_cast_count", "lands_mean"):
                        if sa.get(key) != sb.get(key):
                            diffs.append(f"{key}: {a}={sa.get(key)} {b}={sb.get(key)}")
            elif scenario["kind"] == "rules_state":
                for key in sorted(set(sa) & set(sb)):
                    if canonical_json(sa[key]) != canonical_json(sb[key]):
                        diffs.append(f"final_state.{key} differs")
            out.append(
                {
                    "scenario_id": scenario["id"],
                    "engines": [a, b],
                    "agree": not diffs,
                    "differences": diffs,
                }
            )
    return out


# --------------------------------------------------------------------------
# Benchmark driver
# --------------------------------------------------------------------------


def _measurement(runs: list[RawRun]) -> dict[str, Any]:
    walls = sorted(r.wall_ns / 1e6 for r in runs)
    cpus = sorted(r.cpu_ns / 1e6 for r in runs)
    rss = [r.peak_rss_bytes for r in runs if r.peak_rss_bytes is not None]
    heap = [
        r.python_heap_peak_bytes for r in runs if r.python_heap_peak_bytes is not None
    ]
    return {
        "measured_by": "harness",
        "repeats": len(runs),
        "wall_ms": {
            "min": round(walls[0], 4),
            "median": round(walls[len(walls) // 2], 4),
            "max": round(walls[-1], 4),
        },
        "cpu_ms_median": round(cpus[len(cpus) // 2], 4),
        "peak_rss_bytes_max": max(rss) if rss else None,
        "python_heap_peak_bytes_max": max(heap) if heap else None,
    }


def run_benchmark(
    fixtures: dict[str, Any],
    fixture_path: Path,
    adapters: list[EngineAdapter],
    repeat: int = 3,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Run every scenario on every available engine and build the report.

    Args:
        fixtures: Loaded fixture document.
        fixture_path: Where it came from (hashed into the report).
        adapters: Engines to try.
        repeat: Runs per (engine, scenario); repeats must be identical.
        only: Optional scenario ids to restrict to.

    Returns:
        A ``rules-engine-benchmark-report.v1`` document.
    """
    started = datetime.now(UTC).isoformat(timespec="seconds")
    engines = []
    availability: dict[str, Availability] = {}
    for adapter in adapters:
        avail = adapter.probe()
        availability[adapter.name] = avail
        engines.append({"name": adapter.name, **avail.as_dict()})
    results = []
    cross = []
    for scenario in fixtures["scenarios"]:
        if only and scenario["id"] not in only:
            continue
        sha = scenario_sha256(scenario)
        completed: dict[str, dict[str, Any]] = {}
        for adapter in adapters:
            avail = availability[adapter.name]
            record: dict[str, Any] = {
                "engine": adapter.name,
                "scenario_id": scenario["id"],
                "scenario_sha256": sha,
                "receipt": None,
                "measurement": None,
                "mismatches": [],
                "statistics": {},
            }
            if not avail.available:
                record.update(verdict="unavailable", reasons=avail.reasons)
                results.append(record)
                continue
            missing = sorted(
                set(scenario["required_capabilities"]) - set(avail.capabilities)
            )
            if missing:
                record["receipt"] = _receipt(
                    adapter.name,
                    avail.version or "unknown",
                    scenario,
                    sha,
                    status="unsupported",
                    missing_capabilities=missing,
                    detail=(
                        NativeProbeAdapter.NO_STATE_ENTRY
                        if isinstance(adapter, NativeProbeAdapter)
                        else "engine does not declare these capabilities"
                    ),
                )
                record.update(verdict="unsupported")
                results.append(record)
                continue
            runs = [adapter.run(scenario, sha) for _ in range(max(1, repeat))]
            try:
                receipts = [
                    validate_receipt(r.receipt, adapter.name, scenario, sha)
                    for r in runs
                ]
            except ReceiptError as exc:
                record.update(verdict="rejected_receipt", mismatches=[str(exc)])
                results.append(record)
                continue
            first = receipts[0]
            record["receipt"] = first
            if first["status"] == "completed":
                record["measurement"] = _measurement(runs)
            if any(canonical_json(r) != canonical_json(first) for r in receipts[1:]):
                record.update(
                    verdict="nondeterministic",
                    mismatches=["repeated runs produced different receipts"],
                )
                results.append(record)
                continue
            verdict, mismatches, stats = evaluate(scenario, first)
            record.update(verdict=verdict, mismatches=mismatches, statistics=stats)
            if first["status"] == "completed":
                completed[adapter.name] = first
            results.append(record)
        cross.extend(cross_engine(scenario, completed))
    report = {
        "schema_version": REPORT_SCHEMA,
        "started_at": started,
        "fixture_path": str(fixture_path),
        "fixture_sha256": file_sha256(fixture_path),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "repeat": repeat,
        },
        "engines": engines,
        "results": results,
        "cross_engine": cross,
    }
    report["summary"] = summarize(report)
    report["recommendation"] = recommend(report)
    return report


def summarize(report: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Count verdicts per engine.

    Args:
        report: A benchmark report.

    Returns:
        Engine name -> verdict -> count.
    """
    summary: dict[str, dict[str, int]] = {}
    for record in report["results"]:
        bucket = summary.setdefault(
            record["engine"], dict.fromkeys(sorted(VERDICTS), 0)
        )
        bucket[record["verdict"]] += 1
    return summary


def recommend(report: dict[str, Any]) -> dict[str, Any]:
    """Separate what was measured from what was not, per engine.

    No engine is recommended on unmeasured grounds. An engine counts as
    measured only if at least one scenario completed and was judged.

    Args:
        report: A benchmark report.

    Returns:
        Measured coverage per engine, the unmeasured engines with their
        reasons, and the scenarios no available engine supports.
    """
    summary = report["summary"]
    measured = {}
    unmeasured = {}
    for engine in report["engines"]:
        counts = summary.get(engine["name"], {})
        judged = counts.get("match", 0) + counts.get("divergent", 0)
        if engine["available"] and judged:
            measured[engine["name"]] = {
                "judged": judged,
                "match": counts.get("match", 0),
                "divergent": counts.get("divergent", 0),
                "unsupported": counts.get("unsupported", 0),
                "error_or_rejected": counts.get("error", 0)
                + counts.get("rejected_receipt", 0)
                + counts.get("nondeterministic", 0),
            }
        else:
            unmeasured[engine["name"]] = engine["reasons"] or [
                "available but completed no scenario"
            ]
    uncovered = sorted(
        {r["scenario_id"] for r in report["results"]}
        - {r["scenario_id"] for r in report["results"] if r["verdict"] == "match"}
    )
    return {
        "basis": "measured results only; unavailable engines are not ranked",
        "measured": measured,
        "unmeasured": unmeasured,
        "scenarios_without_a_matching_engine": uncovered,
    }


def validate_report(report: dict[str, Any], fixtures: dict[str, Any]) -> None:
    """Re-check a saved report against the fixtures it claims to have run.

    Every receipt is validated again and every verdict recomputed. A result
    claimed for an unavailable engine, a measurement not taken by the
    harness, a verdict that does not follow from its receipt, or a
    recommendation naming an unmeasured engine all fail.

    Args:
        report: A ``rules-engine-benchmark-report.v1`` document.
        fixtures: The loaded fixture document.

    Raises:
        ReportError: With the first problem found.
    """
    if report.get("schema_version") != REPORT_SCHEMA:
        raise ReportError("wrong report schema_version")
    scenarios = {s["id"]: s for s in fixtures["scenarios"]}
    engines = {e["name"]: e for e in report.get("engines", [])}
    for engine in engines.values():
        if engine["available"] and not engine.get("version"):
            raise ReportError(f"{engine['name']}: available engines need a version")
        if not engine["available"] and not engine.get("reasons"):
            raise ReportError(f"{engine['name']}: unavailable engines need reasons")
    for record in report.get("results", []):
        name, sid = record["engine"], record["scenario_id"]
        where = f"{name}/{sid}"
        if sid not in scenarios:
            raise ReportError(f"{where}: unknown scenario")
        scenario = scenarios[sid]
        sha = scenario_sha256(scenario)
        if record["scenario_sha256"] != sha:
            raise ReportError(f"{where}: scenario hash does not match the fixtures")
        if name not in engines:
            raise ReportError(f"{where}: engine not declared")
        verdict = record.get("verdict")
        if verdict not in VERDICTS:
            raise ReportError(f"{where}: unknown verdict {verdict!r}")
        receipt, measurement = record.get("receipt"), record.get("measurement")
        if not engines[name]["available"]:
            if (
                verdict != "unavailable"
                or receipt is not None
                or measurement is not None
            ):
                raise ReportError(f"{where}: an unavailable engine cannot have results")
            continue
        if verdict == "unavailable":
            raise ReportError(f"{where}: available engine marked unavailable")
        if verdict == "rejected_receipt":
            if measurement is not None:
                raise ReportError(f"{where}: rejected receipts carry no measurement")
            continue
        try:
            validate_receipt(receipt, name, scenario, sha)
        except ReceiptError as exc:
            raise ReportError(f"{where}: {exc}") from exc
        completed = receipt["status"] == "completed"
        if completed:
            if not measurement or measurement.get("measured_by") != "harness":
                raise ReportError(
                    f"{where}: completed result lacks a harness measurement"
                )
            wall = measurement.get("wall_ms", {})
            if not (
                0 < wall.get("min", 0) <= wall.get("median", 0) <= wall.get("max", 0)
            ):
                raise ReportError(f"{where}: wall_ms is not a positive ordered range")
            if int(measurement.get("repeats", 0)) < 1:
                raise ReportError(f"{where}: repeats must be >= 1")
        elif measurement is not None:
            raise ReportError(f"{where}: only completed results carry measurements")
        if verdict == "nondeterministic":
            continue
        recomputed, _, _ = evaluate(scenario, receipt)
        if recomputed != verdict:
            raise ReportError(
                f"{where}: verdict {verdict} but receipt implies {recomputed}"
            )
    if report.get("summary") != summarize(report):
        raise ReportError("summary does not match the results")
    measured = set(report.get("recommendation", {}).get("measured", {}))
    for name in measured:
        if not engines.get(name, {}).get("available"):
            raise ReportError(
                f"recommendation counts unavailable engine {name} as measured"
            )
    if report.get("recommendation") != recommend(report):
        raise ReportError("recommendation does not follow from the results")


def exit_code(report: dict[str, Any]) -> int:
    """Return 1 when any result diverged, errored or replayed differently.

    Args:
        report: A benchmark report.

    Returns:
        0 or 1.
    """
    bad = {"divergent", "error", "rejected_receipt", "nondeterministic"}
    return 1 if any(r["verdict"] in bad for r in report["results"]) else 0


def summary_lines(report: dict[str, Any]) -> list[str]:
    """Human-readable summary of a report.

    Args:
        report: A benchmark report.

    Returns:
        Lines to print.
    """
    lines = []
    for engine in report["engines"]:
        state = (
            "available " + str(engine["version"])
            if engine["available"]
            else ("UNAVAILABLE: " + "; ".join(engine["reasons"]))
        )
        lines.append(f"{engine['name']:<10} {state}")
        counts = report["summary"].get(engine["name"], {})
        lines.append(
            "           " + ", ".join(f"{k}={v}" for k, v in counts.items() if v)
        )
    for record in report["results"]:
        if record["verdict"] in {
            "divergent",
            "error",
            "rejected_receipt",
            "nondeterministic",
        }:
            lines.append(
                f"  {record['verdict'].upper()} {record['engine']}/{record['scenario_id']}"
            )
            lines.extend("    " + m for m in record["mismatches"][:5])
    for pair in report["cross_engine"]:
        if not pair["agree"]:
            lines.append(
                f"  CROSS-ENGINE DIVERGENCE {pair['scenario_id']} {pair['engines']}"
            )
            lines.extend("    " + d for d in pair["differences"][:5])
    return lines


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--engines", default="reference,native,forge,xmage")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--native-bin", type=Path, default=None)
    parser.add_argument("--scenario", action="append", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--validate-report", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        fixtures = load_fixtures(args.fixtures)
    except FixtureError as exc:
        print(f"invalid fixtures: {exc}", file=sys.stderr)
        return 2
    if args.validate_report:
        try:
            validate_report(json.loads(args.validate_report.read_text()), fixtures)
        except (ReportError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"invalid report: {exc}", file=sys.stderr)
            return 2
        print("report valid")
        return 0
    native_bin = args.native_bin or (
        Path(os.environ["CEDH_SIMULATOR_BIN"])
        if os.environ.get("CEDH_SIMULATOR_BIN")
        else None
    )
    adapters = build_adapters([e for e in args.engines.split(",") if e], native_bin)
    report = run_benchmark(
        fixtures,
        args.fixtures,
        adapters,
        args.repeat,
        set(args.scenario) if args.scenario else None,
    )
    validate_report(report, fixtures)
    for line in summary_lines(report):
        print(line)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"report written to {args.out}")
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
