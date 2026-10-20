"""Reviewed leads must never outlive their source facts or owner scope."""

import copy
import hashlib
import json

import pytest

from sabermetrics import assistant_episodes as episodes
from sabermetrics import db
from sabermetrics.assistant_episodes import (
    capture_episode,
    deck_evidence_hash,
    recall_episodes,
)
from sabermetrics.assistant_retrieval_plan import compile_retrieval_plan
from sabermetrics.assistant_store import AssistantStore

pytest_plugins = ["tests.test_assistant_experiments"]


def long_review_capture(prepared):
    _svc, path, _owner, _deck, document, _alts = prepared
    with db.connect(path) as conn:
        card = dict(
            conn.execute("SELECT * FROM cards WHERE id='incoming-0'").fetchone()
        )
    request = (
        "Compare Marchesa engine prerequisites, drawbacks, recovery and rules timing. "
        "Investigate sacrifice outlets, counters, haste and recurring value; distinguish "
        "a repeatable setup from an infinite loop and explain what removal interrupts. "
    ) * 12
    plan = compile_retrieval_plan(request)
    claims = [
        (
            "The proposed engine needs a counter before the creature dies, an outlet, "
            "and the correct delayed trigger timing. Removing the commander before "
            "the death event can prevent recursion; returning a creature does not "
            "by itself prove a repeatable loop or a winning line. "
        )
        * 10
        + f"Investigate {card['name']} as option {i}."
        for i in range(6)
    ]
    review = {
        "source_sha256": ["a" * 64],
        "claim_judgments": [
            {
                "claim_id": f"c{i}",
                "verdict": "contradicted" if i % 2 else "supported",
                "category": "rules_interaction",
                "reason": "Check the death event and delayed trigger against the cited rule. "
                * 5,
                "correction_applied": bool(i % 2),
                "replacement": "The line remains conditional on counters, an outlet and timing. "
                * 10,
                "citations": [
                    {
                        "source_index": 0,
                        "quote": "At the beginning of the next end step.",
                    }
                ],
            }
            for i in range(6)
        ],
    }
    return {
        "document": document,
        "request": request,
        "cards": {"incoming-0": card},
        "selection": {"groups": [{"card_ids": ["incoming-0"]}]},
        "review": review,
        "prompt_version": "p1",
        "retrieval_plan": plan,
        "claim_parts": ["Lead", *claims],
        "db_path": path,
    }


def test_long_reviewed_claims_and_compiled_plan_fit_without_losing_receipts(prepared):
    inputs = long_review_capture(prepared)
    episode = capture_episode(**inputs)
    assert (
        episode is not None
    ), "Realistic reviewed prose and compiled plan exceed the old 12000 cap"
    assert len(json.dumps(episode)) <= 12000
    assert len(episode["hypotheses"]) == 6
    for claim, judgment, hypothesis in zip(
        inputs["claim_parts"][1:],
        inputs["review"]["claim_judgments"],
        episode["hypotheses"],
    ):
        assert hypothesis["claim_sha256"] == hashlib.sha256(claim.encode()).hexdigest()
        assert hypothesis["claim_truncated"]
        assert claim.startswith(hypothesis["claim"])
        assert hypothesis["verdict"] == "unverified"
        assert hypothesis["reviewed_verdict"] == judgment["verdict"]
        assert hypothesis["unverified_remainder"]
        assert hypothesis["card_ids"] == ["incoming-0"]  # Name is beyond the prefix.
        assert hypothesis["category"] == judgment["category"]
        assert hypothesis["correction_applied"] == judgment["correction_applied"]
        assert (
            hypothesis["replacement_sha256"]
            == hashlib.sha256(judgment["replacement"].encode()).hexdigest()
        )
        assert hypothesis["replacement_truncated"]
        assert hypothesis["reason_truncated"]
        assert hypothesis["evidence"] == [
            {
                "source_index": 0,
                "quote_sha256": episodes._hash(judgment["citations"][0]["quote"]),
                "source_sha256": "a" * 64,
            }
        ]
    assert episode["narrative_compacted"]
    attention = episode["retrieval_plan"]
    assert attention["summary_only"]
    assert attention["plan_hash"] == inputs["retrieval_plan"]["plan_hash"]
    assert attention["plan_sha256"] == episodes._hash(inputs["retrieval_plan"])
    assert [t["kind"] for t in attention["tasks"]] == [
        t["kind"] for t in inputs["retrieval_plan"]["tasks"]
    ]
    assert "query" not in json.dumps(attention)


