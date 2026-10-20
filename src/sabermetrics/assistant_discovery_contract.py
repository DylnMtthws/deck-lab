"""Bounded discovery evidence and deterministic checks, never a prose safety verdict.

Facts must come from retrieved structured data. These helpers neither infer card
mechanics nor decide legality. A validated manifest says nothing about unrepresented
claims in free text; runtime must render it or separately review the text.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_DISCOVERY_CANDIDATES = 40
MAX_DISCOVERY_GROUPS = 8
Short = Annotated[str, Field(min_length=1, max_length=160)]
Detail = Annotated[str, Field(min_length=1, max_length=2000)]
FactKind = Literal[
    "mana_cost",
    "mana_value",
    "oracle_text",
    "type_line",
    "condition",
    "function",
    "citation",
]


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DiscoveryRequirement(DiscoveryModel):
    group_id: Short
    label: Short
    requested_count: int | None = Field(default=None, ge=1, le=99)
    request_quote: Detail


class DiscoveryFact(DiscoveryModel):
    fact_id: Short
    kind: FactKind
    value: Detail
    source_id: Short


class DiscoveryCandidate(DiscoveryModel):
    card_id: Short
    name: Short
    rank: int = Field(default=1, ge=1, le=999)
    in_deck: bool = False
    source_facts: list[DiscoveryFact] = Field(default_factory=list, max_length=24)
    function_relevance: list[Detail] = Field(default_factory=list, max_length=8)
    conditions: list[Detail] = Field(default_factory=list, max_length=8)
    costs: list[Detail] = Field(default_factory=list, max_length=8)
    citations: list[Short] = Field(default_factory=list, max_length=16)
    group_ids: list[Short] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_facts(self):
        if len({fact.fact_id for fact in self.source_facts}) != len(self.source_facts):
            raise ValueError("Fact IDs must be unique within each card")
        return self


class DiscoveryLedger(DiscoveryModel):
    candidates: list[DiscoveryCandidate] = Field(
        default_factory=list, max_length=MAX_DISCOVERY_CANDIDATES
    )

    @model_validator(mode="after")
    def unique_cards(self):
        if len({card.card_id for card in self.candidates}) != len(self.candidates):
            raise ValueError("Ledger card IDs must be unique")
        return self


class DiscoveryAnswerGroup(DiscoveryModel):
    group_id: Short
    claimed_count: int | None = Field(default=None, ge=0, le=99)
    card_ids: list[Short] = Field(
        default_factory=list, max_length=MAX_DISCOVERY_CANDIDATES
    )


class DiscoveryClaim(DiscoveryFact):
    card_id: Short


class DiscoveryNote(DiscoveryModel):
    """Bounded candidate-specific prose; source checking remains separate."""

    card_id: Short
    note: Annotated[str, Field(min_length=8, max_length=400)]


class DiscoveryAnswer(DiscoveryModel):
    groups: list[DiscoveryAnswerGroup] = Field(
        default_factory=list, max_length=MAX_DISCOVERY_GROUPS
    )
    claims: list[DiscoveryClaim] = Field(default_factory=list, max_length=160)
    notes: list[DiscoveryNote] = Field(default_factory=list, max_length=40)


class DiscoveryIssue(DiscoveryModel):
    code: Short
    group_id: Short | None = None
    card_id: Short | None = None
    detail: Detail


class DiscoveryValidation(DiscoveryModel):
    complete: bool
    issues: list[DiscoveryIssue]
    grounded_card_ids: list[Short]
    scope: str = (
        "Structured manifest checks only; notes, other free text and Magic legality are not certified."
    )


_NUMBERS = {
    word: i
    for i, word in enumerate(
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
            "eleven",
            "twelve",
        ]
    )
}
_NUMBER = (
    r"(?:[1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
)
_COUNTED = re.compile(
    rf"\b(?P<count>{_NUMBER})\s+(?:(?:ranked|best|additional|more|other|new|distinct)\s+)*(?P<label>(?:[\w-]+\s+){{0,4}}?(?:cards?|candidates?|options?|recommendations?|picks?|suggestions?|outlets?|enablers?|payoffs?|engines?|finishers?|replacements?))\b",
    re.IGNORECASE,
)


def derive_discovery_requirements(
    request: str,
    previous_requirements: list[DiscoveryRequirement] | None = None,
) -> list[DiscoveryRequirement]:
    """Extract explicit list sizes, keeping capacity distinct from an obligation.

    This conservative parser supplies cardinalities, not semantic classification.
    Model-planned uncounted groups can be added by the caller with a request quote.
    """
    requirements: list[DiscoveryRequirement] = []
    references_existing_set = False
    for match in _COUNTED.finditer(request):
        prefix = re.split(r"[.;!?]", request[: match.start()])[-1]
        suffix = request[match.end() :]
        if re.search(r"\b(?:these|those|my|our)\s*$", prefix, re.IGNORECASE):
            references_existing_set = True
            continue
        if re.search(r"\blists?\b", match["label"], re.IGNORECASE) or re.match(
            r"\s+lists?\b", suffix, re.IGNORECASE
        ):
            continue
        if re.search(r"\b(?:not|never|don't|no)\b", prefix, re.IGNORECASE):
            continue
        if re.search(
            r"\b(?:beyond|besides|except|excluding|keep|retain)\s+(?:(?:these|those|the|my)\s+)?$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        # Capacity, existing inventory and rules text are not requested list sizes.
        if re.search(
            r"\b(?:have|has|with|contains?|remaining|room for)\s+(?:only\s+)?$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        if re.match(
            r"\s+(?:in (?:my|the|this) deck|from (?:your |the )?(?:hand|graveyard|library)|slots?\b|(?:that )?(?:I|we|you) (?:added|found|listed|picked|suggested|recommended|discovered)|(?:already|previously)\b)",
            suffix,
            re.IGNORECASE,
        ):
            references_existing_set = True
            continue
        if not re.search(
            r"\b(?:give|show|list|find|suggest|recommend|want|need|identify|research|offer|provide)\b",
            prefix,
            re.IGNORECASE,
        ):
            if re.search(r"\b(?:compare|choose|pick|rank)\b", prefix, re.IGNORECASE):
                references_existing_set = True
            continue
        raw = match["count"].lower()
        requirements.append(
            DiscoveryRequirement(
                group_id=f"list_{len(requirements) + 1}",
                label=match["label"].strip()[:160],
                requested_count=int(raw) if raw.isdigit() else _NUMBERS[raw],
                request_quote=match.group(0),
            )
        )
        if len(requirements) == MAX_DISCOVERY_GROUPS:
            break
    if (
        not requirements
        and not references_existing_set
        and previous_requirements
        and (
            revises_discovered_candidates(request)
            or re.search(
                r"\b(?:revise|narrow|relax|broaden)\b.{0,80}\b(?:criteria|constraints|requirements|lists?|search|roles?)\b",
                request,
                re.IGNORECASE,
            )
        )
    ):
        return previous_requirements[:MAX_DISCOVERY_GROUPS]
    return requirements


def revises_discovered_candidates(request: str) -> bool:
    """Recognize explicit reuse of a preceding candidate set without adding picks."""
    return bool(
        re.search(
            r"\b(?:re[- ]?rank|re[- ]?order|revise|revisit|reconsider|rank|compare|prioritize|choose|pick)\b.{0,100}\b(?:those|these|previous|earlier|existing|discovered|your picks|your recommendations|the candidates)\b|\b(?:those|these|previous|earlier)\b.{0,60}\b(?:candidates|picks|recommendations|options)\b.{0,60}\b(?:rank|priority|order)\b",
            request,
            re.IGNORECASE,
        )
    )


def merge_discovery_ledger(
    existing: DiscoveryLedger, incoming: DiscoveryLedger
) -> DiscoveryLedger:
    """Prioritize current retrievals, then interleave bounded query/role groups.

    Membership must be refreshed from the current deck by the caller on each turn.
    """
    cards = {card.card_id: card for card in existing.candidates}
    for card in incoming.candidates:
        previous = cards.get(card.card_id)
        if previous is not None:
            facts = {fact.fact_id: fact for fact in previous.source_facts}
            facts.update({fact.fact_id: fact for fact in card.source_facts})
            card = card.model_copy(
                update={
                    "source_facts": list(facts.values())[:24],
                    "citations": list(
                        dict.fromkeys(previous.citations + card.citations)
                    )[:16],
                    "group_ids": list(
                        dict.fromkeys(previous.group_ids + card.group_ids)
                    )[:8],
                    "conditions": card.conditions or previous.conditions,
                    "costs": card.costs or previous.costs,
                    "function_relevance": card.function_relevance
                    or previous.function_relevance,
                }
            )
        cards[card.card_id] = card
    fresh_ids = {card.card_id for card in incoming.candidates}
    ranked = sorted(
        cards.values(),
        key=lambda card: (card.card_id not in fresh_ids, card.rank, card.card_id),
    )
    # Interleave explicit roles so a broad, prolific search cannot evict a scarce
    # requested role. Unassigned candidates participate as their own bucket.
    selected: dict[str, DiscoveryCandidate] = {}
    for fresh in (True, False):
        buckets: dict[str, list[DiscoveryCandidate]] = {}
        for card in ranked:
            if (card.card_id in fresh_ids) != fresh:
                continue
            for group_id in card.group_ids or ["unassigned"]:
                buckets.setdefault(group_id, []).append(card)
        while any(buckets.values()) and len(selected) < MAX_DISCOVERY_CANDIDATES:
            for bucket in buckets.values():
                while bucket and bucket[0].card_id in selected:
                    bucket.pop(0)
                if bucket and len(selected) < MAX_DISCOVERY_CANDIDATES:
                    card = bucket.pop(0)
                    selected[card.card_id] = card
    return DiscoveryLedger(candidates=list(selected.values()))


def candidate_from_card(
    card: dict[str, Any],
    *,
    rank: int = 1,
    in_deck: bool | None = None,
    group_ids: list[str] | None = None,
) -> DiscoveryCandidate:
    """Adapt retrieved catalog fields, without inferring mechanics or legality.

    Citation IDs identify the supplied tool record; they are not invented external
    URLs. The caller must supply retrieval output rather than a model's guesses.
    """
    card_id = str(card.get("id") or card.get("card_id") or card.get("oracle_id") or "")
    source_id = ("card:" + card_id)[:160]
    facts = []
    fields: list[tuple[str, FactKind]] = [
        ("mana_cost", "mana_cost"),
        ("cmc", "mana_value"),
        ("type_line", "type_line"),
        ("oracle_text", "oracle_text"),
    ]
    for key, kind in fields:
        value = card.get(key)
        if value is not None and str(value):
            facts.append(
                DiscoveryFact(
                    fact_id=key, kind=kind, value=str(value)[:2000], source_id=source_id
                )
            )
    return DiscoveryCandidate(
        card_id=card_id,
        name=str(card.get("name") or ""),
        rank=rank,
        in_deck=(
            bool(card.get("already_in_library") or card.get("in_deck"))
            if in_deck is None
            else in_deck
        ),
        source_facts=facts,
        citations=[source_id] if facts else [],
        function_relevance=list(card.get("function_relevance") or [])[:8],
        conditions=list(card.get("conditions") or [])[:8],
        costs=list(card.get("costs") or [])[:8],
        group_ids=group_ids or [],
    )


def validate_discovery_prose_counts(content: str) -> list[DiscoveryIssue]:
    """Check explicit counted headings followed by flat Markdown lists only.

    This intentionally makes no claims about prose without that exact structure,
    candidate relevance, printed mechanics, source accuracy, or legality.
    """
    lines = content.splitlines()
    issues = []
    for index, line in enumerate(lines):
        if re.match(r"\s*(?:[-*+] |\d+[.)] )", line):
            continue
        counts = list(_COUNTED.finditer(line))
        if len(counts) != 1:
            continue
        items = []
        first_indent: int | None = None
        for following in lines[index + 1 :]:
            if not following.strip():
                continue
            bullet = re.match(r"^( *)(?:[-*+] |\d+[.)] )(.+)$", following)
            if bullet:
                indent = len(bullet[1])
                if first_indent is None:
                    first_indent = indent
                if indent == first_indent:
                    items.append(bullet[2].strip())
                elif indent < first_indent:
                    break
            elif items or following.lstrip().startswith("#"):
                break
            else:
                break
        if not items:
            continue
        raw = counts[0]["count"].lower()
        expected = int(raw) if raw.isdigit() else _NUMBERS[raw]
        observed = len(set(items))
        if expected != observed:
            issues.append(
                DiscoveryIssue(
                    code="claimed_count_mismatch",
                    detail=f"Heading claims {expected} {counts[0]['label']}; following Markdown list has {observed} distinct entries.",
                )
            )
    return issues


def validate_discovery_answer(
    requirements: list[DiscoveryRequirement],
    ledger: DiscoveryLedger,
    answer: DiscoveryAnswer,
    *,
    excluded_card_ids: set[str] | None = None,
) -> DiscoveryValidation:
    """Check manifest counts, membership, exact fact values and source attribution.

    Return individually grounded candidates for an honest partial when a bounded
    repair cannot fill every requested list. No supplied fact is treated as proof
    of an unrepresented prose claim.
    """
    issues: list[DiscoveryIssue] = []
    cards = {card.card_id: card for card in ledger.candidates}
    excluded = excluded_card_ids or set()
    groups: dict[str, DiscoveryAnswerGroup] = {}
    invalid_cards: set[str] = set()
    listed: list[str] = []

    def issue(
        code: str, detail: str, group_id: str | None = None, card_id: str | None = None
    ) -> None:
        issues.append(
            DiscoveryIssue(
                code=code, detail=detail[:2000], group_id=group_id, card_id=card_id
            )
        )

    for group in answer.groups:
        if group.group_id in groups:
            issue(
                "duplicate_group", "Use one list per requested group.", group.group_id
            )
        groups[group.group_id] = group
        unique = set(group.card_ids)
        if len(unique) != len(group.card_ids):
            issue(
                "duplicate_card",
                "Repeated cards do not count as additional candidates.",
                group.group_id,
            )
        if group.claimed_count is not None and group.claimed_count != len(unique):
            issue(
                "claimed_count_mismatch",
                f"Claimed {group.claimed_count}; listed {len(unique)} distinct cards.",
                group.group_id,
            )
        for card_id in group.card_ids:
            if card_id not in listed:
                listed.append(card_id)
            card = cards.get(card_id)
            if card_id in excluded:
                invalid_cards.add(card_id)
                issue(
                    "previously_recommended",
                    "This candidate was previously recommended; supply an additional candidate for this request.",
                    group.group_id,
                    card_id,
                )
            if card is None:
                invalid_cards.add(card_id)
                issue(
                    "unknown_card",
                    "Retrieve this candidate before recommending it.",
                    group.group_id,
                    card_id,
                )
            elif card.in_deck:
                invalid_cards.add(card_id)
                issue(
                    "already_in_deck",
                    "This card is already in the current deck; choose an additional candidate.",
                    group.group_id,
                    card_id,
                )
            elif not card.source_facts:
                invalid_cards.add(card_id)
                issue(
                    "missing_card_facts",
                    "Retrieve printed facts for this candidate.",
                    group.group_id,
                    card_id,
                )

    for requirement in requirements:
        required_group = groups.get(requirement.group_id)
        observed = (
            len(set(required_group.card_ids) - invalid_cards) if required_group else 0
        )
        if required_group is None:
            issue(
                "missing_group",
                f"Supply the requested {requirement.label} list.",
                requirement.group_id,
            )
        elif observed == 0:
            issue(
                "empty_group",
                f"No retrieved additional candidates supplied for {requirement.label}; this list remains incomplete.",
                requirement.group_id,
            )
        if (
            requirement.requested_count is not None
            and observed != requirement.requested_count
        ):
            issue(
                "requested_count_mismatch",
                f"Requested {requirement.requested_count} {requirement.label}; {observed} retrieved, additional distinct cards supplied.",
                requirement.group_id,
            )

    noted: set[str] = set()
    for note in answer.notes:
        if note.card_id in noted:
            issue(
                "duplicate_note",
                "Provide one note per selected candidate.",
                card_id=note.card_id,
            )
        noted.add(note.card_id)
        if note.card_id not in cards:
            issue(
                "unknown_note_card",
                "Notes must reference retrieved candidate IDs.",
                card_id=note.card_id,
            )
        if note.card_id not in listed:
            issue(
                "unselected_note",
                "Notes must reference a card selected in a discovery group.",
                card_id=note.card_id,
            )

    for claim in answer.claims:
        card = cards.get(claim.card_id)
        fact = (
            next(
                (fact for fact in card.source_facts if fact.fact_id == claim.fact_id),
                None,
            )
            if card
            else None
        )
        if fact is None or (fact.kind, fact.value, fact.source_id) != (
            claim.kind,
            claim.value,
            claim.source_id,
        ):
            invalid_cards.add(claim.card_id)
            issue(
                "unsupported_claim",
                f"Replace {claim.kind} claim with the retrieved fact and its exact source ID.",
                card_id=claim.card_id,
            )
        elif card is not None and claim.source_id not in card.citations:
            invalid_cards.add(claim.card_id)
            issue(
                "unsupported_citation",
                "The claim's source ID is absent from this candidate's retrieved citations.",
                card_id=claim.card_id,
            )
    return DiscoveryValidation(
        complete=not issues,
        issues=issues,
        grounded_card_ids=[
            card_id for card_id in listed if card_id not in invalid_cards
        ],
    )


def prior_recommendations(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Return selected retrieved identities from the last 100 supplied messages.

    This is a transient novelty scope, never a user rejection. Only selections
    intersecting a typed ledger on that same assistant receipt are admitted;
    prose, user text, and selected IDs absent from retrieved records are ignored.
    """
    recommended: dict[str, str] = {}
    for message in messages[-100:]:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        research = message.get("research")
        if not isinstance(research, dict):
            presentation = message.get("presentation")
            research = (
                presentation.get("research") if isinstance(presentation, dict) else None
            )
        if not isinstance(research, dict):
            continue
        manifest = research.get("selected_candidates") or research.get(
            "discovery_answer"
        )
        try:
            selected = DiscoveryAnswer.model_validate(manifest)
        except (TypeError, ValidationError):
            continue
        retrieved: dict[str, str] = {}
        session_state = research.get("session_state")
        session_ledger = (
            session_state.get("ledger") if isinstance(session_state, dict) else None
        )
        for raw_ledger in (research.get("discovery_ledger"), session_ledger):
            try:
                ledger = DiscoveryLedger.model_validate(raw_ledger)
            except (TypeError, ValidationError):
                continue
            retrieved.update(
                {
                    card.card_id: card.name
                    for card in ledger.candidates
                    if card.source_facts
                }
            )
        for group in selected.groups:
            for card_id in group.card_ids:
                if card_id in retrieved:
                    recommended[card_id] = retrieved[card_id]
    return recommended


