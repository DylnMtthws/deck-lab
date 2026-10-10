"""Legacy diagnostics cannot launch the retired runner."""

from sabermetrics import db
from tests.test_resimulate_delta import _client, _seed_deck


def setup(tmp_path):
    client, path, owner = _client(tmp_path)
    client.application.config["DECK_LAB_GOLDFISH_ENABLED"] = True
    return client, path, owner, _seed_deck(path, owner)


def test_old_pages_redirect_into_build(tmp_path):
    client, _, _, deck = setup(tmp_path)
    assert client.get("/goldfish").location == "/build"
    result = client.get(f"/build/deck/{deck}/diagnostics")
    assert result.status_code == 302
    assert result.location == f"/build/deck/{deck}#goldfish"


def test_retired_runner_refuses_submission(tmp_path):
    client, _, _, deck = setup(tmp_path)
    result = client.post(f"/api/decks/{deck}/diagnostics", json={})
    assert result.status_code == 410
    assert result.json["simulator_url"] == f"/api/decks/{deck}/simulate"


def test_archived_history_and_redirect_are_owner_only(tmp_path):
    client, path, _, deck = setup(tmp_path)
    other = db.UsersRepo(path).create(
        email="other@example.test", display_name="Other", role="user", status="active"
    )
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.get(f"/build/deck/{deck}/diagnostics").status_code == 404
    assert client.get(f"/api/decks/{deck}/diagnostics").status_code == 404
    assert client.post(f"/api/decks/{deck}/diagnostics", json={}).status_code == 404


def test_retired_routes_keep_flag_and_csrf_gates(tmp_path):
    client, _, _, deck = setup(tmp_path)
    client.application.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/api/decks/{deck}/diagnostics", json={}).status_code == 400
    client.application.config["DECK_LAB_GOLDFISH_ENABLED"] = False
    assert client.get("/goldfish").status_code == 404
