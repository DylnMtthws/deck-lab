"""Request-specific, application-owned strategic evidence; no model calls.

Statuses describe the stated proposition, never an overall card-quality verdict.
References are exact character spans into content-hashed source fields. Detection
failure and unavailable context are distinct; neither proves resource absence.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant_ability_clauses import ability_clauses, card_identity
from sabermetrics.assistant_context_rank import positive_focus, requested_functions
from sabermetrics.assistant_strategy import draw_routes

VERSION = "assistant-strategic-obligations.v1"
Status = Literal["established", "conditional", "unknown", "contradicted"]
Gap = Literal["context_omitted", "support_not_found", "unsupported_profile"]


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    field: str
    sha256: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class StrategicObligation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dimension: str
    proposition: str
    status: Status
    evidence_refs: list[int] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    gap: Gap | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class StrategicObligations(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = VERSION
    card_id: str
    request_priorities: list[str]
    obligations: list[StrategicObligation]
    evidence: list[EvidenceReference]
    # Ordinal research priorities, not probabilities or a summed quality score.
    ranking: dict[str, int]
    mechanism: list[str]
    coverage: dict[str, Any]


_REPEAT = re.compile(
    r"repeat|recurr|sustain|engine|every turn|each turn|future use|long[ -]game",
    re.IGNORECASE,
)
_IMMEDIATE = re.compile(
    r"immediate|first[ -]use|right away|up[ -]front|burst|one[ -]shot", re.IGNORECASE
)
_NUMBERS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_N = r"(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+)"


def _number(text: str) -> int:
    return int(text) if text.isdigit() else _NUMBERS[text.casefold()]


def request_priorities(request: str) -> list[str]:
    """Conservative lexical intent; unsupported wording keeps balanced fallback."""
    original = request
    request = positive_focus(
        re.sub(
            r"\b(?:don't|do not) prioritize\b",
            "do not want",
            request,
            flags=re.IGNORECASE,
        )
    )
    priorities: list[str] = []
    # Preserve explicit request order when both concerns occur.
    hits = [
        (m.start(), name)
        for pattern, name in ((_REPEAT, "future_uses"), (_IMMEDIATE, "first_use"))
        if (m := pattern.search(request))
    ]
    priorities.extend(name for _, name in sorted(hits))
    if re.search(r"\bnet\b|\badvantage\b|\bdraw(?:ing)?\b", request, re.IGNORECASE):
        priorities.append("net_advantage")
    if re.search(r"divers|different|variet", request, re.IGNORECASE):
        priorities.append("mechanism_diversity")
    if re.search(r"\b(?:cut|replace|swap)\b", original, re.IGNORECASE):
        priorities.append("lost_roles")
    return priorities or ["balanced"]


def build_strategic_obligations(
    card: dict[str, Any],
    request: str = "",
    *,
    suitability: dict[str, Any] | None = None,
    packet: dict[str, Any] | None = None,
    deck_entries: list[dict[str, Any]] | None = None,
) -> StrategicObligations:
    """Build without re-analyzing a joint cut or assuming omitted sources absent.

    Pass the existing *prospective* packet for package recommendations. Its
    compatibility and losses take precedence over pre-cut suitability. A compact
    packet cannot discharge requirements whose sources/clauses it omitted.
    """
    suitability = suitability or card.get("suitability") or {}
    packet = packet if packet is not None else suitability.get("recommendation_packet")
    raw = str(card.get("oracle_text") or "")
    parsed = ability_clauses(card)
    complete = bool(raw) and parsed["coverage"] != "unknown" and len(raw) <= 6000
    evidence: list[EvidenceReference] = []
    rows: list[StrategicObligation] = []

    def ref(
        source: dict[str, Any], field: str = "oracle_text", quote: str | None = None
    ) -> list[int]:
        text = str(source.get(field) or "")
        if not text or (quote is not None and quote not in text):
            return []
        start = text.index(quote) if quote is not None else 0
        item = EvidenceReference(
            source_id=card_identity(source),
            field=field,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            start=start,
            end=start + len(quote if quote is not None else text),
        )
        if item not in evidence:
            evidence.append(item)
        return [evidence.index(item)]

    printed = ref(card)

    def add(
        dimension: str,
        proposition: str,
        status: Status,
        *,
        refs: list[int] | None = None,
        conditions: list[str] | None = None,
        gap: Gap | None = None,
        **facts: Any,
    ) -> None:
        rows.append(
            StrategicObligation(
                dimension=dimension,
                proposition=proposition,
                status=status,
                evidence_refs=printed if refs is None else refs,
                conditions=conditions or [],
                gap=gap,
                facts=facts,
            )
        )

    clauses = []
    for clause in parsed["clauses"] if complete else []:
        # Ability words are nonmechanical labels before an em dash. The shared
        # line parser recognizes triggers only at the start; recover this bounded
        # printed format without changing the original source/evidence span.
        labeled_trigger = re.fullmatch(
            r"[A-Za-z][A-Za-z -]* — ((?:When|Whenever|At the beginning)[^,]*,)\s*(.*)",
            clause["source_quote"],
            re.IGNORECASE,
        )
        if clause["kind"] == "static_or_spell" and labeled_trigger:
            clause = {
                **clause,
                "kind": "triggered",
                "triggers": [labeled_trigger[1]],
                "effects": [labeled_trigger[2]],
            }
        clauses.append(clause)
    routes = draw_routes(card) if complete else []
    functions = requested_functions(request)
    # The context ranker's vocabulary is a recall aid, not an exhaustive goal
    # parser. Extra explicit functions here only cause abstention; they never
    # grant a card relevance. This also keeps an ancillary "preserve draw"
    # clause from reinterpreting a token/counter/selection request as draw-only.
    focus = positive_focus(request)
    from sabermetrics.assistant_search_goal import literal_goal_functions

    functions.update(literal_goal_functions(focus))
    for function, pattern in (
        ("tokens", r"\btokens?\b"),
        ("counters", r"\bcounters?\b"),
        ("selection", r"\bscry\b|\bsurveil\b|\bcard selection\b"),
        ("life", r"\blife\b|\blifegain\b"),
    ):
        if re.search(pattern, focus):
            functions.add(function)
    # These obligations currently model draw economics. They cannot adjudicate
    # mana output, land deployment, interaction, or multi-function tradeoffs.
    # In particular, ancillary draw on a mana rock must not dominate ramp.
    draw_rank_applicable = functions == {"draw"}
    ranking_scope = "draw_only" if draw_rank_applicable else "abstain"
    add(
        "request_relevance",
        "Printed draw evidence is applicable to the requested function.",
        "conditional" if draw_rank_applicable and routes else "unknown",
        conditions=[
            "Draw-only ordering does not establish other requested functions or overall quality."
        ],
        gap=None if draw_rank_applicable and routes else "unsupported_profile",
        requested_functions=sorted(functions),
        draw_route_detected=bool(routes),
        ranking_scope=ranking_scope,
    )
    # Bind use classification to relevant draw clauses when present; another
    # activated ability on an ETB draw creature does not make its draw repeatable.
    relevant = [
        c for c in clauses if re.search(r"\bdraw\b", c["source_quote"], re.IGNORECASE)
    ] or clauses
    activations = [c for c in relevant if c["kind"] == "activated"]
    triggers = [c for c in relevant if c["kind"] == "triggered"]
    costs = [cost for c in activations for cost in c["costs"]]
    cost_text = " ".join(costs)
    effect_text = " ".join(effect for c in relevant for effect in c["effects"])
    # Triggered payments and losses occur in effects, not activation costs.
    # These are resource obligations, not proof that a conditional payment occurs.
    action = r"(?:^|[.,;]\s*|\b(?:and|then)\s+)(?:you (?:may )?)?"
    effect_resources = {
        resource
        for resource, pattern in (
            ("permanent", r"sacrifice\b"),
            ("hand_card", r"discard\b"),
            ("counter", r"remove\b[^.]*\bcounters? from\b"),
            ("life", r"pay\b[^.]*\blife\b"),
        )
        if any(
            re.search(action + pattern, effect, re.IGNORECASE)
            for c in relevant
            for effect in c["effects"]
        )
    }
    resources = sorted(
        {r["resource"] for c in activations for r in c["consumed_resources"]}
        | effect_resources
    )
    if re.search(
        r"\{(?:[1-9][0-9]*|[WUBRGCX/PS]+)\}|\bpay [^.]*mana\b", cost_text, re.IGNORECASE
    ):
        resources.append("mana")
    spell = bool(
        re.search(
            r"\b(?:Instant|Sorcery)\b", str(card.get("type_line") or ""), re.IGNORECASE
        )
    )
    self_name = re.escape(str(card.get("name") or "__unnamed__"))
    self_sacrifice = (
        rf"\bsacrifice (?:{self_name}|this (?:creature|artifact|permanent))\b"
    )
    self_loss_possible = bool(re.search(self_sacrifice, effect_text, re.IGNORECASE))
    self_spent = bool(re.search(self_sacrifice, cost_text, re.IGNORECASE)) or any(
        re.search(self_sacrifice, effect, re.IGNORECASE)
        and not re.search(
            r"\b(?:if|unless|may|can't|cannot)\b", c["source_quote"], re.IGNORECASE
        )
        for c in relevant
        for effect in c["effects"]
    )
    limited = bool(
        re.search(
            r"\bexhaust\b|only once\b(?! (?:each|per|a) turn)", raw, re.IGNORECASE
        )
    )

    def self_trigger(event: str) -> bool:
        return (
            bool(triggers)
            and not activations
            and all(
                re.search(
                    rf"^(?:When|Whenever) (?:{self_name}|this (?:creature|artifact|permanent)) {event}\b",
                    " ".join(c["triggers"]),
                    re.IGNORECASE,
                )
                for c in triggers
            )
        )

    enter_trigger = self_trigger("enters")
    self_death = self_trigger("dies")
    needs_reset = self_spent or limited or enter_trigger or self_death
    recurrent = (
        bool(activations or triggers) and not needs_reset and not self_loss_possible
    )
    setup = sorted(
        {
            p
            for r in routes
            for p in r.prerequisites
            if not p.startswith("activation_cost:") and p != "cast_and_resolve"
        }
    )
    activation_costs = costs
    first_conditions = ["Casting, timing and printed costs must be satisfied."]
    if setup:
        first_conditions.append(
            "Printed trigger/target/conditional prerequisites must be met."
        )
    if "tap" in resources:
        first_conditions.append(
            "Source must be untapped; creature tap costs require continuous control or haste."
        )
    add(
        "first_use",
        "A first use is available under the printed prerequisites.",
        "conditional" if complete else "unknown",
        conditions=first_conditions,
        gap=None if complete else "unsupported_profile",
        activation_costs=activation_costs,
    )
    future_conditions = ["Further uses require new events and payment of all costs."]
    if self_spent or spell or enter_trigger or self_death:
        future_conditions.append(
            "Source must return/recast or reenter; that does not itself renew counters."
        )
    elif self_loss_possible:
        future_conditions.append(
            "The effect may consume its source; further uses can require returning or recasting it."
        )
    if "tap" in resources:
        future_conditions.append("Further activations require untapping.")
    if resources:
        future_conditions.append(
            "Consumed resources need independent replenishment; no loop is established."
        )
    add(
        "future_uses",
        "The printed mechanism supports further uses without an unproved reset.",
        (
            "unknown"
            if not complete
            else (
                "conditional"
                if recurrent
                else ("contradicted" if spell or needs_reset else "unknown")
            )
        ),
        conditions=future_conditions,
        gap="unsupported_profile" if not complete else None,
    )

    # Packet requirements belong to the prospective composition. Never restore
    # pre-cut providers when a packet was supplied, including a compact packet.
    no_cut_context = bool(
        packet
        and packet.get("view") == "compact"
        and packet.get("changes", {}).get("status") == "no_cut"
        and not packet.get("changes", {}).get("support_context")
        and deck_entries is not None
    )
    compatibility = (
        packet.get("ability_compatibility", {})
        if packet is not None and not no_cut_context
        else suitability.get("ability_compatibility", {})
    )
    requirements = compatibility.get("requirements", [])
    support_refs: list[int] = []
    support_matches: list[bool] = []
    missing_provider_context = False
    named_support: list[str] = []
    sources = list(
        packet.get("deck_sources", [])
        if packet and not no_cut_context
        else deck_entries or []
    )
    for requirement in requirements:
        matched_requirement = False
        for provider in requirement.get("providers", []):
            source = next(
                (
                    s
                    for s in sources
                    if card_identity(s)
                    == str(
                        provider.get("oracle_id")
                        or provider.get("card_id")
                        or provider.get("name")
                    )
                    or s.get("name") == provider.get("name")
                ),
                None,
            )
            refs = ref(source, quote=provider.get("source_quote")) if source else []
            if refs:
                matched_requirement = True
                support_refs.extend(refs)
                named_support.append(
                    str((source or {}).get("name") or card_identity(source or {}))
                )
            else:
                missing_provider_context = True
        support_matches.append(matched_requirement)
    support_complete = bool(requirements) and all(support_matches)
    context_omitted = (
        (
            deck_entries is None
            and not (
                packet
                and packet.get("view") != "compact"
                and "library_entries_scanned" in packet.get("coverage", {})
            )
        )
        or missing_provider_context
        or bool(packet and packet.get("view") == "compact" and not no_cut_context)
    )
    gap: Gap = "context_omitted" if context_omitted else "support_not_found"
    add(
        "setup",
        "Compatible printed support is identified for the required setup.",
        "conditional" if support_complete else "unknown",
        refs=list(dict.fromkeys([*printed, *support_refs])),
        conditions=[
            "Printed ingredients do not establish board availability or sustainable supply."
        ],
        gap=None if support_complete else gap,
        providers=sorted(set(named_support)),
    )
    add(
        "resource_supply",
        "Consumed resources can be supplied again for future uses.",
        "unknown" if resources or not complete else "conditional",
        refs=list(dict.fromkeys([*printed, *support_refs])),
        conditions=[
            "One token, return effect or compatible provider does not prove sustained supply."
        ],
        gap=(
            ("unsupported_profile" if support_complete else gap)
            if resources
            else "unsupported_profile" if not complete else None
        ),
        consumed=resources,
    )

    # Recompute exact arithmetic from this source, never from a stale score.
    from sabermetrics.assistant_suitability import _draw_accounting

    accounting = _draw_accounting(card) if complete else {}
    raw_delta = accounting.get("net_hand_delta_per_activation")
    delta: int | None = (
        raw_delta
        if isinstance(raw_delta, int) and not isinstance(raw_delta, bool)
        else None
    )
    branches = accounting.get("spell_branches", [])
    known_delta = delta is not None
    branch_deltas = [b.get("net_hand_delta") for b in branches]
    branch_delta = (
        branch_deltas[0]
        if branch_deltas
        and len(set(branch_deltas)) == 1
        and isinstance(branch_deltas[0], int)
        else None
    )
    ranked_delta = delta if delta is not None else branch_delta
    add(
        "hand_delta",
        "Own hand-card change is accounted separately from other resources.",
        "established" if known_delta or branches else "unknown",
        conditions=[
            "Assumes resolution without replacement effects; sacrifices are separate."
        ],
        gap=None if known_delta or branches else "unsupported_profile",
        **(
            {"per_activation": delta}
            if known_delta
            else {"spell_branches": branches} if branches else {}
        ),
    )
    consumed_permanent = (
        "permanent" in resources or accounting.get("permanent_sacrifice_cost") is True
    )
    add(
        "net_advantage",
        "Hand gain establishes net resource advantage.",
        "contradicted" if ranked_delta is not None and ranked_delta <= 0 else "unknown",
        conditions=[
            "Subtract consumed permanents separately; recursion is not a resource refund."
        ],
        gap=(
            None
            if ranked_delta is not None and ranked_delta <= 0
            else "unsupported_profile"
        ),
        permanent_consumed=bool(consumed_permanent),
    )
    if routes or re.search(r"optional|control|may|forced", request, re.IGNORECASE):
        controls = []
        for clause in relevant:
            if not re.search(r"\bdraw\b", clause["source_quote"], re.IGNORECASE):
                continue
            effect = " ".join(clause["effects"])
            # Inspect the complete outcome, including postposed qualifications
            # and intervening-if triggers. A later optional discard does not
            # make an already unconditional draw optional.
            outcome_conditions = [
                text
                for text in [*clause["triggers"], effect]
                if re.search(r"\b(?:if|unless|except|only)\b", text, re.IGNORECASE)
            ]
            controls.append(
                {
                    "initiating": (
                        "voluntary_activation"
                        if clause["kind"] == "activated"
                        else "voluntary_cast" if spell else "triggered"
                    ),
                    "resolution": (
                        "conditional_choice"
                        if not outcome_conditions
                        and re.search(
                            r"\bmay draw\b",
                            effect,
                            re.IGNORECASE,
                        )
                        else (
                            "unknown"
                            if outcome_conditions
                            or re.search(
                                r"\b(?:if|unless|may)\b[^.]*\bdraw\b",
                                effect,
                                re.IGNORECASE,
                            )
                            else "required_if_resolving"
                        )
                    ),
                    **(
                        {"outcome_conditions": outcome_conditions}
                        if outcome_conditions
                        else {}
                    ),
                }
            )
        add(
            "optionality",
            "Initiation control and resolution choice are distinct.",
            "established" if complete and controls else "unknown",
            gap=None if complete and controls else "unsupported_profile",
            controls=controls,
        )

    if "counter" in resources or re.search(
        r"counter.*renew|replenish.*counter", request, re.IGNORECASE
    ):
        # Exact known starting supply, not a semantic estimate. Only an explicit
        # same-type enters-with clause and source-only removal are accepted.
        supply = None
        removal = re.search(
            rf"\bremove ({_N}) ([\w+/-]+) counters? from (?:{self_name}|this (?:artifact|creature|permanent))\b",
            cost_text,
            re.IGNORECASE,
        )
        # Accept a whole fixed entry sentence, never a matching prefix of a
        # scaled/conditional sentence. Unrecognized counter clauses can modify
        # the supply, so withhold arithmetic rather than interpreting them.
        counter_sentences = [
            sentence.strip()
            for sentence in re.split(r"[.\n]", raw)
            if re.search(r"\bcounters?\b", sentence, re.IGNORECASE)
        ]
        initial_candidates = [
            match
            for sentence in counter_sentences
            if (
                match := re.fullmatch(
                    rf"(?:{self_name}|this (?:artifact|creature|permanent)) enters(?: the battlefield)? with ({_N}) ([\w+/-]+) counters?(?: on it)?",
                    sentence,
                    re.IGNORECASE,
                )
            )
        ]
        initial = initial_candidates[0] if len(initial_candidates) == 1 else None
        exact_removal = bool(
            removal
            and any(
                re.fullmatch(re.escape(removal[0]), part.strip(), re.IGNORECASE)
                for cost in costs
                for part in cost.split(",")
            )
        )
        fixed_counter_clauses = bool(
            initial
            and all(
                sentence == initial[0]
                or any(
                    sentence == c["source_quote"].rstrip(".")
                    and (
                        not re.search(
                            r"\bcounters?\b", " ".join(c["effects"]), re.IGNORECASE
                        )
                        or re.fullmatch(
                            rf"Put {_N} {re.escape(initial[2])} counters? on (?:{self_name}|this (?:artifact|creature|permanent))\.",
                            " ".join(c["effects"]),
                            re.IGNORECASE,
                        )
                    )
                    for c in clauses
                    if c["kind"] == "activated"
                )
                for sentence in counter_sentences
            )
        )
        if (
            complete
            and removal
            and initial
            and exact_removal
            and fixed_counter_clauses
            and len(activations) == 1
            and len(re.findall(r"\bremove\b", cost_text, re.IGNORECASE)) == 1
            and removal[2].casefold() == initial[2].casefold()
            and _number(removal[1]) > 0
        ):
            count, spent = _number(initial[1]), _number(removal[1])
            supply = {
                "starting_counters": count,
                "counters_per_use": spent,
                "uses_without_renewal": count // spent,
                "future_uses_after_first": max(0, count // spent - 1),
                "counter_type": initial[2],
            }
        counter_requirements = [
            r for r in requirements if r.get("dimension") == "counters"
        ]
        add(
            "counter_renewal",
            "Counter renewal is established independently of recursion.",
            "unknown",
            conditions=[
                "Returning a permanent does not preserve counters; inspect its printed entry and renewal clauses."
            ],
            gap=(
                "support_not_found"
                if counter_requirements
                and not any(r.get("providers") for r in counter_requirements)
                and not context_omitted
                else "context_omitted" if context_omitted else "unsupported_profile"
            ),
            finite_supply=supply,
        )

    if packet is not None or re.search(
        r"\bcut|replace|swap|remove\b", request, re.IGNORECASE
    ):
        losses = packet.get("lost_roles", []) if packet else []
        # Packet fields are application-owned facts, referenced as whole values.
        packet_source = {
            "oracle_id": card_identity(card) + ":packet",
            "lost_roles": json.dumps(losses, ensure_ascii=False),
            "lost_engine_links": json.dumps(
                (packet or {}).get("lost_engine_links", []), ensure_ascii=False
            ),
        }
        loss_refs = (
            [
                *ref(packet_source, "lost_roles"),
                *ref(packet_source, "lost_engine_links"),
            ]
            if packet
            else []
        )
        losses_omitted = (packet or {}).get("omitted", {}).get("lost_roles", 0)
        loss_context_known = bool(
            packet and packet.get("changes", {}).get("status") in {"resolved", "no_cut"}
        )
        add(
            "lost_roles",
            "The proposed cut's lost roles are retained for comparison.",
            (
                "established"
                if loss_context_known and not losses_omitted
                else "conditional" if loss_context_known else "unknown"
            ),
            refs=loss_refs,
            conditions=["Lost roles are not proven replaced by incoming ingredients."],
            gap="context_omitted" if not packet or losses_omitted else None,
            roles=losses,
            omitted=losses_omitted,
            lost_engine_links=(packet or {}).get("lost_engine_links", []),
        )
    mechanism = sorted(
        set(
            [r.kind for r in routes]
            + resources
            + (["entry_trigger"] if enter_trigger else [])
            + (["spell"] if spell else [])
            + [
                p
                for p in setup
                if p.endswith("required") or p == "opponent_action_or_choice"
            ]
            + (
                ["turn_event"]
                if any(
                    re.search(
                        r"upkeep|end step|beginning of", r.source_quote, re.IGNORECASE
                    )
                    for r in routes
                )
                else []
            )
        )
    ) or ["unclassified"]
    add(
        "mechanism_diversity",
        "Mechanism differences can break otherwise comparable research priorities.",
        "established" if complete else "unknown",
        conditions=[
            "Prefer useful differences among comparable options; no forced quota."
        ],
        gap=None if complete else "unsupported_profile",
        mechanism=mechanism,
    )
    # Lexicographic dimensions keep one arbitrary sum from certifying quality.
    first = (
        3
        if not complete
        else (
            0
            if spell and not setup
            else 1 if activations and not resources and not setup else 2
        )
    )
    future = (
        3
        if not complete
        else (
            0
            if recurrent and not set(resources) & {"permanent", "counter", "hand_card"}
            else 1 if recurrent else 2
        )
    )
    ranking = {
        "draw_relevance": 0 if routes else 1,
        "coverage": 0 if complete else 1,
        "first_use": first,
        "future_uses": future,
        "hand_delta": (
            0
            if ranked_delta is not None and ranked_delta > 0
            else 2 if ranked_delta is not None and ranked_delta <= 0 else 1
        ),
        "known_hand_gain": -ranked_delta if ranked_delta is not None else 0,
        "supply": 1 if set(resources) & {"permanent", "counter", "hand_card"} else 0,
        "setup": 0 if not setup and not requirements else 1 if support_complete else 2,
    }
    loss = next((row for row in rows if row.dimension == "lost_roles"), None)
    ranking["lost_roles"] = (
        0
        if loss and loss.status == "established" and not loss.facts.get("roles")
        else 1 if loss and loss.status == "established" else 2
    )
    return StrategicObligations(
        card_id=card_identity(card),
        request_priorities=request_priorities(request),
        obligations=rows,
        evidence=evidence,
        ranking=ranking,
        mechanism=mechanism,
        coverage={
            "oracle_complete": complete,
            "ranking_scope": ranking_scope,
            "deck_context": "omitted" if context_omitted else "supplied",
            "scope": "bounded printed evidence; no board trace, loop or measured quality proof",
        },
    )


def strategic_rank_key(
    contract: StrategicObligations | dict[str, Any],
) -> tuple[int, ...]:
    value = (
        contract
        if isinstance(contract, StrategicObligations)
        else StrategicObligations.model_validate(contract)
    )
    ranks = value.ranking
    priorities = value.request_priorities
    # No request-wide default quality ordering. Returning an empty prefix lets
    # callers retain their existing goal/relevance ranking for unsupported roles.
    # Older serialized contracts without applicability evidence also abstain.
    if value.coverage.get("ranking_scope") != "draw_only":
        return ()
    if ranks.get("draw_relevance", 1):
        # Absence of a detected draw route is not proof of absence, and unrelated
        # abilities must not earn draw economics/setup tie breakers.
        return (1,)
    use = [p for p in priorities if p in {"first_use", "future_uses", "lost_roles"}]
    dims = ["draw_relevance", "coverage", *use]
    if "net_advantage" in priorities:
        dims.extend(["hand_delta", "known_hand_gain"])
    # Supply and setup matter within a requested draw function, after relevance
    # and the explicit first/future-use preference, never across functions.
    dims.extend(["supply", "setup"])
    dims = list(dict.fromkeys(dims))
    return tuple(ranks.get(d, 1) for d in dims)


def compact_strategic_obligations(
    contract: StrategicObligations,
    *,
    vocabulary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Whole facts; optionally intern repeated prose in a payload-owned vocabulary.

    The default view round-trips through StrategicObligations. Selection payloads
    pass a shared vocabulary to avoid repeating identical propositions/conditions
    per candidate. Its condition_refs index vocabulary.conditions, never sources.
    """
    result = contract.model_dump(exclude_defaults=True)
    if vocabulary is not None:
        propositions = vocabulary.setdefault("propositions", {})
        conditions = vocabulary.setdefault("conditions", [])
        result["view"] = "shared_vocabulary"
        for row in result["obligations"]:
            propositions[row["dimension"]] = row.pop("proposition")
            refs = []
            for condition in row.pop("conditions", []):
                if condition not in conditions:
                    conditions.append(condition)
                refs.append(conditions.index(condition))
            if refs:
                row["condition_refs"] = refs
    return result


