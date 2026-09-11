"""The registry of what stands between here and an authoritative G2 claim.

A refusal tells you the gate is shut. It does not tell you who opens it or what
opening it would take, and a wall with no door is indistinguishable from a wall
nobody intends to remove. So every blocker the code can emit carries an owner
and a criterion that would close it, and a test asserts the two lists match —
an unregistered blocker is a refusal nobody is accountable for, and a
registered blocker nothing can emit is a promise about a wall that is not there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

BLOCKER_SCHEMA: Literal["research-g2-blockers.v1"] = "research-g2-blockers.v1"
ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PATH = ROOT / "fixtures" / "research" / "g2_blockers.yaml"


class Blocker(BaseModel):
    """One thing that must be true before G2 can be claimed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Matches a ``Refusal.blocker_id`` the code can emit.
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    statement: str = Field(min_length=20)
    #: Who decides. Not who writes the code — who makes the judgement the
    #: blocker is waiting on. For every blocker here that is the owner, because
    #: every one of them is a ratification.
    owner: str = Field(min_length=2)
    #: What would have to become true. Written so that whether it has happened
    #: is checkable, not a matter of opinion.
    closure_criterion: str = Field(min_length=20)
    #: Where to look to see that it happened.
    evidence_of_closure: str = Field(min_length=10)
    #: What the reviewer needs in hand first. Ordering, made explicit.
    depends_on: list[str] = Field(default_factory=list)
    #: Whether it is shut right now. Advisory and hand-maintained; the code is
    #: the authority, and a stale `true` here is safe while a stale `false`
    #: would be a claim the refusals contradict.
    currently_blocking: bool


class BlockerRegistry(BaseModel):
    """Every authoritative-G2 blocker, with owner and closure criterion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-g2-blockers.v1"] = BLOCKER_SCHEMA
    blockers: list[Blocker]

    @model_validator(mode="after")
    def unique_and_resolvable(self) -> BlockerRegistry:
        ids = [blocker.id for blocker in self.blockers]
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        if duplicates:
            raise ValueError(f"duplicate blocker ids: {duplicates}")
        known = set(ids)
        for blocker in self.blockers:
            unknown = sorted(set(blocker.depends_on) - known)
            if unknown:
                raise ValueError(f"{blocker.id} depends on unknown {unknown}")
            if blocker.id in blocker.depends_on:
                raise ValueError(f"{blocker.id} depends on itself")
        return self

    @property
    def by_id(self) -> dict[str, Blocker]:
        return {blocker.id: blocker for blocker in self.blockers}


def load_blockers(path: Path = DEFAULT_PATH) -> BlockerRegistry:
    """Load and validate the blocker registry.

    Args:
        path: The registry file.

    Returns:
        The validated registry.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BlockerRegistry.model_validate(raw)
