"""Narrow request interpretation before the assistant sees execution tools.

This phase interprets intent; it does not grant action authority or certify
preference extraction. Its call occupies the existing first outer-call slot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from sabermetrics.assistant_research_task import EvidenceScope, PlanningResearchTask
from sabermetrics.assistant_tools import StrictModel


class PlannerResearchTask(PlanningResearchTask):
    evidence_scope: EvidenceScope = Field(
        description="Evidence needed by the current request, independently of kind; supported by current_request_quote. named_cards_only means ONLY assessing the explicitly named cards; candidate_discovery means finding additional cards or new lists, even when comparing named benchmarks; existing_candidates means comparing an already retrieved set; unknown when unclear. Resolving named benchmarks does not complete candidate discovery."
    )
    requires_empirical_evidence: bool = Field(
        description="True when this request needs actual Goldfish/simulation/experiment measurements or recorded results. Printed card facts alone cannot answer such requests. False for printed-card research or strategic hypotheses without requested measurement."
    )


class DirectPlannerAnswer(StrictModel):
    content: str = Field(min_length=12, max_length=6000)
    findings: list[Annotated[str, Field(min_length=1, max_length=700)]] = Field(
        min_length=1, max_length=6
    )
    next_action: str = Field(default="", max_length=700)


class RequestPlan(StrictModel):
    research_task: PlannerResearchTask
    route: Literal["answer", "use_tools"] = Field(
        description="answer only when supplied current facts suffice; use_tools continues actual orchestration. A promise of future research is never an answer."
    )
    direct_answer: DirectPlannerAnswer | None = None

    @model_validator(mode="after")
    def direct_answer_required(self):
        if self.route == "answer" and self.direct_answer is None:
            raise ValueError("The answer route requires an explicit direct answer")
        return self


def planner_system() -> str:
    return (Path(__file__).parent / "prompts/assistant_request_planner.txt").read_text()


def synthesis_system() -> str:
    return (
        Path(__file__).parent / "prompts/assistant_answer_synthesis.txt"
    ).read_text()


def planning_payload(current_request: str, state: dict[str, Any]) -> str:
    """Expose the latest request and bounded user evidence, never prior answers."""
    context = state.get("conversation_context") or {}
    composition = state.get("deck_composition") or {}
    count = composition.get("library_card_count")
    commanders = []
    for card in state.get("commanders", [])[:2]:
        text = str(card.get("oracle_text") or "")
        commanders.append(
            {
                "name": card.get("name"),
                "type_line": card.get("type_line"),
                "mana_cost": card.get("mana_cost"),
                "oracle_text": text[:900],
                "oracle_text_truncated": len(text) > 900,
            }
        )
    preferences = [
        {key: row.get(key) for key in ("dimension", "value", "strength", "quote")}
        for row in context.get("preferences", [])[:8]
        if isinstance(row, dict)
    ]
    payload: dict[str, Any] = {
        "current_user_request": current_request,
        "recent_user_statements": [
            {"text": str(row.get("text") or "")[:900]}
            for row in context.get("user_statements", [])[:-1][-3:]
        ],
        "saved_preferences": preferences,
        "preference_scope": "Saved preferences are inherited context. Assess only NEW preferences in current_user_request; null leaves saved memory intact.",
        "commanders": commanders,
        "capacity": {
            "library_card_count": count,
            "open_slots": max(0, 99 - count) if isinstance(count, int) else None,
            "front_land_count": composition.get("front_land_count"),
            "land_back_face_count": composition.get("land_back_face_count"),
        },
        "evidence_scope": "Only the listed current printed facts and computed counts are supplied. No measured Goldfish or experiment result is supplied to the planner. Capacity is not a requested recommendation count.",
    }
    while True:
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) <= 16000:
            return encoded
        if payload["recent_user_statements"]:
            payload["recent_user_statements"].pop(0)
        else:
            raise ValueError(
                "The latest request and planning context exceed the context limit"
            )
