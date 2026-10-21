"""Engine preparation is bounded source retrieval, never rules inference."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics import assistant_engine_review as module
from sabermetrics.assistant_rules import ingest_rules

URL = "https://media.wizards.com/fixture-rules.txt"
SNAPSHOT = dict(source_url=URL, source_version="fixture", content_sha256="snapshot")
EXPLORE = (
    "701.40a Certain abilities instruct a permanent to explore. To have a "
    "permanent explore, its controller reveals the top card of their library. "
    "If it is a land card, they put it into their hand. Otherwise, they put "
    "a +1/+1 counter on the permanent, then may put the revealed card into "
    "their graveyard."
)


def hit(text=EXPLORE, source_id="CR 701.40a", **kwargs):
    return dict(
        kind="rule",
        source_id=source_id,
        text=text,
        text_truncated=False,
        source_url=URL,
        version="fixture",
        source="Wizards text snapshot",
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        card_oracle_id=None,
        **kwargs,
    )


def card(text, *, commander=False, identity="card"):
    return dict(
        name=identity, oracle_id=identity, oracle_text=text, is_commander=commander
    )


def result(hits=(), **kwargs):
    data = dict(
        hits=list(hits),
        status="available" if hits else "no_matches",
        rulings_status="no_matches",
        rules_snapshot=SNAPSHOT,
        limitations=[],
        query_failures=[],
    )
    data.update(kwargs)
    return SimpleNamespace(model_dump=lambda: data)


@pytest.fixture
def lookup(monkeypatch):
    calls = []

    def retrieve(path, query, **kwargs):
        calls.append((query, kwargs))
        return result([hit()] if query == "explore" else [])

    monkeypatch.setattr(module, "retrieve_rules", retrieve)
    return calls


def run(tmp_path, cards, existing=None, **kwargs):
    return module.engine_rule_evidence(
        tmp_path / "rules.db",
        cards,
        existing if existing is not None else {"hits": [], "status": "no_matches"},
        **kwargs,
    )


def test_missing_explore_text_is_retrieved_before_return(tmp_path, lookup):
    rules, receipt = run(
        tmp_path, [card("Whenever a creature enters, it explores.", commander=True)]
    )
    assert [q for q, _ in lookup] == ["explore"]
    assert rules["hits"][0]["text"] == EXPLORE
    assert receipt["sources"][0]["content_sha256"] == hit()["content_sha256"]
    assert receipt["lookup_count"] == 1
    dependency = receipt["evidence_dependencies"]["actions"][0]
    assert dependency["term"] == "explore"
    assert dependency["commander"] is True
    assert dependency["status"] == "candidate_evidence"
    assert dependency["semantics_verified"] is False
    assert receipt["queries"][0]["elapsed_seconds"] >= 0
    assert receipt["rules_snapshots"] == [SNAPSHOT]


def test_commander_and_printed_order_precede_remaining_cards(tmp_path, lookup):
    support = card("Surveil 1. Discover 4.", identity="support")
    commander = card("This creature connives, then explores.", commander=True)
    rules, receipt = run(tmp_path, [support, commander])
    assert [q for q, _ in lookup] == ["connive", "explore"]
    assert all(kwargs["cards"] == [commander] for _, kwargs in lookup)
    assert all(kwargs["deadline_seconds"] == 0.35 for _, kwargs in lookup)
    assert receipt["queries_omitted"] == 2
    dependencies = rules["engine_evidence_dependencies"]["actions"]
    assert [d["term"] for d in dependencies] == [
        "connive",
        "explore",
        "surveil",
        "discover",
    ]
    assert dependencies[2]["status"] == "unknown"
    assert dependencies[2]["reason"] == "lookup_budget_omitted"


@pytest.mark.parametrize(
    "text",
    [
        "Draw a card. Sacrifice a creature: Add {G}.",
        "Whenever this attacks, put a counter on it.",
    ],
)
def test_generic_text_does_not_trigger_caution_queries(tmp_path, lookup, text):
    rules, receipt = run(tmp_path, [dict(card(text), tags=["explore"], role="mill")])
    assert not lookup
    assert not rules["engine_evidence_dependencies"]["actions"]
    assert receipt["lookup_count"] == 0


def test_card_name_is_not_an_action_obligation(tmp_path, lookup):
    run(tmp_path, [dict(card("Mill Watcher gets +1/+1.", identity="Mill Watcher"))])
    assert not lookup


@pytest.mark.parametrize(
    "term,text",
    [
        ("explore", "It explores."),
        ("connive", "It connives."),
        ("dredge", "Dredge 3"),
        ("surveil", "Surveil 2."),
        ("discover", "Discover 4."),
        ("proliferate", "Proliferate."),
        ("amass", "Amass Orcs 1."),
        ("incubate", "Incubate 2."),
        ("mill", "Each player mills two cards."),
    ],
)
def test_printed_action_families_are_literal_queries(tmp_path, lookup, term, text):
    run(tmp_path, [card(text)])
    assert lookup[0][0] == term


def test_faces_and_deep_remaining_cards_are_scanned(tmp_path, lookup):
    cards = [card("Draw a card.", identity=str(i)) for i in range(30)]
    cards.append(
        dict(
            name="front // back",
            oracle_id="face",
            card_faces=[
                dict(name="front", oracle_text="Surveil 1."),
                dict(name="back", oracle_text="Explore."),
            ],
        )
    )
    _, receipt = run(tmp_path, cards)
    assert [q for q, _ in lookup] == ["surveil", "explore"]
    assert (
        receipt["evidence_dependencies"]["supported_vocabulary_scan_complete"] is True
    )
    assert receipt["evidence_dependencies"]["printed_scope_complete"] is False


def test_existing_full_sources_skip_lookup_merge_and_dedup_stably(tmp_path, lookup):
    old = hit("400.7 An object changing zones is a new object.", "CR 400.7")
    existing = dict(hits=[old, hit(), old], rules_snapshot=SNAPSHOT, status="available")
    original = deepcopy(existing)
    rules, receipt = run(tmp_path, [card("Explore. Mill two cards.")], existing)
    assert [q for q, _ in lookup] == ["mill"]
    assert rules["hits"] == [hit(), old]
    assert receipt["hits_omitted"] == 0
    assert existing == original


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"text_truncated": True}, "truncated_or_unspecified_completeness"),
        ({"text_truncated": None}, "truncated_or_unspecified_completeness"),
        ({"content_sha256": "bad"}, "content_hash_mismatch"),
        ({"source_url": "https://example.test/rules"}, "unofficial_rule_source"),
        (
            {"source_url": "https://media.wizards.com:bad/rules"},
            "unofficial_rule_source",
        ),
        ({"version": ""}, "missing_identity_or_provenance"),
    ],
)
def test_rejected_sources_never_enter_review(tmp_path, monkeypatch, change, reason):
    rejected = dict(hit(), **change)
    monkeypatch.setattr(module, "retrieve_rules", lambda *a, **k: result([rejected]))
    rules, receipt = run(tmp_path, [card("Explore.")], dict(hits=[rejected]))
    assert rules["hits"] == []
    assert all(o["reason"] == reason for o in receipt["omissions"])
    assert receipt["evidence_dependencies"]["actions"][0]["status"] == "unknown"
    assert (
        receipt["evidence_dependencies"]["actions"][0]["reason"]
        == "no_retained_complete_matching_rule"
    )


def test_truncated_existing_is_replaced_by_complete_retrieval(tmp_path, lookup):
    truncated = dict(hit(), text=EXPLORE[:50], text_truncated=True)
    rules, receipt = run(tmp_path, [card("Explore.")], dict(hits=[truncated]))
    assert rules["hits"] == [hit()]
    assert receipt["hits_omitted"] == 1
    assert len(lookup) == 1


def test_ruling_keeps_identity_but_cannot_fill_general_action_gap(tmp_path, lookup):
    ruling = dict(
        hit(), kind="card_ruling", card_oracle_id="card", source_id="ruling:3"
    )
    rules, receipt = run(tmp_path, [card("Explore.")], dict(hits=[ruling]))
    assert len(lookup) == 1
    assert rules["hits"] == [hit(), ruling]
    assert receipt["sources"][1]["card_oracle_id"] == "card"
    assert len(receipt["evidence_dependencies"]["actions"][0]["sources"]) == 1


@pytest.mark.parametrize("text", ["701.40 Explore", "701.40. Explore."])
def test_heading_does_not_satisfy_action_obligation(tmp_path, lookup, text):
    heading = hit(text, "CR 701.40")
    _, receipt = run(tmp_path, [card("Explore.")], dict(hits=[heading]))
    assert len(lookup) == 1
    assert len(receipt["evidence_dependencies"]["actions"][0]["sources"]) == 1


def test_term_match_does_not_claim_complete_semantics(tmp_path, lookup):
    incidental = hit(
        "701.40b If a permanent explores, its controller performs the action."
    )
    _, receipt = run(
        tmp_path, [card("Explore.")], dict(hits=[incidental], rules_snapshot=SNAPSHOT)
    )
    assert not lookup
    assert receipt["evidence_dependencies"]["semantics_verified"] is False
    assert (
        receipt["evidence_dependencies"]["actions"][0]["reason"]
        == "full_text_term_match_only"
    )
    assert "does not prove entailment" in receipt["scope"]


def test_mixed_snapshot_sources_are_withheld(tmp_path, lookup):
    stale = dict(hit("400.7 Old zone rule.", "CR 400.7"), version="old")
    old_snapshot = dict(SNAPSHOT, source_version="old", content_sha256="old")
    rules, receipt = run(
        tmp_path, [card("Explore.")], dict(hits=[stale], rules_snapshot=old_snapshot)
    )
    assert rules["hits"] == [hit()]
    assert rules["rules_snapshot"] == SNAPSHOT
    assert receipt["omissions"][0]["reason"] == "incompatible_rules_snapshot"
    assert receipt["rules_snapshots"] == [old_snapshot, SNAPSHOT]


def test_snapshot_change_reconsiders_previously_satisfied_obligation(
    tmp_path, monkeypatch
):
    old = dict(hit(), version="old")
    old_snapshot = dict(SNAPSHOT, source_version="old", content_sha256="old")
    calls = []

    def retrieve(path, query, **kwargs):
        calls.append(query)
        return result([hit()] if query == "explore" else [])

    monkeypatch.setattr(module, "retrieve_rules", retrieve)
    rules, receipt = run(
        tmp_path,
        [card("Explore. Surveil 1.")],
        dict(hits=[old], rules_snapshot=old_snapshot),
    )
    assert calls == ["surveil", "explore"]
    assert rules["hits"] == [hit()]
    assert receipt["hits_omitted"] == 1


def test_fresh_same_text_can_replace_old_incompatible_snapshot(tmp_path, monkeypatch):
    old_snapshot = dict(SNAPSHOT, content_sha256="old-snapshot")
    calls = []

    def retrieve(path, query, **kwargs):
        calls.append(query)
        return result([hit()])

    monkeypatch.setattr(module, "retrieve_rules", retrieve)
    rules, receipt = run(
        tmp_path,
        [card("Explore. Surveil 1.")],
        dict(hits=[hit()], rules_snapshot=old_snapshot),
    )
    assert calls == ["surveil"]
    assert rules["hits"] == [hit()]
    assert receipt["omissions"][0]["reason"] == "incompatible_rules_snapshot"


def test_matching_existing_rule_from_wrong_snapshot_cannot_skip_lookup(
    tmp_path, lookup
):
    old_snapshot = dict(SNAPSHOT, content_sha256="different-snapshot")
    old = dict(hit(), version="old")
    rules, _ = run(
        tmp_path, [card("Explore.")], dict(hits=[old], rules_snapshot=old_snapshot)
    )
    assert len(lookup) == 1
    assert rules["hits"] == [hit()]


@pytest.mark.parametrize(
    "snapshot",
    [None, {}, {"source_url": URL}, dict(SNAPSHOT, content_sha256="")],
)
def test_missing_snapshot_identity_cannot_mix_existing_versions(
    tmp_path, lookup, snapshot
):
    stale = dict(hit("701.42a To surveil, look at cards.", "CR 701.42a"), version="old")
    existing = dict(hits=[hit(), stale], rules_snapshot=snapshot)
    rules, receipt = run(tmp_path, [card("Explore. Surveil 1.")], existing)
    assert [q for q, _ in lookup] == ["explore", "surveil"]
    assert rules["hits"] == [hit()]
    assert rules["rules_snapshot"] == SNAPSHOT
    assert len(receipt["omissions"]) == 2
    assert all(
        o["reason"] == "incompatible_rules_snapshot" for o in receipt["omissions"]
    )
    assert receipt["evidence_dependencies"]["all_terms_have_candidate_sources"] is False


def test_fresh_hits_without_snapshot_metadata_remain_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module, "retrieve_rules", lambda *a, **k: result([hit()], rules_snapshot=None)
    )
    rules, receipt = run(tmp_path, [card("Explore.")])
    assert rules["hits"] == []
    assert receipt["evidence_dependencies"]["actions"][0]["status"] == "unknown"
    assert receipt["omissions"][0]["reason"] == "incompatible_rules_snapshot"


@pytest.mark.parametrize(
    "text", ["Goad target creature.", "Detain it.", "You may exert it."]
)
def test_unsupported_mechanics_never_claim_complete_printed_coverage(
    tmp_path, lookup, text
):
    _, receipt = run(tmp_path, [card(text)])
    assert not lookup
    dependencies = receipt["evidence_dependencies"]
    assert dependencies["actions"] == []
    assert dependencies["coverage_scope"] == "supported_action_vocabulary_only"
    assert dependencies["supported_action_vocabulary"] == list(module._ACTIONS)
    assert dependencies["supported_vocabulary_scan_complete"] is True
    assert dependencies["printed_scope_complete"] is False
    assert dependencies["all_terms_have_candidate_sources"] is False
    assert dependencies["potential_unsupported_mechanics"] == "unknown"
    assert dependencies["semantics_verified"] is False


def test_supported_sources_do_not_certify_other_printed_mechanics(tmp_path, lookup):
    _, receipt = run(tmp_path, [card("Explore. Goad target creature.")])
    dependencies = receipt["evidence_dependencies"]
    assert dependencies["all_terms_have_candidate_sources"] is True
    assert dependencies["coverage_scope"] == "supported_action_vocabulary_only"
    assert dependencies["printed_scope_complete"] is False
    assert dependencies["potential_unsupported_mechanics"] == "unknown"


def test_unrelated_existing_ruling_is_withheld(tmp_path, lookup):
    ruling = dict(
        hit(), kind="card_ruling", card_oracle_id="other", source_id="ruling:3"
    )
    rules, receipt = run(tmp_path, [card("Explore.")], dict(hits=[ruling]))
    assert rules["hits"] == [hit()]
    assert receipt["omissions"][0]["reason"] == "ruling_outside_supplied_cards"


def test_missing_face_oracle_makes_printed_scope_incomplete(tmp_path, lookup):
    cards = [
        dict(
            name="front // back",
            card_faces=[
                dict(name="front", oracle_text="Explore."),
                dict(name="back"),
            ],
        )
    ]
    _, receipt = run(tmp_path, cards)
    assert receipt["evidence_dependencies"]["printed_scope_complete"] is False


def test_overlong_existing_source_reports_source_budget_not_lookup_budget(
    tmp_path, lookup
):
    enormous = hit(EXPLORE + " " + "x" * 12_000)
    rules, receipt = run(
        tmp_path, [card("Explore.")], dict(hits=[enormous], rules_snapshot=SNAPSHOT)
    )
    assert not lookup
    assert not rules["hits"]
    assert receipt["queries_omitted"] == 0
    assert receipt["evidence_dependencies"]["actions"][0]["status"] == "unknown"
    assert (
        receipt["evidence_dependencies"]["actions"][0]["reason"]
        == "total_source_budget"
    )


def test_hit_and_character_caps_apply_to_existing_and_new(tmp_path, lookup):
    old = [
        hit(f"400.{i} A complete source. " + "x" * 1600, f"CR 400.{i}")
        for i in range(12)
    ]
    rules, receipt = run(
        tmp_path,
        [card("Explore.")],
        dict(hits=old, hits_omitted=2, rules_snapshot=SNAPSHOT),
    )
    assert rules["hits"][0] == hit()
    assert len(rules["hits"]) <= 8
    assert sum(len(h["text"]) for h in rules["hits"]) <= 12_000
    assert all(h["text_truncated"] is False for h in rules["hits"])
    assert receipt["hits_omitted"] == 2 + 13 - len(rules["hits"])
    assert all(o["reason"] == "total_source_budget" for o in receipt["omissions"])


def test_small_hit_cap_preserves_commander_sources_first(tmp_path, lookup):
    old = [hit(f"400.{i} Zone rule.", f"CR 400.{i}") for i in range(12)]
    rules, _ = run(
        tmp_path,
        [card("Explore.", commander=True)],
        dict(hits=old, rules_snapshot=SNAPSHOT),
    )
    assert len(rules["hits"]) == 8
    assert rules["hits"] == [hit(), *old[:7]]


def test_scope_is_bounded_to_sixteen_actual_action_cards(tmp_path, lookup):
    cards = [card("Explore.", identity=str(i)) for i in range(30)]
    _, receipt = run(tmp_path, cards)
    assert len(lookup[0][1]["cards"]) == 16
    assert receipt["queries"][0]["cards_omitted"] == 14


def test_missing_and_truncated_oracle_are_explicit(tmp_path, lookup):
    _, receipt = run(
        tmp_path, [dict(card("Explore."), oracle_text_truncated=True), card("")]
    )
    assert receipt["evidence_dependencies"]["printed_scope_complete"] is False
    assert len(receipt["evidence_dependencies"]["oracle_text_incomplete"]) == 2


def test_missing_database_remains_unknown(tmp_path):
    rules, receipt = run(tmp_path, [card("Explore.")])
    assert rules["hits"] == []
    assert receipt["status"] == "unavailable"
    assert receipt["evidence_dependencies"]["actions"][0]["status"] == "unknown"


def test_lookup_failure_receipt_does_not_expose_raw_exception(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private filesystem failure")

    monkeypatch.setattr(module, "retrieve_rules", fail)
    _, receipt = run(tmp_path, [card("Explore.")])
    assert receipt["queries"][0]["query_failures"][0]["exception_type"] == "OSError"
    assert "private filesystem" not in str(receipt)


@pytest.mark.parametrize("cancel_call", [1, 2, 3, 4])
def test_cancellation_propagates_at_each_boundary(tmp_path, lookup, cancel_call):
    count = 0

    class Cancelled(Exception):
        pass

    def check():
        nonlocal count
        count += 1
        if count == cancel_call:
            raise Cancelled("cancel")

    with pytest.raises(Cancelled):
        run(tmp_path, [card("Explore.")], check_current=check)
    assert len(lookup) == (cancel_call >= 3)


def test_local_snapshot_integration_fetches_complete_explore_text(tmp_path):
    path = tmp_path / "rules.db"
    snapshot = ingest_rules(
        path,
        "100.1. Fixture rule.\n701.40 Explore\n" + EXPLORE,
        source_url=URL,
        version="fixture",
    )
    # Exercise the production reader and metadata shape, not a catalog without
    # rules tables or a synthetic RulesEvidence response.
    canonical = module.retrieve_rules(path, "explore", limit=6).model_dump()
    assert canonical["rules_snapshot"] == snapshot
    assert any(h["source_id"] == "CR 701.40a" for h in canonical["hits"])
    rules, receipt = module.engine_rule_evidence(
        path,
        [card("Whenever a creature enters, it explores.", commander=True)],
        {"hits": [], "status": "unavailable"},
    )
    assert EXPLORE in [h["text"] for h in rules["hits"]]
    assert receipt["sources"][0]["source_url"] == URL
    assert receipt["queries"][0]["status"] == "available"
    assert rules["rules_snapshot"] == snapshot
    assert all(h["version"] == snapshot["source_version"] for h in rules["hits"])
    assert receipt["evidence_dependencies"]["semantics_verified"] is False


@pytest.mark.parametrize("include_selected_metadata", [False, True])
def test_populated_reader_reconciles_mixed_edition_sources(
    tmp_path, monkeypatch, include_selected_metadata
):
    path = tmp_path / "rules.db"
    text = (
        "100.1. Fixture rule.\n701.40 Explore\n"
        + EXPLORE
        + "\n701.42 Surveil\n701.42a To surveil 1, look at the top card of your "
        "library. You may put that card into your graveyard."
    )
    ingest_rules(path, text, source_url=URL, version="old")
    reader = module.retrieve_rules
    old = reader(path, "explore", limit=6).model_dump()
    old_explore = next(h for h in old["hits"] if h["source_id"] == "CR 701.40a")
    snapshot = ingest_rules(path, text, source_url=URL, version="current")
    current = reader(path, "surveil", limit=6).model_dump()
    current_surveil = next(h for h in current["hits"] if h["source_id"] == "CR 701.42a")
    # Valid section hashes can span different editions. They cannot jointly
    # discharge obligations, even when the whole-file contents happen to match.
    assert old_explore["content_sha256"] == hashlib.sha256(EXPLORE.encode()).hexdigest()
    calls = []

    def retrieve(*args, **kwargs):
        calls.append(args[1])
        return reader(*args, **kwargs)

    monkeypatch.setattr(module, "retrieve_rules", retrieve)
    rules, receipt = module.engine_rule_evidence(
        path,
        [card("Explore. Surveil 1.")],
        dict(
            hits=[old_explore, current_surveil],
            status="available",
            rules_snapshot=snapshot if include_selected_metadata else None,
        ),
    )
    assert 1 <= len(calls) <= 2
    assert calls[0] == "explore"
    assert rules["rules_snapshot"] == snapshot
    assert rules["hits"]
    assert all(h["version"] == "current" for h in rules["hits"])
    assert any(o["version"] == "old" for o in receipt["omissions"])
    assert all(
        o["reason"] == "incompatible_rules_snapshot" for o in receipt["omissions"]
    )
    assert all(
        a["status"] == "candidate_evidence"
        for a in receipt["evidence_dependencies"]["actions"]
    )
    assert receipt["evidence_dependencies"]["semantics_verified"] is False


def test_contract_covers_engine_obligations_without_card_specifics():
    for fragment in (
        "enablers",
        "costs",
        "zones",
        "recipients",
        "resource renewal",
        "stopping bounds",
        "finite versus infinite",
        "timing",
        "current state",
        "opponent intervention",
        "lethal shortfall",
        "independent finish",
        "support",
        "never certifies a loop",
        "current resource",
    ):
        assert fragment in module.ENGINE_EXPLANATION_CONTRACT