@pytest.mark.parametrize("verdict", ["supported", "contradicted", "unverified"])
def test_complete_short_claim_keeps_review_verdict(prepared, verdict):
    inputs = long_review_capture(prepared)
    inputs["claim_parts"] = ["Lead", "Incoming 0 needs setup."]
    inputs["review"]["claim_judgments"] = [{"claim_id": "c0", "verdict": verdict}]
    episode = capture_episode(**inputs)
    assert episode is not None
    hypothesis = episode["hypotheses"][0]
    assert hypothesis["verdict"] == hypothesis["reviewed_verdict"] == verdict
    assert not hypothesis["claim_truncated"]
    assert not hypothesis["unverified_remainder"]


def test_same_prefix_opposite_full_claims_survive_recall_dedup(prepared):
    inputs = long_review_capture(prepared)
    _svc, path, owner, deck, document, _alts = prepared
    prefix = inputs["claim_parts"][1]
    captured = []
    for ending in ("This loop works.", "This loop does not work.", "This loop works."):
        inputs["claim_parts"] = ["Lead", prefix + ending]
        inputs["review"]["claim_judgments"] = inputs["review"]["claim_judgments"][:1]
        captured.append(capture_episode(**inputs))
    assert all(captured)
    assert len({ep["hypotheses"][0]["claim"] for ep in captured}) == 1
    assert len({ep["hypotheses"][0]["claim_sha256"] for ep in captured}) == 2
    store = AssistantStore(path)
    store.get(owner, deck)
    store._update(
        owner,
        deck,
        lambda p: p.update(
            messages=[
                {"role": "assistant", "research": {"reviewed_episode": ep}}
                for ep in captured
            ]
        ),
    )
    items = recall_episodes(path, owner, deck, document)["items"]
    assert len(items) == 2
    assert {ep["investigation_sha256"] for ep in items} == {
        ep["investigation_sha256"] for ep in captured
    }


def test_required_dependency_ids_cannot_be_compacted_away(prepared):
    inputs = long_review_capture(prepared)
    document = copy.deepcopy(inputs["document"])
    provider = next(e for e in document["entries"] if not e["is_commander"])
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(398)]
    assert len(json.dumps(ids)) > 12000
    with db.connect(inputs["db_path"]) as conn:
        conn.executemany(
            "INSERT INTO cards(id,oracle_id,name,type_line) VALUES(?,?,'Provider','Land')",
            [(cid, cid) for cid in ids],
        )
        conn.commit()
    document["entries"].extend({**provider, "id": cid, "card_id": cid} for cid in ids)
    assert len(document["entries"]) == 400  # Pass the existing entry-count guard.
    inputs["document"] = document
    assert capture_episode(**inputs) is None


def test_capture_abstains_when_only_an_empty_receipt_would_fit(prepared):
    inputs = long_review_capture(prepared)
    document = copy.deepcopy(inputs["document"])
    provider = next(e for e in document["entries"] if not e["is_commander"])
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(100)]
    with db.connect(inputs["db_path"]) as conn:
        conn.executemany(
            "INSERT INTO cards(id,oracle_id,name,type_line) VALUES(?,?,'Provider','Land')",
            [(cid, cid) for cid in ids],
        )
        conn.commit()
    document["entries"].extend({**provider, "id": cid, "card_id": cid} for cid in ids)
    inputs["document"] = document
    # Fixed dependency identities fit alone, but no useful narrative can fit.
    # Reject instead of creating an empty receipt that displaces useful recall.
    assert capture_episode(**inputs) is None


