"""Lossless, bounded local printed evidence for an existing public deck scope.

This module grants no routing or action authority. A failed receipt means use the
normal tool route. Sources are independent full canonical-reader responses, not
claims of current legality, strategic quality, or available battlefield resources.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from sabermetrics.assistant_card_facts import CARD_FIELDS, card_facts_from_connection
from sabermetrics.deck_documents import _is_public_library_zone

MAX_ENTRIES = 128
# The caller separately owns its 40 KB envelope. Oversize evidence abstains
# rather than reducing entry coverage, printed text, faces or source provenance.
MAX_PACKET_BYTES = 38000
VERSION = "assistant-deck-review.v2"
PROVENANCE_FIELDS = ("printing_id", "catalog_id")
SCOPE = (
    "Complete current public-library and commander entries in document order; "
    "local printed snapshot evidence, not live freshness, verified legality or "
    "prices, strategy validation, or available battlefield resources. Missing "
    "fields mean unknown, never absence of an ability. No discovery or action authority."
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class _Fallback(Exception):
    pass


class _CallbackFailure(Exception):
    def __init__(self, error: BaseException):
        self.error = error


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Fallback("unsupported_identity")
    return value.strip()


# Only metadata and repeated descriptors are interned. Printed costs, text, faces and
# restrictions remain verbatim JSON values in positional source rows.
INTERN_FIELDS = (
    "id",
    "oracle_id",
    "keywords",
    "released_at",
    "layout",
    "type_line",
    "color_identity",
)
URL_FIELDS = ("scryfall_uri", "rulings_uri")
# Printing metadata may differ for the same Oracle identity. Compare every
# available mechanical field, retaining absent versus explicit null values.
MECHANICS = (
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
)


def _mechanics(card: dict[str, Any]) -> dict[str, Any]:
    result = {key: card[key] for key in MECHANICS if key in card}
    if isinstance(result.get("card_faces"), list):
        result["card_faces"] = [_mechanics(face) for face in result["card_faces"]]
    return result


URL_PARTS = re.compile(
    r"(https?://[^/]+/|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
DECODE_CONTRACT = (
    "records=[schema,values,provenance,printing_id]; "
    "schema indexes card_schemas; values zip its field names (absent fields omitted, "
    "null preserved). intern_fields values index metadata; url_fields are null or "
    "[0,set,number,optional_slug] for https://scryfall.com/card/set/number/slug"
    "?utm_source=api (default slug: NFKD ASCII lowercase card.name, nonalphanumeric "
    "runs become hyphens, trimmed); [1,printing_id_metadata_index] for "
    "https://api.scryfall.com/cards/ID/rulings; [2,indexes] joins metadata strings "
    "exactly for other URLs. provenance indexes the shared provenance object; "
    "merge printing_id (metadata index) and catalog_id=card.id into it. entries="
    "[entry_id,record_index,optional_quantity,optional_commander_0_or_1]; "
    "omitted trailing entry fields default to quantity=1 and is_commander=false; "
    "identities come from card. "
    "Full canonical responses and their SHA256 hashes remain in local source_cards "
    "keyed by card.id; on-wire source_record_ref=card.id. Receipt packet_hash binds "
    "this entire packet using sorted compact UTF-8 JSON. Printed facts and source provenance are preserved exactly."
)


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def decode_deck_review_packet(
    packet: dict[str, Any],
    source_cards: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Expand all exact printed fields, identities and provenance in entry order.

    Local canonical-response hashes are resolved only when source_cards is
    supplied. Otherwise source_record_ref explicitly identifies that local map
    entry; a hash of a partial decoded card must never stand in for its hash.
    """
    result = []
    metadata = packet["metadata"]
    for entry in packet["entries"]:
        entry_id, record_index = entry[:2]
        quantity = entry[2] if len(entry) > 2 else 1
        commander = bool(entry[3]) if len(entry) > 3 else False
        schema, values, provenance_index, printing_id = packet["records"][record_index]
        card: dict[str, Any] = {}
        for key, value in zip(packet["card_schemas"][schema], values, strict=True):
            if key in packet["intern_fields"]:
                value = metadata[value]
            elif key in packet["url_fields"] and value is not None:
                if value[0] == 0:
                    slug = value[3] if len(value) == 4 else _slug(card["name"])
                    value = f"https://scryfall.com/card/{value[1]}/{value[2]}/{slug}?utm_source=api"
                elif value[0] == 1:
                    value = (
                        f"https://api.scryfall.com/cards/{metadata[value[1]]}/rulings"
                    )
                else:
                    value = "".join(metadata[index] for index in value[1])
            card[key] = copy.deepcopy(value)
        row = {
            "entry_id": entry_id,
            "card_id": card["id"],
            "oracle_id": card["oracle_id"],
            "quantity": quantity,
            "is_commander": commander,
            "card": card,
            "provenance": {
                **copy.deepcopy(packet["provenance"][provenance_index]),
                "printing_id": metadata[printing_id],
                "catalog_id": card["id"],
            },
            "source_record_ref": card["id"],
        }
        if source_cards is not None:
            row["source_record_hash"] = source_cards[card["id"]]["source_record_hash"]
        result.append(row)
    return result


