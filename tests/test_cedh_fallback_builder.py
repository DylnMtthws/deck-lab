"""The generic, catalog-backed starter builder for unsupported commanders.

Uses a small hand-built fake :class:`CardRepository` rather than the shared
``cedh_cards`` fixture: that fixture only covers the Kinnan pack's pool, and
this module's whole point is commanders no curated pack names. The fake here
is a plain test double — not a claim about any real card's oracle text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sabermetrics.cedh.domain import CommanderIdentity, Legality
from sabermetrics.cedh.errors import CandidateConstraintViolation
from sabermetrics.cedh.fallback_builder import (
    GENERIC_CATALOG,
    UTILITY_LANDS,
    build_fallback_candidate,
    looks_like_commander,
)
from sabermetrics.cedh.repositories import CardFacts, CorpusSnapshot

_BASIC_NAMES = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
    "Wastes": "C",
}


def _facts(
    name: str, color_identity: str, *, type_line: str = "", oracle_text: str = ""
) -> CardFacts:
    oid = "oracle:" + name.lower().replace(",", "").replace("'", "").replace(" ", "-")
    colors = tuple(color_identity) if color_identity != "C" else ()
    return CardFacts(
        oracle_id=oid,
        name=name,
        type_line=type_line,
        oracle_text=oracle_text,
        color_identity=colors,
    )


class FakeCardRepository:
    """A minimal, hand-authored :class:`CardRepository` double.

    Covers every name the generic catalog can ask for, plus basics and
    utility lands, all with real colour identities and ``LEGAL`` status,
    so the catalog-sufficiency tests exercise *this module's* fill logic
    rather than gaps in the double.
    """

    def __init__(
        self,
        *,
        extra: dict[str, CardFacts] | None = None,
        banned: set[str] = frozenset(),
    ):
        self._by_name: dict[str, CardFacts] = {}
        for color, roles in GENERIC_CATALOG.items():
            for names in roles.values():
                for name in names:
                    self._by_name[name] = _facts(name, color)
        for name in UTILITY_LANDS:
            self._by_name[name] = _facts(name, "C")
        for name, color in _BASIC_NAMES.items():
            self._by_name[name] = _facts(name, color)
        for name, facts in (extra or {}).items():
            self._by_name[name] = facts
        self._banned = banned

    def snapshot(self) -> CorpusSnapshot:
        return CorpusSnapshot(source_view="fake.catalog", row_count=len(self._by_name))

    def get_by_oracle_ids(self, oracle_ids):
        by_id = {f.oracle_id: f for f in self._by_name.values()}
        return {oid: by_id[oid] for oid in oracle_ids if oid in by_id}

    def resolve_names(self, names):
        return {name: self._by_name[name] for name in names if name in self._by_name}

    def legality(self, oracle_ids, fmt="commander"):
        by_id = {f.oracle_id: f for f in self._by_name.values()}
        out = {}
        for oid in oracle_ids:
            if oid not in by_id:
                continue
            out[oid] = Legality.BANNED if oid in self._banned else Legality.LEGAL
        return out


class EmptyCardRepository:
    """A repository that recognises nothing — the "catalog insufficient" case."""

    def snapshot(self) -> CorpusSnapshot:
        return CorpusSnapshot(source_view="fake.empty", row_count=0)

    def get_by_oracle_ids(self, oracle_ids):
        return {}

    def resolve_names(self, names):
        return {}

    def legality(self, oracle_ids, fmt="commander"):
        return {}


def _commander(name: str, oracle_id: str, color_identity: str) -> CommanderIdentity:
    return CommanderIdentity(
        oracle_ids=(oracle_id,), names=(name,), color_identity=tuple(color_identity)
    )


# Synthetic stand-ins for real legal commanders. Colour identities here are
# test fixture facts, not an assertion about the real cards' printed text.
VIVI = _commander("Vivi Ornitier", "oracle:vivi-ornitier", "R")
SAURON = _commander("Sauron, the Dark Lord", "oracle:sauron-the-dark-lord", "B")
FIVE_COLOR = _commander("Atraxa, Grand Unifier", "oracle:atraxa-grand-unifier", "WUBRG")


class TestBuildsAValidNinetyNine:
    @pytest.mark.parametrize("commander", [VIVI, SAURON, FIVE_COLOR])
    def test_exactly_ninety_nine_cards(self, commander):
        candidate = build_fallback_candidate(commander, FakeCardRepository())
        assert sum(c.quantity for c in candidate.cards) == 99

    @pytest.mark.parametrize("commander", [VIVI, SAURON, FIVE_COLOR])
    def test_no_duplicate_oracle_ids_outside_basics(self, commander):
        candidate = build_fallback_candidate(commander, FakeCardRepository())
        ids = [c.oracle_id for c in candidate.cards]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("commander", [VIVI, SAURON, FIVE_COLOR])
    def test_commander_is_not_in_its_own_ninety_nine(self, commander):
        candidate = build_fallback_candidate(commander, FakeCardRepository())
        ids = {c.oracle_id for c in candidate.cards}
        assert not ids & set(commander.oracle_ids)

    @pytest.mark.parametrize("commander", [VIVI, SAURON, FIVE_COLOR])
    def test_every_card_is_within_commander_colour_identity(self, commander):
        cards = FakeCardRepository()
        candidate = build_fallback_candidate(commander, cards)
        facts = cards.get_by_oracle_ids([c.oracle_id for c in candidate.cards])
        commander_colors = set(commander.color_identity)
        for card_facts in facts.values():
            assert set(card_facts.color_identity) <= commander_colors

    @pytest.mark.parametrize("commander", [VIVI, SAURON, FIVE_COLOR])
    def test_every_card_is_a_real_resolved_catalog_card(self, commander):
        """No invented cards: every oracle_id traces back to a real lookup."""
        cards = FakeCardRepository()
        candidate = build_fallback_candidate(commander, cards)
        facts = cards.get_by_oracle_ids([c.oracle_id for c in candidate.cards])
        assert set(facts) == {c.oracle_id for c in candidate.cards}
        for card in candidate.cards:
            assert facts[card.oracle_id].name == card.name

    @pytest.mark.parametrize("commander", [VIVI, SAURON, FIVE_COLOR])
    def test_no_win_packages_are_claimed(self, commander):
        """A generic starter must not pretend to have a validated win plan."""
        candidate = build_fallback_candidate(commander, FakeCardRepository())
        assert candidate.win_packages == ()

    def test_role_targets_are_respected_or_explicitly_noted_short(self):
        from sabermetrics.cedh.fallback_builder import ROLE_TARGETS

        candidate = build_fallback_candidate(FIVE_COLOR, FakeCardRepository())
        for role, target in ROLE_TARGETS.items():
            have = candidate.role_counts.get(role, 0)
            assert have <= target
            if have < target:
                assert any(f"role '{role}'" in note for note in candidate.notes)


class TestDeterminism:
    def test_two_builds_are_byte_identical(self):
        stamp = datetime(2026, 10, 12, tzinfo=UTC)
        kwargs = {"candidate_id": "fixed", "generated_at": stamp}
        first = build_fallback_candidate(SAURON, FakeCardRepository(), **kwargs)
        second = build_fallback_candidate(SAURON, FakeCardRepository(), **kwargs)
        assert first.to_json() == second.to_json()


class TestLegalityAndCorpusGaps:
    def test_a_banned_catalog_card_is_excluded_and_noted(self):
        cards = FakeCardRepository(banned={"oracle:sol-ring"})
        candidate = build_fallback_candidate(VIVI, cards)
        assert "oracle:sol-ring" not in {c.oracle_id for c in candidate.cards}
        assert any("Commander legality is banned" in note for note in candidate.notes)
        assert sum(c.quantity for c in candidate.cards) == 99

    def test_a_card_outside_colour_identity_is_excluded_and_noted(self):
        # Give Vivi (mono-R) access to a catalog name that resolves to a
        # green card facts record — simulating a name collision the corpus
        # knows about but that does not fit this commander.
        cards = FakeCardRepository(extra={"Beast Within": _facts("Beast Within", "G")})
        candidate = build_fallback_candidate(VIVI, cards)
        assert "oracle:beast-within" not in {c.oracle_id for c in candidate.cards}


class TestCatalogInsufficient:
    def test_raises_with_an_explicit_detail_when_the_corpus_has_nothing(self):
        with pytest.raises(CandidateConstraintViolation, match="insufficient"):
            build_fallback_candidate(VIVI, EmptyCardRepository())

    def test_raises_for_a_colourless_commander_with_the_small_demo_catalog(self):
        """The curated colourless-only bucket alone is thin; this is a
        documented limitation of the generic catalog's current coverage,
        not a silent wrong answer."""
        colorless = _commander("Kozilek, Butcher of Truth", "oracle:kozilek", "")
        # Shrink the colourless-only repository below the usefulness floor by
        # dropping half the colourless catalog, standing in for a corpus
        # that simply does not carry every curated name.
        cards = FakeCardRepository()
        for name in list(cards._by_name)[:40]:
            cards._by_name.pop(name, None)
        with pytest.raises(CandidateConstraintViolation):
            build_fallback_candidate(colorless, cards)


class TestLooksLikeCommander:
    def test_legendary_creature_qualifies(self):
        facts = _facts(
            "Sauron, the Dark Lord", "B", type_line="Legendary Creature — Avatar"
        )
        assert looks_like_commander(facts)

    def test_explicit_oracle_text_exception_qualifies(self):
        facts = _facts(
            "The Prismatic Bridge",
            "",
            type_line="Enchantment",
            oracle_text="can be your commander",
        )
        assert looks_like_commander(facts)

    def test_an_ordinary_card_does_not_qualify(self):
        facts = _facts("Lightning Bolt", "R", type_line="Instant")
        assert not looks_like_commander(facts)


@pytest.mark.parametrize(
    "commander", [VIVI, SAURON, FIVE_COLOR, _commander("Kozilek", "oracle:kozilek", "")]
)
def test_starter_has_bounded_land_count_and_meaningful_roles(commander):
    candidate = build_fallback_candidate(commander, FakeCardRepository())
    assert 36 <= candidate.role_counts["land"] <= 40
    for role in ("acceleration", "interaction", "card_advantage"):
        assert candidate.role_counts[role] >= 8
    assert candidate.role_counts["protection"] >= 2


def test_vivi_and_sauron_keep_named_engine_cards():
    vivi = build_fallback_candidate(
        _commander("Vivi Ornitier", "oracle:vivi", "UR"), FakeCardRepository()
    )
    sauron = build_fallback_candidate(
        _commander("Sauron, the Dark Lord", "oracle:sauron", "UBR"),
        FakeCardRepository(),
    )
    assert {"Talrand, Sky Summoner", "Young Pyromancer"} <= {
        card.name for card in vivi.cards
    }
    assert {"Call of the Ring", "Nazgul", "Dreadhorde Invasion"} <= {
        card.name for card in sauron.cards
    }


def test_fallback_lab_persists_and_imports_without_model_calls(tmp_path):
    from types import SimpleNamespace

    from sabermetrics import db
    from sabermetrics.cedh.domain import LabRequest
    from sabermetrics.cedh.fallback_builder import PACK_ID
    from sabermetrics.cedh.lab import CedhDeckLab
    from sabermetrics.cedh.simulator import DisabledSimulatorClient
    from sabermetrics.deck_documents import DeckDocumentRepo
    from sabermetrics.ui.cedh_routes import _persist_result
    from scripts.setup_db import setup_database

    commander = _commander("Vivi Ornitier", "oracle:vivi", "UR")
    fact = CardFacts(
        oracle_id="oracle:vivi",
        name="Vivi Ornitier",
        color_identity=("U", "R"),
        type_line="Legendary Creature — Wizard",
    )
    cards = FakeCardRepository(extra={fact.name: fact})

    class ForbiddenGateway:
        def call(self, *args, **kwargs):
            pytest.fail("fallback must not call an external model")

    lab = CedhDeckLab(
        cards=cards,
        meta=SimpleNamespace(),
        registry=SimpleNamespace(),
        simulator=DisabledSimulatorClient(),
        gateway=ForbiddenGateway(),
    )
    result = lab.run(
        LabRequest(
            pack_id=PACK_ID, commander=commander, raw_intent="Build a useful starter"
        )
    )
    assert result.candidate is not None and result.usages == []
    path = tmp_path / "starter.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="starter@example.test",
        display_name="Starter",
        role="user",
        status="active",
    )
    # Candidate import resolves through the existing builder corpus by oracle id.
    with db.connect(path) as conn:
        for card in (
            fact,
            *cards.get_by_oracle_ids(
                [c.oracle_id for c in result.candidate.cards]
            ).values(),
        ):
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,color_identity,is_legal_commander,is_legal_in_99) VALUES(?,?,?,?,?,?,?)",
                (
                    card.oracle_id,
                    card.oracle_id,
                    card.name,
                    "Basic Land" if card.name in _BASIC_NAMES else card.type_line,
                    json.dumps(list(card.color_identity)),
                    int(card.oracle_id == fact.oracle_id),
                    1,
                ),
            )
        conn.commit()
    candidate_id = _persist_result(path, owner, result)
    repo = DeckDocumentRepo(path)
    deck_id = repo.import_candidate(owner, candidate_id)
    document = repo.get(owner, deck_id)
    assert document["validation"]["total_count"] == 100
    assert document["validation"]["legal"]
    assert document["source_kind"] == "candidate"


def test_build_ui_offers_and_executes_starter_for_unpacked_commander(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from sabermetrics import db
    from sabermetrics.cedh.fallback_builder import PACK_ID
    from sabermetrics.cedh.lab import CedhDeckLab
    from sabermetrics.cedh.simulator import DisabledSimulatorClient
    from sabermetrics.ui import builder_routes, cedh_routes
    from tests.test_generate_in_new_deck import ImmediateExecutor, _client, _insert_card

    client, path, owner = _client(tmp_path)
    _insert_card(path, "vivi", "Vivi Ornitier", oracle_id="oracle:vivi")
    facts = CardFacts(
        oracle_id="oracle:vivi",
        name="Vivi Ornitier",
        color_identity=("U", "R"),
        type_line="Legendary Creature — Wizard",
    )
    lab = CedhDeckLab(
        cards=FakeCardRepository(extra={facts.name: facts}),
        meta=SimpleNamespace(),
        registry=SimpleNamespace(),
        simulator=DisabledSimulatorClient(),
    )
    monkeypatch.setattr(lab, "pack_summaries", list)
    factory = lambda **kwargs: (lab, SimpleNamespace())
    monkeypatch.setattr(builder_routes, "build_default_lab", factory)
    monkeypatch.setattr(cedh_routes, "build_default_lab", factory)
    monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", ImmediateExecutor())
    packs = client.get("/api/generate/packs?commander=vivi").get_json()["packs"]
    assert packs[0]["pack_id"] == PACK_ID and packs[0]["supported"]
    response = client.post(
        "/lab/build", json={"pack_id": PACK_ID, "commander_id": "vivi"}
    )
    assert response.status_code == 202
    row = db.BuildJobsRepo(path).get(response.get_json()["job_id"])
    assert row["status"] == "done"
    candidate = db.CedhCandidatesRepo(path).get(row["candidate_id"])
    assert candidate["owner_id"] == owner
    assert json.loads(candidate["candidate_json"])["role_counts"]["land"] == 37
