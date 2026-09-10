"""Deck context resolution: two registered ids, one list, and no requester.

Two properties are worth stating up front, because they are what the module
buys and what these tests are here to keep.

``cedh:kinnan-healthy-baseline`` is a declared **alias**, not a second deck.
``context.py`` deliberately publishes no deck hash — ``deck_sha256`` is a
cross-repository contract and a fourth implementation of it would re-create the
bug that contract replaced — so "these two ids name the same 100 cards" is
asserted by comparing the Oracle ids directly.

``context.py`` also deliberately does not import ``sabermetrics.cedh.packs``:
ADR-030's borrow list does not include it, so the pack YAML is read twice, by
two readers, in two packages. Tests are not boundary-checked, so this file
imports both and asserts they agree — which is the whole justification for the
duplication being acceptable.
"""

from __future__ import annotations

import hashlib
import inspect

import pytest
from pydantic import ValidationError

from sabermetrics.assistant.context import (
    PACKS_DIR,
    ContextWinPackage,
    DeckContext,
    DeckContextRegistry,
    DeckContextUnresolvedError,
    UnknownDeckContextError,
    known_context_ids,
    resolve_deck_context,
)
from sabermetrics.assistant.envelope import CorpusProvenance
from sabermetrics.assistant.eval.models import load_questions
from sabermetrics.assistant.sources import BundleCardSource, CardSource
from sabermetrics.cedh.adapters_fixture import FixtureCardRepository
from sabermetrics.cedh.packs import PackRegistry

BASALT = "cedh:kinnan-basalt-fixture"
BASELINE = "cedh:kinnan-healthy-baseline"
PACK_ID = "kinnan_basalt"
PACK_FILE = PACKS_DIR / f"{PACK_ID}.yaml"

#: Field-name substrings that must never appear on a context model. A field
#: that exists gets read, and a price or a collection that gets read becomes a
#: tie-break (ADR-025).
FORBIDDEN_FIELD_TOKENS = (
    "price",
    "usd",
    "budget",
    "cost",
    "owner",
    "owned",
    "collection",
    "requester",
    "user",
)


class _StubCardSource:
    """A :class:`CardSource` whose corpus is exactly what it is told.

    Only ``provenance`` and ``resolve_names`` take part in resolving a context.
    The ranked surface raises, so a regression that starts searching the corpus
    while building a deck context fails loudly instead of quietly working.
    """

    def __init__(self, *, missing=(), duplicated=()):
        self.missing = frozenset(missing)
        self.duplicated = frozenset(duplicated)

    @property
    def result_limit(self):
        return 50

    def provenance(self):
        return CorpusProvenance(
            bundle_id="a" * 64,
            corpus_source_view="fixture:stub-card-source",
            corpus_row_count=0,
            corpus_sha256="b" * 64,
            document_version="stub.v1",
            retrieval_config_sha256="c" * 64,
            tag_library_sha256="d" * 64,
            tag_content_sha256="e" * 64,
            tag_row_count=0,
        )

    def records(self, filters=None, *, limit=None):
        raise AssertionError("resolving a deck context must not read the catalog")

    def search_with_trace(self, query):
        raise AssertionError("resolving a deck context must not run a search")

    def resolve_names(self, names):
        found = {}
        for index, name in enumerate(names):
            if name in self.missing:
                continue
            ids = (f"{index:08d}-0000-0000-0000-00000000000a",)
            if name in self.duplicated:
                ids = (*ids, f"{index:08d}-0000-0000-0000-00000000000b")
            found[name] = ids
        return found


@pytest.fixture(scope="module")
def bundle_cards(research_facade):
    """The portable 100-card bundle, behind the executor's card boundary."""
    return BundleCardSource(research_facade)


@pytest.fixture(scope="module")
def basalt(bundle_cards):
    """The primary Kinnan context, resolved once for the read-only tests."""
    return resolve_deck_context(BASALT, cards=bundle_cards)


