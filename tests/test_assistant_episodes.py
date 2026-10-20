"""Reviewed leads must never outlive their source facts or owner scope."""

import copy

import pytest

from sabermetrics import db
from sabermetrics.assistant_episodes import (
    capture_episode,
    deck_evidence_hash,
    recall_episodes,
)
from sabermetrics.assistant_store import AssistantStore

pytest_plugins = ["tests.test_assistant_experiments"]


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


@pytest.mark.parametrize("change", ["oracle", "deck", "prompt", "age", "owner"])
def test_stale_or_foreign_episode_cannot_be_reused(prepared, monkeypatch, change):
    path, owner, deck, document, ep = seed(prepared, monkeypatch)
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


def test_unchanged_enriched_snapshot_cannot_mask_catalog_provider_changes(
    prepared, monkeypatch, tmp_path
):
    import json

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
    assert recall_episodes(path, owner, deck, document)["items"]
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET oracle_text='Flying' WHERE id=?", (provider["card_id"],)
        )
        conn.commit()
    assert recall_episodes(path, owner, deck, document)["items"] == []
