"""Typed, owner-scoped tools for the conversational Build assistant.

The model can inspect and preview. Experiments create isolated snapshots;
there is intentionally no tool for applying or adopting changes to a deck.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sabermetrics import db
from sabermetrics.card_search import search_cards
from sabermetrics.deck_documents import DeckDocumentRepo, RevisionConflict
from sabermetrics.deck_evidence import (
    DeckEvidenceService,
    _card_roles,
    _library_entries,
)
from sabermetrics.deck_simulation import simulation_view


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyArgs(StrictModel):
    pass


class SearchArgs(StrictModel):
    query: str = Field(default="", max_length=160)
    oracle_text: str = Field(default="", max_length=160)
    type_line: str = Field(default="", max_length=80)
    mana_max: float | None = Field(default=None, ge=0, le=30)
    limit: int = Field(default=8, ge=1, le=12)


class InspectArgs(StrictModel):
    query: str = Field(default="", max_length=160)
    offset: int = Field(default=0, ge=0, le=128)
    limit: int = Field(default=12, ge=1, le=12)


class CardArgs(StrictModel):
    card_id: str = Field(
        min_length=1,
        max_length=128,
        description="Canonical card ID, Oracle ID, or exact card name. Exact names resolve only when they identify one Oracle card; never guess an ID.",
    )


class EvidenceArgs(StrictModel):
    oracle_id: str = Field(default="", max_length=128)
    window_days: Literal[0, 7, 30, 90, 365] = 30


class StrategyArgs(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=4, ge=1, le=4)


class RecommendArgs(StrictModel):
    focus: str = Field(default="", max_length=300)


CardName = Annotated[str, Field(min_length=1, max_length=200)]


class HypotheticalCombinationState(StrictModel):
    battlefield: list[CardName] = Field(
        max_length=100,
        description='Exact canonical card names from the current deck, without prose or statuses. Example: ["Vivi Ornitier", "Curiosity"]. Never write "Curiosity attached to Vivi Ornitier" here; encode attachment separately.',
    )
    hand: list[CardName] = Field(
        max_length=100,
        description='Exact current-deck card names only, e.g. ["Opt"]. No descriptions such as "Opt in hand". Unknown drawn card identities belong only in hand_count, never invented names.',
    )
    graveyard: list[CardName] = Field(
        default_factory=list,
        max_length=100,
        description='Exact current-deck card names only; no prose statuses. Example: ["Opt"].',
    )
    attachments: dict[CardName, CardName] = Field(
        default_factory=dict,
        max_length=20,
        description='Attachment relationships using exact card names as keys and values. Example: {"Curiosity": "Vivi Ornitier"}; both names must also appear separately in battlefield.',
    )
    paired_with: dict[CardName, CardName] = Field(
        default_factory=dict,
        max_length=20,
        description='Soulbond relationships using exact card names. Example: {"Tandem Lookout": "Vivi Ornitier"}; both names must also appear separately in battlefield. Do not describe pairing inside zone names.',
    )
    mana: dict[
        Literal["W", "U", "B", "R", "G", "C"], Annotated[int, Field(ge=0, le=100)]
    ] = Field(max_length=6)
    library_count: int = Field(ge=0, le=100)
    hand_count: int = Field(ge=0, le=100)
    opponent_count: int = Field(ge=1, le=6)
    priority: bool
    stack_empty: bool
    no_interference: bool
    no_other_effects: bool
    own_turn: bool = False
    main_phase: bool = False
    beginning_of_upkeep: bool = False
    choose_optional_draw: bool = False
    choose_discard: bool = False
    decline_ring_bearer_draw: bool = False
    engine_counters: int = Field(default=0, ge=0, le=100)
    damage_each_opponent: int = Field(default=0, ge=0, le=100)


class CombinationReplayArgs(StrictModel):
    combination_index: int = Field(ge=0, le=11)
    hypothetical_state: HypotheticalCombinationState


class SemanticReview(StrictModel):
    alternative_index: int = Field(ge=0, le=2)
    theme_fit: Literal["fit", "unknown", "conflict"]
    power_fit: Literal["fit", "unknown", "conflict"]
    reasoning: str = Field(min_length=20, max_length=600)


class RecommendedExperimentArgs(StrictModel):
    recommendation_id: str = Field(min_length=1, max_length=128)
    semantic_reviews: list[SemanticReview] = Field(default_factory=list, max_length=3)


class Replacement(StrictModel):
    outgoing_entry_id: str = Field(min_length=1, max_length=128)
    incoming_card_id: str = Field(min_length=1, max_length=128)


class Proposal(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    replacements: list[Replacement] = Field(min_length=1, max_length=1)


class ProposalArgs(StrictModel):
    proposal: Proposal


class ExperimentArgs(StrictModel):
    alternatives: list[Proposal] = Field(min_length=1, max_length=3)
    semantic_reviews: list[SemanticReview] = Field(default_factory=list, max_length=3)


class SearchCall(StrictModel):
    name: Literal["search_cards"]
    arguments: SearchArgs


class InspectCall(StrictModel):
    name: Literal["inspect_deck"]
    arguments: InspectArgs


class CardCall(StrictModel):
    name: Literal["card_details"]
    arguments: CardArgs


class EvidenceCall(StrictModel):
    name: Literal["research_evidence"]
    arguments: EvidenceArgs


class CoverageCall(StrictModel):
    name: Literal["goldfish_card_coverage"]
    arguments: InspectArgs


class LatestCall(StrictModel):
    name: Literal["latest_goldfish"]
    arguments: EmptyArgs


class RunCall(StrictModel):
    name: Literal["run_goldfish"]
    arguments: EmptyArgs


class IntelligenceCall(StrictModel):
    name: Literal["generator_intelligence"]
    arguments: EmptyArgs


class StrategyCall(StrictModel):
    name: Literal["retrieve_strategy_evidence"]
    arguments: StrategyArgs


class RequirementsCall(StrictModel):
    name: Literal["assess_deck_requirements"]
    arguments: EmptyArgs


class CombinationsCall(StrictModel):
    name: Literal["analyze_deck_combinations"]
    arguments: EmptyArgs


class KnownCombinationsCall(StrictModel):
    name: Literal["research_known_combinations"]
    arguments: EmptyArgs


class ReplayCombinationCall(StrictModel):
    name: Literal["replay_deck_combination"]
    arguments: CombinationReplayArgs


class MemoryCall(StrictModel):
    name: Literal["research_memory"]
    arguments: EmptyArgs


class RecommendCall(StrictModel):
    name: Literal["find_replacements"]
    arguments: RecommendArgs


class PrepareCall(StrictModel):
    name: Literal["prepare_improvement"]
    arguments: RecommendArgs


class RecommendedExperimentCall(StrictModel):
    name: Literal["test_recommended_alternatives"]
    arguments: RecommendedExperimentArgs


class ProposeCall(StrictModel):
    name: Literal["propose_replacements"]
    arguments: ProposalArgs


class ExperimentCall(StrictModel):
    name: Literal["start_experiment"]
    arguments: ExperimentArgs


ToolCall = Annotated[
    SearchCall
    | InspectCall
    | CardCall
    | EvidenceCall
    | LatestCall
    | CoverageCall
    | RunCall
    | IntelligenceCall
    | StrategyCall
    | RequirementsCall
    | MemoryCall
    | CombinationsCall
    | KnownCombinationsCall
    | ReplayCombinationCall
    | RecommendCall
    | PrepareCall
    | RecommendedExperimentCall
    | ProposeCall
    | ExperimentCall,
    Field(discriminator="name"),
]
TOOL_ADAPTER: TypeAdapter[Any] = TypeAdapter(ToolCall)


class ToolStopped(Exception):
    """A mode change, cancellation or elapsed budget prevents another action."""


class AssistantTools:
    def __init__(
        self,
        db_path: str | Path,
        owner_id: str,
        deck_id: str,
        mode: str,
        constraints: dict[str, Any],
        should_stop: Callable[[], bool],
        *,
        experiment_service=None,
        review_gateway=None,
    ):
        self.review_gateway = review_gateway
        self.reviewed_proposals: set[str] = set()
        self.path = Path(db_path)
        self.owner = owner_id
        self.deck_id = deck_id
        self.mode = mode
        self.constraints = constraints
        self.should_stop = should_stop
        self.repo = DeckDocumentRepo(self.path)
        document = self.repo.get(owner_id, deck_id)
        self.revision = int(document["revision"])
        if experiment_service is None:
            from sabermetrics.assistant_experiments import ExperimentService

            experiment_service = ExperimentService(self.path)
        self.experiments = experiment_service
        self.proposals: list[dict[str, Any]] = []
        self.simulations_started = 0
        self.experiments_started = 0
        self.recommendations: dict[str, list[dict[str, Any]]] = {}
        self.discovery_results: dict[str, dict[str, Any]] = {}
        self.strategy_evidence: dict[str, Any] = {}

    @staticmethod
    def _proposal_key(proposal: dict[str, Any]) -> str:
        return json.dumps(proposal.get("replacements", []), sort_keys=True)

    def _require_review(
        self, document: dict[str, Any], proposals: list[dict[str, Any]]
    ) -> None:
        """Direct model-written proposals use the same gate as discovery."""
        from sabermetrics.assistant_swap_review import review_candidates
        from sabermetrics.functional_candidates import oracle_facts

        pending = [
            p for p in proposals if self._proposal_key(p) not in self.reviewed_proposals
        ]
        if not pending:
            return
        evidence = []
        for proposal in pending:
            replacements = proposal.get("replacements", [])
            if len(replacements) != 1:
                raise ValueError(
                    "Review and test one replacement per alternative; combined swaps need joint strategic evidence."
                )
            replacement = replacements[0]
            preview = self.experiments.validate_replacements(
                self.owner,
                self.deck_id,
                self.revision,
                replacements,
                self.constraints,
            )
            old = next(
                e
                for e in document["entries"]
                if e["id"] == replacement["outgoing_entry_id"]
            )
            new = next(
                e
                for e in preview["document"]["entries"]
                if e.get("card_id") == replacement["incoming_card_id"]
            )
            evidence.append(
                {
                    "incoming_oracle_facts": oracle_facts(new),
                    "outgoing_oracle_facts": oracle_facts(old),
                }
            )
        self.check()
        reviewed = review_candidates(
            self.review_gateway,
            {**document, "strategy_evidence": self.strategy_evidence},
            self.constraints,
            str(
                self.constraints.get("objective")
                or "Assess strategic fit; no measured objective selected"
            ),
            pending,
            evidence,
        )
        self.check()
        accepted = {self._proposal_key(p) for p in reviewed.get("alternatives", [])}
        if any(self._proposal_key(p) not in accepted for p in pending):
            rejected = [
                {
                    key: receipt.get(key)
                    for key in (
                        "outgoing_name",
                        "incoming_name",
                        "reason",
                        "tradeoffs",
                        "validation_reason",
                    )
                }
                for receipt in reviewed.get("review_receipts", [])
                if receipt.get("decision") != "accept"
            ]
            raise ValueError(
                "Strategic review did not support this proposal: "
                + (
                    json.dumps(rejected, ensure_ascii=False)[:2500]
                    if rejected
                    else str(
                        reviewed.get("reason") or "missing or conflicting evidence"
                    )
                )
            )
        self.reviewed_proposals.update(accepted)

    def check(self) -> dict[str, Any]:
        if self.should_stop():
            raise ToolStopped(
                "The assistant was stopped or its execution mode changed."
            )
        document = self.repo.get(self.owner, self.deck_id)
        if int(document["revision"]) != self.revision:
            raise RevisionConflict(int(document["revision"]))
        return document

    def execute(self, raw: ToolCall | dict[str, Any]) -> dict[str, Any]:
        call = TOOL_ADAPTER.validate_python(raw)
        document = self.check()
        args = call.arguments
        if call.name == "research_known_combinations":
            from sabermetrics.combo_catalog import known_combinations

            return {
                **known_combinations(self.path, document),
                "revision": self.revision,
                "original_changed": False,
            }
        if call.name == "replay_deck_combination":
            from sabermetrics.combination_intelligence import (
                analyze_combinations,
                replay_combination,
            )

            lines = analyze_combinations(document).get("combinations", [])
            if args.combination_index >= len(lines):
                raise ValueError("Combination index unavailable for the current deck.")
            line = lines[args.combination_index]
            starting_state = args.hypothetical_state.model_dump()
            known_names = {
                entry.get("name") for entry in _library_entries(document)
            } | {
                entry.get("name")
                for entry in document.get("entries", [])
                if isinstance(entry, dict) and entry.get("is_commander")
            }
            name_errors: list[dict[str, str]] = []
            for zone in ("battlefield", "hand", "graveyard"):
                name_errors.extend(
                    {"field": zone, "supplied_name": name}
                    for name in starting_state[zone]
                    if name not in known_names
                )
            for relation in ("attachments", "paired_with"):
                for source, target in starting_state[relation].items():
                    name_errors.extend(
                        {"field": relation, "supplied_name": name}
                        for name in (source, target)
                        if name not in known_names
                    )
            if name_errors:
                replay = {
                    "status": "rejected",
                    "reason": "Supplied hypothetical state contains names that are not exact current-deck card names; this is a state representation error, not a failed card interaction.",
                    "invalid_names": name_errors[:12],
                    "invalid_names_omitted_count": max(0, len(name_errors) - 12),
                    "expected_exact_combination_names": line["cards"],
                    "repair_hint": 'Use exact card names only in zone lists. Keep attachment/pairing relationships in their separate maps; e.g. battlefield=["Vivi Ornitier", "Curiosity"] and attachments={"Curiosity": "Vivi Ornitier"}. Correct only an unambiguous name representation; preserve all supplied resources and setup assumptions. Do not substitute a different card or silently change the state.',
                    "rejection_kind": "state_name_representation",
                    "trace": [],
                    "win_verified": False,
                }
            else:
                replay = replay_combination(line, starting_state)
            return {
                "revision": self.revision,
                "cards": line["cards"],
                "kind": line["kind"],
                "state_origin": "hypothetical_tool_input_not_observed_game",
                "starting_state": starting_state,
                "replay": replay,
                "original_changed": False,
                "goldfish_executed": False,
                "limitation": "Tests only the authored interaction model under the supplied hypothetical assumptions. It does not establish deck consistency, complete rules legality or a win.",
            }
        if call.name == "analyze_deck_combinations":
            from sabermetrics.combination_intelligence import (
                analyze_combinations,
                summarize_combinations,
            )

            analysis = summarize_combinations(analyze_combinations(document))
            return {
                **analysis,
                "revision": self.revision,
                "state_origin": "deck_list_only",
                "execution_started": False,
                "original_changed": False,
            }
        if call.name == "research_memory":
            from sabermetrics.assistant_memory import research_memory

            return research_memory(self.path, self.owner, self.deck_id, limit=3)
        if call.name == "prepare_improvement":
            # One bounded read plan, no implicit simulation or deck mutation.
            # Reuse the same validators and recommendation handles as individual
            # tools. Evidence and assessment cannot authorize experiment writes.
            try:
                strategy_evidence = self.execute(
                    {
                        "name": "retrieve_strategy_evidence",
                        "arguments": {
                            "query": args.focus or "deck strategy engine recovery"
                        },
                    }
                )
            except ValueError as exc:
                if "searchable words" not in str(exc):
                    raise
                strategy_evidence = self.execute(
                    {
                        "name": "retrieve_strategy_evidence",
                        "arguments": {"query": "deck strategy engine recovery"},
                    }
                )
                strategy_evidence["query_note"] = (
                    "The focus contained no searchable words; used general strategy evidence."
                )
            assessment = self.execute(
                {"name": "assess_deck_requirements", "arguments": {}}
            )
            candidates = self.execute(
                {"name": "find_replacements", "arguments": {"focus": args.focus}}
            )
            return {
                **candidates,
                "strategy_evidence": strategy_evidence,
                "assessment": candidates.get("assessment") or assessment,
                "plan_status": (
                    "ready_for_review"
                    if candidates.get("experiment_ready")
                    else "insufficient_alternatives"
                ),
                "execution_started": False,
            }
        if call.name == "retrieve_strategy_evidence":
            from sabermetrics.assistant_strategy_evidence import (
                retrieve_strategy_evidence,
            )

            commanders = ", ".join(
                str(entry.get("name") or "")
                for entry in document.get("entries", [])
                if entry.get("is_commander")
            )
            self.strategy_evidence = retrieve_strategy_evidence(
                self.path, args.query, commander=commanders, limit=args.limit
            )
            return self.strategy_evidence
        if call.name == "assess_deck_requirements":
            from sabermetrics.assistant_deck_intelligence import (
                assess_deck_requirements,
            )

            return {
                "revision": self.revision,
                **assess_deck_requirements(document, self.constraints),
            }
        if call.name == "find_replacements":
            from sabermetrics.assistant_deck_intelligence import recommend_replacements

            focus_key = " ".join(args.focus.casefold().split())
            if focus_key in self.discovery_results:
                return {**self.discovery_results[focus_key], "already_completed": True}
            result = recommend_replacements(
                self.path,
                document,
                self.constraints,
                focus=args.focus,
                candidate_limit=8,
            )
            self.check()
            from sabermetrics.assistant_swap_review import review_candidates

            reviewed = review_candidates(
                self.review_gateway,
                {**document, "strategy_evidence": self.strategy_evidence},
                self.constraints,
                args.focus,
                result.get("alternatives", []),
                result.get("candidate_evidence", []),
            )
            self.check()
            result.update(reviewed)
            result["alternatives"] = result.get("alternatives", [])[:3]
            result["candidate_evidence"] = result.get("candidate_evidence", [])[:3]
            # Store only schema-checked proposals. Execution revalidates all
            # constraints against the current revision and current price data.
            alternatives = [
                Proposal.model_validate(item).model_dump()
                for item in result.get("alternatives", [])
            ]
            if len(alternatives) > 3:
                raise ValueError("Recommendation exceeded the three-alternative limit.")
            for alternative in alternatives:
                self.reviewed_proposals.add(self._proposal_key(alternative))
            recommendation_id = db.new_id()
            self.recommendations[recommendation_id] = alternatives
            result = {
                **result,
                "recommendation_id": recommendation_id,
                "revision": self.revision,
                "experiment_ready": bool(alternatives),
                "original_changed": False,
            }
            result["next_step"] = (
                "Review the supported alternatives, then call test_recommended_alternatives with this recommendation_id in Automate mode. Do not rediscover the same candidates."
                if self.mode == "automate" and alternatives
                else "Use propose_replacements for a selected reviewed preview. If no alternatives survived review, report why rather than inventing a recommendation."
            )
            self.discovery_results[focus_key] = result
            return result
        if call.name == "test_recommended_alternatives":
            recommended = self.recommendations.get(args.recommendation_id)
            if recommended is None:
                raise ValueError(
                    "Recommendation is unavailable; call find_replacements in this turn first."
                )
            if not 1 <= len(recommended) <= 3:
                raise ValueError(
                    "One to three reviewed alternatives are required; constraints will not be relaxed automatically."
                )
            return self.execute(
                {
                    "name": "start_experiment",
                    "arguments": {
                        "alternatives": recommended,
                        "semantic_reviews": [
                            review.model_dump() for review in args.semantic_reviews
                        ],
                    },
                }
            )
        if call.name == "inspect_deck":
            entries = _library_entries(document)
            curve: Counter[str] = Counter()
            roles: Counter[str] = Counter()
            for entry in entries:
                quantity = int(entry.get("quantity") or 1)
                mana = entry.get("mana_value")
                curve[str(mana) if mana is not None else "unknown"] += quantity
                for role in _card_roles(entry):
                    roles[role] += quantity
            matching = [
                entry
                for entry in document.get("entries", [])
                if args.query.casefold() in str(entry.get("name") or "").casefold()
            ]
            shown = matching[args.offset : args.offset + args.limit]
            return {
                "deck_id": self.deck_id,
                "revision": self.revision,
                "front_land_count": sum(
                    int(entry.get("quantity") or 1)
                    for entry in entries
                    if "Land" in str(entry.get("type_line") or "").split("//", 1)[0]
                ),
                "land_back_face_count": sum(
                    int(entry.get("quantity") or 1)
                    for entry in entries
                    if "Land" not in str(entry.get("type_line") or "").split("//", 1)[0]
                    and "//" in str(entry.get("type_line") or "")
                    and "Land" in str(entry.get("type_line") or "").split("//", 1)[1]
                ),
                "land_count_note": "Quantity-aware counts cover the full main-deck library, excluding commanders and sideboard. Back-land faces are counted separately; imported type lines do not establish that every back face can be played from hand. Do not call this a verified MDFC count.",
                "commanders": [
                    {
                        "name": entry.get("name"),
                        "card_id": entry.get("card_id"),
                        "oracle_id": entry.get("oracle_id"),
                    }
                    for entry in document.get("entries", [])
                    if entry.get("is_commander")
                ],
                "matching_entries": len(matching),
                "offset": args.offset,
                "next_offset": (
                    args.offset + len(shown)
                    if args.offset + len(shown) < len(matching)
                    else None
                ),
                "validation": document.get("validation"),
                "entries": [
                    {
                        key: entry.get(key)
                        for key in (
                            "id",
                            "card_id",
                            "oracle_id",
                            "name",
                            "quantity",
                            "role",
                            "is_commander",
                            "zone_name",
                            "mana_value",
                        )
                    }
                    for entry in shown
                ],
                "mana_curve": dict(curve),
                "role_counts": dict(roles),
                "role_note": "Stored roles and text tags are heuristic classifications, not simulated performance.",
            }
        if call.name == "search_cards":
            colors: set[str] = set()
            for entry in document.get("entries", []):
                if entry.get("is_commander"):
                    raw_colors = entry.get("color_identity") or []
                    colors.update(
                        json.loads(raw_colors)
                        if isinstance(raw_colors, str)
                        else raw_colors
                    )
            with sqlite3.connect(self.path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                results = search_cards(
                    conn,
                    db_key=str(self.path),
                    allowed_colors=colors,
                    **args.model_dump(),
                )
            return {
                "cards": results,
                "source": "Imported Commander-legal card catalog",
                "identity_filter": sorted(colors),
                "revision": self.revision,
            }
        if call.name == "card_details":
            from sabermetrics.assistant_card_facts import card_facts
            from sabermetrics.card_capabilities import relationships
            from sabermetrics.card_economy import (
                card_economy_profile,
                printed_cast_trigger_damage,
            )
            from sabermetrics.cast_trigger_evidence import (
                document_cast_trigger_evidence,
            )

            facts = card_facts(self.path, args.card_id)
            if not facts.get("card"):
                # Names and Oracle IDs are common conversational references.
                # Resolve exact matches only; distinct identities are never merged.
                with db.connect(self.path) as conn:
                    matches = conn.execute(
                        "SELECT MIN(id) AS id, oracle_id FROM cards WHERE oracle_id=? GROUP BY oracle_id",
                        (args.card_id,),
                    ).fetchall()
                    resolution = "exact_oracle_id"
                    if not matches:
                        matches = conn.execute(
                            "SELECT MIN(id) AS id, oracle_id FROM cards WHERE name=? COLLATE NOCASE AND oracle_id IS NOT NULL GROUP BY oracle_id LIMIT 2",
                            (args.card_id.strip(),),
                        ).fetchall()
                        resolution = "exact_name"
                if len(matches) == 1:
                    facts = card_facts(self.path, matches[0]["id"])
                    facts["reference_resolution"] = resolution
                elif len(matches) > 1:
                    return {
                        "status": "ambiguous",
                        "requested_reference": args.card_id,
                        "note": "Exact name identifies multiple Oracle cards; use a catalog card ID.",
                    }
            if facts.get("card"):
                facts["commander_cast_interactions"] = document_cast_trigger_evidence(
                    document, facts["card"]
                )
                facts["card_economy"] = card_economy_profile(facts["card"])
                facts["printed_cast_trigger_damage"] = printed_cast_trigger_damage(
                    facts["card"]
                )
                facts["capability_relationships"] = relationships(facts["card"])
                facts["capability_note"] = (
                    "Bounded Oracle-text inferences with prerequisites; not certified rules execution or native simulation coverage."
                )
            return facts
        if call.name == "research_evidence":
            evidence = DeckEvidenceService(self.path)
            result = (
                evidence.alternatives(
                    document, args.oracle_id, args.window_days, limit=8
                )
                if args.oracle_id
                else evidence.for_deck(document, args.window_days)
            )
            return {
                "evidence": result,
                "revision": self.revision,
                "note": "Recorded list inclusion is evidence of use, not a win forecast or proof of a swap's effect.",
            }
        if call.name == "goldfish_card_coverage":
            view = simulation_view(self.path, self.deck_id)
            coverage = ((view.get("result") or {}).get("diagnostics") or {}).get(
                "coverage"
            ) or {}
            audit = coverage.get("card_coverage") or {}
            cards = [
                card
                for card in audit.get("cards", [])
                if args.query.casefold() in card.get("name", "").casefold()
            ]
            return {
                "report_id": view.get("id"),
                "freshness": view.get("freshness", "unknown"),
                "freshness_scope": view.get("freshness_scope"),
                "counts": audit.get("counts"),
                "cards": cards[args.offset : args.offset + args.limit],
                "matching_slots": len(cards),
                "omitted_count": max(
                    0, len(cards) - len(cards[args.offset : args.offset + args.limit])
                ),
                "note": (
                    "Legacy authoring categories do not distinguish partial from missing effects."
                    if not audit
                    else "Coverage applies to recorded simulation settings; unknown outcomes are not failures."
                ),
            }
        if call.name == "latest_goldfish":
            view = simulation_view(self.path, self.deck_id)
            return {"view": view, **_goldfish_observations(view)}
        if call.name == "generator_intelligence":
            role_data = [
                {
                    "entry_id": entry["id"],
                    "name": entry.get("name"),
                    "stored_role": entry.get("role"),
                    "text_tags": _card_roles(entry),
                }
                for entry in _library_entries(document)
            ]
            source = None
            if document.get("source_kind") == "candidate" and document.get("source_id"):
                row = db.CedhCandidatesRepo(self.path).get(str(document["source_id"]))
                if row and row.get("owner_id") == self.owner:
                    source = {
                        key: row.get(key)
                        for key in (
                            "candidate_id",
                            "pack_id",
                            "deck_sha256",
                            "evidence_hash",
                            "explanation_json",
                            "warnings_json",
                        )
                    }
            from sabermetrics.cedh.fallback_builder import GENERIC_CATALOG, ROLE_TARGETS

            generator_colors: set[str] = set()
            for entry in document.get("entries", []):
                if entry.get("is_commander"):
                    value = entry.get("color_identity") or []
                    generator_colors.update(
                        json.loads(value) if isinstance(value, str) else value
                    )
            curated: dict[str, str] = {}
            for color in ["C", *sorted(generator_colors)]:
                for role, names in GENERIC_CATALOG.get(color, {}).items():
                    for name in names[:3]:
                        if len(curated) < 24:
                            curated.setdefault(name, role)
            pool = []
            present = {
                str(entry.get("oracle_id")) for entry in document.get("entries", [])
            }
            with sqlite3.connect(self.path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                for name, role in curated.items():
                    row = conn.execute(
                        "SELECT id,oracle_id,name,color_identity,mana_cost,type_line FROM cards WHERE name=? AND is_legal_in_99=1 ORDER BY id LIMIT 1",
                        (name,),
                    ).fetchone()
                    if row is None:
                        continue
                    card = dict(row)
                    value = card.get("color_identity") or []
                    identity = set(
                        json.loads(value) if isinstance(value, str) else value
                    )
                    if (
                        not identity.issubset(generator_colors)
                        or str(card["oracle_id"]) in present
                    ):
                        continue
                    pool.append(
                        {
                            **card,
                            "generator_role": role,
                            "source": "generic_fallback authored catalog, resolved against current imported cards",
                        }
                    )
            return {
                "candidate_pool": pool,
                "role_targets": ROLE_TARGETS,
                "targets_note": "Generic starter targets, not a commander-specific optimum or verified win package.",
                "roles": role_data,
                "generator_source": source,
                "note": "Stored generator rationale is untrusted explanation; role tags do not verify card mechanics or finish lines.",
            }
        if call.name == "propose_replacements":
            proposal = args.proposal.model_dump()
            preview = self.experiments.validate_replacements(
                self.owner,
                self.deck_id,
                self.revision,
                proposal["replacements"],
                self.constraints,
            )
            self._require_review(document, [proposal])
            self.check()
            enriched_replacements = []
            for replacement in proposal["replacements"]:
                outgoing = next(
                    entry
                    for entry in document["entries"]
                    if entry["id"] == replacement["outgoing_entry_id"]
                )
                incoming = next(
                    entry
                    for entry in preview["document"]["entries"]
                    if entry.get("card_id") == replacement["incoming_card_id"]
                )
                enriched_replacements.append(
                    {
                        **replacement,
                        "outgoing_name": outgoing["name"],
                        "incoming_name": incoming["name"],
                    }
                )
            validated = {
                **proposal,
                "replacements": enriched_replacements,
                "expected_revision": self.revision,
                "deck_sha256": preview["deck_sha256"],
                "price_total_usd": preview.get("price_total_usd"),
                "status": "preview",
                "semantic_verification": "unverified",
            }
            self.proposals.append(validated)
            return {"proposal": validated, "original_changed": False}
        if call.name == "start_experiment":
            if self.mode != "automate":
                raise ValueError("Experiments are available only in Automate mode.")
            if self.experiments_started:
                raise ValueError(
                    "Only one experiment may be started per assistant turn."
                )
            alternatives = [
                alternative.model_dump() for alternative in args.alternatives
            ]
            self._require_review(document, alternatives)
            required = [
                field
                for constraint, field in (
                    ("theme", "theme_fit"),
                    ("power_level", "power_fit"),
                )
                if str(self.constraints.get(constraint) or "").strip()
            ]
            reviews = {
                review.alternative_index: review for review in args.semantic_reviews
            }
            if required:
                if set(reviews) != set(range(len(alternatives))) or len(
                    args.semantic_reviews
                ) != len(alternatives):
                    raise ValueError(
                        "Review theme and power fit for every alternative before testing; include evidence-based reasoning and mark uncertain fit unknown."
                    )
                if any(
                    getattr(review, field) != "fit"
                    for review in reviews.values()
                    for field in required
                ):
                    raise ValueError(
                        "An alternative conflicts with, or has unknown fit for, the requested theme/power. Revise the candidates; constraints cannot be silently relaxed."
                    )
            for index, review in reviews.items():
                alternatives[index]["rationale"] += (
                    "\nSemantic preference review (model inference, not verified): "
                    + review.reasoning
                )
            self.check()
            experiment = self.experiments.create(
                self.owner,
                self.deck_id,
                self.revision,
                alternatives,
                self.constraints,
            )
            self.experiments_started += 1
            # Cancellation after creation leaves a truthful queued snapshot,
            # never an invented completed experiment or an original deck edit.
            # The service acknowledges cancellation as stopped without a native
            # call, rather than leaving an abandoned queued experiment.
            self.experiments.execute(self.owner, experiment["id"], self.should_stop)
            result = self.experiments.get(self.owner, experiment["id"])
            return {
                "experiment_id": result["id"],
                "status": result["status"],
                "baseline": {
                    "revision": result["baseline"]["revision"],
                    "deck_sha256": result["baseline"]["deck_sha256"],
                },
                "metric_summary": _experiment_metrics(result),
                "comparison_note": "Use each outcome verdict for the selected objective only. Improvement requires compatible measured evidence and independent validation; no verdict establishes an overall deck upgrade.",
                "alternatives": [
                    {
                        "id": item.get("id"),
                        "name": str(item.get("name") or "")[:200],
                        "status": item.get("status"),
                        "outcome": item.get("outcome"),
                        "replacements": item.get("replacements"),
                        "error": str(item.get("error") or "")[:300],
                        "coverage": {
                            key: (item.get("training_report") or {})
                            .get("coverage", {})
                            .get(key)
                            for key in (
                                "modeled_cards",
                                "inert_cards",
                                "unauthored_cards",
                            )
                        },
                        "card_coverage_counts": (
                            (item.get("training_report") or {})
                            .get("coverage", {})
                            .get("card_coverage")
                            or {}
                        ).get("counts"),
                        "comparison_comparable": (item.get("comparison") or {}).get(
                            "comparable"
                        ),
                        "comparison_reason": str(
                            (item.get("comparison") or {}).get("reason") or ""
                        )[:300],
                        "validation_status": (item.get("validation") or {}).get(
                            "status"
                        ),
                        "validation_reason": str(
                            (item.get("validation") or {}).get("reason") or ""
                        )[:300],
                    }
                    for item in result["alternatives"]
                ],
                "summary_note": "One selected actual metric per test per alternative; remaining metrics, complete reports and rationales are retained in the persisted experiment.",
                "error": result.get("error"),
                "original_changed": False,
                "semantic_reviews": [
                    review.model_dump() for review in args.semantic_reviews
                ],
                "semantic_review_status": (
                    "model_inference_not_verified"
                    if reviews
                    else "no_semantic_constraints"
                ),
            }
        if call.name == "run_goldfish":
            return self._run_goldfish(document)
        raise ValueError("Unknown assistant tool.")

    def _run_goldfish(self, document: dict[str, Any]) -> dict[str, Any]:
        if self.simulations_started:
            raise ValueError(
                "Only one current-deck Goldfish run may be started per turn."
            )
        from sabermetrics.cedh.settings import load_cedh_settings
        from sabermetrics.cedh.simulator import (
            HttpSimulatorClient,
            NotSimulated,
            SimulationResult,
        )
        from sabermetrics.deck_simulation import (
            _with_outcome,
            candidate_from_document,
            load_provenance,
            prepare_simulation,
        )

        if not document.get("validation", {}).get("legal"):
            raise ValueError("Goldfish requires a legal deck before running.")
        settings = load_cedh_settings().simulator
        if settings.mode != "http":
            raise ValueError(
                "Native HTTP Goldfish is unavailable; no simulation was run."
            )
        digest, frozen = prepare_simulation(self.path, document)
        payload = json.loads(frozen)
        payload["assistant_run"] = {
            "schema_version": "decklab-assistant-goldfish.v1",
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
        }
        frozen = json.dumps(payload)
        candidate = candidate_from_document(
            document, load_provenance(self.path, document)
        )
        self.check()
        repo = db.DeckDocumentSimulationsRepo(self.path)
        simulation_id = repo.insert(
            deck_id=self.deck_id,
            owner_id=self.owner,
            deck_sha256=digest,
            revision=self.revision,
            result_json=frozen,
        )
        self.simulations_started += 1
        repo.update(simulation_id, status="running")
        try:
            if self.should_stop():
                raise ToolStopped("Goldfish was stopped before its native request.")
            client = HttpSimulatorClient(
                settings.url,
                games=2000,
                objective_turn=settings.objective_turn,
                timeout_seconds=min(settings.timeout_seconds, 45),
                diagnostics=True,
                diagnostics_seed=20261012,
            )
            outcome = client.simulate(candidate)
            if isinstance(outcome, NotSimulated):
                repo.update(
                    simulation_id,
                    status="not_simulated",
                    reason=outcome.detail or outcome.reason,
                )
            elif (
                isinstance(outcome, SimulationResult) and outcome.deck_sha256 == digest
            ):
                repo.update(
                    simulation_id,
                    status="done",
                    result_json=_with_outcome(
                        repo, simulation_id, outcome.model_dump(mode="json")
                    ),
                )
            else:
                raise ValueError(
                    "The native simulator returned no valid matching-deck result."
                )
        except Exception:
            repo.update(
                simulation_id,
                status="not_simulated",
                reason="Assistant Goldfish request stopped or failed; no measured result was stored.",
            )
            raise
        view = simulation_view(self.path, self.deck_id)
        return {
            "simulation_id": simulation_id,
            "status": view.get("status"),
            "view": view,
            **_goldfish_observations(view),
            "original_changed": False,
        }


def _experiment_metrics(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten actual experiment values so bounded chat context preserves them."""
    baseline = result.get("baseline") or {}
    rows = []
    priorities = {
        "opening": "qualified_opening_rate",
        "development": "engine_online_rate",
        "commander_loss": "recovery_draw_rate",
        "creature_wipe": "recovery_board_rate",
        "finish": "positive_finish_proof_rate",
    }

    def value(report, test, metric):
        return (
            (report or {}).get("tests", {}).get(test, {}).get("metrics", {}).get(metric)
            or {}
        ).get("value")

    for alternative in result.get("alternatives", []):
        report = alternative.get("training_report") or {}
        selected = []
        for test, preferred in priorities.items():
            metrics = report.get("tests", {}).get(test, {}).get("metrics", {})
            if metrics:
                selected.append(
                    (test, preferred if preferred in metrics else next(iter(metrics)))
                )
        comparison = alternative.get("comparison") or {}
        validation = alternative.get("validation") or {}
        for test, metric in selected:
            delta = comparison.get("metric_deltas", {}).get(test, {}).get(metric, {})
            heldout = (
                (validation.get("comparison") or {})
                .get("metric_deltas", {})
                .get(test, {})
                .get(metric, {})
            )
            rows.append(
                {
                    "alternative_id": alternative.get("id"),
                    "alternative_name": alternative.get("name"),
                    "test": test,
                    "metric": metric,
                    "unit": report.get("tests", {})
                    .get(test, {})
                    .get("metrics", {})
                    .get(metric, {})
                    .get("unit"),
                    "before": value(baseline.get("training_report"), test, metric),
                    "after": value(report, test, metric),
                    "delta": delta.get("delta"),
                    "reason": str(
                        delta.get("reason") or comparison.get("reason") or ""
                    )[:240],
                    "comparable": delta.get("comparable", False),
                    "validation_before": value(
                        baseline.get("validation_report"), test, metric
                    ),
                    "validation_after": value(
                        alternative.get("validation_report"), test, metric
                    ),
                    "validation_delta": heldout.get("delta"),
                    "validation_status": validation.get("status"),
                    "validation_reason": str(
                        heldout.get("unvalidated_reason")
                        or validation.get("reason")
                        or ""
                    )[:240],
                }
            )
    return rows


