"""Offline tests for the W02 mechanic-stratified extraction benchmark."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_inference_extraction.py"
CASES = (
    ROOT
    / "docs/evaluations/research-viability-20261103/extraction-development-cases.json"
)
RESULTS = ROOT / "docs/evaluations/research-viability-20261103/extraction-results.json"

_spec = importlib.util.spec_from_file_location("benchmark_inference_extraction", SCRIPT)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def card(identity, text, type_line="Creature — Pirate"):
    return {
        "oracle_id": identity,
        "name": identity.title(),
        "type_line": type_line,
        "oracle_text": text,
    }


def label(lid, record, gold, *, v1="interpret", mechanisms=("test",), quote=None):
    text = record["oracle_text"]
    quote = quote or text
    start = text.index(quote)
    return {
        "label_id": lid,
        "source_ref": "card:" + record["oracle_id"],
        "record_sha256": _h(bench.canonical(record)),
        "text_sha256": _h(text),
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "mechanisms": list(mechanisms),
        "v1_expectation": v1,
        "gold": gold,
    }


def document(cards, labels, connections=None):
    # Deep copies: mutation tests must never edit the shared module records.
    case = {
        "case_id": "t",
        "cards": copy.deepcopy(cards),
        "rules": [],
        "labels": copy.deepcopy(labels),
    }
    if connections is not None:
        case["connections"] = connections
    return {
        "schema": bench.CASES_SCHEMA,
        "authorship": {
            "independent": False,
            "expert_reviewed": False,
            "split": "development",
        },
        "cases": [case],
    }


TAP = card("tap", "{T}: Draw a card.")
GRANT = card("grant", 'Creatures you control have "{T}: Draw a card."')


def test_committed_report_is_reproducible_and_disclaims_independence():
    assert bench.main(["--cases", str(CASES), "--check", str(RESULTS)]) == 0
    report = json.loads(RESULTS.read_text())
    claims = report["claims"]
    assert claims["independent_expert_labels"] is False
    assert claims["human_expert_review"] is False
    assert claims["live_inference_quality_measured"] is False
    assert claims["model_or_network_calls"] == 0
    assert claims["label_authorship"]["independent"] is False
    assert report["cases_sha256"] == _h(CASES.read_text())
    summary = report["summary"]
    # Every labelled clause stays in the denominator, including unknowns.
    assert summary["clauses"]["denominator"] == sum(
        len(c["labels"]) for c in report["cases"]
    )
    assert summary["clauses"]["status"]["preserved_unknown"] > 0
    assert summary["clauses"]["status"].get("dropped", 0) == 0


def test_abstention_is_unknown_not_correct_and_precision_is_null():
    doc = document(
        [GRANT], [label("g", GRANT, {"scope": "granted_activated"}, v1="abstain")]
    )
    summary = bench.run_benchmark(doc)["summary"]
    scope = summary["dimensions"]["scope"]
    assert (scope["tp"], scope["fp"], scope["unknown"], scope["gold"]) == (0, 0, 1, 1)
    assert scope["precision"] is None and scope["precision_denominator"] == 0
    assert scope["coverage"] == 0.0
    assert summary["clauses"]["status"] == {"preserved_unknown": 1}
    assert summary["mechanisms"]["test"]["labels"] == 1
    assert summary["mechanisms"]["test"]["preserved_unknown"] == 1


def test_wrong_value_counts_fp_and_fn_and_restrictions_are_claims():
    gold = {"scope": "activated", "quantity": ["draw:2"], "restrictions": ["only_once"]}
    summary = bench.run_benchmark(document([TAP], [label("t", TAP, gold)]))["summary"]
    for dimension in ("quantity", "restrictions"):
        counts = summary["dimensions"][dimension]
        assert (counts["tp"], counts["fp"], counts["fn"], counts["unknown"]) == (
            0,
            1,
            1,
            0,
        )
    assert summary["dimensions"]["scope"]["tp"] == 1
    wrong = {
        f["dimension"]
        for f in summary["findings_for_coordinator"]
        if f["kind"] == "wrong_extraction"
    }
    assert wrong == {"quantity", "restrictions"}


def test_unexpected_interpretation_and_expected_abstention_are_reported():
    doc = document(
        [TAP, GRANT],
        [
            label("t", TAP, {"scope": "activated"}, v1="abstain"),
            label("g", GRANT, {"scope": "granted_activated"}, v1="interpret"),
        ],
    )
    findings = bench.run_benchmark(doc)["summary"]["findings_for_coordinator"]
    kinds = sorted(f["kind"] for f in findings if f["kind"] != "unjudged_prediction")
    assert kinds == ["expected_supported_abstained", "unexpected_interpretation"]
    # The interpreted TAP clause emits dimensions its scope-only gold omits.
    assert {f["where"] for f in findings if f["kind"] == "unjudged_prediction"} == {
        "t/t"
    }


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda d: d["cases"][0]["labels"][0].update(quote="{T}: Draw two"),
            "quote does not equal",
        ),
        (lambda d: d["cases"][0]["labels"][0].update(start=1), "quote does not equal"),
        (
            lambda d: d["cases"][0]["cards"][0].update(
                oracle_text="{T}: Draw two cards."
            ),
            "source mismatch",
        ),
        (
            lambda d: d["cases"][0]["labels"][0].update(text_sha256="0" * 64),
            "text sha256 mismatch",
        ),
        (
            lambda d: d["cases"][0]["labels"][0]["gold"].update(vibes=["x"]),
            "unknown dimension",
        ),
        (
            lambda d: d["cases"][0]["labels"][0]["gold"].update(finite_fuel=False),
            "finite_fuel",
        ),
        (
            lambda d: d["cases"][0]["labels"][0].update(v1_expectation="maybe"),
            "v1_expectation",
        ),
        (lambda d: d["cases"][0]["labels"][0].pop("mechanisms"), "missing"),
        (lambda d: d["authorship"].update(independent=True), "authorship"),
        (
            lambda d: d["cases"][0]["labels"].append(
                copy.deepcopy(d["cases"][0]["labels"][0]) | {"label_id": "dup"}
            ),
            "overlapping",
        ),
    ],
)
def test_malformed_labels_and_source_mismatches_are_strict_errors(mutate, message):
    doc = document([TAP], [label("t", TAP, {"scope": "activated"})])
    mutate(doc)
    with pytest.raises(bench.BenchmarkError) as error:
        bench.run_benchmark(doc)
    assert any(message in e for e in error.value.errors)


def test_unlabelled_source_text_cannot_disappear():
    two = card("two", "{T}: Draw a card.\nDiscard a card: Draw a card.")
    doc = document(
        [two], [label("first", two, {"scope": "activated"}, quote="{T}: Draw a card.")]
    )
    with pytest.raises(bench.BenchmarkError, match="unlabelled source text"):
        bench.run_benchmark(doc)


def test_compiler_side_binding_mismatch_and_dropped_text(monkeypatch):
    real = bench.compile_deck_facts
    doc = document(
        [GRANT], [label("g", GRANT, {"scope": "granted_activated"}, v1="abstain")]
    )

    def rehashed(cards, rules=None):
        bundle = real(cards, rules)
        bundle["sources"][0]["source_sha256"] = "f" * 64
        return bundle

    monkeypatch.setattr(bench, "compile_deck_facts", rehashed)
    with pytest.raises(bench.BenchmarkError, match="compiler source binding"):
        bench.run_benchmark(doc)

    def truncated(cards, rules=None):
        bundle = real(cards, rules)
        bundle["sources"][0]["oracle_text"] = ""
        bundle["sources"][0]["clauses"][0]["text"] = ""
        return bundle

    monkeypatch.setattr(bench, "compile_deck_facts", truncated)
    summary = bench.run_benchmark(doc)["summary"]
    assert summary["clauses"]["status"] == {"dropped": 1}
    assert summary["dimensions"]["scope"]["gold"] == 1
    assert {"kind": "dropped", "where": "t/g"} in summary["findings_for_coordinator"]


def test_search_links_score_expected_and_forbidden_pairs():
    payoff = card(
        "payoff",
        "Whenever you discard a card, this creature deals 1 damage to each opponent.",
    )
    echo = card(
        "echo",
        "Whenever one or more Pirates you control deal damage to a player, draw a card.",
    )
    combat = card(
        "combat",
        "Whenever one or more Pirates you control deal combat damage to a player, draw a card.",
    )
    connections = {
        "expected": [
            {"from": "card:payoff", "to": "card:combat", "join_kind": "event"}
        ],
        "forbidden": [{"from": "card:payoff", "to": "card:echo", "join_kind": "event"}],
        "expect_cycle": False,
    }
    labels = [
        label(c["oracle_id"], c, {"scope": "triggered"}) for c in (payoff, echo, combat)
    ]
    summary = bench.run_benchmark(
        document([payoff, echo, combat], labels, connections)
    )["summary"]
    links = summary["search_links"]
    assert (links["tp"], links["fn"], links["fp"]) == (0, 1, 1)
    assert links["precision_over_labelled"] == 0.0
    kinds = {f["kind"] for f in summary["findings_for_coordinator"]}
    assert {"expected_connection_missing", "forbidden_connection_found"} <= kinds


# --------------------------------------------------------------------------
# Scoring v2: output events and unjudged predictions (review R1, P1)
# --------------------------------------------------------------------------

TAP_GOLD = {
    "scope": "activated",
    "actor": ["you"],
    "costs": [{"kind": "printed_symbols", "text": "{T}"}],
    "quantity": ["draw:1"],
    "restrictions": [],
    "zone_moves": ["draw:library->hand"],
    "output_events": ["draw:card"],
}


def _patched(monkeypatch, mutate_fact):
    real = bench.compile_deck_facts

    def compiled(cards, rules=None):
        bundle = copy.deepcopy(real(cards, rules))
        for fact in bundle["facts"]:
            mutate_fact(fact)
        return bundle

    monkeypatch.setattr(bench, "compile_deck_facts", compiled)


def _tap_label(summary_or_report):
    return summary_or_report["cases"][0]["labels"][0]


def test_complete_tap_gold_is_all_scored_correct_baseline():
    report = bench.run_benchmark(document([TAP], [label("t", TAP, TAP_GOLD)]))
    row = _tap_label(report)
    assert row["all_scored_dimensions_correct"] is True and row["unjudged"] == {}
    assert report["summary"]["all_dimensions"]["fp"] == 0


def _replace_explore(fact):
    for row in fact["outputs"]:
        if row["event"] == "explore":
            row["event"] = "invented_event"


def test_reviewer_explore_corruption_now_changes_scores(monkeypatch):
    document_ = json.loads(CASES.read_text())
    before = bench.run_benchmark(copy.deepcopy(document_))["summary"]
    _patched(monkeypatch, _replace_explore)
    after = bench.run_benchmark(copy.deepcopy(document_))["summary"]
    events_before = before["dimensions"]["output_events"]
    events_after = after["dimensions"]["output_events"]
    assert events_after["tp"] == events_before["tp"] - 2
    assert events_after["fp"] == events_before["fp"] + 2
    assert after["all_dimensions"]["precision"] < before["all_dimensions"]["precision"]
    wrong = {
        f["where"]
        for f in after["findings_for_coordinator"]
        if f["kind"] == "wrong_extraction" and f["dimension"] == "output_events"
    } - {
        f["where"]
        for f in before["findings_for_coordinator"]
        if f["kind"] == "wrong_extraction" and f["dimension"] == "output_events"
    }
    assert wrong == {
        "conflicting_rules_and_sources/explore_trigger",
        "finite_fuel_cycle/cycle_conversion",
    }


@pytest.mark.parametrize(
    "mutate_fact, dimensions",
    [
        # wrong event name
        (
            lambda f: f["outputs"][0].update(event="invented_event"),
            {"output_events", "quantity", "zone_moves"},
        ),
        # missing output event
        (
            lambda f: f.update(outputs=[]),
            {"output_events", "actor", "quantity", "zone_moves"},
        ),
        # extra output event with the same actor
        (
            lambda f: f["outputs"].append(
                {"event": "discard", "actor": "you", "subject": "card"}
            ),
            {"output_events"},
        ),
        # actor difference only
        (lambda f: f["outputs"][0].update(actor="each player"), {"actor"}),
    ],
    ids=["wrong_event", "missing_event", "extra_event", "actor_difference"],
)
def test_output_event_and_actor_mutations_score_wrong(
    monkeypatch, mutate_fact, dimensions
):
    _patched(monkeypatch, mutate_fact)
    report = bench.run_benchmark(document([TAP], [label("t", TAP, TAP_GOLD)]))
    row = _tap_label(report)
    wrong = {d for d, v in row["dimensions"].items() if v["outcome"] == "wrong"}
    assert wrong == dimensions
    assert row["all_scored_dimensions_correct"] is False
    totals = report["summary"]["all_dimensions"]
    assert totals["fp"] == totals["fn"] == len(dimensions)
    assert totals["precision"] < 1.0


def test_extracted_dimension_without_gold_is_unjudged_not_correct():
    partial = {"scope": "activated", "quantity": ["draw:1"]}
    report = bench.run_benchmark(document([TAP], [label("t", TAP, partial)]))
    row = _tap_label(report)
    assert row["all_scored_dimensions_correct"] is False
    assert set(row["unjudged"]) == {
        "actor",
        "costs",
        "output_events",
        "restrictions",
        "zone_moves",
    }
    assert {u["reason"] for u in row["unjudged"].values()} == {"no_gold_label"}
    summary = report["summary"]
    # Unjudged predictions never enter tp/fp or the gold denominator.
    assert summary["all_dimensions"]["gold"] == 2
    assert summary["all_dimensions"]["tp"] == 2
    assert summary["unjudged"]["predictions"] == 5
    assert summary["clauses"]["all_scored_dimensions_correct"] == 0
    assert summary["mechanisms"]["test"]["unjudged_predictions"] == 5
    kinds = [f["kind"] for f in summary["findings_for_coordinator"]]
    assert kinds.count("unjudged_prediction") == 5


def test_explicit_null_gold_is_unjudged_and_blocks_scored_verdict():
    gold = dict(TAP_GOLD, actor=None)
    report = bench.run_benchmark(document([TAP], [label("t", TAP, gold)]))
    row = _tap_label(report)
    assert "actor" not in row["dimensions"]
    assert row["unjudged"] == {
        "actor": {"reason": "gold_explicitly_null", "extracted": ["you"]}
    }
    assert row["all_scored_dimensions_correct"] is False
    assert report["summary"]["dimensions"]["actor"]["gold"] == 0


def test_abstained_output_events_stay_unknown_in_denominator():
    gold = {"scope": "granted_activated", "output_events": ["draw:card"]}
    summary = bench.run_benchmark(
        document([GRANT], [label("g", GRANT, gold, v1="abstain")])
    )["summary"]
    events = summary["dimensions"]["output_events"]
    assert (events["tp"], events["fp"], events["unknown"], events["gold"]) == (
        0,
        0,
        1,
        1,
    )
    assert events["precision"] is None and events["coverage"] == 0.0
    assert summary["unjudged"]["predictions"] == 0


def test_unread_fact_fields_are_disclosed_per_label():
    payoff = card(
        "payoff",
        "Whenever one or more Pirates you control deal damage to a player, draw a card.",
    )
    report = bench.run_benchmark(
        document([payoff], [label("p", payoff, {"scope": "triggered"})])
    )
    fields = _tap_label(report)["unjudged_fact_fields"]["fields"]
    assert "inputs[].recipient" in fields and "required_rules" in fields


def test_committed_report_discloses_scoring_version_change():
    report = json.loads(RESULTS.read_text())
    assert report["scoring_version"] == 2
    (v1,) = report["scoring_history"]
    assert v1["status"] == "superseded_invalid_as_complete_precision_claim"
    assert (v1["totals"]["tp"], v1["totals"]["fp"], v1["totals"]["unknown"]) == (
        139,
        0,
        106,
    )
    assert (
        "not a compiler change"
        in report["claims"]["scoring_change_not_compiler_change"]
    )
    assert report["compiler_version"] == v1["compiler_version"]
    assert report["summary"]["all_dimensions"]["gold"] > v1["totals"]["gold_values"]
    # Raw v1 claim preserved verbatim, with an explicit correction note.
    assert v1["totals"]["fully_correct"] == 16
    assert "overstated scope" in v1["field_correction"]


# --------------------------------------------------------------------------
# Unread fact fields never earn full semantic correctness (review R2, P2-1)
# --------------------------------------------------------------------------


def _corrupt_unread(fact):
    for row in fact["outputs"]:
        row["condition"] = "only if an impossible invented condition holds"
        row["optional"] = True
    fact["required_rules"] = ["invented-rule-999"]


def test_unread_field_corruption_cannot_change_scores_or_claim_semantics(
    monkeypatch,
):
    document_ = json.loads(CASES.read_text())
    before = bench.run_benchmark(copy.deepcopy(document_))["summary"]
    _patched(monkeypatch, _corrupt_unread)
    after = bench.run_benchmark(copy.deepcopy(document_))["summary"]
    # The scored verdict is blind to unread fields by construction ...
    assert after["all_dimensions"] == before["all_dimensions"]
    clauses = after["clauses"]
    assert (
        clauses["all_scored_dimensions_correct"]
        == before["clauses"]["all_scored_dimensions_correct"]
    )
    # ... so it is named and disclosed as partial, never full correctness.
    assert clauses["full_clause_semantic_correctness_judged"] is False
    assert (
        clauses["all_scored_dimensions_correct_with_unread_fact_content"]
        == clauses["all_scored_dimensions_correct"]
    )
    assert after["unjudged"]["fact_fields_on_interpreted_labels"]


def test_scored_verdict_flags_unread_content_on_single_label(monkeypatch):
    _patched(monkeypatch, _corrupt_unread)
    report = bench.run_benchmark(document([TAP], [label("t", TAP, TAP_GOLD)]))
    row = _tap_label(report)
    assert row["all_scored_dimensions_correct"] is True
    assert row["unread_fact_content"] is True
    assert {"outputs[].condition", "outputs[].optional", "required_rules"} <= set(
        row["unjudged_fact_fields"]["fields"]
    )
    assert "fully_correct" not in row


def test_no_full_correctness_field_in_cli_report_or_docs():
    committed = RESULTS.read_text()
    report = json.loads(committed)
    assert report["claims"]["full_clause_semantic_correctness_judged"] is False
    assert "partial verdict" in report["scoring"]["all_scored_dimensions_correct"]
    # Only the preserved v1 history may mention the retired field name.
    history = json.dumps(report.pop("scoring_history"))
    assert "fully_correct" in history
    assert "fully_correct" not in json.dumps(report)
    assert "fully_correct" not in CASES.read_text()
    source = SCRIPT.read_text()
    assert '["fully_correct"]' not in source
    assert "PARTIAL verdict" in bench.__doc__


# --------------------------------------------------------------------------
# Malformed containers are BenchmarkError, never TypeError (review R1, P2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["cases"][0].update(labels=None), "labels must be"),
        (lambda d: d["cases"][0].update(labels=[]), "labels must be"),
        (lambda d: d["cases"][0].update(labels={"t": {}}), "labels must be"),
        (lambda d: d["cases"][0]["labels"].append(None), "invalid or duplicate"),
        (lambda d: d.update(authorship=None), "authorship"),
        (lambda d: d.update(authorship=[]), "authorship"),
        (lambda d: d.pop("authorship"), "authorship"),
        (lambda d: d.update(cases=None), "cases must be"),
        (lambda d: d["cases"].append(None), "case_id"),
        (lambda d: d["cases"][0].update(cards=None), "cards must be a list"),
        (lambda d: d["cases"][0].update(rules={}), "rules must be a list"),
        (lambda d: d["cases"][0]["labels"][0].update(gold=None), "wrong field types"),
        (lambda d: d["cases"][0]["labels"][0].update(gold=[]), "wrong field types"),
        (
            lambda d: d["cases"][0]["labels"][0].update(source_ref=None),
            "wrong field types",
        ),
        (
            lambda d: d["cases"][0]["labels"][0].update(record_sha256=["x"]),
            "wrong field types",
        ),
        (lambda d: d["cases"][0]["labels"][0].update(quote=7), "wrong field types"),
        (
            lambda d: d["cases"][0]["labels"][0].update(mechanisms="test"),
            "wrong field types",
        ),
        (lambda d: d["cases"][0].update(connections=[]), "connections must be"),
        (
            lambda d: d["cases"][0].update(connections={"expected": None}),
            "expected connections must be a list",
        ),
        (
            lambda d: d["cases"][0].update(connections={"dependency_gaps": "explore"}),
            "dependency_gaps",
        ),
        (lambda d: d["cases"][0]["labels"][0]["gold"].update(scope=None), "scope"),
        *[
            (
                lambda d, g=group, k=key, v=value: d["cases"][0].update(
                    connections={
                        g: [
                            {
                                "from": "card:tap",
                                "to": "card:tap",
                                "join_kind": "event",
                                k: v,
                            }
                        ]
                    }
                ),
                f"malformed {group} connection",
            )
            for group in ("expected", "forbidden")
            for key in ("from", "to", "join_kind")
            for value in (None, [], {}, ["card:tap"], 7)
        ],
        (
            lambda d: d["cases"][0].update(connections={"forbidden": [["x"]]}),
            "malformed forbidden connection",
        ),
    ],
)
def test_malformed_containers_are_benchmark_errors(mutate, message):
    doc = document([TAP], [label("t", TAP, {"scope": "activated"})])
    mutate(doc)
    with pytest.raises(bench.BenchmarkError) as error:
        bench.run_benchmark(doc)
    assert any(message in e for e in error.value.errors), error.value.errors
