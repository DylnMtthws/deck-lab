"""The rules corpus is traceable to a dated document, and rebuilds cleanly.

Three things go wrong quietly when a reference index is built casually, and all
three actually happened while building this one:

- The archived text stops matching what was fetched, because a decoded string
  written in text mode round-trips through universal newlines. The source hash
  then describes a document nobody has.
- The table of contents is chunked alongside the rules. It lists every section
  by title in the same ``NNN. Title`` form the body uses, so the parser emits a
  chunk whose entire content is "Commander" — which then beats the actual rules
  text on any query about commanders.
- A rebuild after a parser change leaves the superseded chunks live. They get
  embedded into the new generation and keep answering queries, so the fix looks
  like it did not work.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sabermetrics.reference_layer.chunker import (
    DocumentChunker,
    chunk_id,
    strip_table_of_contents,
)

ROOT = Path(__file__).resolve().parent.parent

#: A miniature Comprehensive Rules with the same shape as the real document:
#: front matter, a contents listing whose entries look exactly like body
#: headings, then the body.
MINI_RULES = """Magic: The Gathering Comprehensive Rules

These rules are effective as of August 7, 2026.

Contents

1. Game Concepts
100. General
101. The Magic Golden Rules

9. Casual Variants
903. Commander

100. General

100.1. These Magic rules apply to any Magic game with two or more players.

100.2. Some formats restrict deck size.

903. Commander

903.1. In the Commander variant, each deck is led by a legendary creature.
"""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_module():
    return _load("build_rules_index", ROOT / "scripts" / "build_rules_index.py")


def test_the_contents_listing_is_dropped_before_chunking():
    stripped = strip_table_of_contents(MINI_RULES)
    assert stripped.startswith("100. General")
    assert "The Magic Golden Rules" not in stripped, "a contents entry survived"
    assert "100.1." in stripped and "903.1." in stripped, "body text was lost"


def test_text_with_no_numbered_rule_is_returned_untouched():
    """An article is not the Comprehensive Rules; do not guess at its structure."""
    article = "Some Article\n\nA paragraph about mana.\n"
    assert strip_table_of_contents(article) == article


def test_a_contents_entry_no_longer_becomes_its_own_chunk(tmp_path):
    path = tmp_path / "comprehensive_rules.txt"
    path.write_text(MINI_RULES, encoding="utf-8")
    chunks = DocumentChunker().chunk_comprehensive_rules(path)
    assert chunks, "the chunker produced nothing"
    titles_only = [chunk for chunk in chunks if chunk.content.strip() == "Commander"]
    assert not titles_only, (
        "a chunk whose entire content is a section title outranks real rules "
        "text on a one-word query"
    )
    assert any("903.1." in chunk.content for chunk in chunks)


def test_chunk_ids_are_content_addressed_and_stable(tmp_path):
    path = tmp_path / "comprehensive_rules.txt"
    path.write_text(MINI_RULES, encoding="utf-8")
    first = DocumentChunker().chunk_comprehensive_rules(path)
    second = DocumentChunker().chunk_comprehensive_rules(path)
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert len({chunk.id for chunk in first}) == len(first), "ids collide"
    for chunk in first:
        assert chunk.id == chunk_id("comprehensive_rules", chunk.section, chunk.content)


def test_a_modified_source_is_refused(build_module, tmp_path):
    """The hash exists to catch an edited archive, so prove that it does."""
    directory = tmp_path / "2026-08-07"
    directory.mkdir()
    raw = MINI_RULES.encode("utf-8")
    (directory / "comprehensive_rules.txt").write_bytes(raw)
    (directory / "source.json").write_text(
        json.dumps(
            {
                "schema_version": "research-rules-source.v1",
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "effective_date": "2026-08-07",
            }
        ),
        encoding="utf-8",
    )
    build_module.verified_source(directory)

    (directory / "comprehensive_rules.txt").write_bytes(raw + b"\n100.3. Extra.\n")
    with pytest.raises(build_module.RulesIndexError, match="modified since"):
        build_module.verified_source(directory)


def test_crlf_bytes_survive_the_round_trip(build_module, tmp_path):
    """The real document is CRLF with a byte-order mark; both must round-trip."""
    directory = tmp_path / "2026-08-07"
    directory.mkdir()
    raw = b"\xef\xbb\xbf" + MINI_RULES.replace("\n", "\r\n").encode("utf-8")
    (directory / "comprehensive_rules.txt").write_bytes(raw)
    (directory / "source.json").write_text(
        json.dumps(
            {
                "schema_version": "research-rules-source.v1",
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "effective_date": "2026-08-07",
            }
        ),
        encoding="utf-8",
    )
    path, normalized_hash, _ = build_module.verified_source(directory)
    text = path.read_text(encoding="utf-8")
    assert "\r" not in text and not text.startswith("﻿")
    assert normalized_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_a_rebuild_prunes_the_chunks_it_no_longer_produces(build_module, tmp_path):
    """Superseded chunks stay searchable unless something removes them."""
    db_path = tmp_path / "reference.db"
    build_module._ensure_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            "INSERT INTO reference_chunks (id, document, section, tier, content) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("keep", build_module.DOCUMENT, "CR 100", 1, "100.1. A rule."),
                ("stale", build_module.DOCUMENT, "CR 100", 1, "General"),
                ("other", "commander_rules", "intro", 1, "Another document."),
            ],
        )
    pruned = build_module._prune_superseded(db_path, {"keep"})
    assert pruned == 1
    with sqlite3.connect(db_path) as connection:
        remaining = {
            str(row[0]) for row in connection.execute("SELECT id FROM reference_chunks")
        }
    assert remaining == {"keep", "other"}, "another document must not be touched"


def test_the_pinned_manifest_matches_the_checked_in_source_contract():
    """The repository states which rules document the Ask path answers from."""
    manifest_path = ROOT / "fixtures" / "research" / "rules_index.json"
    if not manifest_path.is_file():
        pytest.skip("rules index not built on this machine")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "research-rules-index.v1"
    source = manifest["source"]
    assert source["document"] == "comprehensive_rules"
    assert len(source["content_sha256"]) == 64
    assert source["requested_url"].startswith("https://media.wizards.com/")
    # The effective date is read from the document, not the URL, and the two
    # genuinely differ: the file named 20260819 is effective 2026-08-07.
    assert datetime.fromisoformat(source["retrieved_at"]).tzinfo == UTC
    assert manifest["chunk_count"] == manifest["row_count"]
    assert len(manifest["reference_content_sha256"]) == 64
    assert len(manifest["configuration"]["chunker_sha256"]) == 64
