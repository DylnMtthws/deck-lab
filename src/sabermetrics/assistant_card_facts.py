"""Dated, versioned Scryfall evidence without changing the legacy catalog.

Imports are offline and atomic. A retrieval timestamp is never an observation date.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CARD_FIELDS = (
    "id",
    "oracle_id",
    "name",
    "layout",
    "mana_cost",
    "cmc",
    "type_line",
    "oracle_text",
    "color_identity",
    "keywords",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "card_faces",
    "legalities",
    "prices",
    "scryfall_uri",
    "rulings_uri",
    "released_at",
)


def _date(value: str | None) -> str | None:
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("Dates must be ISO 8601 strings or null")
        datetime.fromisoformat(value)
    return value


def import_snapshot(
    db_path: str | Path,
    snapshot_path: str | Path,
    *,
    observed_at: str | None = None,
    retrieved_at: str | None = None,
    version: str | None = None,
    allow_older: bool = False,
) -> dict[str, Any]:
    """Import a supplied Scryfall array (or {data: [...]}) and optional rulings.

    Inline rulings must carry oracle_id, source, published_at and comment.
    observed_at is the source snapshot date supplied by its operator, not mtime.
    """
    from sabermetrics.assistant_knowledge_refresh import iter_card_file

    def file_hash() -> str:
        digest = hashlib.sha256()
        with Path(snapshot_path).open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        return digest.hexdigest()

    digest = file_hash()
    observed_at, retrieved_at = _date(observed_at), _date(retrieved_at)
    imported_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        # Stage to disk so a complete catalog snapshot is never held in Python.
        # No persistent evidence is modified until every record is validated.
        db.execute("PRAGMA temp_store=FILE")
        db.execute(
            "CREATE TEMP TABLE incoming_cards (printing_id TEXT PRIMARY KEY,oracle_id TEXT,payload TEXT NOT NULL)"
        )
        db.execute("CREATE INDEX incoming_oracles ON incoming_cards(oracle_id)")
        db.execute(
            "CREATE TEMP TABLE incoming_facts (catalog_id TEXT PRIMARY KEY,printing_id TEXT NOT NULL,payload TEXT NOT NULL)"
        )
        count = 0
        for card in iter_card_file(snapshot_path, data_object=True):
            if card.get("object") != "card":
                raise ValueError("Expected Scryfall card objects")
            card_id = card.get("id")
            if not isinstance(card_id, str) or not card_id:
                raise ValueError("Missing or duplicate printing identity")
            if not isinstance(card.get("oracle_id"), str):
                raise ValueError("Missing Oracle identity")  # noqa: TRY004
            try:
                db.execute(
                    "INSERT INTO incoming_cards VALUES (?,?,?)",
                    (
                        card_id,
                        card.get("oracle_id"),
                        json.dumps(card, ensure_ascii=False),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError("Missing or duplicate printing identity") from None
            count += 1
        if not count:
            raise ValueError("Snapshot must contain a nonempty Scryfall card array")
        if file_hash() != digest:
            raise ValueError("Snapshot changed during import")
        for card_id, payload in db.execute(
            "SELECT printing_id,payload FROM incoming_cards"
        ):
            card = json.loads(payload)
            row = db.execute(
                "SELECT oracle_id,name FROM cards WHERE id=?", (card_id,)
            ).fetchone()
            catalog_id = card_id
            if row is None:
                oracle_id = card.get("oracle_id")
                if not isinstance(oracle_id, str):
                    raise ValueError("Missing Oracle identity")
                row = db.execute(
                    "SELECT oracle_id,name FROM cards WHERE id=? AND oracle_id=?",
                    (oracle_id, oracle_id),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Printing absent from catalog: {card_id}")
                if (
                    db.execute(
                        "SELECT count(*) FROM incoming_cards WHERE oracle_id=?",
                        (oracle_id,),
                    ).fetchone()[0]
                    > 1
                ):
                    raise ValueError("Ambiguous printings for Oracle-keyed catalog row")
                catalog_id = oracle_id
            if row[0] != card.get("oracle_id") or row[1] != card.get("name"):
                raise ValueError(f"Catalog identity mismatch: {card_id}")
            rulings = card.get("rulings", [])
            if not isinstance(rulings, list):
                raise ValueError("Rulings must be an array")  # noqa: TRY004
            for ruling in rulings:
                if (
                    not isinstance(ruling, dict)
                    or ruling.get("oracle_id") != row[0]
                    or not all(
                        ruling.get(k) for k in ("source", "published_at", "comment")
                    )
                ):
                    raise ValueError(f"Unbound or unsourced ruling: {card_id}")
                _date(ruling["published_at"])
            selected = {key: card[key] for key in CARD_FIELDS if key in card}
            # Image and purchase links are intentionally excluded from face evidence.
            if "card_faces" in selected:
                if not isinstance(selected["card_faces"], list):
                    raise ValueError("card_faces must be an array")
                if not all(isinstance(face, dict) for face in selected["card_faces"]):
                    raise ValueError("card_faces entries must be objects")
                selected["card_faces"] = [
                    {k: face[k] for k in CARD_FIELDS if k in face and k != "card_faces"}
                    for face in selected["card_faces"]
                ]
            for link in ("scryfall_uri", "rulings_uri"):
                if link in selected:
                    if not isinstance(selected[link], str):
                        raise ValueError("Source URLs must be strings")
                    parsed = urlparse(selected[link])
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname not in ("scryfall.com", "api.scryfall.com")
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError(f"Invalid Scryfall source URL: {link}")
            selected["rulings"] = [
                {k: r[k] for k in ("oracle_id", "source", "published_at", "comment")}
                for r in rulings
            ]
            try:
                db.execute(
                    "INSERT INTO incoming_facts VALUES (?,?,?)",
                    (catalog_id, card_id, json.dumps(selected, ensure_ascii=False)),
                )
            except sqlite3.IntegrityError:
                raise ValueError(
                    "Multiple source printings mapped to one catalog identity"
                ) from None
        db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_fact_snapshots "
            "(content_hash TEXT PRIMARY KEY, version TEXT, observed_at TEXT, retrieved_at TEXT, imported_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_card_facts "
            "(snapshot_hash TEXT NOT NULL, card_id TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(snapshot_hash,card_id))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_card_fact_current "
            "(card_id TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_card_fact_printings "
            "(snapshot_hash TEXT NOT NULL, catalog_id TEXT NOT NULL, printing_id TEXT NOT NULL, "
            "PRIMARY KEY(snapshot_hash,catalog_id))"
        )
        db.execute(
            "INSERT OR IGNORE INTO assistant_card_fact_printings SELECT ?,catalog_id,printing_id FROM incoming_facts",
            (digest,),
        )
        existing = db.execute(
            "SELECT version,observed_at,retrieved_at FROM assistant_fact_snapshots WHERE content_hash=?",
            (digest,),
        ).fetchone()
        metadata = (version, observed_at, retrieved_at)
        if existing is not None and existing != metadata:
            raise ValueError(
                "Existing content hash has different provenance; refusing rewrite"
            )
        db.execute(
            "INSERT OR IGNORE INTO assistant_fact_snapshots VALUES (?,?,?,?,?)",
            (digest, version, observed_at, retrieved_at, imported_at),
        )
        db.execute(
            "INSERT OR IGNORE INTO assistant_card_facts SELECT ?,catalog_id,payload FROM incoming_facts",
            (digest,),
        )
        if not allow_older:
            for (card_id,) in db.execute("SELECT catalog_id FROM incoming_facts"):
                current = db.execute(
                    "SELECT s.observed_at,c.snapshot_hash FROM assistant_card_fact_current c JOIN assistant_fact_snapshots s ON s.content_hash=c.snapshot_hash WHERE c.card_id=?",
                    (card_id,),
                ).fetchone()
                if (
                    current
                    and current[1] != digest
                    and current[0]
                    and (
                        not observed_at or _instant(observed_at) < _instant(current[0])
                    )
                ):
                    raise ValueError(
                        "Older or undated snapshot cannot supersede dated current evidence without allow_older"
                    )
        db.execute(
            "INSERT OR REPLACE INTO assistant_card_fact_current SELECT catalog_id,? FROM incoming_facts",
            (digest,),
        )
    return {
        "content_hash": digest,
        "cards": count,
        "observed_at": observed_at,
        "retrieved_at": retrieved_at,
        "version": version,
    }


def card_facts(db_path: str | Path, card_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            return {"card_id": card_id, "status": "missing"}
        catalog = {
            k: row[k] if k in row.keys() else None  # noqa: SIM118
            for k in (*CARD_FIELDS, "image_uri", "is_legal_in_99", "roles")
        }
        result = {
            "card": catalog,
            "status": "catalog_only",
            "provenance": {
                "source": "local_catalog",
                "observed_at": None,
                "retrieved_at": None,
                "date_status": "unknown",
            },
            "legalities": None,
            "prices": None,
            "rulings": [],
            "limitations": [
                "Canonical snapshot missing; catalog legality flags are not verified legality."
            ],
        }
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assistant_card_fact_current'"
        ).fetchone()
        if not exists:
            return _bounded_response(result)
        evidence = db.execute(
            "SELECT f.payload,s.* FROM assistant_card_fact_current c "
            "JOIN assistant_card_facts f ON f.card_id=c.card_id AND f.snapshot_hash=c.snapshot_hash "
            "JOIN assistant_fact_snapshots s ON s.content_hash=c.snapshot_hash WHERE c.card_id=?",
            (card_id,),
        ).fetchone()
        if evidence is None:
            return _bounded_response(result)
        facts = json.loads(evidence["payload"])
        if (
            facts.get("oracle_id") != row["oracle_id"]
            or facts.get("name") != row["name"]
        ):
            result["status"] = "identity_mismatch"
            result["limitations"] = [
                "Canonical evidence rejected because catalog identity changed."
            ]
            return _bounded_response(result)
        rulings = facts.pop("rulings", [])
        rulings_total = len(rulings)
        legalities, prices = facts.pop("legalities", None), facts.pop("prices", None)
        provenance = {
            k: evidence[k]
            for k in (
                "content_hash",
                "version",
                "observed_at",
                "retrieved_at",
                "imported_at",
            )
        }
        differences = [
            field
            for field in (
                "oracle_text",
                "type_line",
                "mana_cost",
                "cmc",
                "color_identity",
            )
            if field in facts
            and field in row.keys()  # noqa: SIM118
            and _normalized_catalog_value(field, facts[field])
            != _normalized_catalog_value(field, catalog[field])
        ]
        limitations = [
            "Snapshot evidence may be stale; no live freshness verification.",
            "Prices are printing-specific snapshot quotes, not purchase offers.",
        ]
        if differences:
            limitations.append(
                "Supplied canonical snapshot differs from local catalog; card facts use snapshot evidence, "
                "while recommendations and candidate legality filters use the local catalog and may be stale."
            )
        provenance["catalog_differences"] = differences
        printing_id = facts["id"]
        facts["id"] = card_id
        provenance.update(
            printing_id=printing_id,
            catalog_id=card_id,
            source="scryfall_snapshot",
            date_status="dated_snapshot" if evidence["observed_at"] else "unknown",
            freshness="not_verified_current",
        )
        for key in ("image_uri", "is_legal_in_99", "roles"):
            facts[key] = catalog[key]
        return _bounded_response(
            {
                "card": facts,
                "status": "canonical_snapshot",
                "provenance": provenance,
                "legalities": (
                    {"values": legalities, "observed_at": evidence["observed_at"]}
                    if legalities is not None
                    else None
                ),
                "prices": (
                    {
                        "values": prices,
                        "observed_at": evidence["observed_at"],
                        "kind": "selected_printing_snapshot",
                        "printing_id": printing_id,
                    }
                    if prices is not None
                    else None
                ),
                "rulings": rulings,
                "rulings_total": rulings_total,
                "limitations": limitations,
                "catalog_fields": ["image_uri", "is_legal_in_99", "roles"],
            }
        )


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _bounded_response(result: dict[str, Any]) -> dict[str, Any]:
    """Cap all evidence text to 20k characters and collection breadth/depth."""
    budget = 16000
    nodes = 240
    omitted = {"characters": 0, "items": 0}

    def bounded(value: Any, depth: int = 0) -> Any:
        nonlocal budget, nodes
        nodes -= 1
        if nodes < 0:
            omitted["items"] += 1
            return None
        if depth > 5:
            omitted["items"] += 1
            return None
        if isinstance(value, str):
            take = min(len(value), 4000, budget)
            budget -= take
            omitted["characters"] += len(value) - take
            return value[:take]
        if isinstance(value, list):
            omitted["items"] += max(0, len(value) - 20)
            take = max(0, min(20, nodes))
            omitted["items"] += max(0, min(len(value), 20) - take)
            return [bounded(item, depth + 1) for item in value[:take]]
        if isinstance(value, dict):
            pairs = list(value.items())[: max(0, nodes)]
            omitted["items"] += max(0, len(pairs) - 32)
            return {str(key)[:80]: bounded(item, depth + 1) for key, item in pairs[:32]}
        return value if value is None or isinstance(value, (int, float, bool)) else None

    output: dict[str, Any] = bounded(result)
    if len(json.dumps(output, ensure_ascii=False)) > 32000:
        output = {
            "card": {
                key: str(result.get("card", {}).get(key, ""))[:128]
                for key in ("id", "oracle_id", "name")
            },
            "status": result.get("status"),
            "provenance": output.get("provenance"),
        }
        omitted["items"] += 1
    output["truncation"] = omitted
    if any(omitted.values()):
        output["truncation"][
            "notice"
        ] = "Evidence truncated for tool budget, including possible Oracle text; consult stored source for complete text."
    return output


def _normalized_catalog_value(field: str, value: Any) -> Any:
    if field == "color_identity":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if isinstance(value, list) and all(isinstance(color, str) for color in value):
            return sorted(value)
    if field == "cmc" and value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if isinstance(value, str):
        return " ".join(value.split())
    return value
