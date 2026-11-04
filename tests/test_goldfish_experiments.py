"""GoldfishExperimentsRepo: owner-scoped, immutable goldfish diagnostics log."""

from __future__ import annotations

from typing import Any

import pytest

from sabermetrics.goldfish_experiments import GoldfishExperimentsRepo

OWNER = "owner-1"
OTHER_OWNER = "owner-2"


def _entry(
    oracle_id: str,
    name: str,
    *,
    quantity: int = 1,
    is_commander: bool = False,
    zone_name: str = "Unsorted",
    role: str = "flex",
) -> dict[str, Any]:
    return {
        "oracle_id": oracle_id,
        "name": name,
        "quantity": quantity,
        "is_commander": is_commander,
        "zone_name": zone_name,
        "role": role,
        "color_identity": [],
    }


def _library(n: int = 99, prefix: str = "lib") -> list[dict[str, Any]]:
    return [_entry(f"{prefix}-{i}", f"Library Card {i}") for i in range(n)]


def _document(
    *,
    deck_id: str = "deck-1",
    revision: int = 1,
    commander_oracle: str = "cmd-1",
    library: list[dict[str, Any]] | None = None,
    legal: bool = True,
) -> dict[str, Any]:
    library_entries = library if library is not None else _library()
    entries = [
        _entry(commander_oracle, "Commander One", is_commander=True),
        *library_entries,
    ]
    total = sum(e["quantity"] for e in entries)
    return {
        "id": deck_id,
        "revision": revision,
        "entries": entries,
        "validation": {
            "total_count": total,
            "legal": legal,
            "issues": [] if legal else ["The deck is not legal."],
        },
    }


def _replace_cards(
    library: list[dict[str, Any]], old_prefix_indices: list[int], new_prefix: str
) -> list[dict[str, Any]]:
    """Swap the library entries at ``old_prefix_indices`` for new unique cards."""
    out = [dict(entry) for entry in library]
    for n, index in enumerate(old_prefix_indices):
        out[index] = _entry(f"{new_prefix}-{n}", f"New Card {n}")
    return out


@pytest.fixture
def repo(tmp_path) -> GoldfishExperimentsRepo:
    return GoldfishExperimentsRepo(tmp_path / "goldfish.db")


SETTINGS = {
    "plan_id": "vivi-affordable",
    "games": 200,
    "seed": 20261010,
    "pilot_version": "1",
}


def test_create_and_get_round_trip(repo: GoldfishExperimentsRepo) -> None:
    document = _document()
    row = repo.create(OWNER, document, SETTINGS, hypothesis="baseline run")
    assert row["status"] == "queued"
    assert row["hypothesis"] == "baseline run"
    assert row["baseline_id"] == row["id"]
    assert row["parent_id"] is None
    assert row["result"] is None

    fetched = repo.get(OWNER, row["id"])
    assert fetched is not None
    assert fetched["document"] == document
    assert fetched["settings"] == SETTINGS
    assert fetched["deck_id"] == "deck-1"
    assert fetched["deck_revision"] == 1


def test_foreign_owner_never_sees_row(repo: GoldfishExperimentsRepo) -> None:
    document = _document()
    row = repo.create(OWNER, document, SETTINGS)

    assert repo.get(OTHER_OWNER, row["id"]) is None
    assert repo.history(OTHER_OWNER, "deck-1") == []
    with pytest.raises(ValueError):
        repo.finish(OTHER_OWNER, row["id"], {"ok": True})
    with pytest.raises(ValueError):
        repo.fail(OTHER_OWNER, row["id"], "nope")
    # The real owner is unaffected by the foreign owner's rejected calls.
    assert repo.get(OWNER, row["id"])["status"] == "queued"


def test_snapshot_immutable_after_caller_mutates_original_dict(
    repo: GoldfishExperimentsRepo,
) -> None:
    document = _document()
    settings = dict(SETTINGS)
    row = repo.create(OWNER, document, settings)

    # Mutate the caller's own objects after the call returns.
    document["entries"][0]["name"] = "Mutated Commander"
    document["entries"][1]["quantity"] = 999
    document["revision"] = 999
    settings["games"] = 999999

    fetched = repo.get(OWNER, row["id"])
    assert fetched is not None
    assert fetched["document"]["entries"][0]["name"] == "Commander One"
    assert fetched["document"]["entries"][1]["quantity"] == 1
    assert fetched["deck_revision"] == 1
    assert fetched["settings"]["games"] == 200

    # The dict returned by create() itself must not alias the stored copy.
    row["document"]["entries"][0]["name"] = "Also mutated"
    fetched_again = repo.get(OWNER, row["id"])
    assert fetched_again["document"]["entries"][0]["name"] == "Commander One"


