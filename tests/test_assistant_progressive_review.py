import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics import assistant_progressive_review as progressive
from sabermetrics.assistant_answer_review import SourceEvidence
from sabermetrics.assistant_claim_fragments import reviewed_claim_text
from sabermetrics.assistant_claim_review import evidence_catalog, focused_sources
from sabermetrics.assistant_provider_budget import ProviderOperationCancelled
from sabermetrics.cedh.errors import ModelProviderError

NAMES = ["Amber Engine", "Blue Engine", "Copper Engine", "Dawn Engine"]
PARTS = ["Candidates"] + [
    f"Draw: {name} ({{1}}) — Pay {{1}} to draw a card." for name in NAMES
]
SOURCES = [
    f"Card: {name}\nMana cost: {{1}}\nOracle text:\n{{1}}: Draw a card."
    for name in NAMES
]


class Reviewer:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def generate(self, request):
        payload = json.loads(request.user)
        with self.lock:
            self.calls.append(payload)
        judgments = [
            {
                "claim_id": u["claim_id"],
                "category": "printed_fact",
                "verdict": "supported",
                "citations": [
                    {"source_index": u["part_index"] - 1, "quote": "{1}: Draw a card."}
                ],
            }
            for u in payload["claims"]
        ]
        return SimpleNamespace(
            value={"fulfillment": "complete", "judgments": judgments}
        )


def test_bounded_global_original_ids_and_coordinator_callback():
    reviewer = Reviewer()
    coordinator = threading.get_ident()
    publications = []

    def publish(corrected, receipt):
        assert threading.get_ident() == coordinator
        assert len(corrected) == len(PARTS)
        assert receipt["fulfillment"] == "unknown"
        for j in receipt["claim_judgments"]:
            assert reviewed_claim_text(receipt, j, PARTS)
        publications.append(receipt)

    corrected, receipt = progressive.progressive_review(
        reviewer, PARTS, SOURCES, "Find draw cards", on_reviewed=publish
    )
    assert corrected == PARTS
    assert receipt["fulfillment"] == "complete"
    assert receipt["progressive_review"]["job_count"] == 3
    assert receipt["progressive_review"]["job_limit"] == 3
    assert len(publications) == 3
    counts = Counter(u["claim_id"] for call in reviewer.calls for u in call["claims"])
    assert counts == {f"c{i}": 1 for i in range(4)}
    assert [j["claim_id"] for j in receipt["claim_judgments"]] == [
        f"c{i}" for i in range(4)
    ]
    final = reviewer.calls[-1]
    selection = final["research_context"]["progressive_selection"]
    additional = selection["additional_parent_parts"]
    assert not (set(final["parent_parts"]) & set(additional))
    combined = {**final["parent_parts"], **additional}
    assert [combined[str(i)] for i in selection["selection_order"]] == PARTS
    assert [r["source_index"] for r in final["source_records"]] == list(range(4))
    for call in reviewer.calls[:-1]:
        assert len(call["source_records"]) < 4
        assert {r["source_index"] for r in call["source_records"]} == {
            u["part_index"] - 1 for u in call["claims"]
        }
        assert len(call["parent_parts"]) < len(PARTS) - 1


@pytest.mark.parametrize(
    "body",
    [
        "Together with Blue Engine, this draws cards.",
        "Both use the same fodder.",
        "The best engine in the deck.",
        "No other card is needed.",
        "They share available mana.",
        "Works without any support.",
        "The only draw engine in the deck.",
        "This is the cheapest draw engine in your deck.",
        "This is the most efficient draw engine.",
        "This is the least expensive option.",
        "This engine is cheaper than the alternatives.",
        "This is the fastest draw engine.",
        "This is a top draw engine among your cards.",
    ],
)
def test_coupled_claim_is_reserved_for_global_batch(body):
    parts = PARTS[:]
    parts[1] = parts[1].split(" — ", 1)[0] + " — " + body
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(reviewer, parts, SOURCES, "Find cards")
    assert len(reviewer.calls) == 3
    assert receipt["progressive_review"]["enabled"]
    assert {u["claim_id"] for u in reviewer.calls[-1]["claims"]} == {"c0"}
    assert all(
        "c0" not in {u["claim_id"] for u in call["claims"]}
        for call in reviewer.calls[:-1]
    )


