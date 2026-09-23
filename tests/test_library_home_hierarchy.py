"""Library and home hierarchy fixes: DYL-57, DYL-61, DYL-62, DYL-63."""

from __future__ import annotations

import re
from pathlib import Path

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sabermetrics" / "ui"
CSS_PATH = UI / "static" / "deck-lab.css"
TEMPLATES = UI / "templates" / "deck_lab"
DECK_CARD_TEMPLATE = TEMPLATES / "_deck_card.html"
LIBRARY_TEMPLATE = TEMPLATES / "library.html"
COMMANDER_TEMPLATE = TEMPLATES / "commander.html"
RESEARCH_FRAGMENT_TEMPLATE = TEMPLATES / "research_fragment.html"

HEART = "♥"
STAR = "★"


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, f"missing CSS rule for {selector}"
    return match.group(1)


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _app(tmp_path, monkeypatch, **env: str):
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "library-home.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="hierarchy@example.test",
        display_name="Hierarchy",
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
    _login(client, user)
    return client, path


def _seed_deck(client, title: str) -> str:
    created = client.post(
        "/build/new", json={"title": title, "commander_card_id": "kinnan"}
    )
    assert created.status_code == 201
    return str(created.get_json()["id"])


def _favorite_button(html: str) -> str:
    match = re.search(r"<button[^>]*dl-deck-favorite[^>]*>", html)
    assert match, "deck card is missing its saved-deck control"
    return match.group(0)


def _filter_nav(html: str) -> str:
    match = re.search(r'<nav class="dl-underline-tabs".*?</nav>', html, re.DOTALL)
    assert match, "the /build filter nav is missing"
    return match.group(0)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\s{re.escape(name)}="([^"]*)"', tag)
    assert match, f"{name} missing from {tag}"
    return match.group(1)


# --- DYL-57: home leads with the instructions -------------------------------


def test_home_places_how_it_goes_above_the_door_grid(tmp_path, monkeypatch) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    home = client.get("/").get_data(as_text=True)
    assert home.count('class="dl-how"') == 1
    assert home.count('class="dl-door-grid"') == 1
    hero = home.index('class="dl-home-hero"')
    how = home.index('class="dl-how"')
    doors = home.index('class="dl-door-grid"')
    assert hero < how < doors, "instructions must sit between the hero and the doors"


def test_home_resume_sits_between_instructions_and_doors(tmp_path, monkeypatch) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    _seed_deck(client, "Resume draft")
    home = client.get("/").get_data(as_text=True)
    how = home.index('class="dl-how"')
    resume = home.index("Pick up where you left off")
    doors = home.index('class="dl-door-grid"')
    assert how < resume < doors
    # The deck-card script stays last on the page.
    assert home.index("deck-lab-library.js") > doors


def test_home_reorder_preserves_every_flag_gate(tmp_path, monkeypatch) -> None:
    client, _path = _app(tmp_path, monkeypatch, SABER_DECK_LAB_RESEARCH="0")
    home = client.get("/").get_data(as_text=True)
    assert "Find out what is actually winning" not in home
    assert "Lay it out like it is on the table" in home
    assert 'class="dl-how"' in home
    assert "Pick up where you left off" not in home, "no decks yet"

    _seed_deck(client, "Gated draft")
    home = client.get("/").get_data(as_text=True)
    assert "Pick up where you left off" in home
    assert home.index('class="dl-how"') < home.index("Pick up where you left off")


def test_home_hides_builder_sections_when_the_builder_is_off(
    tmp_path, monkeypatch
) -> None:
    client, _path = _app(tmp_path, monkeypatch, SABER_DECK_LAB_BUILDER="0")
    home = client.get("/").get_data(as_text=True)
    assert "Lay it out like it is on the table" not in home
    assert "Find out what is actually winning" in home
    assert "Pick up where you left off" not in home
    assert home.index('class="dl-how"') < home.index('class="dl-door-grid"')


# --- DYL-61: the saved-deck control carries a real tooltip ------------------


def test_saved_deck_control_has_aria_label_and_tooltip_in_both_states(
    tmp_path, monkeypatch
) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    deck_id = _seed_deck(client, "Tooltip draft")

    unsaved = _favorite_button(client.get("/build").get_data(as_text=True))
    assert _attr(unsaved, "aria-label").strip()
    assert _attr(unsaved, "data-dl-tip").strip()
    assert _attr(unsaved, "title").strip()
    assert "active" not in _attr(unsaved, "class")

    toggled = client.post(f"/build/deck/{deck_id}/favorite")
    assert toggled.status_code == 302

    saved = _favorite_button(client.get("/build").get_data(as_text=True))
    assert _attr(saved, "aria-label").strip()
    assert _attr(saved, "data-dl-tip").strip()
    assert _attr(saved, "title").strip()
    assert "active" in _attr(saved, "class")
    assert _attr(saved, "data-dl-tip") != _attr(unsaved, "data-dl-tip")


def test_saved_deck_tooltip_matches_the_native_title(tmp_path, monkeypatch) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    _seed_deck(client, "Title draft")
    button = _favorite_button(client.get("/build").get_data(as_text=True))
    assert _attr(button, "data-dl-tip") == _attr(button, "title")


def test_build_saved_filter_tab_carries_a_tooltip() -> None:
    html = LIBRARY_TEMPLATE.read_text()
    match = re.search(r"<a[^>]*filter='favorites'[^>]*>", html)
    assert match, "saved-decks filter link is missing"
    assert "data-dl-tip=" in match.group(0)


