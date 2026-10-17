"""A bounded package-composition primitive; never an actionable swap review."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, ValidationError

from sabermetrics.assistant_research_task import ResearchHypothesis, validate_hypotheses
from sabermetrics.assistant_tools import StrictModel
from sabermetrics.cedh.errors import ModelProviderError, ModelValidationError
from sabermetrics.cedh.model_gateway import ReasoningMode, StructuredRequest
from sabermetrics.deck_evidence import _library_entries
from sabermetrics.errors import LLMCostCeilingExceeded


class PackageChoice(StrictModel):
    incoming_index: int = Field(ge=0, le=19)
    outgoing_index: int = Field(ge=0, le=11)
    incoming_quote: str = Field(min_length=12, max_length=1200)
    outgoing_quote: str = Field(min_length=12, max_length=1200)
    loss: str = Field(min_length=10, max_length=200)


class PackageComposition(StrictModel):
    choices: list[PackageChoice] = Field(default_factory=list, max_length=3)
    shared_interaction: str = Field(default="", max_length=650)
    conditions: list[Annotated[str, Field(min_length=1, max_length=140)]] = Field(
        default_factory=list, max_length=4
    )
    blocker: str = Field(default="", max_length=500)


def compose_package(
    tools, document: dict[str, Any], focus: str, count: int, gateway
) -> dict[str, Any]:
    """One separately metered call over <=20 new cards and <=12 actual cuts."""
    from sabermetrics.assistant_candidate_recall import recall_candidates
    from sabermetrics.assistant_context_rank import rank_card_context
    from sabermetrics.assistant_deck_intelligence import assess_deck_requirements
    from sabermetrics.assistant_engine_graph import (
        build_engine_graph,
        engine_loss_score,
    )
    from sabermetrics.assistant_strategy import StrategyContext

    def blocked(reason: str) -> dict[str, Any]:
        return {
            "status": "insufficient_evidence",
            "reason": reason,
            "hypotheses": [],
            "original_changed": False,
        }

    if gateway is None:
        return blocked("Package research provider is unavailable.")
    strategy = StrategyContext.model_validate(
        assess_deck_requirements(document, tools.constraints)["strategy_context"]
    )
    focused = (
        focus
        + " "
        + " ".join(
            contract.capability.replace("_", " ") for contract in strategy.contracts
        )
    )
    recall = recall_candidates(tools.path, document, strategy, focused)
    tools.check()
    present = {
        str(c.get("oracle_id"))
        for c in document.get("entries", [])
        if c.get("oracle_id")
    }
    present_ids = {
        str(c.get("card_id")) for c in document.get("entries", []) if c.get("card_id")
    }
    incoming = [
        item.card
        for item in recall.candidates
        if str(item.card.get("oracle_id")) not in present
        and str(item.card.get("id")) not in present_ids
        and 12 <= len(str(item.card.get("oracle_text") or "")) <= 1200
    ]
    incoming.sort(key=lambda c: -rank_card_context(document, c, focused).priority)
    incoming = incoming[:20]
    graph = build_engine_graph(document)
    outgoing = [
        c
        for c in _library_entries(document)
        if c.get("card_id") not in tools.constraints.get("protected_cards", [])
        and "land" not in str(c.get("type_line") or "").casefold()
        and 12 <= len(str(c.get("oracle_text") or "")) <= 1200
    ]
    outgoing.sort(key=lambda c: (engine_loss_score(graph, c), str(c.get("name"))))
    outgoing = outgoing[:12]
    if min(len(incoming), len(outgoing)) < count:
        return blocked(
            "The bounded catalog or cut pool has fewer cards than requested."
        )

    def printed(card):
        return {
            k: card.get(k) for k in ("name", "mana_cost", "type_line", "oracle_text")
        }

    supports = [
        c
        for c in _library_entries(document)
        if rank_card_context(document, c, focused).priority > 0
    ]
    payload: dict[str, Any] = {
        "focus": focus,
        "count": count,
        "existing_card_names": [c["name"] for c in document["entries"]],
        "commanders": [
            printed(c) for c in document["entries"] if c.get("is_commander")
        ],
        "existing_support": [printed(c) for c in supports[:8]],
        "incoming": [printed(c) for c in incoming],
        "outgoing": [printed(c) for c in outgoing],
    }
    while len(json.dumps(payload).encode()) > 24576 and len(incoming) > count:
        incoming.pop()
        payload["incoming"] = [printed(c) for c in incoming]
    if len(json.dumps(payload).encode()) > 24576:
        return blocked("Package evidence exceeds the bounded source budget.")
    encoded = json.dumps(payload, ensure_ascii=False)
    try:
        generated = gateway.generate(
            StructuredRequest(
                prompt_version="assistant-package-composition.v1",
                system=(
                    Path(__file__).parent / "prompts/assistant_package_composition.txt"
                ).read_text(),
                user=encoded,
                schema=PackageComposition,
                mode=ReasoningMode.EXPLAIN,
                call_type="build:package-research",
                max_output_tokens=4096,
                evidence_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            )
        ).value
        tools.check()
        plan = PackageComposition.model_validate(
            generated.model_dump() if hasattr(generated, "model_dump") else generated
        )
    except LLMCostCeilingExceeded:
        return blocked("The model cost ceiling prevented package composition.")
    except (ModelProviderError, ModelValidationError, ValidationError):
        return blocked(
            "The package provider did not return a complete validated composition."
        )
    if (
        len(plan.choices) != count
        or len(plan.shared_interaction) < 10
        or not plan.conditions
    ):
        return blocked(
            plan.blocker
            or "No coherent package was supported by the supplied evidence."
        )
    if (
        len({c.incoming_index for c in plan.choices}) != count
        or len({c.outgoing_index for c in plan.choices}) != count
    ):
        return blocked(
            "The selected package did not contain distinct additions and cuts."
        )
    pairs, losses = [], []
    for choice in plan.choices:
        if choice.incoming_index >= len(incoming) or choice.outgoing_index >= len(
            outgoing
        ):
            return blocked("A selected index was outside the supplied pool.")
        new, old = incoming[choice.incoming_index], outgoing[choice.outgoing_index]
        if (
            choice.incoming_quote not in new["oracle_text"]
            or choice.outgoing_quote not in old["oracle_text"]
        ):
            return blocked(
                "A selected card's quoted ability was not in its supplied Oracle text."
            )
        pairs.append({"incoming_card_id": new["id"], "outgoing_entry_id": old["id"]})
        losses.append(
            {
                "outgoing_entry_id": old["id"],
                "explanation": old["name"] + ": " + choice.loss,
            }
        )
    prose = " ".join(
        [plan.shared_interaction, *plan.conditions, *(c.loss for c in plan.choices)]
    )
    selected_names = {
        incoming[c.incoming_index]["name"].casefold() for c in plan.choices
    }
    for card in incoming:
        name = card["name"]
        if (
            name.casefold() not in selected_names
            and name.casefold() in prose.casefold()
        ):
            return blocked("The interaction depends on an unselected catalog card.")
    for name in payload["existing_card_names"]:
        pattern = (
            r"(?:\badd(?:ing)?\s+|\binclude\s+|\bbring in\s+|\bnew\s+(?:addition\s+)?|\baddition:\s*)"
            + re.escape(name)
            + r"\b|"
            + re.escape(name)
            + r"\s*\(new\)"
        )
        if re.search(pattern, prose, re.IGNORECASE):
            return blocked("An existing deck card was described as a new addition.")
    hypothesis = ResearchHypothesis.model_validate(
        {
            "proposal": {
                "name": "Coordinated research package",
                "rationale": plan.shared_interaction,
                "replacements": pairs,
            },
            "shared_interaction": plan.shared_interaction,
            "conditions": plan.conditions,
            "losses": losses,
        }
    )
    hypotheses, errors = validate_hypotheses(tools, [hypothesis])
    if not hypotheses:
        return blocked(
            "Joint constraint validation rejected the package: " + "; ".join(errors)
        )
    hypotheses[0]["source_citations"] = [
        {
            "incoming": incoming[c.incoming_index]["name"],
            "incoming_quote": c.incoming_quote,
            "outgoing": outgoing[c.outgoing_index]["name"],
            "outgoing_quote": c.outgoing_quote,
        }
        for c in plan.choices
    ]
    return {
        "status": "research_hypothesis",
        "hypotheses": hypotheses,
        "original_changed": False,
        "scope": "Catalog-grounded, jointly constraint-checked research hypothesis; not strategic approval or measured improvement.",
        "pool": {
            "incoming": len(incoming),
            "outgoing": len(outgoing),
            "recall_candidates": len(recall.candidates),
        },
    }
