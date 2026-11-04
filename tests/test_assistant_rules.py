"""Local rules snapshot import and retrieval.

Every rules text here is a SYNTHETIC fixture. Rule numbers mirror the shape of
the Comprehensive Rules so the parser and anchors are exercised, but the wording
is invented and nothing below asserts it is official rules text.
"""

from __future__ import annotations

import hashlib
import os
import socket
import sqlite3
from pathlib import Path

import pytest

from sabermetrics.assistant_rules import (
    MAX_HIT_CHARS,
    ingest_rules,
    retrieve_rules,
)
from scripts.prepare_assistant_rules import main as prepare_main

URL = "https://media.wizards.com/synthetic/rules.txt"

SYNTHETIC_RULES = """\
SYNTHETIC FIXTURE - not official rules text.

Contents
1. Game Concepts
Glossary
Credits

1. Game Concepts

100. General

100.1. Synthetic: these fixture rules describe a game.

100.2. Synthetic: a ring of players sits around the table.

107. Symbols

107.5. Synthetic: the tap symbol in a cost means tap this permanent.

2. Parts of a Card

302. Creatures

302.6. Synthetic: a creature's ability with the tap symbol in its cost can't be activated unless its controller has controlled it continuously since their most recent turn began. Tapping other untapped creatures as a cost is not restricted by this rule. Ignore this rule for creatures with haste (see rule 702.10).

602. Activating Abilities

602.5. Synthetic: a player can't begin to activate an ability that's prohibited.

602.5a Synthetic: a creature's tap-symbol ability is prohibited under rule 302.6 unless it has haste.

701. Keyword Actions

701.9. Amass

701.9a Synthetic: to amass N, if you don't control an Army creature, create a 0/0 Army token. Choose an Army creature you control and put N +1/+1 counters on it.

701.9b Synthetic: an Army you already control receives the counters; no new token is created.

702. Keyword Abilities

702.10. Haste

702.10a Synthetic: haste is a static ability.

702.10b Synthetic: a creature with haste can attack and use tap abilities this turn.

702.19. Trample

702.19a Synthetic: trample is a static ability that modifies combat damage.

702.19b Synthetic: excess combat damage from a trampler is assigned to the player.

Glossary

Haste
Synthetic glossary entry. See rule 702.10.

Credits

Synthetic credits.
"""

LATHRIL = {
    "oracle_id": "oid-lathril",
    "name": "Lathril, Blade of the Elves",
    "type_line": "Legendary Creature — Elf Noble",
    "oracle_text": "Menace\n{T}, Tap ten untapped Elves you control: Each opponent loses 10 life.",
}
SOL_RING = {
    "oracle_id": "oid-sol-ring",
    "name": "Sol Ring",
    "type_line": "Artifact",
    "oracle_text": "{T}: Add {C}{C}.",
}
AMASSER = {
    "oracle_id": "oid-amasser",
    "name": "Synthetic Orc Captain",
    "type_line": "Creature — Orc",
    "oracle_text": "When this enters, amass Orcs 2.",
}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "rules.db"
    ingest_rules(path, SYNTHETIC_RULES, source_url=URL, version="synthetic-1")
    return path


def _ids(evidence) -> list[str]:
    return [h.source_id for h in evidence.hits]


