"""Generate a deck from the new-deck dialog, with job progress."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from html import unescape
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.cedh.domain import ROLES, LabRequest
from sabermetrics.cedh.factory import build_default_lab
from sabermetrics.cedh.lab import CedhDeckLab
from sabermetrics.cedh.packs import PackSummary
from sabermetrics.deck_documents import DeckDocumentRepo, map_candidate_role
from sabermetrics.ui import cedh_routes
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
GENERATE_JS = ROOT / "src/sabermetrics/ui/static/deck-lab-generate.js"
HARNESS = Path(__file__).with_name("generate_flow_harness.mjs")
FIXTURE_CARDS = ROOT / "fixtures/cedh/cards.json"
NO_PACK = "No strategy pack supports this commander yet."
STEP_LABELS = {
    "queued": "Queued",
    "running": "Building",
    "simulating": "Simulating",
    "explaining": "Explaining",
    "done": "Done",
}


class ImmediateExecutor:
    """Run a queued build before the response returns."""

    def submit(self, function, *args):
        function(*args)
        return object()


class HoldingExecutor:
    """Accept a build without starting it."""

    def submit(self, function, *args):
        return object()


def _client(tmp_path: Path, *, research: bool = False):
    path = tmp_path / "generate.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="generate@example.test",
        display_name="Generate",
        role="user",
        status="active",
    )
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_REDESIGN_ENABLED=True,
        DECK_LAB_BUILDER_ENABLED=True,
        DECK_LAB_RESEARCH_ENABLED=research,
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    return client, path, owner


def _insert_card(
    path: Path,
    card_id: str,
    name: str,
    *,
    oracle_id: str | None = None,
    commander: bool = True,
) -> None:
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                card_id,
                oracle_id or card_id,
                name,
                "{G}{U}",
                2,
                "Legendary Creature — Human",
                "",
                '["G","U"]',
                1 if commander else 0,
                1,
            ),
        )
        conn.commit()


def _seed_fixture_cards(path: Path) -> None:
    cards = json.loads(FIXTURE_CARDS.read_text(encoding="utf-8"))["cards"]
    rows = []
    for card in cards:
        type_line = str(card.get("type_line") or "")
        legendary = "Legendary" in type_line and "Creature" in type_line
        rows.append(
            (
                card["oracle_id"],
                card["oracle_id"],
                card["name"],
                card.get("mana_cost"),
                card.get("mana_value"),
                type_line,
                card.get("oracle_text"),
                json.dumps(card.get("color_identity") or []),
                1 if legendary else 0,
                1,
            )
        )
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


def _persist_fixture_candidate(path: Path, owner: str) -> tuple[str, dict]:
    lab, _modes = build_default_lab(db_path=str(path), user_id=owner)
    result = lab.run(LabRequest(pack_id="kinnan_basalt"))
    assert result.candidate is not None, result.unsupported_detail
    candidate = result.candidate
    db.CedhCandidatesRepo(path).save(
        candidate_id=candidate.candidate_id,
        owner_id=owner,
        pack_id=result.pack_id or "kinnan_basalt",
        commander_key=candidate.commander.key,
        commander_name=candidate.commander.display_name,
        deck_sha256=candidate.deck_sha256,
        candidate_json=candidate.to_json(),
        evidence_hash=candidate.provenance.evidence_hash,
        meta_available=candidate.provenance.meta_available,
        simulation_status=(
            result.simulation.status if result.simulation else "not_simulated"
        ),
    )
    stored = json.loads(candidate.to_json())
    return candidate.candidate_id, stored


def _document(path: Path, owner: str, deck_id: str) -> dict:
    return DeckDocumentRepo(path).get(owner, deck_id)


@lru_cache(maxsize=1)
def _harness() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the generate harness")
    result = subprocess.run(
        [node, str(HARNESS), str(GENERATE_JS)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "generate harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_new_deck_dialog_offers_empty_and_generate(tmp_path) -> None:
    client, _path, _owner = _client(tmp_path)
    html = client.get("/build").get_data(as_text=True)
    assert 'name="deck_start"' in html
    empty = re.search(
        r'<input type="radio" name="deck_start" value="empty" checked[^>]*>\s*Empty deck',
        html,
    )
    generate = re.search(
        r'<input type="radio" name="deck_start" value="generate"[^>]*>\s*'
        r"Generate from a strategy pack",
        html,
    )
    assert empty and generate
    assert 'name="title"' in html
    assert 'name="commander_card_id"' in html
    assert "data-generate-pack-slot" in html
    assert "<select" not in html
    assert 'createElement("select")' in GENERATE_JS.read_text()
    pack_select = _harness()["packSelect"]
    assert pack_select["tag"] == "SELECT"
    assert pack_select["label"] == "Strategy pack"
    assert "What should this deck focus on?" in html
    assert "data-generate-progress" in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    for status, label in STEP_LABELS.items():
        assert re.search(
            rf'data-generate-step="{status}"[^>]*>.*?{label}',
            html,
            re.DOTALL,
        )
    assert "deck-lab-generate.js" in html


def test_packs_endpoint_filters_by_commander_and_reports_unsupported(
    tmp_path, monkeypatch
) -> None:
    client, path, _owner = _client(tmp_path)
    _insert_card(path, "kinnan", "Kinnan, Bonder Prodigy")
    _insert_card(path, "atraxa", "Atraxa, Praetors' Voice")
    _insert_card(path, "najeela", "Najeela, the Blade-Blossom")

    listed = client.get("/api/generate/packs").get_json()
    assert listed["message"] is None
    assert any(
        pack["pack_id"] == "kinnan_basalt" and pack["supported"] is True
        for pack in listed["packs"]
    )
    assert all("supported" in pack for pack in listed["packs"])

    filtered = client.get("/api/generate/packs?commander=kinnan").get_json()
    assert [pack["pack_id"] for pack in filtered["packs"]] == ["kinnan_basalt"]
    assert filtered["message"] is None

    missing = client.get("/api/generate/packs?commander=atraxa").get_json()
    assert missing["packs"] == []
    assert missing["message"] == NO_PACK
    unknown = client.get("/api/generate/packs?commander=missing-card").get_json()
    assert unknown["packs"] == []
    assert unknown["message"] == NO_PACK

    original = CedhDeckLab.pack_summaries

    def with_unsupported(self):
        return list(original(self)) + [
            PackSummary(
                pack_id="najeela_unsupported",
                name="Najeela",
                commander_names=("Najeela, the Blade-Blossom",),
                summary="An unsupported commander stays visible.",
                supported=False,
                detail="This commander is unsupported.",
            )
        ]

    monkeypatch.setattr(CedhDeckLab, "pack_summaries", with_unsupported)
    shown = client.get("/api/generate/packs?commander=najeela").get_json()
    assert shown["message"] is None
    assert len(shown["packs"]) == 1
    assert shown["packs"][0]["supported"] is False
    assert shown["packs"][0]["pack_id"] == "najeela_unsupported"
    assert "unsupported" in shown["packs"][0]["detail"].casefold()
    still_hidden = client.get("/api/generate/packs?commander=kinnan").get_json()
    assert [pack["pack_id"] for pack in still_hidden["packs"]] == ["kinnan_basalt"]


def test_commander_page_has_generate_button_with_preselected_commander(
    tmp_path,
) -> None:
    client, path, _owner = _client(tmp_path, research=True)
    _insert_card(path, "kinnan", "Kinnan, Bonder Prodigy")
    page = client.get("/research/commander/kinnan")
    assert page.status_code == 200
    html = unescape(page.get_data(as_text=True))
    match = re.search(r'<a class="dl-button" href="([^"]+)">Generate a list</a>', html)
    assert match, html
    href = match.group(1)
    assert "generate=1" in href
    assert "commander=kinnan" in href
    assert "Kinnan" in href


def test_import_candidate_yields_100_cards_with_mapped_roles(tmp_path) -> None:
    _client_unused, path, owner = _client(tmp_path)
    del _client_unused
    _seed_fixture_cards(path)
    candidate_id, stored = _persist_fixture_candidate(path, owner)
    assert isinstance(stored.get("cards"), list)
    assert sum(card["quantity"] for card in stored["cards"]) == 99
    deck_id = DeckDocumentRepo(path).import_candidate(owner, candidate_id)
    document = _document(path, owner, deck_id)
    library = [entry for entry in document["entries"] if not entry["is_commander"]]
    commanders = [entry for entry in document["entries"] if entry["is_commander"]]
    assert sum(entry["quantity"] for entry in library) == 99
    assert sum(entry["quantity"] for entry in commanders) == len(
        stored["commander"]["oracle_ids"]
    )
    assert sum(entry["quantity"] for entry in document["entries"]) == 100
    by_oracle = {entry["oracle_id"]: entry for entry in library}
    for card in stored["cards"]:
        entry = by_oracle[card["oracle_id"]]
        assert entry["quantity"] == card["quantity"]
        assert entry["role"] == map_candidate_role(card["role"])


def test_import_candidate_is_idempotent(tmp_path) -> None:
    client, path, owner = _client(tmp_path)
    _seed_fixture_cards(path)
    candidate_id, _stored = _persist_fixture_candidate(path, owner)
    repo = DeckDocumentRepo(path)
    first = repo.import_candidate(owner, candidate_id)
    second = repo.import_candidate(owner, candidate_id)
    assert first == second
    with db.connect(path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM deck_documents WHERE owner_id=? AND source_id=?",
            (owner, candidate_id),
        ).fetchone()[0]
    assert count == 1
    again = client.post(f"/build/import/candidate/{candidate_id}", json={})
    repeat = client.post(f"/build/import/candidate/{candidate_id}", json={})
    assert again.status_code == 200
    assert again.get_json()["id"] == first
    assert repeat.get_json()["id"] == first
    assert repeat.get_json()["url"].endswith(f"/build/deck/{first}")


def test_role_mapping_covers_every_cedh_role() -> None:
    expected = {
        "acceleration": "ramp",
        "tutor": "tutor",
        "interaction": "removal",
        "protection": "protection",
        "card_advantage": "draw",
        "win_package": "wincon",
        "land": "land",
        "flex": "other",
    }
    assert set(expected) == set(ROLES)
    for role in ROLES:
        assert map_candidate_role(role) == expected[role]
    assert map_candidate_role("not-a-role") == "other"
    assert map_candidate_role(None) == "other"


def test_progress_view_maps_each_job_status() -> None:
    snapshots = _harness()["progress"]
    assert [item["status"] for item in snapshots] == list(STEP_LABELS)
    order = list(STEP_LABELS)
    for index, item in enumerate(snapshots):
        steps = item["steps"]
        for status, label in STEP_LABELS.items():
            assert steps[status]["label"] == label
            position = order.index(status)
            if position < index:
                assert steps[status]["state"] == "complete"
                assert steps[status]["mark"] == "✓"
                assert steps[status]["current"] is None
            elif position == index:
                assert steps[status]["state"] == "current"
                assert steps[status]["current"] == "step"
                assert steps[status]["mark"] == ""
            else:
                assert steps[status]["state"] == "upcoming"


def test_polling_backoff_and_timeout_message() -> None:
    timeout = _harness()["timeout"]
    delays = timeout["delays"]
    assert delays[0] == 1000
    assert delays[1] == 2000
    assert delays[2] == 4000
    assert all(delay <= 4000 for delay in delays)
    waited = 0
    for index, delay in enumerate(delays):
        waited += delay
        if index < len(delays) - 1:
            assert waited < 300_000
    assert waited >= 300_000
    assert timeout["statusFetches"] == len(delays) - 1
    assert timeout["timeoutVisible"] is True
    assert timeout["spinnerHidden"] is True
    assert "timed out after 5 minutes" in timeout["timeoutText"]
    assert timeout["link"] == "/lab/build/job-1"
    assert "Open the job page" in timeout["timeoutText"]


def test_failed_job_shows_error_and_retry() -> None:
    failed = _harness()["failed"]
    assert failed["errorHidden"] is False
    assert "The build failed." in failed["errorText"]
    assert "The fixture corpus was unavailable." in failed["errorText"]
    assert failed["retryVisible"] is True
    assert failed["spinnerHidden"] is True
    assert failed["postsBefore"] == 1
    assert failed["postsAfter"] == 2
    assert failed["delaysAfterRetry"] == failed["delaysBeforeRetry"] + 1


def test_done_job_imports_candidate_and_navigates() -> None:
    done = _harness()["done"]
    assert done["url"] == "/build/import/candidate/cand-42"
    assert done["method"] == "POST"
    assert done["csrf"] == "csrf-test"
    assert done["contentType"] == "application/json"
    assert done["body"] == "{}"
    assert done["assigned"] == ["/build/deck/deck-9"]


def test_end_to_end_fixture_generation_to_builder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", ImmediateExecutor())
    client, path, _owner = _client(tmp_path)
    _seed_fixture_cards(path)
    response = client.post("/lab/build", json={"pack_id": "kinnan_basalt"})
    assert response.status_code == 202, response.get_data(as_text=True)
    status_url = response.get_json()["status_url"]
    job = client.get(f"{status_url}.json").get_json()
    assert job["status"] == "done", job
    assert "steps" not in job
    imported = client.post(
        f"/build/import/candidate/{job['candidate_id']}",
        json={},
    )
    assert imported.status_code == 200
    deck_id = imported.get_json()["id"]
    page = client.get(f"/build/deck/{deck_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    match = re.search(
        r'<script id="deck-document-data" type="application/json">(.*?)</script>',
        html,
    )
    assert match
    document = json.loads(match.group(1))
    assert sum(entry["quantity"] for entry in document["entries"]) == 100
    assert document["validation"]["total_count"] == 100


def test_generate_disabled_with_message_when_no_pack_matches() -> None:
    empty = _harness()["emptyPacks"]
    assert empty["selectDisabled"] is True
    assert empty["buttonDisabled"] is True
    assert empty["messageHidden"] is False
    assert empty["messageText"] == NO_PACK
    supported = _harness()["supportedPack"]
    assert supported["selectDisabled"] is False
    assert supported["buttonDisabled"] is False


def test_legacy_lab_routes_unchanged(tmp_path, monkeypatch) -> None:
    """Legacy /lab HTML and JSON stay on the pre-generate contract.

    Relies on the same route surface as tests/test_cedh_ui.py
    (TestLabIndex, TestBuild, TestCandidateExport) and
    tests/test_build_jobs.py::TestLifecycle. Gate 1 re-runs those modules.
    """
    monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", HoldingExecutor())
    client, _path, _owner = _client(tmp_path)
    client.application.config["DECK_LAB_BUILDER_ENABLED"] = False
    lab = client.get("/lab/")
    assert lab.status_code == 200
    body = lab.get_data(as_text=True)
    assert "Kinnan" in body
    assert "supported" in body
    assert "synthetic fixture" in body
    assert 'name="pack_id"' in body
    assert 'name="budget_usd"' not in body
    assert "No budget setting, on purpose" in body

    queued = client.post("/lab/build", data={"pack_id": "kinnan_basalt"})
    assert queued.status_code == 303
    job_url = queued.headers["Location"]
    payload = client.get(f"{job_url}.json").get_json()
    assert payload["status"] == "queued"
    assert "steps" not in payload
    page = client.get(job_url)
    assert page.status_code == 200
    job_html = page.get_data(as_text=True)
    assert 'http-equiv="refresh"' in job_html
    assert "This page refreshes while the build runs." in job_html

    client.application.config["DECK_LAB_BUILDER_ENABLED"] = True
    home = client.get("/lab/")
    assert home.status_code == 302
    assert home.headers["Location"].endswith("/build")
    kept = client.get("/lab/?generator=1")
    assert kept.status_code == 200
    assert 'name="pack_id"' in kept.get_data(as_text=True)
    assert "deck-lab-generate.js" not in kept.get_data(as_text=True)
