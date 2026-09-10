"""A portable retrieval bundle for Research Assistant tests.

The R3 executor resolves a deck context by name against the catalog, so a
three-card bundle cannot exercise it and a ``skipif`` is not available:
``scripts/check_r*.py`` pin the skipped count exactly, and a test that starts
skipping is a test that stopped running.

This module therefore builds a bundle over the **100 real Kinnan card names**
from ``fixtures/cedh/cards.json``. Names and the 99-card list are real; Oracle
ids are the fixture's uuid5 hashes and the snapshot says so, so nothing built
here can be mistaken for ``mtg_v1``.

Oracle text in that fixture is the placeholder ``[fixture paraphrase] <name>.``,
which tags nothing and ranks nothing. :data:`AUTHORED_TEXT` restores real Oracle
text for the handful of cards the executor tests actually search and tag. It is
authored test data, listed explicitly rather than generated, so a reader can see
exactly which rows carry real text and which do not.

The encoder and cross-encoder are deterministic pure-Python stand-ins. No model
file, no network, no hub lookup.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from sabermetrics.mechanics.tags.predicates import FaceView
from sabermetrics.substrate.bundle import build_bundle
from sabermetrics.substrate.corpus import (
    CardView,
    CorpusExport,
    SnapshotIdentity,
    corpus_content_sha256,
)
from sabermetrics.substrate.settings import ResearchSettings, load_research_settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CARDS = ROOT / "fixtures" / "cedh" / "cards.json"

#: The synthetic snapshot label. Deliberately not ``mtg_v1.card_any_medium``:
#: ``run_g2.py`` refuses an authoritative claim over any other source view.
SOURCE_VIEW = "fixture:research-portable"

#: Embedding width for the test encoder. Small enough to build quickly, wide
#: enough that unrelated cards do not collide onto one vector.
TEST_DIMENSIONS = 16

#: Real Oracle text for the cards the R3 executor tests search, tag, or scope.
#: Every other row keeps the fixture's placeholder text.
AUTHORED_TEXT: dict[str, str] = {
    "Basalt Monolith": (
        "Basalt Monolith doesn't untap during your untap step.\n"
        "{T}: Add {C}{C}{C}.\n"
        "{3}: Untap Basalt Monolith."
    ),
    "Grim Monolith": (
        "Grim Monolith doesn't untap during your untap step.\n"
        "{T}: Add {C}{C}{C}.\n"
        "{4}: Untap Grim Monolith."
    ),
    "Mana Vault": (
        "Mana Vault doesn't untap during your untap step.\n"
        "At the beginning of your upkeep, you may pay {4}. If you do, untap "
        "Mana Vault.\n"
        "At the beginning of your draw step, if Mana Vault is untapped, it "
        "deals 1 damage to you.\n"
        "{T}: Add {C}{C}{C}."
    ),
    "Sol Ring": "{T}: Add {C}{C}.",
    "Mox Diamond": (
        "If Mox Diamond would enter the battlefield, you may discard a land "
        "card instead. If you do, put Mox Diamond onto the battlefield. If you "
        "don't, put it into its owner's graveyard.\n"
        "{T}: Add one mana of any color."
    ),
    "Chrome Mox": (
        "Imprint — When Chrome Mox enters the battlefield, you may exile a "
        "nonartifact, nonland card from your hand.\n"
        "{T}: Add one mana of any of the exiled card's colors."
    ),
    "Talisman of Curiosity": (
        "{T}: Add {C}.\n"
        "{T}: Add {G} or {U}. Talisman of Curiosity deals 1 damage to you."
    ),
    "Force of Will": (
        "You may pay 1 life and exile a blue card from your hand rather than "
        "pay this spell's mana cost.\n"
        "Counter target spell."
    ),
    "Force of Negation": (
        "If it's not your turn, you may exile a blue card from your hand "
        "rather than pay this spell's mana cost.\n"
        "Counter target noncreature spell. If that spell is countered this "
        "way, exile it instead of putting it into its owner's graveyard."
    ),
    "Fierce Guardianship": (
        "If you control a commander, you may cast this spell without paying "
        "its mana cost.\n"
        "Counter target noncreature spell."
    ),
    "Pact of Negation": (
        "Counter target spell.\n"
        "At the beginning of your next upkeep, pay {3}{U}{U}. If you don't, "
        "you lose the game."
    ),
    "Mental Misstep": (
        "({U/P} can be paid with either {U} or 2 life.)\n"
        "Counter target spell with mana value 1."
    ),
    "Delighted Halfling": (
        "{T}: Add {C}.\n"
        "{T}: Add one mana of any color. Spend this mana only to cast a "
        "legendary spell. That spell can't be countered."
    ),
    "Birds of Paradise": ("Flying\n{T}: Add one mana of any color."),
    # The three green one-drops deck-local-010 requires. Real text so the
    # mana-dork tag fires on them exactly as it does on the Human control.
    "Elvish Mystic": "{T}: Add {G}.",
    "Llanowar Elves": "{T}: Add {G}.",
    "Fyndhorn Elves": "{T}: Add {G}.",
    "Kinnan, Bonder Prodigy": (
        "Whenever you tap a nonland permanent for mana, add one mana of any "
        "type that permanent produced.\n"
        "{5}{G}{U}: Look at the top five cards of your library. You may put a "
        "non-Human creature card from among them onto the battlefield. Put the "
        "rest on the bottom of your library in a random order."
    ),
    "Thrasios, Triton Hero": (
        "{4}: Scry 1, then reveal the top card of your library. If it's a land "
        "card, put it onto the battlefield tapped. Otherwise, draw a card."
    ),
    "Copy Artifact": (
        "You may have Copy Artifact enter the battlefield as a copy of any "
        "artifact on the battlefield, except it's an enchantment in addition "
        "to its other types."
    ),
    "Dramatic Reversal": "Untap all nonland permanents you control.",
    "Boseiju, Who Endures": (
        "This land enters tapped unless you control two or fewer other lands.\n"
        "{T}: Add {G}.\n"
        "Channel — {1}{G}, Discard Boseiju, Who Endures: Destroy target "
        "artifact, enchantment, or nonbasic land an opponent controls."
    ),
    "Otawara, Soaring City": (
        "This land enters tapped unless you control two or fewer other lands.\n"
        "{T}: Add {U}.\n"
        "Channel — {X}{U}, Discard Otawara, Soaring City: Return target "
        "artifact, creature, enchantment, or planeswalker an opponent controls "
        "to its owner's hand."
    ),
    "Rhystic Study": (
        "Whenever an opponent casts a spell, you may draw a card unless that "
        "player pays {1}."
    ),
    "Mystic Remora": (
        "Cumulative upkeep {1}\n"
        "Whenever an opponent casts a noncreature spell, you may draw a card "
        "unless that player pays {4}."
    ),
}

#: A synthetic control card. Its mechanics are *identical* to Llanowar Elves —
#: same printed ability, so the same mechanic tags fire — and it differs in
#: exactly one respect: it is a Human. That isolates the creature subtype as
#: the only thing that can separate it from a qualifying non-Human mana dork,
#: which is precisely what a subtype filter has to be able to do. It is
#: obviously not a real card, and the corpus it lives in is labelled synthetic.
CONTROL_HUMAN_DORK = {
    "oracle_id": "d0000000-0000-4000-8000-000000000001",
    "name": "Synthetic Human Druid",
    "layout": "normal",
    "mana_cost": "{G}",
    "mana_value": 1.0,
    "type_line": "Creature — Human Druid",
    "oracle_text": "{T}: Add {G}.",
    "colors": ["G"],
    "color_identity": ["G"],
    "keywords": [],
    "all_types": ["Creature"],
    "castable_cmcs": [1.0],
    "faces": [],
}

#: A synthetic modal double-faced control. It publishes NO card-level oracle
#: text — its text lives on its faces, like 891 real corpus rows — and its back
#: face taps for mana. Anything reading ``oracle_text`` directly sees nothing
#: here; the tag build and the indexed document both see the face.
CONTROL_FACE_ONLY = {
    "oracle_id": "d0000000-0000-4000-8000-000000000002",
    "name": "Synthetic Split Sorcery",
    "layout": "modal_dfc",
    "mana_cost": "",
    "mana_value": 3.0,
    "type_line": "Sorcery // Land — Island",
    "oracle_text": "",
    "colors": ["U"],
    "color_identity": ["U"],
    "keywords": [],
    "all_types": ["Sorcery", "Land"],
    "castable_cmcs": [],
    "faces": [
        {
            "name": "Synthetic Split Sorcery",
            "mana_cost": "{2}{U}",
            "type_line": "Sorcery",
            "oracle_text": "Return target creature to its owner's hand.",
        },
        {
            "name": "Synthetic Split Shore",
            "mana_cost": "",
            "type_line": "Land — Island",
            "oracle_text": "{T}: Add {U}.",
        },
    ],
}

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEncoder:
    """A deterministic bag-of-tokens encoder standing in for a local model."""

    dimensions = TEST_DIMENSIONS

    def encode(self, texts: Sequence[str], *, batch_size: int) -> NDArray[np.float32]:
        """Encode texts into normalized hashed-token vectors.

        Args:
            texts: Documents or queries to encode.
            batch_size: Accepted for protocol parity; the encoder is exact.

        Returns:
            One normalized ``float32`` row per input text.
        """
        del batch_size
        rows = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for index, text in enumerate(texts):
            for token in _TOKEN.findall(text.casefold()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                rows[index, digest[0] % self.dimensions] += 1.0
            norm = float(np.linalg.norm(rows[index]))
            if norm == 0.0:
                # A vector of zeros is refused downstream, and an all-stopword
                # query is not an error. Fall back to a fixed unit basis.
                rows[index, 0] = 1.0
            else:
                rows[index] /= norm
        return rows


class OverlapScorer:
    """A deterministic token-overlap cross-encoder stand-in."""

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        """Score query/document pairs by shared-token count.

        Args:
            pairs: ``(query, document)`` pairs to score.

        Returns:
            One non-negative float per pair, higher being more relevant.
        """
        scores: list[float] = []
        for query, document in pairs:
            wanted = set(_TOKEN.findall(query.casefold()))
            found = set(_TOKEN.findall(document.casefold()))
            scores.append(float(len(wanted & found)))
        return scores


def research_settings(root: Path) -> ResearchSettings:
    """Return production retrieval settings retargeted at a temporary root.

    Args:
        root: Directory that will hold the generated bundle.

    Returns:
        Settings whose artifact root, embedding width, and pool bounds suit a
        100-card offline corpus.
    """
    settings = load_research_settings()
    return settings.model_copy(
        update={
            "artifacts": settings.artifacts.model_copy(update={"root": root}),
            "embedding": settings.embedding.model_copy(
                update={"dimensions": TEST_DIMENSIONS, "query_prefix": ""}
            ),
            "retrieval": settings.retrieval.model_copy(
                update={
                    "lexical_pool": 200,
                    "dense_pool": 200,
                    "rerank_pool": 100,
                    "result_limit": 50,
                }
            ),
        }
    )


def research_export() -> CorpusExport:
    """Materialize the 100-card portable corpus.

    Returns:
        A frozen export whose ``source_view`` names it as a fixture.
    """
    payload = json.loads(FIXTURE_CARDS.read_text(encoding="utf-8"))
    legality: dict[str, str] = dict(payload["legality"].get("commander", {}))
    rows = [*payload["cards"], CONTROL_HUMAN_DORK, CONTROL_FACE_ONLY]
    legality.setdefault(str(CONTROL_HUMAN_DORK["oracle_id"]), "legal")
    legality.setdefault(str(CONTROL_FACE_ONLY["oracle_id"]), "legal")
    cards = tuple(
        CardView(
            oracle_id=str(row["oracle_id"]),
            name=str(row["name"]),
            layout=str(row.get("layout") or "normal"),
            mana_cost=row.get("mana_cost"),
            mana_value=float(row.get("mana_value") or 0.0),
            type_line=str(row.get("type_line") or ""),
            oracle_text=AUTHORED_TEXT.get(str(row["name"]), row.get("oracle_text")),
            colors=tuple(row.get("colors") or ()),
            color_identity=tuple(row.get("color_identity") or ()),
            keywords=tuple(row.get("keywords") or ()),
            all_types=tuple(row.get("all_types") or ()),
            castable_cmcs=tuple(float(value) for value in row.get("castable_cmcs", ())),
            faces=tuple(
                FaceView(
                    name=str(face.get("name") or ""),
                    mana_cost=face.get("mana_cost"),
                    type_line=face.get("type_line"),
                    oracle_text=face.get("oracle_text"),
                )
                for face in row.get("faces") or ()
            ),
            commander_legal=legality.get(str(row["oracle_id"])),
        )
        for row in rows
    )
    return CorpusExport(
        identity=SnapshotIdentity(source_view=SOURCE_VIEW, row_count=len(cards)),
        cards=cards,
        content_sha256=corpus_content_sha256(cards),
    )


def build_research_bundle(root: Path) -> ResearchSettings:
    """Build and activate the portable bundle beneath ``root``.

    Args:
        root: Directory to hold the bundle and its ``CURRENT`` pointer.

    Returns:
        The settings the bundle was built with, ready for a facade.
    """
    settings = research_settings(root)
    build_bundle(
        research_export(),
        settings,
        encoder=HashingEncoder(),
        built_at="2026-09-09T00:00:00+00:00",
        verify_model_files=False,
    )
    return settings
