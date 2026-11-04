"""Integrity checks for independent authored swap challenges and KPI accounting."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "swap_benchmark",
    Path(__file__).resolve().parents[1] / "scripts/evaluate_swap_quality.py",
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_reference_corpus_has_distinct_archetypes_and_quality_challenges():
    corpus, decks, cards = benchmark.load_challenge()
    assert set(decks) == {
        "affordable-vivi",
        "high-power-vivi",
        "affordable-sauron",
        "high-power-sauron",
    }
    assert len({case["id"] for case in corpus["cases"]}) == len(corpus["cases"])
    for case in corpus["cases"]:
        assert case["rubric"]
        assert case["outgoing"] in cards
        assert case["incoming"] in cards or case["id"] == "missing-evidence"
    assert any(c.get("theme") for c in corpus["cases"])
    assert any(c.get("budget") for c in corpus["cases"])
    assert any("accepted" in c["acceptable_outcomes"] for c in corpus["cases"])


def test_kpi_fails_blanket_rejection_and_blanket_acceptance():
    for outcome in ("rejected", "accepted", "demonstrated_upgrade"):
        report = benchmark.evaluate(
            lambda *args: {"outcome": outcome, "evidence": ["authored"]}
        )
        assert not report["kpi"]["all_passed"]
    report = benchmark.evaluate(lambda *args: {"outcome": "demonstrated_upgrade"})
    assert (
        report["kpi"]["false_demonstrated_upgrade_labels"]
        == report["kpi"]["cases_total"]
    )


def test_kpi_requires_evidence_and_preserves_reference_documents():
    def destructive(document, *args):
        document["entries"] = []
        return {"outcome": "flagged", "evidence": ["concern"]}

    report = benchmark.evaluate(destructive)
    assert all(row["source_mutated"] and not row["passed"] for row in report["cases"])
    report = benchmark.evaluate(lambda *args: {"outcome": "accepted"})
    assert all(not row["passed"] for row in report["cases"])
