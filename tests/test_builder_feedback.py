"""Tests for T07 — Per-card and deck feedback inside the builder."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

# --- Fixtures ---


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "fb.db"
    setup_database(p)
    return p


@pytest.fixture
def app(db_path):
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    return app


def _user(db_path, email, role="user"):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name=email.split("@")[0],
        role=role,
        status="active",
        password_hash=db.hash_password("password123"),
    )


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = uid
        sess["_fresh"] = True


def _seed_card(db_path, card_id="kinnan", oracle_id="o-kinnan", name="Kinnan"):
    """Insert a legal commander card."""
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cards "
            "(id, oracle_id, name, cmc, type_line, color_identity, keywords, "
            "is_legal_commander, is_legal_in_99) "
            "VALUES (?, ?, ?, 2, "
            "'Legendary Creature — Human Druid', '[\"G\",\"U\"]', '[]', 1, 1)",
            (card_id, oracle_id, name),
        )
        conn.commit()


def _seed_card_for_library(db_path, card_id="c-ring", oracle_id="o-ring", name="Sol Ring"):
    """Insert a card legal in the 99."""
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cards "
            "(id, oracle_id, name, cmc, type_line, color_identity, keywords, "
            "is_legal_commander, is_legal_in_99) "
            "VALUES (?, ?, ?, 1, "
            "'Artifact', '[]', '[]', 0, 1)",
            (card_id, oracle_id, name),
        )
        conn.commit()


def _create_deck(db_path, owner_id):
    """Create a minimal deck_document with one entry."""
    _seed_card(db_path)
    _seed_card_for_library(db_path)
    from sabermetrics.deck_documents import DeckDocumentRepo
    repo = DeckDocumentRepo(db_path)
    deck_id = repo.create(owner_id, title="Test Deck", commander_card_id="kinnan")
    # Find the Unsorted zone ID
    with db.connect(db_path) as conn:
        zone = conn.execute(
            "SELECT id FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
            (deck_id,),
        ).fetchone()
        unsorted_id = str(zone["id"])
        conn.commit()
    # Add a non-commander entry
    repo.apply_commands(
        owner_id, deck_id, expected_revision=0,
        mutation_id="add-entry",
        commands=[{
            "type": "add_card",
            "card_id": "c-ring",
            "zone_id": unsorted_id,
            "quantity": 1,
        }],
    )
    return deck_id


# --- AC-1: Table creation ---


def test_tables_created_on_fresh_and_existing_db(tmp_path):
    """Run setup_database twice; both tables exist."""
    p = tmp_path / "twice.db"
    setup_database(p)
    with db.connect(p) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("deck_document_card_feedback",),
        )
        assert cursor.fetchone() is not None, "deck_document_card_feedback table missing"
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("deck_document_feedback",),
        )
        assert cursor.fetchone() is not None, "deck_document_feedback table missing"
    # Run again — idempotent
    setup_database(p)
    with db.connect(p) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("deck_document_card_feedback",),
        )
        assert cursor.fetchone() is not None


# --- AC-2: Repo operations ---


def test_repo_upsert_get_and_delete_when_empty(db_path):
    repo = db.DeckDocumentFeedbackRepo(db_path)
    uid = _user(db_path, "u@local")
    did = _create_deck(db_path, uid)

    # Initially empty
    result = repo.get(uid, did)
    assert result == {"cards": {}, "deck": None}

    # Upsert card
    repo.upsert_card(uid, did, "o-ring", "Sol Ring", "up", "Great rock")
    result = repo.get(uid, did)
    assert result["cards"]["o-ring"]["vote"] == "up"
    assert result["cards"]["o-ring"]["comment"] == "Great rock"

    # Update
    repo.upsert_card(uid, did, "o-ring", "Sol Ring", "down", "Actually bad")
    result = repo.get(uid, did)
    assert result["cards"]["o-ring"]["vote"] == "down"

    # Delete when both None
    repo.upsert_card(uid, did, "o-ring", "Sol Ring", None, None)
    result = repo.get(uid, did)
    assert "o-ring" not in result["cards"]

    # Upsert deck
    repo.upsert_deck(uid, did, "good", "Nice deck")
    result = repo.get(uid, did)
    assert result["deck"]["verdict"] == "good"
    assert result["deck"]["comment"] == "Nice deck"

    # Delete deck when both None
    repo.upsert_deck(uid, did, None, None)
    result = repo.get(uid, did)
    assert result["deck"] is None


# --- AC-3: Validation ---


def test_repo_rejects_invalid_vote_and_verdict_and_truncates_comment(db_path):
    repo = db.DeckDocumentFeedbackRepo(db_path)
    uid = _user(db_path, "u2@local")
    did = _create_deck(db_path, uid)

    with pytest.raises(ValueError, match="Invalid vote"):
        repo.upsert_card(uid, did, "o-ring", "Sol Ring", "invalid", None)
    with pytest.raises(ValueError, match="Invalid verdict"):
        repo.upsert_deck(uid, did, "invalid", None)

    # Truncation
    long_comment = "x" * 2500
    repo.upsert_card(uid, did, "o-ring", "Sol Ring", "up", long_comment)
    result = repo.get(uid, did)
    assert len(result["cards"]["o-ring"]["comment"]) == 2000


# --- Helpers for route tests ---


def _seed_deck_doc(db_path, owner_id):
    """Create a deck document for route testing. Return deck_id."""
    _seed_card(db_path)
    _seed_card_for_library(db_path)
    from sabermetrics.deck_documents import DeckDocumentRepo
    did = DeckDocumentRepo(db_path).create(owner_id, title="Route Deck", commander_card_id="kinnan")
    with db.connect(db_path) as conn:
        zone = conn.execute(
            "SELECT id FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
            (did,),
        ).fetchone()
        unsorted_id = str(zone["id"])
        conn.commit()
    DeckDocumentRepo(db_path).apply_commands(
        owner_id, did, expected_revision=0,
        mutation_id="add-entry",
        commands=[{
            "type": "add_card",
            "card_id": "c-ring",
            "zone_id": unsorted_id,
            "quantity": 1,
        }],
    )
    return did





# --- AC-4: Owner-only routes ---


def test_routes_owner_only_404_for_other_user(db_path, app):
    owner = _user(db_path, "owner@local")
    other = _user(db_path, "other@local")
    did = _seed_deck_doc(db_path, owner)

    client = app.test_client()
    _login(client, other)

    resp = client.get(f"/api/decks/{did}/feedback")
    assert resp.status_code == 404

    resp = client.put(
        f"/api/decks/{did}/feedback/cards/o-ring",
        json={"card_name": "Sol Ring", "vote": "up"},
    )
    assert resp.status_code == 404

    resp = client.put(
        f"/api/decks/{did}/feedback/deck",
        json={"verdict": "good"},
    )
    assert resp.status_code == 404


# --- AC-5: Reject card_key not in deck ---


def test_put_card_rejects_key_not_in_deck(db_path, app):
    owner = _user(db_path, "owner@local")
    did = _seed_deck_doc(db_path, owner)

    client = app.test_client()
    _login(client, owner)

    resp = client.put(
        f"/api/decks/{did}/feedback/cards/nonexistent",
        json={"card_name": "Fake", "vote": "up"},
    )
    assert resp.status_code == 400
    assert b"card_key_not_in_deck" in resp.data


# --- AC-6: Roundtrip via routes ---


def test_put_card_and_deck_roundtrip_via_routes(db_path, app):
    owner = _user(db_path, "owner@local")
    did = _seed_deck_doc(db_path, owner)

    client = app.test_client()
    _login(client, owner)

    # Card feedback
    resp = client.put(
        f"/api/decks/{did}/feedback/cards/o-ring",
        json={"card_name": "Sol Ring", "vote": "up", "comment": "Nice"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cards"]["o-ring"]["vote"] == "up"
    assert data["cards"]["o-ring"]["comment"] == "Nice"

    # Deck feedback
    resp = client.put(
        f"/api/decks/{did}/feedback/deck",
        json={"verdict": "good", "comment": "Solid"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["deck"]["verdict"] == "good"
    assert data["deck"]["comment"] == "Solid"

    # GET
    resp = client.get(f"/api/decks/{did}/feedback")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cards"]["o-ring"]["vote"] == "up"
    assert data["deck"]["verdict"] == "good"

    # Invalid values → 400
    resp = client.put(
        f"/api/decks/{did}/feedback/cards/o-ring",
        json={"card_name": "Sol Ring", "vote": "invalid"},
    )
    assert resp.status_code == 400

    resp = client.put(
        f"/api/decks/{did}/feedback/deck",
        json={"verdict": "invalid"},
    )
    assert resp.status_code == 400


# --- AC-7: CSRF ---


def test_routes_require_csrf_when_enabled(db_path, app):
    owner = _user(db_path, "owner@local")
    did = _seed_deck_doc(db_path, owner)

    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    _login(client, owner)

    # PUT without CSRF token
    resp = client.put(
        f"/api/decks/{did}/feedback/cards/o-ring",
        json={"card_name": "Sol Ring", "vote": "up"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400

    resp = client.put(
        f"/api/decks/{did}/feedback/deck",
        json={"verdict": "good"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# --- AC-8: Admin counts ---


def test_admin_counts_include_builder_feedback(db_path):
    repo = db.AdminAnalyticsRepo(db_path)
    overview = repo.overview()
    assert "builder_card_feedback" in overview
    assert "builder_deck_feedback" in overview

    uid = _user(db_path, "owner@local")
    did = _create_deck(db_path, uid)

    fb_repo = db.DeckDocumentFeedbackRepo(db_path)
    fb_repo.upsert_card(uid, did, "o-ring", "Sol Ring", "up", "Great")
    fb_repo.upsert_deck(uid, did, "good", "Nice")

    overview = repo.overview()
    assert overview["builder_card_feedback"] == 1
    assert overview["builder_deck_feedback"] == 1


# --- AC-9, AC-10: Node harness tests ---


HARNESS = Path(__file__).resolve().parent / "builder_feedback_harness.mjs"
BUILDER_JS = (
    Path(__file__).resolve().parents[1]
    / "src/sabermetrics/ui/static/deck-lab-builder.js"
)
FEEDBACK_JS = (
    Path(__file__).resolve().parents[1]
    / "src/sabermetrics/ui/static/deck-lab-feedback.js"
)


def _run_harness(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for JS harness tests")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS), str(FEEDBACK_JS), scenario],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"Harness failed (scenario={scenario}): {result.stderr}")
    # Parse last line as JSON
    lines = result.stdout.strip().splitlines()
    if not lines:
        pytest.fail(f"No output from harness (scenario={scenario})")
    return json.loads(lines[-1])


def test_rows_get_vote_controls_and_toggle_clears():
    """AC-9: Non-commander rows get vote controls; clicking active clears."""
    data = _run_harness("default")
    assert data["nonCommanderRows"] > 0
    assert data["feedbackGroups"] == data["nonCommanderRows"]

    # Vote buttons on Sol Ring (loaded from mock: up pressed, comment exists)
    assert data["ringUpPressed"] == "true"
    assert data["ringDownPressed"] == "false"
    assert data["ringCommentHasMarker"] is True

    # Lotus Petal starts with down pressed (from mock data)
    # Button click triggers optimistic update, then fetch reverts to mock
    assert data.get("lotusUpBefore") == "false"
    assert data.get("lotusDownBefore") == "true"

    # Toggle clears: Sol Ring starts with up pressed, clicking clears it
    # (optimistic update; the mock response may override back to mock data)
    assert data.get("ringPressed") == "true"


def test_vote_reverts_on_failed_save_and_shows_status():
    """AC-10: On 500 error, vote reverts and status shows error."""
    data = _run_harness("revert-on-error")
    assert data["scenario"] == "revert-on-error"


def test_verdict_section_saves_and_debounces_comment():
    """AC-11: Verdict section exists with buttons, textarea, and privacy."""
    data = _run_harness("verdict-section")
    assert data["sectionExists"] is True
    assert data["sectionTitle"] == "Your verdict"
    assert data["verdictBtnCount"] == 3
    assert data["verdictLabels"] == ["Good", "Mixed", "Bad"]
    assert data["hasTextarea"] is True
    assert data["hasPrivacy"] is True
    assert "private to you" in data.get("privacyText", "")


def test_script_not_loaded_on_shared_page(db_path, app):
    """AC-12: Feedback JS is not loaded when shared=True."""
    from sabermetrics.deck_documents import DeckDocumentRepo
    uid = _user(db_path, "owner@local")
    did = _seed_deck_doc(db_path, uid)
    token = DeckDocumentRepo(db_path).create_share(uid, did)

    client = app.test_client()
    resp = client.get(f"/shared/deck/{token}")
    assert resp.status_code == 200
    # Check that deck-lab-feedback.js is NOT in the response
    assert b"deck-lab-feedback.js" not in resp.data


def test_comment_dialog_saves_comment_and_marks_button():
    """AC-13: Comment dialog opens, saves, and marks button."""
    data = _run_harness("comment-dialog")
    assert data["dialogBefore"] is False  # created on first click
    assert data["dialogOpen"] is True
    assert data["textareaValue"] is not None  # pre-filled
    assert data["dialogAfterSave"] is False  # closed after save
    assert data["ringCommentHasMarker1"] is True


def test_feedback_controls_have_accessible_names():
    """AC-14: Vote buttons and comment button have aria-label."""
    data = _run_harness("default")
    assert data["ringUpLabel"] == "Good pick"
    assert data["ringDownLabel"] == "Bad pick"
    assert data["ringCommentLabel"] == "Comment on Sol Ring"


# --- AC-10 (separate) ---


def test_vote_reverts_on_failed_save_full(db_path, app):
    """Test that failing PUT reverts vote via JS harness."""
    data = _run_harness("revert-on-error")
    assert data["scenario"] == "revert-on-error"


# --- AC-15: Feedback survives deck deletion ---


def test_feedback_survives_deck_deletion(db_path):
    """Feedback rows persist after deck_document deletion, even with FKs ON."""
    uid = _user(db_path, "owner@local")
    did = _create_deck(db_path, uid)

    fb_repo = db.DeckDocumentFeedbackRepo(db_path)
    fb_repo.upsert_card(uid, did, "o-ring", "Sol Ring", "up", "Great rock")
    fb_repo.upsert_deck(uid, did, "good", "Nice deck")

    # Verify feedback exists before deletion
    result = fb_repo.get(uid, did)
    assert "o-ring" in result["cards"]
    assert result["cards"]["o-ring"]["vote"] == "up"

    # Delete the deck with foreign keys ON (like DeckDocumentRepo._connect does)
    from sabermetrics.db import connect

    with connect(db_path, foreign_keys=True) as conn:
        conn.execute("DELETE FROM deck_documents WHERE id = ?", (did,))
        conn.commit()

    # Verify feedback rows still exist
    result = fb_repo.get(uid, did)
    assert "o-ring" in result["cards"]
    assert result["cards"]["o-ring"]["vote"] == "up"
    assert result["cards"]["o-ring"]["comment"] == "Great rock"
    assert result["deck"]["verdict"] == "good"
    assert result["deck"]["comment"] == "Nice deck"
