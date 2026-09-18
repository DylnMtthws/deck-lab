"""The R3 executor and the envelopes it is forbidden to return without.

Three properties are what this file exists to keep, and each is asserted as a
mechanism rather than as a convention.

**A result cannot be built without its honesty fields.** ``provenance``,
``coverage``, ``field``, ``availability``, ``tier`` and ``ordering`` have no
defaults, so a renderer cannot be handed a row whose basis is unstated. The
parametrized construction tests below are the guard on that, and ``Coverage``
additionally refuses counts that contradict each other.

**Absence is typed, and it propagates.** A step that could not run returns
``StepNotRun`` with a closed-set reason; a set operation over one returns a
``StepNotRun`` naming it, never a set over the survivors. An empty tuple and a
refusal read identically once they are in a table, which is the whole point.

**Deck scope is injected, never authored.** ``allowed_oracle_ids`` is the one
filter that can name a card, and the executor is the only thing that populates
it. The portable bundle's 100 cards are exactly the Kinnan list, so a deck
scoped against it is indistinguishable from the corpus; the deck-scope tests
therefore bind a **subset** pack, where containment is observable.

Nothing here asserts a ranking position. The portable bundle's encoder is a
deterministic hashing stand-in rather than BGE, so relative order is a fixture
artefact; set membership, structure and invariants are not.
"""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from sabermetrics.assistant.context import DeckContext, DeckContextRegistry
from sabermetrics.assistant.envelope import (
    CardRow,
    Coverage,
    NoFieldEvidence,
    ProfileFacetValue,
    StepNotRun,
    StepResult,
)
from sabermetrics.assistant.executor import ResearchExecutor, _RunContext
from sabermetrics.assistant.ir import (
    CardSearchStep,
    DifferenceStep,
    IntersectStep,
    ResearchPlan,
    RulesLookupStep,
    StatedAbsence,
    TagFilterStep,
    UnionStep,
)
from sabermetrics.assistant.sources import (
    BundleCardSource,
    ReferenceRulesSource,
    RulesUnavailable,
)
from sabermetrics.reference_layer.retriever import RetrievedChunk
from sabermetrics.substrate.models import (
    CardFilters,
    CardSearchQuery,
    RetrievalAvailability,
)

BASALT = "cedh:kinnan-basalt-fixture"

#: Mechanic tags the portable bundle actually assigns, with their populations.
#: ``adds_two_or_more`` is a strict subset of ``mana_rock`` here, which is what
#: makes the intersection and difference tests below say something.
ROCK_TAG = "mana:mana_rock"
BIG_MANA_TAG = "mana:adds_two_or_more"
FREE_SPELL_TAG = "cost:free_alternative_cost"

#: Every honesty field a :class:`StepResult` refuses to be built without.
REQUIRED_HONESTY_FIELDS = (
    "tier",
    "ordering",
    "provenance",
    "coverage",
    "field",
    "availability",
)

#: Field-name substrings no result model may carry. A price or a collection
#: that exists gets read, and one that gets read becomes a tie-break (ADR-025).
FORBIDDEN_FIELD_TOKENS = ("price", "usd", "owned", "collection", "budget")

#: A deliberately small strategy pack, written under the file name the basalt
#: context reads. The portable bundle *is* the real Kinnan list, so scoping a
#: search to that deck selects the whole corpus and proves nothing. Five cards
#: and a commander make containment observable.
SUBSET_PACK = textwrap.dedent("""\
    pack_id: kinnan_basalt_subset
    name: Kinnan — a deliberately small subset, for scope tests
    commander_names:
    - Kinnan, Bonder Prodigy
    primary_win_package:
      name: Unbounded colourless into Thrasios
      kind: loop
      pieces:
      - Basalt Monolith
      - Thrasios, Triton Hero
      converts_via: Kinnan doubles the Monolith's output.
    role_targets:
      acceleration: 3
    auto_include:
    - Sol Ring
    cards:
    - name: Basalt Monolith
      roles: [acceleration]
    - name: Mana Vault
      roles: [acceleration]
    - name: Sol Ring
      roles: [acceleration]
    - name: Thrasios, Triton Hero
      roles: [win_package]
    - name: Force of Will
      roles: [interaction]
    """)

