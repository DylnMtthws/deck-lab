import json
import threading
from concurrent.futures import ThreadPoolExecutor
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
    f"Draw: {name} ({{1}}) — Activation cost: {{1}}. Qualified draw strategy."
    for name in NAMES
]
SOURCES = [
    f"Card: {name}\nMana cost: {{1}}\nOracle text:\n{{1}}: Draw a card."
    for name in NAMES
]


class Reviewer:
    def __init__(self):
        self.calls = []

    def generate(self, request):
        payload = json.loads(request.user)
        self.calls.append(payload)
        return SimpleNamespace(
            value={
                "fulfillment": "partial",
                "judgments": [
                    {
                        "claim_id": u["claim_id"],
                        "category": "printed_fact",
                        "verdict": "supported",
                        "citations": [
                            {
                                "source_index": u["part_index"] - 1,
                                "quote": "{1}: Draw a card.",
                            }
                        ],
                    }
                    for u in payload["claims"]
                ],
            }
        )


def test_exact_facts_publish_on_coordinator_before_one_global_call():
    reviewer = Reviewer()
    coordinator = threading.get_ident()
    published = []

    def publish(corrected, receipt):
        assert threading.get_ident() == coordinator
        assert receipt["fulfillment"] == "unknown"
        assert corrected == PARTS
        assert all(
            reviewed_claim_text(receipt, j, PARTS) for j in receipt["claim_judgments"]
        )
        if receipt.get("method") == "EXACT_source_check":
            assert not reviewer.calls
            assert "review_model" not in receipt
        published.append(receipt)

    corrected, receipt = progressive.progressive_review(
        reviewer, PARTS, SOURCES, "Find draw", on_reviewed=publish
    )
    assert corrected == PARTS
    assert len(reviewer.calls) == 1
    assert len(published) == 2
    assert [j["claim_id"] for j in receipt["claim_judgments"]] == [
        f"c{i}" for i in range(8)
    ]
    assert not receipt["missing_claim_ids"]
    assert receipt["fulfillment"] == "partial"
    jobs = receipt["progressive_review"]
    assert jobs["job_count"] == jobs["job_limit"] == jobs["model_job_count"] == 1
    assert jobs["exact_fact_count"] == 4
    assert [j["stage"] for j in jobs["jobs"]] == ["exact", "global"]
    assert all(j["bound"] for j in jobs["jobs"])
    assert {u["claim_id"] for u in reviewer.calls[0]["claims"]} == {
        "c1",
        "c3",
        "c5",
        "c7",
    }
    selection = reviewer.calls[0]["research_context"]["progressive_selection"]
    combined = {
        **reviewer.calls[0]["parent_parts"],
        **selection["additional_parent_parts"],
    }
    assert [combined[str(i)] for i in selection["selection_order"]] == PARTS
    assert receipt["source_sha256"] == [progressive.text_hash(s) for s in SOURCES]
    assert jobs["evidence_bytes_sent"] == sum(len(s.encode()) for s in SOURCES)


@pytest.mark.parametrize(
    "claim",
    [
        "Pay {1} to draw a card.",
        "Draw a card.",
        "Activation costs {1}.",
        "Draws a card only after paying {1}, without tapping.",
        "This is the cheapest draw engine in your deck.",
        "It outperforms all alternatives.",
        "Together with Blue Engine, this draws cards.",
        "Both use the same fodder.",
        "No other card is needed.",
        "This is your sole draw engine.",
        "Potential deck support: Amber Engine (counter support).",
        "Tap-symbol cost: this creature needs continuous control or haste; untapping is not assumed.",
    ],
)
def test_unknown_and_global_claims_stay_in_single_semantic_call(claim):
    parts = [
        p.replace("Activation cost: {1}. Qualified draw strategy.", claim)
        for p in PARTS
    ]
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(reviewer, parts, SOURCES, "Find cards")
    assert len(reviewer.calls) == 1
    assert not receipt["progressive_review"]["enabled"]
    assert {u["claim_id"] for u in reviewer.calls[0]["claims"]} == {
        f"c{i}" for i in range(4)
    }
    assert len(receipt["claim_judgments"]) == 4


