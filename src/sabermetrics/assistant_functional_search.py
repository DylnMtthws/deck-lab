"""Function-first recall over the shared legal catalog, with bounded evidence.

Reuses the generator-derived recall channels without constructing every engine
family or a pairwise graph. All scores are discovery heuristics, not rules proof.
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from collections import deque
from contextlib import closing
from itertools import groupby
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sabermetrics.assistant_candidate_recall import _colors, _lexical, _semantic
from sabermetrics.assistant_strategy import draw_routes, strategy_capabilities
from sabermetrics.card_search import _catalog_faces, _identity_ok
from sabermetrics.deck_evidence import _library_entries

PrintedType = Literal[
    "creature",
    "artifact",
    "enchantment",
    "instant",
    "sorcery",
    "land",
    "planeswalker",
    "battle",
    "permanent",
]


class FunctionalTypeGroup(BaseModel):
    """One conjunction of canonical printed types within a bounded OR filter."""

    model_config = ConfigDict(extra="forbid", strict=True)
    required_types: list[PrintedType] = Field(min_length=1, max_length=3)
    excluded_types: list[PrintedType] = Field(default_factory=list, max_length=3)


class FunctionalSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mana_production_required: bool = False
    query: str = Field(
        default="", max_length=500, description="Research goal, not a card-name filter."
    )
    function: Literal[
        "draw",
        "selection",
        "mana",
        "interaction",
        "recursion",
        "sacrifice",
        "counters",
        "death_payoff",
        "protection",
        "any",
    ] = "draw"
    type_line: Literal[
        "",
        "any",
        "creature",
        "artifact",
        "enchantment",
        "instant",
        "sorcery",
        "land",
        "planeswalker",
        "battle",
        "permanent",
    ] = ""
    draw_event: Literal[
        "any",
        "death",
        "sacrifice",
        "combat",
        "opponent_action",
        "turn",
        "graveyard_leave",
    ] = Field(
        default="any",
        description="Preferred draw mechanism, not an exact Oracle substring. Choose opponent_action for generic opponent-spell/draw engines, death or sacrifice for recursion engines.",
    )
    mana_max: float | None = Field(
        default=None,
        ge=0,
        le=30,
        description="Null means unspecified; zero means ONLY zero-mana cards.",
    )
    optionality: Literal["any", "preferred", "required"] = Field(
        default="any",
        description="Draw-control preference, applied only when function is draw. Ignored for other functions; it never requires a sacrifice outlet, mana source or protection card to draw cards.",
    )
    include_existing: bool = False
    limit: int = Field(default=12, ge=1, le=20)
    excluded_names: list[str] = Field(default_factory=list, max_length=8)
    prefer_recurring: bool = False
    prefer_low_mana: bool = False
    prefer_multifunctional: bool = False
    allowed_type_groups: list[FunctionalTypeGroup] = Field(
        default_factory=list, max_length=4
    )

    @field_validator("type_line", mode="before")
    @classmethod
    def normalize_type(cls, value):
        return (
            ""
            if isinstance(value, str) and value.casefold().strip() == "any"
            else value.casefold().strip() if isinstance(value, str) else value
        )


_FUNCTIONS = {
    "selection": re.compile(r"\bscry\b|\bsurveil\b|look at the top", re.IGNORECASE),
    "mana": re.compile(r"\badd\b|search your library[^.]*\bland\b", re.IGNORECASE),
    "interaction": re.compile(
        r"counter target|destroy target|exile target", re.IGNORECASE
    ),
    "recursion": re.compile(r"from (?:your|a|the) graveyard", re.IGNORECASE),
}

_ROLE_FUNCTIONS = {"sacrifice", "counters", "death_payoff", "protection"}
_CHEAP_FUNCTION_TERMS = {
    "draw": ("draw",),
    "sacrifice": ("sacrifice",),
    "counters": ("+1/+1",),
    # Preserve the older 'put into a graveyard from the battlefield' wording
    # accepted by the exact role gate, as well as die/dies trigger wording.
    "death_payoff": ("die", "graveyard"),
    "protection": (
        "hexproof",
        "shroud",
        "indestructible",
        "protection",
        "phas",
        "counter",
    ),
}
_TRIGGER_START = re.compile(r"^(?:When(?:ever)?|At the beginning)\b", re.IGNORECASE)
_COUNTER_QUANTITY = (
    r"(?:(?:an?|another|one|two|three|four|five|six|seven|eight|nine|ten|x|"
    r"that many|twice that many|a number of|the same number of|one or more|[0-9]+)\s+)?"
    r"(?:additional\s+)?"
)
_COUNTER_PLACEMENT = re.compile(
    rf"\b(?:put|distribute)\s+{_COUNTER_QUANTITY}\+1/\+1 counters?\b"
    rf"|\benters?\s+(?:the battlefield\s+)?(?:tapped\s+)?with\s+{_COUNTER_QUANTITY}\+1/\+1 counters?\b",
    re.IGNORECASE,
)
_PROTECTIVE_KEYWORD = r"(?:hexproof|shroud|indestructible|phasing|protection from)"
_GRANTED_PROTECTION = re.compile(
    rf"\b(?:has|have|gains?|are|is)\b[^.:\n]{{0,100}}\b{_PROTECTIVE_KEYWORD}\b",
    re.IGNORECASE,
)
_OUTLET_COST = re.compile(
    r"\bsacrifice\s+(?:an?|another|one|two|three|four|five|six|x|[0-9]+|"
    r"any number of|one or more|all)\s+(?:[\w+-]+\s+){0,3}?"
    r"(?:creatures?|artifacts?|permanents?|enchantments?|lands?|planeswalkers?|"
    r"battles?|tokens?|foods?|treasures?|clues?|blood)\b",
    re.IGNORECASE,
)


def _role_routes(card: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Collect exact printed mechanisms, preserving restrictions rather than proving rules.

    These bounded clause gates exclude incidental mentions and trigger antecedents.
    They do not promise complete Oracle parsing, unlimited activations, beneficial
    strategic impact, or applicability to a particular board state.
    """
    result: dict[str, list[dict[str, Any]]] = {
        key: [] for key in sorted(_ROLE_FUNCTIONS)
    }
    raw = str(card.get("oracle_text") or "")
    for match in re.finditer(r"[^.\n]+(?:\.|$)", raw, re.MULTILINE):
        quote = match[0].strip()
        text = quote.lstrip("• ")
        restriction = re.match(
            r"\s*(?:Activate only\b|This ability triggers only\b)[^.\n]*(?:\.|$)",
            raw[match.end() :],
            re.IGNORECASE,
        )
        restrictions = []
        if restriction:
            quote = raw[match.start() : match.end() + restriction.end()].strip()
            restrictions.append(restriction[0].strip())
        trigger = bool(_TRIGGER_START.match(text))
        activation_cost = ""
        event = ""
        if trigger:
            event, separator, effect = text.partition(",")
            if not separator:
                effect = ""
        elif ":" in text:
            activation_cost, effect = text.split(":", 1)
        else:
            effect = text
        conditional = (
            trigger
            or bool(activation_cost)
            or bool(restrictions)
            or bool(
                re.search(
                    r"\b(?:if|unless|may|only|until|from)\b|\bas long as\b",
                    text,
                    re.IGNORECASE,
                )
            )
        )

        def add(function: str, mechanism: str) -> None:
            result[function].append(
                {
                    "source_quote": quote,
                    "mechanism": mechanism,
                    "kind": (
                        "trigger"
                        if trigger
                        else "activation" if activation_cost else "printed_effect"
                    ),
                    "activation_cost": activation_cost.strip(),
                    "conditional": conditional,
                    "restrictions": restrictions,
                    "scope": "Printed mechanism only; preserve full Oracle conditions, targets, costs and timing. Repeatability and strategic benefit are not certified.",
                }
            )

        if activation_cost and _OUTLET_COST.search(activation_cost):
            add("sacrifice", "activated_sacrifice_cost")
        if _COUNTER_PLACEMENT.search(effect) and not re.search(
            r"\b(?:can't|cannot|don't|doesn't|may not)\b", effect, re.IGNORECASE
        ):
            add("counters", "plus_one_counter_placement")
        # Enters-with counters is a replacement/static printed clause, not a
        # trigger antecedent. Trigger antecedents mentioning counters stay out.
        death_event = bool(re.search(r"\bdies?\b", event, re.IGNORECASE)) or bool(
            re.search(
                r"\bput into\b[^.]{0,80}\bgraveyard\b[^.]{0,80}\bfrom (?:the )?battlefield\b",
                event,
                re.IGNORECASE,
            )
        )
        creature_event = bool(re.search(r"\bcreatures?\b", event, re.IGNORECASE))
        name = str(card.get("name") or "")
        self_event = bool(name and name.casefold() in event.casefold()) or bool(
            re.search(r"\bthis (?:creature|permanent)\b", event, re.IGNORECASE)
        )
        if (
            trigger
            and death_event
            and (creature_event or self_event)
            and effect.strip()
        ):
            add(
                "death_payoff",
                "self_death_value" if self_event else "creature_death_trigger",
            )
        # Negative references such as 'lose hexproof' or 'can't gain hexproof'
        # are not protective effects. Keep this conservative within each clause.
        detection_effect = re.sub(r"\([^)]*(?:\)|$)", "", effect)
        negative = re.search(
            r"\b(?:lose|loses|losing|without|can't|cannot|don't|doesn't|not|no|neither)\b",
            detection_effect,
            re.IGNORECASE,
        )
        keyword = re.search(
            rf"(?:^|,\s*){_PROTECTIVE_KEYWORD}\b",
            detection_effect.strip(),
            re.IGNORECASE,
        )
        granted = _GRANTED_PROTECTION.search(detection_effect)
        if not negative and (keyword or granted):
            add("protection", "printed_or_granted_protection")
        if (
            re.search(r"\b(?:phase|phases) out\b", detection_effect, re.IGNORECASE)
            and not negative
        ):
            add("protection", "phase_out_effect")
        counterspell = re.search(
            r"\bcounter target\b[^.\n]{0,100}\bspell\b",
            detection_effect,
            re.IGNORECASE,
        )
        if counterspell and not re.search(
            r"\b(?:can't|cannot|don't|doesn't|do not)\s*$",
            detection_effect[: counterspell.start()],
            re.IGNORECASE,
        ):
            add("protection", "targeted_spell_counter")
    return result


