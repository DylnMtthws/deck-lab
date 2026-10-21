"""Claim-driven lookup budgeting, using supplied text rather than live cards."""

from types import SimpleNamespace

import pytest

from sabermetrics import assistant_targeted_evidence as module


@pytest.fixture
def lookup(monkeypatch):
    calls = []

    def retrieve(path, query, **kwargs):
        calls.append((query, kwargs))
        return SimpleNamespace(
            model_dump=lambda: dict(
                hits=[],
                status="unavailable",
                rulings_status="unavailable",
                limitations=["Synthetic missing snapshot."],
            )
        )

    monkeypatch.setattr(module, "retrieve_rules", retrieve)
    return calls


def run(tmp_path, text, claims, support=None, existing=None):
    return module.finalist_evidence(
        tmp_path / "rules.db",
        [{"oracle_text": text}],
        existing or {"status": "unavailable", "hits": []},
        support,
        claims,
    )


@pytest.mark.parametrize(
    "claim",
    [
        "Repeat this activation with haste to generate energy.",
        "Repeat the activation for value.",
    ],
)
def test_support_return_and_generic_repeat_do_not_displace_tap(tmp_path, lookup, claim):
    support = [
        {
            "oracle_text": "When a creature with a +1/+1 counter dies, return it from the graveyard to the battlefield."
        }
    ]
    rules, receipt = run(
        tmp_path,
        "{T}: You get an energy counter. Activate only once each turn.",
        [claim],
        support,
    )
    queries = [query for query, _ in lookup]
    assert len(queries) == 2
    assert any("summoning sickness" in query for query in queries)
    assert any("energy counters player" in query for query in queries)
    assert not any("zone change" in query for query in queries)
    assert all(call[1]["cards"][-1] == support[0] for call in lookup)
    assert receipt["queries_omitted"] == 1
    assert rules["status"] == "unavailable"
    assert rules["targeted_lookups"][0]["limitations"]


@pytest.mark.parametrize(
    "claim",
    [
        "Return this creature from the graveyard to the battlefield.",
        "This creature recurs each turn.",
        "This creature returns each turn.",
    ],
)
def test_actual_recursion_claim_precedes_printed_mechanics(tmp_path, lookup, claim):
    run(tmp_path, "{T}: You get an energy counter.", [claim])
    assert "return graveyard" in lookup[0][0]
    assert len(lookup) == 2


@pytest.mark.parametrize(
    "claim",
    [
        "It returns with a +1/+1 counter and loops repeatedly.",
        "It retains its counters.",
        "The +1/+1 counter renews for each activation.",
    ],
)
def test_actual_counter_renewal_claim_gets_lookup(tmp_path, lookup, claim):
    run(tmp_path, "{T}: Draw a card.", [claim])
    assert lookup[0][0].startswith("zone change")
    assert len(lookup) <= 2


@pytest.mark.parametrize(
    "claim,expected,excluded",
    [
        (
            "Keep the energy counters for another activation.",
            "energy counters player",
            "+1/+1",
        ),
        (
            "Remove a +1/+1 counter to activate it.",
            "+1/+1 counters permanent",
            "energy",
        ),
    ],
)
def test_counter_kinds_do_not_imply_zone_renewal(
    tmp_path, lookup, claim, expected, excluded
):
    run(tmp_path, "{T}: Draw a card.", [claim])
    assert expected in lookup[0][0]
    assert excluded not in lookup[0][0]
    assert not any("zone change" in query for query, _ in lookup)


def test_separate_claims_do_not_create_counter_return_assertion(tmp_path, lookup):
    run(
        tmp_path,
        "{T}: Draw a card.",
        [
            "You get an energy counter.",
            "Return another creature from the graveyard.",
        ],
    )
    assert not any(query.startswith("zone change") for query, _ in lookup)


def test_full_evidence_provenance_and_omissions_survive(tmp_path, monkeypatch):
    full = dict(
        kind="rule",
        source_id="synthetic",
        content_sha256="fixture-hash",
        source_url="https://example.test/rules",
        version="fixture",
        text="Full supplied evidence. " * 200,
        text_truncated=False,
    )
    monkeypatch.setattr(
        module,
        "retrieve_rules",
        lambda *args, **kwargs: SimpleNamespace(
            model_dump=lambda: dict(
                hits=[full],
                status="available",
                rulings_status="unavailable",
                limitations=[],
            )
        ),
    )
    old = [dict(full, text="excerpt", text_truncated=True)] + [
        dict(full, source_id=str(i), content_sha256=str(i)) for i in range(7)
    ]
    rules, receipt = run(
        tmp_path,
        "{T}: Draw a card.",
        ["Tap it with haste."],
        existing=dict(hits=old, hits_omitted=2),
    )
    assert rules["hits"][0] == full
    assert len(rules["hits"]) == 6
    assert rules["hits_omitted"] == 4
    assert receipt["sources"][0] == {
        key: full[key]
        for key in (
            "kind",
            "source_id",
            "content_sha256",
            "source_url",
            "version",
            "text_truncated",
        )
    }