def test_plan_summary_preserves_bounded_task_coverage_and_full_identity(prepared):
    inputs = long_review_capture(prepared)
    plan = inputs["retrieval_plan"]
    plan["results"] = [
        {
            "task_id": "task-1",
            "kind": "rules",
            "coverage": "uncovered",
            "evidence_class": "official_rules",
            "evidence": {"text": "x" * 20000},
        }
    ]
    del plan["tasks"]
    episode = capture_episode(**inputs)
    assert episode is not None
    summary = episode["retrieval_plan"]
    assert summary["version"] == plan["version"]
    assert summary["plan_sha256"] == episodes._hash(plan)
    assert summary["tasks"] == [
        {
            "id": "task-1",
            "kind": "rules",
            "coverage": "uncovered",
            "evidence_class": "official_rules",
        }
    ]
    assert len(json.dumps(summary)) < 1000


def test_escaped_narrative_still_obeys_serialized_size_cap(prepared):
    inputs = long_review_capture(prepared)
    inputs["claim_parts"] = ["Lead", *["Incoming 0: " + '\\"雪' * 2000] * 6]
    episode = capture_episode(**inputs)
    assert episode is not None
    assert len(json.dumps(episode)) <= 12000
    assert all(h["claim_truncated"] for h in episode["hypotheses"])


def seed(prepared, monkeypatch):
    _svc, path, owner, deck, document, _alts = prepared
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET oracle_text='{T}: Add {G}.',mana_cost='{G}' WHERE id='incoming-0'"
        )
        conn.commit()
        card = dict(
            conn.execute("SELECT * FROM cards WHERE id='incoming-0'").fetchone()
        )
    episode = capture_episode(
        document,
        "find mana creatures",
        {"incoming-0": card},
        {"groups": [{"card_ids": ["incoming-0"]}]},
        {
            "claim_judgments": [
                {"claim_id": "c0", "verdict": "unverified", "reason": "Missing setup."}
            ]
        },
        "p1",
        db_path=path,
    )
    assert episode
    store = AssistantStore(path)
    store.get(owner, deck)
    store._update(
        owner,
        deck,
        lambda p: p.update(
            messages=[{"role": "assistant", "research": {"reviewed_episode": episode}}]
        ),
    )
    return path, owner, deck, document, episode


def test_recalled_episode_is_a_rechecked_lead_not_verified_advice(
    prepared, monkeypatch
):
    path, owner, deck, document, _ep = seed(prepared, monkeypatch)
    result = recall_episodes(
        path, owner, deck, document, query="mana", prompt_version="p1"
    )
    assert result["status"] == "available"
    item = result["items"][0]
    assert item["current_catalog_facts_rechecked"]
    assert item["hypotheses"][0]["verdict"] == "unverified"
    assert (
        item["rules_freshness"] == item["strategic_validity"] == "requires_revalidation"
    )
    assert "not_expert" in item["review_kind"]


@pytest.mark.parametrize("verdict", ["supported", "contradicted"])
def test_legacy_truncated_receipts_remain_non_authoritative_leads(
    prepared, monkeypatch, verdict
):
    path, owner, deck, document, episode = seed(prepared, monkeypatch)
    hypothesis = episode["hypotheses"][0]
    hypothesis.pop("claim_sha256")  # The original full text cannot be recovered.
    hypothesis.pop("reviewed_verdict")
    hypothesis.update(
        claim="An old excerpt",
        claim_truncated=True,
        verdict=verdict,
        unverified_remainder=False,
    )
    AssistantStore(path)._update(
        owner,
        deck,
        lambda p: p.update(
            messages=[{"role": "assistant", "research": {"reviewed_episode": episode}}]
        ),
    )
    item = recall_episodes(path, owner, deck, document)["items"][0]
    recalled = item["hypotheses"][0]
    assert recalled["reviewed_verdict"] == verdict
    assert recalled["verdict"] == "unverified"
    assert recalled["unverified_remainder"]
    assert "claim_sha256" not in recalled


