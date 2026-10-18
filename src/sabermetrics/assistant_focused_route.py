"""Conservative zero-call routing for printed facts and retained session recaps.

This helper supplies no answer and grants no action or legality authority. A
request outside the narrow complete grammars keeps the ordinary planner route.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sabermetrics.assistant_conversation_context import SessionFinding

FactField = Literal["mana_cost", "mana_value", "oracle_text", "type_line", "card_facts"]


class FocusedRoutePlan(BaseModel):
    """A bounded route decision; canonical names come only from supplied records."""

    model_config = ConfigDict(extra="forbid", strict=True)
    route: Literal["named_card_facts", "session_summary", "fallback"]
    named_cards: list[str] = Field(default_factory=list, max_length=1)
    reason: str = Field(min_length=1, max_length=300)
    fact_field: FactField | None = None
    retained_findings_count: int = Field(default=0, ge=0, le=32)


_FACT_PATTERNS: list[tuple[FactField, str]] = [
    ("mana_cost", r"what(?: is|'s) (?:the )?mana cost of (?P<name>.+)"),
    ("mana_cost", r"how much mana does (?P<name>.+) cost"),
    ("mana_value", r"what(?: is|'s) (?:the )?(?:mana value|cmc) of (?P<name>.+)"),
    ("type_line", r"what(?: is|'s) (?:the )?(?:card type|type line) of (?P<name>.+)"),
    ("oracle_text", r"what does (?P<name>.+) do"),
    (
        "oracle_text",
        r"(?:show|quote|give me) (?:the )?(?:oracle|printed) text (?:of|for) (?P<name>.+)",
    ),
    ("card_facts", r"(?:tell me about|describe) (?P<name>.+)"),
]
_SUMMARY = re.compile(
    r"(?:summari[sz]e|recap)(?: (?:our|the|this))? (?:session|conversation|findings|research|discussion)"
    r"(?: so far)?|(?:summari[sz]e|recap) what we(?:'ve| have)? (?:found|learned|discussed)(?: so far)?"
    r"|what have we (?:found|learned|discussed)(?: so far)?",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    return " ".join(value.replace("’", "'").strip().split()).casefold()


def focused_route(
    request: str,
    authoritative_cards: list[dict[str, Any]],
    *,
    session_findings: list[dict[str, Any]] | None = None,
) -> FocusedRoutePlan:
    """Route only a complete factual question or explicit evidence-backed recap.

    Supplied cards must be actual catalog/deck records, and findings must come
    from the session's provenance-checked retained evidence. Validation here
    checks their schema, not the truth of claims or evidence entailment.
    """
    if not request.strip() or len(request) > 600:
        return FocusedRoutePlan(
            route="fallback",
            reason="Request is empty or exceeds the focused route bound.",
        )
    text = request.strip().rstrip(".?!").strip()
    text = re.sub(r"^please\s+", "", text, flags=re.IGNORECASE)
    if _SUMMARY.fullmatch(text):
        valid_findings = []
        for row in (session_findings or [])[-32:]:
            try:
                valid_findings.append(SessionFinding.model_validate(row))
            except (ValidationError, TypeError):
                continue
        if valid_findings:
            return FocusedRoutePlan(
                route="session_summary",
                reason="Explicit recap with retained structured session evidence.",
                retained_findings_count=len(valid_findings),
            )
        return FocusedRoutePlan(
            route="fallback", reason="Recap has no valid retained structured findings."
        )

    for field, pattern in _FACT_PATTERNS:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        requested_name = match["name"].strip().strip('"“”')
        matches = [
            card
            for card in authoritative_cards
            if isinstance(card.get("name"), str)
            and _normalize(card["name"]) == _normalize(requested_name)
        ]
        if not matches:
            return FocusedRoutePlan(
                route="fallback",
                reason="The exact named card is absent from authoritative supplied records.",
            )
        # Duplicate records are allowed only when every relevant printed fact and
        # identity agree; distinct printings/face ambiguity retain normal planning.
        signatures = {
            tuple(
                str(card.get(key) or "")
                for key in (
                    "oracle_id",
                    "name",
                    "mana_cost",
                    "cmc",
                    "mana_value",
                    "type_line",
                    "oracle_text",
                )
            )
            for card in matches
        }
        if len(signatures) != 1:
            return FocusedRoutePlan(
                route="fallback",
                reason="The supplied named-card records disagree or are ambiguous.",
            )
        card = matches[0]
        if field == "mana_value":
            present = card.get("mana_value") is not None or card.get("cmc") is not None
        elif field in {"oracle_text", "card_facts"}:
            present = isinstance(card.get("oracle_text"), str) and bool(
                card["oracle_text"].strip()
            )
        else:
            present = isinstance(card.get(field), str) and bool(card[field].strip())
        if not present:
            return FocusedRoutePlan(
                route="fallback",
                reason="The requested printed fact is missing from supplied authoritative records.",
            )
        return FocusedRoutePlan(
            route="named_card_facts",
            named_cards=[card["name"]],
            fact_field=field,
            reason="Single exact supplied card and a complete printed-fact question.",
        )
    return FocusedRoutePlan(
        route="fallback",
        reason="Request requires ordinary intent planning beyond the focused factual or recap grammar.",
    )
