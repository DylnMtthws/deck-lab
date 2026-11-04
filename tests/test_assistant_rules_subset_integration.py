"""Offline bounded-validation fixtures; never starts Forge or another engine."""

import hashlib
import json
from pathlib import Path

import pytest

from sabermetrics.assistant_corrective_retrieval import corrective_evidence
from sabermetrics.assistant_mechanical_trace import verify_trace
from sabermetrics.assistant_rules import ingest_rules

FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures" / "assistant_mechanical_subset.json"
    ).read_text()
)


@pytest.mark.parametrize("scenario", FIXTURE["scenarios"], ids=lambda s: s["id"])
def test_offline_subset_harness_receipts(scenario):
    assert FIXTURE["schema"] == "assistant-mechanical-subset.v1"
    receipt = verify_trace(
        FIXTURE["cards"], FIXTURE["initial_state"], scenario["actions"]
    )
    # These are JSON adapter-ready bounded validations, never infinite labels.
    replay = json.loads(json.dumps(receipt))
    assert replay["status"] == scenario["expected_status"]
    assert len(replay["steps"]) == scenario["expected_valid_steps"]
    assert replay["final_state"]["mana"]["C"] == scenario["expected_final_mana_c"]


def test_retrieval_and_mechanics_have_separate_evidence_scopes(tmp_path):
    db = tmp_path / "local.sqlite"
    ingest_rules(
        db,
        "122.2 Counters are not retained when an object moves between zones.\n400.7 An object moving zones becomes a new object.\n401.1 A library is a zone.",
        source_url="https://magic.wizards.com/rules",
        version="synthetic-subset",
    )
    claim = "Fixture Battery retains counters on a zone change under rule 122.2."
    digest = hashlib.sha256(claim.encode()).hexdigest()
    review = {
        "claim_unit_version": "assistant-claim-fragments.v1",
        "claim_judgments": [
            {
                "claim_id": "c0",
                "part_index": 1,
                "start": 0,
                "end": len(claim),
                "reviewed_claim": claim,
                "claim_sha256": digest,
                "part_sha256": digest,
                "verdict": "conflict",
                "category": "rules_interaction",
                "reason": "Missing zone-change rule",
                "retrieval_gap": {
                    "kind": "missing_rule",
                    "missing_proposition": "Rule 122.2: counters are not retained across zone changes.",
                },
            }
        ],
    }
    retrieval = corrective_evidence(
        db, review, ["Answer", claim], [], FIXTURE["cards"], {}
    )
    assert retrieval["affected_claim_ids"] == ["c0"]
    assert retrieval["gaps"][0]["status"] == "candidate_evidence"
    assert retrieval["additional_sources"][0].startswith("Rule source: ")
    # Retrieval cannot mark the claim supported or execute the interaction.
    assert "verdict" not in retrieval["gaps"][0]
    bounce = FIXTURE["scenarios"][2]["actions"][:1]
    mechanical = verify_trace(FIXTURE["cards"], FIXTURE["initial_state"], bounce)
    assert mechanical["status"] == "valid"
    assert mechanical["final_state"]["objects"][0]["counters"] == {}
    assert mechanical["final_state"]["objects"][0]["incarnation"] == 1