@pytest.mark.parametrize(
    "claim",
    [
        "This is the cheapest draw engine in your deck.",
        "This is the priciest draw engine.",
        "It outperforms all alternative draw engines.",
        "This is your sole draw engine.",
        "This is an unrivalled draw engine.",
        "Every alternative is inferior to this engine.",
        "Your deck has no substitute for this draw engine.",
        "This engine dominates the available alternatives.",
        "Draws a card and outperforms all alternatives.",
        "Pay {1} to draw a card using your sole engine.",
        "Potential deck support: Amber Engine (counter support).",
    ],
)
def test_deck_superlative_never_publishes_locally_and_keeps_original_global_id(claim):
    parts = PARTS[:]
    parts[1] = parts[1].split(" — ", 1)[0] + " — " + claim
    reviewer = Reviewer()
    published = []
    _, receipt = progressive.progressive_review(
        reviewer,
        parts,
        SOURCES,
        "Find draw engines",
        on_reviewed=lambda corrected, result: published.append(result),
    )
    units = progressive.claim_units(parts, set())
    cid = next(u["claim_id"] for u in units if u["claim"] == claim)
    assert len(reviewer.calls) == len(published) == 3
    assert all(
        cid not in {j["claim_id"] for j in result["claim_judgments"]}
        for result in published[:-1]
    )
    final = reviewer.calls[-1]
    assert {u["claim_id"] for u in final["claims"]} == {cid}
    assert final["claims"][0]["claim"] == claim
    assert {r["source_index"] for r in final["source_records"]} == set(range(4))
    assert cid in {j["claim_id"] for j in receipt["claim_judgments"]}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"protected_parts": {2}},
        {"research_context": {"empirical_receipts": ["measured"]}},
        {"research_context": {"task": {"kind": "package"}}},
        {"research_context": {"recommendation_packets": ["malformed"]}},
        {
            "research_context": {
                "recommendation_packets": [
                    {"incoming": "Amber Engine", "outgoing": "Blue Engine"}
                ]
            }
        },
    ],
)
def test_protected_or_package_context_is_single_batch(kwargs):
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(
        reviewer, PARTS, SOURCES, "Find cards", **kwargs
    )
    assert len(reviewer.calls) == 1
    assert not receipt["progressive_review"]["enabled"]


def test_global_failure_keeps_successful_local_findings_and_unknown_fulfillment():
    class FinalFailure(Reviewer):
        def generate(self, request):
            if "progressive_selection" in json.loads(request.user)["research_context"]:
                raise ModelProviderError("failed final review")
            return super().generate(request)

    published = []
    corrected, receipt = progressive.progressive_review(
        FinalFailure(),
        PARTS,
        SOURCES,
        "Find draw",
        on_reviewed=lambda c, r: published.append(r),
    )
    assert corrected == PARTS
    assert len(published) == 2
    assert len(receipt["claim_judgments"]) == 3
    assert receipt["missing_claim_ids"] == ["c3"]
    assert receipt["fulfillment"] == "unknown"
    assert receipt["status"] == "unverified"


@pytest.mark.parametrize("corruption", ["duplicate", "hash", "sources"])
def test_invalid_local_binding_is_not_published_and_retried_in_final(
    monkeypatch, corruption
):
    original_review = progressive.review_claims

    def corrupt(*args, **kwargs):
        corrected, receipt = original_review(*args, **kwargs)
        if (
            "c0" in kwargs["claim_ids"]
            and "progressive_selection" not in kwargs["research_context"]
        ):
            receipt = deepcopy(receipt)
            if corruption == "duplicate":
                receipt["claim_judgments"] *= 2
            elif corruption == "hash":
                receipt["claim_judgments"][0]["part_sha256"] = "stale hash"
            else:
                receipt["source_sha256"].reverse()
        return corrected, receipt

    monkeypatch.setattr(progressive, "review_claims", corrupt)
    reviewer = Reviewer()
    published = []
    _, receipt = progressive.progressive_review(
        reviewer,
        PARTS,
        SOURCES,
        "Find draw",
        on_reviewed=lambda c, r: published.append(r),
    )
    assert all(
        "c0" not in {j["claim_id"] for j in r["claim_judgments"]}
        for r in published[:-1]
    )
    assert "c0" in {u["claim_id"] for u in reviewer.calls[-1]["claims"]}
    assert len(receipt["claim_judgments"]) == 4
    assert receipt["progressive_review"]["job_count"] <= 3