def functional_profile(card: dict[str, Any]) -> dict[str, Any]:
    routes = []
    raw = str(card.get("oracle_text") or "")
    optional_compounds = [
        m[0]
        for m in re.finditer(
            r"(?:When(?:ever)?|At the beginning)[^.\n]*\bmay\b[^.\n]*\.\s*If you do,[^.\n]*\bdraw\b[^.\n]*",
            raw,
            re.IGNORECASE,
        )
    ]
    for route in draw_routes(card):
        if route.kind == "unclassified":
            compound = next(
                (text for text in optional_compounds if route.source_quote in text),
                None,
            )
            if compound:
                route = route.model_copy(
                    update={
                        "kind": "trigger",
                        "source_quote": compound,
                        "prerequisites": [
                            *route.prerequisites,
                            "optional_cost_required",
                            "trigger_event_required",
                        ],
                    }
                )
        text = route.source_quote.casefold()
        control = (
            "voluntary_activation"
            if route.kind == "activation"
            else (
                "optional_trigger"
                if route.kind == "trigger" and re.search(r"\bmay\b", text)
                else (
                    "mandatory_trigger"
                    if route.kind == "trigger"
                    else "voluntary_spell" if route.kind == "spell" else "unclassified"
                )
            )
        )
        routes.append({**route.model_dump(), "draw_control": control})
    return {
        "draw_routes": routes,
        "function_routes": _role_routes(card),
        "draw_control_coverage": "partial_printed_clauses",
        "secondary_capabilities": sorted(strategy_capabilities(card))[:12],
        "source_oracle_text": card.get("oracle_text") or "",
        "scope": "Bounded printed-clause classification, not complete rules execution. Voluntary activation does not make drawing optional after resolution. Full Oracle text remains authoritative; modal/conditional restrictions may cross clauses.",
    }


