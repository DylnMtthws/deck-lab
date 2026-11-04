"""Controlled model judgments test workflow boundaries, not recommendation quality."""

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_deck_inference import (
    HypothesisVerification,
    SourceFirstDiscovery,
    research_deck,
)
from sabermetrics.cedh.errors import ModelProviderError


def records():
    return [
        {
            "id": "a",
            "oracle_id": "a",
            "name": "Unfamiliar Lens",
            "mana_cost": "{U}",
            "type_line": "Artifact",
            "oracle_text": "Put the top card of your library into your hand.",
        },
        {
            "id": "b",
            "oracle_id": "b",
            "name": "Unfamiliar Toll",
            "mana_cost": "{B}",
            "type_line": "Creature — Cleric",
            "oracle_text": "Discard a card: Unfamiliar Toll gains flying until end of turn.",
        },
        {
            "id": "c",
            "oracle_id": "c",
            "name": "Unknown Scope",
            "type_line": "Enchantment",
            "oracle_text": "At the beginning of your end step, investigate twice unless the chosen player paid the chosen amount.",
        },
    ]


class Gateway:
    def __init__(self, *, bad_reference=False, duplicate=False, fail=False):
        self.requests = []
        self.bad_reference = bad_reference
        self.duplicate = duplicate
        self.fail = fail

    def generate(self, request):
        self.requests.append(request)
        payload = json.loads(request.user)
        payload["raw_inventory"] = [
            dict(zip(payload["wire_layout"]["source_columns"], row, strict=True))
            for row in payload["raw_inventory"]
        ]
        if self.fail:
            raise ModelProviderError("credential-like response must stay private")
        if request.schema is SourceFirstDiscovery:
            assert "candidates" not in payload
            assert "facts" not in payload
            assert any(
                "chosen player" in s["oracle_text"] for s in payload["raw_inventory"]
            )
            source_ids = [s["source_id"] for s in payload["raw_inventory"][:2]]
            return SimpleNamespace(
                value=SourceFirstDiscovery.model_validate(
                    {
                        "plans": [
                            {
                                "summary": "A conditional hand resource may pay a later discard cost.",
                                "source_ids": (
                                    ["forged"] if self.bad_reference else source_ids
                                ),
                                "assumptions": [
                                    "The top card is available and both relevant effects can be used."
                                ],
                                "resource_renewal": "Moving a card from library to hand may supply one discard payment.",
                                "stopping_conditions": "Library material is finite; no repeated access is established.",
                            }
                        ],
                        "coverage_gaps": [
                            "The chosen-player clause remains uninterpreted."
                        ],
                    }
                )
            )
        assert request.schema is HypothesisVerification
        assert "chosen-player" in " ".join(payload["discovery_gaps"])
        rows = []
        for candidate in payload["candidates"]:
            rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "verdict": "conditional",
                    "source_ids": candidate["source_ids"],
                    "explanation": "If the source moves a card into hand, that card can supply the discard payment.",
                    "conditions": [
                        "Each complete cost must be paid; neither current resources nor repeated access is observed."
                    ],
                    "resource_renewal": "This moves existing library material into hand; it creates no unlimited supply.",
                    "stopping_conditions": "The library is finite and further access remains conditional.",
                    "strategic_tradeoff": "The setup and available resources remain unknown; this is not a ranking.",
                }
            )
        if self.duplicate and rows:
            rows.append(deepcopy(rows[0]))
        return SimpleNamespace(
            value=HypothesisVerification.model_validate(
                {
                    "assessments": rows[:8],
                    "coverage_gaps": ["Principal-plan completeness is unknown."],
                }
            )
        )


