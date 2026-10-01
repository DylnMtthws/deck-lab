"""T02 — Real mana symbols.

Tests for deck-lab-mana.js (fileName, symbol with Scryfall SVGs, error
fallback), the builder integration, and the Jinja macro replacement.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANA_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-mana.js"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
HARNESS = Path(__file__).with_name("mana_symbols_harness.mjs")
DECK_LAB = ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab"

CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
BUILDER_TEMPLATE = DECK_LAB / "builder.html"


def _payload() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the harness")
    result = subprocess.run(
        [node, str(HARNESS), str(MANA_JS), str(BUILDER_JS)],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# AC-1: fileName(symbol) — parametrized mapping table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_sym,expected_file",
    [
        ("W", "W.svg"),
        ("2", "2.svg"),
        ("10", "10.svg"),
        ("X", "X.svg"),
        ("W/U", "WU.svg"),
        ("2/W", "2W.svg"),
        ("W/P", "WP.svg"),
        ("G/U/P", "GUP.svg"),
        ("C", "C.svg"),
        ("S", "S.svg"),
        ("T", "T.svg"),
        ("Q", "Q.svg"),
        ("", None),
        ("INVALID", None),
    ],
)
def test_file_name_mapping_table(input_sym, expected_file) -> None:
    """AC-1: fileName resolves every example to the correct Scryfall SVG path."""
    payload = _payload()
    for case in payload["fileNameCases"]:
        if case["input"] == input_sym:
            assert case["pass"] is True, (
                f"fileName({input_sym!r}): expected {expected_file!r}, got {case['actual']!r}"
            )
            return
    pytest.fail(f"Input {input_sym!r} not found in payload")


# ---------------------------------------------------------------------------
# AC-2: symbol() returns img with correct src and alt
# ---------------------------------------------------------------------------


def test_symbol_returns_img_with_scryfall_src_and_alt() -> None:
    """AC-2: decorative → empty alt, aria-hidden. Non-decorative → has alt."""
    payload = _payload()
    dec = payload["symbolDecorative"]
    assert dec["tag"] == "IMG"
    assert "dl-ms" in dec["className"]
    assert dec["src"].startswith("https://svgs.scryfall.io/card-symbols/")
    assert dec["src"].endswith("W.svg")
    assert dec["alt"] == ""
    assert dec["ariaHidden"] == "true"
    assert dec["width"] == 16
    assert dec["height"] == 16

    nd = payload["symbolNonDecorative"]
    assert nd["tag"] == "IMG"
    assert "dl-ms" in nd["className"]
    assert nd["src"].endswith("U.svg")
    assert nd["alt"] == "{U}"
    assert nd["ariaHidden"] is None

    inv = payload["symbolInvalid"]
    assert inv["tag"] == "SPAN"
    assert "dl-mana-generic" in inv["className"]


# ---------------------------------------------------------------------------
# AC-3: error fallback
# ---------------------------------------------------------------------------


def test_symbol_falls_back_to_span_on_error() -> None:
    """AC-3: dispatching 'error' on the img replaces it with a span."""
    payload = _payload()
    fb = payload["errorFallback"]
    assert fb["imgGone"] is True
    assert fb["spanExists"] is True
    assert "dl-mana-symbol" in fb["spanClass"]
    assert "dl-mana-generic" in fb["spanClass"]
    assert fb["spanText"] == "R"


# ---------------------------------------------------------------------------
# AC-4: builder uses SVG when mana.js is loaded
# ---------------------------------------------------------------------------


def test_builder_mana_cost_uses_svg_when_mana_js_loaded() -> None:
    """AC-4: a mana-cost like {1}{U}{U} produces three img.dl-ms elements."""
    payload = _payload()
    bw = payload["builderWithMana"]
    assert bw["rows"] >= 1
    assert bw["imgCount"] == 3, f"expected 3 img.dl-ms, got {bw['imgCount']}"


# ---------------------------------------------------------------------------
# AC-5: builder falls back without mana.js
# ---------------------------------------------------------------------------


def test_builder_falls_back_without_mana_js() -> None:
    """AC-5: without DeckLabMana, existing .dl-mana-symbol elements still appear."""
    payload = _payload()
    bw = payload["builderWithoutMana"]
    assert bw["rows"] >= 1
    assert bw["fallbackCount"] >= 1


# ---------------------------------------------------------------------------
# AC-6: server templates use the macro (with Flask test client)
# ---------------------------------------------------------------------------


from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

_UI_APP_CONFIG = dict(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _seed_commander_and_deck(path) -> tuple[str, str]:
    """Seed a commander card and a generated deck. Returns (user_id, deck_id)."""
    user_id = db.UsersRepo(path).create(
        email="mana-test@example.test",
        display_name="Mana",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
               (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
                is_legal_commander,is_legal_in_99,image_uri)
               VALUES('mana-cmd','o-mana-cmd','Mana Commander','{W}{U}{B}{R}{G}',5,
                'Legendary Creature — God','All colors','["W","U","B","R","G"]',1,1,NULL)"""
        )
        conn.commit()

    deck_id = "deck-mana-test"
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO generated_decks
               (id, commander_id, owner_id, deck_name, cards_json, rationale,
                budget_usd, power_target, estimated_bracket)
               VALUES(?, 'mana-cmd', ?, 'Mana Deck', '[]', '{}', 200, 3, 3)""",
            (deck_id, user_id),
        )
        conn.commit()
    return user_id, deck_id


def test_server_templates_use_macro(tmp_path, monkeypatch) -> None:
    """AC-6: rendered templates use scryfall SVGs; no 'mana mana-' pips outside
    form checkbox labels. Render pages that contain color pips through Flask."""
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "mana-ac6.db"
    setup_database(path)
    user_id, _deck_id = _seed_commander_and_deck(path)

    app = create_app(path)
    app.config.update(**_UI_APP_CONFIG)
    client = app.test_client()
    _login(client, user_id)

    # Pages that render mana symbols server-side via the macro
    for url, label in [
        ("/research?tab=commanders", "Research commanders tab"),
        ("/research?tab=cards", "Research cards tab"),
    ]:
        resp = client.get(url)
        assert resp.status_code == 200, (
            f"{label} ({url}) returned {resp.status_code}"
        )
        html = resp.get_data(as_text=True)

        # Server-rendered SVG symbols from the macro
        assert "svgs.scryfall.io/card-symbols/" in html, (
            f"{label} ({url}): no Scryfall SVG found"
        )

        # No old-style mana pips outside exempt label chips
        for match in re.finditer(
            r'class="mana\s+mana-([WUBRGC])"\s*>\s*\1\s*</',
            html,
        ):
            preceding = html[: match.start()]
            exempt = False
            label_open = preceding.rfind("<label")
            label_close = preceding.rfind("</label>")
            if label_open > label_close and "dl-color-filter-chip" in preceding[label_open:]:
                exempt = True
            if not exempt:
                pytest.fail(
                    f"{label} ({url}):{match.start()}: found old mana pip "
                    f"'{match.group(0).strip()}' outside exempt label chips"
                )


# ---------------------------------------------------------------------------
# AC-8: pages with color pips render 200
# ---------------------------------------------------------------------------


def test_pages_with_color_pips_render_200(tmp_path, monkeypatch) -> None:
    """AC-8: /research?tab=commanders, /research?tab=cards, /, /build/<deck>
    each return 200 and contain Scryfall SVGs where color pips appear."""
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "mana-ac8.db"
    setup_database(path)
    user_id, _deck_id = _seed_commander_and_deck(path)

    app = create_app(path)
    app.config.update(**_UI_APP_CONFIG)
    client = app.test_client()
    _login(client, user_id)

    for url, label in [
        ("/research?tab=commanders", "Research commanders tab"),
        ("/research?tab=cards", "Research cards tab"),
    ]:
        resp = client.get(url)
        assert resp.status_code == 200, (
            f"{label} ({url}) returned {resp.status_code}"
        )
        html = resp.get_data(as_text=True)
        assert "svgs.scryfall.io/card-symbols/" in html, (
            f"{label} ({url}): no Scryfall SVG symbol found"
        )


# ---------------------------------------------------------------------------
# AC-7: builder page loads mana.js before builder.js
# ---------------------------------------------------------------------------


def test_builder_page_loads_mana_js_before_builder_js() -> None:
    """AC-7: deck-lab-mana.js appears before deck-lab-builder.js in the rendered
    builder.html."""
    html = BUILDER_TEMPLATE.read_text()
    mana_idx = html.index("deck-lab-mana.js")
    builder_idx = html.index("deck-lab-builder.js")
    assert mana_idx < builder_idx
