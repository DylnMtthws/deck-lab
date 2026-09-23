"""Bare /research/ opens the Cards tab on the server and in the client."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

STATIC = Path(__file__).parents[1] / "src/sabermetrics/ui/static"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "default-tab.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="cards-tab@example.test",
        display_name="Cards Tab",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES('kinnan','oracle','Kinnan',2,'Legendary Creature','["G","U"]',1,1)"""
        )
        conn.commit()
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False, WTF_CSRF_ENABLED=False)
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return test_client


def test_bare_research_renders_cards_tab_current(client):
    page = client.get("/research/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-tab="cards"' in html
    assert 'aria-current="page">Cards</a>' in html
    assert 'aria-current="page">Commanders</a>' not in html
    assert 'name="tab" value="cards"' in html
    unknown = client.get("/research/?tab=not-a-tab")
    assert 'data-tab="cards"' in unknown.get_data(as_text=True)
    assert 'aria-current="page">Cards</a>' in unknown.get_data(as_text=True)
    commanders = client.get("/research/?tab=commanders")
    assert 'data-tab="commanders"' in commanders.get_data(as_text=True)
    assert 'aria-current="page">Commanders</a>' in commanders.get_data(as_text=True)


def test_tab_key_from_url_defaults_to_cards():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute tabKeyFromUrl")
    harness = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const window = {};
const document = { querySelector() { return null; } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  window,
  document,
  URL,
  location: new URL('https://decklab.studio/research/')
});
const tabKeyFromUrl = window.tabKeyFromUrl;
assert.strictEqual(typeof tabKeyFromUrl, 'function');
assert.strictEqual(tabKeyFromUrl(new URL('https://decklab.studio/research/')), 'cards');
assert.strictEqual(
  tabKeyFromUrl(new URL('https://decklab.studio/research/?q=Kinnan')),
  'cards'
);
assert.strictEqual(
  tabKeyFromUrl(new URL('https://decklab.studio/research/?tab=nope')),
  'cards'
);
assert.strictEqual(
  tabKeyFromUrl(new URL('https://decklab.studio/research/?tab=commanders')),
  'commanders'
);
console.log(JSON.stringify({
  passed: true,
  defaultTab: tabKeyFromUrl(new URL('https://decklab.studio/research/'))
}));
"""
    result = subprocess.run(
        [node, "-e", harness, str(STATIC / "deck-lab-research.js")],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "tabKeyFromUrl harness failed")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["passed"] is True
    assert payload["defaultTab"] == "cards"