def test_source_focus_and_wire_evidence_ids_keep_identical_basis():
    sources = SourceEvidence()
    sources.extend(
        [
            "Current deck printed mana costs:\nAmber Engine: {1}\nUnmentioned Card: {9}",
            *SOURCES,
        ]
    )
    _records, quotes = evidence_catalog(focused_sources(PARTS, sources))
    by_index = {
        index: eid
        for eid, (index, quote) in quotes.items()
        if quote == "{1}: Draw a card."
    }

    class WireReviewer:
        def generate(self, request):
            payload = json.loads(request.user)
            return SimpleNamespace(
                value={
                    "fulfillment": "partial",
                    **{
                        u["claim_id"]: {
                            "category": "printed",
                            "verdict": "supported",
                            "evidence_ids": [by_index[u["part_index"]]],
                            "correction": None,
                            "reason": "",
                            "unverified_remainder": False,
                        }
                        for u in payload["claims"]
                    },
                }
            )

    _, receipt = progressive.progressive_review(
        WireReviewer(), PARTS, sources, "Find draw"
    )
    assert len(receipt["claim_judgments"]) == 4
    assert receipt["omitted_sources"] == 1
    for j in receipt["claim_judgments"]:
        assert j["citations"][0]["source_index"] == j["part_index"]
    assert receipt["fulfillment"] == "partial"


def test_late_cancellation_prevents_publication_and_final_call():
    cancelled = threading.Event()

    class CancellingReviewer(Reviewer):
        def generate(self, request):
            result = super().generate(request)
            cancelled.set()
            return result

    published = []
    reviewer = CancellingReviewer()
    with pytest.raises(ProviderOperationCancelled):
        progressive.progressive_review(
            reviewer,
            PARTS,
            SOURCES,
            "draw",
            check_current=lambda: not cancelled.is_set(),
            on_reviewed=lambda c, r: published.append(r),
        )
    assert not published
    assert len(reviewer.calls) <= 2


def test_completion_callbacks_are_serial_and_precede_final_review():
    fast_published = threading.Event()
    coordinator = threading.get_ident()
    order = []

    class OrderedReviewer(Reviewer):
        def generate(self, request):
            payload = json.loads(request.user)
            ids = {u["claim_id"] for u in payload["claims"]}
            if ids == {"c0"}:
                assert fast_published.wait(3)
            if "progressive_selection" in payload["research_context"]:
                assert len(order) == 2
            return super().generate(request)

    def publish(corrected, receipt):
        assert threading.get_ident() == coordinator
        ids = {j["claim_id"] for j in receipt["claim_judgments"]}
        order.append(ids)
        if ids == {"c1", "c2"}:
            fast_published.set()

    progressive.progressive_review(
        OrderedReviewer(), PARTS, SOURCES, "draw", on_reviewed=publish
    )
    assert order == [{"c1", "c2"}, {"c0"}, {"c3"}]