def test_global_failure_keeps_exact_facts_and_unknown_fulfillment():
    class Failure(Reviewer):
        def generate(self, request):
            self.calls.append(json.loads(request.user))
            raise ModelProviderError("failed global review")

    reviewer = Failure()
    published = []
    _, receipt = progressive.progressive_review(
        reviewer, PARTS, SOURCES, "Draw", on_reviewed=lambda c, r: published.append(r)
    )
    assert len(reviewer.calls) == len(published) == 1
    assert {
        j["claim_id"] for j in receipt["claim_judgments"] if j["verdict"] == "supported"
    } == {"c0", "c2", "c4", "c6"}
    assert len(receipt["claim_judgments"]) == 8
    assert all(
        j["method"] == "unreviewed_abstention"
        for j in receipt["claim_judgments"]
        if j["verdict"] == "unverified"
    )
    assert receipt["missing_claim_ids"] == ["c1", "c3", "c5", "c7"]
    assert receipt["fulfillment"] == "unknown"
    assert receipt["status"] == "unverified"


@pytest.mark.parametrize("corruption", ["duplicate", "hash", "sources", "indices"])
def test_invalid_exact_receipt_is_not_published_and_retried_globally(
    monkeypatch, corruption
):
    original = progressive.exact_cost_facts

    def corrupt(*args):
        corrected, receipt = original(*args)
        if corruption == "duplicate":
            receipt["claim_judgments"] *= 2
        elif corruption == "hash":
            receipt["claim_judgments"][0]["part_sha256"] = "stale"
        elif corruption == "sources":
            receipt["source_sha256"].reverse()
        else:
            receipt["evidence_indices"] = []
        return corrected, receipt

    monkeypatch.setattr(progressive, "exact_cost_facts", corrupt)
    reviewer = Reviewer()
    published = []
    _, receipt = progressive.progressive_review(
        reviewer, PARTS, SOURCES, "Draw", on_reviewed=lambda c, r: published.append(r)
    )
    assert len(reviewer.calls) == len(published) == 1
    assert len(reviewer.calls[0]["claims"]) == 8
    assert len(receipt["claim_judgments"]) == 8
    assert not receipt["progressive_review"]["jobs"][0]["bound"]


def test_focused_source_basis_wire_ids_and_metadata_remain_bound():
    sources = SourceEvidence()
    sources.card_names = NAMES[:]
    sources.extend(
        [
            "Current deck printed mana costs:\nAmber Engine: {1}\nUnmentioned Card: {9}",
            *SOURCES,
        ]
    )
    _records, quotes = evidence_catalog(focused_sources(PARTS, sources))
    by_index = {
        i: eid for eid, (i, quote) in quotes.items() if quote == "{1}: Draw a card."
    }

    class WireReviewer(Reviewer):
        def generate(self, request):
            payload = json.loads(request.user)
            self.calls.append(payload)
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

    _, receipt = progressive.progressive_review(WireReviewer(), PARTS, sources, "Draw")
    assert receipt["omitted_sources"] == 1
    assert receipt["status"] == "unverified"
    assert len(receipt["claim_judgments"]) == 8
    assert all(
        j["citations"][0]["source_index"] == j["part_index"]
        for j in receipt["claim_judgments"]
    )
    assert receipt["source_sha256"] == [
        progressive.text_hash(s) for s in focused_sources(PARTS, sources)
    ]


def test_cancellation_after_exact_publication_prevents_provider_call():
    reviewer = Reviewer()
    current = True

    def publish(c, r):
        nonlocal current
        current = False

    with pytest.raises(ProviderOperationCancelled):
        progressive.progressive_review(
            reviewer,
            PARTS,
            SOURCES,
            "Draw",
            on_reviewed=publish,
            check_current=lambda: current,
        )
    assert not reviewer.calls


def test_cancellation_after_model_return_prevents_semantic_publication():
    current = True
    published = []

    class CancellingReviewer(Reviewer):
        def generate(self, request):
            nonlocal current
            result = super().generate(request)
            current = False
            return result

    with pytest.raises(ProviderOperationCancelled):
        progressive.progressive_review(
            CancellingReviewer(),
            PARTS,
            SOURCES,
            "Draw",
            check_current=lambda: current,
            on_reviewed=lambda c, r: published.append(r),
        )
    assert len(published) == 1
    assert published[0]["method"] == "EXACT_source_check"