def test_lookup_cancellation_propagates(tmp_path, monkeypatch):
    def cancelled(*args, **kwargs):
        raise RuntimeError("cancelled")

    monkeypatch.setattr(module, "retrieve_rules", cancelled)
    with pytest.raises(RuntimeError, match="cancelled"):
        run(tmp_path, "{T}: Draw a card.", ["Tap it."])


def test_sacrifice_counter_and_tap_claim_does_not_assert_recursion(tmp_path, lookup):
    _, receipt = run(
        tmp_path,
        "Sacrifice another creature: Put a +1/+1 counter on this creature. {T}: Draw a card.",
        [
            "Sacrifice another creature to put a +1/+1 counter on it, then tap it with haste."
        ],
    )
    queries = [query for query, _ in lookup]
    assert len(queries) == 2
    assert "+1/+1 counters permanent" in queries[0]
    assert "summoning sickness" in queries[1]
    assert not any("return" in query or "renewal" in query for query in queries)
    assert receipt["queries_omitted"] == 1


def test_bare_graveyard_or_death_is_not_a_return_claim(tmp_path, lookup):
    run(
        tmp_path,
        "{T}: Draw a card.",
        ["The creature dies and goes to your graveyard with its +1/+1 counters."],
    )
    queries = [query for query, _ in lookup]
    assert not any(
        "return graveyard" in query or "counter renewal" in query for query in queries
    )


CONTROL = (
    "302.6. A creature's activated ability with the tap symbol or the untap symbol "
    "in its activation cost can't be activated unless the creature has been under "
    "its controller's control continuously since their most recent turn began."
)
HASTE = (
    "702.10c If a creature has haste, its controller can activate its activated "
    "abilities whose cost includes the tap symbol or the untap symbol even if "
    "that creature hasn't been controlled by that player continuously since "
    "their most recent turn began."
)
OFFICIAL_URL = "https://media.wizards.com/fixture/rules.txt"


def dependency_hit(text, source_id):
    import hashlib

    return dict(
        kind="rule",
        source_id=source_id,
        text=text,
        text_truncated=False,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        source_url=OFFICIAL_URL,
        version="fixture-edition",
        source="Wizards text snapshot; imported source identity",
        card_oracle_id=None,
    )


@pytest.mark.parametrize("suffix", ["; untapping is not assumed.", "."])
def test_declared_tap_condition_keeps_both_dependencies_before_optional_rulings(
    tmp_path, monkeypatch, suffix
):
    control = dependency_hit(CONTROL, "CR 302.6")
    haste = dependency_hit(HASTE, "CR 702.10c")
    optional = [
        dict(control, kind="card_ruling", source_id=f"ruling:{i}") for i in range(4)
    ]
    calls = []

    def retrieve(path, query, **kwargs):
        calls.append((query, kwargs))
        return SimpleNamespace(
            model_dump=lambda: dict(
                hits=[control, *optional[:2]] if "summoning" in query else optional[2:],
                status="available",
                rulings_status="available",
                limitations=[],
            )
        )

    monkeypatch.setattr(module, "retrieve_rules", retrieve)
    rules, receipt = run(
        tmp_path,
        "{T}, Pay {E}: You get two energy counters.",
        [
            "Generate energy counters.",
            "Tap-symbol cost: this creature needs continuous control or haste" + suffix,
        ],
        existing={"hits": [haste], "status": "available"},
    )
    assert len(calls) == 2
    assert "summoning sickness" in calls[0][0]
    assert "energy counters player" in calls[1][0]
    assert not any("zone change" in q for q, _ in calls)
    assert all(kwargs["deadline_seconds"] == 0.35 for _, kwargs in calls)
    assert len(rules["hits"]) == 6
    assert rules["hits"][:2] == [control, haste]
    assert rules["evidence_dependencies"]["tap_symbol"]["limitations"] == []
    assert receipt["sources"][1]["content_sha256"] == haste["content_sha256"]
    assert rules["hits"][1] == haste  # full text and provenance survive
    assert "Sources only" in receipt["evidence_dependencies"]["tap_symbol"]["scope"]