@pytest.mark.parametrize("change", ["oracle", "deck", "prompt", "age", "owner"])
@pytest.mark.parametrize("compacted", [False, True])
def test_stale_or_foreign_episode_cannot_be_reused(
    prepared, monkeypatch, change, compacted
):
    path, owner, deck, document, ep = seed(prepared, monkeypatch)
    if compacted:
        ep = capture_episode(**long_review_capture(prepared))
        assert ep and ep["narrative_compacted"]
        AssistantStore(path)._update(
            owner,
            deck,
            lambda p: p.update(
                messages=[{"role": "assistant", "research": {"reviewed_episode": ep}}]
            ),
        )
        assert recall_episodes(path, owner, deck, document)["items"]
    version = "p1"
    if change == "oracle":
        with db.connect(path) as conn:
            conn.execute(
                "UPDATE cards SET oracle_text='Draw a card.' WHERE id='incoming-0'"
            )
            conn.commit()
    elif change == "deck":
        document = copy.deepcopy(document)
        document["entries"][0]["quantity"] += 1
    elif change == "prompt":
        version = "p2"
    elif change == "age":
        ep["created_at"] = "2000-01-01T00:00:00+00:00"
        AssistantStore(path)._update(
            owner,
            deck,
            lambda p: p.update(
                messages=[{"role": "assistant", "research": {"reviewed_episode": ep}}]
            ),
        )
    else:
        owner = "foreign"
    result = recall_episodes(path, owner, deck, document, prompt_version=version)
    assert result["items"] == []
    if change == "owner":
        assert result["status"] == "not_found"


def test_unreviewed_selection_cannot_seed_memory():
    assert capture_episode({}, "q", {}, {"groups": []}, None, "p1") is None


def test_deck_evidence_identity_ignores_title_but_retains_mechanics():
    document = {
        "entries": [
            {
                "id": "x",
                "card_id": "x",
                "oracle_id": "o",
                "name": "X",
                "quantity": 1,
                "zone_name": "Library",
                "oracle_text": "Draw a card.",
            }
        ]
    }
    original = deck_evidence_hash(document)
    document["title"] = "New title"
    assert deck_evidence_hash(document) == original
    document["entries"][0]["oracle_text"] = "Discard a card."
    assert deck_evidence_hash(document) != original


def test_duplicate_physical_entries_and_face_power_changes_invalidate_hash():
    original = {
        "entries": [
            {
                "id": "a",
                "oracle_id": "o",
                "name": "A",
                "quantity": 1,
                "zone_name": "Library",
                "oracle_text": "Draw a card.",
                "power": "1",
                "toughness": "1",
            }
        ]
    }
    fingerprint = deck_evidence_hash(original)
    duplicate = copy.deepcopy(original)
    duplicate["entries"].append({**duplicate["entries"][0], "id": "b"})
    assert deck_evidence_hash(duplicate) != fingerprint
    for key, value in [
        ("power", "99"),
        ("toughness", "99"),
        ("card_faces", [{"oracle_text": "Add one mana."}]),
    ]:
        changed = copy.deepcopy(original)
        changed["entries"][0][key] = value
        assert deck_evidence_hash(changed) != fingerprint


def test_claim_bindings_distinguish_opposite_hypotheses_and_deduplicate(
    prepared, monkeypatch
):
    path, owner, deck, document, _ep = seed(prepared, monkeypatch)
    with db.connect(path) as conn:
        card = dict(
            conn.execute("SELECT * FROM cards WHERE id='incoming-0'").fetchone()
        )
    name = card["name"]

    def episode(claim):
        return capture_episode(
            document,
            "mana question",
            {"incoming-0": card},
            {"groups": [{"card_ids": ["incoming-0"]}]},
            {
                "claim_judgments": [
                    {
                        "claim_id": "c0",
                        "verdict": "unverified",
                        "category": "rules_interaction",
                        "reason": "Check timing.",
                    }
                ]
            },
            "p1",
            db_path=path,
            claim_parts=["Lead", claim],
        )

    positive = episode(name + " provides repeatable mana.")
    negative = episode(name + " does not provide repeatable mana.")
    assert positive["investigation_sha256"] != negative["investigation_sha256"]
    store = AssistantStore(path)
    store._update(
        owner,
        deck,
        lambda p: p.update(
            messages=[
                {"role": "assistant", "research": {"reviewed_episode": x}}
                for x in [positive, negative, positive]
            ]
        ),
    )
    recalled = recall_episodes(path, owner, deck, document, prompt_version="p1")
    assert len(recalled["items"]) == 2
    claims = {x["hypotheses"][0]["claim"] for x in recalled["items"]}
    assert claims == {
        name + " provides repeatable mana.",
        name + " does not provide repeatable mana.",
    }
    assert all(
        x["hypotheses"][0]["card_ids"] == ["incoming-0"] for x in recalled["items"]
    )