# Inspect at most MAX_ENTRIES keyed identities, never the whole database.
MAX_SOURCE_VALUE_BYTES = 256_000
_OVERSIZED = "__deck_review_oversized_source__"


def _database_identity(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return str(resolved), stat.st_dev, stat.st_ino


def _source_records(connection: sqlite3.Connection, card_ids: set[str]) -> str:
    """Hash raw scoped records, retaining missing vs present and raw payloads.

    These reads share the caller's SQLite snapshot. A bounded projection keeps
    oversized values out of Python; no global database generation is consulted.
    """
    digest = hashlib.sha256()

    def record(query: str, parameters: tuple[str, ...]) -> tuple[Any, ...] | None:
        with closing(connection.execute(query, parameters)) as cursor:
            names = tuple(column[0] for column in cursor.description)
            rows = cursor.fetchmany(2)
        if len(rows) > 1:
            raise _Fallback("invalid_or_unavailable_source")
        row = tuple(rows[0]) if rows else None
        if row and any(value == _OVERSIZED for value in row):
            raise _Fallback("invalid_or_unavailable_source")
        digest.update(_json((names, row)).encode("utf-8"))
        return row

    columns = {row[1] for row in connection.execute("PRAGMA table_info(cards)")}
    fields = [
        field
        for field in (*CARD_FIELDS, "image_uri", "is_legal_in_99", "roles")
        if field in columns
    ]

    def bounded(field: str) -> str:
        return (
            f'CASE WHEN length(CAST("{field}" AS BLOB)) > {MAX_SOURCE_VALUE_BYTES} '
            f"THEN '{_OVERSIZED}' "
            f'ELSE "{field}" END AS "{field}"'
        )

    catalog_fields = ",".join(bounded(field) for field in fields)
    snapshot_fields = ",".join(
        bounded(field)
        for field in (
            "content_hash",
            "version",
            "observed_at",
            "retrieved_at",
            "imported_at",
        )
    )
    for card_id in sorted(card_ids):
        record(f"SELECT {catalog_fields} FROM cards WHERE id=?", (card_id,))
        current = record(
            f"SELECT {bounded('snapshot_hash')} FROM assistant_card_fact_current WHERE card_id=?",
            (card_id,),
        )
        if current:
            snapshot_hash = current[0]
            record(
                f"SELECT {bounded('payload')} FROM assistant_card_facts "
                "WHERE snapshot_hash=? AND card_id=?",
                (snapshot_hash, card_id),
            )
            record(
                f"SELECT {snapshot_fields} FROM assistant_fact_snapshots WHERE content_hash=?",
                (snapshot_hash,),
            )
    return digest.hexdigest()


def build_deck_review_packet(
    db_path: str | Path,
    document: dict[str, Any],
    *,
    check_current: Callable[[], Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (packet, source_cards, receipt), or (None, {}, fallback receipt).

    source_cards is keyed by normalized catalog card ID. Each value contains the
    full card fields for review plus `printed_evidence` (the exact canonical
    reader response), `provenance`, and `source_record_hash`. Hashes use sorted,
    compact, UTF-8 JSON. No cross-call cache or network access is used.

    check_current is called between reads and before delivery. Its exceptions,
    including cancellation, propagate unchanged. Other failures abstain without
    exposing database messages. All evidence comes from one read-only SQLite
    snapshot. A fresh scoped read validates catalog rows, current pointers, raw
    payloads and snapshot metadata before delivery; unrelated writes do not
    invalidate it. This verifies local evidence, never upstream freshness.

    Without a persistent canonical change log, a change fully restored before
    validation (ABA) cannot be distinguished from no change. Immutable snapshot
    records are compared too, including in-place edits without pointer changes.
    """

    def check() -> None:
        if check_current:
            try:
                check_current()
            except BaseException as error:
                raise _CallbackFailure(error) from error

    count = 0
    try:
        check()
        doc = copy.deepcopy(document)
        zones: dict[str, str] = {}
        for zone in doc.get("zones", []):
            zone_id, zone_name = zone["id"], zone["name"]
            if not isinstance(zone_name, str) or not zone_name.strip():
                raise _Fallback("unsupported_scope")
            zone_name = zone_name.strip().casefold()
            if zone_id in zones and zones[zone_id] != zone_name:
                raise _Fallback("unsupported_scope")
            zones[zone_id] = zone_name
        entries = []
        entry_ids = set()
        for raw in doc["entries"]:
            commander = raw.get("is_commander", False)
            if commander not in (False, True, 0, 1):
                raise _Fallback("unsupported_scope")
            zone_id = raw.get("zone_id")
            zone = raw.get("zone_name")
            if zone_id is not None:
                if zone_id not in zones:
                    raise _Fallback("unsupported_scope")
                if "zone_name" in raw and (
                    not isinstance(zone, str)
                    or zone.strip().casefold() != zones[zone_id]
                ):
                    raise _Fallback("unsupported_scope")
                zone = zones[zone_id]
            # No zone ID is the document's valid default Library/Unsorted scope.
            if not commander and not _is_public_library_zone(zone):
                continue
            entry_id = _identity(raw.get("id", raw.get("entry_id")))
            if entry_id in entry_ids:
                raise _Fallback("duplicate_conflict")
            entry_ids.add(entry_id)
            card_id = _identity(raw.get("card_id", raw.get("cardid")))
            if "cardid" in raw and _identity(raw["cardid"]) != card_id:
                raise _Fallback("identity_conflict")
            quantity = raw.get("quantity", 1)
            if type(quantity) is not int or quantity < 1:
                raise _Fallback("unsupported_quantity")
            entries.append((raw, entry_id, card_id, quantity, bool(commander)))
        count = len(entries)
        if not 0 < count <= MAX_ENTRIES:
            raise _Fallback("entry_limit" if count else "empty_scope")

        sources: dict[str, dict[str, Any]] = {}
        records: list[Any] = []
        provenance: list[dict[str, Any]] = []
        packed_entries: list[Any] = []
        provenance_indexes: dict[str, int] = {}
        record_indexes: dict[str, int] = {}
        oracle_records: dict[str, str] = {}
        schemas: list[list[str]] = []
        metadata: list[Any] = []
        metadata_indexes: dict[str, int] = {}

        def intern(value: Any) -> int:
            key = _json(value)
            if key not in metadata_indexes:
                metadata_indexes[key] = len(metadata)
                metadata.append(copy.deepcopy(value))
            return metadata_indexes[key]

        def pack_field(key: str, value: Any, card: dict[str, Any]) -> Any:
            if key in INTERN_FIELDS:
                return intern(value)
            if key in URL_FIELDS and value is not None:
                if not isinstance(value, str):
                    raise _Fallback("unsupported_metadata")
                match = re.fullmatch(
                    r"https://scryfall\.com/card/([^/]+)/([^/]+)/([^/?]+)\?utm_source=api",
                    value,
                )
                if match:
                    edition, number, slug = match.groups()
                    result: list[Any] = [0, edition, number]
                    if slug != _slug(card["name"]):
                        result.append(slug)
                    return result
                match = re.fullmatch(
                    r"https://api\.scryfall\.com/cards/([^/]+)/rulings", value
                )
                if match:
                    return [1, intern(match[1])]
                return [2, [intern(part) for part in URL_PARTS.split(value) if part]]
            return copy.deepcopy(value)

        scope_entries = []
        path = Path(db_path)
        identity = _database_identity(path)
        uri = Path(identity[0]).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as observer:
            observer.execute("BEGIN")
            expected = _source_records(observer, {entry[2] for entry in entries})

            def stable() -> None:
                check()
                if _database_identity(path) != identity:
                    raise _Fallback("source_changed")

            for raw, entry_id, card_id, quantity, commander in entries:
                stable()
                # Read every eligible entry, including repeated identities; never
                # reuse a partial response or select a preferred printing.
                evidence = card_facts_from_connection(observer, card_id)
                stable()
                if evidence.get("status") != "canonical_snapshot":
                    raise _Fallback("missing_or_unsupported_source")
                truncation = evidence.get("truncation")
                if not isinstance(truncation, dict) or any(truncation.values()):
                    raise _Fallback("truncated_source")
                card, prov = evidence["card"], evidence["provenance"]
                oracle_id = _identity(card.get("oracle_id"))
                name = _identity(card.get("name"))
                if (
                    card.get("id") != card_id
                    or (
                        raw.get("oracle_id") is not None
                        and _identity(raw["oracle_id"]) != oracle_id
                    )
                    or (raw.get("name") is not None and raw["name"] != name)
                ):
                    raise _Fallback("identity_conflict")
                if (
                    prov.get("source") != "scryfall_snapshot"
                    or prov.get("catalog_id") != card_id
                    or not all(
                        isinstance(prov.get(key), str) and prov[key]
                        for key in ("content_hash", "printing_id")
                    )
                ):
                    raise _Fallback("unsupported_provenance")
                # Missing text cannot become negative evidence. Explicit empty
                # Oracle text is valid (e.g. a vanilla creature).
                faces = card.get("card_faces")
                if faces is not None:
                    if (
                        not isinstance(faces, list)
                        or len(faces) < 2
                        or any(
                            not isinstance(face, dict)
                            or not isinstance(face.get("name"), str)
                            or not isinstance(face.get("oracle_text"), str)
                            for face in faces
                        )
                    ):
                        raise _Fallback("incomplete_printed_record")
                elif not isinstance(card.get("oracle_text"), str):
                    raise _Fallback("incomplete_printed_record")
                if (
                    "//" in name
                    or card.get("layout")
                    in {"transform", "modal_dfc", "split", "adventure", "flip"}
                ) and not faces:
                    raise _Fallback("incomplete_printed_record")

                printed = {
                    key: value
                    for key, value in card.items()
                    if key not in {"image_uri", "is_legal_in_99", "roles"}
                }
                oracle_hash = _hash(_mechanics(printed))
                if (
                    oracle_id in oracle_records
                    and oracle_records[oracle_id] != oracle_hash
                ):
                    raise _Fallback("duplicate_conflict")
                oracle_records[oracle_id] = oracle_hash
                record_hash = _hash(evidence)
                if card_id in sources:
                    if sources[card_id]["source_record_hash"] != record_hash:
                        raise _Fallback("duplicate_conflict")
                else:
                    sources[card_id] = {
                        **copy.deepcopy(card),
                        "provenance": copy.deepcopy(prov),
                        "printed_evidence": copy.deepcopy(evidence),
                        "source_record_hash": record_hash,
                    }
                    shared_prov = {
                        key: value
                        for key, value in prov.items()
                        if key not in PROVENANCE_FIELDS
                    }
                    prov_key = _json(shared_prov)
                    if prov_key not in provenance_indexes:
                        provenance_indexes[prov_key] = len(provenance)
                        provenance.append(copy.deepcopy(shared_prov))
                    record_indexes[card_id] = len(records)
                    # A schema is shared by records with the same present fields;
                    # explicit null occupies a position, absence never does.
                    schema = [key for key in CARD_FIELDS if key in printed]
                    schema.extend(sorted(set(printed) - set(schema)))
                    if schema not in schemas:
                        schemas.append(schema)
                    records.append(
                        [
                            schemas.index(schema),
                            [pack_field(key, printed[key], printed) for key in schema],
                            provenance_indexes[prov_key],
                            intern(prov["printing_id"]),
                        ]
                    )
                entry = [entry_id, card_id, oracle_id, quantity, commander]
                scope_entries.append(entry)
                packed_entry = [entry_id, record_indexes[card_id]]
                if commander:
                    packed_entry.extend([quantity, 1])
                elif quantity != 1:
                    packed_entry.append(quantity)
                packed_entries.append(packed_entry)

            scope = {
                "document_id": doc.get("id"),
                "revision": doc.get("revision"),
                "entries": scope_entries,
            }
            packet = {
                "version": VERSION,
                "scope": SCOPE,
                "document_scope_hash": _hash(scope),
                "decode_contract": DECODE_CONTRACT,
                "card_schemas": schemas,
                "intern_fields": list(INTERN_FIELDS),
                "url_fields": list(URL_FIELDS),
                "metadata": metadata,
                "entries": packed_entries,
                "records": records,
                "provenance": provenance,
            }
            # Also bound ordinary JSON serialization with whitespace, so callers
            # need not use our compact serializer to satisfy the transport cap.
            size = len(
                json.dumps(packet, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
            if size > MAX_PACKET_BYTES:
                raise _Fallback("packet_size_limit")
            stable()
            # End assembly's snapshot to observe committed WAL changes. The
            # validation read is itself atomic across every scoped record.
            observer.rollback()
            observer.execute("BEGIN")
            if _source_records(observer, set(sources)) != expected:
                raise _Fallback("source_changed")
            if _database_identity(path) != identity:
                raise _Fallback("source_changed")
        return (
            packet,
            sources,
            {
                "status": "ready",
                "fallback": False,
                "entries": count,
                "packet_bytes": size,
                "packet_hash": _hash(packet),
                "document_scope_hash": packet["document_scope_hash"],
            },
        )
    except _CallbackFailure as failure:
        raise failure.error
    except _Fallback as failure:
        reason = str(failure)
    except (
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        OverflowError,
    ):
        reason = "invalid_or_unavailable_source"
    return (
        None,
        {},
        {
            "status": "fallback",
            "fallback": True,
            "route": "normal_tools",
            "reason": reason,
            "entries": count,
        },
    )
