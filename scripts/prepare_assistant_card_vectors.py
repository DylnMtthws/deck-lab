"""Prepare optional Research Assistant card vectors with a local encoder only.

Run outside request handling, after the canonical card refresh. No model weights
are downloaded and no provider calls occur. Interrupted runs leave individually
validated rows usable; runtime rejects stale facts and incompatible encoders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from sabermetrics.assistant_candidate_recall import card_content
from sabermetrics.reference_layer.strategy_corpus import local_embedding_service


def prepare_vectors(path: Path, service: Any) -> dict[str, Any]:
    """Refresh reusable card vectors in small transactions; never train a model."""
    embedded = reused = 0
    with sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS assistant_card_vectors (oracle_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, embedding_model TEXT NOT NULL, embedding_json TEXT NOT NULL)"
        )
        rows = conn.execute(
            "SELECT * FROM cards WHERE is_legal_in_99=1 AND oracle_id IS NOT NULL GROUP BY oracle_id HAVING id=MIN(id) ORDER BY oracle_id"
        ).fetchall()
        pending = []
        for row in rows:
            card = dict(row)
            content = card_content(card)
            digest = hashlib.sha256(content.encode()).hexdigest()
            old = conn.execute(
                "SELECT content_hash,embedding_model FROM assistant_card_vectors WHERE oracle_id=?",
                (card["oracle_id"],),
            ).fetchone()
            if (
                old
                and old["content_hash"] == digest
                and old["embedding_model"] == service._model_name
            ):
                reused += 1
                continue
            pending.append((card["oracle_id"], digest, content))
        # Group similar lengths to avoid padding every short card to the longest
        # random card in its batch. Existing valid checkpoints remain reusable.
        pending.sort(key=lambda row: (len(row[2]), row[0]))
        for start in range(0, len(pending), 16):
            batch = pending[start : start + 16]
            texts = [row[2] for row in batch]
            vectors = (
                service.embed_many(texts)
                if hasattr(service, "embed_many")
                else [service.embed(text) for text in texts]
            )
            if len(vectors) != len(batch):
                raise ValueError("Encoder omitted or added card vectors")
            for (oid, digest, _), values in zip(batch, vectors):
                vector = [round(float(v), 7) for v in values]
                if (
                    not vector
                    or not all(math.isfinite(v) for v in vector)
                    or not sum(v * v for v in vector)
                ):
                    raise ValueError(
                        "Encoder returned an empty, zero, or nonfinite vector"
                    )
                conn.execute(
                    "INSERT INTO assistant_card_vectors(oracle_id,content_hash,embedding_model,embedding_json) VALUES(?,?,?,?) ON CONFLICT(oracle_id) DO UPDATE SET content_hash=excluded.content_hash,embedding_model=excluded.embedding_model,embedding_json=excluded.embedding_json",
                    (
                        oid,
                        digest,
                        service._model_name,
                        json.dumps(vector, separators=(",", ":")),
                    ),
                )
                embedded += 1
            conn.commit()
    return {
        "version": "assistant-card-vectors.v1",
        "embedding_model": service._model_name,
        "embedded": embedded,
        "reused": reused,
        "network_calls": 0,
        "training_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--threads", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    service = local_embedding_service(threads=args.threads)
    if service is None:
        parser.error("Configure a local strategy encoder before preparing card vectors")
    print(json.dumps(prepare_vectors(args.db, service), sort_keys=True))


if __name__ == "__main__":
    main()
