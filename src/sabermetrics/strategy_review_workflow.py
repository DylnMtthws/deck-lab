"""Offline review queue and SSH-signed, content-bound strategy import.

Trust records are operator-owned inputs, never accepted from source manifests.
Signatures attest attribution and exact bytes, not correctness or legal rights.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sabermetrics.assistant_source_import import source_arguments
from sabermetrics.reference_layer.strategy_corpus import ingest_text

NAMESPACE = "deck-lab-strategy-review-v1"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Bind permission, author, version and content hash, not just prose."""
    return hashlib.sha256(canonical(manifest)).hexdigest()


def validate_trusted_reviewers(registry: dict[str, Any]) -> None:
    """Reject malformed operator records before interpreting any expertise."""
    if not isinstance(registry, dict):
        raise TypeError("Trusted registry must be an object")
    for reviewer_id, trust in registry.items():
        if (
            not isinstance(reviewer_id, str)
            or not reviewer_id.strip()
            or not isinstance(trust, dict)
            or trust.get("actor_kind") not in {"human", "ai"}
            or any(
                not isinstance(trust.get(key), str) or not trust[key].strip()
                for key in ("principal", "public_key")
            )
        ):
            raise ValueError("Invalid trusted reviewer identity or actor_kind")
        scopes = trust.get("expert_scopes", [])
        if not isinstance(scopes, list) or any(
            not isinstance(scope, str) or not scope.strip() for scope in scopes
        ):
            raise ValueError("expert_scopes must be a list of nonblank exact scopes")
        basis = trust.get("expertise_basis")
        if ("expertise_basis" in trust or scopes) and (
            not isinstance(basis, str) or not basis.strip()
        ):
            raise ValueError("expertise_basis must be a nonblank string")


def _source_id(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        "\0".join(
            (
                manifest["document"],
                manifest.get("source_url") or "",
                manifest["version"],
                manifest.get("section", ""),
            )
        ).encode()
    ).hexdigest()


def verify_signature(
    payload: dict[str, Any], signature: str, trust: dict[str, Any]
) -> bool:
    """Verify an SSHSIG against one explicitly trusted principal/key, offline."""
    principal, key = trust["principal"], trust["public_key"]
    if (
        not isinstance(principal, str)
        or not principal
        or any(c.isspace() for c in principal)
    ):
        raise ValueError("Trusted principal must be a single nonempty token")
    if (
        not isinstance(key, str)
        or "\n" in key
        or not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-"))
    ):
        raise ValueError("Trusted public key must be one SSH public key")
    if not isinstance(signature, str) or len(signature) > 16384:
        raise ValueError("Invalid signature")
    with tempfile.TemporaryDirectory(prefix="strategy-review-") as folder:
        allowed, sig = Path(folder) / "allowed", Path(folder) / "review.sig"
        allowed.write_text(f"{principal} {key}\n")
        sig.write_text(signature)
        try:
            result = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed),
                    "-I",
                    principal,
                    "-n",
                    NAMESPACE,
                    "-s",
                    str(sig),
                ],
                input=canonical(payload),
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
    return result.returncode == 0


