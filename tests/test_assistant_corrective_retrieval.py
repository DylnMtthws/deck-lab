import hashlib
import sqlite3

import pytest

from sabermetrics import assistant_corrective_retrieval as corrective
from sabermetrics.assistant_rules import RuleHit, RulesEvidence, ingest_rules


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def review(claims, *, category="rules_interaction"):
    parts = ["Answer", *claims]
    judgments = [
        {
            "claim_id": f"c{i}",
            "part_index": i + 1,
            "start": 0,
            "end": len(claim),
            "reviewed_claim": claim,
            "claim_sha256": digest(claim),
            "part_sha256": digest(claim),
            "category": category,
            "verdict": "unverified",
            "reason": "Missing ruling",
        }
        for i, claim in enumerate(claims)
    ]
    return parts, {
        "claim_unit_version": "assistant-claim-fragments.v1",
        "claim_judgments": judgments,
    }


def hit(text="400.7 A zone change makes a new object.", **kwargs):
    return RuleHit(
        kind="rule",
        source_id="CR 400.7",
        text=text,
        source_url="https://magic.wizards.com/rules",
        version="fixture",
        content_sha256=digest(text),
        source="Wizards snapshot",
        **kwargs,
    )


def invoke(parts, receipt, **kwargs):
    return corrective.corrective_evidence(
        "unused.sqlite",
        receipt,
        parts,
        kwargs.pop("existing_sources", []),
        kwargs.pop("cards", []),
        {},
        **kwargs,
    )


def test_missing_ruling_lookup_real_local_db(tmp_path):
    db = tmp_path / "rules.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE card_rulings (id TEXT, card_oracle_id TEXT, ruling_text TEXT, ruling_date TEXT, source TEXT)"
        )
        conn.execute(
            "INSERT INTO card_rulings VALUES ('1','widget','Widget returns without counters.','2026-01-01','https://magic.wizards.com/ruling')"
        )
    parts, receipt = review(["Widget returns without counters."])
    sources = ["Card: Widget\nOriginal Oracle text"]
    result = corrective.corrective_evidence(
        db, receipt, parts, sources, [{"name": "Widget", "oracle_id": "widget"}], {}
    )
    assert result["affected_claim_ids"] == ["c0"]
    assert result["additional_sources"][0].startswith("Rule source: ruling:1")
    assert result["additional_sources"][0].endswith("Widget returns without counters.")
    assert sources == ["Card: Widget\nOriginal Oracle text"]
    assert result["gaps"][0]["target"] == ["widget"]


@pytest.mark.parametrize(
    "claim,category",
    [
        ("Widget will survive until my next turn.", "rules_interaction"),
        ("Widget guarantees an infinite loop.", "rules_interaction"),
        ("Widget is the best strategy.", "strategic_judgment"),
        ("The future battlefield has two Widgets.", "rules_interaction"),
    ],
)
def test_future_unknown_and_strategy_skip(monkeypatch, claim, category):
    monkeypatch.setattr(
        corrective, "retrieve_rules", lambda *a, **k: pytest.fail("lookup")
    )
    parts, receipt = review([claim], category=category)
    result = invoke(parts, receipt)
    assert result["gaps"][0]["resolvable"] is False
    assert not result["additional_sources"] and not result["lookups"]


def test_two_query_budget_explicit_third_gap(monkeypatch):
    calls = []

    def retrieve(*args, **kwargs):
        calls.append(args[1])
        return RulesEvidence(status="no_matches", query=args[1])

    monkeypatch.setattr(corrective, "retrieve_rules", retrieve)
    parts, receipt = review(
        ["First counter ruling.", "Second tap ruling.", "Third sacrifice ruling."]
    )
    result = invoke(parts, receipt, max_queries=50)
    assert len(calls) == 2
    assert len(result["gaps"]) == 3
    assert result["gaps"][2]["status"] == "lookup_budget_omitted"
    assert result["stop_reason"] == "lookup_budget"


def test_repeated_evidence_stops_without_rereview(monkeypatch):
    record = hit()
    monkeypatch.setattr(
        corrective,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(status="available", query=a[1], hits=[record]),
    )
    parts, receipt = review(["A zone change removes counters."])
    result = invoke(
        parts, receipt, existing_sources=["Rule source: old\n" + record.text]
    )
    assert not result["affected_claim_ids"] and not result["additional_sources"]
    assert result["lookups"][0]["omitted"]["repeated_evidence"] == 1
    assert result["stop_reason"] == "no_new_complete_evidence"


def test_dedup_queries_and_new_hashes(monkeypatch):
    calls = []

    def retrieve(*a, **k):
        calls.append(a[1])
        return RulesEvidence(status="available", query=a[1], hits=[hit(), hit()])

    monkeypatch.setattr(corrective, "retrieve_rules", retrieve)
    parts, receipt = review(["Zone change.", "Zone change."])
    result = invoke(parts, receipt)
    assert len(calls) == len(result["additional_sources"]) == 1
    assert result["affected_claim_ids"] == ["c0", "c1"]
    again = invoke(parts, receipt, seen_queries=calls)
    assert not again["lookups"]
    assert all(g["status"] == "query_already_seen" for g in again["gaps"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("part_sha256", "bad"),
        ("claim_sha256", "bad"),
        ("start", 1),
        ("reviewed_claim", "different"),
    ],
)
def test_stale_exact_fragment_guards(monkeypatch, field, value):
    monkeypatch.setattr(
        corrective, "retrieve_rules", lambda *a, **k: pytest.fail("lookup")
    )
    parts, receipt = review(["A zone change."])
    receipt["claim_judgments"][0][field] = value
    assert invoke(parts, receipt)["gaps"][0]["status"] == "stale_receipt"


