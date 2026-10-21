"""Bounded local view vectors, with atomic generation replacement.

Readers hold one SQLite connection to an immutable published generation. Builders
write a sibling temporary database; interruption never replaces the old artifact.
No network, model download, scheduler, or eligibility decision is implemented here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
import tempfile
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabermetrics.assistant_card_views import (
    VIEW_VERSION,
    _valid_vector,
    _view_text,
    build_card_views,
)

INDEX_VERSION = "card-view-sqlite.v1"
MAX_DIMENSIONS = 8192
MAX_TEXT_BYTES = 1024 * 1024
BATCH_SIZE = 16


class ViewIndexRefreshError(ValueError):
    """A failed attempt, carrying partial counts and the retained generation."""

    def __init__(self, message: str, counts: dict[str, int], generation: str | None):
        super().__init__(message)
        self.counts = {**counts, "failed": counts["failed"] + 1}
        self.previous_generation = generation


def _service(encoder: Any) -> Any:
    if encoder is None:
        from sabermetrics.reference_layer.strategy_corpus import local_embedding_service

        encoder = local_embedding_service()
    if encoder is None:
        raise ValueError("configure a pinned local encoder before preparation")
    return encoder


def _blob(vector: Any, dimensions: int | None = None) -> bytes:
    from itertools import islice

    values = [float(v) for v in islice(iter(vector), MAX_DIMENSIONS + 1)]
    if (
        not _valid_vector(values)
        or len(values) > MAX_DIMENSIONS
        or (dimensions is not None and len(values) != dimensions)
    ):
        raise ValueError("invalid or inconsistent vector dimensions")
    try:
        blob = struct.pack("<" + "f" * len(values), *values)
    except (OverflowError, struct.error):
        raise ValueError("vector cannot be represented as float32") from None
    _unblob(blob, len(values))  # Reject float32 overflow/underflow as well.
    return blob


def _unblob(blob: bytes, dimensions: int) -> list[float]:
    if not 1 <= dimensions <= MAX_DIMENSIONS or len(blob) != dimensions * 4:
        raise ValueError("invalid float32 row length")
    vector = list(struct.unpack("<" + "f" * dimensions, blob))
    if not _valid_vector(vector):
        raise ValueError("invalid float32 vector")
    return vector


def informative(views: dict[str, Any], name: str) -> bool:
    view = views["views"][name]
    return (
        bool(view["roles"])
        if name == "functional_role"
        else bool(view["draw_routes"]) if name == "prerequisite" else True
    )


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA cache_size=-2048")
        return db
    except BaseException:
        db.close()
        raise


def metadata(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute(
        "SELECT payload FROM metadata WHERE id=1 AND length(payload)<=65536"
    ).fetchone()
    if row is None or len(row[0]) > 65536:
        raise ValueError("missing or oversized index metadata")
    value = json.loads(row[0])
    if not isinstance(value, dict):
        raise ValueError("malformed index metadata")  # noqa: TRY004
    if (
        value.get("artifact_version") != INDEX_VERSION
        or value.get("version") != VIEW_VERSION
        or not isinstance(value.get("generation"), str)
        or len(value["generation"]) != 64
        or type(value.get("dimensions")) is not int
        or not 1 <= value["dimensions"] <= MAX_DIMENSIONS
        or not isinstance(value.get("embedding_model"), str)
        or not isinstance(value.get("source", {}), dict)
    ):
        raise ValueError("incompatible view generation")
    return value


def refresh_view_index(
    cards: Iterable[dict[str, Any]],
    output: str | Path,
    *,
    encoder: Any = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stream cards, reuse hash-valid rows, and publish only a complete build.

    Memory: at most sixteen texts/vectors plus one card projection; SQLite cache
    is 2 MiB. Duplicate Oracle identities fail closed. Unsupported inference views
    are omitted, but supplied printed text can still be indexed. Failures raise
    and leave the prior generation unchanged; no partial freshness is published.
    """
    service = _service(encoder)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        # Never replace a caller's catalog or an unrelated SQLite database.
        try:
            with closing(_connect(output)) as probe:
                tables = {
                    r[0]
                    for r in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if tables != {"metadata", "cards", "vectors"}:
                    raise ValueError("output is not an owned card view index")
                metadata(probe)
        except sqlite3.DatabaseError as exc:
            raise ValueError("output is not a recognizable card view index") from exc
    previous = None
    previous_meta: dict[str, Any] = {}
    try:
        previous = _connect(output)
        previous_meta = metadata(previous)
    except (OSError, ValueError, sqlite3.Error, TypeError):
        if previous is not None:
            previous.close()
        previous = None
    handle, temporary = tempfile.mkstemp(
        prefix="." + output.name + ".", suffix=".tmp", dir=output.parent
    )
    os.close(handle)
    db = sqlite3.connect(temporary)
    counts = dict(
        present=0,
        eligible=0,
        indexed=0,
        stale=0,
        unsupported=0,
        failed=0,
        excluded=0,
        reused_rows=0,
        encoded_rows=0,
    )
    dimensions: int | None = None
    pending: list[tuple[tuple[Any, ...], str]] = []
    try:
        db.execute("PRAGMA cache_size=-2048")
        db.executescript(
            "CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL); CREATE TABLE cards(oracle_id TEXT PRIMARY KEY,source_hash TEXT NOT NULL); CREATE TABLE vectors(oracle_id TEXT NOT NULL,view TEXT NOT NULL,source_hash TEXT NOT NULL,view_hash TEXT NOT NULL,text_sha256 TEXT NOT NULL,vector BLOB NOT NULL,vector_sha256 TEXT NOT NULL,PRIMARY KEY(oracle_id,view));"
        )

        def flush() -> None:
            nonlocal dimensions
            if not pending:
                return
            texts = [text for _, text in pending]
            encoded = (
                list(service.embed_many(texts))
                if hasattr(service, "embed_many")
                else [service.embed(text) for text in texts]
            )
            if len(encoded) != len(pending):
                raise ValueError("encoder returned incomplete batch")
            for (row, _), vector in zip(pending, encoded):
                blob = _blob(vector, dimensions)
                dimensions = len(blob) // 4
                db.execute(
                    "INSERT INTO vectors VALUES (?,?,?,?,?,?,?)",
                    (*row, blob, hashlib.sha256(blob).hexdigest()),
                )
                counts["encoded_rows"] += 1
            pending.clear()

        for card in cards:
            counts["present"] += 1
            oid = str(card.get("oracle_id") or "")
            if not oid:
                counts["excluded"] += 1
                continue
            counts["eligible"] += 1
            views = build_card_views(card)
            try:
                db.execute(
                    "INSERT INTO cards VALUES (?,?)", (oid, views["source_hash"])
                )
            except sqlite3.IntegrityError:
                raise ValueError("duplicate Oracle identity in projection") from None
            if (
                card.get("oracle_text_truncated")
                or card.get("layout") not in (None, "", "normal", "token")
                or card.get("card_faces")
            ):
                counts["unsupported"] += 1
            old_hash = (
                previous.execute(
                    "SELECT source_hash FROM cards WHERE oracle_id=?", (oid,)
                ).fetchone()
                if previous
                else None
            )
            card_stale = old_hash is not None and (
                old_hash[0] != views["source_hash"]
                or previous_meta.get("embedding_model") != service._model_name
            )
            for name, view in views["views"].items():
                if not informative(views, name):
                    continue
                text = _view_text(views, name)
                if len(text.encode()) > MAX_TEXT_BYTES:
                    raise ValueError("view text exceeds per-row bound")
                row = (
                    oid,
                    name,
                    views["source_hash"],
                    view["view_hash"],
                    hashlib.sha256(text.encode()).hexdigest(),
                )
                reused = False
                if (
                    previous
                    and previous_meta.get("embedding_model") == service._model_name
                ):
                    old = previous.execute(
                        "SELECT source_hash,view_hash,text_sha256,vector,vector_sha256 FROM vectors WHERE oracle_id=? AND view=? AND length(vector)<=32768",
                        (oid, name),
                    ).fetchone()
                    if (
                        old
                        and tuple(old[:3]) == row[2:]
                        and isinstance(old[3], bytes)
                        and hashlib.sha256(old[3]).hexdigest() == old[4]
                    ):
                        try:
                            _unblob(old[3], previous_meta["dimensions"])
                            if (
                                dimensions is not None
                                and dimensions != previous_meta["dimensions"]
                            ):
                                raise ValueError("inconsistent dimensions")
                            dimensions = previous_meta["dimensions"]
                            db.execute(
                                "INSERT INTO vectors VALUES (?,?,?,?,?,?,?)",
                                (*row, old[3], old[4]),
                            )
                            counts["reused_rows"] += 1
                            reused = True
                        except ValueError:
                            pass
                if not reused:
                    card_stale = card_stale or bool(old_hash)
                    pending.append((row, text))
                    if len(pending) == BATCH_SIZE:
                        flush()
            counts["stale"] += int(card_stale)
            counts["indexed"] += 1
        flush()
        # Sorted hashes make generation identity independent of input permutation.
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                [INDEX_VERSION, VIEW_VERSION, service._model_name, dimensions],
                separators=(",", ":"),
            ).encode()
        )
        for row in db.execute(
            "SELECT oracle_id,view,source_hash,view_hash,text_sha256,vector_sha256 FROM vectors ORDER BY oracle_id,view"
        ):
            digest.update(json.dumps(row, separators=(",", ":")).encode())
        generation = digest.hexdigest()
        counts["expected"] = (source or {}).get("expected", counts["present"])
        counts["excluded"] += (source or {}).get("excluded", 0)
        if source and counts["indexed"] < counts["expected"]:
            source = {**source, "coverage": "partial_cached_projection"}
        receipt = {
            "artifact_version": INDEX_VERSION,
            "version": VIEW_VERSION,
            "embedding_model": service._model_name,
            "dimensions": dimensions or previous_meta.get("dimensions", 1),
            "generation": generation,
            "previous_generation": previous_meta.get("generation"),
            "unchanged": generation == previous_meta.get("generation"),
            "refresh_success_at": datetime.now(UTC).isoformat(),
            "counts": counts,
            "source": source or {},
            "freshness": "local_projection_only_not_live_verified",
        }
        encoded_metadata = json.dumps(receipt, sort_keys=True)
        if len(encoded_metadata.encode()) > 65536:
            raise ValueError("index metadata exceeds 64 KiB bound")
        db.execute("INSERT INTO metadata VALUES (1,?)", (encoded_metadata,))
        db.commit()
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("index integrity check failed")
        db.close()
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    except Exception as exc:
        counts.setdefault("expected", (source or {}).get("expected", counts["present"]))
        raise ViewIndexRefreshError(
            str(exc), counts, previous_meta.get("generation")
        ) from exc
    finally:
        db.close()
        if previous is not None:
            previous.close()
        Path(temporary).unlink(missing_ok=True)