def render_strategic_obligations(
    contract: StrategicObligations | dict[str, Any], *, max_chars: int = 1800
) -> str:
    """Application rendering hook; preserve conditions and visible omissions."""
    value = (
        contract
        if isinstance(contract, StrategicObligations)
        else StrategicObligations.model_validate(contract)
    )
    statements: list[str] = []
    priorities = [
        *value.request_priorities,
        "counter_renewal",
        "lost_roles",
        "hand_delta",
        "resource_supply",
        "optionality",
    ]
    ordered = sorted(
        value.obligations,
        key=lambda row: (
            priorities.index(row.dimension)
            if row.dimension in priorities
            else len(priorities)
        ),
    )
    for row in ordered:
        statement = f"{row.dimension} [{row.status}]: {row.proposition}"
        if row.facts:
            statement += " " + json.dumps(
                row.facts, ensure_ascii=False, separators=(",", ":")
            )
        statement += " " + " ".join(row.conditions)
        if row.gap:
            statement += f" Evidence gap: {row.gap}."
        if len(" ".join([*statements, statement])) + 65 > max_chars:
            continue
        statements.append(statement)
    omitted = len(value.obligations) - len(statements)
    if omitted:
        statements.append(
            f"{omitted} additional strategic obligations remain in structured evidence."
        )
    result = " ".join(statements)
    return result if len(result) <= max_chars else ""