def assess_review(
    manifest: dict[str, Any],
    folder: Path,
    review: dict[str, Any] | None,
    trusted_reviewers: dict[str, Any],
) -> dict[str, Any]:
    """Validate permission first; untrusted review declarations stay queued."""
    validate_trusted_reviewers(trusted_reviewers)
    source_arguments(manifest, folder)
    for key in ("source_key", "version", "document", "permission", "author"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"Source requires nonempty {key}")
    if manifest.get("authorship") not in {"human", "ai", "mixed", "not_recorded"}:
        raise ValueError(
            "Source authorship must be explicit: human, ai, mixed or not_recorded"
        )
    result: dict[str, Any] = {
        "source_key": manifest["source_key"],
        "version": manifest["version"],
        "manifest_sha256": manifest_digest(manifest),
        "authorship": manifest.get("authorship", "not_recorded"),
        "review_status": "pending",
        "expert_reviewed": False,
        "missing_coverage": [
            "attributed_signed_human_review",
            "expert_strategy_review",
        ],
    }
    if not review:
        return result
    payload = review.get("payload", {})
    if not isinstance(payload, dict):
        raise TypeError("Review payload must be an object")
    required = ("reviewer_id", "reviewed_at", "scope", "decision", "manifest_sha256")
    if not all(
        isinstance(payload.get(k), str) and payload[k].strip() for k in required
    ):
        raise ValueError(
            "Review payload requires identity, date, scope, decision and manifest hash"
        )
    date.fromisoformat(payload["reviewed_at"])
    trust = trusted_reviewers.get(payload["reviewer_id"])
    if payload["manifest_sha256"] != result["manifest_sha256"]:
        raise ValueError("Review does not bind this exact source manifest")
    if payload["decision"] not in {"approved", "changes_requested", "rejected"}:
        raise ValueError("Unsupported review decision")
    if not trust or not verify_signature(payload, review.get("signature", ""), trust):
        result["review_status"] = "unverified_signature_or_identity"
        return result
    result.update(
        reviewer_id=payload["reviewer_id"],
        reviewed_at=payload["reviewed_at"],
        review_scope=payload["scope"],
        decision=payload["decision"],
    )
    result.update(_review_classification(payload, trust))
    return result


def _review_classification(
    payload: dict[str, Any], trust: dict[str, Any]
) -> dict[str, Any]:
    """Interpret signed scope and decision using only the current operator registry."""
    result: dict[str, Any] = {}
    human = trust.get("actor_kind") == "human"
    expert = (
        human
        and bool(trust.get("expertise_basis"))
        and payload["scope"] in trust.get("expert_scopes", [])
    )
    result["review_status"] = (
        ("expert_reviewed" if expert else "human_reviewed") if human else "ai_reviewed"
    )
    result["expert_reviewed"] = expert and payload["decision"] == "approved"
    result["missing_coverage"] = (
        [] if human else ["attributed_signed_human_review"]
    ) + ([] if result["expert_reviewed"] else ["expert_strategy_review"])
    return result


def import_reviewed(
    path: Path,
    db_path: Path,
    review: dict[str, Any],
    trusted_reviewers: dict[str, Any],
) -> dict[str, Any]:
    """Import approved human review, retaining exact signed provenance beside chunks."""
    manifest = json.loads(path.read_text())
    assessment = assess_review(manifest, path.parent, review, trusted_reviewers)
    if (
        assessment["review_status"] not in {"human_reviewed", "expert_reviewed"}
        or assessment.get("decision") != "approved"
    ):
        raise ValueError("Reviewed import requires a verified approved human review")
    arguments = source_arguments(manifest, path.parent)
    # A failed receipt write leaves chunks explicitly pending, never expert-certified.
    arguments["review_status"] = "signed_review_pending_receipt"
    count = ingest_text(db_path, embed=False, **arguments)
    receipt = canonical(
        {
            "source_manifest": manifest,
            "assessment": assessment,
            "review": review,
            "trusted_identity": trusted_reviewers[review["payload"]["reviewer_id"]],
        }
    ).decode()
    receipt_sha256 = hashlib.sha256(receipt.encode()).hexdigest()
    source_id = _source_id(manifest)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS strategy_signed_reviews (receipt_sha256 TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO strategy_signed_reviews VALUES (?, ?, ?)",
            (receipt_sha256, assessment["manifest_sha256"], receipt),
        )
        rows = conn.execute(
            "SELECT chunk_id, provenance_json FROM reference_chunk_metadata WHERE source_id=?",
            (source_id,),
        ).fetchall()
        for chunk_id, raw in rows:
            provenance = json.loads(raw)
            provenance["review_status"] = assessment["review_status"]
            provenance["review_manifest_sha256"] = assessment["manifest_sha256"]
            provenance["review_receipt_sha256"] = receipt_sha256
            provenance["review_scope"] = assessment["review_scope"]
            provenance["reviewer_id"] = assessment["reviewer_id"]
            conn.execute(
                "UPDATE reference_chunk_metadata SET provenance_json=? WHERE chunk_id=?",
                (json.dumps(provenance, sort_keys=True), chunk_id),
            )
    return {**assessment, "chunks": count, "status": "imported"}