def render_discovery_groups(
    requirements: list[DiscoveryRequirement],
    ledger: DiscoveryLedger,
    answer: DiscoveryAnswer,
    *,
    fit_reasons: dict[str, str] | None = None,
) -> list[str]:
    """Render one complete candidate per line with retrieved names and mana costs.

    Callers must run manifest validation and source-check the returned prose.
    Notes are not verified here. Unretrieved identities are omitted rather than
    invented; duplicate note bodies are omitted pending a bounded repair.
    """
    labels = {row.group_id: row.label for row in requirements}
    cards = {row.card_id: row for row in ledger.candidates}
    notes: dict[str, str] = {}
    repeated: set[str] = set()
    for note_row in answer.notes:
        if note_row.card_id in notes:
            repeated.add(note_row.card_id)
        notes[note_row.card_id] = " ".join(note_row.note.split())
    rendered = []
    for group in answer.groups:
        label = " ".join(labels.get(group.group_id, "Candidates").split())
        seen: set[str] = set()
        for card_id in group.card_ids:
            card = cards.get(card_id)
            if card is None or card_id in seen:
                continue
            seen.add(card_id)
            mana_cost = next(
                (fact.value for fact in card.source_facts if fact.kind == "mana_cost"),
                None,
            )
            row = f"{label}: {card.name}"
            row += (
                f" ({mana_cost})"
                if mana_cost is not None
                else " (mana cost not retrieved)"
            )
            if card_id in notes and card_id not in repeated:
                row += f" — {notes[card_id]}"
            if fit_reasons and card_id in fit_reasons:
                row += " " + fit_reasons[card_id]
            rendered.append(row)
    return rendered