def functional_search(
    path: str | Path, document: dict[str, Any], args: FunctionalSearchArgs
) -> dict[str, Any]:
    started = time.monotonic()
    from sabermetrics.assistant_suitability import (
        candidate_suitability,
        deck_support_index,
    )

    support_index = deck_support_index(document)
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    identities = [_colors(c.get("color_identity")) for c in commanders]
    if not commanders or any(value is None for value in identities):
        return {
            "cards": [],
            "status": "unknown_commander_identity",
            "channel_status": {"catalog": "unavailable"},
        }
    allowed = set().union(*(value for value in identities if value is not None))
    library = _library_entries(document)
    existing = {str(c.get("oracle_id") or c.get("card_id")) for c in library}
    existing_ids = {str(c.get("card_id")) for c in library}
    support = [c for c in library if "sacrifice_outlet" in strategy_capabilities(c)]
    commander_text = " ".join(
        str(c.get("oracle_text") or "") for c in commanders
    ).casefold()
    return_engine = (
        "dies" in commander_text
        and "return" in commander_text
        and "battlefield" in commander_text
    )
    cards, profiles = {}, {}
    excluded_names = {name.casefold() for name in args.excluded_names}
    cheap_terms = _CHEAP_FUNCTION_TERMS.get(args.function)
    function_pattern = _FUNCTIONS.get(args.function)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        for row in _catalog_faces(conn, str(Path(path).resolve())):
            if str(row.get("name") or "").casefold() in excluded_names:
                continue
            if args.mana_production_required:
                from sabermetrics.assistant_search_goal import produces_mana

                if not produces_mana(row):
                    continue
            oracle_text = str(row.get("oracle_text") or "")
            folded_text = oracle_text.casefold()
            # Necessary lexical cues only: this avoids expensive route/profile
            # construction for irrelevant rows without narrowing semantic recall.
            # Full mechanical relevance checks below remain authoritative.
            if cheap_terms is not None and not any(
                term in folded_text for term in cheap_terms
            ):
                continue
            if function_pattern is not None and not function_pattern.search(
                oracle_text
            ):
                continue
            if not _identity_ok(row, allowed):
                continue
            kind = str(row.get("type_line") or "")
            printed_types = set(re.findall(r"[a-z]+", kind.split("—", 1)[0].casefold()))

            def has_type(card_type: str) -> bool:
                return (
                    bool(
                        printed_types
                        & {
                            "creature",
                            "artifact",
                            "enchantment",
                            "land",
                            "planeswalker",
                            "battle",
                        }
                    )
                    if card_type == "permanent"
                    else card_type in printed_types
                )

            if args.allowed_type_groups and not any(
                all(has_type(t) for t in group.required_types)
                and not any(has_type(t) for t in group.excluded_types)
                for group in args.allowed_type_groups
            ):
                continue
            if args.type_line == "permanent" and any(
                t in kind.casefold() for t in ("instant", "sorcery")
            ):
                continue
            if (
                args.type_line not in {"", "permanent"}
                and args.type_line not in kind.casefold()
            ):
                continue
            value = row.get("mana_value")
            if args.mana_max is not None and (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value > args.mana_max
            ):
                continue
            oid = str(row.get("oracle_id") or row["id"])
            present = oid in existing or str(row["id"]) in existing_ids
            if present and not args.include_existing:
                continue
            card = {k: v for k, v in row.items() if not k.startswith("_")}
            profile = functional_profile(card)
            if args.function == "draw" and not profile["draw_routes"]:
                continue
            if (
                args.function in _ROLE_FUNCTIONS
                and not profile["function_routes"][args.function]
            ):
                continue
            controls = {r["draw_control"] for r in profile["draw_routes"]}
            controlled = bool(
                controls
                & {"voluntary_activation", "optional_trigger", "voluntary_spell"}
            )
            if (
                args.function == "draw"
                and args.optionality == "required"
                and (
                    not controlled
                    or "mandatory_trigger" in controls
                    or "unclassified" in controls
                )
            ):
                continue
            card["already_in_library"] = present
            card["functional_profile"] = profile
            cards[oid], profiles[oid] = card, profile
        lexical = _lexical(list(cards.values()), args.query)
        semantic, semantic_status, rejected = _semantic(
            conn, cards, args.query or args.function
        )
    semantic_scores = dict(semantic)
    from sabermetrics.assistant_retrieval_context import recall_view_scores

    view_scores, view_receipt = recall_view_scores(
        cards, args.query or args.function, db_path=path
    )
    for oid, score in view_scores.items():
        semantic_scores[oid] = max(score, semantic_scores.get(oid, 0))
    prefer_cheap_sacrifice = args.function == "sacrifice" and bool(
        re.search(
            r"\b(?:free|cheap|low[- ]mana|no[- ]mana|zero[- ]mana|mana[- ]free)\b",
            args.query,
            re.IGNORECASE,
        )
    )
    associations: dict[str, dict[str, Any]] = {}
    association_status = "unavailable_no_draw_anchor"
    anchors = [c for c in library if c.get("oracle_id") and draw_routes(c)][:2]
    if anchors:
        from sabermetrics.assistant_associations import research_card_associations

        for anchor in anchors:
            result = research_card_associations(
                Path(path), document, anchor["oracle_id"], limit=8
            )
            association_status = str(result["status"])
            for row in result.get("associations", []):
                if str(row.get("oracle_id")) in cards:
                    associations[str(row["oracle_id"])] = row

    from sabermetrics.assistant_strategic_obligations import strategic_rank_key

    suitability = {
        oid: candidate_suitability(
            card,
            support_index,
            (args.query or args.function)
            + (" cheap" if args.prefer_low_mana else "")
            + (" repeatable" if args.prefer_recurring else ""),
            include_packet=False,
        )
        for oid, card in cards.items()
    }

    def rank(oid: str) -> tuple:
        profile = profiles[oid]
        routes = profile["draw_routes"]
        controls = {r["draw_control"] for r in routes}
        controllable = bool(
            controls & {"voluntary_activation", "optional_trigger", "voluntary_spell"}
        )
        death_draw = any("death_event_required" in r["prerequisites"] for r in routes)
        events = set()
        for route in routes:
            for prerequisite, event in (
                ("death_event_required", "death"),
                ("sacrifice_required", "sacrifice"),
                ("combat_required", "combat"),
                ("opponent_action_or_choice", "opponent_action"),
            ):
                if prerequisite in route["prerequisites"]:
                    events.add(event)
            text = route["source_quote"].casefold()
            if re.search(r"upkeep|at the beginning|end step", text):
                events.add("turn")
            if "leave your graveyard" in text:
                events.add("graveyard_leave")
        profile["draw_events"] = sorted(events)
        creature = "creature" in str(cards[oid].get("type_line") or "").casefold()
        engine_link = bool(
            args.function == "draw" and death_draw and support and return_engine
        )
        cards[oid]["functional_profile"]["deck_support"] = {
            "relationship": (
                "death_draw_with_recursion_and_sacrifice"
                if engine_link
                else "not_established"
            ),
            "status": "inferred_ingredients",
            "commander_sources": (
                [
                    {"name": c.get("name"), "oracle_text": c.get("oracle_text")}
                    for c in commanders
                ]
                if engine_link
                else []
            ),
            "sacrifice_sources": (
                [
                    {"name": c.get("name"), "oracle_text": c.get("oracle_text")}
                    for c in support[:3]
                ]
                if engine_link
                else []
            ),
            "conditions": (
                [
                    "Commander return conditions must be met; repeatability and timing are not certified."
                ]
                if engine_link
                else []
            ),
        }
        channels = ["function:" + args.function]
        if lexical.get(oid):
            channels.append("lexical")
        if oid in semantic_scores:
            channels.append("semantic")
        if oid in associations:
            channels.append("association")
        if engine_link:
            channels.append("engine_ingredients")
        cards[oid]["recall_channels"] = channels
        if oid in associations:
            cards[oid]["association_evidence"] = associations[oid]
        role_routes = profile["function_routes"].get(args.function, [])
        recurring_routes = routes if args.function == "draw" else role_routes
        recurring = any(
            r["kind"] in {"activation", "trigger"} for r in recurring_routes
        )
        secondary = profile["secondary_capabilities"]
        multifunctional = bool(
            set(secondary)
            & {
                "sacrifice_outlet",
                "creature_token_producer",
                "treasure_producer",
                "counter_producer",
                "graveyard_access",
            }
        )
        mana = cards[oid].get("mana_value")
        no_mana_activation = any(
            not re.search(
                r"\{(?:[1-9][0-9]*|[WUBRGCSXP](?:/[WUBRGPC])?|2/[WUBRG])\}|\bmana\b",
                route["activation_cost"],
                re.IGNORECASE,
            )
            for route in role_routes
            if route["mechanism"] == "activated_sacrifice_cost"
        )
        cheap_cast = isinstance(mana, (int, float)) and mana <= 2
        relevance = (-semantic_scores.get(oid, 0), -lexical.get(oid, 0))
        return (
            *(relevance if args.function == "any" else (0, 0)),
            -int(prefer_cheap_sacrifice and no_mana_activation),
            -int(prefer_cheap_sacrifice and cheap_cast),
            mana if prefer_cheap_sacrifice and isinstance(mana, (int, float)) else 0,
            -int(
                args.function == "draw"
                and args.draw_event != "any"
                and args.draw_event in events
            ),
            -int(
                args.function == "draw"
                and args.optionality == "preferred"
                and controllable
            ),
            *strategic_rank_key(suitability[oid]["strategic_obligations"]),
            # Legacy scores only break ties; they never certify quality.
            -suitability[oid]["strategic_judgment"]["priority"],
            -int(args.prefer_recurring and recurring),
            -int(args.prefer_multifunctional and multifunctional),
            -int(engine_link and creature),
            -int(engine_link),
            -int(recurring),
            -int(args.function == "draw" and oid in associations),
            -semantic_scores.get(oid, 0),
            -lexical.get(oid, 0),
            mana if isinstance(mana, (int, float)) else 100,
            str(cards[oid].get("name")),
            oid,
        )

    ranked = {oid: rank(oid) for oid in cards}
    ordered = sorted(cards, key=ranked.__getitem__)
    # Equal substantive priorities can vary mechanisms before alphabetical ties.
    # No worse-ranked card displaces a better-supported candidate for a quota.
    diversified: list[str] = []
    for _, peers in groupby(ordered, key=lambda oid: ranked[oid][:-2]):
        mechanisms: dict[tuple[str, ...], deque[str]] = {}
        for oid in peers:
            signature = tuple(suitability[oid]["strategic_obligations"]["mechanism"])
            mechanisms.setdefault(signature, deque()).append(oid)
        active = deque(mechanisms.values())
        while active:
            queue = active.popleft()
            diversified.append(queue.popleft())
            if queue:
                active.append(queue)
    ordered = diversified
    # Attach rich evidence only to the shortlist; the full pool was evaluated
    # against one shared deck scan before applying the result limit.
    for oid in ordered[: args.limit]:
        from sabermetrics.assistant_evidence_packet import (
            build_recommendation_packet,
            compact_recommendation_packet,
        )

        cards[oid]["suitability"] = suitability[oid]
        packet = build_recommendation_packet(
            document, cards[oid], focus=args.query or args.function
        )
        cards[oid]["suitability"]["recommendation_packet"] = (
            compact_recommendation_packet(packet)
        )
        from sabermetrics.assistant_strategic_obligations import (
            build_strategic_obligations,
            compact_strategic_obligations,
        )

        cards[oid]["suitability"]["strategic_obligations"] = (
            compact_strategic_obligations(
                build_strategic_obligations(
                    cards[oid],
                    (args.query or args.function)
                    + (" repeatable" if args.prefer_recurring else ""),
                    suitability=suitability[oid],
                    packet=packet,
                    deck_entries=support_index.get("entries"),
                )
            )
        )
    from sabermetrics.assistant_compatibility import compare_candidates

    compare_candidates([cards[oid] for oid in ordered[: args.limit]])
    from sabermetrics.assistant_jev import shadow_candidates

    jev_trial = shadow_candidates(
        args.query or args.function, [cards[oid] for oid in ordered[: args.limit]]
    )
    return {
        "status": "available",
        "cards": [cards[oid] for oid in ordered[: args.limit]],
        "source": "Shared Commander-legal local catalog; function-first union recall",
        "revision": document.get("revision"),
        "matched_cards": len(cards),
        "channel_status": {
            "mechanical": "available",
            "lexical": "available",
            "semantic": semantic_status,
            "association": association_status,
        },
        "stale_vectors_rejected": rejected,
        "view_recall": view_receipt,
        "ranking_version": "assistant-suitability.v2",
        "jev_trial": jev_trial,
        "elapsed_seconds": round(time.monotonic() - started, 4),
        "limitations": [
            "Scores rank discovery relevance, not strategic quality or measured performance.",
            "Clause coverage is incomplete; empty matches do not establish absence from all Magic cards.",
        ],
    }