def test_tooltip_css_primitive_is_not_defined_in_this_branch() -> None:
    """The [data-dl-tip] primitive ships on the shell branch; only apply it here."""
    assert "[data-dl-tip]" not in CSS_PATH.read_text()


# --- DYL-62: star for saved decks, heart for favourite commanders ----------


def test_deck_side_affordances_use_a_star_not_a_heart() -> None:
    card = DECK_CARD_TEMPLATE.read_text()
    assert STAR in card, "the deck card should use the saved-deck star"
    assert HEART not in card, "the deck card must not use the commander heart"

    library = LIBRARY_TEMPLATE.read_text()
    assert STAR in library, "the /build filters should use the saved-deck star"
    # The heart survives on /build only on the commander cross-link, which is
    # deliberately outside the deck-filter nav (see test_build_commanders_chip).
    filters = library[
        library.index('aria-label="Deck filters"') : library.index("</nav>")
    ]
    assert HEART not in filters, "deck filters must not use the commander heart"


def test_commander_side_affordances_keep_the_heart() -> None:
    for template in (COMMANDER_TEMPLATE, RESEARCH_FRAGMENT_TEMPLATE):
        assert HEART in template.read_text(), f"{template.name} must keep its heart"


def test_deck_card_relabels_favorite_as_saved(tmp_path, monkeypatch) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    deck_id = _seed_deck(client, "Saved wording")
    button = _favorite_button(client.get("/build").get_data(as_text=True))
    assert "save" in _attr(button, "aria-label").lower()
    assert "favorite" not in _attr(button, "aria-label").lower()
    assert "favorite" not in _attr(button, "data-dl-tip").lower()

    client.post(f"/build/deck/{deck_id}/favorite")
    button = _favorite_button(client.get("/build").get_data(as_text=True))
    assert "saved" in _attr(button, "aria-label").lower()
    assert "favorite" not in _attr(button, "data-dl-tip").lower()


def test_saved_filter_pill_is_relabelled_but_the_query_param_is_unchanged(
    tmp_path, monkeypatch
) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    deck_id = _seed_deck(client, "Round trip")
    client.post(f"/build/deck/{deck_id}/favorite")

    page = client.get("/build").get_data(as_text=True)
    assert "filter=favorites" in page, "the URL parameter must not be renamed"
    assert ">Saved" in page or "Saved " in page

    filtered = client.get("/build?filter=favorites").get_data(as_text=True)
    assert "Round trip" in filtered
    assert 'aria-current="page"' in filtered

    other = _seed_deck(client, "Not saved")
    assert other
    filtered = client.get("/build?filter=favorites").get_data(as_text=True)
    assert "Round trip" in filtered
    assert "Not saved" not in filtered


def test_saved_state_still_round_trips_through_the_favorite_column(
    tmp_path, monkeypatch
) -> None:
    client, path = _app(tmp_path, monkeypatch)
    deck_id = _seed_deck(client, "Column check")
    client.post(f"/build/deck/{deck_id}/favorite")
    with db.connect(path) as conn:
        row = conn.execute(
            "SELECT favorite FROM deck_documents WHERE id = ?", (deck_id,)
        ).fetchone()
    assert row is not None and row[0] == 1


# --- DYL-63: underline tabs and hierarchy on /build ------------------------


def test_underline_tab_component_exists_in_css() -> None:
    css = CSS_PATH.read_text()
    container = _rule_body(css, ".dl-underline-tabs")
    assert re.search(r"display\s*:\s*flex", container)
    base = _rule_body(css, ".dl-underline-tabs a")
    assert re.search(r"border-bottom\s*:\s*2px solid transparent", base)
    current = _rule_body(css, ".dl-underline-tabs a[aria-current=page]")
    assert re.search(r"border-bottom[^;]*var\(--brand\)", current)
    assert _rule_body(css, ".dl-underline-tabs a:hover")
    assert re.search(r"outline", _rule_body(css, ".dl-underline-tabs a:focus-visible"))


def test_retired_pill_component_is_gone() -> None:
    assert "dl-library-pills" not in CSS_PATH.read_text()
    assert "dl-library-pills" not in LIBRARY_TEMPLATE.read_text()


def test_build_filters_render_as_underline_tabs_without_tab_roles(
    tmp_path, monkeypatch
) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    page = client.get("/build").get_data(as_text=True)
    assert 'class="dl-underline-tabs"' in page
    assert 'aria-label="Deck filters"' in page
    assert 'role="tab"' not in page
    assert 'role="tablist"' not in page
    assert _filter_nav(page).count('aria-current="page"') == 1

    for filter_name in ("favorites", "recent"):
        nav = _filter_nav(
            client.get(f"/build?filter={filter_name}").get_data(as_text=True)
        )
        assert nav.count('aria-current="page"') == 1
        current = [a for a in re.findall(r"<a\b[^>]*>", nav) if "aria-current" in a]
        assert len(current) == 1
        assert f"filter={filter_name}" in current[0]


def test_build_keeps_the_primary_new_deck_cta_in_the_header(
    tmp_path, monkeypatch
) -> None:
    client, _path = _app(tmp_path, monkeypatch)
    page = client.get("/build").get_data(as_text=True)
    cta = page.index('class="dl-button dl-button-primary" type="button" data-new-deck')
    header_end = page.index('class="dl-library-filter-row"')
    assert cta < header_end, "the primary CTA must stay above the tabs"
    assert 'class="dl-underline-tabs"' in page