def test_current_catalog_power_change_invalidates_episode(prepared, monkeypatch):
    path, owner, deck, document, _ep = seed(prepared, monkeypatch)
    assert recall_episodes(path, owner, deck, document)["items"]
    with db.connect(path) as conn:
        conn.execute("UPDATE cards SET power='99' WHERE id='incoming-0'")
        conn.commit()
    assert not recall_episodes(path, owner, deck, document)["items"]


def test_supporting_catalog_change_invalidates_saved_entry_evidence(
    prepared, monkeypatch
):
    path, owner, deck, document, _ep = seed(prepared, monkeypatch)
    assert recall_episodes(path, owner, deck, document)["items"]
    # Update a deck dependency's current catalog without refreshing saved entry text.
    provider = next(
        c for c in document["entries"] if c.get("card_id") and not c.get("is_commander")
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET oracle_text=? WHERE id=?", ("Flying", provider["card_id"])
        )
        conn.commit()
    assert recall_episodes(path, owner, deck, document)["items"] == []


def test_saved_entry_missing_stats_does_not_make_unchanged_catalog_episode_stale(
    prepared, monkeypatch
):
    _svc, path, _owner, _deck, document, _alts = prepared
    provider = next(
        c for c in document["entries"] if c.get("card_id") and not c.get("is_commander")
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET power='2',toughness='2' WHERE id=?",
            (provider["card_id"],),
        )
        conn.commit()
    path, owner, deck, document, _ep = seed(prepared, monkeypatch)
    result = recall_episodes(path, owner, deck, document)
    assert result["status"] == "available" and result["items"]


@pytest.mark.parametrize("compacted", [False, True])
@pytest.mark.parametrize("change", ["catalog", "snapshot"])
def test_catalog_and_snapshot_freshness_remain_independent(
    prepared, monkeypatch, tmp_path, compacted, change
):
    from sabermetrics.assistant_card_facts import import_snapshot

    _svc, path, _owner, _deck, document, _alts = prepared
    provider = next(
        c for c in document["entries"] if c.get("card_id") and not c.get("is_commander")
    )
    snapshot = tmp_path / "provider-snapshot.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "object": "card",
                    "id": provider["card_id"],
                    "oracle_id": provider["oracle_id"],
                    "name": provider["name"],
                    "type_line": provider["type_line"],
                    "oracle_text": "Create a 1/1 Soldier creature token.",
                }
            ]
        )
    )
    import_snapshot(
        path, snapshot, version="synthetic-provider.v1", observed_at="2026-10-20"
    )
    path, owner, deck, document, _ep = seed(prepared, monkeypatch)
    if compacted:
        episode = capture_episode(**long_review_capture(prepared))
        assert episode is not None
        AssistantStore(path)._update(
            owner,
            deck,
            lambda p: p.update(
                messages=[
                    {"role": "assistant", "research": {"reviewed_episode": episode}}
                ]
            ),
        )
    assert recall_episodes(path, owner, deck, document)["items"]
    if change == "catalog":
        with db.connect(path) as conn:
            conn.execute(
                "UPDATE cards SET oracle_text='Flying' WHERE id=?",
                (provider["card_id"],),
            )
            conn.commit()
    else:
        facts = json.loads(snapshot.read_text())
        facts[0]["oracle_text"] = "Flying"
        snapshot.write_text(json.dumps(facts))
        import_snapshot(
            path, snapshot, version="synthetic-provider.v2", observed_at="2026-10-20"
        )
    assert recall_episodes(path, owner, deck, document)["items"] == []
