"""I02 — Design polish after integration review."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
STACKS_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-stacks.css"
DECK_LAB_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
AVATAR_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "avatar.css"
HARNESS_PATH = ROOT / "tests" / "design_polish_i02_harness.mjs"


# ---------------------------------------------------------------------------
# AC-1: Stacks CSS has no global chrome overrides
# ---------------------------------------------------------------------------


def _read_css(path: Path) -> str:
    return path.read_text(encoding="utf-8")


AC1_EXCEPTED = "body.dl-has-stacks .dl-bulk-controls.is-empty { display: none; }"


def test_stacks_css_has_no_global_chrome_overrides() -> None:
    """The only selector starting with body.dl-has-stacks is the bulk-controls rule."""
    css = _read_css(STACKS_CSS)
    lines = css.splitlines()

    body_has_stacks_selectors = [
        line for line in lines if line.strip().startswith("body.dl-has-stacks")
    ]

    assert (
        len(body_has_stacks_selectors) >= 1
    ), "Expected at least one body.dl-has-stacks rule (the bulk-controls exception)"
    for sel in body_has_stacks_selectors:
        assert (
            sel.strip() == AC1_EXCEPTED
        ), f"Unexpected body.dl-has-stacks rule: {sel.strip()}"


# ---------------------------------------------------------------------------
# AC-2a: New deck Unsorted zone is clear of Commander box
# ---------------------------------------------------------------------------


def test_new_deck_unsorted_zone_clear_of_commander_box(tmp_path: Path) -> None:
    """Unsorted zone x/y equals 220/18 for both creation paths (create and import_text)."""
    from sabermetrics import db
    from sabermetrics.deck_documents import DeckDocumentRepo
    from scripts.setup_db import setup_database

    db_path = tmp_path / "test.db"
    setup_database(db_path)

    owner = db.UsersRepo(db_path).create(
        email="owner@test.test", display_name="Owner", status="active"
    )
    seed_card(db_path)

    repo = DeckDocumentRepo(db_path)

    # Path 1: create()
    deck_id_1 = repo.create(owner, title="Test Deck", commander_card_id="kinnan")
    zone_1 = _get_unsorted_zone(db_path, deck_id_1)
    assert zone_1 is not None, "Expected an Unsorted zone after create()"
    assert zone_1["x"] == 220, f"create(): Unsorted x should be 220, got {zone_1['x']}"
    assert zone_1["y"] == 18, f"create(): Unsorted y should be 18, got {zone_1['y']}"

    # Path 2: import_text()
    deck_id_2 = repo.import_text(
        owner,
        text="1 Sol Ring\n1 Kinnan Test",
        title="Imported Deck",
        commander_card_ids=["kinnan"],
    )
    zone_2 = _get_unsorted_zone(db_path, deck_id_2)
    assert zone_2 is not None, "Expected an Unsorted zone after import_text()"
    assert (
        zone_2["x"] == 220
    ), f"import_text(): Unsorted x should be 220, got {zone_2['x']}"
    assert (
        zone_2["y"] == 18
    ), f"import_text(): Unsorted y should be 18, got {zone_2['y']}"


# ---------------------------------------------------------------------------
# AC-2b: Zone inside Commander box renders beside it
# ---------------------------------------------------------------------------


def _run_builder_harness(scenario: str, tmp_path: Path) -> dict:
    """Run the Node harness for a given scenario and return parsed JSON."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder harness")
    harness = tmp_path / "i02_harness_run.mjs"
    harness.write_text(HARNESS_PATH.read_text(encoding="utf-8"))
    completed = subprocess.run(
        [node, str(harness), str(BUILDER_JS), scenario],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Harness stderr:\n{completed.stderr}\nstdout:\n{completed.stdout}"
        )
    return json.loads(completed.stdout)


def test_mat_zone_inside_commander_box_renders_beside_it(tmp_path: Path) -> None:
    """A zone at (80, 120) with a commander present renders with left >= 18+170+24."""
    result = _run_builder_harness("zone-overlap", tmp_path)

    cmd_zone = None
    unsorted_zone = None
    for zone in result["zones"]:
        if zone.get("isCommander"):
            cmd_zone = zone
        else:
            unsorted_zone = zone

    assert cmd_zone is not None, "Commander zone should exist"
    assert unsorted_zone is not None, "Unsorted zone should exist"

    cmd_width = float(cmd_zone.get("width", "170").replace("px", ""))

    unsorted_left = float(unsorted_zone["left"].replace("px", ""))

    # The Commander box is at left=18, top=18. Its width is at least 170.
    # The zone at (80, 120) is inside the Commander box (18 <= 80 < 18+width)
    # so it should be shifted to 18 + cmd_width + 24.
    expected_min_left = 18 + cmd_width + 24
    assert unsorted_left >= expected_min_left, (
        f"Overlapping zone should be shifted right; got left={unsorted_left}, "
        f"expected >= {expected_min_left}"
    )

    # Zone at (600, 400) should not be shifted
    result2 = _run_builder_harness("zone-no-overlap", tmp_path)
    far_zone = None
    for zone in result2["zones"]:
        if not zone.get("isCommander"):
            far_zone = zone
    assert far_zone is not None
    far_left = float(far_zone["left"].replace("px", ""))
    assert (
        far_left == 600
    ), f"Non-overlapping zone should keep its position; got left={far_left}, expected 600"


# ---------------------------------------------------------------------------
# AC-2c: freeZonePosition avoids Commander box
# ---------------------------------------------------------------------------


