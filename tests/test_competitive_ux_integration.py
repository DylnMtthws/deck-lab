"""Cross-feature integration checks for the competitive-UX program.

Each task (T00-T14) has its own focused tests. This module checks what no single
task can: that the merged builder page loads every module as a real script
element in a working order, that owner-only modules stay off shared pages, and
that the features work together on one generated deck end to end.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from sabermetrics import db
from sabermetrics.ui import builder_routes, cedh_routes
from tests.test_generate_in_new_deck import (
    ImmediateExecutor,
    _client,
    _seed_fixture_cards,
)

# Loaded on every builder page, in this relative order.
SHARED_MODULES = (
    "deck-lab-mana.js",
    "deck-lab-builder.js",
    "deck-lab-card-panel.js",
    "deck-lab-status.js",
    "deck-lab-stats.js",
    "deck-lab-evidence.js",
    "deck-lab-simulate.js",
)
# Loaded only for the owner.
OWNER_ONLY_MODULES = (
    "deck-lab-history.js",
    "deck-lab-hotkeys.js",
    "deck-lab-considering.js",
    "deck-lab-feedback.js",
    "deck-lab-meta-compare.js",
)


def _script_positions(html: str) -> dict[str, int]:
    found = {}
    for match in re.finditer(
        r'<script src="[^"]*/(deck-lab-[a-z-]+\.js)" defer></script>', html
    ):
        found.setdefault(match.group(1), match.start())
    return found


def _document(html: str) -> dict:
    match = re.search(
        r'<script id="deck-document-data" type="application/json">(.*?)</script>', html
    )
    assert match, "builder page has no embedded deck document"
    return json.loads(match.group(1))


def _generated_deck(tmp_path, monkeypatch):
    monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", ImmediateExecutor())
    monkeypatch.setattr(builder_routes, "_SIM_EXECUTOR", ImmediateExecutor())
    client, path, owner = _client(tmp_path, research=True)
    _seed_fixture_cards(path)
    queued = client.post("/lab/build", json={"pack_id": "kinnan_basalt"})
    assert queued.status_code == 202, queued.get_data(as_text=True)
    job = client.get(queued.get_json()["status_url"] + ".json").get_json()
    assert job["status"] == "done", job
    imported = client.post(f"/build/import/candidate/{job['candidate_id']}", json={})
    assert imported.status_code == 200
    return client, path, owner, imported.get_json()["id"]


def test_owner_builder_page_loads_every_module_as_a_real_script(tmp_path, monkeypatch):
    client, _path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    html = client.get(f"/build/deck/{deck_id}").get_data(as_text=True)
    assert "&lt;script" not in html, "a script tag was autoescaped into text"
    positions = _script_positions(html)
    for module in SHARED_MODULES + OWNER_ONLY_MODULES:
        assert module in positions, f"{module} is not loaded as a script element"
    assert positions["deck-lab-mana.js"] < positions["deck-lab-builder.js"]
    for module in SHARED_MODULES[2:] + OWNER_ONLY_MODULES:
        assert positions["deck-lab-builder.js"] < positions[module], module
    assert positions["deck-lab-card-panel.js"] < positions["deck-lab-evidence.js"]
    for marker in (
        "data-card-panel",
        "data-status-bar",
        'id="card-image-dialog"',
        "data-undo",
        "data-redo",
        "data-hotkeys-help",
        '<option value="role">',
        "data-export-copy-archidekt",
        "export.txt?format=plain",
    ):
        assert marker in html, marker


def test_shared_page_keeps_read_only_modules_and_drops_owner_modules(
    tmp_path, monkeypatch
):
    client, _path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    share_url = client.post(f"/api/decks/{deck_id}/share").get_json()["url"]
    html = client.get(urlparse(share_url).path).get_data(as_text=True)
    positions = _script_positions(html)
    for module in OWNER_ONLY_MODULES:
        assert module not in positions, f"{module} must not load on shared pages"
    for module in ("deck-lab-card-panel.js", "deck-lab-status.js"):
        assert module in positions, module
    assert 'id="card-image-dialog"' in html
    assert "data-undo" not in html


def test_generated_deck_is_complete_roled_and_exports_cleanly(tmp_path, monkeypatch):
    client, _path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    document = _document(client.get(f"/build/deck/{deck_id}").get_data(as_text=True))
    assert document["validation"]["total_count"] == 100
    library = [e for e in document["entries"] if not e["is_commander"]]
    assert library and all(e.get("role") for e in library), "imported cards lack roles"

    plain = client.get(f"/build/deck/{deck_id}/export.txt?format=plain").get_data(
        as_text=True
    )
    assert sum(int(line.split(" ", 1)[0]) for line in plain.splitlines()) == 100
    archidekt = client.get(f"/build/deck/{deck_id}/export.txt?format=archidekt")
    assert "[Commander]" in archidekt.get_data(as_text=True)


def test_considering_batch_view_and_simulation_absence_work_together(
    tmp_path, monkeypatch
):
    client, _path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    document = _document(client.get(f"/build/deck/{deck_id}").get_data(as_text=True))
    movable = [e["id"] for e in document["entries"] if not e["is_commander"]][:3]
    # The exact batch shape deck-lab-considering.js sends.
    commands = [
        {"type": "create_zone", "zone_id": "zone-consider-1", "name": "Considering"}
    ]
    commands += [
        {
            "type": "move_entry",
            "entry_id": entry_id,
            "zone_id": "zone-consider-1",
            "sort_order": 999 + i,
        }
        for i, entry_id in enumerate(movable)
    ]
    saved = client.post(
        f"/api/decks/{deck_id}/commands",
        json={
            "mutation_id": "smoke-1",
            "expected_revision": document["revision"],
            "commands": commands,
        },
    )
    assert saved.status_code == 200, saved.get_data(as_text=True)
    after = saved.get_json()
    assert after["validation"]["total_count"] == 97

    plain = client.get(f"/build/deck/{deck_id}/export.txt?format=plain").get_data(
        as_text=True
    )
    assert sum(int(line.split(" ", 1)[0]) for line in plain.splitlines()) == 97

    view = client.post(
        f"/api/decks/{deck_id}/commands",
        json={
            "mutation_id": "smoke-2",
            "expected_revision": after["revision"],
            "commands": [{"type": "update_view", "group_mode": "role"}],
        },
    )
    assert view.status_code == 200

    queued = client.post(f"/api/decks/{deck_id}/simulate")
    assert queued.status_code == 202
    latest = client.get(f"/api/decks/{deck_id}/simulations/latest").get_json()
    assert "Not simulated" in json.dumps(latest), latest


def test_evidence_reports_absence_instead_of_zero(tmp_path, monkeypatch):
    client, _path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    evidence = client.get(f"/api/decks/{deck_id}/evidence").get_json()
    assert evidence["window_days"] == 30
    if not evidence["available"]:
        assert evidence["cards"] == {}
    for fact in evidence["cards"].values():
        assert fact["lists"] > 0 and evidence["denominator"] > 0


def test_research_syntax_applies_on_cards_tab(tmp_path, monkeypatch):
    client, _path, _owner, _deck_id = _generated_deck(tmp_path, monkeypatch)
    page = client.get("/research/?tab=cards&q=t:instant mv<=1")
    assert page.status_code == 200
    assert 'data-syntax-term="t:instant"' in page.get_data(as_text=True)


def test_builder_feedback_roundtrips_and_survives_deck_deletion(tmp_path, monkeypatch):
    client, path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    document = _document(client.get(f"/build/deck/{deck_id}").get_data(as_text=True))
    card = next(
        e for e in document["entries"] if not e["is_commander"] and e.get("oracle_id")
    )
    voted = client.put(
        f"/api/decks/{deck_id}/feedback/cards/{card['oracle_id']}",
        json={"card_name": card["name"], "vote": "up", "comment": "Core piece"},
    )
    assert voted.status_code == 200, voted.get_data(as_text=True)
    verdict = client.put(
        f"/api/decks/{deck_id}/feedback/deck", json={"verdict": "good", "comment": ""}
    )
    assert verdict.status_code == 200
    stored = client.get(f"/api/decks/{deck_id}/feedback").get_json()
    assert stored["cards"][card["oracle_id"]]["vote"] == "up"
    assert stored["deck"]["verdict"] == "good"

    deleted = client.post(f"/build/deck/{deck_id}/delete")
    assert deleted.status_code in (200, 302, 303)
    with db.connect(path) as conn:
        cards = conn.execute(
            "SELECT card_name FROM deck_document_card_feedback WHERE deck_id=?",
            (deck_id,),
        ).fetchall()
        decks = conn.execute(
            "SELECT verdict FROM deck_document_feedback WHERE deck_id=?", (deck_id,)
        ).fetchall()
    assert [row["card_name"] for row in cards] == [card["name"]]
    assert [row["verdict"] for row in decks] == ["good"]


def test_meta_compare_endpoints_state_their_sample(tmp_path, monkeypatch):
    client, _path, _owner, deck_id = _generated_deck(tmp_path, monkeypatch)
    diff = client.get(f"/api/decks/{deck_id}/meta-diff")
    assert diff.status_code == 200
    payload = diff.get_json()
    for key in ("denominator", "window_days", "min_event_size"):
        assert key in payload, key
    document = _document(client.get(f"/build/deck/{deck_id}").get_data(as_text=True))
    entry = next(
        e for e in document["entries"] if not e["is_commander"] and e.get("oracle_id")
    )
    alternatives = client.get(f"/api/decks/{deck_id}/alternatives/{entry['oracle_id']}")
    assert alternatives.status_code == 200