#: The three cards in the subset pack that carry ``mana:mana_rock``. The corpus
#: carries seven, so the two scopes cannot be confused for each other.
SUBSET_ROCKS = frozenset({"Basalt Monolith", "Mana Vault", "Sol Ring"})


class _RefusingRulesSource:
    """A rules source that reports a typed reason instead of empty rows."""

    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail

    def lookup(self, question, *, top_k, tier_filter=(), document_filter=()):
        raise RulesUnavailable(self.reason, self.detail)


class _FakeReferenceRetriever:
    """A reference retriever returning two chunks, one of them unsectioned."""

    def __init__(self):
        self.queries = []

    def retrieve(self, query):
        self.queries.append(query)
        return [
            RetrievedChunk(
                id="chunk-a",
                document="comprehensive_rules",
                section="603.2",
                tier=1,
                content="A triggered ability triggers when its event occurs.",
                similarity_score=0.71,
            ),
            RetrievedChunk(
                id="chunk-b",
                document="comprehensive_rules",
                section=None,
                tier=1,
                content="An unsectioned chunk, which still needs a citation.",
                similarity_score=0.44,
            ),
        ]


class _RecordingCardSource:
    """A card source that records the filters the executor actually ran with.

    Everything is delegated. The point is not to fake the substrate but to see
    the one value a plan is forbidden to author and the executor is required
    to inject.
    """

    def __init__(self, inner):
        self._inner = inner
        self.structured_filters = []

    @property
    def result_limit(self):
        return self._inner.result_limit

    def provenance(self):
        return self._inner.provenance()

    def records(self, filters=None, *, limit=None):
        self.structured_filters.append(filters)
        return self._inner.records(filters, limit=limit)

    def search_with_trace(self, query):
        return self._inner.search_with_trace(query)

    def resolve_names(self, names):
        return self._inner.resolve_names(names)


@pytest.fixture(scope="module")
def cards(research_facade):
    """The portable 100-card bundle, behind the executor's card boundary."""
    return BundleCardSource(research_facade)


@pytest.fixture(scope="module")
def provenance(cards):
    """The identity every envelope in this file carries."""
    return cards.provenance()


@pytest.fixture(scope="module")
def executor(cards):
    """An executor with no reference layer bound, which is R3's real state."""
    return ResearchExecutor(cards)


@pytest.fixture(scope="module")
def subset_registry(tmp_path_factory):
    """A context registry over the small pack, so deck scope is observable."""
    packs = tmp_path_factory.mktemp("subset_packs")
    (packs / "kinnan_basalt.yaml").write_text(SUBSET_PACK, encoding="utf-8")
    return DeckContextRegistry(packs)


@pytest.fixture(scope="module")
def subset_executor(cards, subset_registry):
    """An executor bound to the subset pack rather than the shipped list."""
    return ResearchExecutor(cards, contexts=subset_registry)


def tag_filter(step_id, *tags, scope="corpus", limit=200):
    """Build an unranked structured step over one or more mechanic tags."""
    return TagFilterStep(
        id=step_id,
        filters=CardFilters(required_tags=tags),
        scope=scope,
        limit=limit,
        field_absence="not_a_field_query",
    )


def card_search(
    step_id, text, *, top_k=10, scope="corpus", absence="not_a_field_query"
):
    """Build a ranked step over the hybrid retrieval pipeline."""
    return CardSearchStep(
        id=step_id,
        query=CardSearchQuery(text=text, top_k=top_k),
        scope=scope,
        field_absence=absence,
    )


def plan(*steps, answer=None, intent="a hand-written R3 plan", context_id=None):
    """Build a validated plan whose answer defaults to its last step."""
    return ResearchPlan(
        intent=intent,
        context_id=context_id,
        steps=steps,
        answer_step=answer or steps[-1].id,
    )


