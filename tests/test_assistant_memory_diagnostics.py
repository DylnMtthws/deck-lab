"""Capture, attachment, persistence and recall are different observable events."""

import copy

from sabermetrics import assistant_episodes as episodes
from sabermetrics.assistant_episodes import capture_episode_receipt, recall_episodes
from sabermetrics.assistant_store import AssistantStore
from tests.test_assistant_episodes import long_review_capture

pytest_plugins = ["tests.test_assistant_experiments"]


def test_remaining_capture_skip_branches_are_observable(
    prepared, monkeypatch, tmp_path
):
    base = long_review_capture(prepared)
    large = copy.deepcopy(base["document"])
    large["entries"] = [large["entries"][0]] * 401
    assert (
        capture_episode_receipt(**{**base, "document": large})["reason"]
        == "deck_entry_limit"
    )
    assert (
        capture_episode_receipt(**{**base, "db_path": tmp_path / "missing.db"})[
            "reason"
        ]
        == "catalog_recheck_failed"
    )
    with monkeypatch.context() as patch:
        patch.setattr(episodes, "_catalog_identities", lambda *_: {"missing": None})
        missing = capture_episode_receipt(**base)
        assert missing["reason"] == "dependency_evidence_unavailable"
        assert missing["missing_dependencies"] == 1
    with monkeypatch.context() as patch:
        patch.setattr(episodes, "MAX_EPISODE_CHARS", 100)
        oversized = capture_episode_receipt(**base)
        assert oversized["reason"] == "required_receipt_exceeds_budget"
        assert oversized["minimum_receipt_chars"] > oversized["maximum_episode_chars"]


def test_capture_skip_reasons_are_explicit_without_claim_or_credential_text(prepared):
    base = long_review_capture(prepared)
    for changes, reason in [
        ({"selection": None}, "missing_selection"),
        ({"review": None}, "missing_review_judgments"),
        ({"selection": {"groups": []}}, "no_selected_candidates"),
        ({"cards": {}}, "missing_selected_evidence"),
        ({"db_path": None}, "catalog_recheck_unavailable"),
    ]:
        receipt = capture_episode_receipt(**{**base, **changes})
        assert receipt["status"] == "skipped" and receipt["reason"] == reason
        assert receipt["attachment_status"] == "not_attached"
        assert receipt["persistence_status"] == "not_applicable"
        assert receipt["episode"] is None
        assert len(receipt["input_sha256"]) == 64
        assert "claim_parts" not in receipt and "cards" not in receipt


def test_capture_attachment_and_atomic_store_receipt_can_be_traced_through_recall(
    prepared,
):
    _svc, path, owner, deck, document, _alts = prepared
    receipt = capture_episode_receipt(**long_review_capture(prepared))
    episode = receipt.pop("episode")
    assert episode and receipt["status"] == "captured"
    assert receipt["persistence_status"] == "pending_store"
    store = AssistantStore(path)
    turn = store.begin(owner, deck, "draw", document["revision"])
    original = {
        "role": "assistant",
        "research": {"memory_capture": receipt, "reviewed_episode": episode},
    }
    saved = store.finish(owner, deck, turn["turn_id"], original)
    recorded = saved["messages"][-1]["research"]
    assert recorded["memory_capture"]["persistence_status"] == "persisted"
    assert (
        original["research"]["memory_capture"]["persistence_status"] == "pending_store"
    )
    recalled = recall_episodes(path, owner, deck, document, prompt_version="p1")
    assert recalled["status"] == "available"
    assert (
        recalled["items"][0]["investigation_sha256"] == receipt["investigation_sha256"]
    )
    # A stopped turn cannot publish a memory-success receipt.
    other = store.begin(owner, deck, "draw again", document["revision"])
    store.stop(owner, deck)
    before = copy.deepcopy(store.get(owner, deck))
    assert (
        store.finish(owner, deck, other["turn_id"], original)["messages"]
        == before["messages"]
    )
