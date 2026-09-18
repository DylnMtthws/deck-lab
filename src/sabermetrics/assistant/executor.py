"""The deterministic executor: one handler per step kind, no model anywhere.

The executor is the only thing in the design that retrieves, ranks, scores or
computes. A plan says *what*; this says *how*, and nothing above it may reach
past it to the corpus.

Two behaviours are load-bearing and are implemented here rather than described:

**Deck scoping is injected, never authored.** ``allowed_oracle_ids`` is the one
filter that can name specific cards, and :func:`_scope_filters` is the only
place it is ever populated. A plan asks for ``scope="deck"``; the executor
resolves the context and materializes the ids. A hand-written plan therefore
cannot restrict a search to its own answer.

**Absence propagates.** A set operation over a step that did not run returns a
typed absence naming that step, never a set over the survivors. A partial union
is an empty list pretending to be an answer.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sabermetrics.assistant.context import (
    DeckContext,
    DeckContextRegistry,
    DeckContextUnresolvedError,
    UnknownDeckContextError,
)
from sabermetrics.assistant.envelope import (
    CardRow,
    CorpusProvenance,
    Coverage,
    NoFieldEvidence,
    PlanRun,
    ProfileFacetValue,
    ResultOrdering,
    RulesRow,
    StatedAbsenceRecord,
    StepNotRun,
    StepNotRunReason,
    StepOutcome,
    StepResult,
)
from sabermetrics.assistant.ir import (
    CardSearchStep,
    DeckProfileStep,
    DifferenceStep,
    IntersectStep,
    PlanStep,
    ResearchPlan,
    RulesLookupStep,
    TagFilterStep,
    UnionStep,
)
from sabermetrics.assistant.sources import (
    CardSource,
    CardSourceUnavailable,
    RulesSource,
    RulesUnavailable,
)
from sabermetrics.substrate.catalog import CatalogRecord
from sabermetrics.substrate.models import (
    CardFilters,
    CardSearchQuery,
    RetrievalAvailability,
    StageEvidence,
)

_STRUCTURED_AVAILABILITY = RetrievalAvailability(
    lexical=False,
    dense=False,
    reranked=False,
    notices=("structured read; no ranked stage ran",),
)


class ExecutorError(RuntimeError):
    """A plan could not be executed for a reason no envelope models."""


@dataclass
class _RunContext:
    """Mutable state for one plan execution."""

    plan: ResearchPlan
    provenance: CorpusProvenance
    deck: DeckContext | None
    #: Why the bound context did not resolve, when it did not. Carried so a
    #: corpus step that still ran can say its deck annotations are absent
    #: rather than reporting every card as not-in-deck, which is a false
    #: measurement wearing a successful status.
    context_failure: str | None = None
    outcomes: dict[str, StepOutcome] = field(default_factory=dict)


class ResearchExecutor:
    """Run one validated :class:`ResearchPlan` against the substrate."""

    def __init__(
        self,
        cards: CardSource,
        *,
        rules: RulesSource | None = None,
        contexts: DeckContextRegistry | None = None,
    ) -> None:
        """Bind the executor to its deterministic sources.

        Args:
            cards: The card retrieval substrate.
            rules: The reference layer. ``None`` means every ``rules_lookup``
                returns a typed absence, which is the honest state of a
                deployment with no reference index built.
            contexts: Deck context resolver. Defaults to the shipped registry.
        """
        self._cards = cards
        self._rules = rules
        self._contexts = contexts or DeckContextRegistry()

    def run(self, plan: ResearchPlan, *, question_id: str | None = None) -> PlanRun:
        """Execute every step of a plan in declaration order.

        Args:
            plan: A validated plan.
            question_id: Optional golden-question id, recorded on the run.

        Returns:
            The complete run, including any step that did not run.
        """
        started = time.perf_counter()
        provenance = self._cards.provenance()
        deck, context_failure = self._resolve_context(plan)
        ctx = _RunContext(
            plan=plan,
            provenance=provenance,
            deck=deck,
            context_failure=None if context_failure is None else context_failure[1],
        )
        for step in plan.steps:
            if context_failure is not None and _needs_context(step):
                ctx.outcomes[step.id] = StepNotRun(
                    step_id=step.id,
                    kind=step.kind,
                    reason=context_failure[0],
                    detail=context_failure[1],
                    provenance=provenance,
                )
                continue
            ctx.outcomes[step.id] = self.run_step(step, ctx)
        return PlanRun(
            plan_sha256=plan.sha256(),
            question_id=question_id,
            intent=plan.intent,
            context_id=plan.context_id,
            steps=tuple(ctx.outcomes[step.id] for step in plan.steps),
            answer_step=plan.answer_step,
            clarification_required=plan.clarification_required,
            stated_absences=tuple(
                StatedAbsenceRecord(
                    reason=absence.reason,
                    detail=absence.detail,
                    answers_expectation=absence.answers_expectation,
                )
                for absence in plan.stated_absences
            ),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def run_step(self, step: PlanStep, ctx: _RunContext) -> StepOutcome:
        """Execute one step and return its provenance-bearing outcome.

        Args:
            step: The step to run.
            ctx: State for the plan this step belongs to.

        Returns:
            A result, or a typed absence naming why the step did not run.
        """
        if isinstance(step, CardSearchStep):
            return self._card_search(step, ctx)
        if isinstance(step, TagFilterStep):
            return self._tag_filter(step, ctx)
        if isinstance(step, RulesLookupStep):
            return self._rules_lookup(step, ctx)
        if isinstance(step, DeckProfileStep):
            return self._deck_profile(step, ctx)
        if isinstance(step, UnionStep | IntersectStep | DifferenceStep):
            return self._set_operation(step, ctx)
        raise ExecutorError(f"no handler for step kind {step.kind!r}")

    # -- leaf handlers ----------------------------------------------------

    def _card_search(self, step: CardSearchStep, ctx: _RunContext) -> StepOutcome:
        """Run ranked hybrid retrieval over a structurally narrowed population."""
        started = time.perf_counter()
        # Checked here rather than in the validator, unlike the query token
        # cap. A too-long query is wrong about the plan itself and is wrong
        # everywhere; a top_k above the ceiling is wrong only about *this*
        # deployment's configuration, and the same plan is servable against a
        # bundle configured with a larger result_limit. Refusing it at
        # authoring time would make a plan's validity depend on the machine it
        # was written on.
        ceiling = self._cards.result_limit
        if step.query.top_k > ceiling:
            return StepNotRun(
                step_id=step.id,
                kind=step.kind,
                reason="limit_exceeds_retrieval_ceiling",
                detail=(
                    f"step asks for {step.query.top_k} results; the configured "
                    f"retrieval ceiling is {ceiling}, so the step would silently "
                    "return fewer than it declared"
                ),
                provenance=ctx.provenance,
            )
        query = CardSearchQuery(
            text=step.query.text,
            filters=_scope_filters(step.query.filters, step.scope, ctx),
            top_k=step.query.top_k,
        )
        try:
            trace = self._cards.search_with_trace(query)
        except CardSourceUnavailable as exc:
            return _unavailable(step, exc.reason, exc.detail, ctx)

        result = trace.result
        records = self._hydrate(tuple(hit.oracle_id for hit in result.hits))
        stages = {hit.oracle_id: hit.stages for hit in result.hits}
        cards = tuple(
            _card_row(
                records[hit.oracle_id],
                rank=rank,
                stages=stages[hit.oracle_id],
                deck=ctx.deck,
            )
            for rank, hit in enumerate(result.hits, start=1)
            if hit.oracle_id in records
        )
        ranked = result.availability.lexical or result.availability.dense
        examined = _examined(trace.rankings) if ranked else result.eligible_cards
        eligible = result.eligible_cards
        returned = len(cards)
        dropped = max(0, eligible - returned)
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            tier="fact",
            ordering="ranked" if ranked else "oracle_id",
            provenance=ctx.provenance,
            coverage=Coverage(
                eligible=eligible,
                eligible_is_exact=True,
                examined=examined,
                returned=returned,
                dropped=dropped,
                truncated=dropped > 0,
                set_input_incomplete=False,
            ),
            field=NoFieldEvidence(reason=step.field_absence),
            availability=result.availability,
            cards=cards,
            notices=_context_notices(ctx),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _tag_filter(self, step: TagFilterStep, ctx: _RunContext) -> StepOutcome:
        """Select the whole structured population, then bound it in Python.

        The ranked path caps at ``result_limit``. A mechanic-tag population is
        not a ranking, so reading a capped ranking as a population would put a
        bound in a field named for a count — and a downstream intersection
        would then publish that bound as its denominator.
        """
        started = time.perf_counter()
        filters = _scope_filters(step.filters, step.scope, ctx)
        try:
            rows = self._cards.records(filters)
        except CardSourceUnavailable as exc:
            return _unavailable(step, exc.reason, exc.detail, ctx)

        eligible = len(rows)
        selected = rows[: step.limit]
        cards = tuple(
            _card_row(record, rank=rank, stages=(), deck=ctx.deck)
            for rank, record in enumerate(selected, start=1)
        )
        dropped = eligible - len(cards)
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            tier="fact",
            ordering="oracle_id",
            provenance=ctx.provenance,
            coverage=Coverage(
                eligible=eligible,
                eligible_is_exact=True,
                examined=eligible,
                returned=len(cards),
                dropped=dropped,
                truncated=dropped > 0,
                set_input_incomplete=False,
            ),
            field=NoFieldEvidence(reason=step.field_absence),
            availability=_STRUCTURED_AVAILABILITY,
            cards=cards,
            notices=_context_notices(ctx),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _rules_lookup(self, step: RulesLookupStep, ctx: _RunContext) -> StepOutcome:
        """Query the reference layer's one active embedding generation."""
        started = time.perf_counter()
        if self._rules is None:
            return StepNotRun(
                step_id=step.id,
                kind=step.kind,
                reason="reference_index_absent",
                detail=(
                    "this deployment has no reference layer bound; the "
                    "Comprehensive Rules corpus has not been encoded under the "
                    "pinned embedding model"
                ),
                provenance=ctx.provenance,
            )
        try:
            rows = self._rules.lookup(
                step.question,
                top_k=step.limit,
                tier_filter=step.tier_filter,
                document_filter=step.document_filter,
            )
        except RulesUnavailable as exc:
            return _unavailable(step, exc.reason, exc.detail, ctx)

        # The character budget is applied AFTER ranking and in rank order, so
        # it changes how much of the ranking is returned and never which order.
        # At least one row is always kept: a budget smaller than the top chunk
        # is a budget that returns the top chunk, not nothing.
        fetched = rows
        if step.char_budget is not None:
            kept: list[RulesRow] = []
            used = 0
            for row in rows:
                if kept and used + len(row.content) > step.char_budget:
                    break
                kept.append(row)
                used += len(row.content)
            rows = tuple(kept)
        dropped = len(fetched) - len(rows)
        chars_returned = sum(len(row.content) for row in rows)
        notices = ["reference retrieval is dense-only"]
        if step.char_budget is not None:
            notices.append(
                f"char budget {step.char_budget}: returned {len(rows)} of "
                f"{len(fetched)} ranked chunks, {chars_returned} characters"
            )

        return StepResult(
            step_id=step.id,
            kind=step.kind,
            tier="fact",
            ordering="ranked",
            provenance=ctx.provenance,
            coverage=Coverage(
                eligible=len(fetched),
                eligible_is_exact=False,
                examined=None,
                examined_absent_because=(
                    "the reference retriever does not report how many chunks "
                    "it scored"
                ),
                returned=len(rows),
                dropped=dropped,
                truncated=dropped > 0,
                set_input_incomplete=False,
                truncation_source=("char_budget",) if dropped else (),
            ),
            field=NoFieldEvidence(reason="not_a_field_query"),
            availability=RetrievalAvailability(
                lexical=False,
                dense=True,
                reranked=False,
                notices=tuple(notices),
            ),
            rules=rows,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _deck_profile(self, step: DeckProfileStep, ctx: _RunContext) -> StepOutcome:
        """Report deterministic facts about the bound deck context."""
        started = time.perf_counter()
        deck = ctx.deck
        if deck is None:
            return StepNotRun(
                step_id=step.id,
                kind=step.kind,
                reason="deck_context_absent",
                detail="this plan binds no deck context",
                provenance=ctx.provenance,
            )
        ordered: list[str] = []
        facets: list[ProfileFacetValue] = []
        for facet in step.facets:
            ids, value = _facet(facet, deck)
            facets.append(value)
            for oracle_id in ids:
                if oracle_id not in ordered:
                    ordered.append(oracle_id)
        try:
            records = self._hydrate(tuple(ordered))
        except CardSourceUnavailable as exc:
            return _unavailable(step, exc.reason, exc.detail, ctx)

        eligible = len(ordered)
        cards = tuple(
            _card_row(records[oracle_id], rank=rank, stages=(), deck=deck)
            for rank, oracle_id in enumerate(ordered[: step.limit], start=1)
            if oracle_id in records
        )
        dropped = eligible - len(cards)
        notices = [
            f"deck context {deck.context_id} from strategy pack {deck.pack_id}",
        ]
        if deck.baseline_of is not None:
            notices.append(
                f"{deck.context_id} is a declared alias of {deck.baseline_of}; "
                "it is the same list, not a second one"
            )
        if not deck.thresholds_available:
            notices.append(
                "no configured role threshold: a role count below its pack "
                "target is not by itself a deficit"
            )
        if not deck.field_comparison_available:
            notices.append(
                "no field comparison is available; field statistics arrive " "with R5"
            )
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            tier="fact",
            ordering="declaration",
            provenance=ctx.provenance,
            coverage=Coverage(
                eligible=eligible,
                eligible_is_exact=True,
                examined=eligible,
                returned=len(cards),
                dropped=dropped,
                truncated=dropped > 0,
                set_input_incomplete=False,
            ),
            field=NoFieldEvidence(reason=step.field_absence),
            availability=_STRUCTURED_AVAILABILITY,
            cards=cards,
            facets=tuple(facets),
            notices=tuple(notices),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    # -- set operations ---------------------------------------------------

    def _set_operation(
        self, step: UnionStep | IntersectStep | DifferenceStep, ctx: _RunContext
    ) -> StepOutcome:
        """Combine earlier result sets without ever inventing a member."""
        started = time.perf_counter()
        names = (
            (step.left, step.right) if isinstance(step, DifferenceStep) else step.inputs
        )
        inputs: list[StepResult] = []
        for name in names:
            outcome = ctx.outcomes.get(name)
            if outcome is None:
                raise ExecutorError(f"step {step.id!r} consumes unrun step {name!r}")
            if isinstance(outcome, StepNotRun):
                return StepNotRun(
                    step_id=step.id,
                    kind=step.kind,
                    reason="input_step_not_run",
                    detail=(
                        f"input step {name!r} did not run ({outcome.reason}); a "
                        "set operation over the surviving inputs would be an "
                        "empty list pretending to be an answer"
                    ),
                    provenance=ctx.provenance,
                )
            inputs.append(outcome)

        tiers = {result.tier for result in inputs}
        if len(tiers) > 1:
            return StepNotRun(
                step_id=step.id,
                kind=step.kind,
                reason="input_tier_mismatch",
                detail=(
                    "inputs carry different assertion tiers "
                    f"({', '.join(sorted(tiers))}); flattening them would "
                    "present one tier's evidence as another's"
                ),
                provenance=ctx.provenance,
            )
        if {result.provenance for result in inputs} != {ctx.provenance}:
            raise ExecutorError(
                f"step {step.id!r} combines results from different corpora"
            )

        rows: dict[str, CardRow] = {}
        for result in inputs:
            for card in result.cards:
                rows.setdefault(card.oracle_id, card)

        if isinstance(step, UnionStep):
            # Take the ROW from the same leg that set the rank. Reading the row
            # out of `rows` (first-declared leg wins) while ordering by best
            # rank would attach one leg's stage evidence to another leg's
            # position, so "why is this first" would print scores that
            # contradict the ordering.
            winners = _by_best_rank(inputs)
            for card in winners:
                rows[card.oracle_id] = card
            chosen = [card.oracle_id for card in winners]
            eligible = len(chosen)
        elif isinstance(step, IntersectStep):
            shared = set.intersection(*(set(r.oracle_ids) for r in inputs))
            chosen = [oid for oid in inputs[0].oracle_ids if oid in shared]
            eligible = min(len(result.cards) for result in inputs)
        else:
            removed = set(inputs[1].oracle_ids)
            chosen = [oid for oid in inputs[0].oracle_ids if oid not in removed]
            eligible = len(inputs[0].cards)

        incomplete = tuple(
            result.step_id
            for result in inputs
            if result.coverage.truncated or result.coverage.set_input_incomplete
        )
        notices = tuple(
            f"input {result.step_id!r} returned {result.coverage.returned} of "
            f"{result.coverage.eligible} in {result.ordering} order, so this "
            "set result may be incomplete"
            for result in inputs
            if result.coverage.truncated or result.coverage.set_input_incomplete
        )
        cards = tuple(
            rows[oracle_id].model_copy(update={"rank": rank})
            for rank, oracle_id in enumerate(chosen, start=1)
        )
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            tier=inputs[0].tier,
            ordering=_combined_ordering(inputs),
            provenance=ctx.provenance,
            coverage=Coverage(
                eligible=eligible,
                eligible_is_exact=True,
                examined=sum(result.coverage.returned for result in inputs),
                returned=len(cards),
                # A set operation has no bound of its own. Narrowing is the
                # operation, not truncation, so nothing is dropped here; what
                # an intersection can hide is an input that was itself cut off,
                # and that is what `set_input_incomplete` carries.
                dropped=0,
                truncated=False,
                set_input_incomplete=bool(incomplete),
                truncation_source=incomplete,
            ),
            field=NoFieldEvidence(reason="not_a_field_query"),
            availability=_STRUCTURED_AVAILABILITY,
            cards=cards,
            notices=notices,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    # -- helpers ----------------------------------------------------------

    def _resolve_context(
        self, plan: ResearchPlan
    ) -> tuple[DeckContext | None, tuple[StepNotRunReason, str] | None]:
        """Resolve the plan's bound context, or report why it could not be."""
        if plan.context_id is None:
            return None, None
        try:
            return self._contexts.resolve(plan.context_id, cards=self._cards), None
        except UnknownDeckContextError as exc:
            return None, ("deck_context_unresolved", str(exc))
        except (DeckContextUnresolvedError, CardSourceUnavailable) as exc:
            return None, ("deck_context_unresolved", str(exc))

    def _hydrate(self, oracle_ids: Sequence[str]) -> dict[str, CatalogRecord]:
        """Fetch full catalog rows for a bounded set of Oracle ids."""
        if not oracle_ids:
            return {}
        records = self._cards.records(
            CardFilters(
                allowed_oracle_ids=tuple(dict.fromkeys(oracle_ids)),
                commander_legal=None,
            )
        )
        return {record.oracle_id: record for record in records}


def _context_notices(ctx: _RunContext) -> tuple[str, ...]:
    """Say so when deck annotations are absent because the context failed."""
    if ctx.context_failure is None:
        return ()
    return (
        "deck annotations are absent: the bound deck context did not resolve "
        f"({ctx.context_failure}). in_deck and deck_roles are null rather than "
        "false or empty",
    )


def _needs_context(step: PlanStep) -> bool:
    """Return whether a step cannot run without a resolved deck context."""
    return isinstance(step, DeckProfileStep) or getattr(step, "scope", "") == "deck"


def _unavailable(
    step: PlanStep, reason: StepNotRunReason, detail: str, ctx: _RunContext
) -> StepNotRun:
    """Build the typed absence for a substrate layer that could not serve."""
    return StepNotRun(
        step_id=step.id,
        kind=step.kind,
        reason=reason,
        detail=detail,
        provenance=ctx.provenance,
    )


def _scope_filters(filters: CardFilters, scope: str, ctx: _RunContext) -> CardFilters:
    """Return the filters a step actually runs with.

    This is the only place ``allowed_oracle_ids`` is ever populated. The filter
    set is rebuilt through the constructor rather than copied, so an injected
    scope that contradicts an authored predicate fails loudly instead of
    skipping validation.

    Args:
        filters: The predicates the plan authored.
        scope: ``"corpus"`` or ``"deck"``.
        ctx: State for the plan being run.

    Returns:
        The authored filters, restricted to the bound deck when asked.

    Raises:
        ExecutorError: If a deck-scoped step reached execution with no context,
            which plan validation is supposed to have prevented.
    """
    if scope != "deck":
        return filters
    if ctx.deck is None:
        raise ExecutorError("a deck-scoped step reached execution with no context")
    payload = filters.model_dump()
    payload["allowed_oracle_ids"] = ctx.deck.oracle_ids
    return CardFilters(**payload)


def _examined(rankings: dict[str, tuple[str, ...]]) -> int:
    """Return how many distinct cards any ranking stage actually scored."""
    scored: set[str] = set()
    for stage in ("lexical", "dense"):
        scored.update(rankings.get(stage, ()))
    return len(scored)


def _card_row(
    record: CatalogRecord,
    *,
    rank: int,
    stages: tuple[StageEvidence, ...],
    deck: DeckContext | None,
) -> CardRow:
    """Build one result row from a catalog record."""
    return CardRow(
        oracle_id=record.oracle_id,
        name=record.name,
        type_line=record.type_line,
        mana_cost=record.mana_cost,
        mana_value=record.mana_value,
        color_identity=record.color_identity,
        types=record.types,
        tags=record.tags,
        rank=rank,
        stages=stages,
        deck_roles=None if deck is None else deck.roles_for(record.oracle_id),
        in_deck=None if deck is None else record.oracle_id in set(deck.oracle_ids),
    )


def _by_best_rank(inputs: Sequence[StepResult]) -> list[CardRow]:
    """Order a union by each card's BEST rank across every input.

    Taking the rank from whichever input mentioned a card first would make the
    order depend on the order the plan happens to declare its legs: a card
    ranked 2nd by a precise query and 43rd by a broad one would be presented
    43rd purely because the broad leg was written first. A union is
    commutative, so its ordering has to be too.

    Ties break on Oracle id, so the whole ordering is deterministic.
    """
    best: dict[str, tuple[int, CardRow]] = {}
    for result in inputs:
        for card in result.cards:
            current = best.get(card.oracle_id)
            if current is None or card.rank < current[0]:
                best[card.oracle_id] = (card.rank, card)
    return [
        card
        for _, card in sorted(
            best.values(), key=lambda entry: (entry[0], entry[1].oracle_id)
        )
    ]


def _combined_ordering(inputs: Sequence[StepResult]) -> ResultOrdering:
    """Return the ordering a set result may honestly claim.

    Mixing a ranked input with an unranked one produces an order that is not a
    ranking, and a recall-at-k window over it would measure the alphabet.
    """
    orderings = {result.ordering for result in inputs}
    if orderings == {"ranked"}:
        return "ranked"
    if orderings == {"declaration"}:
        return "declaration"
    return "oracle_id"


def _facet(facet: str, deck: DeckContext) -> tuple[tuple[str, ...], ProfileFacetValue]:
    """Return the cards a facet names and the facet's reported value."""
    if facet == "card_list":
        return deck.library_oracle_ids, ProfileFacetValue(
            facet=facet,
            counts={"library": len(deck.library_oracle_ids)},
            oracle_ids=deck.library_oracle_ids,
            note="the library in strategy-pack declaration order",
        )
    if facet == "commander":
        return deck.commander_oracle_ids, ProfileFacetValue(
            facet=facet,
            labels=deck.commander_names,
            counts={"commanders": len(deck.commander_oracle_ids)},
            oracle_ids=deck.commander_oracle_ids,
        )
    if facet == "primary_win_package":
        package = deck.primary_win_package
        return package.piece_oracle_ids, ProfileFacetValue(
            facet=facet,
            labels=(package.name, *package.piece_names),
            counts={"pieces": len(package.piece_oracle_ids)},
            oracle_ids=package.piece_oracle_ids,
            note=package.converts_via,
        )
    if facet == "secondary_win_packages":
        ids = tuple(
            oracle_id
            for package in deck.secondary_win_packages
            for oracle_id in package.piece_oracle_ids
        )
        return ids, ProfileFacetValue(
            facet=facet,
            labels=tuple(package.name for package in deck.secondary_win_packages),
            counts={"packages": len(deck.secondary_win_packages)},
            oracle_ids=ids,
            note=(
                "declared in the strategy pack; the pack is not a combo corpus "
                "and cannot establish an undeclared line"
            ),
        )
    if facet == "roles":
        ids = tuple(oracle_id for ids_ in deck.roles.values() for oracle_id in ids_)
        return _unique(ids), ProfileFacetValue(
            facet=facet,
            labels=tuple(deck.roles),
            counts=dict(deck.role_counts),
            oracle_ids=_unique(ids),
            note="roles are authored in the strategy pack, not derived",
        )
    if facet == "role_targets":
        return (), ProfileFacetValue(
            facet=facet,
            labels=tuple(deck.role_targets),
            counts=dict(deck.role_targets),
            note=(
                "pack targets, not thresholds: no configured threshold makes a "
                "count below its target a deficit"
            ),
        )
    if facet == "auto_include":
        return deck.auto_include_oracle_ids, ProfileFacetValue(
            facet=facet,
            counts={"auto_include": len(deck.auto_include_oracle_ids)},
            oracle_ids=deck.auto_include_oracle_ids,
        )
    raise ExecutorError(f"no reader for deck facet {facet!r}")


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """Return values with duplicates removed, order preserved."""
    return tuple(dict.fromkeys(values))
