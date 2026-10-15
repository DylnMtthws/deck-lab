"""Synthetic control evidence tests the gate, not the assistant's MTG expertise."""

import copy
import json
import stat
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sabermetrics.assistant_expertise import (
    DOMAIN_NAMES,
    RUBRIC,
    Benchmark,
    Key,
    Policy,
    Review,
    Run,
    digest,
    evaluate,
    prepare_packet,
    wilson_lower,
)
from scripts.evaluate_assistant_expertise import main, read

NOW = datetime.now(UTC)
QUOTE = "Keep the repeatable engine unless a supported tradeoff justifies its loss."


@pytest.fixture
def evidence():
    benchmark = Benchmark.model_validate(
        {
            "version": "assistant-expertise-benchmark.v1",
            "split": "sealed_holdout",
            "sealed_at": (NOW - timedelta(days=1)).isoformat(),
            "curator_id": "curator",
            "cases": [
                {
                    "id": f"case-{i}",
                    "domain": DOMAIN_NAMES[i % 6],
                    "commander": f"commander-{i}",
                    "archetype": f"archetype-{i % 6}",
                    "author_ids": ["question-author"],
                    "prompt": f"Control question {i}",
                    "context": "Synthetic test context, not an MTG benchmark.",
                    "references": [
                        {
                            "id": "source",
                            "text": QUOTE,
                            "source": "Synthetic test evidence",
                        }
                    ],
                }
                for i in range(12)
            ],
        }
    )
    identity = {
        "revision": "a" * 40,
        "model_id": "candidate-model",
        "config_sha256": "b" * 64,
        "corpus_sha256": "c" * 64,
        "contributor_ids": ["answer-builder"],
    }
    run = Run.model_validate(
        {
            "version": "assistant-expertise-run.v1",
            "benchmark_sha256": digest(benchmark),
            "created_at": (NOW - timedelta(hours=1)).isoformat(),
            "candidate": identity,
            "baseline": {
                **identity,
                "revision": "d" * 40,
                "model_id": "baseline-model",
            },
            "total_cost_usd": 1.0,
            "pairs": [
                {
                    "case_id": c.id,
                    "candidate": {
                        "text": "Preserve the repeatable engine and explain its tradeoffs.",
                        "citations": [{"reference_id": "source", "quote": QUOTE}],
                    },
                    "baseline": {
                        "text": "Use a generic replacement without comparing functions.",
                        "citations": [],
                    },
                }
                for c in benchmark.cases
            ],
        }
    )
    policy = Policy.model_validate(
        {
            "version": "assistant-expertise-policy.v1",
            "min_cases": 12,
            "min_commanders": 12,
            "min_archetypes": 6,
            "min_cases_per_domain": 2,
            "reviews_per_case": 2,
            "min_dimension_score": 3,
            "min_pass_fraction": 0.9,
            "min_domain_pass_fraction": 0.8,
            "max_regression_fraction": 0.05,
            "min_decisive_commander_groups": 8,
            "min_group_win_lower_bound": 0.5,
            "max_age_days": 14,
            "max_benchmark_age_days": 90,
            "max_total_cost_usd": 5.0,
            "approved_curators": ["curator"],
            "approved_benchmarks": [digest(benchmark)],
            "approved_experts": {
                name: {"person_id": name, "domains": list(DOMAIN_NAMES)}
                for name in ("expert-one", "expert-two")
            },
        }
    )
    packet, key = prepare_packet(benchmark, run)
    reviews = [
        Review.model_validate(
            {
                "case_id": c.id,
                "packet_sha256": key.packet_sha256,
                "reviewer_id": reviewer,
                "kind": "human_expert",
                "scores": {
                    s: {d: 4 if s == key.candidate_slots[c.id] else 2 for d in RUBRIC}
                    for s in ("A", "B")
                },
                "critical_errors": {"A": [], "B": []},
                "preferred": key.candidate_slots[c.id],
                "rationale": "Synthetic control judgment; not actual independent MTG review.",
            }
        )
        for c in benchmark.cases
        for reviewer in policy.approved_experts
    ]
    return benchmark, run, packet, key, reviews, policy