@pytest.fixture(scope="module")
def pack_reading():
    """The same pack read through ``cedh.packs``, plus a name for every id.

    The fixture card repository and the portable retrieval bundle are both
    built from ``fixtures/cedh/cards.json``, so the two readers resolve names
    to the *same* Oracle ids and the comparison can be made on ids as well as
    on names.
    """
    repo = FixtureCardRepository()
    pack = PackRegistry(repo).get(PACK_ID)
    wanted = [
        *pack.commander.oracle_ids,
        *pack.pool,
        *pack.auto_include,
        *pack.primary_win_package.piece_oracle_ids,
    ]
    for package in pack.secondary_win_packages:
        wanted.extend(package.piece_oracle_ids)
    names = {
        oracle_id: facts.name
        for oracle_id, facts in repo.get_by_oracle_ids(wanted).items()
    }
    return pack, names


def _id_for(context, name):
    """Return the one Oracle id a resolved context gives a card name."""
    return next(
        oracle_id
        for oracle_id, card_name in context.names_by_oracle_id.items()
        if card_name == name
    )


# -- registration ---------------------------------------------------------


def test_exactly_the_two_advertised_context_ids_are_registered():
    assert known_context_ids() == (BASALT, BASELINE)


@pytest.mark.parametrize("context_id", [BASALT, BASELINE])
def test_a_registered_context_resolves_to_one_commander_and_ninety_nine_cards(
    context_id, bundle_cards
):
    context = resolve_deck_context(context_id, cards=bundle_cards)
    assert context.context_id == context_id
    assert context.pack_id == PACK_ID
    assert context.commander_names == ("Kinnan, Bonder Prodigy",)
    assert len(context.commander_oracle_ids) == 1
    assert len(context.library_oracle_ids) == 99
    assert len(set(context.library_oracle_ids)) == 99
    assert not set(context.commander_oracle_ids) & set(context.library_oracle_ids)
    assert len(context.names_by_oracle_id) == 100


@pytest.mark.parametrize("context_id", [BASALT, BASELINE])
def test_oracle_ids_is_the_commander_then_the_library_deduplicated(
    context_id, bundle_cards
):
    context = resolve_deck_context(context_id, cards=bundle_cards)
    assert context.oracle_ids == (
        *context.commander_oracle_ids,
        *context.library_oracle_ids,
    )
    assert len(context.oracle_ids) == len(set(context.oracle_ids)) == 100


def test_the_library_keeps_pack_declaration_order_not_oracle_id_order(basalt):
    """Slicing a hash ordering at fifty makes half the deck unreachable."""
    assert list(basalt.library_oracle_ids) != sorted(basalt.library_oracle_ids)
    assert basalt.names_by_oracle_id[basalt.library_oracle_ids[0]] == "Ancient Tomb"


# -- the baseline is an alias, not a second deck ---------------------------


def test_the_healthy_baseline_is_a_declared_alias_of_the_basalt_fixture(bundle_cards):
    basalt = resolve_deck_context(BASALT, cards=bundle_cards)
    baseline = resolve_deck_context(BASELINE, cards=bundle_cards)
    assert basalt.baseline_of is None
    assert baseline.baseline_of == BASALT
    assert baseline.context_id != basalt.context_id
    assert baseline.label != basalt.label


def test_the_two_context_ids_name_an_identical_list_of_oracle_ids(bundle_cards):
    """The module publishes no deck hash, so compare the ids themselves."""
    basalt = resolve_deck_context(BASALT, cards=bundle_cards)
    baseline = resolve_deck_context(BASELINE, cards=bundle_cards)
    assert sorted(baseline.oracle_ids) == sorted(basalt.oracle_ids)
    assert baseline.commander_oracle_ids == basalt.commander_oracle_ids
    assert baseline.library_oracle_ids == basalt.library_oracle_ids
    assert baseline.roles == basalt.roles
    assert baseline.pack_id == basalt.pack_id
    assert baseline.pack_sha256 == basalt.pack_sha256


def test_the_context_publishes_a_pack_hash_and_no_deck_hash():
    """``deck_sha256`` is a cross-repository contract; this is not it."""
    assert "pack_sha256" in DeckContext.model_fields
    assert "deck_sha256" not in DeckContext.model_fields
    assert not [name for name in DeckContext.model_fields if name.startswith("deck_")]