def _goldfish_observations(view: dict[str, Any]) -> dict[str, Any]:
    """Retain native values at a shallow depth in the model's evidence context."""
    diagnostics = (view.get("result") or {}).get("diagnostics") or {}
    priorities = {
        "opening": ("qualified_opening_rate", "mean_kept_cards"),
        "development": ("engine_online_rate", "mean_missed_land_drops"),
        "commander_loss": ("disruption_exercised_rate", "recovery_draw_rate"),
        "creature_wipe": ("disruption_exercised_rate", "recovery_board_rate"),
        "finish": ("positive_finish_proof_rate", "verified_finish_rate"),
    }
    observations = []
    statuses = []
    for test, names in priorities.items():
        body = diagnostics.get("tests", {}).get(test) or {}
        if not body:
            continue
        statuses.append(
            {
                "test": test,
                "status": body.get("status"),
                "limitations": body.get("limitations", [])[:2],
            }
        )
        for name in names:
            metric = body.get("metrics", {}).get(name)
            if isinstance(metric, dict):
                observations.append({"test": test, "metric": name, **metric})
    return {
        "observations": observations,
        "test_statuses": statuses,
        "settings": diagnostics.get("settings"),
        "coverage": diagnostics.get("coverage"),
        "coverage_note": "Per-card coverage, when present, counts card slots under recorded simulation settings. Supported does not imply multiplayer validation. Legacy authoring labels do not distinguish missing effects from partial diagnostic effects. Consult each card’s implemented effects and limitations; unknown is not failure.",
        "freshness": view.get("freshness", "unknown"),
        "freshness_scope": view.get("freshness_scope"),
        "freshness_reason": view.get("freshness_reason"),
        "stale": view.get("stale"),
        "report_status": view.get("status"),
        "report_id": view.get("id"),
        "report_revision": view.get("revision"),
        "report_deck_sha256": view.get("deck_sha256"),
        "report_reason": view.get("reason"),
        "report_note": "Null metrics are unknown. A stale report measures a previous list; do not treat it as current.",
    }


