"""Conservative literal discovery contracts; ambiguous requests remain broad."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from sabermetrics.assistant_context_rank import positive_focus


class SearchGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    function: str | None = None
    card_type: str | None = None
    mana_production_required: bool = False
    prefer_low_mana: bool = False
    status: str = "unresolved"


_FUNCTIONS = {
    "mana": r"\bmana[- ]producing\b|\bmana dorks?\b|\bramp\b|\bproduce mana\b|\bmana rocks?\b",
    "draw": r"\bdraw(?:ing)?\b|\bcard advantage\b",
    "sacrifice": r"\b(?:sac|sacrifice) outlets?\b",
    "recursion": r"\brecursion\b|\breanimat\w*\b",
    "protection": r"\bprotection\b|\bprotect(?:ing)?\b",
    "interaction": r"\bremoval\b|\binteraction\b|\bcounterspells?\b",
    "selection": r"\bscry\b|\bsurveil\b|\bcard selection\b",
    "counters": r"\+1/\+1 counter|\bcounter sources?\b",
}
_TYPES = r"creatures?|artifacts?|enchantments?|instants?|sorceries|sorcery|lands?|planeswalkers?|battles?|permanents?"


def literal_goal_functions(positive_text: str) -> set[str]:
    """Shared literal vocabulary; callers supply positive, normalized scope."""
    return {
        function
        for function, pattern in _FUNCTIONS.items()
        if re.search(pattern, positive_text, re.IGNORECASE)
    }


def compile_search_goal(quote: str, request: str = "") -> SearchGoal:
    """Only literal positive discovery language can override a planner's filters.

    Never use model-authored labels as authority. Multiple objectives/types,
    exceptions and disjunctions keep retrieval broad rather than guessing scope.
    """
    action_pattern = r"\b(?:give|find|recommend|suggest|additional|more|options|need)\b"
    if not re.search(action_pattern, quote, re.IGNORECASE) and not (
        request
        and quote in request
        and re.search(action_pattern, request, re.IGNORECASE)
    ):
        return SearchGoal()
    normalized_quote = re.sub(
        r"\b(?:don't|do not|never) (?:recommend|find|suggest|give|add)\b|\bavoid(?: recommending)?\b",
        "don't want",
        quote,
        flags=re.IGNORECASE,
    )
    text = positive_focus(normalized_quote.replace("‑", "-").replace("–", "-"))
    functions = literal_goal_functions(text)
    if len(functions) != 1:
        return SearchGoal(status="ambiguous")
    function = next(iter(functions))
    # Ancillary questions about costs/drawbacks must not become extra card types.
    head = re.split(r"[.!?;]", text, maxsplit=1)[0]
    head = re.split(r"\b(?:for|using|in|with)\b", head, maxsplit=1)[0]
    nouns = {m[0] for m in re.finditer(r"\b(?:" + _TYPES + r")\b", head)}
    normalized = {"sorceries": "sorcery"}.get
    types = {normalized(noun, noun.rstrip("s")) for noun in nouns}
    card_type = next(iter(types)) if len(types) == 1 else None
    if re.search(r"\b(?:noncreature|non-creature|except|unless|or|without)\b", head):
        card_type = None
    # "Creature-heavy decks" describes the deck, not the desired card type.
    if re.search(
        r"\b(?:creature|artifact|enchantment|land)[- ](?:heavy|based)\b", head
    ):
        card_type = None
    preference_text = positive_focus(request or quote)
    cheap = bool(
        re.search(
            r"\bcheap(?:er)?\b|\blow(?:er)?[- ](?:casting[- ]cost|mana|cost)\b",
            preference_text,
        )
    )
    return SearchGoal(
        function=function,
        card_type=card_type,
        mana_production_required=bool(
            re.search(r"\bmana[- ]producing\b|\bproduce mana\b|\bmana dorks?\b", text)
        ),
        prefer_low_mana=cheap,
        status="literal_discovery_contract",
    )


def enforce_search_goal(
    values: dict[str, Any],
    goal: SearchGoal,
    request: str,
    preferences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    values = dict(values)
    if goal.function:
        values["function"] = goal.function
    if goal.card_type:
        values["type_line"] = goal.card_type
        values["allowed_type_groups"] = []
    if goal.mana_production_required:
        values["mana_production_required"] = True
    if goal.prefer_low_mana:
        values["prefer_low_mana"] = True
        prior_numeric = any(
            p.get("dimension") == "mana"
            and p.get("strength") == "required"
            and re.search(r"\d", str(p.get("value") or ""))
            for p in preferences or []
        )
        explicit_bound = re.search(
            r"\b(?:mana value|cmc)\s*(?:of\s*)?\d|\b(?:at most|up to|under|less than|no more than)\s+\d+\s+mana\b|\d+\s*(?:mana\s*)?or (?:less|fewer)\b|\bcost(?:s|ing)?\s+\d+\s+mana\b",
            request,
            re.IGNORECASE,
        )
        if not prior_numeric and not explicit_bound:
            values["mana_max"] = None
    return values


def produces_mana(card: dict[str, Any]) -> bool:
    """Literal own mana effects only; unknown/granted abilities do not qualify."""
    if card.get("card_faces") or card.get("oracle_text_truncated"):
        return False
    for line in str(card.get("oracle_text") or "").splitlines():
        if re.search(r"\b(?:has|have|gain|gains)\b.*:", line, re.IGNORECASE):
            continue
        effect = line.split(":", 1)[-1]
        if re.search(
            r"\badd (?:\{[0-9WUBRGCX/]+\}|(?:one|two|three|four|five|six|seven|\d+|x|that much|an amount of) mana)\b",
            effect,
            re.IGNORECASE,
        ):
            return True
        # A closing brace is not a word character; keep symbol matching separate.
        if re.search(r"\badd\s+\{[0-9WUBRGCX/]+\}", effect, re.IGNORECASE):
            return True
    return False


def meets_goal(card: dict[str, Any], goal: SearchGoal) -> bool:
    if goal.card_type:
        types = set(
            re.findall(
                r"[a-z]+", str(card.get("type_line") or "").split("—", 1)[0].lower()
            )
        )
        if goal.card_type == "permanent":
            if not types & {
                "creature",
                "artifact",
                "enchantment",
                "land",
                "planeswalker",
                "battle",
            }:
                return False
        elif goal.card_type not in types:
            return False
    if goal.function:
        if card.get("oracle_text_truncated") or not card.get("oracle_text"):
            return False
        from sabermetrics.assistant_functional_search import (
            _FUNCTIONS,
            _ROLE_FUNCTIONS,
            functional_profile,
        )

        profile = functional_profile(card)
        if goal.function == "draw" and not profile["draw_routes"]:
            return False
        if goal.function in _ROLE_FUNCTIONS and not profile["function_routes"].get(
            goal.function
        ):
            return False
        if goal.function in _FUNCTIONS and not _FUNCTIONS[goal.function].search(
            str(card.get("oracle_text") or "")
        ):
            return False
    return not goal.mana_production_required or produces_mana(card)
