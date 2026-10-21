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