@pytest.mark.parametrize("actual_gateway", [False, True])
@pytest.mark.parametrize("after_acquire", [False, True])
@pytest.mark.parametrize("deadline", [False, True])
def test_queued_review_checks_caller_before_any_provider_execution(
    monkeypatch, tmp_path, actual_gateway, after_acquire, deadline
):
    from sabermetrics import assistant_provider_budget as budget_module
    from sabermetrics.cedh.cost_ledger import check_operation_current

    stale, waiting, release = (threading.Event() for _ in range(3))
    ready = [threading.Event(), threading.Event()]
    reviewer = Reviewer()
    caller_threads = []
    outer_checks = []
    transport_calls = []
    client = None

    def current():
        caller_threads.append(threading.get_ident())
        if stale.is_set():
            if deadline:
                raise TimeoutError("review deadline expired")
            return False
        return True

    gateway = reviewer
    if actual_gateway:
        import httpx

        from sabermetrics.cedh.provider_deepseek import DeepSeekGateway
        from sabermetrics.cedh.settings import load_cedh_settings

        settings = load_cedh_settings()
        monkeypatch.setenv(settings.model.credential_env, "fixture-token")

        def transport(request):
            transport_calls.append(request)
            raise AssertionError("cancelled review reached HTTP transport")

        client = httpx.Client(transport=httpx.MockTransport(transport))
        gateway = DeepSeekGateway(
            settings, db_path=str(tmp_path / "review-cancel.db"), client=client
        )
    # Nested wrapper callbacks remain scoped and execute on the review thread.
    gateway = budget_module.budget_gateway(
        budget_module.budget_gateway(gateway),
        check_current=lambda: outer_checks.append(threading.get_ident()),
    )
    underlying = budget_module._PERMITS

    class ObservedPermits:
        def acquire(self, **kwargs):
            acquired = underlying.acquire(**kwargs)
            if threading.current_thread().name.startswith("review-waiter"):
                if acquired and after_acquire:
                    stale.set()
                elif not acquired:
                    waiting.set()
            return acquired

        def release(self):
            underlying.release()

    monkeypatch.setattr(budget_module, "_PERMITS", ObservedPermits())

    def holder(index):
        with budget_module.ProviderBudget().operation():
            ready[index].set()
            assert release.wait(3)

    def review():
        owner = threading.get_ident()
        try:
            return progressive.progressive_review(
                gateway, PARTS, SOURCES, "Draw", check_current=current
            )
        finally:
            assert caller_threads and set(caller_threads) == {owner}
            assert set(outer_checks) <= {owner}
            if actual_gateway:
                assert outer_checks
            # Hooks from failed nested wrappers must leave no stale scope.
            check_operation_current()

    try:
        with (
            ThreadPoolExecutor(max_workers=2) as holders,
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="review-waiter"
            ) as waiter,
        ):
            held = [holders.submit(holder, i) for i in range(2)]
            try:
                assert all(event.wait(3) for event in ready)
                pending = waiter.submit(review)
                assert waiting.wait(3)
                if after_acquire:
                    release.set()
                else:
                    stale.set()
                with pytest.raises(
                    TimeoutError if deadline else ProviderOperationCancelled
                ):
                    pending.result(timeout=3)
            finally:
                release.set()
            for future in held:
                future.result(timeout=3)
        assert not reviewer.calls
        assert not transport_calls
        # Cancellation releases the global permits for subsequent operations.
        assert (
            budget_module.budget_gateway(
                SimpleNamespace(generate=lambda r: r)
            ).generate(7)
            == 7
        )
    finally:
        if client is not None:
            client.close()


@pytest.mark.parametrize("fulfillment", ["complete", "partial"])
def test_all_exact_still_has_one_real_fulfillment_call_without_duplicate_judgments(
    fulfillment,
):
    parts = [p.replace(" Qualified draw strategy.", "") for p in PARTS]

    class CompleteReviewer(Reviewer):
        def generate(self, request):
            result = super().generate(request)
            result.value["fulfillment"] = fulfillment
            return result

    reviewer = CompleteReviewer()
    published = []
    _, receipt = progressive.progressive_review(
        reviewer, parts, SOURCES, "Draw", on_reviewed=lambda c, r: published.append(r)
    )
    assert len(reviewer.calls) == len(published) == 1
    assert len(receipt["claim_judgments"]) == 4
    assert all(j["method"] == "EXACT_source_check" for j in receipt["claim_judgments"])
    assert receipt["fulfillment"] == fulfillment
    assert receipt["status"] == "no_supported_correction"