def audit_receipt(
    db_path: Path, receipt_sha256: str, trusted_reviewers: dict[str, Any]
) -> dict[str, Any]:
    """Read-only offline binding audit; raises ValueError on tampering or budgets.

    Caller-supplied trust is authoritative, never the receipt's identity snapshot.
    Review status is recomputed from current trust; stale classifications fail
    closed. Successful binding checks do not certify expertise or source accuracy.
    """
    validate_trusted_reviewers(trusted_reviewers)
    scope = "Signature and stored content binding only; no expertise or correctness certification."
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT manifest_sha256, length(CAST(receipt_json AS BLOB)) "
            "FROM strategy_signed_reviews WHERE receipt_sha256=?",
            (receipt_sha256,),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown signed receipt")
        digest, size = row
        if size > 8_000_000:
            raise ValueError("Receipt exceeds offline audit budget")
        raw = conn.execute(
            "SELECT receipt_json FROM strategy_signed_reviews WHERE receipt_sha256=?",
            (receipt_sha256,),
        ).fetchone()[0]
        if hashlib.sha256(raw.encode()).hexdigest() != receipt_sha256:
            raise ValueError("Receipt hash mismatch")
        receipt = json.loads(raw)
        if "source_manifest" not in receipt:
            return {"status": "legacy_unauditable", "scope": scope}
        manifest = receipt["source_manifest"]
        review = receipt["review"]
        payload = review["payload"]
        if (
            manifest_digest(manifest) != digest
            or payload.get("manifest_sha256") != digest
        ):
            raise ValueError("Signed manifest digest mismatch")
        trust = trusted_reviewers.get(payload.get("reviewer_id"))
        if not trust or not verify_signature(
            payload, review.get("signature", ""), trust
        ):
            raise ValueError("Unverified signature or identity")
        classification = _review_classification(payload, trust)
        # Match ingest_text's defaults/normalization without reopening source files.
        expected_metadata = (
            manifest["document"],
            manifest.get("section", ""),
            manifest.get("tier", 2),
            manifest.get("source_url"),
            manifest["version"],
            manifest["permission"],
            manifest.get("source_kind", "user_supplied"),
            manifest["source_key"],
            manifest.get("commander", "")[:200],
            manifest.get("archetype", "")[:100],
        )
        expected_provenance = {
            "author": manifest.get("author", ""),
            "source_date": manifest.get("source_date", ""),
            "source_class": manifest.get("source_class")
            or manifest.get("source_kind", "user_supplied"),
            "primary_links": manifest.get("primary_links") or [],
            "limitations": manifest.get("limitations", ""),
            "permission_basis": manifest.get("permission_basis", ""),
            "license_url": manifest.get("license_url", ""),
            "attribution_url": manifest.get("attribution_url", ""),
            "review_status": classification["review_status"],
            "reviewer_id": payload["reviewer_id"],
            "review_scope": payload["scope"],
        }
        source_id = _source_id(manifest)
        count, size, provenance_size = conn.execute(
            "SELECT count(*), coalesce(sum(length(CAST(r.content AS BLOB))), 0), "
            "coalesce(sum(length(CAST(m.provenance_json AS BLOB))), 0) "
            "FROM reference_chunk_metadata m LEFT JOIN reference_chunks r ON r.id=m.chunk_id "
            "WHERE m.source_id=?",
            (source_id,),
        ).fetchone()
        if not 0 < count <= 1000 or size > 1_000_000 or provenance_size > 8_000_000:
            raise ValueError("Missing chunks or offline audit budget exceeded")
        rows = conn.execute(
            "SELECT r.content, m.char_start, m.char_end, m.content_hash, "
            "m.provenance_json, m.chunk_id, r.document, r.section, r.tier, "
            "m.source_url, m.version, m.permission, m.source_kind, m.source_key, "
            "m.commander, m.archetype FROM reference_chunk_metadata m "
            "LEFT JOIN reference_chunks r ON r.id=m.chunk_id "
            "WHERE m.source_id=? ORDER BY m.char_start LIMIT 1001",
            (source_id,),
        ).fetchall()
        parts, offset = [], 0
        for index, row in enumerate(rows):
            content, start, end, content_digest, provenance_raw, chunk_id, *metadata = (
                row
            )
            if (
                chunk_id != f"source:{source_id}:{index}"
                or tuple(metadata) != expected_metadata
            ):
                raise ValueError("Stored chunk source metadata mismatch")
            if (
                not isinstance(content, str)
                or not content
                or type(start) is not int
                or type(end) is not int
                or start != offset
                or end != start + len(content)
                or hashlib.sha256(content.encode()).hexdigest() != content_digest
            ):
                raise ValueError("Stored chunk content or offsets mismatch")
            provenance = json.loads(provenance_raw)
            if (
                not isinstance(provenance, dict)
                or provenance.get("review_receipt_sha256") != receipt_sha256
                or provenance.get("review_manifest_sha256") != digest
            ):
                raise ValueError("Stored chunk receipt binding mismatch")
            if any(
                key not in provenance or canonical(provenance[key]) != canonical(value)
                for key, value in expected_provenance.items()
            ):
                raise ValueError("Stored chunk provenance metadata mismatch")
            # Optional declarations cannot override the signed payload/current registry.
            for key, value in {
                "reviewed_at": payload["reviewed_at"],
                "decision": payload["decision"],
                "authorship": manifest.get("authorship", "not_recorded"),
                "expert_reviewed": classification["expert_reviewed"],
                "missing_coverage": classification["missing_coverage"],
            }.items():
                if key in provenance and canonical(provenance[key]) != canonical(value):
                    raise ValueError("Stored chunk review metadata mismatch")
            parts.append(content)
            offset = end
        text = "".join(parts)
        if hashlib.sha256(text.encode()).hexdigest() != manifest.get("sha256") or (
            "text" in manifest and manifest["text"] != text
        ):
            raise ValueError("Source content hash mismatch")
    return {
        "status": "binding_verified",
        "manifest_sha256": digest,
        "chunks": count,
        "review_status": classification["review_status"],
        "scope": scope,
    }


def inventory(config_root: Path) -> dict[str, Any]:
    """Packaged source inventory; free-text legacy status never proves expertise."""
    seed = json.loads((config_root / "strategy_source_seed.json").read_text())
    sources = seed["sources"] + [
        json.loads((config_root / name).read_text())
        for name in (
            "crystal_storm_user_primer.json",
            "wiki_deck_archetypes_20261020.json",
            "crystal_storm_source_review.json",
        )
    ]
    return {
        "sources": len(sources),
        "source_kinds": dict(Counter(s["source_kind"] for s in sources)),
        "author_declarations": dict(
            Counter(s.get("author", "not_recorded") for s in sources)
        ),
        "declared_review_statuses": dict(
            Counter(s.get("review_status", "not_recorded") for s in sources)
        ),
        "verified_expert_strategy_sources": 0,
        "missing_coverage": ["signed independent human expert strategy reviews"],
        "limitations": "Packaged inventory only. Official rules are mechanical evidence, not expert strategic review. Legacy declarations are unverified; inspect signed review receipts separately.",
    }
