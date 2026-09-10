"""Resolve a plan's ``context_id`` to a typed, deterministic deck context.

The golden question set binds most of its questions to ``cedh:kinnan-basalt-fixture``
and its healthy-deck questions to ``cedh:kinnan-healthy-baseline``. Neither
string resolved to anything before R3. This module is where they resolve.

**Why this reads the pack YAML rather than importing ``cedh.packs``.** ADR-030
enumerates what ``assistant`` may borrow from ``cedh`` — the model gateway, the
cost ledger, the simulator client, deck documents and the reference layer — and
``cedh.packs`` is not on that list. Importing it would not leak a planning
dependency backward into deterministic generation, so this is a scope question
rather than a correctness one; the deliberate choice is to stay inside the
documented borrow list and cross-check the duplication with a test that imports
both readers and asserts they agree.

**Why there is no deck hash here.** ``deck_sha256`` is a cross-repository
contract pinned by ``fixtures/cedh/contracts/hash-golden-vectors.json`` in three
implementations, and CLAUDE.md forbids extending what it covers. A fourth
implementation, or a hash spanning pack identity as well as the list, would
re-create exactly the bug that contract replaced: one deck reading as two
because it was reached under two different configurations. What is published
here is :attr:`DeckContext.pack_sha256`, which identifies the **configuration**
and says so in its name. That the two context ids name the same 100 cards is
asserted directly, by comparing the sorted Oracle ids.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.assistant.sources import CardSource

ROOT = Path(__file__).resolve().parents[3]
PACKS_DIR = ROOT / "config" / "cedh_packs"

#: The context ids the golden question set binds to, and the pack each reads.
#:
#: ``cedh:kinnan-healthy-baseline`` is a declared **alias** of the same pack,
#: not a second list. The healthy-deck category asks whether the assistant
#: manufactures a finding on a list with no known problem; the Kinnan pack is
#: the only authored list in the repository, and inventing a second one would
#: not make that measurement more real. ``baseline_of`` records the aliasing so
#: nothing downstream reports two decks.
_CONTEXTS: Mapping[str, dict[str, Any]] = {
    "cedh:kinnan-basalt-fixture": {
        "label": "Kinnan — Basalt engine into Thrasios",
        "pack_file": "kinnan_basalt.yaml",
        "baseline_of": None,
    },
    "cedh:kinnan-healthy-baseline": {
        "label": "Kinnan — Basalt engine into Thrasios (healthy baseline)",
        "pack_file": "kinnan_basalt.yaml",
        "baseline_of": "cedh:kinnan-basalt-fixture",
    },
}


class UnknownDeckContextError(RuntimeError):
    """A plan binds a context id that is not registered."""


class DeckContextUnresolvedError(RuntimeError):
    """A registered context could not be resolved against the active corpus."""


class _Context(BaseModel):
    """Strict, frozen base for every context model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextWinPackage(_Context):
    """One declared assembly line, as the strategy pack states it."""

    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    piece_names: tuple[str, ...] = Field(min_length=1)
    piece_oracle_ids: tuple[str, ...] = ()
    converts_via: str = ""