class ViewQuerySession:
    """One query encoding and one row lookup per current eligible card.

    Callers supply eligibility (including legality) before calling similarities.
    Legacy JSON is bounded by its loader; its row map is built once per session.
    SQLite never materializes the artifact or an artifact-wide Python row map.
    """

    def __init__(
        self, artifact: dict[str, Any] | str | Path, query: str, *, encoder: Any = None
    ):
        self.db: sqlite3.Connection | None = None
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.meta: dict[str, Any] = {}
        try:
            if isinstance(artifact, dict):
                self.meta = artifact
                if (
                    artifact.get("artifact_version") != "card-view-vectors.v1"
                    or artifact.get("version") != VIEW_VERSION
                ):
                    raise ValueError("incompatible legacy view index")
                self.rows = {
                    (row["source_hash"], row["view"]): row
                    for row in artifact["records"]
                }
            else:
                self.db = _connect(Path(artifact))
                self.meta = metadata(self.db)
            service = _service(encoder)
            if self.meta.get("embedding_model") != service._model_name:
                raise ValueError("incompatible embedding model")
            self.query = [float(value) for value in service.embed(query)]
            if (
                not _valid_vector(self.query)
                or len(self.query) > MAX_DIMENSIONS
                or (self.db and len(self.query) != self.meta["dimensions"])
            ):
                raise ValueError("incompatible query vector")
            self.norm = math.sqrt(sum(x * x for x in self.query))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def similarities(self, views: dict[str, Any]) -> dict[str, float]:
        oid = str(views["views"]["printed_effect"]["facts"].get("oracle_id") or "")
        rows = self.rows
        if self.db is not None:
            rows = {}
            for (
                name,
                source_hash,
                view_hash,
                text_hash,
                blob,
                blob_hash,
            ) in self.db.execute(
                "SELECT view,source_hash,view_hash,text_sha256,vector,vector_sha256 FROM vectors WHERE oracle_id=? AND view IN ('printed_effect','functional_role','prerequisite') AND length(vector)<=32768 AND length(source_hash)=64 AND length(view_hash)=64 AND length(text_sha256)=64 AND length(vector_sha256)=64 LIMIT 3",
                (oid,),
            ):
                if (
                    not isinstance(blob, bytes)
                    or len(blob) > MAX_DIMENSIONS * 4
                    or hashlib.sha256(blob).hexdigest() != blob_hash
                ):
                    continue
                try:
                    decoded_vector = _unblob(blob, self.meta["dimensions"])
                except ValueError:
                    continue
                rows[(source_hash, name)] = {
                    "oracle_id": oid,
                    "view_hash": view_hash,
                    "text_sha256": text_hash,
                    "vector": decoded_vector,
                }
        result = {}
        for name, view in views["views"].items():
            if not informative(views, name):
                continue
            row = rows.get((views["source_hash"], name), {})
            vector = row.get("vector")
            if (
                row.get("oracle_id", oid) != oid
                or row.get("view_hash") != view["view_hash"]
                or row.get("text_sha256")
                != hashlib.sha256(_view_text(views, name).encode()).hexdigest()
                or not _valid_vector(vector)
                or len(vector) != len(self.query)
            ):
                continue
            denom = math.sqrt(sum(x * x for x in vector)) * self.norm
            result[name] = max(
                0.0, min(1.0, sum(a * b for a, b in zip(vector, self.query)) / denom)
            )
        return result