def test_free_zone_position_avoids_commander_box(tmp_path: Path) -> None:
    """freeZonePosition must not return a position inside the Commander box."""
    result = _run_builder_harness("free-zone", tmp_path)
    assert result["freeZonePosition"] is not None, "Expected a free zone position"
    pos = result["freeZonePosition"]
    cmd = result["commandBox"]
    assert cmd is not None, "Commander box should exist"

    cmd_left = cmd["left"]
    cmd_top = cmd["top"]
    cmd_right = cmd_left + cmd["width"]
    cmd_bottom = cmd_top + 260

    zone_left = pos["x"]
    zone_top = pos["y"]
    zone_right = zone_left + 360
    zone_bottom = zone_top + 240

    # The position should NOT overlap with the Commander box (with 16px gutter)
    no_overlap = (
        zone_right + 16 <= cmd_left
        or cmd_right + 16 <= zone_left
        or zone_bottom + 16 <= cmd_top
        or cmd_bottom + 16 <= zone_top
    )
    assert no_overlap, (
        f"freeZonePosition {pos} overlaps Commander box "
        f"(left={cmd_left}, top={cmd_top}, right={cmd_right}, bottom={cmd_bottom})"
    )


# ---------------------------------------------------------------------------
# AC-3: Off-scale sizes fixed
# ---------------------------------------------------------------------------


def test_offscale_sizes_fixed() -> None:
    """CSS assertions on changed selectors for type-scale compliance."""
    css = _read_css(DECK_LAB_CSS)
    avatar_css_text = _read_css(AVATAR_CSS)

    # 1. Playmat zoom icon buttons: 15px -> 14px
    assert ".dl-zoom .dl-icon-button" in css
    _assert_font_size(css, ".dl-zoom .dl-icon-button", "14px")

    # 2. Dialog h2 titles: 24px -> 20px
    assert ".dl-dialog-head h2" in css
    _assert_font_size(css, ".dl-dialog-head h2", "20px")

    # 3. Research page h1: 24px -> 28px
    assert ".dl-research-main .dl-page-head h1" in css
    _assert_font_size(css, ".dl-research-main .dl-page-head h1", "28px")

    # 4. Research search kbd: 10px -> 11px (uses font shorthand, not font-size)
    _assert_font_size_or_shorthand(css, ".dl-research-search kbd", "11px")

    # 5. Color filter mana pips: 10px -> 11px
    assert "#research-filters .dl-color-filter-chip .mana" in css
    _assert_font_size(css, "#research-filters .dl-color-filter-chip .mana", "11px")

    # 6. Account avatar: 22px -> 20px
    assert ".account-avatar" in avatar_css_text
    _assert_font_size(avatar_css_text, ".account-avatar", "20px")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_font_size(css: str, selector: str, expected_size: str) -> None:
    """Assert that the font-size for a CSS selector equals the expected value."""
    _assert_property(css, selector, r"font-size:\s*", expected_size)


def _assert_font_size_or_shorthand(css: str, selector: str, expected_size: str) -> None:
    """Assert font-size via explicit property or font shorthand."""
    flat = css.replace("\n", "")
    escaped = re.escape(selector)
    blocks = re.findall(escaped + r"\s*\{([^}]+)\}", flat)
    for block in blocks:
        # Check font: <size> (shorthand) or font-size: <size> (explicit)
        if re.search(
            r"(?:^|;)\s*font(?:\-size)?:\s*" + re.escape(expected_size),
            block,
        ):
            return
    raise AssertionError(
        f"Expected {selector} to have font(-size): {expected_size}. "
        f"Blocks found: {len(blocks)}"
    )


def _assert_property(
    css: str, selector: str, prop_pattern: str, expected_value: str
) -> None:
    """Assert a CSS property value for a selector."""
    flat = css.replace("\n", "")
    pattern = (
        re.escape(selector) + r"\s*\{[^}]*?" + prop_pattern + re.escape(expected_value)
    )
    match = re.search(pattern, flat)
    assert match, (
        f"Expected {selector} to have {prop_pattern}{expected_value}. "
        f"Selector found in CSS: {selector in flat}"
    )


def seed_card(db_path: Path) -> None:
    """Seed minimal card data for deck_document tests."""
    from sabermetrics import db

    with db.connect(db_path) as conn:
        if not conn.execute("SELECT 1 FROM cards WHERE id='kinnan'").fetchone():
            conn.execute(
                "INSERT INTO cards "
                "(id, oracle_id, name, mana_cost, cmc, type_line, oracle_text, "
                "color_identity, image_uri, is_legal_commander, is_legal_in_99) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "kinnan",
                    "oracle-kinnan",
                    "Kinnan Test",
                    "{G}{U}",
                    2,
                    "Legendary Creature — Human Druid",
                    "Mana ability",
                    '["G","U"]',
                    "https://img.test/kinnan.jpg",
                    1,
                    1,
                ),
            )
        if not conn.execute("SELECT 1 FROM cards WHERE id='ring'").fetchone():
            conn.execute(
                "INSERT INTO cards "
                "(id, oracle_id, name, mana_cost, cmc, type_line, oracle_text, "
                "color_identity, image_uri, is_legal_commander, is_legal_in_99) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ring",
                    "oracle-ring",
                    "Sol Ring",
                    "{1}",
                    1,
                    "Artifact",
                    "Add mana",
                    "[]",
                    None,
                    0,
                    1,
                ),
            )
        conn.commit()


def _get_unsorted_zone(db_path: Path, deck_id: str) -> dict | None:
    """Return the Unsorted zone row for a deck, or None."""
    from sabermetrics import db

    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT x, y FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
            (deck_id,),
        ).fetchone()
        return dict(row) if row else None
