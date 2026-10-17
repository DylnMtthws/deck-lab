"""Function-first recall over the shared legal catalog, with bounded evidence.

Reuses the generator-derived recall channels without constructing every engine
family or a pairwise graph. All scores are discovery heuristics, not rules proof.
"""

from __future__ import annotations

import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant_candidate_recall import _colors, _lexical, _semantic
from sabermetrics.assistant_strategy import draw_routes, strategy_capabilities
from sabermetrics.card_search import _catalog_faces, _identity_ok
from sabermetrics.deck_evidence import _library_entries


class FunctionalSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(
        default="", max_length=500, description="Research goal, not a card-name filter."
    )
    function: Literal[
        "draw", "selection", "mana", "interaction", "recursion", "any"
    ] = "draw"
    type_line: str = Field(default="", max_length=80)
    mana_max: float | None = Field(
        default=None,
        ge=0,
        le=30,
        description="Null means unspecified; zero means ONLY zero-mana cards.",
    )
    optionality: Literal["any", "preferred", "required"] = "any"
    include_existing: bool = False
    limit: int = Field(default=12, ge=1, le=20)
    excluded_names: list[str] = Field(default_factory=list, max_length=8)
    prefer_recurring: bool = False
    prefer_multifunctional: bool = False


_FUNCTIONS = {
    "selection": re.compile(r"\bscry\b|\bsurveil\b|look at the top", re.IGNORECASE),
    "mana": re.compile(r"\badd\b|search your library[^.]*\bland\b", re.IGNORECASE),
    "interaction": re.compile(
        r"counter target|destroy target|exile target", re.IGNORECASE
    ),
    "recursion": re.compile(r"from (?:your|a|the) graveyard", re.IGNORECASE),
}


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
        "draw_control_coverage": "partial_printed_clauses",
        "secondary_capabilities": sorted(strategy_capabilities(card))[:12],
        "source_oracle_text": card.get("oracle_text") or "",
        "scope": "Bounded printed-clause classification, not complete rules execution. Voluntary activation does not make drawing optional after resolution. Full Oracle text remains authoritative; modal/conditional restrictions may cross clauses.",
    }


def functional_search(
    path: str | Path, document: dict[str, Any], args: FunctionalSearchArgs
) -> dict[str, Any]:
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
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        for row in _catalog_faces(conn, str(Path(path).resolve())):
            if str(row.get("name") or "").casefold() in {
                n.casefold() for n in args.excluded_names
            }:
                continue
            if not _identity_ok(row, allowed):
                continue
            kind = str(row.get("type_line") or "")
            if args.type_line.casefold() not in kind.casefold():
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
            if args.function in _FUNCTIONS and not _FUNCTIONS[args.function].search(
                str(card.get("oracle_text") or "")
            ):
                continue
            controls = {r["draw_control"] for r in profile["draw_routes"]}
            controlled = bool(
                controls
                & {"voluntary_activation", "optional_trigger", "voluntary_spell"}
            )
            if args.optionality == "required" and (
                not controlled
                or "mandatory_trigger" in controls
                or "unclassified" in controls
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

    def rank(oid: str) -> tuple:
        profile = profiles[oid]
        routes = profile["draw_routes"]
        controls = {r["draw_control"] for r in routes}
        controllable = bool(
            controls & {"voluntary_activation", "optional_trigger", "voluntary_spell"}
        )
        death_draw = any("death_event_required" in r["prerequisites"] for r in routes)
        creature = "creature" in str(cards[oid].get("type_line") or "").casefold()
        engine_link = bool(death_draw and support and return_engine)
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
        recurring = any(r["kind"] in {"activation", "trigger"} for r in routes)
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
        return (
            -int(args.optionality == "preferred" and controllable),
            -int(args.prefer_recurring and recurring),
            -int(args.prefer_multifunctional and multifunctional),
            -int(engine_link and creature),
            -int(engine_link),
            -int(recurring),
            -int(oid in associations),
            -semantic_scores.get(oid, 0),
            -lexical.get(oid, 0),
            mana if isinstance(mana, (int, float)) else 100,
            str(cards[oid].get("name")),
            oid,
        )

    ordered = sorted(cards, key=rank)
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
        "limitations": [
            "Scores rank discovery relevance, not strategic quality or measured performance.",
            "Clause coverage is incomplete; empty matches do not establish absence from all Magic cards.",
        ],
    }
