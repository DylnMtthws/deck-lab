"""Hash commitment: the only seal available where every file is readable."""

from __future__ import annotations

from datetime import date

from sabermetrics.assistant.eval.heldout import (
    HeldoutCommitment,
    count_questions,
    file_sha256,
    verify_commitment,
)

QUESTIONS = """schema_version: research-golden-questions.v1
questions:
  - id: heldout-001
    ask: a question
  - id: heldout-002
    ask: another
"""


def _commitment(path) -> HeldoutCommitment:
    return HeldoutCommitment(
        name="rules-heldout",
        sealed_on=date(2026, 9, 12),
        file_sha256=file_sha256(path),
        question_count=count_questions(path),
        document_content_sha256="0" * 64,
        rules_index_generation_id="41fe565a",
        published_path="fixtures/research/heldout/rules_heldout.yaml",
        sealed_by="Dylan Matthews",
    )


def test_a_sealed_file_verifies(tmp_path):
    path = tmp_path / "heldout.yaml"
    path.write_text(QUESTIONS, encoding="utf-8")
    assert verify_commitment(_commitment(path), path) == []


def test_editing_the_file_after_sealing_breaks_the_seal(tmp_path):
    path = tmp_path / "heldout.yaml"
    path.write_text(QUESTIONS, encoding="utf-8")
    sealed = _commitment(path)
    path.write_text(QUESTIONS + "  - id: heldout-003\n    ask: added later\n")
    problems = verify_commitment(sealed, path)
    assert any("sha256 mismatch" in p for p in problems), problems
    assert any("question count" in p for p in problems), problems


def test_a_missing_file_is_a_stated_failure(tmp_path):
    path = tmp_path / "heldout.yaml"
    path.write_text(QUESTIONS, encoding="utf-8")
    sealed = _commitment(path)
    assert verify_commitment(sealed, tmp_path / "elsewhere.yaml") == [
        f"{tmp_path / 'elsewhere.yaml'} does not exist"
    ]
