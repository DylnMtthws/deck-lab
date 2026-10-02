"""Keyboard editing and undo/redo for the deck builder."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
HISTORY_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-history.js"
HOTKEYS_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-hotkeys.js"
HARNESS = Path(__file__).with_name("keyboard_undo_harness.mjs")
TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)

HELP_KEYS = (
    "/",
    "Shift+Enter",
    "Cmd+Z",
    "Ctrl+Z",
    "Cmd+Shift+Z",
    "Ctrl+Shift+Z",
    "Ctrl+Y",
    "ArrowDown",
    "ArrowUp",
    "+",
    "=",
    "-",
    "Space",
    "Delete",
    "Backspace",
    "?",
    "j",
    "k",
    "x",
    "m",
)


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the keyboard undo harness")
    return node


def _payload(scenario: str, stdin: str | None = None) -> dict:
    result = subprocess.run(
        [
            _node(),
            str(HARNESS),
            str(BUILDER_JS),
            str(HISTORY_JS),
            str(HOTKEYS_JS),
            scenario,
        ],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _plan(before: dict, commands: list[dict], after: dict) -> dict:
    return _payload(
        "inverse",
        json.dumps({"before": before, "commands": commands, "after": after}),
    )


def _project(document: dict) -> dict:
    entries = tuple(
        sorted(
            (
                entry["card_id"],
                entry["name"],
                int(entry["quantity"]),
                entry["zone_id"],
                entry["role"] or "",
                bool(entry["is_commander"]),
            )
            for entry in document["entries"]
        )
    )
    zones = tuple(sorted((zone["id"], zone["name"]) for zone in document["zones"]))
    return {"entries": entries, "zones": zones}


class _World:
    def __init__(self, path: Path, owner: str) -> None:
        self.repo = DeckDocumentRepo(path)
        self.owner = owner
        self.deck_id = self.repo.create(
            owner, title="Keys", commander_card_id="commander"
        )
        self.revision = 0
        self._step = 0

    def apply(self, commands: list[dict]) -> dict:
        self._step += 1
        document = self.repo.apply_commands(
            self.owner,
            self.deck_id,
            expected_revision=self.revision,
            mutation_id=f"k{self._step}",
            commands=commands,
        )
        self.revision = int(document["revision"])
        return document

    def unsorted(self) -> str:
        document = self.repo.get(self.owner, self.deck_id)
        return next(
            zone["id"] for zone in document["zones"] if zone["name"] == "Unsorted"
        )

    def entry(self, name: str) -> dict:
        document = self.repo.get(self.owner, self.deck_id)
        return next(entry for entry in document["entries"] if entry["name"] == name)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    path = tmp_path / "keys.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "commander",
                    "oracle-commander",
                    "Kinnan Test",
                    "{G}{U}",
                    2,
                    "Legendary Creature — Human",
                    "Partner",
                    '["G","U"]',
                    1,
                    1,
                ),
                (
                    "ring",
                    "oracle-ring",
                    "Sol Ring",
                    "{1}",
                    1,
                    "Artifact",
                    "Add mana",
                    "[]",
                    0,
                    1,
                ),
                (
                    "island",
                    "oracle-island",
                    "Island",
                    "",
                    0,
                    "Basic Land — Island",
                    "{T}: Add {U}",
                    '["U"]',
                    0,
                    1,
                ),
            ],
        )
        conn.commit()
    return _World(path, owner)


def _roundtrip(world: _World, commands: list[dict]) -> None:
    before = world.repo.get(world.owner, world.deck_id)
    after = world.apply(commands)
    planned = _plan(before, commands, after)
    assert planned["undoable"] is True, planned
    restored = world.apply(planned["inverse"])
    assert _project(restored) == _project(before)


@pytest.mark.parametrize(
    "kind",
    [
        "add_card",
        "add_card_stack",
        "remove_entry",
        "set_quantity",
        "set_quantity_zero",
        "adjust_quantity",
        "adjust_quantity_remove",
        "move_entry",
        "set_role",
        "rename_zone",
        "create_zone",
        "delete_zone",
    ],
)
def test_inverse_for_each_supported_command_kind(world: _World, kind: str) -> None:
    zone = world.unsorted()
    if kind == "add_card":
        _roundtrip(
            world,
            [{"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 1}],
        )
    elif kind == "add_card_stack":
        world.apply(
            [{"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 1}]
        )
        _roundtrip(
            world,
            [{"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 2}],
        )
    elif kind == "remove_entry":
        world.apply(
            [
                {
                    "type": "add_card",
                    "card_id": "ring",
                    "zone_id": zone,
                    "quantity": 1,
                    "role": "ramp",
                },
            ]
        )
        _roundtrip(
            world, [{"type": "remove_entry", "entry_id": world.entry("Sol Ring")["id"]}]
        )
    elif kind == "set_quantity":
        world.apply(
            [{"type": "add_card", "card_id": "island", "zone_id": zone, "quantity": 1}]
        )
        _roundtrip(
            world,
            [
                {
                    "type": "set_quantity",
                    "entry_id": world.entry("Island")["id"],
                    "quantity": 4,
                }
            ],
        )
    elif kind == "set_quantity_zero":
        world.apply(
            [{"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 2}]
        )
        _roundtrip(
            world,
            [
                {
                    "type": "set_quantity",
                    "entry_id": world.entry("Sol Ring")["id"],
                    "quantity": 0,
                }
            ],
        )
    elif kind == "adjust_quantity":
        world.apply(
            [{"type": "add_card", "card_id": "island", "zone_id": zone, "quantity": 2}]
        )
        _roundtrip(
            world,
            [
                {
                    "type": "adjust_quantity",
                    "entry_id": world.entry("Island")["id"],
                    "delta": 1,
                }
            ],
        )
    elif kind == "adjust_quantity_remove":
        world.apply(
            [{"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 1}]
        )
        _roundtrip(
            world,
            [
                {
                    "type": "adjust_quantity",
                    "entry_id": world.entry("Sol Ring")["id"],
                    "delta": -1,
                }
            ],
        )
    elif kind == "move_entry":
        world.apply(
            [
                {"type": "create_zone", "name": "Fast Mana", "zone_id": "fast"},
                {"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 1},
            ]
        )
        _roundtrip(
            world,
            [
                {
                    "type": "move_entry",
                    "entry_id": world.entry("Sol Ring")["id"],
                    "zone_id": "fast",
                    "sort_order": 3,
                }
            ],
        )
    elif kind == "set_role":
        world.apply(
            [{"type": "add_card", "card_id": "ring", "zone_id": zone, "quantity": 1}]
        )
        _roundtrip(
            world,
            [
                {
                    "type": "set_role",
                    "entry_id": world.entry("Sol Ring")["id"],
                    "role": "ramp",
                }
            ],
        )
    elif kind == "rename_zone":
        world.apply([{"type": "create_zone", "name": "Fast Mana", "zone_id": "fast"}])
        _roundtrip(world, [{"type": "rename_zone", "zone_id": "fast", "name": "Mana"}])
    elif kind == "create_zone":
        _roundtrip(
            world, [{"type": "create_zone", "name": "Interaction", "zone_id": "inter"}]
        )
    elif kind == "delete_zone":
        world.apply(
            [
                {"type": "create_zone", "name": "Fast Mana", "zone_id": "fast"},
                {
                    "type": "add_card",
                    "card_id": "ring",
                    "zone_id": "fast",
                    "quantity": 1,
                },
            ]
        )
        _roundtrip(world, [{"type": "delete_zone", "zone_id": "fast"}])
    else:
        raise AssertionError(kind)


def test_undo_redo_roundtrip_through_command_api() -> None:
    payload = _payload("roundtrip")
    assert payload["bodies"][0] == [{"type": "remove_entry", "entry_id": "entry-ring"}]
    inverse = payload["bodies"][1]
    assert inverse[0]["type"] == "add_card"
    assert inverse[0]["card_id"] == "ring"
    assert inverse[0]["zone_id"] == "zone-main"
    assert inverse[0]["quantity"] == 1
    assert payload["bodies"][2] == [{"type": "remove_entry", "entry_id": "entry-ring"}]
    assert payload["afterRemove"]["undoTip"] == "Undo: remove Sol Ring"
    assert payload["afterUndo"]["redoTip"] == "Redo: remove Sol Ring"


def test_undo_buttons_disabled_and_tooltips_name_action() -> None:
    payload = _payload("buttons")
    assert payload["after"]["undoDisabled"] is False
    assert payload["after"]["redoDisabled"] is True
    assert payload["after"]["undoTip"] == "Undo: remove Sol Ring"
    assert payload["after"]["undoLabel"] == "Undo: remove Sol Ring"
    assert payload["afterUndo"]["undoDisabled"] is True
    assert payload["afterUndo"]["redoDisabled"] is False
    assert payload["afterUndo"]["redoTip"] == "Redo: remove Sol Ring"
    assert payload["stats"]["undo"] == 0
    assert payload["stats"]["redo"] == 1


def test_conflict_409_clears_history() -> None:
    payload = _payload("conflict")
    assert payload["before"]["undo"] == 1
    assert payload["after"]["undo"] == 0
    assert payload["after"]["redo"] == 0
    assert payload["buttons"]["undoDisabled"] is True
    assert payload["buttons"]["redoDisabled"] is True


def test_non_undoable_command_clears_redo_and_says_so() -> None:
    payload = _payload("nonundoable")
    assert payload["before"]["redo"] == 1
    assert payload["before"]["undo"] == 0
    assert payload["after"]["redo"] == 0
    assert payload["after"]["undo"] == 0
    assert payload["buttons"]["title"] == "Can't undo rename deck"
    assert payload["buttons"]["redoDisabled"] is True


def test_history_capped_at_50() -> None:
    payload = _payload("cap")
    assert payload["undo"] == 50
    assert payload["oldest"][0]["role"] == "step-1"
    assert payload["newest"][0]["role"] == "step-50"


def test_slash_focuses_search_but_not_inside_inputs() -> None:
    payload = _payload("slash")
    assert payload["fromButton"] == "card-search"
    assert payload["fromField"] == "other-field"


def test_shift_enter_adds_first_result() -> None:
    payload = _payload("shiftenter")
    adds = [
        body for body in payload["bodies"] if body and body[0]["type"] == "add_card"
    ]
    assert len(adds) == 1
    assert adds[0][0]["card_id"] == "sol"
    assert adds[0][0]["zone_id"] == "zone-main"


def test_jk_navigation_and_plus_minus_delete_on_current_entry() -> None:
    payload = _payload("navigate")
    assert payload["focused"] == [
        "entry-cmd",
        "entry-ring",
        "entry-cmd",
        "entry-ring",
        "entry-cmd",
        "entry-lotus",
    ]
    assert payload["afterPlus"] == 2
    assert payload["afterMinus"] == 1
    assert payload["removed"] is True
    assert payload["undoTip"] == "Undo: remove Sol Ring"
    assert payload["restored"] == 1
    assert payload["deleted"][0]["type"] == "remove_entry"
    assert payload["deleted"][0]["entry_id"] == "entry-lotus"
    assert payload["bodies"][0] == [
        {"type": "adjust_quantity", "entry_id": "entry-ring", "delta": 1}
    ]


def test_x_toggles_selection_and_m_opens_zone_select() -> None:
    payload = _payload("selection")
    assert payload["selected"] == ["entry-ring"]
    assert payload["cleared"] == []
    assert payload["spaced"] == ["entry-ring"]
    assert payload["tag"] == "SELECT"
    assert "dl-inline-zone-select" in payload["className"]


def _assert_keys(text: str) -> None:
    for token in HELP_KEYS:
        if len(token) == 1 and token.isalpha():
            assert re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", text), token
        elif token == "Shift+Enter":
            ok = "<kbd>Shift</kbd>+<kbd>Enter</kbd>" in text or "Shift + Enter" in text
            assert ok, token
        elif token == "ArrowDown":
            ok = "<kbd>↓</kbd>" in text or "↓" in text
            assert ok, token
        elif token == "ArrowUp":
            ok = "<kbd>↑</kbd>" in text or "↑" in text
            assert ok, token
        elif token == "Cmd+Z":
            ok = "<kbd>⌘ Z</kbd>" in text or "⌘ Z" in text
            assert ok, token
        elif token == "Ctrl+Z":
            ok = "<kbd>Ctrl Z</kbd>" in text or "Ctrl Z" in text
            assert ok, token
        elif token == "Cmd+Shift+Z":
            ok = "<kbd>⌘ Shift Z</kbd>" in text or "⌘ Shift Z" in text
            assert ok, token
        elif token == "Ctrl+Shift+Z":
            ok = "<kbd>Ctrl Shift Z</kbd>" in text or "Ctrl Shift Z" in text
            assert ok, token
        elif token == "Ctrl+Y":
            ok = "<kbd>Ctrl Y</kbd>" in text or "Ctrl Y" in text
            assert ok, token
        else:
            assert token in text, token


def test_help_dialog_lists_all_keys() -> None:
    html = TEMPLATE.read_text()
    start = html.find("<dialog")
    dialogs = []
    while start >= 0:
        end = html.find("</dialog>", start)
        chunk = html[start : end + len("</dialog>")]
        if "data-hotkeys-help" in chunk:
            dialogs.append(chunk)
        start = html.find("<dialog", end)
    assert len(dialogs) == 1
    _assert_keys(dialogs[0])
    payload = _payload("help")
    assert payload["open"] is True
    _assert_keys(payload["text"])


def test_nothing_installed_in_shared_mode() -> None:
    payload = _payload("shared")
    assert payload["sameCommand"] is True
    assert payload["undoInstalled"] is False
    assert payload["searchFocused"] is False
    assert payload["stayed"] == "plain-button"


def test_builder_internal_mutation_is_undoable() -> None:
    payload = _payload("internal")
    assert payload["bodies"][0] == [
        {"type": "adjust_quantity", "entry_id": "entry-ring", "delta": 1}
    ]
    assert payload["quantity"] == 2
    assert payload["undoTip"] == "Undo: change Sol Ring quantity"
    assert payload["bodies"][1] == [
        {"type": "set_quantity", "entry_id": "entry-ring", "quantity": 1}
    ]
    assert payload["restored"] == 1