def valid_coverage(**overrides):
    """Return a self-consistent coverage claim, with fields overridable."""
    values = {
        "eligible": 7,
        "eligible_is_exact": True,
        "examined": 7,
        "returned": 7,
        "dropped": 0,
        "truncated": False,
        "set_input_incomplete": False,
    }
    values.update(overrides)
    return values


def step_result_fields(provenance, **overrides):
    """Return every field a :class:`StepResult` needs, ready to be broken."""
    values = {
        "step_id": "a_step",
        "kind": "tag_filter",
        "tier": "fact",
        "ordering": "oracle_id",
        "provenance": provenance,
        "coverage": Coverage(**valid_coverage(eligible=0, examined=0, returned=0)),
        "field": NoFieldEvidence(reason="not_a_field_query"),
        "availability": RetrievalAvailability(
            lexical=False, dense=False, reranked=False
        ),
        "elapsed_ms": 0.0,
    }
    values.update(overrides)
    return values


def names(result):
    """Return the card names one step result carries, as a set."""
    return {card.name for card in result.cards}


# -- an envelope cannot be built without its basis -------------------------


def test_a_step_result_with_every_honesty_field_is_constructible(provenance):
    result = StepResult(**step_result_fields(provenance))
    assert result.status == "ok"
    assert result.schema_version == "research-step-result.v1"
    assert result.oracle_ids == ()


@pytest.mark.parametrize("missing", REQUIRED_HONESTY_FIELDS)
def test_omitting_any_honesty_field_refuses_the_step_result(provenance, missing):
    values = step_result_fields(provenance)
    del values[missing]
    with pytest.raises(ValidationError, match=missing):
        StepResult(**values)


def test_a_step_result_refuses_a_returned_count_that_disagrees_with_its_rows(
    provenance,
):
    row = CardRow(
        oracle_id="00000000-0000-0000-0000-00000000000a",
        name="Sol Ring",
        type_line="Artifact",
        mana_cost="{1}",
        mana_value=1.0,
        color_identity=(),
        types=("artifact",),
        tags=(ROCK_TAG,),
        rank=1,
    )
    values = step_result_fields(
        provenance,
        cards=(row,),
        coverage=Coverage(**valid_coverage(eligible=5, examined=5, returned=4)),
    )
    with pytest.raises(ValidationError, match="disagrees with the rows returned"):
        StepResult(**values)


# -- coverage refuses to contradict itself ---------------------------------


