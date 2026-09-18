"""Sealing a held-out question set by hash commitment, and checking the seal.

Every agent that works in this repository can read every file in it and every
byte in its history, so there is no way to hide a held-out set here. What can
be done is to prove the set existed BEFORE a run: commit the hash of the file,
the number of questions in it, the pinned document it was written against and
the index generation it will be scored on; run; then publish the file and check
that it still hashes to what was committed.

That proves the key predates the run. It does not prove the key is independent
of the visible one — nothing mechanical can — which is why the set is authored
offline by the owner and not by anything that has read the visible labels.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

HELDOUT_COMMITMENT_SCHEMA: Literal["research-heldout-commitment.v1"] = (
    "research-heldout-commitment.v1"
)
ROOT = Path(__file__).resolve().parents[4]
DEFAULT_COMMITMENT = ROOT / "fixtures" / "research" / "heldout_commitment.yaml"


class HeldoutCommitment(BaseModel):
    """What was sealed, and against what, before any run saw it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-heldout-commitment.v1"] = (
        HELDOUT_COMMITMENT_SCHEMA
    )
    #: What the sealed file is for, e.g. ``rules-heldout``.
    name: str = Field(min_length=1)
    sealed_on: date
    #: SHA-256 of the sealed file's exact bytes.
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: How many questions the file holds. Published so the denominator is
    #: fixed before the numerator is known.
    question_count: int = Field(ge=1)
    #: The pinned document the questions and labels were written against.
    document_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: The index generation the set is to be scored on. A run against a
    #: different generation is a different measurement.
    rules_index_generation_id: str = Field(min_length=1)
    #: Where the file will be published after the run. Recorded so a later
    #: reader can find what the hash refers to.
    published_path: str = Field(min_length=1)
    #: Who sealed it. Free text, required.
    sealed_by: str = Field(min_length=1)


class SealError(RuntimeError):
    """The seal cannot be made or does not hold."""


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's exact bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_questions(path: Path) -> int:
    """Count the questions in a golden-question YAML file.

    Reads the file as data only. It is not validated as a
    :class:`GoldenQuestionFile` here because the file may be sealed before its
    labels are complete; what is fixed at sealing is the count.
    """
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(questions, list) or not questions:
        raise SealError(f"{path} holds no 'questions' list to count")
    return len(questions)


def verify_commitment(commitment: HeldoutCommitment, published: Path) -> list[str]:
    """Return every way the published file fails to match its seal.

    Args:
        commitment: The sealed record.
        published: The file now being published.

    Returns:
        Human-readable mismatches; empty when the seal holds.
    """
    problems: list[str] = []
    if not published.is_file():
        return [f"{published} does not exist"]
    actual = file_sha256(published)
    if actual != commitment.file_sha256:
        problems.append(
            f"sha256 mismatch: sealed {commitment.file_sha256[:12]}, "
            f"published {actual[:12]}. The file changed after sealing"
        )
    try:
        count = count_questions(published)
    except SealError as exc:
        problems.append(str(exc))
    else:
        if count != commitment.question_count:
            problems.append(
                f"question count mismatch: sealed {commitment.question_count}, "
                f"published {count}"
            )
    return problems


def load_commitment(path: Path = DEFAULT_COMMITMENT) -> HeldoutCommitment | None:
    """Load the checked-in commitment, or ``None`` when nothing is sealed."""
    if not path.is_file():
        return None
    return HeldoutCommitment.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    )