# -- cross-check against the other reader of the same YAML -----------------


def test_both_pack_readers_agree_on_the_commander(basalt, pack_reading):
    pack, _ = pack_reading
    assert basalt.commander_names == pack.commander.names
    assert basalt.commander_oracle_ids == pack.commander.oracle_ids


def test_both_pack_readers_agree_on_the_set_of_card_names(basalt, pack_reading):
    pack, names = pack_reading
    assert {names[oracle_id] for oracle_id in pack.pool} == {
        basalt.names_by_oracle_id[oracle_id] for oracle_id in basalt.library_oracle_ids
    }
    assert sorted(pack.pool) == sorted(basalt.library_oracle_ids)


def test_both_pack_readers_agree_on_every_role_assignment(basalt, pack_reading):
    pack, names = pack_reading
    from_pack: dict[str, set[str]] = {}
    for oracle_id, roles in pack.pool.items():
        for role in roles:
            from_pack.setdefault(role, set()).add(names[oracle_id])
    from_context = {
        role: {basalt.names_by_oracle_id[oracle_id] for oracle_id in oracle_ids}
        for role, oracle_ids in basalt.roles.items()
    }
    assert from_context == from_pack
    assert basalt.role_counts == {
        role: len(members) for role, members in from_pack.items()
    }


def test_both_pack_readers_agree_on_the_role_targets(basalt, pack_reading):
    pack, _ = pack_reading
    assert basalt.role_targets == dict(pack.role_budget.targets)
    assert set(basalt.role_targets) >= set(basalt.roles)


def test_both_pack_readers_agree_on_the_win_package_pieces(basalt, pack_reading):
    pack, names = pack_reading
    assert basalt.primary_win_package.piece_names == tuple(
        names[oracle_id] for oracle_id in pack.primary_win_package.piece_oracle_ids
    )
    assert (
        basalt.primary_win_package.piece_oracle_ids
        == pack.primary_win_package.piece_oracle_ids
    )
    assert basalt.primary_win_package.name == pack.primary_win_package.name
    assert basalt.primary_win_package.kind == pack.primary_win_package.kind.value
    assert len(basalt.secondary_win_packages) == len(pack.secondary_win_packages)
    for mine, theirs in zip(
        basalt.secondary_win_packages, pack.secondary_win_packages, strict=True
    ):
        assert mine.name == theirs.name
        assert mine.piece_oracle_ids == theirs.piece_oracle_ids
        assert mine.piece_names == tuple(
            names[oracle_id] for oracle_id in theirs.piece_oracle_ids
        )


def test_both_pack_readers_agree_on_the_auto_include_list(basalt, pack_reading):
    pack, names = pack_reading
    assert basalt.auto_include_oracle_ids == pack.auto_include
    assert {names[oracle_id] for oracle_id in pack.auto_include} <= set(
        basalt.names_by_oracle_id.values()
    )


def test_roles_for_reports_every_authored_role_of_one_card_sorted(basalt):
    monolith = _id_for(basalt, "Basalt Monolith")
    assert basalt.roles_for(monolith) == ("acceleration", "win_package")
    assert basalt.roles_for(_id_for(basalt, "Sol Ring")) == ("acceleration",)
    assert basalt.roles_for("00000000-0000-0000-0000-000000000000") == ()


# -- pack identity --------------------------------------------------------


def test_the_pack_sha256_is_the_hash_of_the_pack_file_bytes(basalt):
    assert basalt.pack_sha256 == hashlib.sha256(PACK_FILE.read_bytes()).hexdigest()


def test_the_pack_sha256_is_stable_across_two_independent_resolutions(bundle_cards):
    first = resolve_deck_context(BASALT, cards=bundle_cards)
    second = resolve_deck_context(BASALT, cards=bundle_cards)
    assert first.pack_sha256 == second.pack_sha256
    assert first == second