def test_source_first_then_bound_assessment_without_third_generation():
    cards = records()
    original = deepcopy(cards)
    gateway = Gateway()
    reply, receipt = research_deck(gateway, cards, {}, "Explain this unfamiliar deck.")
    assert len(gateway.requests) == 2
    assert cards == original
    assert receipt["semantic_proof"] is False
    assert receipt["principal_plan_coverage"] == "unknown"
    assert receipt["fulfillment"] == "partial"
    assert receipt["assessments"]
    assert all(
        a["status"] == "model_assessed_conditional" for a in receipt["assessments"]
    )
    assert "{U}" in " ".join(reply["findings"])
    assert "Library material" in gateway.requests[1].user
    assert "no unlimited supply" in " ".join(reply["findings"])
    assert "Principal-plan completeness" in " ".join(reply["findings"])
    assert receipt["clause_manifest"]


def test_provider_failure_preserves_literal_facts_and_does_not_replay():
    gateway = Gateway(fail=True)
    reply, receipt = research_deck(gateway, records(), {}, "Explain the deck.")
    assert all(c["status"] == "unavailable" for c in receipt["calls"])
    assert len(gateway.requests) <= 2
    assert not receipt["assessments"]
    assert "credential-like" not in json.dumps(receipt)
    assert "Put the top card" in " ".join(reply["findings"])
    assert receipt["fulfillment"] == "unknown"


def test_duplicate_judgment_cannot_promote_affected_candidate():
    _, receipt = research_deck(
        Gateway(duplicate=True), records(), {}, "Explain the deck."
    )
    first = receipt["candidates"][0]["candidate_id"]
    assert first in receipt["unassessed_candidate_ids"]
    assert all(a["candidate_id"] != first for a in receipt["assessments"])
    assert "assessment_binding_rejected" in receipt["limitations"]


def test_insufficient_deadline_does_not_dispatch():
    gateway = Gateway()
    reply, receipt = research_deck(
        gateway, records(), {}, "Explain the deck.", deadline=time.monotonic() + 1
    )
    assert not gateway.requests
    assert not receipt["assessments"]
    assert any("deadline_reserve" in x for x in receipt["limitations"])
    assert "{U}" in " ".join(reply["findings"])


@pytest.mark.parametrize("signal", [False, ValueError("revision fence")])
def test_cancellation_propagates(signal):
    def check():
        if isinstance(signal, Exception):
            raise signal
        return signal

    gateway = Gateway()
    with pytest.raises((InterruptedError, ValueError)):
        research_deck(gateway, records(), {}, "Explain the deck.", check_current=check)
    assert not gateway.requests


def test_context_and_source_changes_change_evidence_and_cache_keys():
    first = Gateway()
    second = Gateway()
    a = records()
    b = records()
    b[0]["oracle_text"] += " Unknown extra restriction."
    _, one = research_deck(first, a, {}, "Explain the deck.")
    _, two = research_deck(
        second, b, {}, "Explain the deck.", context={"protected": ["a"]}
    )
    assert one["source_manifest_sha256"] != two["source_manifest_sha256"]
    assert first.requests[0].cache_key != second.requests[0].cache_key


def test_utf8_wire_overflow_withholds_complete_context_not_silent_pruning():
    cards = records()
    cards[0]["oracle_text"] = "無" * 18000
    gateway = Gateway()
    reply, receipt = research_deck(gateway, cards, {}, "Explain the deck.")
    assert not gateway.requests
    assert any("wire_limit" in x for x in receipt["limitations"])
    assert receipt["source_manifest"]
    assert len(" ".join(reply["findings"]).encode()) < 18000


def test_candidate_prerequisite_survives_verifier_omission():
    class OmittedCondition(Gateway):
        def generate(self, request):
            value = super().generate(request)
            if request.schema is HypothesisVerification:
                for row in value.value.assessments:
                    row.conditions = []
            return value

    reply, receipt = research_deck(
        OmittedCondition(), records(), {}, "Explain this deck."
    )
    assert receipt["assessments"]
    assert "The top card is available" in " ".join(
        receipt["assessments"][0]["conditions"]
    )
    assert "The top card is available" in " ".join(reply["findings"])