def test_a_self_consistent_coverage_claim_is_accepted():
    coverage = Coverage(
        **valid_coverage(
            eligible=10, examined=10, returned=7, dropped=3, truncated=True
        )
    )
    assert coverage.truncated is True
    assert coverage.truncation_source == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param(
            {"dropped": 3, "truncated": False},
            "truncated must be exactly whether rows were dropped",
            id="dropped_rows_without_admitting_truncation",
        ),
        pytest.param(
            {"dropped": 0, "truncated": True},
            "truncated must be exactly whether rows were dropped",
            id="claimed_truncation_with_nothing_dropped",
        ),
        pytest.param(
            {"eligible": 5, "returned": 4, "dropped": 3, "truncated": True},
            "returned plus dropped exceeds the eligible population",
            id="more_rows_accounted_for_than_were_eligible",
        ),
        pytest.param(
            {"examined": None},
            "an unmeasured examined count must say why",
            id="unmeasured_examined_count_with_no_reason",
        ),
        pytest.param(
            {"examined": 7, "examined_absent_because": "not measured"},
            "a measured examined count must not also state a reason",
            id="measured_examined_count_that_also_excuses_itself",
        ),
        pytest.param(
            {"set_input_incomplete": True},
            "an incomplete set input must name the truncating steps",
            id="incomplete_set_input_naming_no_source",
        ),
    ],
)
def test_coverage_refuses_a_claim_that_contradicts_its_own_counts(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Coverage(**valid_coverage(**overrides))


# -- the field basis is the plan's claim, not a handler default ------------


@pytest.mark.parametrize(
    "authored",
    [
        "not_a_field_query",
        "field_statistics_deferred_to_r5",
        "cohort_below_event_size_floor",
        "meta_repository_unavailable",
    ],
)
def test_a_card_step_reports_the_field_absence_the_plan_authored(executor, authored):
    run = executor.run(
        plan(
            card_search("ranked_step", "add colorless mana", absence=authored),
            TagFilterStep(
                id="tagged_step",
                filters=CardFilters(required_tags=(ROCK_TAG,)),
                field_absence=authored,
            ),
            answer="tagged_step",
        )
    )
    for step in run.steps:
        assert isinstance(step.field, NoFieldEvidence)
        assert step.field.status == "no_field_evidence"
        assert step.field.reason == authored


def test_a_rate_is_never_reported_without_the_numbers_that_make_it_readable(executor):
    run = executor.run(plan(tag_filter("rocks_step", ROCK_TAG)))
    field = run.steps[0].field
    # There is no third state: either a FieldWindow with its denominator and
    # window, or a named reason for having none.
    assert field.status == "no_field_evidence"
    assert not hasattr(field, "denominator")


# -- the retrieval ceiling is refused, not quietly applied -----------------


def test_a_top_k_above_the_retrieval_ceiling_is_not_run_rather_than_shrunk(
    executor, cards
):
    ceiling = cards.result_limit
    over = ceiling + 50
    assert over <= 100, "CardSearchQuery caps top_k at 100; pick a smaller bundle"
    run = executor.run(
        plan(card_search("greedy_step", "counter target spell", top_k=over))
    )
    step = run.steps[0]
    assert isinstance(step, StepNotRun)
    assert step.reason == "limit_exceeds_retrieval_ceiling"
    assert str(ceiling) in step.detail and str(over) in step.detail
    assert step.provenance == cards.provenance()
    assert step.oracle_ids == ()


def test_a_top_k_at_the_retrieval_ceiling_still_runs(executor, cards):
    run = executor.run(
        plan(card_search("exact_step", "add colorless mana", top_k=cards.result_limit))
    )
    assert isinstance(run.steps[0], StepResult)


# -- rules lookups are typed absences until the index exists ---------------


def rules_plan(step_id="rules_step"):
    """Build a one-step plan over the reference layer."""
    return plan(
        RulesLookupStep(id=step_id, question="what is a state-triggered ability")
    )


def test_a_rules_lookup_with_no_bound_reference_index_is_a_typed_absence(executor):
    step = executor.run(rules_plan()).steps[0]
    assert isinstance(step, StepNotRun)
    assert step.reason == "reference_index_absent"
    assert step.detail
    assert step.oracle_ids == ()


def test_a_rules_source_that_refuses_surfaces_its_own_reason(cards):
    source = _RefusingRulesSource(
        "reference_index_stale", "the active generation is short of its chunk count"
    )
    step = ResearchExecutor(cards, rules=source).run(rules_plan()).steps[0]
    assert isinstance(step, StepNotRun)
    assert step.reason == "reference_index_stale"
    assert step.detail == source.detail


def test_a_working_rules_source_returns_cited_chunks_and_no_cards(cards):
    retriever = _FakeReferenceRetriever()
    executor = ResearchExecutor(cards, rules=ReferenceRulesSource(retriever))
    step = executor.run(rules_plan()).steps[0]
    assert isinstance(step, StepResult)
    assert step.ordering == "ranked"
    assert step.cards == ()
    assert step.oracle_ids == ()
    assert [row.citation for row in step.rules] == [
        "rules:603.2",
        "rules:comprehensive_rules#2",
    ]
    assert step.coverage.returned == len(step.rules)
    # The retriever cannot say how many chunks it scored, and the envelope says
    # so rather than reporting a number it does not have.
    assert step.coverage.examined is None
    assert step.coverage.examined_absent_because


def test_a_rules_lookup_passes_the_plan_bounds_through_to_the_retriever(cards):
    retriever = _FakeReferenceRetriever()
    executor = ResearchExecutor(cards, rules=ReferenceRulesSource(retriever))
    executor.run(
        plan(
            RulesLookupStep(
                id="bounded_rules",
                question="what is a state-triggered ability",
                tier_filter=(1, 2),
                document_filter=("comprehensive_rules",),
                limit=3,
            )
        )
    )
    query = retriever.queries[-1]
    assert query.top_k == 3
    assert query.tier_filter == [1, 2]
    assert query.document_filter == ["comprehensive_rules"]


# -- deck scope is injected by the executor, never authored ----------------


def scoped_plan(scope):
    """Build the same tag filter at corpus scope and at deck scope."""
    return plan(
        tag_filter("rocks_step", ROCK_TAG, scope=scope),
        intent="which mana rocks does this list play",
        context_id=BASALT,
    )


def test_a_deck_scoped_step_returns_only_cards_the_bound_deck_plays(subset_executor):
    step = subset_executor.run(scoped_plan("deck")).steps[0]
    assert isinstance(step, StepResult)
    assert names(step) == SUBSET_ROCKS
    assert all(card.in_deck for card in step.cards)


def test_the_same_step_at_corpus_scope_reaches_cards_the_deck_does_not_play(
    subset_executor,
):
    """The comparison is the proof: without injection the two are identical."""
    in_deck = subset_executor.run(scoped_plan("deck")).steps[0]
    corpus = subset_executor.run(scoped_plan("corpus")).steps[0]
    assert names(in_deck) < names(corpus)
    assert corpus.coverage.eligible > in_deck.coverage.eligible
    assert any(not card.in_deck for card in corpus.cards)


def test_the_executor_injects_the_deck_ids_the_plan_is_forbidden_to_name(
    cards, subset_registry
):
    recording = _RecordingCardSource(cards)
    executor = ResearchExecutor(recording, contexts=subset_registry)
    deck = subset_registry.resolve(BASALT, cards=cards)

    authored = scoped_plan("deck")
    assert authored.steps[0].filters.allowed_oracle_ids == ()

    executor.run(authored)
    ran_with = recording.structured_filters[-1]
    assert ran_with.allowed_oracle_ids == deck.oracle_ids
    assert ran_with.required_tags == (ROCK_TAG,)

    executor.run(scoped_plan("corpus"))
    assert recording.structured_filters[-1].allowed_oracle_ids == ()


def test_a_deck_scoped_row_carries_the_roles_the_pack_authored(subset_executor):
    step = subset_executor.run(scoped_plan("deck")).steps[0]
    assert {card.deck_roles for card in step.cards} == {("acceleration",)}


# -- set operations ---------------------------------------------------------


def set_op_plan(kind):
    """Build a plan whose set op consumes a step that will not run.

    ``over_ceiling`` asks for more results than the bundle will ever return, so
    it is the reproducible way to put a typed absence upstream of a set op.
    """
    failing = card_search("over_ceiling", "counter target spell", top_k=100)
    survivor = tag_filter("rocks_step", ROCK_TAG)
    if kind == "difference":
        combine = DifferenceStep(
            id="combined_step", left="rocks_step", right="over_ceiling"
        )
    elif kind == "intersect":
        combine = IntersectStep(
            id="combined_step", inputs=("over_ceiling", "rocks_step")
        )
    else:
        combine = UnionStep(id="combined_step", inputs=("over_ceiling", "rocks_step"))
    return plan(failing, survivor, combine)


@pytest.mark.parametrize("kind", ["union", "intersect", "difference"])
def test_a_not_run_input_makes_the_set_op_not_run_rather_than_empty(executor, kind):
    run = executor.run(set_op_plan(kind))
    combined = run.answer
    assert isinstance(combined, StepNotRun)
    assert combined.reason == "input_step_not_run"
    assert "over_ceiling" in combined.detail
    assert "limit_exceeds_retrieval_ceiling" in combined.detail
    assert combined.oracle_ids == ()
    # The surviving input still ran; the set op refused to publish it alone.
    survivor = next(step for step in run.steps if step.step_id == "rocks_step")
    assert isinstance(survivor, StepResult)
    assert survivor.cards


def combination_plan():
    """Build every set op over two overlapping unranked populations."""
    return plan(
        tag_filter("rocks_step", ROCK_TAG),
        tag_filter("big_mana_step", BIG_MANA_TAG),
        IntersectStep(id="shared_step", inputs=("rocks_step", "big_mana_step")),
        UnionStep(id="either_step", inputs=("rocks_step", "big_mana_step")),
        DifferenceStep(id="rocks_only_step", left="rocks_step", right="big_mana_step"),
        answer="shared_step",
    )


def test_set_operations_are_order_deterministic_across_two_runs(executor):
    first = executor.run(combination_plan())
    second = executor.run(combination_plan())
    assert first.plan_sha256 == second.plan_sha256
    assert [step.oracle_ids for step in first.steps] == [
        step.oracle_ids for step in second.steps
    ]


def test_the_set_operations_combine_their_inputs_without_inventing_a_member(executor):
    run = executor.run(combination_plan())
    by_id = {step.step_id: step for step in run.steps}
    rocks = set(by_id["rocks_step"].oracle_ids)
    big = set(by_id["big_mana_step"].oracle_ids)
    assert set(by_id["shared_step"].oracle_ids) == rocks & big
    assert set(by_id["either_step"].oracle_ids) == rocks | big
    assert set(by_id["rocks_only_step"].oracle_ids) == rocks - big


def test_an_intersection_narrows_without_claiming_truncation(executor):
    shared = executor.run(combination_plan()).answer
    assert isinstance(shared, StepResult)
    assert shared.coverage.dropped == 0
    assert shared.coverage.truncated is False
    assert shared.coverage.set_input_incomplete is False
    assert shared.coverage.truncation_source == ()
    assert len(shared.cards) < len(SUBSET_ROCKS) + len(shared.cards)


def test_a_bounded_input_marks_the_set_result_incomplete_and_names_it(executor):
    bounded = plan(
        tag_filter("rocks_step", ROCK_TAG, limit=2),
        tag_filter("big_mana_step", BIG_MANA_TAG),
        IntersectStep(id="shared_step", inputs=("rocks_step", "big_mana_step")),
    )
    run = executor.run(bounded)
    cut_off = next(step for step in run.steps if step.step_id == "rocks_step")
    assert cut_off.coverage.truncated is True
    assert cut_off.coverage.returned == 2 < cut_off.coverage.eligible

    shared = run.answer
    assert shared.coverage.truncated is False
    assert shared.coverage.set_input_incomplete is True
    assert shared.coverage.truncation_source == ("rocks_step",)
    assert any("may be incomplete" in notice for notice in shared.notices)


def test_a_set_op_over_ranked_and_unranked_inputs_does_not_claim_ranked(executor):
    run = executor.run(
        plan(
            card_search("ranked_step", "add colorless mana", top_k=5),
            tag_filter("rocks_step", ROCK_TAG),
            UnionStep(id="either_step", inputs=("ranked_step", "rocks_step")),
        )
    )
    ranked, unranked, combined = run.steps
    assert ranked.ordering == "ranked"
    assert unranked.ordering == "oracle_id"
    assert combined.ordering != "ranked"
    assert run.answer_ordering == combined.ordering


def fact_result(provenance, step_id, tier):
    """Build one empty, well-formed result at a chosen assertion tier."""
    return StepResult(**step_result_fields(provenance, step_id=step_id, tier=tier))


def test_mixed_tier_inputs_refuse_to_flatten_into_one_result(executor, provenance):
    """Unreachable through the public API today, and deliberately guarded.

    Every R3 step is Fact tier, so no hand-written plan can reach this branch.
    R5's field statistics and R6's simulator studies are not Fact tier, and the
    moment one of them becomes a set-op input, flattening would present a
    measurement as a fact. The handler is called directly for that reason.
    """
    ctx = _RunContext(
        plan=ResearchPlan(
            intent="a placeholder plan for the private handler",
            clarification_required=True,
        ),
        provenance=provenance,
        deck=None,
        outcomes={
            "facts_step": fact_result(provenance, "facts_step", "fact"),
            "reading_step": fact_result(provenance, "reading_step", "interpretation"),
        },
    )
    combined = executor._set_operation(
        UnionStep(id="mixed_step", inputs=("facts_step", "reading_step")), ctx
    )
    assert isinstance(combined, StepNotRun)
    assert combined.reason == "input_tier_mismatch"
    assert "fact" in combined.detail and "interpretation" in combined.detail


def test_same_tier_inputs_to_the_private_handler_still_combine(executor, provenance):
    ctx = _RunContext(
        plan=ResearchPlan(
            intent="a placeholder plan for the private handler",
            clarification_required=True,
        ),
        provenance=provenance,
        deck=None,
        outcomes={
            "facts_step": fact_result(provenance, "facts_step", "fact"),
            "more_facts_step": fact_result(provenance, "more_facts_step", "fact"),
        },
    )
    combined = executor._set_operation(
        UnionStep(id="ok_step", inputs=("facts_step", "more_facts_step")), ctx
    )
    assert isinstance(combined, StepResult)
    assert combined.tier == "fact"


# -- the charter absences, as a mechanism ----------------------------------


@pytest.mark.parametrize("model", [CardRow, DeckContext, StepResult, ProfileFacetValue])
def test_no_result_model_carries_a_price_or_a_collection_field(model):
    """Mirrors ``test_cedh_builder.TestPriceIsNotAnInput``: nothing to reach for.

    cEDH is proxy-normal and the engine does not know who is asking, so a price
    or an owned-cards field would not be a display detail — it would become a
    tie-break the first time anyone sorted by it (ADR-025).
    """
    offending = {
        name
        for name in model.model_fields
        if any(token in name.casefold() for token in FORBIDDEN_FIELD_TOKENS)
    }
    assert not offending, f"{model.__name__} carries {sorted(offending)}"


def test_a_returned_card_row_exposes_no_price_anywhere_in_its_payload(executor):
    step = executor.run(plan(tag_filter("rocks_step", ROCK_TAG))).steps[0]
    assert step.cards
    for card in step.cards:
        payload = card.model_dump()
        for token in FORBIDDEN_FIELD_TOKENS:
            assert not [name for name in payload if token in name.casefold()]


# -- the enclosure a narrator may not escape -------------------------------


def test_the_result_set_is_the_union_of_every_step_and_the_answer_is_one_step(
    executor,
):
    run = executor.run(
        plan(
            tag_filter("rocks_step", ROCK_TAG),
            tag_filter("free_spells_step", FREE_SPELL_TAG),
            answer="free_spells_step",
        )
    )
    rocks, free_spells = run.steps
    assert not set(rocks.oracle_ids) & set(free_spells.oracle_ids)

    assert run.returned_oracle_ids == free_spells.oracle_ids
    assert set(run.result_set_oracle_ids) == set(rocks.oracle_ids) | set(
        free_spells.oracle_ids
    )
    # The enclosure is strictly wider than the answer, which is exactly why a
    # narrator is checked against the result set and not against the answer.
    assert set(run.returned_oracle_ids) < set(run.result_set_oracle_ids)


def test_a_result_set_never_contains_an_id_no_step_produced(executor):
    run = executor.run(combination_plan())
    produced = {oracle_id for step in run.steps for oracle_id in step.oracle_ids}
    assert set(run.result_set_oracle_ids) == produced
    assert len(run.result_set_oracle_ids) == len(set(run.result_set_oracle_ids))


def test_a_run_whose_answer_did_not_run_returns_no_ids_but_keeps_the_rest(executor):
    run = executor.run(set_op_plan("union"))
    assert run.returned_oracle_ids == ()
    assert run.answer_ordering is None
    assert run.result_set_oracle_ids  # the surviving input is still enclosed


# -- zero-step plans are answers, and run -----------------------------------


def test_a_clarification_plan_runs_and_produces_no_steps(executor):
    run = executor.run(
        ResearchPlan(
            intent="which of the two engines do you mean",
            clarification_required=True,
        ),
        question_id="ambiguous-probe",
    )
    assert run.steps == ()
    assert run.clarification_required is True
    assert run.answer is None
    assert run.answer_step is None
    assert run.question_id == "ambiguous-probe"
    assert run.returned_oracle_ids == ()
    assert run.result_set_oracle_ids == ()


def test_an_absence_plan_carries_every_absence_it_stated(executor):
    run = executor.run(
        ResearchPlan(
            intent="how much would this deck cost to build",
            stated_absences=(
                StatedAbsence(
                    reason="price_is_not_in_the_engine",
                    detail="the engine carries no price field to read",
                    answers_expectation=0,
                ),
                StatedAbsence(
                    reason="collection_is_not_in_the_engine",
                    detail="the engine does not know who is asking",
                    answers_expectation=1,
                ),
            ),
        )
    )
    assert run.steps == ()
    assert run.clarification_required is False
    assert [absence.reason for absence in run.stated_absences] == [
        "price_is_not_in_the_engine",
        "collection_is_not_in_the_engine",
    ]
    assert [absence.answers_expectation for absence in run.stated_absences] == [0, 1]
    assert run.result_set_oracle_ids == ()


# -- a rules lookup bounded in characters, not chunks -----------------------


class _SizedReferenceRetriever:
    """A retriever returning ``top_k`` chunks of a fixed size, best first."""

    def __init__(self, size):
        self.size = size
        self.queries = []

    def retrieve(self, query):
        self.queries.append(query)
        return [
            RetrievedChunk(
                id=f"chunk-{rank}",
                document="comprehensive_rules",
                section=f"CR {rank}",
                tier=1,
                content="x" * self.size,
                similarity_score=1.0 - rank / 100,
            )
            for rank in range(1, query.top_k + 1)
        ]


def test_a_char_budget_truncates_in_rank_order_and_discloses_it(cards):
    """Chunks are a unit that changes meaning with the chunker; characters are not.

    At limit 6 a re-chunk that halved chunk size halved the text a lookup
    admitted, and the same fix read as a regression at one bound and as a
    large improvement at a fixed character budget. So the budget is the
    operative bound, applied after ranking and in rank order, and what it
    dropped is on the coverage rather than silently absent.
    """
    retriever = _SizedReferenceRetriever(size=1000)
    executor = ResearchExecutor(cards, rules=ReferenceRulesSource(retriever))
    step = executor.run(
        plan(
            RulesLookupStep(
                id="budgeted",
                question="what is a state-triggered ability",
                limit=12,
                char_budget=2500,
            )
        )
    ).steps[0]
    assert isinstance(step, StepResult)
    assert retriever.queries[-1].top_k == 12, "the cap is what the retriever sees"
    assert [row.rank for row in step.rules] == [1, 2], "2,000 fits; a third would not"
    assert step.coverage.eligible == 12
    assert step.coverage.returned == 2
    assert step.coverage.dropped == 10
    assert step.coverage.truncated is True
    assert step.coverage.truncation_source == ("char_budget",)
    assert any("char budget 2500" in note for note in step.availability.notices)


def test_a_char_budget_below_one_chunk_still_returns_the_top_chunk(cards):
    """A budget smaller than the best passage returns the best passage, not nothing."""
    retriever = _SizedReferenceRetriever(size=1000)
    executor = ResearchExecutor(cards, rules=ReferenceRulesSource(retriever))
    step = executor.run(
        plan(
            RulesLookupStep(
                id="tiny",
                question="what is a state-triggered ability",
                limit=3,
                char_budget=500,
            )
        )
    ).steps[0]
    assert isinstance(step, StepResult)
    assert [row.rank for row in step.rules] == [1]
    assert step.coverage.dropped == 2


def test_no_char_budget_means_the_chunk_limit_alone_applies(cards):
    retriever = _SizedReferenceRetriever(size=1000)
    executor = ResearchExecutor(cards, rules=ReferenceRulesSource(retriever))
    step = executor.run(
        plan(
            RulesLookupStep(
                id="plain", question="what is a state-triggered ability", limit=3
            )
        )
    ).steps[0]
    assert isinstance(step, StepResult)
    assert len(step.rules) == 3
    assert step.coverage.truncated is False