def grade(evidence):
    benchmark, run, _packet, key, reviews, policy = evidence
    return evaluate(benchmark, run, key, reviews, policy, now=NOW)


def test_complete_control_passes_and_binds_every_identity(evidence):
    result = grade(evidence)
    assert result["status"] == "qualified_improvement"
    assert result["promotion_eligible"]
    assert result["cases"] == 12 and result["pass_fraction"] == 1
    assert result["commander_group_wins"] == 12
    assert result["group_win_wilson_lower_95"] > 0.5
    assert sum(v["total"] for v in result["by_domain"].values()) == 12
    assert result["policy_sha256"] == digest(evidence[-1])
    assert "never deployment authorization" in result["scope"]


def test_packet_blinds_system_metadata_and_balances_slots(evidence):
    benchmark, run, packet, key, *_ = evidence
    encoded = json.dumps(packet)
    assert run.candidate.model_id not in encoded
    assert run.baseline.model_id not in encoded
    assert run.candidate.revision not in encoded
    assert "contributor_ids" not in encoded
    assert list(key.candidate_slots.values()).count("A") == 6
    for case in packet["cases"]:
        assert (
            case["answers"][key.candidate_slots[case["id"]]]["text"]
            == run.pairs[0].candidate.text
        )
    assert key.benchmark_sha256 == digest(benchmark)


def test_changed_answer_or_blinding_key_cannot_reuse_reviews(evidence):
    evidence[1].pairs[0].candidate.text = "Changed after the experts reviewed it."
    with pytest.raises(ValueError, match="changed after blinding"):
        grade(evidence)


def test_swapping_slots_invalidates_packet_hash(evidence):
    key = evidence[3]
    key.candidate_slots["case-0"] = "B" if key.candidate_slots["case-0"] == "A" else "A"
    with pytest.raises(ValueError, match="changed after blinding"):
        grade(evidence)


def test_model_votes_never_substitute_for_experts(evidence):
    for review in evidence[4]:
        review.kind = "model_judge"
    report = grade(evidence)
    assert report["status"] == "blocked"
    assert report["advisory_model_reviews"] == 24
    assert all(
        c["expert_reviews"] == 0 and not c["passed"] for c in report["case_results"]
    )


def test_unenrolled_or_duplicate_people_never_create_quorum(evidence):
    evidence[-1].approved_experts["expert-two"].person_id = "expert-one"
    with pytest.raises(ValueError, match="Duplicate reviewer/person"):
        grade(evidence)


@pytest.mark.parametrize(
    "change",
    [
        "no_experts",
        "unknown_expert",
        "own_work",
        "own_question",
        "wrong_domain",
        "unapproved_benchmark",
        "unapproved_curator",
        "coverage",
    ],
)
def test_independence_and_coverage_blocks(evidence, change):
    policy = evidence[-1]
    if change == "no_experts":
        policy.approved_experts.clear()
    elif change == "unknown_expert":
        evidence[4][0].reviewer_id = "not-enrolled"
    elif change == "own_work":
        policy.approved_experts["expert-one"].person_id = "answer-builder"
    elif change == "own_question":
        policy.approved_experts["expert-one"].person_id = "question-author"
    elif change == "wrong_domain":
        policy.approved_experts["expert-one"].domains = ["rules"]
    elif change == "unapproved_benchmark":
        policy.approved_benchmarks = []
    elif change == "unapproved_curator":
        policy.approved_curators = []
    else:
        policy.min_cases = 120
    report = grade(evidence)
    assert report["status"] == "blocked"
    assert not report["promotion_eligible"]