def test_a_registry_caches_one_resolution_per_bundle(bundle_cards):
    registry = DeckContextRegistry()
    assert registry.resolve(BASALT, cards=bundle_cards) is registry.resolve(
        BASALT, cards=bundle_cards
    )


# -- failure is named, never silently shortened ----------------------------


def test_an_unregistered_context_id_names_the_registered_ids(bundle_cards):
    with pytest.raises(UnknownDeckContextError) as excinfo:
        resolve_deck_context("cedh:not-a-registered-deck", cards=bundle_cards)
    message = str(excinfo.value)
    assert "cedh:not-a-registered-deck" in message
    for context_id in known_context_ids():
        assert context_id in message


def test_a_corpus_missing_pack_cards_names_what_did_not_resolve():
    cards = _StubCardSource(missing={"Basalt Monolith", "Force of Will"})
    with pytest.raises(DeckContextUnresolvedError) as excinfo:
        resolve_deck_context(BASALT, cards=cards)
    message = str(excinfo.value)
    assert BASALT in message
    assert "unresolved" in message
    assert "Basalt Monolith" in message
    assert "Force of Will" in message
    assert "Sol Ring" not in message


def test_a_name_carried_by_two_cards_is_ambiguous_rather_than_resolved():
    cards = _StubCardSource(duplicated={"Sol Ring"})
    with pytest.raises(DeckContextUnresolvedError, match="ambiguous: Sol Ring"):
        resolve_deck_context(BASALT, cards=cards)


def test_a_missing_strategy_pack_file_is_reported_not_treated_as_empty(
    bundle_cards, tmp_path
):
    registry = DeckContextRegistry(tmp_path)
    with pytest.raises(DeckContextUnresolvedError, match="missing"):
        registry.resolve(BASALT, cards=bundle_cards)


def test_the_stub_card_source_satisfies_the_card_source_protocol():
    assert isinstance(_StubCardSource(), CardSource)


# -- the context states its own limits -------------------------------------


@pytest.mark.parametrize("context_id", [BASALT, BASELINE])
def test_r3_carries_no_role_threshold_and_no_field_comparison(context_id, bundle_cards):
    context = resolve_deck_context(context_id, cards=bundle_cards)
    assert context.thresholds_available is False
    assert context.field_comparison_available is False


# -- the golden question set has no dangling context id --------------------


def test_every_context_id_the_golden_question_set_binds_is_registered():
    registered = set(known_context_ids())
    bound = {
        question.context_id
        for question in load_questions().questions
        if question.context_id is not None
    }
    assert bound, "the golden question set binds no deck context at all"
    assert not bound - registered, f"dangling context ids: {sorted(bound - registered)}"


# -- ADR-025: no price, no collection, no requester ------------------------


class TestPriceAndOwnershipAreNotInTheContext:
    """A context is a property of a list, not of who asked about it.

    Mirrors ``test_cedh_builder.py::TestOwnershipIsNotAnInput``: the point is
    that there is no field to read and no argument to pass, because a price or
    a collection that can be read eventually becomes a tie-break.
    """

    @pytest.mark.parametrize("model", [DeckContext, ContextWinPackage])
    def test_no_context_model_has_a_price_or_ownership_field(self, model):
        offenders = [
            name
            for name in model.model_fields
            if any(token in name for token in FORBIDDEN_FIELD_TOKENS)
        ]
        assert offenders == []

    def test_a_caller_adding_a_price_field_fails_loudly(self, basalt):
        payload = basalt.model_dump()
        payload["price_usd"] = 1.0
        with pytest.raises(ValidationError, match="price_usd"):
            DeckContext(**payload)

    def test_resolution_takes_the_context_and_the_corpus_and_nothing_else(self):
        assert set(inspect.signature(resolve_deck_context).parameters) == {
            "context_id",
            "cards",
        }
        assert set(inspect.signature(DeckContextRegistry.resolve).parameters) == {
            "self",
            "context_id",
            "cards",
        }

    def test_a_caller_still_passing_a_requester_fails_loudly(self):
        with pytest.raises(TypeError):
            resolve_deck_context(BASALT, cards=_StubCardSource(), owner_id="dylan")