def _add_rulings(path: Path, rows: list[tuple[str, str, str | None, str]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS card_rulings (id INTEGER PRIMARY KEY AUTOINCREMENT, card_oracle_id TEXT NOT NULL, ruling_date DATE, ruling_text TEXT NOT NULL, source TEXT DEFAULT 'mtgapi')"
        )
        conn.executemany(
            "INSERT INTO card_rulings (card_oracle_id, ruling_text, ruling_date, source) VALUES (?,?,?,?)",
            rows,
        )


def _rules_text(path: Path) -> dict[str, str]:
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT rule_id, text FROM assistant_rules"))


# --- import ---------------------------------------------------------------


def test_import_parses_rules_and_stops_at_headings(db: Path) -> None:
    texts = _rules_text(db)
    assert "302.6" in texts and "602.5a" in texts and "701.9b" in texts
    # Section headings, the glossary and credits never leak into a rule body.
    assert "Parts of a Card" not in texts["107.5"]
    assert "Glossary" not in texts["702.19b"]
    assert "Creatures" not in texts["107.5"]
    assert texts["100.1"].startswith("100.1. Synthetic")


def test_import_records_raw_source_hash_and_verifies_expected(tmp_path: Path) -> None:
    raw = b"\xef\xbb\xbf" + SYNTHETIC_RULES.replace("\n", "\r\n").encode()
    meta = ingest_rules(
        tmp_path / "r.db",
        raw,
        source_url=URL,
        version=" synthetic-crlf ",
        expected_sha256=hashlib.sha256(raw).hexdigest().upper(),
    )
    assert meta["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["source_version"] == "synthetic-crlf"
    assert "\r" not in _rules_text(tmp_path / "r.db")["302.6"]
    with pytest.raises(ValueError, match="SHA-256"):
        ingest_rules(
            tmp_path / "r.db",
            raw,
            source_url=URL,
            version="x",
            expected_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://media.wizards.com/rules.txt",
        "https://media.wizards.com.evil.example/rules.txt",
        "https://user:pw@media.wizards.com/rules.txt",
        "https://media.wizards.com:8443/rules.txt",
        "https://example.com/rules.txt",
    ],
)
def test_import_rejects_non_wizards_sources(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="Wizards"):
        ingest_rules(tmp_path / "r.db", SYNTHETIC_RULES, source_url=url, version="v")


@pytest.mark.parametrize(
    "bad_text",
    [
        "no numbered rules here",
        SYNTHETIC_RULES + "\n" + SYNTHETIC_RULES,  # two editions concatenated
        SYNTHETIC_RULES.replace("100.2. Synthetic", "702.30. Synthetic"),  # reordered
        SYNTHETIC_RULES.replace(  # truncated rule body
            "100.1. Synthetic: these fixture rules describe a game.", "100.1. x"
        ),
        b"\xff\xfe not utf-8 " + SYNTHETIC_RULES.encode(),
    ],
)
def test_malformed_or_mixed_input_keeps_previous_snapshot(db: Path, bad_text) -> None:
    before = _rules_text(db)
    with pytest.raises(ValueError):
        ingest_rules(db, bad_text, source_url=URL, version="synthetic-2")
    assert _rules_text(db) == before
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT source_version FROM assistant_rules_metadata"
        ).fetchone() == ("synthetic-1",)


@pytest.mark.parametrize("version", ["", "   ", "x" * 121, "v1\nv2"])
def test_import_rejects_bad_versions(tmp_path: Path, version: str) -> None:
    with pytest.raises(ValueError, match="version"):
        ingest_rules(
            tmp_path / "r.db", SYNTHETIC_RULES, source_url=URL, version=version
        )


def test_reimport_replaces_snapshot_and_fts(db: Path) -> None:
    replacement = SYNTHETIC_RULES.replace(
        "701.9a Synthetic: to amass", "701.9a Synthetic v2: to amass"
    )
    ingest_rules(db, replacement, source_url=URL, version="synthetic-2")
    evidence = retrieve_rules(db, "how does amass work")
    assert evidence.rules_snapshot["source_version"] == "synthetic-2"
    assert any("Synthetic v2" in h.text for h in evidence.hits)
    with sqlite3.connect(db) as conn:
        fts_rows = conn.execute("SELECT COUNT(*) FROM assistant_rules_fts").fetchone()[
            0
        ]
        rule_rows = conn.execute("SELECT COUNT(*) FROM assistant_rules").fetchone()[0]
    assert fts_rows == rule_rows


def test_prepare_cli_requires_hash_and_reports_rejection(
    db: Path, tmp_path: Path, capsys
) -> None:
    text_file = tmp_path / "rules.txt"
    text_file.write_bytes(SYNTHETIC_RULES.encode())
    common = ["--db-path", str(db), "--text-file", str(text_file), "--source-url", URL]
    assert prepare_main([*common, "--version", "cli", "--sha256", "0" * 64]) == 1
    assert "previous snapshot kept" in capsys.readouterr().err
    digest = hashlib.sha256(text_file.read_bytes()).hexdigest()
    assert prepare_main([*common, "--version", "cli", "--sha256", digest]) == 0
    assert '"source_version": "cli"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        prepare_main([*common, "--version", "cli"])  # --sha256 is mandatory


# --- retrieval --------------------------------------------------------------


def test_missing_database_or_snapshot_is_unavailable_not_negative(
    tmp_path: Path,
) -> None:
    missing = retrieve_rules(tmp_path / "absent.db", "trample")
    assert missing.status == "unavailable" and missing.hits == []
    assert not (tmp_path / "absent.db").exists()  # read-only open never creates
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    evidence = retrieve_rules(empty, "trample", cards=[LATHRIL])
    assert evidence.status == "unavailable"
    assert evidence.rulings_status == "unavailable"