def test_one_critical_error_cannot_be_averaged_away(evidence):
    key, reviews = evidence[3:5]
    reviews[0].critical_errors[key.candidate_slots[reviews[0].case_id]] = [
        "unsupported_rules_claim"
    ]
    report = grade(evidence)
    assert report["status"] == "failed"
    assert report["pass_fraction"] > 0.9
    assert any("Critical factual" in f for f in report["failures"])


def test_weak_domain_cannot_hide_in_high_average(evidence):
    key, reviews = evidence[3:5]
    reviews[0].scores[key.candidate_slots[reviews[0].case_id]]["tradeoffs"] = 1
    report = grade(evidence)
    assert report["status"] == "failed"
    assert any("domain" in f for f in report["failures"])


def test_opposing_experts_require_adjudication(evidence):
    review = evidence[4][0]
    review.preferred = "B" if review.preferred == "A" else "A"
    assert any("disagreement" in s for s in grade(evidence)["blocked_reasons"])


def test_ties_do_not_demonstrate_improvement(evidence):
    for review in evidence[4]:
        review.preferred = "tie"
    report = grade(evidence)
    assert report["status"] == "qualified_no_gain"
    assert not report["promotion_eligible"]
    assert report["group_win_wilson_lower_95"] is None


def test_one_candidate_vote_plus_tie_is_not_a_win(evidence):
    evidence[4][0].preferred = "tie"
    report = grade(evidence)
    assert report["case_results"][0]["preference"] == "tie"
    assert report["commander_group_wins"] == 11


def test_reported_budget_overrun_fails(evidence):
    evidence[-1].max_total_cost_usd = 0.5
    assert any("cost budget" in f for f in grade(evidence)["failures"])


def test_stale_run_is_blocked(evidence):
    b, r, _, k, reviews, p = evidence
    result = evaluate(b, r, k, reviews, p, now=NOW + timedelta(days=20))
    assert result["status"] == "blocked"
    assert any("stale" in s for s in result["blocked_reasons"])


def test_exact_quote_check_detects_fabrication(evidence):
    benchmark, run, _, _, reviews, policy = evidence
    run.pairs[0].candidate.citations[
        0
    ].quote = "This quote never occurred in the supplied reference."
    _packet, key = prepare_packet(benchmark, run)
    remapped = []
    for old in reviews:
        new = old.model_dump()
        new.update(
            packet_sha256=key.packet_sha256, preferred=key.candidate_slots[old.case_id]
        )
        remapped.append(Review.model_validate(new))
    result = evaluate(benchmark, run, key, remapped, policy, now=NOW)
    assert result["status"] == "failed"
    assert any("citation" in r for r in result["case_results"][0]["reasons"])


def test_wrong_packet_and_missing_pairs_rejected(evidence):
    evidence[4][0].packet_sha256 = "0" * 64
    with pytest.raises(ValueError, match="different packet"):
        grade(evidence)
    evidence[1].pairs.pop()
    with pytest.raises(ValueError, match="exactly every"):
        grade(evidence)


def test_duplicate_and_empty_benchmarks_rejected(evidence):
    raw = evidence[0].model_dump()
    raw["cases"].append(copy.deepcopy(raw["cases"][0]))
    with pytest.raises(ValidationError):
        Benchmark.model_validate(raw)
    raw["cases"] = []
    with pytest.raises(ValidationError):
        Benchmark.model_validate(raw)


def test_schema_disallows_missing_scores_and_nonfinite_cost(evidence):
    raw = evidence[4][0].model_dump()
    raw["scores"]["A"].pop("grounding")
    with pytest.raises(ValidationError):
        Review.model_validate(raw)
    run = evidence[1].model_dump()
    run["total_cost_usd"] = float("nan")
    with pytest.raises(ValidationError):
        Run.model_validate(run)


def test_wilson_uses_commander_groups():
    assert wilson_lower(12, 12) > 0.5
    assert wilson_lower(6, 12) < 0.5
    assert wilson_lower(0, 0) is None