def test_three_replacements_accepted_four_rejected(
    repo: GoldfishExperimentsRepo,
) -> None:
    parent_document = _document()
    parent = repo.create(OWNER, parent_document, SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    three_swaps = _document(library=_replace_cards(_library(), [0, 1, 2], "new"))
    child = repo.create(OWNER, three_swaps, SETTINGS, parent_id=parent["id"])
    assert child["parent_id"] == parent["id"]
    assert child["baseline_id"] == parent["id"]

    four_swaps = _document(library=_replace_cards(_library(), [0, 1, 2, 3], "new"))
    with pytest.raises(ValueError, match="replacements"):
        repo.create(OWNER, four_swaps, SETTINGS, parent_id=parent["id"])


def test_identical_rerun_allowed(repo: GoldfishExperimentsRepo) -> None:
    parent_document = _document()
    parent = repo.create(OWNER, parent_document, SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    rerun = repo.create(OWNER, _document(), SETTINGS, parent_id=parent["id"])
    assert rerun["parent_id"] == parent["id"]


def test_quantity_change_counted_toward_replacement_budget(
    repo: GoldfishExperimentsRepo,
) -> None:
    parent_document = _document()
    parent = repo.create(OWNER, parent_document, SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    # Bump one card's quantity by 3 (net +3) and drop three singleton cards
    # entirely to keep the library at 99 — a quantity-only move that must be
    # counted as 3 replacements, not as "1 card changed."
    library = _library()
    library[0] = dict(library[0], quantity=4)
    accepted_library = [entry for i, entry in enumerate(library) if i not in (1, 2, 3)]
    accepted = _document(library=accepted_library)
    child = repo.create(OWNER, accepted, SETTINGS, parent_id=parent["id"])
    assert child["id"]

    library = _library()
    library[0] = dict(library[0], quantity=5)
    rejected_library = [
        entry for i, entry in enumerate(library) if i not in (1, 2, 3, 4)
    ]
    rejected = _document(library=rejected_library)
    with pytest.raises(ValueError, match="replacements"):
        repo.create(OWNER, rejected, SETTINGS, parent_id=parent["id"])


def test_basic_land_quantity_loophole_rejected(repo: GoldfishExperimentsRepo) -> None:
    # 95 nonland cards plus four singleton basics, 99 cards total.
    nonland = _library(n=95)
    basics = [
        _entry("land-plains", "Plains"),
        _entry("land-island", "Island"),
        _entry("land-mountain", "Mountain"),
        _entry("land-swamp", "Swamp"),
    ]
    parent_document = _document(library=[*nonland, *basics])
    parent = repo.create(OWNER, parent_document, SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    # Consolidating four singleton basics into one 4x basic is a 4-card
    # replacement by quantity, same as any other card — basics get no
    # exemption from the budget just because they are "fungible" lands.
    consolidated_basics = [_entry("land-forest", "Forest", quantity=4)]
    child_document = _document(library=[*nonland, *consolidated_basics])
    with pytest.raises(ValueError, match="replacements"):
        repo.create(OWNER, child_document, SETTINGS, parent_id=parent["id"])


def test_commander_swap_rejected_even_with_balanced_library(
    repo: GoldfishExperimentsRepo,
) -> None:
    parent_document = _document(commander_oracle="cmd-1")
    parent = repo.create(OWNER, parent_document, SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    swapped_commander = _document(commander_oracle="cmd-2")
    with pytest.raises(ValueError, match="[Cc]ommander"):
        repo.create(OWNER, swapped_commander, SETTINGS, parent_id=parent["id"])


def test_settings_mismatch_rejected(repo: GoldfishExperimentsRepo) -> None:
    parent = repo.create(OWNER, _document(), SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    different_settings = dict(SETTINGS, games=201)
    with pytest.raises(ValueError, match="[Ss]ettings"):
        repo.create(OWNER, _document(), different_settings, parent_id=parent["id"])


def test_incomplete_parent_rejected(repo: GoldfishExperimentsRepo) -> None:
    parent = repo.create(OWNER, _document(), SETTINGS)
    # Still queued: never finished.
    with pytest.raises(ValueError):
        repo.create(OWNER, _document(), SETTINGS, parent_id=parent["id"])


def test_cross_deck_parent_rejected(repo: GoldfishExperimentsRepo) -> None:
    parent = repo.create(OWNER, _document(deck_id="deck-A"), SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    other_deck_document = _document(deck_id="deck-B")
    with pytest.raises(ValueError, match="deck"):
        repo.create(OWNER, other_deck_document, SETTINGS, parent_id=parent["id"])


def test_cross_owner_parent_rejected(repo: GoldfishExperimentsRepo) -> None:
    parent = repo.create(OWNER, _document(), SETTINGS)
    repo.finish(OWNER, parent["id"], {"tests": {}})

    with pytest.raises(ValueError):
        repo.create(OTHER_OWNER, _document(), SETTINGS, parent_id=parent["id"])


def test_root_and_parent_chain_retained(repo: GoldfishExperimentsRepo) -> None:
    root = repo.create(OWNER, _document(), SETTINGS)
    repo.finish(OWNER, root["id"], {"tests": {}})
    assert root["baseline_id"] == root["id"]

    child_document = _document(library=_replace_cards(_library(), [0], "mid"))
    child = repo.create(OWNER, child_document, SETTINGS, parent_id=root["id"])
    repo.finish(OWNER, child["id"], {"tests": {}})
    assert child["parent_id"] == root["id"]
    assert child["baseline_id"] == root["id"]

    grandchild_document = _document(library=_replace_cards(_library(), [0, 1], "late"))
    grandchild = repo.create(
        OWNER, grandchild_document, SETTINGS, parent_id=child["id"]
    )
    assert grandchild["parent_id"] == child["id"]
    assert grandchild["baseline_id"] == root["id"]

    history = repo.history(OWNER, "deck-1")
    assert [row["id"] for row in history] == [
        grandchild["id"],
        child["id"],
        root["id"],
    ]


def test_unknown_result_values_stored_without_zero_filling(
    repo: GoldfishExperimentsRepo,
) -> None:
    row = repo.create(OWNER, _document(), SETTINGS)
    sparse_result = {
        "tests": {
            "opening": {"status": "unsupported", "metrics": {}, "limitations": []},
        },
        "note": "engine reported nothing else",
    }
    repo.finish(OWNER, row["id"], sparse_result)

    fetched = repo.get(OWNER, row["id"])
    assert fetched["result"] == sparse_result
    assert "finish_turn_count" not in fetched["result"]
    assert fetched["status"] == "done"


def test_finish_rejects_duplicate_completion(repo: GoldfishExperimentsRepo) -> None:
    row = repo.create(OWNER, _document(), SETTINGS)
    repo.finish(OWNER, row["id"], {"tests": {}, "run": 1})

    with pytest.raises(ValueError, match="already finished"):
        repo.finish(OWNER, row["id"], {"tests": {}, "run": 2})

    fetched = repo.get(OWNER, row["id"])
    assert fetched["result"] == {"tests": {}, "run": 1}


def test_fail_records_error_and_is_blocked_after_done(
    repo: GoldfishExperimentsRepo,
) -> None:
    row = repo.create(OWNER, _document(), SETTINGS)
    repo.fail(OWNER, row["id"], "engine crashed")
    fetched = repo.get(OWNER, row["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "engine crashed"

    done_row = repo.create(OWNER, _document(deck_id="deck-2"), SETTINGS)
    repo.finish(OWNER, done_row["id"], {"tests": {}})
    with pytest.raises(ValueError):
        repo.fail(OWNER, done_row["id"], "too late")


def test_illegal_document_rejected(repo: GoldfishExperimentsRepo) -> None:
    with pytest.raises(ValueError):
        repo.create(OWNER, _document(legal=False), SETTINGS)


def test_missing_parent_rejected(repo: GoldfishExperimentsRepo) -> None:
    with pytest.raises(ValueError):
        repo.create(OWNER, _document(), SETTINGS, parent_id="does-not-exist")
