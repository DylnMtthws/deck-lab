"""Flask test client tests for Scryfall-style query syntax in the Research Cards tab.

See AC-10 through AC-13 in T03-research-scryfall-syntax.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.research import reset_card_results_cache
from sabermetrics.ui.app import create_app
from sabermetrics.ui.scryfall_query import ParsedQuery
from scripts.setup_db import setup_database


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_card_results_cache()
    yield
    reset_card_results_cache()


def _seed_test_data(path: Path) -> None:
    """Populate a small card corpus suited for filter testing."""
    setup_database(path)
    rows = [
        # id, oracle_id, name, cmc, type_line, oracle_text, color_identity,
        # is_legal_commander, is_legal_in_99, image_uri, rarity
        (
            "bolt",
            "o1",
            "Lightning Bolt",
            1,
            "Instant",
            "Deal 3 damage",
            '["R"]',
            0,
            1,
            "img",
            "common",
        ),
        (
            "opt",
            "o2",
            "Opt",
            1,
            "Instant",
            "Scry 1. Draw a card.",
            '["U"]',
            0,
            1,
            "img",
            "common",
        ),
        (
            "c-sphinx",
            "o3",
            "Consecrated Sphinx",
            6,
            "Creature — Sphinx",
            "Flying. Whenever an opponent draws a card, you may draw a card.",
            '["U"]',
            0,
            1,
            "img",
            "mythic",
        ),
        (
            "d-vault",
            "o4",
            "Demonic Vault",
            2,
            "Artifact",
            "Tap: Add {C}.",
            "[]",
            0,
            1,
            "img",
            "rare",
        ),
        (
            "s-lotus",
            "o5",
            "Sapphire Lotus",
            0,
            "Artifact",
            "Tap: Add {U}.",
            "[]",
            0,
            1,
            "img",
            "rare",
        ),
        (
            "g-hydra",
            "o6",
            "Giant Hydra",
            4,
            "Creature — Hydra",
            "Trample. Power is equal to damage.",
            '["G"]',
            0,
            1,
            "img",
            "uncommon",
        ),
        (
            "w-angel",
            "o7",
            "Winged Angel",
            5,
            "Creature — Angel",
            "Flying, vigilance.",
            '["W"]',
            0,
            1,
            "img",
            "rare",
        ),
        (
            "b-demon",
            "o8",
            "Black Demon",
            4,
            "Creature — Demon",
            "Flying, trample.",
            '["B"]',
            0,
            1,
            "img",
            "rare",
        ),
        (
            "r-haste",
            "o9",
            "Raging Haste",
            2,
            "Sorcery",
            "Creatures you control gain haste.",
            '["R"]',
            0,
            1,
            "img",
            "uncommon",
        ),
        (
            "kinnan",
            "o10",
            "Kinnan",
            2,
            "Legendary Creature — Human Druid",
            "",
            '["G","U"]',
            1,
            1,
            "img",
            "mythic",
        ),
        (
            "solring",
            "o11",
            "Sol Ring",
            1,
            "Artifact",
            "Tap: Add {C}{C}.",
            "[]",
            0,
            1,
            "img",
            "uncommon",
        ),
        (
            "cm-pact",
            "o12",
            "Colorless Pact",
            3,
            "Enchantment",
            "At the beginning of your upkeep, draw a card.",
            "[]",
            0,
            1,
            "img",
            "rare",
        ),
    ]
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri,rarity)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "syntax.db"
    _seed_test_data(path)
    user = db.UsersRepo(path).create(
        email="syntax@example.test",
        display_name="Syntax",
        role="user",
        status="active",
    )
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False, WTF_CSRF_ENABLED=False)
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return test_client


def _html(response, status=200) -> str:
    assert response.status_code == status
    return response.get_data(as_text=True)


def _count_results(html: str) -> int:
    """Count dl-card-result entries in the HTML."""
    return len(re.findall(r'class="dl-card-result"', html))


class TestSyntaxQueryFiltersResults:
    """AC-10: q=t:instant mv<=1 returns only instants with MV ≤ 1."""

    def test_type_and_mv_filter(self, client):
        """Syntax query limits results compared to unfiltered."""
        all_html = _html(client.get("/research/?tab=cards"))
        total = _count_results(all_html)
        assert total >= 2

        filtered = _html(client.get("/research/?tab=cards&q=t:instant mv<=1"))
        count = _count_results(filtered)
        assert count > 0
        assert count < total, "syntax filter should reduce result count"

    def test_syntax_only_returns_matching_cards(self, client):
        """t:instant mv<=1 only returns Lightning Bolt and Opt."""
        html = _html(client.get("/research/?tab=cards&q=t:instant mv<=1"))
        assert "Lightning Bolt" in html
        assert "Opt" in html
        assert "Consecrated Sphinx" not in html
        assert "Sol Ring" not in html
        assert "Giant Hydra" not in html


class TestFormArgsOverrideParsedValues:
    """AC-11: Explicit form args win over parsed query values."""

    def test_form_oracle_wins_over_parsed(self, client):
        """Explicit oracle_text=damage overrides parsed o:draw."""
        html = _html(client.get("/research/?tab=cards&q=o:draw&oracle_text=damage"))
        assert "Lightning Bolt" in html
        assert "Opt" not in html  # Opt has "draw a card" but not "damage"

    def test_form_type_wins_over_parsed(self, client):
        """Explicit card_type=Artifact overrides parsed t:instant."""
        html = _html(client.get("/research/?tab=cards&q=t:instant&card_type=Artifact"))
        assert "Sol Ring" in html
        assert "Lightning Bolt" not in html

    def test_empty_form_does_not_override_parsed(self, client):
        """Form arg with empty string does not block parsed value."""
        html = _html(client.get("/research/?tab=cards&q=t:creature&card_type="))
        assert "Giant Hydra" in html
        assert "Lightning Bolt" not in html

    def test_color_form_override(self, client):
        """Explicit card_color=U overrides parsed c:R."""
        html = _html(
            client.get("/research/?tab=cards&q=c:R&card_color=U&color_mode=include")
        )
        assert "Opt" in html  # U
        assert "Lightning Bolt" not in html  # R but form says U only


class TestUnsupportedNoticeRendered:
    """AC-12: Unsupported syntax terms render a notice."""

    def test_is_commander_shows_notice(self, client):
        html = _html(client.get("/research/?tab=cards&q=is:commander"))
        assert "data-query-unsupported" in html
        assert "Not supported yet" in html
        assert "is:commander" in html

    def test_set_code_shows_notice(self, client):
        html = _html(client.get("/research/?tab=cards&q=set:mh3"))
        assert "data-query-unsupported" in html
        assert "set:mh3" in html

    def test_applied_terms_show_notice(self, client):
        html = _html(client.get("/research/?tab=cards&q=t:creature o:draw"))
        assert "data-query-applied" in html
        assert "Applied from search" in html

    def test_mixed_applied_and_unsupported(self, client):
        html = _html(
            client.get("/research/?tab=cards&q=t:creature o:draw is:commander set:mh3")
        )
        assert "data-query-applied" in html
        assert "data-query-unsupported" in html

    def test_unsupported_notice_includes_all_terms(self, client):
        html = _html(client.get("/research/?tab=cards&q=set:mh3 f:edh"))
        assert "set:mh3" in html
        assert "f:edh" in html

    def test_query_notices_absent_when_not_needed(self, client):
        """Plain name query has no notices."""
        html = _html(client.get("/research/?tab=cards&q=Lightning"))
        assert "data-query-applied" not in html
        assert "data-query-unsupported" not in html


class TestPlainQueryUnchanged:
    """AC-13: Results for a plain name query are identical with parser
    and with parser returning an empty ParsedQuery.
    """

    def test_plain_query_matches_unparsed(self, client, monkeypatch):
        import sabermetrics.ui.research_routes as routes

        plain_href = "/research/?tab=cards&q=Lightning"
        plain_html = _html(client.get(plain_href))
        plain_count = _count_results(plain_html)

        # Monkeypatch parse_query to return empty ParsedQuery
        original = routes.parse_query
        routes.parse_query = lambda q: ParsedQuery()

        try:
            unparsed_html = _html(client.get(plain_href))
            unparsed_count = _count_results(unparsed_html)
        finally:
            routes.parse_query = original

        assert plain_count == unparsed_count
        assert plain_count > 0

    def test_plain_two_word_query(self, client, monkeypatch):
        import sabermetrics.ui.research_routes as routes

        plain_href = "/research/?tab=cards&q=Sol Ring"
        plain_html = _html(client.get(plain_href))
        assert "Sol Ring" in plain_html

        original = routes.parse_query
        routes.parse_query = lambda q: ParsedQuery()

        try:
            unparsed_html = _html(client.get(plain_href))
        finally:
            routes.parse_query = original

        assert plain_html == unparsed_html


class TestSyntaxNotAppliedOnOtherTabs:
    """AC-14: Syntax parsing and notices are restricted to the cards tab."""

    def test_commanders_tab_not_affected(self, client, monkeypatch):
        import sabermetrics.ui.research_routes as routes

        parse_calls = []

        original = routes.parse_query
        routes.parse_query = lambda q: (parse_calls.append(q), ParsedQuery())[1]

        try:
            html = _html(client.get("/research/?tab=commanders&q=mv<=2"))
        finally:
            routes.parse_query = original

        assert (
            parse_calls == []
        ), "parse_query should not be called for the commanders tab"
        assert "data-query-applied" not in html
        assert "data-query-unsupported" not in html

    def test_metagame_tab_not_affected(self, client, monkeypatch):
        import sabermetrics.ui.research_routes as routes

        parse_calls = []

        original = routes.parse_query
        routes.parse_query = lambda q: (parse_calls.append(q), ParsedQuery())[1]

        try:
            html = _html(client.get("/research/?tab=metagame&q=mv<=2"))
        finally:
            routes.parse_query = original

        assert (
            parse_calls == []
        ), "parse_query should not be called for the metagame tab"
        assert "data-query-applied" not in html
        assert "data-query-unsupported" not in html

    def test_decks_tab_not_affected(self, client, monkeypatch):
        import sabermetrics.ui.research_routes as routes

        parse_calls = []

        original = routes.parse_query
        routes.parse_query = lambda q: (parse_calls.append(q), ParsedQuery())[1]

        try:
            html = _html(client.get("/research/?tab=decks&q=mv<=2"))
        finally:
            routes.parse_query = original

        assert parse_calls == [], "parse_query should not be called for the decks tab"
        assert "data-query-applied" not in html
        assert "data-query-unsupported" not in html