class DeckContext(_Context):
    """The deterministic facts a ``deck_profile`` step may read.

    Carries no price and no ownership field, and takes no requester: a context
    is a property of a list, not of who asked about it (ADR-025).
    """

    schema_version: str = "research-deck-context.v1"
    context_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    #: Identity of the **configuration**, not of the deck. See the module
    #: docstring: this deliberately is not a ``deck_sha256``.
    pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_of: str | None = None
    commander_names: tuple[str, ...] = Field(min_length=1)
    commander_oracle_ids: tuple[str, ...] = Field(min_length=1)
    #: The library in pack declaration order, which is the order a profile
    #: returns rows in. Oracle-id order would be a hash ordering, and slicing
    #: one at fifty would make half the deck structurally unreachable.
    library_oracle_ids: tuple[str, ...] = Field(min_length=1)
    names_by_oracle_id: dict[str, str]
    roles: dict[str, tuple[str, ...]]
    role_counts: dict[str, int]
    role_targets: dict[str, int]
    primary_win_package: ContextWinPackage
    secondary_win_packages: tuple[ContextWinPackage, ...] = ()
    auto_include_oracle_ids: tuple[str, ...] = ()
    #: R3 configures no role threshold and can run no field comparison. Both
    #: are carried so a profile states its own limits structurally, rather
    #: than a reader inferring that a missing comparison was a passing one.
    thresholds_available: bool = False
    field_comparison_available: bool = False

    @property
    def oracle_ids(self) -> tuple[str, ...]:
        """Return the commander plus the library, deduplicated in deck order."""
        seen: dict[str, None] = {}
        for oracle_id in (*self.commander_oracle_ids, *self.library_oracle_ids):
            seen.setdefault(oracle_id, None)
        return tuple(seen)

    def roles_for(self, oracle_id: str) -> tuple[str, ...]:
        """Return every role the pack assigns to one card.

        Args:
            oracle_id: A card identity.

        Returns:
            Role names in sorted order, empty when the card is not in the deck.
        """
        return tuple(
            sorted(role for role, ids in self.roles.items() if oracle_id in ids)
        )


def known_context_ids() -> tuple[str, ...]:
    """Return every registered context id, sorted."""
    return tuple(sorted(_CONTEXTS))