def recover_interrupted_goldfish(
    db_path: str | Path, owner_id: str, deck_id: str, now: datetime | None = None
) -> int:
    """Expire only unfinished assistant-owned requests past their recovery TTL.

    Native requests have a 45-second timeout. A fifteen-minute unfinished
    marker is therefore interrupted work, not a current run or a measured
    failure. Frozen snapshots/provenance and any evidence remain in the row.
    """
    DeckDocumentRepo(db_path).get(owner_id, deck_id)
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(minutes=15)
    recovered = 0
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id,status,result_json FROM deck_document_simulations WHERE owner_id=? AND deck_id=? AND status IN ('queued','running') AND CASE WHEN json_valid(result_json) THEN json_extract(result_json,'$.assistant_run.schema_version') END=? ORDER BY created_at,id LIMIT 50",
            (owner_id, deck_id, "decklab-assistant-goldfish.v1"),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["result_json"] or "null")
                marker = (
                    payload.get("assistant_run") if isinstance(payload, dict) else None
                )
                if (
                    not isinstance(marker, dict)
                    or marker.get("schema_version") != "decklab-assistant-goldfish.v1"
                ):
                    continue
                started = datetime.fromisoformat(str(marker.get("started_at") or ""))
                if started.tzinfo is None or started >= cutoff:
                    continue
            except (TypeError, ValueError):
                continue
            marker.update(status="interrupted", recovered_at=current.isoformat())
            updated = json.dumps(payload)
            cursor = conn.execute(
                "UPDATE deck_document_simulations SET status='not_simulated',reason=?,result_json=? WHERE id=? AND owner_id=? AND deck_id=? AND status=? AND result_json=?",
                (
                    "Not simulated: assistant Goldfish request was interrupted; no completed measurement is available.",
                    updated,
                    row["id"],
                    owner_id,
                    deck_id,
                    row["status"],
                    row["result_json"],
                ),
            )
            recovered += cursor.rowcount
        conn.commit()
    return recovered


