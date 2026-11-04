"""Joint packages must retain exact card evidence and copy-only boundaries."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sabermetrics.assistant_deck_intelligence import recommend_replacements
from sabermetrics.assistant_experiments import ExperimentService
from sabermetrics.assistant_swap_review import review_candidates
from tests.test_assistant_candidate_recall import insert
from tests.test_assistant_deck_intelligence import prepared as _base_prepared

prepared = _base_prepared


@pytest.fixture
def package_case(prepared):
    path, document = prepared
    insert(path, "old-a", "Body A", "Creature — Human", "Flying\nVigilance\nReach")
    insert(path, "old-b", "Body B", "Creature — Human", "Vigilance\nReach")
    insert(
        path,
        "extra",
        "Extra Land",
        "Enchantment",
        "You may play an additional land on each of your turns.",
    )
    insert(
        path,
        "reuse",
        "Reuse Land",
        "Artifact",
        "You may play lands from your graveyard.",
    )
    service = ExperimentService(path)
    island = next(c for c in document["entries"] if not c.get("is_commander"))
    for cid in ("old-a", "old-b"):
        document = service._candidate(
            document,
            [{"outgoing_entry_id": island["id"], "incoming_card_id": cid}],
            service._constraints({}),
        )["document"]
    next(c for c in document["entries"] if c.get("is_commander"))[
        "oracle_text"
    ] = "Whenever a land you control enters, draw a card."
    result = recommend_replacements(
        path, document, candidate_limit=8, include_packages=True
    )
    index = next(
        i
        for i, row in enumerate(result["alternatives"])
        if len(row["replacements"]) > 1
    )
    return path, document, result, index


def decisions_for(evidence, *, omit_last=False):
    citations = []
    for index, pair in enumerate(evidence["replacement_pairs"]):
        for side in ("incoming", "outgoing"):
            text = pair[side + "_oracle_facts"]["oracle_text"]
            citations.append(
                {
                    "replacement_index": index,
                    "side": side,
                    "quote": text,
                    "source_field": "oracle_text",
                }
            )
    if omit_last:
        citations.pop()
    return {
        "candidate_index": 0,
        "decision": "accept",
        "reason": "Coordinated land support",
        "benefit": "Additional land plays and graveyard access",
        "tradeoffs": ["Loses two creature bodies"],
        "citations": citations,
    }


def test_package_discovery_jointly_validates_and_never_mutates_source(package_case):
    path, document, result, index = package_case
    original = deepcopy(document)
    proposal, evidence = (
        result["alternatives"][index],
        result["candidate_evidence"][index],
    )
    assert evidence["strategy_transition"]["allowed"]
    assert index < 3  # The final three-result cut must not starve packages.
    assert len(evidence["requirements"]) >= 2
    service = ExperimentService(path)
    candidate = service._candidate(
        document, proposal["replacements"], service._constraints({})
    )
    assert candidate["document"]["validation"]["total_count"] == 100
    assert candidate["document"]["validation"]["legal"]
    assert document == original
    assert {
        pair["incoming_oracle_facts"]["name"] for pair in evidence["replacement_pairs"]
    } == {"Extra Land", "Reuse Land"}


@pytest.mark.parametrize(
    "failure",
    [None, "missing_pair_citation", "wrong_pair", "missing_transition", "short_quote"],
)
def test_every_changed_card_requires_its_own_exact_citation(package_case, failure):
    _, document, result, index = package_case
    proposal = deepcopy(result["alternatives"][index])
    evidence = deepcopy(result["candidate_evidence"][index])
    decision = decisions_for(evidence, omit_last=failure == "missing_pair_citation")
    if failure == "wrong_pair":
        decision["citations"][-1]["replacement_index"] = 0
    if failure == "missing_transition":
        del evidence["strategy_transition"]
    if failure == "short_quote":
        decision["citations"][-1]["quote"] = "Flying"
    requests = []

    def generate(request):
        requests.append(request)
        return SimpleNamespace(value={"decisions": [decision]})

    response = review_candidates(
        SimpleNamespace(generate=generate),
        {**document, "strategy_context": result["strategy_context"]},
        {},
        "land support",
        [proposal],
        [evidence],
    )
    assert bool(response["alternatives"]) is (failure is None)
    if requests:
        assert len(requests[0].user.encode()) <= 24576
        state = json.loads(requests[0].user)
        assert len(state["candidates"][0]["replacements"]) == 2


def test_protected_cuts_block_package_discovery(package_case):
    path, document, _, _ = package_case
    protected = [c["card_id"] for c in document["entries"] if "Body" in c["name"]]
    result = recommend_replacements(
        path,
        document,
        {"protected_cards": protected},
        candidate_limit=8,
        include_packages=True,
    )
    assert not any(len(p["replacements"]) > 1 for p in result["alternatives"])


def test_requested_recursion_remains_comparable_after_package(
    package_case, monkeypatch
):
    import sabermetrics.assistant_deck_intelligence as intelligence

    path, document, _, _ = package_case
    original = intelligence.assess_deck_requirements
    calls = []

    def focused_assessment(*args, **kwargs):
        result = original(*args, **kwargs)
        if not calls:
            result["requirements"].append(
                {
                    "requirement": "recursion",
                    "actual": 0,
                    "target": 1,
                    "gap": 1,
                    "basis": "requested-recovery-starter-target",
                }
            )
        calls.append(True)
        return result

    monkeypatch.setattr(intelligence, "assess_deck_requirements", focused_assessment)
    result = recommend_replacements(
        path, document, candidate_limit=8, include_packages=True
    )
    packages = [
        e
        for e in result["candidate_evidence"]
        if len(e.get("replacement_pairs", [])) == 2
    ]
    assert packages
    assert all(
        any(row["requirement"] == "recursion" for row in e["joint_requirement_changes"])
        for e in packages
    )
