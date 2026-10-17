"""Per-turn research obligations; hypotheses never grant action authority."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import Field, model_validator

from sabermetrics.assistant_tools import Proposal, StrictModel


class ResearchTask(StrictModel):
    additions: int = Field(default=0, ge=0, le=99)
    cuts: int = Field(default=0, ge=0, le=99)
    coordinated: bool = False
    deliverables: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=6
    )


class CutLoss(StrictModel):
    outgoing_entry_id: str = Field(min_length=1, max_length=128)
    explanation: str = Field(min_length=10, max_length=400)


class ResearchHypothesis(StrictModel):
    proposal: Proposal
    shared_interaction: str = Field(min_length=10, max_length=700)
    conditions: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        min_length=1, max_length=4
    )
    losses: list[CutLoss] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def every_cut_has_a_loss(self):
        expected = {p.outgoing_entry_id for p in self.proposal.replacements}
        observed = [loss.outgoing_entry_id for loss in self.losses]
        if len(observed) != len(expected) or set(observed) != expected:
            raise ValueError("Each distinct cut needs its own loss explanation.")
        return self


def progress(task: ResearchTask, plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Count one coherent package; never combine mutually exclusive alternatives."""
    counts = []
    for plan in plans:
        pairs = plan.get("replacements", [])
        counts.append(
            (
                len(
                    {
                        p.get("incoming_card_id")
                        for p in pairs
                        if p.get("incoming_card_id")
                    }
                ),
                len(
                    {
                        p.get("outgoing_entry_id")
                        for p in pairs
                        if p.get("outgoing_entry_id")
                    }
                ),
            )
        )
    best = max(
        counts,
        key=lambda n: min(n[0], task.additions) + min(n[1], task.cuts),
        default=(0, 0),
    )
    if not task.coordinated:
        best = (
            len(
                {
                    p.get("incoming_card_id")
                    for plan in plans
                    for p in plan.get("replacements", [])
                    if p.get("incoming_card_id")
                }
            ),
            len(
                {
                    p.get("outgoing_entry_id")
                    for plan in plans
                    for p in plan.get("replacements", [])
                    if p.get("outgoing_entry_id")
                }
            ),
        )
    missing = {
        "additions": max(0, task.additions - best[0]),
        "cuts": max(0, task.cuts - best[1]),
    }
    return {
        **task.model_dump(),
        "supported_package_limit": 3,
        "deliverables_evaluated": False,
        "observed_additions": best[0],
        "observed_cuts": best[1],
        "missing": missing,
        "counts_complete": not any(missing.values()),
        "scope": "Counts establish ingredients only; shared interaction, conditions and losses still need evidence. No measured improvement is inferred.",
    }


_NUMBER = r"(?:[1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine|ten)"
_WORDS = {
    word: n
    for n, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
        ]
    )
}


def reconcile_task(
    task: ResearchTask, request: str
) -> tuple[ResearchTask, dict[str, Any]]:
    """Explicit cardinalities are a floor, not a semantic intent classifier."""
    floors = {"additions": 0, "cuts": 0}
    patterns = [
        re.compile(
            rf"\b(?P<n>{_NUMBER})(?:[- ]cards?)?\s+(?:(?:distinct|independent|coherent)\s+)?(?P<kind>additions?|cuts?|swaps?|replacements?)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?P<kind>add|cut|remove|replace)\s+(?P<n>{_NUMBER})\b", re.IGNORECASE
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(request):
            clause_prefix = re.split(r"[.;!?]", request[: match.start()])[-1]
            if re.search(r"\b(?:not|never|don't|no)\b", clause_prefix, re.IGNORECASE):
                continue
            raw = match["n"].lower()
            count = int(raw) if raw.isdigit() else _WORDS[raw]
            kind = match["kind"].lower()
            suffix = request[match.end() :]
            if re.match(
                r"\s+(?:mana|counters?|damage|life|energy|poison|experience|charge|loyalty)\b",
                suffix,
                re.IGNORECASE,
            ) or re.match(
                r"\s+cards?\s+(?:from|in|into|to)\s+(?:(?:your|the|a|an|their)\s+)?(?:hand|graveyard|library|exile|battlefield)\b",
                suffix,
                re.IGNORECASE,
            ):
                continue
            alternative_for_one = kind in {"replacement", "replacements"} and re.match(
                r"\s+for\b", suffix, re.IGNORECASE
            )
            keys = (
                ("additions",)
                if kind in {"add", "addition", "additions"} or alternative_for_one
                else (
                    ("cuts",)
                    if kind in {"cut", "cuts", "remove"}
                    else ("additions", "cuts")
                )
            )
            for key in keys:
                floors[key] = max(floors[key], count)
    updates = {key: max(getattr(task, key), value) for key, value in floors.items()}
    disagreements = {
        key: {"model": getattr(task, key), "explicit_floor": value}
        for key, value in floors.items()
        if value > getattr(task, key)
    }
    independent = bool(
        re.search(
            r"\b(?:independent|separate|alternatives|options)\b", request, re.IGNORECASE
        )
    )
    explicit_package = bool(
        re.search(
            r"(?<!no )(?<!not a )\b(?:package|together|combined|coordinated|coherent)\b",
            request,
            re.IGNORECASE,
        )
    )
    if not task.coordinated and (
        explicit_package or (all(floors.values()) and not independent)
    ):
        updates["coordinated"] = True
        disagreements["coordinated"] = {"model": False, "explicit_floor": True}
    return task.model_copy(update=updates), disagreements


def validate_hypotheses(
    tools, hypotheses: list[ResearchHypothesis]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reuse current revision/legality/protected-card checks on isolated copies."""
    accepted, errors = [], []
    original = tools.check()
    for hypothesis in hypotheses:
        proposal = hypothesis.proposal.model_dump()
        try:
            preview = tools.experiments.validate_replacements(
                tools.owner,
                tools.deck_id,
                tools.revision,
                proposal["replacements"],
                tools.constraints,
            )
            tools.check()
        except ValueError as exc:
            errors.append(str(exc)[:300])
            continue
        accepted.append(
            {
                **hypothesis.model_dump(),
                "replacements": proposal["replacements"],
                "replacement_names": [
                    {
                        "outgoing": next(
                            c["name"]
                            for c in original["entries"]
                            if c["id"] == pair["outgoing_entry_id"]
                        ),
                        "incoming": next(
                            c["name"]
                            for c in preview["document"]["entries"]
                            if c.get("card_id") == pair["incoming_card_id"]
                        ),
                    }
                    for pair in proposal["replacements"]
                ],
                "status": "research_hypothesis",
                "joint_strategic_review": "not_performed",
                "measured": False,
                "revision": tools.revision,
                "deck_sha256": preview["deck_sha256"],
                "printed_sources": [
                    {
                        key: card.get(key)
                        for key in ("name", "mana_cost", "type_line", "oracle_text")
                    }
                    for card in preview["document"]["entries"]
                    if card.get("card_id")
                    in {p["incoming_card_id"] for p in proposal["replacements"]}
                ],
            }
        )
    return accepted, errors