class DeckContextRegistry:
    """Resolve and cache deck contexts against one card source."""

    def __init__(
        self,
        packs_dir: Path = PACKS_DIR,
        contexts: Mapping[str, dict[str, Any]] | None = None,
    ) -> None:
        """Bind the registry to a directory of strategy packs.

        Args:
            packs_dir: Directory holding the pack YAML files.
            contexts: Context registry to resolve against. Defaults to the two
                shipped ids. Injectable so a test can bind a context of its own
                — a deck built to exercise one filter — without adding it to
                the registry the product ships.
        """
        self._packs_dir = packs_dir
        self._contexts = _CONTEXTS if contexts is None else contexts
        self._cache: dict[tuple[str, str], DeckContext] = {}

    def resolve(self, context_id: str, *, cards: CardSource) -> DeckContext:
        """Resolve one context id against the active corpus.

        Args:
            context_id: A registered context id.
            cards: The card source whose corpus the names resolve against.

        Returns:
            The fully resolved context.

        Raises:
            UnknownDeckContextError: If the id is not registered.
            DeckContextUnresolvedError: If the pack is missing, or any card
                name fails to resolve or resolves ambiguously.
        """
        spec = self._contexts.get(context_id)
        if spec is None:
            raise UnknownDeckContextError(
                f"unknown deck context {context_id!r}; "
                f"registered: {', '.join(sorted(self._contexts))}"
            )
        key = (context_id, cards.provenance().bundle_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        context = _build(context_id, spec, self._packs_dir, cards)
        self._cache[key] = context
        return context


def resolve_deck_context(context_id: str, *, cards: CardSource) -> DeckContext:
    """Resolve one context id without a caching registry.

    Args:
        context_id: A registered context id.
        cards: The card source whose corpus the names resolve against.

    Returns:
        The fully resolved context.
    """
    return DeckContextRegistry().resolve(context_id, cards=cards)


def _build(
    context_id: str,
    spec: Mapping[str, Any],
    packs_dir: Path,
    cards: CardSource,
) -> DeckContext:
    """Read one pack and resolve every card name it declares."""
    path = packs_dir / str(spec["pack_file"])
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise DeckContextUnresolvedError(
            f"strategy pack for {context_id!r} is missing: {path}"
        ) from exc
    pack = yaml.safe_load(raw_bytes.decode("utf-8"))
    if not isinstance(pack, dict):
        raise DeckContextUnresolvedError(f"strategy pack is not a mapping: {path}")

    commander_names = tuple(str(name) for name in pack.get("commander_names") or ())
    card_entries = list(pack.get("cards") or ())
    library_names = tuple(str(entry["name"]) for entry in card_entries)
    package_names: list[str] = []
    primary_raw = pack.get("primary_win_package") or {}
    package_names.extend(str(name) for name in primary_raw.get("pieces") or ())
    secondary_raw = list(pack.get("secondary_win_packages") or ())
    for entry in secondary_raw:
        package_names.extend(str(name) for name in entry.get("pieces") or ())
    auto_include_names = tuple(str(name) for name in pack.get("auto_include") or ())

    wanted = (
        *commander_names,
        *library_names,
        *package_names,
        *auto_include_names,
    )
    resolved = _resolve_names(wanted, cards, context_id)

    roles: dict[str, list[str]] = {}
    for entry in card_entries:
        oracle_id = resolved[str(entry["name"])]
        for role in entry.get("roles") or ():
            roles.setdefault(str(role), []).append(oracle_id)

    return DeckContext(
        context_id=context_id,
        label=str(spec["label"]),
        pack_id=str(pack.get("pack_id") or path.stem),
        pack_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        baseline_of=spec["baseline_of"],
        commander_names=commander_names,
        commander_oracle_ids=tuple(resolved[name] for name in commander_names),
        library_oracle_ids=tuple(resolved[name] for name in library_names),
        names_by_oracle_id={
            resolved[name]: name for name in (*commander_names, *library_names)
        },
        roles={role: tuple(ids) for role, ids in sorted(roles.items())},
        role_counts={role: len(ids) for role, ids in sorted(roles.items())},
        role_targets={
            str(role): int(target)
            for role, target in sorted((pack.get("role_targets") or {}).items())
        },
        primary_win_package=_package(primary_raw, resolved),
        secondary_win_packages=tuple(
            _package(entry, resolved) for entry in secondary_raw
        ),
        auto_include_oracle_ids=tuple(resolved[name] for name in auto_include_names),
    )


def _package(raw: Mapping[str, Any], resolved: Mapping[str, str]) -> ContextWinPackage:
    """Build one declared win package from its pack entry."""
    piece_names = tuple(str(name) for name in raw.get("pieces") or ())
    return ContextWinPackage(
        name=str(raw.get("name") or "unnamed package"),
        kind=str(raw.get("kind") or "unspecified"),
        piece_names=piece_names,
        piece_oracle_ids=tuple(resolved[name] for name in piece_names),
        converts_via=str(raw.get("converts_via") or ""),
    )


def _resolve_names(
    names: Sequence[str], cards: CardSource, context_id: str
) -> dict[str, str]:
    """Resolve every declared name to exactly one Oracle id.

    A name that does not resolve, or that resolves to more than one card, fails
    the whole context. The alternative — dropping it — would silently shorten
    the deck, which is the failure mode the pack loader already refuses.

    Args:
        names: Every card name the pack declares.
        cards: The card source to resolve against.
        context_id: The context being resolved, for the error message.

    Returns:
        One Oracle id per distinct name.

    Raises:
        DeckContextUnresolvedError: On any unresolved or ambiguous name.
    """
    wanted = tuple(dict.fromkeys(names))
    found = cards.resolve_names(wanted)
    missing = sorted(name for name in wanted if name not in found)
    ambiguous = sorted(name for name, ids in found.items() if len(ids) != 1)
    if missing or ambiguous:
        problems = []
        if missing:
            problems.append(f"unresolved: {', '.join(missing)}")
        if ambiguous:
            problems.append(f"ambiguous: {', '.join(ambiguous)}")
        raise DeckContextUnresolvedError(
            f"deck context {context_id!r} does not resolve against this corpus "
            f"({'; '.join(problems)})"
        )
    return {name: ids[0] for name, ids in found.items()}
