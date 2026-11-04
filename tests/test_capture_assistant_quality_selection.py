"""Focused adapter tests; no benchmark labels or model calls."""

import copy
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/capture_assistant_quality_selection.py"
)
spec = importlib.util.spec_from_file_location("selection_capture", SCRIPT)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


@pytest.fixture
def case():
    return {
        "id": "synthetic",
        "prompt": "Prioritize a repeatable sacrifice outlet and discuss cost and setup.",
        "context": {
            "recommendation_mode": "prioritize_or_retain_from_current_shell",
            "deck": ["Outlet", "Draw"],
            "candidate_pool": ["Outlet", "Draw"],
        },
        "requested": {"recommendations": 1},
        "claim_questions": [{"id": "probe-1", "assertion": "An unassessed assertion."}],
        "evidence": [
            {
                "id": "card:Outlet",
                "name": "Outlet",
                "oracle_id": "outlet",
                "type_line": "Legendary Creature",
                "mana_cost": "{B}",
                "oracle_text": "Sacrifice another creature: Draw a card.",
            },
            {
                "id": "card:Draw",
                "name": "Draw",
                "oracle_id": "draw",
                "type_line": "Sorcery",
                "mana_cost": "{U}",
                "oracle_text": "Draw a card.",
            },
        ],
    }


def test_shell_membership_and_no_commander_inference(case):
    original = copy.deepcopy(case)
    document, candidates = adapter.document_from_case(case)
    assert [c["name"] for c in candidates] == case["context"]["candidate_pool"]
    assert all(
        c["zone_name"] == "Library" and not c.get("is_commander")
        for c in document["entries"]
    )
    assert case == original
    case["context"]["candidate_pool"].append("Not in shell")
    with pytest.raises(ValueError, match="current shell"):
        adapter.document_from_case(case)


def test_missing_evidence_fails_instead_of_inventing(case):
    case["evidence"].pop()
    with pytest.raises(ValueError, match="Missing public card evidence"):
        adapter.document_from_case(case)


def test_current_capture_is_actual_rank_and_render_with_missing_dimensions(case):
    receipt = adapter.capture([case])[0]
    assert receipt["scope"] == "offline_selection_only"
    assert receipt["status"] == "partial"
    assert receipt["candidates"] == case["context"]["candidate_pool"]
    rows = receipt["ranked_candidates"]
    assert [r["rank_key"] for r in rows] == sorted(r["rank_key"] for r in rows)
    assert receipt["recommendations"] == [
        {"card": rows[0]["card"], "rationale": rows[0]["rationale"]}
    ]
    for row in rows:
        from sabermetrics.assistant_strategic_obligations import (
            render_strategic_obligations,
            strategic_rank_key,
        )
        from sabermetrics.assistant_suitability import render_fit_reason

        assert row["rank_key"] == [
            *strategic_rank_key(row["strategic_obligations"]),
            -row["suitability"]["strategic_judgment"]["priority"],
        ]
        assert row["rationale"] == render_fit_reason(
            row["suitability"]
        ) + " " + render_strategic_obligations(row["strategic_obligations"])
    for dimension in ("claims", "cuts", "packages", "addressed_topics", "answer"):
        assert dimension not in receipt
    assert receipt["telemetry"]["latency_ms"] >= 0
    assert receipt["telemetry"]["cost_usd"] == 0
    assert receipt["telemetry"]["input_tokens"] is None
    assert receipt["telemetry"]["output_tokens"] is None
    assert receipt["instrumentation"]["cost_basis"] == "modeled_zero_offline_only"
    assert len(receipt["capture_metadata"]["code_freeze"]["sha256"]) == 64


def test_baseline_archived_source_is_isolated_and_has_no_current_obligations(case):
    receipt = adapter.capture([case], "eaeeda91")[0]
    metadata = receipt["capture_metadata"]
    assert metadata["source"].startswith("eaeeda91")
    assert metadata["ranking"] == "baseline_negative_suitability_priority_only"
    assert metadata["obligation_renderer_available"] is False
    assert all(
        row["strategic_obligations"] is None for row in receipt["ranked_candidates"]
    )
    for row in receipt["ranked_candidates"]:
        assert row["rank_key"] == [
            -row["suitability"]["strategic_judgment"]["priority"]
        ]
    assert not any(
        "strategic_obligations.py" in path
        for path in metadata["code_freeze"]["files_sha256"]
    )


def test_public_reader_never_opens_labels(monkeypatch):
    original = Path.read_bytes
    seen = []

    def guarded(path):
        seen.append(path.name)
        assert "labels" not in path.name and "heldout" not in path.name
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    cases = adapter.public_cases("development")
    assert cases and all(c["evidence"] for c in cases)
    assert set(seen) == {
        "development.cases.json",
        "development.evidence.json",
        "rules.evidence.json",
    }


def test_heldout_requires_freeze_before_reading_inputs(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--split",
            "heldout",
            "--output",
            str(tmp_path / "capture.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "requires --final-code-freeze before loading inputs" in result.stderr
    assert not (tmp_path / "capture.json").exists()


def test_freeze_mismatch_rejects_execution(case):
    with pytest.raises(ValueError, match="Freeze mismatch"):
        adapter.capture([case], final_code_freeze="not-the-source-hash")


def test_freeze_hashes_adapter_and_source_bytes(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    module = source / "example.py"
    module.write_text("VALUE = 1\n")
    before = adapter.code_freeze(tmp_path)
    assert set(before["files_sha256"]) == {"capture_adapter", "src/example.py"}
    module.write_text("VALUE = 2\n")
    assert adapter.code_freeze(tmp_path)["sha256"] != before["sha256"]
