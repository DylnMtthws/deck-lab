from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CONSIDERING_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-considering.js"
HARNESS = Path(__file__).with_name("considering_harness.mjs")
BUILDER_TEMPLATE = (
    ROOT
    / "src"
    / "sabermetrics"
    / "ui"
    / "templates"
    / "deck_lab"
    / "builder.html"
)


def _payload(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the considering harness")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS), str(CONSIDERING_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# AC-1 — button inserted and disabled without selection
# ---------------------------------------------------------------------------


def test_button_inserted_and_disabled_without_selection() -> None:
    payload = _payload("button_present_and_disabled")

    assert payload["buttonExists"] is True
    assert payload["buttonDisabled"] is True
    assert payload["buttonLabel"] == "Move to Considering"
    assert payload["badgeExists"] is True


# ---------------------------------------------------------------------------
# AC-2 — creates considering zone and moves in one batch
# ---------------------------------------------------------------------------


def test_creates_considering_zone_and_moves_in_one_batch() -> None:
    payload = _payload("creates_zone_and_moves")

    assert payload["fetchCount"] == 1, "exactly one fetch"
    assert payload["createZone"] == 1, "one create_zone command"
    assert payload["commandCount"] >= 2
    msg = "non-commander entries only"
    assert payload["moveCommands"] == ["entry-ring", "entry-lotus"], msg
    # Selection is cleared after move
    assert payload["selectionAfter"] == []


# ---------------------------------------------------------------------------
# AC-3 — moves into existing considering zone without creating
# ---------------------------------------------------------------------------


def test_moves_into_existing_considering_zone_without_creating() -> None:
    payload = _payload("existing_zone_no_create")

    assert payload["fetchCount"] == 1, "exactly one fetch"
    assert payload["createZone"] == 0, "no zone creation needed"
    assert payload["commandCount"] >= 2
    assert payload["moveCommands"] == ["entry-ring", "entry-lotus"]


# ---------------------------------------------------------------------------
# AC-4 — label switches to "Move to deck" and moves to Unsorted
# ---------------------------------------------------------------------------


def test_label_switches_to_move_to_deck_and_moves_to_unsorted() -> None:
    payload = _payload("move_to_deck_label")

    assert payload["buttonLabel"] == "Move to deck"
    assert payload["fetchCount"] == 1
    assert payload["commandCount"] >= 1
    # The move commands target Unsorted (might create it)
    assert payload["moveToUnsortedLength"] == 2


# ---------------------------------------------------------------------------
# AC-5 — badge counts private zone quantities and hides at zero
# ---------------------------------------------------------------------------


def test_badge_counts_private_zone_quantities_and_hides_at_zero() -> None:
    payload = _payload("badge_quantities")

    assert payload["badgeText"] == "+5 considering"
    assert payload["badgeHidden"] is False
    assert payload["badgeTitle"] == "Not counted toward 100 or included in exports"

    zero_payload = _payload("badge_hides_at_zero")
    assert zero_payload["badgeExists"] is True
    assert zero_payload["badgeHidden"] is True


# ---------------------------------------------------------------------------
# AC-6 — nothing inserted in shared mode
# ---------------------------------------------------------------------------


def test_nothing_inserted_in_shared_mode() -> None:
    payload = _payload("shared_mode")

    assert payload["buttonExists"] is False
    assert payload["badgeExists"] is False


# ---------------------------------------------------------------------------
# AC-7 — builder page includes script after builder
# ---------------------------------------------------------------------------


def test_builder_page_includes_script_after_builder(tmp_path: Path) -> None:
    from sabermetrics.deck_documents import DeckDocumentRepo
    from sabermetrics.ui.app import create_app
    from scripts.setup_db import setup_database

    db_path = tmp_path / "test.db"
    setup_database(db_path)
    repo = DeckDocumentRepo(db_path)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    owner_id = "owner-1"
    conn.execute(
        "INSERT INTO users(id,email,display_name,status) VALUES(?,'test@test','Test','active')",
        (owner_id,),
    )
    conn.execute(
        "INSERT INTO cards(id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,"
        "color_identity,is_legal_commander,is_legal_in_99)"
        "VALUES('cmd','o-cmd','Commander','{G}{U}',2,'Legendary Creature',"
        "'T: Add G.','[\"G\",\"U\"]',1,1)"
    )
    conn.commit()
    deck_id = repo.create(owner_id, commander_card_id="cmd")
    token = repo.create_share(owner_id, deck_id)
    conn.close()

    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    client = app.test_client()

    # Login as the owner
    with client.session_transaction() as session:
        session["_user_id"] = owner_id
        session["_fresh"] = True

    # Owner's builder page — script element must be present
    owner_html = client.get(f"/build/deck/{deck_id}").data.decode()
    script_re = re.compile(
        r'<script src="[^"]*/deck-lab-considering\.js" defer></script>'
    )
    assert script_re.search(owner_html), (
        "owner page must contain a real <script> element for deck-lab-considering.js"
    )
    # It must appear after deck-lab-builder.js
    builder_idx = owner_html.index("/deck-lab-builder.js")
    considering_idx = owner_html.index("/deck-lab-considering.js")
    assert considering_idx > builder_idx, (
        "considering script must appear after builder script"
    )
    # No escaped script element
    assert "&lt;script" not in owner_html, (
        "owner page must not contain escaped script elements"
    )
    # No fallback mixed-case variants
    assert "&lt;SCRIPT" not in owner_html

    # Shared page — script element must be absent
    shared_html = client.get(f"/shared/deck/{token}").data.decode()
    assert not script_re.search(shared_html), (
        "shared page must not contain the considering script element"
    )


# ---------------------------------------------------------------------------
# AC-8 — server count excludes considering
# ---------------------------------------------------------------------------


def test_server_count_excludes_considering(tmp_path: Path) -> None:
    from sabermetrics.deck_documents import _PRIVATE_PUBLIC_ZONES, DeckDocumentRepo
    from scripts.setup_db import setup_database

    assert "considering" in _PRIVATE_PUBLIC_ZONES, (
        "Considering is in private zones"
    )

    db_path = tmp_path / "test.db"
    setup_database(db_path)
    repo = DeckDocumentRepo(db_path)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    owner_id = "owner-1"
    deck_id = "deck-testcount"

    conn.execute(
        "INSERT INTO users(id,email,display_name,status) VALUES(?,'test@test','Test','active')",
        (owner_id,),
    )
    conn.execute(
        "INSERT INTO deck_documents(id,owner_id,title) VALUES(?,?,'Test')",
        (deck_id, owner_id),
    )
    zone_unsorted_id = "zone-unsorted"
    zone_consider_id = "zone-consider"
    conn.execute(
        "INSERT INTO deck_zones(id,deck_id,name,sort_order,x,y) "
        "VALUES(?,?,'Unsorted',0,40,18)",
        (zone_unsorted_id, deck_id),
    )
    conn.execute(
        "INSERT INTO deck_zones(id,deck_id,name,sort_order,x,y) "
        "VALUES(?,?,'Considering',1,500,18)",
        (zone_consider_id, deck_id),
    )
    # Commander
    conn.execute(
        "INSERT INTO deck_entries(id,deck_id,name,oracle_id,quantity,"
        "is_commander,zone_id,type_line,mana_cost,mana_value,color_identity) "
        "VALUES('e-cmd',?,'Kinnan','o1',1,1,NULL,'Legendary Creature',"
        "'{G}{U}',2,'[\"G\",\"U\"]')",
        (deck_id,),
    )
    # 99 library cards in Unsorted
    for i in range(99):
        conn.execute(
            "INSERT INTO deck_entries(id,deck_id,name,oracle_id,quantity,"
            "is_commander,zone_id,type_line,mana_cost,mana_value,color_identity) "
            "VALUES(?,?,'Card '||?,'o'||?,1,0,?,"
            "'Creature','{1}',1,'[\"G\"]')",
            (f"e-lib-{i}", deck_id, str(i), str(100 + i), zone_unsorted_id),
        )
    # 3 cards in Considering
    for i in range(3):
        conn.execute(
            "INSERT INTO deck_entries(id,deck_id,name,oracle_id,quantity,"
            "is_commander,zone_id,type_line,mana_cost,mana_value,color_identity) "
            "VALUES(?,?,'Consider '||?,'oc'||?,1,0,?,"
            "'Instant','{1}',1,'[\"G\"]')",
            (f"e-consider-{i}", deck_id, str(i), str(200 + i), zone_consider_id),
        )
    conn.commit()
    conn.close()

    document = repo.get(owner_id, deck_id)
    val = document["validation"]

    # total_count should be 100 (commander + 99 library), not counting the
    # 3 Considering cards
    assert val["total_count"] == 100, (
        f"expected 100, got {val['total_count']}"
    )
    assert val["library_count"] == 99
    assert val["commander_count"] == 1


# ---------------------------------------------------------------------------
# AC-9 — button enables after checkbox selection via UI
# ---------------------------------------------------------------------------


def test_button_enables_after_checkbox_selection_via_ui() -> None:
    payload = _payload("checkbox_selection")

    assert payload["checkboxesFound"] > 0
    # Before selection: disabled
    assert payload["beforeDisabled"] is True
    # After selection: enabled with correct label
    assert payload["afterDisabled"] is False
    assert payload["afterLabel"] == "Move to Considering"
    # After clear selection: disabled again
    assert payload["clearedDisabled"] is True
