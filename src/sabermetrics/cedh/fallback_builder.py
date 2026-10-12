"""A deterministic, catalog-backed starter builder for *unsupported* commanders.

Every curated :class:`~sabermetrics.cedh.domain.StrategyPack` names a specific
win package for a specific commander, authored and reviewed in advance. Most
legal commanders have no such pack. Before this module, that meant a dead
end: :func:`sabermetrics.cedh.lab.CedhDeckLab.run` returned ``supported=False``
and the Build UI offered nothing.

This module is the fallback for that gap. It builds a legal, colour-correct
99 for *any* commander identity from a small curated catalog of generically
strong, real Commander staples — the same "author a pool, narrow
deterministically" shape a :class:`StrategyPack` uses, except the pool is not
commander-specific and nothing here claims a win condition.

What this deliberately is **not**:

* It is not a strategy pack. There is no authored win package, no tournament
  evidence tying it to this commander, and no simulator strategy pack mapped
  to it. The candidate it returns carries no win packages at all, rather than
  a fabricated one — claiming a combo "complete" with zero required pieces
  would be worse than claiming none.
* It does not invent cards. Every name in :data:`GENERIC_CATALOG` is resolved
  against the live :class:`CardRepository` before it is trusted; a name the
  corpus does not recognise, is not Commander-legal, or falls outside the
  commander's colour identity is skipped and recorded, exactly like
  :mod:`sabermetrics.cedh.builder` does for a curated pool.
* It does not pretend the result is competitively validated. Callers are
  expected to run it through the native goldfish diagnostics like any other
  list and read the result as a first draft, not a verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sabermetrics.cedh.candidate import (
    CandidateCard,
    CandidateProvenance,
    DeckCandidate,
    new_candidate_id,
)
from sabermetrics.cedh.domain import CommanderIdentity, Legality
from sabermetrics.cedh.errors import CandidateConstraintViolation
from sabermetrics.cedh.repositories import CardFacts, CardRepository

BUILDER_VERSION = "1"
DECK_SIZE = 99

#: Pack id this builder's candidates carry in their provenance. Distinct from
#: any authored pack id so a stored candidate is always honest about how it
#: was built.
PACK_ID = "generic_fallback"
PACK_NAME = "Starter deck"

BASIC_LAND_BY_COLOR: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
COLORLESS_BASIC = "Wastes"

#: Role targets for the generic starter. Deliberately no ``win_package``
#: role: an unsupported commander has no curated combo or attrition plan to
#: claim, and reserving slots for one here would be exactly the "pretend
#: this is proven" failure mode this module exists to avoid.
ROLE_TARGETS: dict[str, int] = {
    "land": 37,
    "acceleration": 10,
    "interaction": 12,
    "tutor": 0,
    "card_advantage": 12,
    "protection": 4,
    "flex": 24,
}
assert sum(ROLE_TARGETS.values()) == DECK_SIZE

#: Non-land roles are filled first, in this order, so a shallow catalog
#: starves "flex" before it starves interaction or card advantage.
FILL_ORDER: tuple[str, ...] = (
    "acceleration",
    "interaction",
    "tutor",
    "card_advantage",
    "protection",
    "flex",
)

#: Below this many *resolved, legal, in-identity* non-land cards, the catalog
#: is too thin for this colour identity to call the result a useful starter
#: rather than a basic-land pile with a commander attached.
MIN_NONLAND_CARDS = 59

#: Curated, real-card catalog of generically strong Commander staples, keyed
#: by the single colour they are castable in, plus ``"C"`` for colourless.
#: This is the deterministic narrowing for an unsupported commander, playing
#: the same role a :class:`StrategyPack` pool plays for a supported one:
#: names are resolved against the live repository, never assumed, so a
#: reprint rename or a corpus gap shows up as a skipped card and a note.
GENERIC_CATALOG: dict[str, dict[str, tuple[str, ...]]] = {
    "C": {
        "acceleration": (
            "Sol Ring",
            "Arcane Signet",
            "Mind Stone",
            "Fellwar Stone",
            "Worn Powerstone",
            "Burnished Hart",
            "Solemn Simulacrum",
            "Thran Dynamo",
            "Basalt Monolith",
        ),
        "card_advantage": ("Skullclamp", "Scroll Rack"),
        "interaction": ("Nevinyrral's Disk", "Oblivion Stone"),
        "protection": ("Lightning Greaves", "Swiftfoot Boots"),
    },
    "W": {
        "interaction": ("Swords to Plowshares", "Path to Exile", "Prison Realm"),
        "tutor": ("Enlightened Tutor",),
        "card_advantage": ("Smothering Tithe", "Mentor of the Meek"),
        "protection": ("Giver of Runes",),
    },
    "U": {
        "interaction": ("Counterspell", "Swan Song", "Cyclonic Rift"),
        "tutor": ("Mystical Tutor",),
        "card_advantage": ("Rhystic Study", "Mystic Remora"),
    },
    "B": {
        "interaction": ("Toxic Deluge", "Damnation"),
        "tutor": ("Vampiric Tutor", "Demonic Tutor"),
        "card_advantage": ("Phyrexian Arena", "Necropotence"),
        "acceleration": ("Dark Ritual",),
    },
    "R": {
        "interaction": ("Chaos Warp", "Deflecting Swat", "Abrade"),
        "card_advantage": ("Faithless Looting", "Light Up the Stage"),
        "acceleration": ("Seething Song",),
    },
    "G": {
        "acceleration": (
            "Llanowar Elves",
            "Elvish Mystic",
            "Birds of Paradise",
            "Nature's Lore",
            "Three Visits",
            "Rampant Growth",
        ),
        "tutor": ("Eladamri's Call", "Worldly Tutor"),
        "card_advantage": ("Sylvan Library",),
        "interaction": ("Beast Within", "Krosan Grip"),
        "protection": ("Blossoming Defense",),
    },
}

# A complete artifact-combat core gives even a colorless identity a bounded
# mana base and actual draw/interaction. All names still require live legality.
GENERIC_CATALOG["C"] = {
    "acceleration": (
        "Sol Ring",
        "Arcane Signet",
        "Mind Stone",
        "Fellwar Stone",
        "Worn Powerstone",
        "Burnished Hart",
        "Solemn Simulacrum",
        "Thran Dynamo",
        "Basalt Monolith",
        "Everflowing Chalice",
        "Hedron Archive",
        "Prismatic Lens",
        "Guardian Idol",
        "Thought Vessel",
    ),
    "card_advantage": (
        "Skullclamp",
        "Scroll Rack",
        "Mind's Eye",
        "Idol of Oblivion",
        "Tome of Legends",
        "Endless Atlas",
        "Staff of Nin",
        "Mystic Forge",
        "Mask of Memory",
        "Infiltration Lens",
        "Staff of Domination",
        "The Immortal Sun",
        "Trading Post",
        "Well of Lost Dreams",
    ),
    "interaction": (
        "Nevinyrral's Disk",
        "Oblivion Stone",
        "Meteor Golem",
        "Spine of Ish Sah",
        "Unstable Obelisk",
        "Scour from Existence",
        "Introduction to Annihilation",
        "All Is Dust",
        "Perilous Vault",
        "Steel Hellkite",
        "Duplicant",
        "Ratchet Bomb",
        "Powder Keg",
        "Lux Cannon",
    ),
    "protection": (
        "Lightning Greaves",
        "Swiftfoot Boots",
        "Darksteel Plate",
        "Welding Jar",
        "Whispersilk Cloak",
        "Mirror Shield",
    ),
    "flex": (
        "Myr Battlesphere",
        "Wurmcoil Engine",
        "Hangarback Walker",
        "Walking Ballista",
        "Retrofitter Foundry",
        "Myr Turbine",
        "Sharding Sphinx",
        "Precursor Golem",
        "Chief of the Foundry",
        "Foundry Inspector",
        "Jhoira's Familiar",
        "Scrap Trawler",
        "Junk Diver",
        "Myr Retriever",
        "Workshop Assistant",
        "Scarecrone",
        "Bronze Guardian",
        "Darksteel Juggernaut",
        "Lodestone Golem",
        "Stonecoil Serpent",
        "Metalwork Colossus",
        "Triplicate Titan",
        "Phyrexian Triniform",
        "Traxos, Scourge of Kroog",
        "Akroma's Memorial",
        "Forsaken Monument",
        "Ugin, the Ineffable",
        "Karn, Scion of Urza",
        "Soul of New Phyrexia",
        "Steel Overseer",
        "Animation Module",
    ),
}
# Identity checks remove colored cards in the shared pool; surplus depth is
# intentional. Color-specific engines take priority over the generic flex.
GENERIC_CATALOG["U"]["flex"] = (
    "Talrand, Sky Summoner",
    "Murmuring Mystic",
    "Archmage Emeritus",
    "Docent of Perfection",
    "Curiosity",
    "Ophidian Eye",
    "Consider",
    "Opt",
    "Ponder",
    "Preordain",
    "Brainstorm",
    "Treasure Cruise",
)
GENERIC_CATALOG["R"]["flex"] = (
    "Young Pyromancer",
    "Third Path Iconoclast",
    "Guttersnipe",
    "Electrostatic Field",
    "Fiery Inscription",
    "Coruscation Mage",
    "Storm-Kiln Artist",
    "Birgi, God of Storytelling",
    "Crash Through",
    "Expedite",
    "Dragon Fodder",
    "Krenko's Command",
)
GENERIC_CATALOG["B"]["flex"] = (
    "Call of the Ring",
    "Nazgul",
    "Orcish Bowmasters",
    "Dreadhorde Invasion",
    "Lazotep Reaver",
    "Mordor Muster",
    "The Lord of the Nazgul",
    "Reanimate",
    "Victimize",
    "Living Death",
    "Feed the Swarm",
    "Sign in Blood",
)

#: Utility lands considered for any commander with at least one colour. Real
#: named lands, resolved and legality-checked the same as every other card —
#: never added on the assumption that a corpus containing one contains all.
UTILITY_LANDS: tuple[str, ...] = (
    "Command Tower",
    "Exotic Orchard",
    "Myriad Landscape",
    "Path of Ancestry",
    "Terramorphic Expanse",
    "Evolving Wilds",
)


def looks_like_commander(facts: CardFacts) -> bool:
    """Best-effort check that a resolved card can lead a Commander deck.

    Not a substitute for a real "can be your commander" flag — ``mtg_v1``
    does not publish one to this repository — so this is deliberately
    conservative: a Legendary Creature, or any card whose oracle text states
    the exception explicitly (backgrounds, some planeswalkers).
    """
    type_line = facts.type_line or ""
    if "Legendary" in type_line and "Creature" in type_line:
        return True
    text = (facts.oracle_text or "").casefold()
    return "can be your commander" in text


def _catalog_names_for(colors: set[str]) -> dict[str, list[str]]:
    """Role -> ordered candidate names: colourless first, then WUBRG order."""
    by_role: dict[str, list[str]] = {}
    for key in (*[c for c in "WUBRG" if c in colors], "C"):
        for role, names in GENERIC_CATALOG.get(key, {}).items():
            by_role.setdefault(role, []).extend(names)
    return by_role


def _distribute(total: int, buckets: int) -> list[int]:
    """Split ``total`` across ``buckets`` as evenly as possible, deterministically."""
    base, remainder = divmod(total, buckets)
    return [base + (1 if i < remainder else 0) for i in range(buckets)]


def build_fallback_candidate(
    commander: CommanderIdentity,
    cards: CardRepository,
    *,
    candidate_id: str | None = None,
    generated_at: datetime | None = None,
) -> DeckCandidate:
    """Build one deterministic generic starter 99 for any commander identity.

    Args:
        commander: The commander to build around. Not required to appear in
            any authored strategy pack.
        cards: Card facts and legality. The only source of truth for which
            catalog names are real, legal and in colour.
        candidate_id: Override the generated id, for reproducible tests.
        generated_at: Override the timestamp, for reproducible tests.

    Returns:
        A candidate holding exactly 99 legal, singleton cards and no win
        packages.

    Raises:
        CandidateConstraintViolation: The catalog cannot supply enough real,
            legal, in-colour cards for this identity to produce a 99 — not a
            build the user should be shown as if it were a normal result.
    """
    colors = set(commander.color_identity)
    commander_ids = set(commander.oracle_ids)

    by_role = _catalog_names_for(colors)
    all_names = sorted({name for names in by_role.values() for name in names})
    resolved = cards.resolve_names(all_names)
    legality = cards.legality([f.oracle_id for f in resolved.values()], "commander")

    notes: list[str] = [
        "Deterministic artifact-combat starter with color-specific engine cards; commander synergy and multiplayer performance remain unvalidated."
    ]
    seen: set[str] = set()
    eligible_by_role: dict[str, list[CardFacts]] = {}
    for role, names in by_role.items():
        bucket: list[CardFacts] = []
        for name in names:
            facts = resolved.get(name)
            if facts is None:
                notes.append(f"{name} ({role}): not found in the card corpus")
                continue
            if not colors and name == "Arcane Signet":
                continue
            if facts.oracle_id in commander_ids or facts.oracle_id in seen:
                continue
            status = legality.get(facts.oracle_id, Legality.UNKNOWN)
            if status is not Legality.LEGAL:
                notes.append(
                    f"{facts.name} ({role}): Commander legality is {status.value}"
                )
                continue
            if not set(facts.color_identity) <= colors:
                notes.append(
                    f"{facts.name} ({role}): colour identity "
                    f"{''.join(facts.color_identity) or 'C'} is outside the "
                    f"commander's {''.join(sorted(colors)) or 'C'}"
                )
                continue
            bucket.append(facts)
            seen.add(facts.oracle_id)
        eligible_by_role[role] = bucket

    nonland_total = sum(len(bucket) for bucket in eligible_by_role.values())
    if nonland_total < MIN_NONLAND_CARDS:
        raise CandidateConstraintViolation(
            f"the generic catalog only resolved {nonland_total} legal, "
            f"in-colour card(s) for identity {''.join(sorted(colors)) or 'C'} "
            f"(need at least {MIN_NONLAND_CARDS}); the catalog is insufficient "
            "to build a useful starter for this commander"
        )

    chosen: dict[str, CandidateCard] = {}

    def add(facts: CardFacts, role: str) -> None:
        chosen[facts.oracle_id] = CandidateCard(
            oracle_id=facts.oracle_id,
            name=facts.name,
            role=role,
            quantity=1,
            source="pool",
            priority=0.0,
        )

    nonland_cap = DECK_SIZE - ROLE_TARGETS["land"]
    for role in FILL_ORDER:
        target = ROLE_TARGETS.get(role, 0)
        have = 0
        for facts in eligible_by_role.get(role, ()):
            if have >= target or len(chosen) >= nonland_cap:
                break
            add(facts, role)
            have += 1
        if have < target:
            notes.append(
                f"role '{role}' is short: {have} of {target} slots filled "
                "from the generic catalog"
            )

    # Spill any remaining catalog depth into flex, best-effort, still capped
    # so there is always room left for the land count below.
    for role in FILL_ORDER:
        for facts in eligible_by_role.get(role, ()):
            if len(chosen) >= nonland_cap:
                break
            if facts.oracle_id not in chosen:
                add(facts, "flex")

    minimum_roles = {
        "acceleration": 8,
        "interaction": 8,
        "card_advantage": 8,
        "protection": 2,
    }
    for role, minimum in minimum_roles.items():
        if (
            sum(card.quantity for card in chosen.values() if card.role == role)
            < minimum
        ):
            raise CandidateConstraintViolation(
                f"Catalog cannot supply the minimum {minimum} {role} cards; no useful starter was built."
            )

    # Lands: real utility lands in colour first, then basics for the rest.
    land_target = DECK_SIZE - len(chosen)
    if not 36 <= land_target <= 40:
        raise CandidateConstraintViolation(
            "Catalog is too shallow for a useful starter: land count must stay between 36 and 40."
        )
    land_count = 0
    if colors:
        utility = cards.resolve_names(list(UTILITY_LANDS))
        utility_legality = cards.legality(
            [f.oracle_id for f in utility.values()], "commander"
        )
        for name in UTILITY_LANDS:
            if land_count >= land_target:
                break
            facts = utility.get(name)
            if facts is None:
                continue
            if facts.oracle_id in commander_ids or facts.oracle_id in chosen:
                continue
            if (
                utility_legality.get(facts.oracle_id, Legality.UNKNOWN)
                is not Legality.LEGAL
            ):
                continue
            if not set(facts.color_identity) <= colors:
                continue
            add(facts, "land")
            land_count += 1

    shortfall = DECK_SIZE - len(chosen)
    if shortfall > 0:
        basic_names = (
            [BASIC_LAND_BY_COLOR[c] for c in "WUBRG" if c in colors]
            if colors
            else [COLORLESS_BASIC]
        )
        basics = cards.resolve_names(basic_names)
        basic_legality = cards.legality(
            [card.oracle_id for card in basics.values()], "commander"
        )
        available = [
            basics[n]
            for n in basic_names
            if n in basics
            and set(basics[n].color_identity) <= colors
            and basic_legality.get(basics[n].oracle_id) is Legality.LEGAL
        ]
        if not available:
            raise CandidateConstraintViolation(
                f"the generic starter is {shortfall} card(s) short of 99 and "
                f"no basic land among {basic_names} resolved against the "
                "corpus, so the shortfall cannot be repaired"
            )
        for facts, count in zip(
            available, _distribute(shortfall, len(available)), strict=True
        ):
            if count <= 0:
                continue
            existing = chosen.get(facts.oracle_id)
            quantity = count + (existing.quantity if existing else 0)
            chosen[facts.oracle_id] = CandidateCard(
                oracle_id=facts.oracle_id,
                name=facts.name,
                role="land",
                quantity=quantity,
                source="repair",
                priority=-1.0,
            )
        notes.append(f"{shortfall} basic land(s) added to reach 99")

    total = sum(c.quantity for c in chosen.values())
    if total != DECK_SIZE:
        raise CandidateConstraintViolation(
            f"the generic starter produced {total} cards instead of "
            f"{DECK_SIZE}; this is a builder defect, not a result to show"
        )

    role_counts: dict[str, int] = {}
    for card in chosen.values():
        role_counts[card.role] = role_counts.get(card.role, 0) + card.quantity

    snapshot = cards.snapshot()
    provenance = CandidateProvenance(
        pack_id=PACK_ID,
        pack_version=BUILDER_VERSION,
        pack_source="deterministic generic catalog; no curated strategy pack",
        card_snapshot=snapshot.label,
        builder_version=BUILDER_VERSION,
    )

    return DeckCandidate(
        candidate_id=candidate_id or new_candidate_id(),
        generated_at=generated_at or datetime.now(UTC),
        commander=commander,
        cards=tuple(sorted(chosen.values(), key=lambda c: c.oracle_id)),
        win_packages=(),
        role_counts=role_counts,
        provenance=provenance,
        notes=tuple(notes),
    )