def write_inputs(tmp_path, evidence):
    b, r, _, k, reviews, p = evidence
    for name, data in {
        "benchmark": b.model_dump(),
        "run": r.model_dump(),
        "key": k.model_dump(),
        "reviews": [v.model_dump() for v in reviews],
        "policy": p.model_dump(),
    }.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data))


def test_cli_pins_release_and_reports_missing_experts(tmp_path, evidence):
    write_inputs(tmp_path, evidence)
    args = [
        "gate",
        *[
            arg
            for name in ("benchmark", "run", "key", "reviews", "policy")
            for arg in (f"--{name}", str(tmp_path / f"{name}.json"))
        ],
        "--expected-candidate",
        "a" * 40,
        "--report",
        str(tmp_path / "report.json"),
    ]
    assert main(args) == 0
    assert json.loads((tmp_path / "report.json").read_text())["promotion_eligible"]
    evidence[-1].approved_experts.clear()
    write_inputs(tmp_path, evidence)
    assert main(args) == 2
    args[args.index("--expected-candidate") + 1] = "b" * 40
    assert main(args) == 1


def test_packet_cli_writes_private_key_and_refuses_overwrite(tmp_path, evidence):
    write_inputs(tmp_path, evidence)
    target = tmp_path / "new-key.json"
    args = [
        "packet",
        "--benchmark",
        str(tmp_path / "benchmark.json"),
        "--run",
        str(tmp_path / "run.json"),
        "--key",
        str(target),
        "--packet",
        str(tmp_path / "packet.json"),
    ]
    assert main(args) == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert main(args) == 1
    Key.model_validate(read(target))


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"approved": false, "approved": true}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        read(path)


def test_author_alias_does_not_hide_self_review(evidence):
    policy = evidence[-1]
    policy.approved_experts["question-author"] = policy.approved_experts[
        "expert-one"
    ].model_copy()
    report = grade(evidence)
    assert report["status"] == "blocked"
    assert any("independence" in reason for reason in report["blocked_reasons"])


def test_expert_disagreement_with_tie_does_not_generate_a_gain(evidence):
    # Each commander's one control case gets a candidate vote and a tie.
    for review in evidence[4][::2]:
        review.preferred = "tie"
    assert grade(evidence)["status"] == "qualified_no_gain"


def test_source_reference_or_development_split_cannot_change_after_review(evidence):
    evidence[0].split = "development"
    with pytest.raises(ValueError, match="frozen benchmark"):
        grade(evidence)


def test_schema_export_and_checked_in_policy_have_no_fabricated_enrollment(tmp_path):
    from pathlib import Path

    path = tmp_path / "schemas.json"
    assert main(["schema", "--output", str(path)]) == 0
    assert set(read(path)) == {"benchmark", "run", "key", "reviews", "policy"}
    policy = Policy.model_validate(read(Path("config/assistant_expertise_policy.json")))
    assert policy.approved_experts == {}
    assert policy.approved_curators == policy.approved_benchmarks == []
    assert policy.min_cases == 120


def test_incomplete_review_set_is_not_counted_as_success(evidence):
    del evidence[4][-1]
    report = grade(evidence)
    assert report["status"] == "blocked"
    assert report["cases"] == 12
    assert report["case_results"][-1]["passed"] is False


def test_invalid_retry_invalidates_previous_success_report(tmp_path, evidence):
    write_inputs(tmp_path, evidence)
    output = tmp_path / "report.json"
    output.write_text('{"status":"qualified_improvement","promotion_eligible":true}')
    (tmp_path / "reviews.json").write_text("not JSON")
    args = [
        "gate",
        *[
            arg
            for name in ("benchmark", "run", "key", "reviews", "policy")
            for arg in (f"--{name}", str(tmp_path / f"{name}.json"))
        ],
        "--expected-candidate",
        "a" * 40,
        "--report",
        str(output),
    ]
    assert main(args) == 1
    assert read(output)["promotion_eligible"] is False
    assert read(output)["status"] == "invalid"