def resolve_presentation_cards(
    db_path: str | Path, known_ids: set[str], content: str, requested_ids: list[str]
) -> list[dict[str, Any]]:
    """Select only resolved known catalog cards; the model supplies no metadata."""
    import re

    from sabermetrics.card_search import search_needle

    known = sorted(known_ids)[:512]
    if not known:
        return []
    with sqlite3.connect(db_path, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        marks = ",".join("?" for _ in known)
        rows = conn.execute(
            f"SELECT id,oracle_id,name,mana_cost,type_line,oracle_text,image_uri FROM cards WHERE id IN ({marks}) ORDER BY name,id",
            known,
        ).fetchall()
    cards = {str(row["id"]): dict(row) for row in rows}
    chosen = [card_id for card_id in requested_ids if card_id in cards]
    normalized = search_needle(content).casefold()
    for card_id, card in cards.items():
        aliases = [str(card["name"]), str(card["name"]).split(" // ", 1)[0]]
        if any(
            re.search(
                r"(?<!\w)" + re.escape(search_needle(alias).casefold()) + r"(?!\w)",
                normalized,
            )
            for alias in aliases
            if search_needle(alias)
        ):
            chosen.append(card_id)
    selected = []
    seen = set()
    for card_id in chosen:
        card = cards[card_id]
        identity = str(card.get("oracle_id") or card_id)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(
            {
                key: card.get(key)
                for key in (
                    "id",
                    "name",
                    "mana_cost",
                    "type_line",
                    "oracle_text",
                    "image_uri",
                )
            }
        )
        if len(selected) >= 12:
            break
    return selected
