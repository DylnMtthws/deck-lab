"""Smaller wire context must retain complete semantic data and local bindings."""

import json
from types import SimpleNamespace

from sabermetrics.assistant_claim_fragments import fragment_units, matching_judgment
from sabermetrics.assistant_claim_review import evidence_catalog, review_claims
from tests.test_assistant_claim_fragments import PARTS, SOURCE, Reviewer


def test_compact_wire_retains_text_evidence_and_local_hash_binding():
    captured = {}

    class Capture(Reviewer):
        def generate(self, request):
            captured["request"] = request
            return super().generate(request)

    _, receipt = review_claims(Capture(), PARTS, [SOURCE], "draw")
    request = captured["request"]
    state = json.loads(request.user)
    units = fragment_units(PARTS, set())
    removed = {"claim_sha256", "part_sha256"}
    assert state["claims"] == [
        {**{k: v for k, v in u.items() if k not in removed}, "source_indices": [0]}
        for u in units
    ]
    assert state["parent_parts"] == {"1": PARTS[1]}
    assert state["source_records"] == evidence_catalog([SOURCE])[0]
    assert all(
        matching_judgment(u, j) for u, j in zip(units, receipt["claim_judgments"])
    )
    metrics = receipt["review_model"]
    assert metrics["input_bytes"] == len(request.user.encode())
    assert metrics["transport_bytes_saved"] > 450
    assert (
        metrics["uncompressed_input_bytes"] - metrics["input_bytes"]
        == metrics["transport_bytes_saved"]
    )
    assert request.prompt_version == "assistant-claim-review.v9"


def test_explicit_gap_survives_wire_and_bound_receipt():
    parts = ["Lead", "Draw Engine ({B}) — If it returns, it is a new object."]
    gap = {
        "kind": "missing_rule",
        "missing_proposition": "A permanent returning from a graveyard is a new object.",
    }

    class Gateway:
        def generate(self, request):
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "partial",
                        "c0": {
                            "k": "rules",
                            "v": "unverified",
                            "e": [],
                            "why": "Need zone-change rule.",
                            "gap": gap,
                        },
                    }
                )
            )

    _, receipt = review_claims(Gateway(), parts, [SOURCE], "recursion")
    judgment = receipt["claim_judgments"][0]
    assert judgment["retrieval_gap"] == gap
    assert judgment["verdict"] == "unverified"
    assert matching_judgment(fragment_units(parts, set())[0], judgment)


def test_citation_downgrade_has_diagnostic_without_invented_missing_dependency():
    parts = ["Lead", "Draw Engine ({B}) — This returns as a new object."]

    class Gateway:
        def generate(self, request):
            return SimpleNamespace(
                value=request.schema.model_validate(
                    {
                        "f": "complete",
                        "c0": {"k": "rules", "v": "supported", "e": ["e0"]},
                    }
                )
            )

    _, receipt = review_claims(Gateway(), parts, [SOURCE], "recursion")
    judgment = receipt["claim_judgments"][0]
    assert judgment["verdict"] == "unverified"
    assert judgment["reason"] == "missing_applicable_rule_citation"
    assert judgment["retrieval_gap"] is None


def test_selected_identical_claims_keep_occurrence_identity_on_wire():
    parent = (
        "Draw Engine ({B}) — For the draw ability. Activation costs {1}. "
        "For the return ability. Activation costs {1}."
    )
    variants = [
        ["Lead", "Other ({U}) — One. Two. Three.", parent],
        ["Lead", "Other ({U}) — One.", parent],
    ]
    captured = []

    class Gateway:
        def generate(self, request):
            captured.append(request)
            return SimpleNamespace(value={"fulfillment": "partial", "judgments": []})

    for parts in variants:
        review_claims(Gateway(), parts, [SOURCE], "draw", claim_ids={"c4"})
    left, right = [json.loads(r.user) for r in captured]
    assert (
        left["claims"][0]["claim"]
        == right["claims"][0]["claim"]
        == "Activation costs {1}."
    )
    assert left["claims"][0]["start"] != right["claims"][0]["start"]
    assert left["parent_parts"] == right["parent_parts"]
    assert captured[0].cache_key != captured[1].cache_key


def test_production_schema_omits_subspan_proposals_but_retains_local_fact():
    import pytest
    from pydantic import ValidationError

    from tests.test_assistant_claim_subspan_contract import PARTS, SOURCE

    class Gateway:
        calls = 0

        def generate(self, request):
            self.calls += 1
            schema = request.schema.model_json_schema(by_alias=True)
            properties = schema["$defs"]["WireJudgment"]["properties"]
            assert "ss" not in properties
            assert "supported_subspans" not in properties
            assert "Optional ss" not in request.system
            assert "dependency_refs" not in request.system
            wire = {"f": "partial", "c0": {"k": "rules", "v": "unverified", "e": []}}
            with pytest.raises(ValidationError):
                request.schema.model_validate(
                    {"f": "partial", "c0": {**wire["c0"], "ss": []}}
                )
            return SimpleNamespace(value=request.schema.model_validate(wire))

    gateway = Gateway()
    corrected, receipt = review_claims(
        gateway, PARTS, [SOURCE], "mana", evidence_indices={0}
    )
    assert gateway.calls == 1
    assert corrected == PARTS
    judgment = receipt["claim_judgments"][0]
    assert judgment["verdict"] == "unverified"
    assert receipt["fulfillment"] == "unknown"
    unit = fragment_units(PARTS, set())[0]
    assert matching_judgment(unit, judgment)
    (span,) = judgment["supported_subspans"]
    assert span["text"] == "Mystic has a tap ability."
    assert span["evidence_ids"] == ["e2"]
    assert span["citations"] == [{"source_index": 0, "quote": "{T}: Add {G}."}]
    assert span["parent_claim_sha256"] == unit["claim_sha256"]
    assert span["parent_part_sha256"] == unit["part_sha256"]
    assert span["source_sha256"] == receipt["source_sha256"]
    assert receipt["evidence_indices"] == [0]