def test_explicit_rule_ids_are_case_insensitive_and_expand(db: Path) -> None:
    evidence = retrieve_rules(db, "What does CR602.5A say?", limit=4)
    assert _ids(evidence)[:3] == ["CR 602.5a", "CR 602.5", "CR 302.6"]
    header = retrieve_rules(db, "rule 702.19", limit=2)
    # A bare keyword header carries no rule text; its subrules are returned.
    assert _ids(header) == ["CR 702.19a", "CR 702.19b"]
    assert all(h.source_url == URL and h.version == "synthetic-1" for h in header.hits)


def test_unknown_rule_id_is_reported_not_invented(db: Path) -> None:
    evidence = retrieve_rules(db, "rule 999.9")
    assert "CR 999.9" not in _ids(evidence)
    assert any("999.9 is not in the loaded snapshot" in x for x in evidence.limitations)


def test_lathril_tap_cost_surfaces_summoning_sickness_scope(db: Path) -> None:
    evidence = retrieve_rules(
        db, "Can Lathril tap ten elves the turn it comes in?", cards=[LATHRIL], limit=4
    )
    ids = _ids(evidence)
    assert ids[0] == "CR 302.6"
    assert "CR 602.5a" in ids
    assert "Tapping other untapped creatures" in evidence.hits[0].text
    # "Ring" in an unrelated rule must not match through card-name words.
    assert "CR 100.2" not in ids


def test_card_lookup_by_name_uses_card_text_not_name_words(db: Path) -> None:
    lathril = retrieve_rules(
        db, "Lathril, Blade of the Elves", cards=[LATHRIL], limit=3
    )
    assert lathril.hits and lathril.hits[0].source_id == "CR 302.6"
    ring = retrieve_rules(db, "Sol Ring", cards=[SOL_RING], limit=3)
    # A noncreature "{T}:" is not a summoning sickness question, and the word
    # "ring" in the card name is not a rules concept.
    assert ring.status == "no_matches" and ring.hits == []
    assert any("does not establish that no rule applies" in x for x in ring.limitations)


def test_amass_returns_existing_army_clause(db: Path) -> None:
    by_query = retrieve_rules(db, "Does amass put counters on my existing army?")
    assert {"CR 701.9a", "CR 701.9b"} <= set(_ids(by_query))
    by_card = retrieve_rules(db, AMASSER["name"], cards=[AMASSER], limit=2)
    assert _ids(by_card) == ["CR 701.9a", "CR 701.9b"]


def test_generic_words_alone_do_not_produce_hits(db: Path) -> None:
    evidence = retrieve_rules(db, "should I play this card in my deck this turn")
    assert evidence.hits == []
    assert evidence.status == "no_matches"


def test_bounds_are_enforced(db: Path) -> None:
    with pytest.raises(ValueError):
        retrieve_rules(db, "")
    with pytest.raises(ValueError):
        retrieve_rules(db, "   ")
    with pytest.raises(ValueError):
        retrieve_rules(db, "x" * 301)
    for limit in (0, 7):
        with pytest.raises(ValueError):
            retrieve_rules(db, "trample", limit=limit)
    many = [{"oracle_id": f"oid-{i}", "name": f"Card {i}"} for i in range(17)]
    with pytest.raises(ValueError, match="16"):
        retrieve_rules(db, "trample", cards=many)
    # Duplicates and non-dict entries do not count against the scope.
    retrieve_rules(db, "trample", cards=[LATHRIL] * 40 + ["junk", None])
    assert len(retrieve_rules(db, "trample haste amass tap", limit=6).hits) <= 6


def test_long_rule_text_is_truncated_and_flagged(tmp_path: Path) -> None:
    long_body = "Synthetic trample detail. " * 100
    text = SYNTHETIC_RULES.replace(
        "702.19b Synthetic: excess", "702.19b Synthetic: " + long_body + "excess"
    )
    path = tmp_path / "r.db"
    ingest_rules(path, text, source_url=URL, version="long")
    hit = next(h for h in retrieve_rules(path, "rule 702.19b").hits)
    assert hit.text_truncated and len(hit.text) == MAX_HIT_CHARS
    full = _rules_text(path)["702.19b"]
    assert hit.content_sha256 == hashlib.sha256(full.encode()).hexdigest()


def test_tampered_rule_text_is_withheld(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE assistant_rules SET text='302.6. altered text' WHERE rule_id='302.6'"
        )
    evidence = retrieve_rules(db, "rule 302.6")
    assert "CR 302.6" not in _ids(evidence)
    assert any("failed its hash check" in x for x in evidence.limitations)