def test_multiple_turns_share_global_two_provider_limit():
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    class BlockingReviewer(Reviewer):
        def generate(self, request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    entered.set()
            assert release.wait(3)
            try:
                return super().generate(request)
            finally:
                with lock:
                    active -= 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        turns = [
            executor.submit(
                progressive.progressive_review,
                BlockingReviewer(),
                PARTS,
                SOURCES,
                "draw",
            )
            for _ in range(2)
        ]
        try:
            assert entered.wait(3)
        finally:
            release.set()
        for turn in turns:
            assert len(turn.result(timeout=3)[1]["claim_judgments"]) == 4
    assert peak == 2


def test_realistic_paragraphs_packets_and_commander_support_get_local_review():
    commander = "Marchesa, the Black Rose"
    sources = [
        *SOURCES,
        f"Card: {commander}\nOracle text:\nDethrone (Whenever this creature attacks the player with the most life, put a +1/+1 counter on it.)",
        "Current deck:\nMarchesa, the Black Rose is the commander.",
        "Rule 602\nAn activated ability's costs must be paid before it can resolve.",
    ]
    parts = ["Candidates"] + [
        f"Draw: {name} ({{1}}) — Draws a card only after paying {{1}}, without tapping. "
        f"Potential deck support: {commander} (counter support). "
        "Both selected engines consume the same fodder."
        for name in NAMES
    ]
    context = {
        "preferences": [],
        "task": {"kind": "discover", "evidence_scope": "candidate_discovery"},
        "empirical_receipts": [],
        "recommendation_packets": [
            {"incoming": name, "prerequisites": ["counter support"], "coverage": {}}
            for name in NAMES
        ],
    }
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(
        reviewer, parts, sources, "Find draw cards", research_context=context
    )
    assert receipt["progressive_review"]["enabled"]
    assert len(reviewer.calls) == 3
    expected_coupled = {f"c{i * 3 + j}" for i in range(4) for j in (1, 2)}
    assert {u["claim_id"] for u in reviewer.calls[-1]["claims"]} == expected_coupled
    for call in reviewer.calls[:-1]:
        assert not ({u["claim_id"] for u in call["claims"]} & expected_coupled)
        assert all("Potential deck support" not in u["claim"] for u in call["claims"])
        assert {4, 5, 6} <= {r["source_index"] for r in call["source_records"]}
        assert len(call["research_context"]["recommendation_packets"]) == 2
        assert len(call["source_records"]) < len(sources)
    selection = reviewer.calls[-1]["research_context"]["progressive_selection"]
    assert set(selection["additional_parent_parts"]) == {"0"}
    for job in receipt["progressive_review"]["jobs"][:-1]:
        assert job["evidence_bytes_sent"] < sum(len(s.encode()) for s in sources)


def test_no_safe_source_reduction_uses_single_full_batch():
    # Every row explicitly depends on every retrieved record. Splitting it
    # would duplicate all evidence with no safe input reduction.
    parts = ["Candidates"] + [row + " " + "; ".join(NAMES) + "." for row in PARTS[1:]]
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(reviewer, parts, SOURCES, "Find cards")
    assert len(reviewer.calls) == 1
    assert not receipt["progressive_review"]["enabled"]


def test_global_and_local_corrections_in_same_original_part_both_survive():
    parts = PARTS[:]
    parts[1] = (
        parts[1].replace("Pay {1}", "Pay {2}") + " Both engines draw cards together."
    )
    sources = [
        SOURCES[0] + "\nBoth engines can draw cards independently.",
        *SOURCES[1:],
    ]

    class CorrectingReviewer(Reviewer):
        def generate(self, request):
            result = super().generate(request)
            payload = json.loads(request.user)
            for unit, judgment in zip(payload["claims"], result.value["judgments"]):
                if unit["part_index"] == 1:
                    judgment["verdict"] = "contradicted"
                    if "Pay {2}" in unit["claim"]:
                        judgment.update(correction_span="{2}", replacement="{1}")
                    else:
                        judgment.update(
                            correction_span="together",
                            replacement="independently",
                            citations=[
                                {
                                    "source_index": 0,
                                    "quote": "Both engines can draw cards independently.",
                                }
                            ],
                        )
            return result

    corrected, receipt = progressive.progressive_review(
        CorrectingReviewer(), parts, sources, "Find draw"
    )
    assert corrected[1] == PARTS[1] + " Both engines draw cards independently."
    assert len(receipt["patches"]) == 2
    assert all(
        reviewed_claim_text(receipt, j, parts) for j in receipt["claim_judgments"]
    )
    changed = parts[:]
    changed[1] = corrected[1]
    assert reviewed_claim_text(receipt, receipt["claim_judgments"][0], changed) == ""


def test_large_discovery_stays_three_jobs_and_each_batch_respects_claim_limit():
    parts = ["Candidates"] + [
        f"Draw: Engine {i} ({{1}}) — Pay {{1}} to draw a card. Activation costs {{1}}."
        for i in range(40)
    ]
    sources = [
        f"Card: Engine {i}\nOracle text:\n{{1}}: Draw a card." for i in range(40)
    ]
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(
        reviewer, parts, sources, "Find draw cards"
    )
    assert receipt["progressive_review"]["enabled"]
    assert len(reviewer.calls) == 3
    assert all(len(call["claims"]) <= 64 for call in reviewer.calls)
    assert len(receipt["claim_judgments"]) == 80
    assert not receipt["missing_claim_ids"]


@pytest.mark.parametrize(
    "claim",
    [
        "Pay {2} to draw a card.",
        "Draw a card.",
        "Draws a card only after paying {1}, without tapping.",
        "Activation costs {1}.",
        "Mana cost {1}.",
    ],
)
def test_explicit_local_facts_keep_useful_subset_review(claim):
    parts = PARTS[:]
    parts[1] = parts[1].split(" — ", 1)[0] + " — " + claim
    reviewer = Reviewer()
    published = []
    _, receipt = progressive.progressive_review(
        reviewer,
        parts,
        SOURCES,
        "Find draw",
        on_reviewed=lambda c, r: published.append(r),
    )
    assert any(
        "c0" in {u["claim_id"] for u in call["claims"]} for call in reviewer.calls[:-1]
    )
    assert all(r["fulfillment"] == "unknown" for r in published)
    assert receipt["fulfillment"] == "complete"
