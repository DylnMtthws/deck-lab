"""DYL-62: /build exposes a separate heart chip for favourite commanders.

Saved decks (star) and favourite commanders (heart) are two different
hierarchies.  /build owns the deck filters; the commander chip is a
cross-page link to the *existing* favourites surface, not a fourth filter.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import url_for

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sabermetrics" / "ui"
CSS_PATH = UI / "static" / "deck-lab.css"
LIBRARY_TEMPLATE = UI / "templates" / "deck_lab" / "library.html"

HEART = "♥"
COMMANDERS_HREF = "/favorites/commanders"


def _app(tmp_path, monkeypatch, **env: str):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "commanders-chip.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="chip@example.test",
        display_name="Chip",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','Mana text','["G","U"]',1,1,NULL)""")
        conn.commit()
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return app, client, path


def _seed_deck(client, title: str) -> str:
    created = client.post(
        "/build/new", json={"title": title, "commander_card_id": "kinnan"}
    )
    assert created.status_code == 201
    return str(created.get_json()["id"])


def _filter_nav(html: str) -> str:
    match = re.search(r'<nav class="dl-underline-tabs".*?</nav>', html, re.DOTALL)
    assert match, "the /build deck-filter nav is missing"
    return match.group(0)


def _commander_chip(html: str) -> str:
    match = re.search(rf'<a\b[^>]*href="{COMMANDERS_HREF}"[^>]*>', html)
    assert match, "/build is missing the favourite-commanders chip"
    return match.group(0)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\s{re.escape(name)}="([^"]*)"', tag)
    assert match, f"{name} missing from {tag}"
    return match.group(1)


# --- the surface the chip points at already exists -------------------------


def test_favourite_commanders_listing_route_already_exists(tmp_path, monkeypatch):
    app, client, _path = _app(tmp_path, monkeypatch)
    with app.test_request_context():
        assert url_for("main.favorite_commanders") == COMMANDERS_HREF
    response = client.get(COMMANDERS_HREF)
    # Research on (the redesign default) hands off to the research favourites
    # view; with research off the legacy grid renders directly.
    assert response.status_code in (200, 302)
    if response.status_code == 302:
        assert "favorites=1" in response.headers["Location"]


def test_chip_reuses_the_existing_route_and_adds_no_new_one(tmp_path, monkeypatch):
    app, _client, _path = _app(tmp_path, monkeypatch)
    rules = {str(rule) for rule in app.url_map.iter_rules()}
    assert COMMANDERS_HREF in rules
    assert not [
        r for r in rules if "commander" in r and r.startswith("/build")
    ], "the chip must not introduce a /build commanders route"


# --- the chip itself -------------------------------------------------------


def test_build_renders_a_commanders_chip(tmp_path, monkeypatch):
    _created, client, _path = _app(tmp_path, monkeypatch)
    page = client.get("/build").get_data(as_text=True)
    chip = _commander_chip(page)
    assert "commander" in _attr(chip, "aria-label").lower()


def test_chip_lives_outside_the_deck_filter_nav(tmp_path, monkeypatch):
    _created, client, _path = _app(tmp_path, monkeypatch)
    page = client.get("/build").get_data(as_text=True)
    assert COMMANDERS_HREF not in _filter_nav(
        page
    ), "the commanders chip is not a deck filter and must sit outside the nav"
    assert 'class="dl-tab-divider"' in page, "the two hierarchies need a visual break"


def test_chip_uses_a_heart_that_is_hidden_from_assistive_tech(tmp_path, monkeypatch):
    _created, client, _path = _app(tmp_path, monkeypatch)
    page = client.get("/build").get_data(as_text=True)
    chip_start = page.index(f'href="{COMMANDERS_HREF}"')
    chip_html = page[chip_start : page.index("</a>", chip_start)]
    assert HEART in chip_html, "the commanders chip must carry the heart"
    assert re.search(
        r'<span aria-hidden="true">♥</span>', chip_html
    ), "the heart glyph must stay aria-hidden"


def test_chip_accessible_name_distinguishes_commanders_from_decks(
    tmp_path, monkeypatch
):
    _created, client, _path = _app(tmp_path, monkeypatch)
    page = client.get("/build").get_data(as_text=True)
    name = _attr(_commander_chip(page), "aria-label").lower()
    assert "commander" in name
    assert "deck" not in name
    # WCAG 2.5.3: the accessible name contains the visible label.
    assert "commanders" in name


def test_chip_is_never_marked_current_on_build(tmp_path, monkeypatch):
    _created, client, _path = _app(tmp_path, monkeypatch)
    for url in ("/build", "/build?filter=favorites", "/build?filter=recent"):
        chip = _commander_chip(client.get(url).get_data(as_text=True))
        assert (
            "aria-current" not in chip
        ), f"{url}: the cross-page chip must not claim to be the current page"


def test_chip_is_not_styled_as_a_deck_filter_tab():
    library = LIBRARY_TEMPLATE.read_text()
    chip = re.search(r"<a\b[^>]*url_for\('main.favorite_commanders'\)[^>]*>", library)
    assert chip, "the chip should link via url_for, not a hardcoded path"
    assert "dl-underline-tabs" not in chip.group(0)
    css = CSS_PATH.read_text()
    assert ".dl-library-crosslink" in css, "the chip needs its own non-tab styling"
    assert ".dl-tab-divider" in css


def test_tooltip_primitive_is_defined_exactly_once():
    assert CSS_PATH.read_text().count("content: attr(data-dl-tip);") == 1


# --- the star deck filters keep working ------------------------------------


def test_deck_filters_still_round_trip_with_the_chip_present(tmp_path, monkeypatch):
    _created, client, _path = _app(tmp_path, monkeypatch)
    saved = _seed_deck(client, "Round trip")
    _seed_deck(client, "Not saved")
    client.post(f"/build/deck/{saved}/favorite")

    page = client.get("/build").get_data(as_text=True)
    assert "filter=favorites" in page
    assert _filter_nav(page).count('aria-current="page"') == 1

    filtered = client.get("/build?filter=favorites").get_data(as_text=True)
    assert "Round trip" in filtered
    assert "Not saved" not in filtered
    nav = _filter_nav(filtered)
    assert nav.count('aria-current="page"') == 1
    current = [a for a in re.findall(r"<a\b[^>]*>", nav) if "aria-current" in a]
    assert "filter=favorites" in current[0]

    recent = _filter_nav(client.get("/build?filter=recent").get_data(as_text=True))
    assert recent.count('aria-current="page"') == 1
