"""Current, versioned research artifacts; publication never certifies deck quality."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

MAX_CARDS = 24
MAX_HISTORY = 8
PRINTED_FIELDS = (
    "id",
    "name",
    "oracle_id",
    "provenance",
    "mana_cost",
    "mana_value",
    "cmc",
    "type_line",
    "oracle_text",
    "oracle_text_truncated",
    "card_faces",
    "layout",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "color_identity",
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def create_workspace(
    message: str, revision: int, generation: int, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A new request invalidates assessments; prior source cards remain inspectable."""
    old = copy.deepcopy(previous or {})
    previous_cards = old.get("cards", [])
    priority = sorted(
        previous_cards,
        key=lambda c: (not c.get("pinned", False), -int(c.get("generation", 0))),
    )
    retained_ids = {c["card_id"] for c in priority[:MAX_CARDS]}
    cards = [c for c in previous_cards if c["card_id"] in retained_ids]
    for card in cards:
        card["status"] = "superseded"
        for claim in card.get("claims", []):
            claim["previous_status"] = claim.get("status")
            claim["status"] = "superseded"
    return {
        "generation": generation,
        "deck_revision": revision,
        "request_text": message,
        "status": "queued",
        "cards": cards,
        "archived_omitted": max(0, len(previous_cards) - len(cards)),
        "steering": copy.deepcopy(
            old.get(
                "steering",
                {
                    "excluded_card_ids": [],
                    "prioritized_card_ids": [],
                },
            )
        ),
        "job": {"state": "queued", "checkpoint": "accepted", "retry_allowed": False},
    }


def catalog_card_record(card: dict[str, Any]) -> dict[str, Any] | None:
    """Whole supplied records only. No generated search text becomes evidence."""
    identity = str(card.get("id") or card.get("card_id") or "")
    text = card.get("oracle_text")
    if (
        not identity
        or not card.get("name")
        or not isinstance(text, str)
        or not text.strip()
    ):
        return None
    if card.get("oracle_text_truncated"):
        return None
    faces = card.get("card_faces")
    if faces is not None and (
        not isinstance(faces, list)
        or any(
            not isinstance(face, dict) or face.get("oracle_text_truncated")
            for face in faces
        )
    ):
        return None
    printed = {key: copy.deepcopy(card[key]) for key in PRINTED_FIELDS if key in card}
    if len(json.dumps(printed, ensure_ascii=False).encode()) > 32000:
        return None
    return {
        "card_id": identity,
        "name": str(card["name"]),
        "printed": printed,
        "source_sha256": _hash(printed),
        "status": "retrieved",
        "claims": [],
        "scope": "Supplied printed card text only; suitability and rules interactions are not established.",
    }


def workspace_event(cards: list[dict[str, Any]], *, reason: str = "") -> dict[str, Any]:
    return {
        "type": "partial_finding",
        "name": "research_card_update",
        "ok": True,
        "data": {
            "source_scope": "research workspace",
            "cards": cards[:MAX_CARDS],
            "reason": reason,
        },
    }