@pytest.mark.parametrize(
    "problem",
    ["missing", "truncated", "incompatible", "heading", "unofficial", "bad_hash"],
)
def test_unusable_haste_dependency_is_explicit(tmp_path, monkeypatch, problem):
    control = dependency_hit(CONTROL, "CR 302.6")
    haste = dependency_hit(HASTE, "CR 702.10c")
    if problem == "truncated":
        haste["text_truncated"] = True
        haste["text"] = "702.10c If a creature has haste, its controller can activate"
    elif problem == "incompatible":
        haste["version"] = "different-edition"
    elif problem == "heading":
        haste = dependency_hit("702.10. Haste", "CR 702.10")
    elif problem == "unofficial":
        haste["source_url"] = "https://example.test/rules"
    elif problem == "bad_hash":
        haste["content_sha256"] = "wrong"
    monkeypatch.setattr(
        module,
        "retrieve_rules",
        lambda *args, **kwargs: SimpleNamespace(
            model_dump=lambda: dict(
                hits=[control],
                status="available",
                rulings_status="no_matches",
                limitations=[],
            )
        ),
    )
    rules, receipt = run(
        tmp_path,
        "{T}: You get {E}.",
        ["Tap-symbol cost: this creature needs continuous control or haste."],
        existing={"hits": [] if problem == "missing" else [haste]},
    )
    notes = " ".join(rules["limitations"])
    assert "haste_activation is missing" in notes
    assert receipt["evidence_dependencies"]["tap_symbol"]["retained"] == [
        "continuous_control"
    ]
    if problem in ("truncated", "incompatible"):
        assert problem in notes


def test_local_snapshot_fetches_actual_activation_exception_in_initial_tap_lookup(
    tmp_path,
):
    from sabermetrics.assistant_rules import ingest_rules

    path = tmp_path / "rules.db"
    snapshot = (
        "100.1. Fixture for local source QA.\n\n"
        + CONTROL
        + "\n\n702.10. Haste\n\n702.10a Haste is a static ability.\n\n"
        + HASTE
    )
    meta = ingest_rules(
        path, snapshot, source_url=OFFICIAL_URL, version="fixture-edition"
    )
    rules, receipt = module.finalist_evidence(
        path,
        [
            {
                "name": "Energy creature",
                "type_line": "Creature",
                "oracle_text": "{T}, Pay {E}: You get two energy counters.",
            }
        ],
        {"hits": []},
        claims=[
            "Generate energy counters.",
            "Tap-symbol cost: this creature needs continuous control or haste.",
        ],
    )
    assert receipt["lookup_count"] == 2
    assert rules["hits"][:2] == [
        dependency_hit(CONTROL, "CR 302.6"),
        dependency_hit(HASTE, "CR 702.10c"),
    ]
    assert rules["rules_snapshot"] == meta
    assert receipt["queries"][0]["rules_snapshot"] == meta
    assert not rules["evidence_dependencies"]["tap_symbol"]["limitations"]


@pytest.mark.parametrize("moved", [False, True])
def test_direct_tap_lookup_protects_dependencies_from_rulings_and_moved_numbers(
    tmp_path, moved
):
    import sqlite3

    from sabermetrics.assistant_rules import ingest_rules, retrieve_rules

    path = tmp_path / "rules.db"
    control = CONTROL.replace("302.6.", "303.8.") if moved else CONTROL
    haste = HASTE.replace("702.10c", "702.11c") if moved else HASTE
    snapshot = "100.1. Fixture QA.\n\n" + control + "\n\n" + haste
    ingest_rules(path, snapshot, source_url=OFFICIAL_URL, version="fixture-edition")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE card_rulings (id INTEGER, card_oracle_id TEXT, ruling_text TEXT)"
        )
        conn.executemany(
            "INSERT INTO card_rulings VALUES (?, 'energy-creature', ?)",
            [(i, f"Optional ruling {i}") for i in range(4)],
        )
    result = retrieve_rules(
        path,
        "tap symbol activated ability summoning sickness haste",
        cards=[
            {
                "oracle_id": "energy-creature",
                "type_line": "Creature",
                "oracle_text": "{T}, Pay {E}: Draw a card.",
            }
        ],
        limit=3,
        deadline_seconds=0.35,
    )
    assert [h.text for h in result.hits[:2]] == [control, haste]
    assert result.hits[2].kind == "card_ruling"