def test_contradicted_candidate_keeps_its_copied_source_fact():
    class Mixed(Gateway):
        def generate(self, request):
            value = super().generate(request)
            if request.schema is SourceFirstDiscovery:
                payload = json.loads(request.user)
                value.value.plans.append(
                    value.value.plans[0].model_copy(
                        update={
                            "source_ids": [payload["raw_inventory"][2][0]],
                            "summary": "A separate proposed interaction remains uncertain.",
                        }
                    )
                )
            else:
                value.value.assessments[1].verdict = "contradicted"
            return value

    reply, receipt = research_deck(Mixed(), records(), {}, "Explain this deck.")
    assert "chosen player paid" in " ".join(reply["findings"])
    assert receipt["fact_display_omissions"] == 0
    assert any(a["verdict"] == "conditional" for a in receipt["assessments"])
    assert any(a["verdict"] == "contradicted" for a in receipt["assessments"])


def test_callback_crossing_deadline_prevents_dispatch(monkeypatch):
    from sabermetrics import assistant_deck_inference as module

    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    gateway = Gateway()

    def progress(_event):
        clock[0] = 61.0

    _, receipt = research_deck(
        gateway, records(), {}, "Explain this deck.", on_progress=progress
    )
    assert not gateway.requests
    assert not receipt["assessments"]
    assert any("deadline" in reason for reason in receipt["limitations"])


def test_late_verification_is_withheld(monkeypatch):
    from sabermetrics import assistant_deck_inference as module

    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class Late(Gateway):
        def generate(self, request):
            value = super().generate(request)
            if request.schema is HypothesisVerification:
                clock[0] = 61.0
            return value

    reply, receipt = research_deck(Late(), records(), {}, "Explain this deck.")
    assert not receipt["assessments"]
    assert "Conditional assessment" not in " ".join(reply["findings"])
    assert "Put the top card" in " ".join(reply["findings"])


def test_commander_quantities_and_deck_scope_reach_both_calls():
    class Scoped(Gateway):
        def generate(self, request):
            data = json.loads(request.user)
            entries = data["context"]["deck_entries"]
            assert entries["columns"] == [
                "source_ids",
                "quantity",
                "is_commander",
                "admitted_zone",
            ]
            assert any(
                row[1:] == [2, True, "commander_deck_entry"] for row in entries["rows"]
            )
            assert "not" not in entries["scope"] or "resources" in entries["scope"]
            return super().generate(request)

    gateway = Scoped()
    research_deck(
        gateway,
        records(),
        {},
        "Explain the deck.",
        context={
            "deck_entries": [
                {
                    "card_id": "a",
                    "quantity": 2,
                    "is_commander": True,
                    "admitted_zone": "commander_deck_entry",
                },
                {
                    "card_id": "b",
                    "quantity": 1,
                    "is_commander": False,
                    "admitted_zone": "public_library_deck_entry",
                },
            ]
        },
    )
    assert len(gateway.requests) == 2


def test_representative_small_text_deck_fits_discovery_and_verification():
    cards = [
        {
            "id": str(i),
            "name": "Unknown device " + str(i),
            "type_line": "Artifact",
            "mana_cost": "{1}",
            "oracle_text": "Draw a card.",
        }
        for i in range(92)
    ]

    class Compact:
        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            data = json.loads(request.user)
            if request.schema is SourceFirstDiscovery:
                assert len(data["raw_inventory"]) == 92
                assert all(row[4] == "Draw a card." for row in data["raw_inventory"])
                return SimpleNamespace(
                    value=SourceFirstDiscovery.model_validate(
                        {
                            "plans": [
                                {
                                    "summary": "A draw effect may increase cards in hand under its unresolved setup.",
                                    "source_ids": [data["raw_inventory"][0][0]],
                                    "resource_renewal": "The cards come from the finite library, not an unlimited supply.",
                                    "stopping_conditions": "Ability access and finite library material remain necessary.",
                                }
                            ]
                        }
                    )
                )
            row = data["candidates"][0]
            return SimpleNamespace(
                value=HypothesisVerification.model_validate(
                    {
                        "assessments": [
                            {
                                "candidate_id": row["candidate_id"],
                                "source_ids": row["source_ids"],
                                "verdict": "unresolved",
                                "explanation": "The source does not establish the setup or a principal winning plan.",
                                "conditions": [],
                                "resource_renewal": "Drawing uses finite library material; no renewable access is established.",
                                "stopping_conditions": "The available source does not establish repeated access.",
                                "strategic_tradeoff": "A principal plan cannot be ranked from this text alone.",
                            }
                        ]
                    }
                )
            )

    gateway = Compact()
    _, receipt = research_deck(gateway, cards, {}, "Explain this deck.")
    assert len(gateway.requests) == 2
    assert len(receipt["source_manifest"]) == 92
    assert all(n <= 48000 for n in receipt["payload_utf8_bytes"].values())
    assert all("wire_limit" not in reason for reason in receipt["limitations"])