def test_snapshot_count_mismatch_is_unavailable(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM assistant_rules WHERE rule_id='100.2'")
    evidence = retrieve_rules(db, "rule 302.6")
    assert evidence.status == "unavailable" and evidence.hits == []


def test_rulings_are_scoped_attributed_and_not_authenticated(db: Path) -> None:
    _add_rulings(
        db,
        [
            (
                "oid-lathril",
                "Synthetic ruling: you may tap elves that just arrived.",
                "2021-02-05",
                "mtgapi",
            ),
            (
                "oid-other",
                "Synthetic ruling about an unrelated card.",
                "2022-01-01",
                "mtgapi",
            ),
        ],
    )
    evidence = retrieve_rules(
        db, "Can Lathril tap elves that just arrived?", cards=[LATHRIL], limit=3
    )
    rulings = [h for h in evidence.hits if h.kind == "card_ruling"]
    assert evidence.rulings_status == "available"
    assert [h.card_oracle_id for h in rulings] == ["oid-lathril"]
    assert "not independently verified" in rulings[0].source
    assert rulings[0].source_url is None and rulings[0].version == "2021-02-05"
    assert any(h.kind == "rule" for h in evidence.hits)  # rules still present
    one = retrieve_rules(db, "rule 302.6", cards=[LATHRIL], limit=1)
    assert _ids(one) == ["CR 302.6"]  # a ruling never displaces the only rule


def test_no_ruling_in_scope_is_not_negative_proof(db: Path) -> None:
    _add_rulings(db, [("oid-other", "Synthetic ruling.", None, "mtgapi")])
    evidence = retrieve_rules(db, "trample", cards=[LATHRIL])
    assert evidence.rulings_status == "no_matches"
    assert any(
        "does not establish that no ruling exists" in x for x in evidence.limitations
    )
    unscoped = retrieve_rules(db, "trample")
    assert unscoped.rulings_status == "unavailable"
    assert any("rulings were not searched" in x for x in unscoped.limitations)


def test_unreadable_rulings_table_is_unavailable(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE card_rulings (oracle TEXT, body TEXT)")
    evidence = retrieve_rules(db, "rule 302.6", cards=[LATHRIL])
    assert evidence.rulings_status == "unavailable"
    assert evidence.status == "available"  # rule evidence unaffected


# --- mana abilities -----------------------------------------------------------

# Defining phrases match the real edition's wording so the phrase-verified
# anchors are exercised; every sentence around them is synthetic.
MANA_RULES = (
    SYNTHETIC_RULES.replace(
        "107. Symbols",
        """\
100.3. Synthetic: a player may play a card from their deck on their turn in this game.

106. Mana

106.12. Synthetic: to “tap [a permanent] for mana” is to activate a mana ability of that permanent with the tap symbol in its cost. See rule 605.

106.12a Synthetic: an ability that triggers whenever a permanent “is tapped for mana” triggers whenever such a mana ability resolves.

107. Symbols""",
    )
    .replace(
        "602. Activating Abilities",
        """\
601. Casting Spells

601.2. Synthetic: casting a cascade spell with a nonland permanent and colored mana.

602. Activating Abilities""",
    )
    .replace(
        "701. Keyword Actions",
        """\
605. Mana Abilities

605.1. Synthetic: some abilities are mana abilities.

605.1a Synthetic: an activated ability is a mana ability if it has no target and could add mana.

605.1b Synthetic: a triggered ability is a mana ability if it triggers from activating a mana ability and could add mana.

605.3. Synthetic: activating mana abilities.

605.3b Synthetic: an activated mana ability doesn’t go on the stack and resolves immediately.

701. Keyword Actions""",
    )
)
KINNAN = {
    "oracle_id": "oid-kinnan",
    "name": "Kinnan, Bonder Prodigy",
    "type_line": "Legendary Creature — Human Druid",
    "oracle_text": "Whenever you tap a nonland permanent for mana, add one additional mana of any type that permanent produced.",
}
BIRDS = {
    "oracle_id": "oid-birds",
    "name": "Birds of Paradise",
    "type_line": "Creature — Bird",
    "oracle_text": "Flying\n{T}: Add one mana of any color.",
}


@pytest.fixture
def mana_db(tmp_path: Path) -> Path:
    path = tmp_path / "mana.db"
    ingest_rules(path, MANA_RULES, source_url=URL, version="synthetic-mana")
    return path


def test_mana_ability_question_retains_defining_rules(mana_db: Path) -> None:
    # Regression: "nonland", "mana" and "permanent" made a cascade rule outrank
    # the definition of tapping for mana and the mana-ability criteria.
    evidence = retrieve_rules(mana_db, "Kinnan mana ability nonland permanent", limit=4)
    assert _ids(evidence)[:3] == ["CR 106.12", "CR 605.1a", "CR 605.1b"]
    assert evidence.hits[0].version == "synthetic-mana"
    # Card names are identity, not concepts; the scoped trigger card leads with
    # the triggered-ability criteria and the "is tapped for mana" clause.
    scoped = retrieve_rules(
        mana_db, "Is Kinnan's trigger a mana ability?", cards=[KINNAN]
    )
    assert _ids(scoped)[:3] == ["CR 106.12", "CR 605.1b", "CR 106.12a"]


def test_card_lookup_of_tapped_for_mana_trigger_uses_mana_rules(mana_db: Path) -> None:
    lookup = retrieve_rules(mana_db, "Kinnan, Bonder Prodigy", cards=[KINNAN], limit=3)
    assert _ids(lookup) == ["CR 106.12", "CR 605.1b", "CR 106.12a"]
    # A mana rock's "{T}: Add" alone is not a mana-ability question.
    ring = retrieve_rules(mana_db, "Sol Ring", cards=[SOL_RING], limit=3)
    assert ring.hits == []


def test_stack_question_adds_no_stack_clause(mana_db: Path) -> None:
    evidence = retrieve_rules(
        mana_db, "Does tapping a mana rock for mana use the stack?", limit=3
    )
    assert _ids(evidence) == ["CR 106.12", "CR 605.3b", "CR 605.1a"]
    assert "go on the stack" in evidence.hits[1].text


def test_sickness_and_mana_anchors_share_the_limit(mana_db: Path) -> None:
    evidence = retrieve_rules(
        mana_db,
        "Can Birds of Paradise tap for mana with summoning sickness?",
        cards=[BIRDS],
        limit=4,
    )
    assert _ids(evidence) == ["CR 302.6", "CR 106.12", "CR 602.5a", "CR 605.1a"]


def test_mana_anchor_follows_renumbering_by_text_not_number(tmp_path: Path) -> None:
    moved = MANA_RULES.replace(
        "106.12. Synthetic: to “tap [a permanent] for mana”",
        "106.11. Synthetic: an unrelated rule about mana pools.\n\n106.13. Synthetic: to “tap [a permanent] for mana”",
    ).replace("106.12a Synthetic", "106.13a Synthetic")
    path = tmp_path / "moved.db"
    ingest_rules(path, moved, source_url=URL, version="moved")
    ids = _ids(retrieve_rules(path, "what is a mana ability", limit=3))
    assert ids[0] == "CR 106.13" and "CR 106.12" not in ids
    # Two rules carrying the defining phrase are ambiguous: no anchor is guessed.
    doubled = moved.replace(
        "106.11. Synthetic: an unrelated rule about mana pools.",
        "106.11. Synthetic: to “tap [a permanent] for mana” is also defined here.",
    )
    path2 = tmp_path / "doubled.db"
    ingest_rules(path2, doubled, source_url=URL, version="doubled")
    ids2 = _ids(retrieve_rules(path2, "what is a mana ability", limit=3))
    assert ids2[:2] == ["CR 605.1a", "CR 605.1b"]


def test_generic_words_do_not_match_rules_that_contain_them(mana_db: Path) -> None:
    # 100.3 contains play/card/deck/turn/game; generic words alone still miss.
    evidence = retrieve_rules(mana_db, "should I play this card in my deck this turn")
    assert evidence.hits == [] and evidence.status == "no_matches"


@pytest.mark.skipif(
    not os.environ.get("SABER_RULES_PROBE_FILE"),
    reason="set SABER_RULES_PROBE_FILE to an imported-edition text file to probe it",
)
def test_probe_real_edition_mana_and_sickness(tmp_path: Path) -> None:
    """Opt-in probe of a real, operator-supplied edition; asserts phrase anchors only."""
    path = tmp_path / "real.db"
    raw = Path(os.environ["SABER_RULES_PROBE_FILE"]).read_bytes()
    ingest_rules(path, raw, source_url=URL, version="probe")
    kinnan = _ids(retrieve_rules(path, "Kinnan mana ability nonland permanent"))
    assert {"CR 106.12", "CR 605.1a", "CR 605.1b"} <= set(kinnan[:3])
    sick = _ids(retrieve_rules(path, "summoning sickness"))
    assert sick[:2] == ["CR 302.6", "CR 602.5a"]


def test_retrieval_is_read_only_and_offline(db: Path, monkeypatch) -> None:
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    before = db.read_bytes()
    retrieve_rules(db, "Can Lathril tap ten elves?", cards=[LATHRIL])
    assert db.read_bytes() == before
