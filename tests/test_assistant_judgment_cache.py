"""Exact review reuse must never cross a validity boundary."""

import hashlib

import pytest

from sabermetrics.assistant_judgment_cache import (
    MAX_ENTRIES,
    MAX_TOTAL_BYTES,
    ExactJudgmentCache,
    JudgmentContext,
    JudgmentReceipt,
)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def context(**changes):
    return JudgmentContext(
        **{
            "owner_id": "owner",
            "run_id": "run",
            "deck_revision": 3,
            "deck_hash": digest("deck"),
            "task_scope_hash": digest("question"),
            "constraints_hash": digest("constraints"),
            "steering_hash": digest("steering"),
            "evidence_manifest_hash": digest("manifest"),
            "source_hashes": (digest("source"),),
            "freshness_hash": digest("currentness"),
            "prompt_version": "prompt-v1",
            "schema_version": "schema-v1",
            "model_id": "pinned-model",
            "policy_version": "policy-v1",
            "judgment_kind": "claim_review",
            **changes,
        }
    )


def receipt(ctx, text="claim", **changes):
    return JudgmentReceipt(
        **{
            "context_fingerprint": ctx.fingerprint(),
            "text_hash": digest(text),
            "evidence_manifest_hash": ctx.evidence_manifest_hash,
            "review_receipt_id": "review-1",
            "committed": True,
            "value": {"status": "supported", "claims": [{"text_hash": digest(text)}]},
            **changes,
        }
    )


def test_exact_committed_receipt_reuse_is_detached_and_does_not_invent_whole_answer_approval():
    cache = ExactJudgmentCache("run", "owner")
    ctx, reviewed = context(), receipt(context())
    assert cache.put(ctx, reviewed)
    reviewed.value["status"] = "tampered"
    found = cache.get(ctx, text_hash=digest("claim"))
    assert found.value["status"] == "supported"
    assert found.value.get("answer_approved") is None
    found.value["status"] = "tampered-again"
    assert cache.get(ctx, text_hash=digest("claim")).value["status"] == "supported"
    assert cache.get(ctx, text_hash=digest("edited claim")) is None


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("owner_id", "other"),
        ("run_id", "other"),
        ("deck_revision", 4),
        ("deck_hash", digest("new deck")),
        ("task_scope_hash", digest("new task")),
        ("constraints_hash", digest("new constraints")),
        ("steering_hash", digest("new steering")),
        ("evidence_manifest_hash", digest("new evidence")),
        ("source_hashes", (digest("new source"),)),
        ("freshness_hash", digest("new freshness")),
        ("prompt_version", "prompt-v2"),
        ("schema_version", "schema-v2"),
        ("model_id", "other-model"),
        ("policy_version", "policy-v2"),
        ("judgment_kind", "answer_review"),
    ],
)
def test_any_changed_validity_dimension_is_a_miss(field, replacement):
    cache = ExactJudgmentCache("run", "owner")
    assert cache.put(context(), receipt(context()))
    assert cache.get(context(**{field: replacement}), text_hash=digest("claim")) is None


@pytest.mark.parametrize("field", list(JudgmentContext.model_fields))
def test_every_validity_input_is_required(field):
    cache = ExactJudgmentCache("run", "owner")
    ctx = context().model_dump()
    del ctx[field]
    assert cache.get(ctx, text_hash=digest("claim")) is None
    assert not cache.put(ctx, receipt(context()))


def test_unknown_or_noncanonical_freshness_and_uncommitted_or_wrong_evidence_are_not_reused():
    cache = ExactJudgmentCache("run", "owner")
    invalid = context().model_dump()
    invalid["freshness_hash"] = "unknown"
    assert not cache.put(invalid, receipt(context()))
    invalid = context().model_dump()
    invalid["source_hashes"] = ("not-a-hash",)
    assert not cache.put(invalid, receipt(context()))
    invalid = context().model_dump()
    invalid["model_id"] = " "
    assert not cache.put(invalid, receipt(context()))
    candidate = receipt(context()).model_dump()
    candidate["committed"] = False
    assert not cache.put(context(), candidate)
    assert not cache.put(
        context(), receipt(context(), evidence_manifest_hash=digest("other"))
    )
    assert not cache.put(
        context(), receipt(context(), context_fingerprint=digest("other"))
    )


def test_lru_and_serialized_payload_limits_bound_memory_and_never_partially_accept_a_receipt():
    cache = ExactJudgmentCache("run", "owner")
    for n in range(MAX_ENTRIES):
        assert cache.put(context(), receipt(context(), text=str(n)))
    assert cache.get(context(), text_hash=digest("0")) is not None
    assert cache.put(context(), receipt(context(), text="extra"))
    assert cache.entries == MAX_ENTRIES
    assert cache.get(context(), text_hash=digest("1")) is None
    assert cache.get(context(), text_hash=digest("0")) is not None
    before = cache.entries
    assert not cache.put(
        context(), receipt(context(), text="huge", value={"text": "x" * 32768})
    )
    assert cache.entries == before
    for n in range(30):
        assert cache.put(
            context(),
            receipt(context(), text=f"large-{n}", value={"review": "x" * 16000}),
        )
    assert cache.retained_bytes <= MAX_TOTAL_BYTES
    assert cache.entries < MAX_ENTRIES


def test_non_json_values_and_nonfinite_receipts_are_rejected():
    cache = ExactJudgmentCache("run", "owner")
    assert not cache.put(context(), receipt(context(), value={"cost": float("nan")}))
    assert not cache.put(context(), receipt(context(), value={"object": object()}))
    assert cache.entries == 0


@pytest.mark.parametrize("commit", [False, 1, 0, "true", None])
def test_explicit_commit_marker_cannot_be_coerced(commit):
    cache = ExactJudgmentCache("run", "owner")
    candidate = receipt(context()).model_dump()
    candidate["committed"] = commit
    assert not cache.put(context(), candidate)