def test_fact_cache_is_bounded_reuse_not_semantic_judgment_reuse():
    from sabermetrics.assistant_deck_inference import clear_compilation_cache

    clear_compilation_cache()
    first = Gateway()
    second = Gateway()
    _, a = research_deck(first, records(), {}, "Explain this deck.")
    _, b = research_deck(second, records(), {}, "Explain this deck.")
    assert a["compilation_cache_hit"] is False
    assert b["compilation_cache_hit"] is True
    assert len(first.requests) == len(second.requests) == 2
    a["compiler_coverage"]["facts"] = -100
    third = Gateway()
    _, c = research_deck(third, records(), {}, "Explain this deck.")
    assert c["compiler_coverage"]["facts"] >= 0
    changed = records()
    changed[0]["oracle_text"] += " Unknown restriction."
    _, d = research_deck(Gateway(), changed, {}, "Explain this deck.")
    assert d["compilation_cache_hit"] is False


@pytest.mark.parametrize("supplemental", [False, True])
def test_scoped_card_rulings_reach_verification_without_general_rule_credit(
    supplemental,
):
    import hashlib

    text = "This ability does not provide unrestricted access to repeated activations."
    ruling = {
        "kind": "card_ruling",
        "source_id": "ruling:77",
        "card_oracle_id": "a",
        "text": text,
        "text_truncated": False,
        "source": "Attributed local ruling",
        "version": "2026-01-01",
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }

    class RulingGateway(Gateway):
        def generate(self, request):
            data = json.loads(request.user)
            if request.schema is SourceFirstDiscovery:
                result = super().generate(request)
                result.value.missing_rules = [
                    __import__(
                        "sabermetrics.assistant_deck_inference",
                        fromlist=["MissingRule"],
                    ).MissingRule(
                        proposition="Whether repeated access is restricted for this source.",
                        source_ids=[data["raw_inventory"][0][0]],
                    )
                ]
                return result
            assert text in request.user
            assert any(row[4] == text for row in data["raw_inventory"])
            return super().generate(request)

    _, receipt = research_deck(
        RulingGateway(),
        records(),
        {} if supplemental else {"hits": [ruling]},
        "Explain the deck.",
        rule_lookup=(
            (lambda gap: {"status": "candidate_evidence", "hits": [ruling]})
            if supplemental
            else None
        ),
    )
    assert any("rule:" in sid for sid in receipt["source_manifest"])


def test_cycle_wire_preserves_repeated_occurrences_and_link_endpoints():
    import runpy
    from pathlib import Path

    case = runpy.run_path(
        str(Path(__file__).parents[1] / "scripts/benchmark_deck_inference.py")
    )["development_cases"]()[0]

    class Capture:
        def generate(self, request):
            payload = json.loads(request.user)
            if request.schema is SourceFirstDiscovery:
                return SimpleNamespace(value=SourceFirstDiscovery(plans=[]))
            traces = [c for c in payload["candidates"] if c.get("links")]
            assert traces
            for trace in traces:
                assert len(trace["step_refs"]) == len(trace["links"]) + 1
                for index, link in enumerate(trace["links"]):
                    assert link[:2] == trace["step_refs"][index : index + 2]
                if trace.get("cycle_detected"):
                    assert len(set(trace["step_refs"])) < len(trace["step_refs"])
            return SimpleNamespace(value=HypothesisVerification(assessments=[]))

    research_deck(
        Capture(), case["cards"], case["rules"], "Explain this unfamiliar deck."
    )