@pytest.mark.parametrize(
    "verdict,remainder,correct",
    [
        ("supported", True, False),
        ("unverified", False, False),
        ("contradicted", False, False),
        ("contradicted", False, True),
    ],
)
@pytest.mark.parametrize("fulfillment", ["complete", "partial"])
def test_all_exact_retains_actual_semantic_qualifications(
    verdict, remainder, correct, fulfillment
):
    parts = [p.replace(" Qualified draw strategy.", "") for p in PARTS]

    class QualifiedReviewer(Reviewer):
        def generate(self, request):
            result = super().generate(request)
            result.value["fulfillment"] = fulfillment
            judgment = result.value["judgments"][0]
            judgment.update(
                verdict=verdict,
                unverified_remainder=remainder,
                reason="Payable cost still depends on the game state.",
            )
            if correct:
                judgment.update(
                    correction_span="Activation cost: {1}.",
                    replacement="Printed activation cost: {1}.",
                )
            return result

    reviewer = QualifiedReviewer()
    publications = []
    corrected, receipt = progressive.progressive_review(
        reviewer,
        parts,
        SOURCES,
        "Draw",
        on_reviewed=lambda c, r: publications.append(r),
    )
    assert len(reviewer.calls) == len(publications) == 1
    assert corrected == parts
    assert receipt["status"] == "unverified"
    assert receipt["fulfillment"] == (
        "unknown" if fulfillment == "complete" else fulfillment
    )
    assert receipt["unresolved"]
    assert not receipt["patches"]
    assert not receipt["missing_claim_ids"]
    assert [j["claim_id"] for j in receipt["claim_judgments"]] == [
        "c0",
        "c1",
        "c2",
        "c3",
    ]
    assert all(
        j["method"] == "EXACT_source_check"
        and j["verdict"] == "supported"
        and reviewed_claim_text(receipt, j, parts)
        for j in receipt["claim_judgments"]
    )
    recheck = receipt["progressive_review"]["semantic_recheck"]
    assert recheck["applied"] is False
    semantic = recheck["receipt"]
    judgment = semantic["claim_judgments"][0]
    assert judgment["claim_id"] == "c0"
    assert judgment["verdict"] == ("unverified" if remainder else verdict)
    assert judgment["unverified_remainder"] is remainder
    assert judgment["reason"] == "Payable cost still depends on the game state."
    assert reviewed_claim_text(semantic, judgment, parts)
    assert bool(semantic["patches"]) is correct
    assert receipt["progressive_review"]["global_review_complete"]


def test_semantic_correction_does_not_transfer_exact_verdict_to_rewritten_part():
    class CorrectingReviewer(Reviewer):
        def generate(self, request):
            result = super().generate(request)
            result.value["judgments"][0].update(
                verdict="contradicted",
                correction_span="Qualified",
                replacement="Tentative",
            )
            return result

    corrected, receipt = progressive.progressive_review(
        CorrectingReviewer(), PARTS, SOURCES, "Draw"
    )
    assert corrected[1] == PARTS[1].replace("Qualified", "Tentative")
    assert len(receipt["patches"]) == 1
    exact = receipt["claim_judgments"][0]
    assert reviewed_claim_text(receipt, exact, PARTS)
    assert reviewed_claim_text(receipt, exact, corrected) == ""


def test_large_discovery_never_fans_out_or_exceeds_semantic_claim_limit():
    parts = ["Candidates"] + [
        f"Draw: Engine {i} ({{1}}) — Activation cost: {{1}}. Draw strategy."
        for i in range(40)
    ]
    sources = [
        f"Card: Engine {i}\nOracle text:\n{{1}}: Draw a card." for i in range(40)
    ]
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(reviewer, parts, sources, "Draw")
    assert len(reviewer.calls) == 1
    assert len(reviewer.calls[0]["claims"]) == 40
    assert len(receipt["claim_judgments"]) == 80
    assert not receipt["missing_claim_ids"]