def apply_workspace_event(
    workspace: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    """Pure reducer. The session store additionally fences the current turn token."""
    result = copy.deepcopy(workspace)
    data = event.get("data") or {}
    if event.get("name") != "research_card_update" or event.get("ok") is not True:
        return result
    if data.get("generation", result.get("generation")) != result.get("generation"):
        return result
    if result.get("status") not in {"queued", "running"}:
        return result
    existing = {c["card_id"]: c for c in result.get("cards", [])}
    excluded = set((result.get("steering") or {}).get("excluded_card_ids", []))
    for incoming in data.get("cards", [])[:MAX_CARDS]:
        if not isinstance(incoming, dict) or not incoming.get("card_id"):
            continue
        cid = incoming["card_id"]
        active_count = sum(
            c.get("generation") == result["generation"]
            and c.get("status") != "superseded"
            for c in existing.values()
        )
        current = existing.get(cid, {})
        adds_active = (
            current.get("generation") != result["generation"]
            or current.get("status") == "superseded"
        )
        if cid in excluded or (adds_active and active_count >= MAX_CARDS):
            continue
        old = existing.get(cid, {})
        record = copy.deepcopy(incoming)
        if not isinstance(record.get("printed"), dict) or record.get(
            "source_sha256"
        ) != _hash(record["printed"]):
            continue
        # Late enrichment cannot erase an already reviewed assessment with identical facts.
        if (
            old.get("source_sha256") == record["source_sha256"]
            and old.get("status") == "reviewed"
            and record.get("status") == "retrieved"
        ):
            continue
        if (
            old.get("generation") == result["generation"]
            and old.get("source_sha256") == record["source_sha256"]
            and record.get("claims_complete") is False
            and old.get("claims")
            and record.get("claims")
            and {c.get("part_sha256") for c in old["claims"]}
            == {c.get("part_sha256") for c in record["claims"]}
        ):
            merged = {c["claim_id"]: c for c in old["claims"]}
            merged.update({c["claim_id"]: c for c in record["claims"]})
            record["claims"] = list(merged.values())
        signature = lambda c: _hash(
            {k: c.get(k) for k in ("source_sha256", "claims", "status")}
        )
        if old and signature(old) == signature(record):
            continue
        history = copy.deepcopy(old.get("history", []))
        if old.get("claims"):
            history.append(
                {
                    "version": old.get("version", 1),
                    "generation": old.get("generation"),
                    "source_sha256": old.get("source_sha256"),
                    "reason": data.get("reason") or "Assessment or evidence updated.",
                    "claims": copy.deepcopy(old["claims"]),
                }
            )
        record.update(
            version=int(old.get("version", 0)) + 1,
            generation=result["generation"],
            history=history[-MAX_HISTORY:],
            pinned=bool(old.get("pinned", False)),
            excluded=bool(old.get("excluded", False)),
        )
        existing[cid] = record
    result["cards"] = list(existing.values())
    return result


def reviewed_card_records(
    parts: list[str],
    corrected: list[str],
    receipt: dict[str, Any],
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Publish only exact-bound judgments; unreviewed text gets no inherited verdict."""
    from sabermetrics.assistant_claim_fragments import (
        VERSION,
        fragment_units,
        matching_judgment,
    )

    if receipt.get("claim_unit_version") != VERSION:
        return []
    source_manifest = receipt.get("workspace_card_sources")
    if not isinstance(source_manifest, dict):
        return []
    judgments = {j["claim_id"]: j for j in receipt.get("claim_judgments", [])}
    by_part: dict[int, list[dict[str, Any]]] = {}
    source_hashes = receipt.get("source_sha256", [])
    for unit in fragment_units(parts, set()):
        judgment = judgments.get(unit["claim_id"], {})
        if not matching_judgment(unit, judgment):
            continue
        index = unit["part_index"]
        rendered = judgment.get("rendered_claim", "")
        if not isinstance(rendered, str) or not rendered or index >= len(corrected):
            continue
        status = judgment.get("verdict", "unverified")
        if judgment.get("unverified_remainder"):
            status = "unverified"
        if rendered not in corrected[index]:
            status = "withdrawn"
        by_part.setdefault(index, []).append(
            {
                "claim_id": f"{index}:{unit['start']}:{unit['claim_sha256']}",
                "text": rendered,
                "original_text": unit["claim"],
                "correction_applied": bool(judgment.get("correction_applied")),
                "status": status,
                "category": judgment.get("category"),
                "claim_sha256": unit["claim_sha256"],
                "part_sha256": unit["part_sha256"],
                "reason": judgment.get("reason", ""),
                "evidence": [
                    {
                        **citation,
                        "source_sha256": (
                            source_hashes[citation["source_index"]]
                            if 0
                            <= citation.get("source_index", -1)
                            < len(source_hashes)
                            else None
                        ),
                    }
                    for citation in judgment.get("citations", [])
                ],
            }
        )
    records = []
    for index, claims in by_part.items():
        prefix = parts[index].split(" — ", 1)[0]
        matches = [
            c
            for c in cards
            if c.get("name")
            and (
                f": {c['name']} (" in prefix
                or prefix.startswith(str(c["name"]) + " (")
                or prefix.endswith(": " + str(c["name"]))
            )
        ]
        if len(matches) != 1:
            continue
        record = catalog_card_record(matches[0])
        if (
            record is not None
            and source_manifest.get(record["card_id"]) == record["source_sha256"]
        ):
            record.update(
                status="reviewed",
                claims=claims,
                claims_complete=len(claims)
                == sum(u["part_index"] == index for u in fragment_units(parts, set())),
                scope="Claim-scoped review against supplied evidence; comparative recommendation and request completion are separate checks.",
            )
            records.append(record)
    return records