def test_supplemental_rule_conflict_refreshes_original_bindings():
    from sabermetrics.assistant_deck_inference import MissingRule

    original = {
        "kind": "rule",
        "source_id": "602.1",
        "source": "Synthetic rules",
        "version": "v1",
        "complete": True,
        "text": "An activated ability has a cost and an effect.",
    }
    conflicting = {**original, "text": "An activated ability never has a cost."}

    class Conflict(Gateway):
        def generate(self, request):
            if request.schema is SourceFirstDiscovery:
                result = super().generate(request)
                result.value.missing_rules = [
                    MissingRule(
                        proposition="Check the activation cost rule.",
                        source_ids=[json.loads(request.user)["raw_inventory"][0][0]],
                    )
                ]
                return result
            payload = json.loads(request.user)
            assert original["text"] in request.user
            assert conflicting["text"] in request.user
            assert "conflicting_duplicate_identity" in request.user
            result = super().generate(request)
            rule_ids = [
                row[0]
                for row in payload["raw_inventory"]
                if row[4] in (original["text"], conflicting["text"])
            ]
            assert len(rule_ids) == 2
            for assessment in result.value.assessments:
                assessment.source_ids.extend(rule_ids)
            return result

    _, receipt = research_deck(
        Conflict(),
        records(),
        {"sources": [original]},
        "Explain this deck.",
        rule_lookup=lambda gap: {"sources": [conflicting]},
    )
    assert not receipt["assessments"]
    assert "assessment_binding_rejected" in receipt["limitations"]
    assert len(receipt["source_manifest"]) == len(records()) + 2


@pytest.mark.parametrize("oversized", [False, True])
def test_invalid_supplement_does_not_promote_or_destroy_original_sources(oversized):
    from sabermetrics.assistant_deck_inference import MissingRule

    original = {
        "kind": "rule",
        "source_id": "initial",
        "source": "Synthetic rules",
        "version": "v1",
        "complete": True,
        "text": "An activated ability has a cost and an effect.",
    }
    added = {
        **original,
        "source_id": "new",
        "complete": False,
        "text": "x" * 100100 if oversized else "Incomplete supplied rule text.",
    }

    class InvalidSupplement(Gateway):
        def generate(self, request):
            if request.schema is SourceFirstDiscovery:
                result = super().generate(request)
                result.value.missing_rules = [
                    MissingRule(
                        proposition="Check whether activation requires this condition.",
                        source_ids=[json.loads(request.user)["raw_inventory"][0][0]],
                    )
                ]
                return result
            assert not oversized, "Overflow must withhold the semantic call."
            payload = json.loads(request.user)
            result = super().generate(request)
            added_ids = [
                row[0] for row in payload["raw_inventory"] if row[4] == added["text"]
            ]
            assert len(added_ids) == 1
            for assessment in result.value.assessments:
                assessment.source_ids.extend(added_ids)
            return result

    gateway = InvalidSupplement()
    reply, receipt = research_deck(
        gateway,
        records(),
        {"sources": [original]},
        "Explain this deck.",
        rule_lookup=lambda gap: {"sources": [added]},
    )
    assert not receipt["assessments"]
    assert reply["findings"]
    if oversized:
        assert len(gateway.requests) == 1
        assert (
            "supplemental_inventory_overflow_semantics_withheld"
            in receipt["limitations"]
        )
        assert len(receipt["source_manifest"]) == len(records()) + 1
    else:
        assert "assessment_binding_rejected" in receipt["limitations"]