@pytest.mark.parametrize("when", [1, 3, 4, 5])
def test_cancellation_guard_exceptions_propagate(monkeypatch, when):
    class Cancelled(RuntimeError):
        pass

    count = 0

    def guard():
        nonlocal count
        count += 1
        if count == when:
            raise Cancelled()

    monkeypatch.setattr(
        corrective,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(status="no_matches", query=a[1]),
    )
    parts, receipt = review(["A zone change."])
    with pytest.raises(Cancelled):
        invoke(parts, receipt, check_current=guard)


def test_retrieval_errors_propagate(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("cancelled retrieval")

    monkeypatch.setattr(corrective, "retrieve_rules", fail)
    with pytest.raises(RuntimeError, match="cancelled retrieval"):
        invoke(*review(["A zone change."]))


def test_incomplete_unofficial_and_tampered_records_omitted(monkeypatch):
    records = [
        hit(text_truncated=True),
        hit().model_copy(update={"source_url": "https://example.com"}),
        hit().model_copy(update={"content_sha256": "bad"}),
    ]
    monkeypatch.setattr(
        corrective,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(status="available", query=a[1], hits=records),
    )
    result = invoke(*review(["A zone change."]))
    assert not result["additional_sources"]
    assert result["lookups"][0]["omitted"]["incomplete_or_unofficial"] == 3


def test_real_rule_snapshot_complete_record(tmp_path):
    db = tmp_path / "snapshot.sqlite"
    ingest_rules(
        db,
        "400.7 An object moving zones becomes a new object.\n401.1 A library is a zone.\n402.1 A hand is a zone.",
        source_url="https://magic.wizards.com/rules",
        version="synthetic",
    )
    parts, receipt = review(["Rule 400.7 establishes a new object."])
    result = corrective.corrective_evidence(db, receipt, parts, [], [], {})
    assert result["additional_sources"]
    assert (
        "400.7 An object moving zones becomes a new object."
        in result["additional_sources"][0]
    )


def test_missing_judgment_stays_explicit_without_search(monkeypatch):
    monkeypatch.setattr(
        corrective, "retrieve_rules", lambda *a, **k: pytest.fail("lookup")
    )
    parts, receipt = review([])
    receipt["missing_claim_ids"] = ["c0"]
    gap = invoke(parts, receipt)["gaps"][0]
    assert gap["claim_id"] == "c0" and gap["status"] == "missing_judgment"
    assert gap["evidence_type"] == "unknown" and not gap["resolvable"]


def test_zero_budget_records_all_omissions(monkeypatch):
    monkeypatch.setattr(
        corrective, "retrieve_rules", lambda *a, **k: pytest.fail("lookup")
    )
    result = invoke(*review(["Rule 122.2.", "Rule 400.7."]), max_queries=0)
    assert all(g["status"] == "lookup_budget_omitted" for g in result["gaps"])


@pytest.mark.parametrize(
    "claim",
    [
        "Widget will still be on the battlefield.",
        "Widget is alive next turn.",
        "Widget remains on the battlefield.",
    ],
)
def test_future_board_state_is_not_a_rule_lookup(monkeypatch, claim):
    monkeypatch.setattr(
        corrective, "retrieve_rules", lambda *a, **k: pytest.fail("lookup")
    )
    result = invoke(*review([claim]))
    assert not result["gaps"][0]["resolvable"]


def test_official_origin_ruling_only(monkeypatch):
    official = hit().model_copy(
        update={
            "kind": "card_ruling",
            "source_id": "ruling:1",
            "source_url": "https://data.scryfall.io/rulings.json",
            "source": "Scryfall bulk rulings fixture; original source labeled 'wotc' by Scryfall",
        }
    )
    community = official.model_copy(
        update={"source": "original source labeled 'scryfall' by Scryfall"}
    )
    monkeypatch.setattr(
        corrective,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(
            status="available", query=a[1], hits=[community, official]
        ),
    )
    result = invoke(*review(["A counter rule."]))
    assert len(result["additional_sources"]) == 1
    assert result["lookups"][0]["omitted"]["incomplete_or_unofficial"] == 1


def test_same_new_record_can_affect_two_different_claims_without_duplicate_append(
    monkeypatch,
):
    monkeypatch.setattr(
        corrective,
        "retrieve_rules",
        lambda *a, **k: RulesEvidence(status="available", query=a[1], hits=[hit()]),
    )
    result = invoke(
        *review(["Zone change removes counters.", "Returning creates a new object."])
    )
    assert result["affected_claim_ids"] == ["c0", "c1"]
    assert len(result["additional_sources"]) == 1
    assert result["lookups"][1]["new_complete_records"] == 0
    assert result["lookups"][1]["relevant_new_records"] == 1