def test_same_edition_label_with_different_snapshot_hash_is_incompatible(
    tmp_path, monkeypatch
):
    control, haste = dependency_hit(CONTROL, "CR 302.6"), dependency_hit(
        HASTE, "CR 702.10c"
    )
    snapshot = dict(
        source_url=OFFICIAL_URL, source_version="fixture-edition", content_sha256="new"
    )
    monkeypatch.setattr(
        module,
        "retrieve_rules",
        lambda *args, **kwargs: SimpleNamespace(
            model_dump=lambda: dict(
                hits=[control],
                status="available",
                rulings_status="no_matches",
                limitations=[],
                rules_snapshot=snapshot,
            )
        ),
    )
    rules, _ = run(
        tmp_path,
        "{T}: Draw a card.",
        ["Tap with haste."],
        existing={
            "hits": [haste],
            "rules_snapshot": dict(snapshot, content_sha256="old"),
        },
    )
    assert "incompatible snapshot version or hash" in " ".join(rules["limitations"])
    assert "haste_activation is missing" in " ".join(rules["limitations"])


def review_sources(rules):
    from sabermetrics.assistant_answer_review import evidence_sources

    return "\n".join(
        evidence_sources(
            {"entries": [{"name": "Fixture", "is_commander": True}]},
            [],
            ["Tap-symbol cost: this creature needs continuous control or haste."],
            rules,
        )
    )


@pytest.mark.parametrize(
    "problem",
    ["missing", "truncated", "incompatible", "unofficial", "bad_hash", "snapshot_hash"],
)
def test_rejected_dependencies_do_not_reach_answer_review(
    tmp_path, monkeypatch, problem
):
    control = dependency_hit(CONTROL, "CR 302.6")
    rejected = dependency_hit(HASTE + " Rejected fixture text.", "CR 702.10c")
    snapshot = dict(
        source_url=OFFICIAL_URL, source_version="fixture-edition", content_sha256="new"
    )
    existing = {"hits": [rejected]}
    if problem == "missing":
        existing["hits"] = []
    elif problem == "truncated":
        rejected["text_truncated"] = True
        # Even a fragment too short to classify must not escape as ordinary text.
        rejected["text"] = (
            "702.10c If a creature has haste, its controller can activate"
        )
    elif problem == "incompatible":
        rejected["version"] = "other-edition"
    elif problem == "unofficial":
        rejected["source_url"] = "https://example.test/rules"
    elif problem == "bad_hash":
        rejected["content_sha256"] = "wrong"
    elif problem == "snapshot_hash":
        existing["rules_snapshot"] = dict(snapshot, content_sha256="old")
    monkeypatch.setattr(
        module,
        "retrieve_rules",
        lambda *args, **kwargs: SimpleNamespace(
            model_dump=lambda: dict(
                hits=[control],
                status="available",
                rulings_status="no_matches",
                limitations=[],
                rules_snapshot=snapshot,
            )
        ),
    )
    rules, receipt = run(
        tmp_path, "{T}: Draw a card.", ["Tap with haste."], existing=existing
    )
    sources = review_sources(rules)
    assert CONTROL in sources
    assert rejected["text"] not in sources
    assert rules["hits"] == [control]
    assert rules["hits_omitted"] == (problem != "missing")
    assert "haste_activation is missing" in " ".join(rules["limitations"])
    assert receipt["evidence_dependencies"]["tap_symbol"]["retained"] == [
        "continuous_control"
    ]
    assert (
        "not an automatic rule verdict"
        in rules["evidence_dependencies"]["tap_symbol"]["scope"]
    )


def test_complete_pair_wins_without_reusing_other_edition_as_fallback(
    tmp_path, monkeypatch
):
    control, haste = dependency_hit(CONTROL, "CR 302.6"), dependency_hit(
        HASTE, "CR 702.10c"
    )
    rejected = dependency_hit(CONTROL + " Rejected other edition.", "CR 303.8")
    rejected["version"] = "other-edition"
    optional = [
        dict(control, kind="card_ruling", source_id=f"ruling:{i}", text=f"Optional {i}")
        for i in range(5)
    ]
    monkeypatch.setattr(
        module,
        "retrieve_rules",
        lambda *args, **kwargs: SimpleNamespace(
            model_dump=lambda: dict(
                hits=[rejected, *optional],
                status="available",
                rulings_status="available",
                limitations=[],
            )
        ),
    )
    rules, _ = run(
        tmp_path,
        "{T}: Draw a card.",
        ["Tap with haste."],
        existing={"hits": [control, haste]},
    )
    assert rules["hits"] == [control, haste, *optional[:4]]
    assert rules["hits_omitted"] == 2
    assert rejected["text"] not in review_sources(rules)
    assert "incompatible snapshot versions" in " ".join(rules["limitations"])


