"""Build the Comprehensive Rules index from a provisioned source artefact.

    python scripts/build_rules_index.py
    python scripts/build_rules_index.py --verify-only

Offline. It reads the text that ``provision_rules_source.py`` saved, checks it
still hashes to what that fetch recorded, chunks it, embeds the chunks with the
pinned model and activates one generation atomically.

The manifest it writes is the point. An index nobody can trace back to a dated,
hashed document is a pile of passages with opinions in them: quoting a rule
then means quoting whatever text happened to be on disk the day somebody ran a
build. So the manifest carries the source hash and effective date, the chunker
configuration AND a hash of the chunker's own source, the model identity, the
resulting chunk-id set and the generation id — and the build re-chunks to
confirm the ids are reproducible before it writes anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabermetrics.reference_layer import chunker as chunker_module
from sabermetrics.reference_layer.chunker import Chunk, DocumentChunker
from sabermetrics.reference_layer.indexer import (
    EmbeddingIndexer,
    reference_content_sha256,
)
from sabermetrics.substrate.settings import load_research_settings

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA = "research-rules-index.v1"
SOURCE_ROOT = ROOT / "data" / "reference" / "comprehensive_rules"
DEFAULT_DB = ROOT / "data" / "research-indexes" / "reference.db"
#: Checked in, so the repository states which rules document the Ask path is
#: supposed to be answering from, independently of what is on any one disk.
PINNED_MANIFEST = ROOT / "fixtures" / "research" / "rules_index.json"

#: The reference-layer tables, so this index does not require the legacy
#: application database or its migration script to exist.
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS reference_chunks (
        id TEXT PRIMARY KEY,
        document TEXT NOT NULL,
        section TEXT,
        tier INTEGER NOT NULL,
        content TEXT NOT NULL,
        embedding BLOB,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON reference_chunks(document)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_tier ON reference_chunks(tier)",
    """
    CREATE TABLE IF NOT EXISTS reference_embedding_generations (
        generation_id TEXT PRIMARY KEY,
        model_id TEXT NOT NULL,
        model_revision TEXT NOT NULL,
        document_template_version TEXT NOT NULL,
        dimensions INTEGER NOT NULL CHECK(dimensions > 0),
        dtype TEXT NOT NULL,
        normalized INTEGER NOT NULL CHECK(normalized IN (0, 1)),
        content_sha256 TEXT NOT NULL,
        embedding_sha256 TEXT NOT NULL,
        row_count INTEGER NOT NULL CHECK(row_count > 0),
        complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0, 1)),
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_chunk_embeddings (
        generation_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        embedding BLOB NOT NULL,
        PRIMARY KEY (generation_id, chunk_id),
        FOREIGN KEY (generation_id)
            REFERENCES reference_embedding_generations(generation_id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reference_embeddings_chunk
        ON reference_chunk_embeddings(chunk_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS active_reference_embedding_generation (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        generation_id TEXT NOT NULL,
        activated_at TIMESTAMP NOT NULL,
        FOREIGN KEY (generation_id)
            REFERENCES reference_embedding_generations(generation_id)
    )
    """,
)


class RulesIndexError(RuntimeError):
    """The rules index could not be built from a verifiable source."""


def latest_source(root: Path = SOURCE_ROOT) -> Path:
    """Return the newest provisioned source directory."""
    candidates = sorted(path for path in root.glob("*/source.json"))
    if not candidates:
        raise RulesIndexError(
            f"no provisioned rules source under {root}; run "
            "scripts/provision_rules_source.py first"
        )
    return candidates[-1].parent


def verified_source(directory: Path) -> tuple[Path, str, dict[str, Any]]:
    """Verify the provisioned bytes and write the text the chunker will read.

    The archived artefact is the exact bytes the publisher served: UTF-8 with a
    byte-order mark and CRLF line endings. The chunker needs neither, and
    normalising silently would mean the thing that was hashed and the thing
    that was parsed are two different documents. So the normalisation is an
    explicit derived file with its own hash, and both hashes go in the
    manifest.

    Args:
        directory: A dated directory holding ``source.json`` and the bytes.

    Returns:
        The normalised text path, its digest, and the recorded source metadata.

    Raises:
        RulesIndexError: If the bytes no longer hash to the recorded digest.
    """
    source = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    raw = (directory / "comprehensive_rules.txt").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != source["content_sha256"]:
        raise RulesIndexError(
            f"{directory} has been modified since it was fetched\n"
            f"  recorded: {source['content_sha256']}\n  on disk:  {digest}"
        )
    normalized = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    path = directory / "normalized.txt"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalized)
    return path, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), source


def chunker_sha256() -> str:
    """Hash the chunker's own source, so a parser change is visible."""
    path = Path(chunker_module.__file__)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chunk_ids_sha256(chunks: list[Chunk]) -> str:
    """Hash the ordered chunk identities, for a cheap rebuild comparison."""
    payload = "\n".join(chunk.id for chunk in chunks)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reproducible_chunks(path: Path) -> list[Chunk]:
    """Chunk twice and refuse unless both passes agree exactly."""
    chunker = DocumentChunker()
    first = chunker.chunk_comprehensive_rules(path)
    second = DocumentChunker().chunk_comprehensive_rules(path)
    if [chunk.id for chunk in first] != [chunk.id for chunk in second]:
        raise RulesIndexError("chunk ids are not reproducible across two passes")
    if [chunk.content for chunk in first] != [chunk.content for chunk in second]:
        raise RulesIndexError("chunk content is not reproducible across two passes")
    if not first:
        raise RulesIndexError("the chunker produced nothing from the rules text")
    return first


def _deduplicated(chunks: list[Chunk]) -> tuple[list[Chunk], list[str]]:
    """Collapse chunks that are byte-identical, keeping the first of each.

    A content-addressed id is a statement that identical text is the same
    chunk, so two chunks sharing an id are not a collision to work around —
    they are the same passage reached twice. The Comprehensive Rules produce
    them because the section regex matches a heading in the table of contents
    as well as in the body.

    Storing them separately was only possible while ids were random, and it was
    never right: the upsert would have overwritten one with the other, leaving
    the corpus hash counting a chunk the database does not hold.

    Args:
        chunks: The chunker's output, in order.

    Returns:
        The deduplicated chunks and a description of what was collapsed.
    """
    seen: dict[str, Chunk] = {}
    collapsed: list[str] = []
    for chunk in chunks:
        if chunk.id in seen:
            collapsed.append(f"{chunk.section or 'preamble'}: {chunk.content[:60]!r}")
            continue
        seen[chunk.id] = chunk
    return list(seen.values()), collapsed


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


DOCUMENT = "comprehensive_rules"


def _prune_superseded(db_path: Path, keep: set[str]) -> int:
    """Delete chunks of this document that the current build no longer produces.

    ``index_chunks`` merges into the standing corpus and never deletes, because
    the reference layer is designed to hold several documents at once. That is
    right for adding an article and wrong for REBUILDING one: a chunker change
    leaves every superseded chunk live, embedded into the new generation and
    competing in every search. The table-of-contents chunks survived exactly
    this way and kept winning rules queries after the parser stopped emitting
    them.

    This script owns one document, so it removes that document's orphans.

    Args:
        db_path: The reference database.
        keep: Chunk ids the current build produced.

    Returns:
        How many superseded rows were deleted.
    """
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        rows = [
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM reference_chunks WHERE document = ?", (DOCUMENT,)
            )
        ]
        superseded = [chunk_id for chunk_id in rows if chunk_id not in keep]
        connection.executemany(
            "DELETE FROM reference_chunks WHERE id = ?",
            ((chunk_id,) for chunk_id in superseded),
        )
    return len(superseded)


def _ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for statement in _SCHEMA:
            connection.execute(statement)


def main() -> int:
    """Build one rules generation and write its manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path, default=PINNED_MANIFEST)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check the source and chunking without embedding or writing",
    )
    args = parser.parse_args()

    directory = args.source or latest_source()
    normalized_path, normalized_hash, source = verified_source(directory)
    chunks, collapsed = _deduplicated(_reproducible_chunks(normalized_path))
    content_hash = reference_content_sha256(chunks)
    sections = sorted({chunk.section or "" for chunk in chunks})
    settings = load_research_settings()
    configuration = {
        "chunker": f"{DocumentChunker.__module__}.{DocumentChunker.__qualname__}",
        "chunker_sha256": chunker_sha256(),
        "target_chunk_tokens": DocumentChunker.TARGET_CHUNK_TOKENS,
        "chars_per_token": DocumentChunker.CHARS_PER_TOKEN,
        "embedding_model_id": settings.embedding.model_id,
        "embedding_revision": settings.embedding.revision,
        "dimensions": settings.embedding.dimensions,
        "normalize": settings.embedding.normalize,
    }
    print(f"source     {directory.name} ({source['content_sha256'][:12]})")
    print(f"normalized {normalized_hash[:12]}")
    print(f"chunks     {len(chunks)} across {len(sections)} sections")
    if collapsed:
        print(f"collapsed  {len(collapsed)} duplicate chunk(s): {collapsed[0]}")
    print(f"content    {content_hash[:12]}")
    if args.verify_only:
        print("verify-only: nothing written")
        return 0

    _ensure_schema(args.db_path)
    pruned = _prune_superseded(args.db_path, {chunk.id for chunk in chunks})
    if pruned:
        print(f"pruned     {pruned} superseded chunk(s) from a previous build")
    indexer = EmbeddingIndexer(args.db_path)
    row_count = indexer.index_chunks(list(chunks))
    with sqlite3.connect(args.db_path) as connection:
        generation_id = str(
            connection.execute(
                "SELECT generation_id FROM active_reference_embedding_generation "
                "WHERE singleton = 1"
            ).fetchone()[0]
        )
        stored_chunks = int(
            connection.execute(
                "SELECT COUNT(*) FROM reference_chunks WHERE document = ?",
                (DOCUMENT,),
            ).fetchone()[0]
        )
    if stored_chunks != len(chunks):
        raise RulesIndexError(
            f"{stored_chunks} stored rows for {len(chunks)} chunks of "
            f"{DOCUMENT}; the corpus does not match what was just built"
        )

    _write_atomic(
        args.manifest,
        {
            "schema_version": MANIFEST_SCHEMA,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": source,
            # The served bytes are archival; this is what the parser saw.
            "normalized_sha256": normalized_hash,
            "configuration": configuration,
            "chunk_count": len(chunks),
            "duplicate_chunks_collapsed": collapsed,
            "superseded_chunks_pruned": pruned,
            "chunk_ids_sha256": chunk_ids_sha256(chunks),
            "reference_content_sha256": content_hash,
            "generation_id": generation_id,
            "row_count": row_count,
            "note": (
                "an index is a prerequisite for answering a rules question, "
                "not an answer to one. whether a retrieved passage supports a "
                "claim is unlabelled and therefore unmeasured"
            ),
        },
    )
    print(f"generation {generation_id} with {row_count} rows -> {args.db_path}")
    print(f"manifest   {args.manifest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RulesIndexError, ValueError, KeyError) as exc:
        print(f"RULES INDEX REFUSED: {exc}")
        raise SystemExit(2) from None
