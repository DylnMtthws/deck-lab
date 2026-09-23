"""Commander drill-in keeps the research table and rejects hostile back targets."""

import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

import pytest
from flask import url_for

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

CSS = Path(__file__).parents[1] / "src/sabermetrics/ui/static/deck-lab.css"


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, f"missing CSS rule for {selector}"
    return match.group(1)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "back-link.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="back-link@example.test",
        display_name="Back Link",
        role="user",
        status="active",
    )
    today = date.today().isoformat()
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99)
            VALUES('kinnan','oracle-kinnan','Kinnan','{G}{U}',2,
             'Legendary Creature — Human Druid','', '["G","U"]',1,1)""")
        conn.execute("""INSERT INTO decks(id,source,source_id,commander_id)
            VALUES('d1','test','1','kinnan')""")
        conn.execute(
            """INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,standing,tournament_date)
            VALUES('r1','event-1','d1','kinnan',1,?)""",
            (today,),
        )
        conn.commit()
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False, WTF_CSRF_ENABLED=False)
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return test_client


def _html(response) -> str:
    assert response.status_code == 200
    return unescape(response.get_data(as_text=True))


def _back_href(html: str) -> str:
    match = re.search(
        r'<a class="dl-button dl-research-back" href="([^"]+)">'
        r'<span aria-hidden="true">‹</span> Back to table</a>',
        html,
    )
    assert match, "commander page is missing the Back to table link"
    return match.group(1)


def _query_pairs(query: str) -> list[tuple[str, str]]:
    return parse_qsl(query, keep_blank_values=True)


def _back_pairs(href: str) -> list[tuple[str, str]]:
    params = parse_qs(urlparse(href).query, keep_blank_values=True)
    assert "back" in params
    return _query_pairs(params["back"][0])


def _index(client) -> str:
    with client.application.test_request_context("/"):
        return url_for("research.index")


def test_back_control_is_secondary_and_sits_above_the_detail_grid():
    body = _rule_body(CSS.read_text(), ".dl-detail-head > .dl-research-back")
    assert re.search(r"grid-column\s*:\s*1\s*/\s*-1", body)
    assert re.search(r"justify-self\s*:\s*start", body)


def test_back_link_restores_filtered_sorted_paged_table(client):
    state = (
        "tab=metagame&q=Kinnan&page=2&window=30&sort=name"
        "&color=U&color=G&color_mode=include"
    )
    detail = _html(
        client.get("/research/commander/kinnan", query_string={"back": state})
    )
    assert detail.index("Back to table") < detail.index('class="dl-commander-art"')
    assert ">Research</a> / Commanders / Kinnan" in detail
    assert 'aria-pressed="false"' in detail
    href = _back_href(detail)
    assert href.startswith("/")
    assert not href.startswith("//")
    assert _query_pairs(urlparse(href).query) == _query_pairs(state)
    restored = _html(client.get(href))
    assert 'aria-current="page">Meta</a>' in restored
    assert 'value="Kinnan"' in restored
    assert "PAGE 2" in restored
    assert 'value="30" selected' in restored
    assert re.search(r'sort=name" role="menuitem" aria-current="true"', restored)
    assert "Name A–Z" in restored
    assert 'value="U" aria-label="Blue" checked' in restored
    assert 'value="G" aria-label="Green" checked' in restored
    assert 'value="W" aria-label="White" checked' not in restored


def test_direct_commander_link_falls_back_to_research_index(client):
    index = _index(client)
    bare = _back_href(_html(client.get("/research/commander/kinnan")))
    assert bare == index
    blank = _back_href(
        _html(client.get("/research/commander/kinnan", query_string={"back": "   "}))
    )
    assert blank == index


def test_hostile_back_values_fall_back_to_research_index(client):
    index = _index(client)
    hostile = [
        "https://evil.example",
        "//evil.example",
        "/build",
        "/research/../build",
        "tab=metagame\nhttps://evil.example",
        "q=https://evil.example",
        "/\\\\evil.example",
    ]
    for value in hostile:
        href = _back_href(
            _html(
                client.get("/research/commander/kinnan", query_string={"back": value})
            )
        )
        assert href == index, value
        assert "evil.example" not in href
        assert urlparse(href).path.rstrip("/") == urlparse(index).path.rstrip("/")


def test_same_index_path_is_accepted_and_rebuilt(client):
    index = _index(client)
    supplied = f"{index}?tab=commanders&q=Kinnan&page=2"
    href = _back_href(
        _html(client.get("/research/commander/kinnan", query_string={"back": supplied}))
    )
    assert urlparse(href).path.rstrip("/") == urlparse(index).path.rstrip("/")
    assert _query_pairs(urlparse(href).query) == _query_pairs(
        "tab=commanders&q=Kinnan&page=2"
    )
    assert "evil.example" not in href


def test_drill_in_links_carry_the_table_query(client):
    commanders = "tab=commanders&q=Kinnan&page=1&color=U&color=G&color_mode=include"
    catalog = _html(client.get("/research/?" + commanders))
    assert 'aria-label="Open Kinnan"' in catalog
    assert 'aria-label="Favorite Kinnan"' in catalog
    tile = re.search(
        r'class="dl-card-result" href="([^"]+)" aria-label="Open Kinnan"',
        catalog,
    )
    assert tile
    assert _back_pairs(tile.group(1)) == _query_pairs(commanders)
    detail = _html(client.get(tile.group(1)))
    back = _back_href(detail)
    assert _query_pairs(urlparse(back).query) == _query_pairs(commanders)
    restored = _html(client.get(back))
    assert 'aria-current="page">Commanders</a>' in restored
    assert 'value="Kinnan"' in restored

    metagame = (
        "tab=metagame&q=Kinnan&window=30&sort=name&page=1&color=U&color_mode=include"
    )
    table = _html(client.get("/research/?" + metagame))
    name = re.search(r'class="dl-meta-commander-name" href="([^"]+)"', table)
    profile = re.search(r'class="dl-button" href="([^"]+)">Profile</a>', table)
    assert name and profile
    assert _back_pairs(name.group(1)) == _query_pairs(metagame)
    assert _back_pairs(profile.group(1)) == _query_pairs(metagame)
    assert parse_qs(urlparse(name.group(1)).query)["window"] == ["30"]