def test_unbounded_semantic_context_abstains_without_fanout():
    parts = ["Candidates"] + [
        f"Draw: Engine {i} ({{1}}) — Unknown claim." for i in range(65)
    ]
    reviewer = Reviewer()
    _, receipt = progressive.progressive_review(reviewer, parts, SOURCES, "Draw")
    assert not reviewer.calls
    assert receipt["status"] == "unverified"
    assert len(receipt["missing_claim_ids"]) == 65
    assert len(receipt["claim_judgments"]) == 65
    assert receipt["progressive_review"]["model_job_count"] == 0


def test_multiple_turns_keep_process_wide_two_provider_limit():
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0

    class BlockingReviewer(Reviewer):
        def generate(self, request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
                if active == 2:
                    entered.set()
            assert release.wait(3)
            try:
                return super().generate(request)
            finally:
                with lock:
                    active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        turns = [
            pool.submit(
                progressive.progressive_review,
                BlockingReviewer(),
                PARTS,
                SOURCES,
                "Draw",
            )
            for _ in range(2)
        ]
        try:
            assert entered.wait(3)
        finally:
            release.set()
        assert all(
            len(turn.result(timeout=3)[1]["claim_judgments"]) == 8 for turn in turns
        )
    assert peak == 2


def test_current_renderer_tap_condition_stays_semantic_and_lead_is_visible():
    names = ["Mystic Archaeologist", "Azure Mage", "Roil Cartographer"]
    costs = ["{3}{U}{U}", "{3}{U}", "{T}, Pay six {E}"]
    abilities = [
        "{3}{U}{U}: Draw two cards.",
        "{3}{U}: Draw a card.",
        "{T}, Pay six {E}: Draw three cards.",
    ]
    tap = "Tap-symbol cost: this creature needs continuous control or haste."
    lead = "Selected candidates. Casting, timing and activation resources still need to be available. Untapping is not assumed."
    parts = [lead] + [
        f"Draw: {name} ({{1}}{{U}}) — Qualified strategy. Activation cost: {cost}."
        for name, cost in zip(names, costs)
    ]
    parts[3] += " " + tap
    sources = [
        f"Card: {name}\nOracle text:\n{ability}"
        for name, ability in zip(names, abilities)
    ]

    class RendererReviewer(Reviewer):
        def generate(self, request):
            payload = json.loads(request.user)
            self.calls.append(payload)
            assert (
                payload["research_context"]["progressive_selection"][
                    "additional_parent_parts"
                ]["0"]
                == lead
            )
            assert all(
                not u["claim"].startswith("Activation cost:") for u in payload["claims"]
            )
            assert any(u["claim"] == tap for u in payload["claims"])
            return SimpleNamespace(
                value={
                    "fulfillment": "partial",
                    "judgments": [
                        {
                            "claim_id": u["claim_id"],
                            "category": "strategic_judgment",
                            "verdict": "unverified",
                            "citations": [],
                        }
                        for u in payload["claims"]
                    ],
                }
            )

    reviewer = RendererReviewer()
    publications = []
    _, receipt = progressive.progressive_review(
        reviewer,
        parts,
        sources,
        "Repeatable draw",
        on_reviewed=lambda c, r: publications.append(r),
    )
    assert len(reviewer.calls) == len(publications) == 1
    assert len(publications[0]["claim_judgments"]) == 3
    assert publications[0]["fulfillment"] == "unknown"
    assert len(receipt["claim_judgments"]) == 7
    assert receipt["fulfillment"] == "partial"
    assert all(
        j["verdict"] == "unverified"
        for j in receipt["claim_judgments"]
        if j["reviewed_claim"] == tap
    )


def test_lead_and_workspace_context_remain_in_model_input_without_exact_matches():
    reviewer = Reviewer()
    parts = [p.replace("Activation cost: {1}. ", "") for p in PARTS]
    context = {
        "workspace_binding": {"document": "current", "revision": 9},
        "source_manifest": {"card": "sha256"},
    }
    progressive.progressive_review(
        reviewer, parts, SOURCES, "Draw", research_context=context
    )
    received = reviewer.calls[0]["research_context"]
    assert received["workspace_binding"] == context["workspace_binding"]
    assert received["source_manifest"] == context["source_manifest"]
    assert received["progressive_selection"]["additional_parent_parts"] == {
        "0": parts[0]
    }
    assert context == {
        "workspace_binding": {"document": "current", "revision": 9},
        "source_manifest": {"card": "sha256"},
    }
