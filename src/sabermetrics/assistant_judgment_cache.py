"""Exact, bounded same-run reuse of committed version-bound judgments.

This cache is a performance hint, never financial admission, authorization,
review policy or a source of new semantic judgments. Missing validity inputs
are misses. There is no cross-request lookup or in-flight coordination.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_ENTRIES = 64
MAX_RECEIPT_BYTES = 32 * 1024
MAX_TOTAL_BYTES = 256 * 1024
_SHA256 = r"^[0-9a-f]{64}$"


class JudgmentContext(BaseModel):
    """Complete immutable validity key; no wildcard or unknown freshness values."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    owner_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    deck_revision: int = Field(ge=0)
    deck_hash: str = Field(pattern=_SHA256)
    task_scope_hash: str = Field(pattern=_SHA256)
    constraints_hash: str = Field(pattern=_SHA256)
    steering_hash: str = Field(pattern=_SHA256)
    evidence_manifest_hash: str = Field(pattern=_SHA256)
    source_hashes: tuple[str, ...] = Field(min_length=1, max_length=256)
    freshness_hash: str = Field(pattern=_SHA256)
    prompt_version: str = Field(min_length=1, max_length=200)
    schema_version: str = Field(min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=200)
    judgment_kind: Literal["claim_review", "answer_review", "candidate_comparison"]

    def fingerprint(self) -> str:
        """Hash canonical validity state, retaining source identity and order."""
        for value in self.source_hashes:
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Every source hash must be lowercase SHA-256.")
        for field in (
            "owner_id",
            "run_id",
            "prompt_version",
            "schema_version",
            "model_id",
            "policy_version",
        ):
            value = getattr(self, field)
            if not value.strip() or value != value.strip():
                raise ValueError("Validity identity must be nonblank and canonical.")
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class JudgmentReceipt(BaseModel):
    """A caller-validated, committed verdict bound to the exact cache key.

    `value` must already have passed the existing review schema and policy.
    The cache does not turn supported subclaims into a whole-answer approval.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal["assistant.judgment-reuse.v1"] = "assistant.judgment-reuse.v1"
    context_fingerprint: str = Field(pattern=_SHA256)
    text_hash: str = Field(pattern=_SHA256)
    evidence_manifest_hash: str = Field(pattern=_SHA256)
    review_receipt_id: str = Field(min_length=1, max_length=200)
    committed: Literal[True]
    value: dict[str, Any]

    @field_validator("committed", mode="before")
    @classmethod
    def require_explicit_commit(cls, value: Any) -> bool:
        """Literal validation alone would coerce integer 1 to True."""
        if value is not True:
            raise ValueError("A committed receipt requires explicit True.")
        return True

    @field_validator("review_receipt_id")
    @classmethod
    def require_receipt_identity(cls, value: str) -> str:
        """Do not accept missing identity disguised as whitespace."""
        if not value.strip() or value != value.strip():
            raise ValueError("Review receipt identity must be nonblank and canonical.")
        return value


def _context(value: JudgmentContext | Mapping[str, Any]) -> JudgmentContext | None:
    try:
        context = (
            value
            if isinstance(value, JudgmentContext)
            else JudgmentContext.model_validate(value)
        )
        context.fingerprint()
        return context
    except (ValidationError, ValueError, TypeError):
        return None


class ExactJudgmentCache:
    """LRU storage of JSON receipts for one run and a strictly complete context.

    Construction supplies the current owner and run; every access must match.
    JSON round trips detach nested structures and reject non-JSON/non-finite
    values. Entry and byte limits bound both retained payload and eviction.
    """

    def __init__(self, run_id: str, owner_id: str) -> None:
        if not all(
            isinstance(v, str) and v.strip() and len(v) <= 200
            for v in (run_id, owner_id)
        ):
            raise ValueError("A bounded owner and run identity are required.")
        self.run_id = run_id
        self.owner_id = owner_id
        self._entries: OrderedDict[str, bytes] = OrderedDict()
        self._size = 0

    def _key(
        self, context: JudgmentContext | Mapping[str, Any], text_hash: str
    ) -> tuple[JudgmentContext, str] | None:
        parsed = _context(context)
        if (
            parsed is None
            or parsed.run_id != self.run_id
            or parsed.owner_id != self.owner_id
        ):
            return None
        if (
            not isinstance(text_hash, str)
            or len(text_hash) != 64
            or any(c not in "0123456789abcdef" for c in text_hash)
        ):
            return None
        fingerprint = parsed.fingerprint()
        return parsed, fingerprint + ":" + text_hash

    def put(
        self,
        context: JudgmentContext | Mapping[str, Any],
        receipt: JudgmentReceipt | Mapping[str, Any],
    ) -> bool:
        """Store only a committed receipt whose exact text/evidence key matches."""
        try:
            parsed = (
                receipt
                if isinstance(receipt, JudgmentReceipt)
                else JudgmentReceipt.model_validate(receipt)
            )
            pair = self._key(context, parsed.text_hash)
            if pair is None:
                return False
            ctx, key = pair
            if (
                parsed.context_fingerprint != ctx.fingerprint()
                or parsed.evidence_manifest_hash != ctx.evidence_manifest_hash
            ):
                return False
            encoded = json.dumps(
                parsed.model_dump(mode="python"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > MAX_RECEIPT_BYTES:
                return False
        except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
            return False
        prior = self._entries.pop(key, None)
        if prior is not None:
            self._size -= len(prior)
        self._entries[key] = encoded
        self._size += len(encoded)
        while len(self._entries) > MAX_ENTRIES or self._size > MAX_TOTAL_BYTES:
            _, old = self._entries.popitem(last=False)
            self._size -= len(old)
        return True

    def get(
        self, context: JudgmentContext | Mapping[str, Any], *, text_hash: str
    ) -> JudgmentReceipt | None:
        """Return a detached exact receipt, or miss without changing any policy."""
        pair = self._key(context, text_hash)
        if pair is None:
            return None
        _, key = pair
        encoded = self._entries.get(key)
        if encoded is None:
            return None
        self._entries.move_to_end(key)
        return JudgmentReceipt.model_validate(json.loads(encoded))

    @property
    def retained_bytes(self) -> int:
        """Report JSON payload bytes only; never include cached content itself."""
        return self._size

    @property
    def entries(self) -> int:
        """Report the bounded number of retained exact receipts."""
        return len(self._entries)