@pytest.mark.parametrize(
    "problem", ["truncated", "bad_hash", "incompatible", "snapshot_hash"]
)
def test_fresh_valid_dependency_replaces_rejected_old_evidence(
    tmp_path, monkeypatch, problem
):
    control, haste = dependency_hit(CONTROL, "CR 302.6"), dependency_hit(
        HASTE, "CR 702.10c"
    )
    rejected = dict(haste)
    snapshot = dict(
        source_url=OFFICIAL_URL, source_version="fixture-edition", content_sha256="new"
    )
    existing = {"hits": [rejected]}
    if problem == "truncated":
        rejected.update(text="702.10c If a creature has haste", text_truncated=True)
    elif problem == "bad_hash":
        rejected["content_sha256"] = "wrong"
    elif problem == "incompatible":
        rejected["version"] = "other-edition"
    else:
        existing["rules_snapshot"] = dict(snapshot, content_sha256="old")
    monkeypatch.setattr(
        module,
        "retrieve_rules",
        lambda *args, **kwargs: SimpleNamespace(
            model_dump=lambda: dict(
                hits=[control, haste],
                status="available",
                rulings_status="no_matches",
                limitations=[],
                rules_snapshot=snapshot,
            )
        ),
    )
    rules, _ = run(
        tmp_path, "{T}: Draw a card.", ["Tap with haste."], existing=existing
    )
    assert rules["hits"] == [control, haste]
    assert rules["evidence_dependencies"]["tap_symbol"]["retained"] == [
        "continuous_control",
        "haste_activation",
    ]
    assert not any("is missing" in note for note in rules["limitations"])
    assert HASTE in review_sources(rules)


def test_required_tap_lookup_avoids_broad_search_and_accepts_redundant_rule_sources(
    tmp_path, monkeypatch
):
    from sabermetrics import assistant_rules as rules

    path = tmp_path / "rules.db"
    redundant = CONTROL.replace("302.6.", "107.5.")
    rules.ingest_rules(
        path,
        "100.1. Fixture QA.\n\n" + redundant + "\n\n" + CONTROL + "\n\n" + HASTE,
        source_url=OFFICIAL_URL,
        version="fixture-edition",
    )

    def no_broad_search(*args, **kwargs):
        raise AssertionError(
            "Required dependencies must not wait for broad FTS/card-concept search"
        )

    monkeypatch.setattr(rules, "_rule_rows", no_broad_search)
    result = rules.retrieve_rules(
        path,
        "tap symbol activated ability summoning sickness haste",
        cards=[{"oracle_id": "creature", "oracle_text": "Level up {1}{G}."}],
        required_tap_only=True,
        deadline_seconds=0.35,
        limit=3,
    )
    assert {rules.tap_dependency(hit.text) for hit in result.hits} == {
        "continuous_control",
        "haste_activation",
    }
    assert not any(
        "missing" in note or "ambiguous" in note for note in result.limitations
    )
    assert not result.query_failures


def test_rule_query_failure_records_cause_without_assuming_deadline(
    tmp_path, monkeypatch
):
    import sqlite3

    from sabermetrics import assistant_rules as rules

    path = tmp_path / "rules.db"
    rules.ingest_rules(
        path,
        "100.1. Fixture QA.\n\n" + CONTROL + "\n\n" + HASTE,
        source_url=OFFICIAL_URL,
        version="fixture-edition",
    )

    def fail(*args, **kwargs):
        exc = sqlite3.OperationalError("synthetic private SQL is not exposed")
        exc.sqlite_errorname = "SQLITE_ERROR"
        raise exc

    monkeypatch.setattr(rules, "_collect_rule_hits", fail)
    result = rules.retrieve_rules(
        path, "tap symbol haste", required_tap_only=True, deadline_seconds=0.35
    )
    assert result.status == "unavailable"
    assert result.query_failures == [
        {
            "stage": "rules_query",
            "exception_type": "OperationalError",
            "sqlite_error_name": "SQLITE_ERROR",
            "deadline_exhausted": False,
        }
    ]
    assert "synthetic private SQL" not in str(result.model_dump())


def test_finalist_uses_required_only_mode_for_tap_query(tmp_path, lookup):
    run(
        tmp_path,
        "{T}: Draw a card. Remove a counter: Draw a card.",
        ["Tap-symbol cost: this creature needs continuous control or haste."],
    )
    assert lookup[0][1]["required_tap_only"] is True
    assert all(
        "required_tap_only" not in kwargs
        for query, kwargs in lookup
        if query != module._TAP_QUERY
    )
