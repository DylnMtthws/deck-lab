"""Versioned, attributable reference ingestion and optional local semantic vectors.

Only ingest text the operator has permission to use. URLs identify supplied
sources; this module never silently downloads or substitutes webpage contents.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FastEmbedService:
    """Small CPU ONNX model; pinned artifact identity guards index compatibility."""

    def __init__(self, model_path: str, model_name: str, *, threads: int = 1) -> None:
        from fastembed import TextEmbedding

        files = sorted(Path(model_path).rglob("*"))
        digest = hashlib.sha256()
        for file in files:
            if file.is_file() and file.suffix in {".onnx", ".json"}:
                digest.update(file.relative_to(model_path).as_posix().encode())
                with file.open("rb") as handle:
                    for part in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(part)
        self._model_name = f"fastembed@{package_version('fastembed')}:{model_name}:{digest.hexdigest()}"
        self._model = TextEmbedding(
            model_name=model_name,
            specific_model_path=model_path,
            local_files_only=True,
            threads=threads,
        )
        self._cache: dict[str, Any] = {}
        self._inference_lock = threading.Lock()

    def embed(self, text: str) -> Any:
        key = content_hash(text)
        with self._inference_lock:
            if key not in self._cache:
                if len(self._cache) >= 128:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = next(iter(self._model.embed([text], batch_size=1)))
            return self._cache[key]

    def embed_many(self, texts: list[str]) -> list[Any]:
        """Prepare offline card artifacts in bounded batches without query-cache churn."""
        with self._inference_lock:
            return list(self._model.embed(texts, batch_size=16))


_fast_service: FastEmbedService | None = None
_fast_initialization_lock = threading.Lock()


def local_embedding_service(*, threads: int = 1) -> Any:
    """Reuse application cache with an explicitly configured, local-only model."""
    global _fast_service
    fast_path = os.environ.get("DECK_LAB_STRATEGY_ONNX_PATH")
    if fast_path and Path(fast_path).is_dir():
        model_name = os.environ.get(
            "DECK_LAB_STRATEGY_ONNX_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        with _fast_initialization_lock:
            if _fast_service is None:
                _fast_service = FastEmbedService(fast_path, model_name, threads=threads)
            return _fast_service
    model_path = os.environ.get("DECK_LAB_STRATEGY_EMBEDDING_MODEL")
    if not model_path or not Path(model_path).is_dir():
        return None
    import torch
    from sentence_transformers import SentenceTransformer

    from sabermetrics.analytics.embeddings import get_embedding_service

    torch.set_num_threads(1)
    service: Any = get_embedding_service(model_path)
    # Cache identity includes the immutable snapshot/artifact directory.
    if service._model is None:
        service._model = SentenceTransformer(
            model_path, device="cpu", local_files_only=True
        )
    return service


def ingest_text(
    db_path: Path,
    text: str,
    *,
    document: str,
    source_url: str | None,
    version: str,
    permission: str,
    source_kind: str = "user_supplied",
    section: str = "",
    commander: str = "",
    archetype: str = "",
    tier: int = 2,
    chunk_chars: int = 1800,
    embed: bool = False,
    source_key: str = "",
    activate: bool = True,
    author: str = "",
    source_date: str = "",
    source_class: str = "",
    primary_links: list[dict[str, str]] | None = None,
    limitations: str = "",
    permission_basis: str = "",
    review_status: str = "not_recorded",
    license_url: str = "",
    attribution_url: str = "",
) -> int:
    """Store exact contiguous chunks and provenance, replacing this source version.

    Versions of a source remain independently auditable. No model is required.
    Embedding is opt-in and fails if no local model is available.
    """
    if (
        not text.strip()
        or not document.strip()
        or not version.strip()
        or not permission.strip()
    ):
        raise ValueError("text, document, version and permission are required")
    if source_kind not in {
        "user_supplied",
        "licensed_strategy",
        "official_rules",
        "strategy_excerpt",
        "authored_guidance",
        "wiki_secondary",
    }:
        raise ValueError("unsupported source kind")
    if source_url:
        parsed = urlsplit(source_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or len(source_url) > 500
        ):
            raise ValueError("source URL must be HTTP(S), at most 500 characters")
    if not 200 <= chunk_chars <= 8000:
        raise ValueError("chunk_chars must be between 200 and 8000")
    if source_kind == "wiki_secondary" and not (source_url and source_date and version):
        raise ValueError("wiki secondary sources require URL, revision and source_date")
    provenance_json = json.dumps(
        {
            "author": author,
            "source_date": source_date,
            "source_class": source_class or source_kind,
            "primary_links": primary_links or [],
            "limitations": limitations,
            "permission_basis": permission_basis,
            "review_status": review_status,
            "license_url": license_url,
            "attribution_url": attribution_url,
        },
        sort_keys=True,
    )
    service = local_embedding_service() if embed else None
    if embed and service is None:
        raise ValueError(
            "configure DECK_LAB_STRATEGY_EMBEDDING_MODEL to a local model directory"
        )
    chunks = [
        text[start : start + chunk_chars] for start in range(0, len(text), chunk_chars)
    ]
    now = datetime.now(UTC).isoformat()
    lineage = source_key or content_hash(document + "\0" + section)
    source_id = content_hash(
        document + "\0" + (source_url or "") + "\0" + version + "\0" + section
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS reference_chunks (id TEXT PRIMARY KEY, document TEXT, section TEXT, tier INTEGER, content TEXT, embedding BLOB, last_updated TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS reference_chunk_metadata (chunk_id TEXT PRIMARY KEY, source_id TEXT, source_url TEXT, version TEXT, permission TEXT, source_kind TEXT, ingested_at TEXT, content_hash TEXT, embedding_model TEXT, embedding_json TEXT, char_start INTEGER, char_end INTEGER)"
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(reference_chunk_metadata)")
        }
        for field in ("commander", "archetype"):
            if field not in columns:
                conn.execute(
                    f"ALTER TABLE reference_chunk_metadata ADD COLUMN {field} TEXT"
                )
        for field, kind in (
            ("provenance_json", "TEXT"),
            ("source_key", "TEXT"),
            ("is_current", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if field not in columns:
                conn.execute(
                    f"ALTER TABLE reference_chunk_metadata ADD COLUMN {field} {kind}"
                )
        existing_content = conn.execute(
            "SELECT r.content FROM reference_chunk_metadata m JOIN reference_chunks r ON r.id=m.chunk_id WHERE m.source_id=? ORDER BY m.char_start",
            (source_id,),
        ).fetchall()
        if existing_content and "".join(row[0] for row in existing_content) != text:
            raise ValueError(
                "Source version already contains different text; supply a new version to preserve historical evidence"
            )
        if (
            not activate
            and conn.execute(
                "SELECT 1 FROM reference_chunk_metadata WHERE source_id=? AND is_current=1 LIMIT 1",
                (source_id,),
            ).fetchone()
        ):
            raise ValueError(
                "Cannot archive the currently active source version; import a distinct historical version instead"
            )
        if (
            source_url
            and not source_key
            and conn.execute(
                "SELECT 1 FROM reference_chunk_metadata m JOIN reference_chunks r ON r.id=m.chunk_id WHERE m.source_url=? AND coalesce(r.section,'')=? AND r.document<>? LIMIT 1",
                (source_url, section, document),
            ).fetchone()
        ):
            raise ValueError(
                "Source URL and section already exist under a different document title; supply an explicit source_key to resolve lineage"
            )
        # Migrate matching legacy snapshots into the explicit lineage on import.
        conn.execute(
            "UPDATE reference_chunk_metadata SET source_key=? WHERE source_key IS NULL AND chunk_id IN (SELECT id FROM reference_chunks WHERE document=? AND coalesce(section,'')=?)",
            (lineage, document, section),
        )
        if activate:
            conn.execute(
                "UPDATE reference_chunk_metadata SET is_current=0 WHERE source_key=?",
                (lineage,),
            )
        old = conn.execute(
            "SELECT chunk_id FROM reference_chunk_metadata WHERE source_id=?",
            (source_id,),
        ).fetchall()
        conn.executemany("DELETE FROM reference_chunks WHERE id=?", old)
        conn.execute(
            "DELETE FROM reference_chunk_metadata WHERE source_id=?", (source_id,)
        )
        for i, chunk in enumerate(chunks):
            chunk_id = f"source:{source_id}:{i}"
            vector = service.embed(chunk).tolist() if service else None
            conn.execute(
                "INSERT INTO reference_chunks(id,document,section,tier,content) VALUES(?,?,?,?,?)",
                (chunk_id, document, section, tier, chunk),
            )
            conn.execute(
                "INSERT INTO reference_chunk_metadata(chunk_id,source_id,source_url,version,permission,source_kind,ingested_at,content_hash,embedding_model,embedding_json,char_start,char_end,commander,archetype,source_key,is_current,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    chunk_id,
                    source_id,
                    source_url,
                    version,
                    permission,
                    source_kind,
                    now,
                    content_hash(chunk),
                    service._model_name if service else None,
                    json.dumps(vector) if vector is not None else None,
                    i * chunk_chars,
                    min(len(text), (i + 1) * chunk_chars),
                    commander[:200],
                    archetype[:100],
                    lineage,
                    int(activate),
                    provenance_json,
                ),
            )
    return len(chunks)


def rank_scores(
    rows: list[dict[str, Any]], terms: set[str], tokenize: Any, query: str
) -> tuple[dict[str, dict[str, float]], list[str], bool]:
    """BM25 plus cosine candidate union, followed by explainable weighted reranking."""
    counts = [
        Counter(
            tokenize(
                str(row["content"])
                + " "
                + str(row["document"] or "")
                + " "
                + str(row["section"] or "")
                + " "
                + str(row.get("commander") or "")
                + " "
                + str(row.get("archetype") or "")
            )
        )
        for row in rows
    ]
    avg_len = sum(sum(c.values()) for c in counts) / max(1, len(counts))
    df = {term: sum(term in c for c in counts) for term in terms}
    limitations: list[str] = []
    service = None
    query_vector = None
    if any(row.get("embedding_json") for row in rows):
        try:
            service = local_embedding_service()
            if service:
                query_vector = service.embed(query).tolist()
        except (ImportError, RuntimeError, OSError, ValueError):
            limitations.append(
                "Configured local semantic model is unavailable; using lexical retrieval."
            )
    scores = {}
    semantic_used = False
    rejected = 0
    for row, counts_row in zip(rows, counts):
        bm25 = 0.0
        length = sum(counts_row.values())
        for term in terms:
            freq = counts_row[term]
            if freq:
                idf = math.log(1 + (len(rows) - df[term] + 0.5) / (df[term] + 0.5))
                bm25 += (
                    idf
                    * freq
                    * 2.2
                    / (freq + 1.2 * (0.25 + 0.75 * length / max(1, avg_len)))
                )
        semantic = 0.0
        if row.get("embedding_json") and service and query_vector is not None:
            try:
                vector = json.loads(row["embedding_json"])
                if row.get("embedding_model") != service._model_name or row.get(
                    "content_hash"
                ) != content_hash(str(row["content"])):
                    raise ValueError("stale model or content")
                if (
                    len(vector) != len(query_vector)
                    or not vector
                    or not all(
                        isinstance(x, (int, float)) and math.isfinite(x) for x in vector
                    )
                ):
                    raise ValueError("invalid vector")
                denom = math.sqrt(
                    sum(x * x for x in vector) * sum(x * x for x in query_vector)
                )
                if denom <= 0:
                    raise ValueError("zero vector")
                semantic = max(
                    0.0,
                    min(1.0, sum(a * b for a, b in zip(vector, query_vector)) / denom),
                )
                semantic_used = True
            except (ValueError, TypeError, json.JSONDecodeError):
                rejected += 1
        coverage = len(terms & counts_row.keys()) / max(1, len(terms))
        # Semantic-only results require a conservative similarity floor.
        if bm25 > 0 or semantic >= 0.65:
            scores[str(row["id"])] = {
                "bm25": round(bm25, 4),
                "semantic_cosine": round(semantic, 4),
                "query_coverage": round(coverage, 4),
                "rerank": round(
                    0.35 * bm25 / (1 + bm25) + 0.55 * semantic + 0.10 * coverage, 4
                ),
            }
    lexical_order = sorted(
        (key for key in scores if scores[key]["bm25"] > 0),
        key=lambda key: (-scores[key]["bm25"], key),
    )
    semantic_order = sorted(
        (key for key in scores if scores[key]["semantic_cosine"] >= 0.65),
        key=lambda key: (-scores[key]["semantic_cosine"], key),
    )
    for order, channel in (
        (lexical_order, "lexical_rank"),
        (semantic_order, "semantic_rank"),
    ):
        for position, key in enumerate(order, 1):
            scores[key][channel] = position
    for score in scores.values():
        score["channel_fusion"] = round(
            sum(
                1 / (60 + score[channel])
                for channel in ("lexical_rank", "semantic_rank")
                if channel in score
            ),
            6,
        )
        score["rerank"] = round(score["rerank"] + score["channel_fusion"], 6)
    if rejected:
        limitations.append(
            f"Rejected {rejected} stale or invalid embeddings; lexical evidence remains usable."
        )
    if not semantic_used:
        for score in scores.values():
            score["rerank"] = round(
                0.8 * score["bm25"] / (1 + score["bm25"])
                + 0.2 * score["query_coverage"]
                + score["channel_fusion"],
                4,
            )
        limitations.append(
            "No compatible local semantic index is available; BM25 lexical retrieval only."
        )
    return scores, limitations, semantic_used
