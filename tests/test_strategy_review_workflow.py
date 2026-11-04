"""Owned offline checks; keys are ephemeral test identities, never expert receipts."""

import copy
import hashlib
import json
import shutil
import sqlite3
import subprocess

import pytest

from sabermetrics.reference_layer.strategy_corpus import ingest_text
from sabermetrics.strategy_review_workflow import (
    NAMESPACE,
    assess_review,
    audit_receipt,
    canonical,
    import_reviewed,
    manifest_digest,
)


@pytest.fixture
def source(tmp_path, request):
    text = "Original test-only strategy text, not a human-authored source."
    file_backed = getattr(request, "param", "inline") == "file"
    if file_backed:
        text *= 80
    manifest = {
        "source_key": "test-source",
        "version": "v1",
        "document": "Test guidance",
        "source_url": None,
        "source_kind": "authored_guidance",
        "author": "Test AI",
        "authorship": "ai",
        "text": text,
        "permission": "Original test fixture",
        "permission_basis": "original_authored_work",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "section": "test section",
    }
    if file_backed:
        manifest.pop("text")
        manifest["content_file"] = "source.txt"
        (tmp_path / "source.txt").write_text(text)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


@pytest.fixture
def signed(source, tmp_path):
    if not shutil.which("ssh-keygen"):
        pytest.skip("SSH signature verification requires ssh-keygen")
    key = tmp_path / "test-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    payload = {
        "reviewer_id": "fixture",
        "reviewed_at": "2026-10-21",
        "scope": "nonland_mana",
        "decision": "approved",
        "manifest_sha256": manifest_digest(source[1]),
    }
    message = tmp_path / "review"
    message.write_bytes(canonical(payload))
    subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(message)],
        capture_output=True,
        check=True,
    )
    review = {"payload": payload, "signature": message.with_suffix(".sig").read_text()}
    trust = {
        "fixture": {
            "principal": "fixture",
            "public_key": key.with_suffix(".pub").read_text().strip(),
            "actor_kind": "human",
            "expert_scopes": ["nonland_mana"],
            "expertise_basis": "TEST IDENTITY ONLY",
        }
    }
    return review, trust


def test_unsigned_claimed_expertise_stays_pending(source):
    path, manifest = source
    manifest["review_status"] = "expert_reviewed"
    result = assess_review(manifest, path.parent, None, {})
    assert result["review_status"] == "pending"
    assert not result["expert_reviewed"]
    assert result["missing_coverage"]


def test_approval_binds_permission_and_exact_version(source, signed):
    path, manifest = source
    review, trusted = signed
    assert assess_review(manifest, path.parent, review, trusted)["expert_reviewed"]
    for field in ("permission", "version", "author", "source_key"):
        changed = {**manifest, field: "changed"}
        with pytest.raises(ValueError, match="exact source"):
            assess_review(changed, path.parent, review, trusted)
    forged = copy.deepcopy(review)
    forged["payload"]["decision"] = "rejected"
    assert (
        assess_review(manifest, path.parent, forged, trusted)["review_status"]
        == "unverified_signature_or_identity"
    )
    assert (
        assess_review(manifest, path.parent, review, {})["review_status"]
        == "unverified_signature_or_identity"
    )


def test_ai_review_never_becomes_human_or_expert(source, signed, tmp_path):
    path, manifest = source
    review, trusted = signed
    trusted["fixture"]["actor_kind"] = "ai"
    result = assess_review(manifest, path.parent, review, trusted)
    assert result["review_status"] == "ai_reviewed"
    assert not result["expert_reviewed"]
    with pytest.raises(ValueError, match="approved human"):
        import_reviewed(path, tmp_path / "db", review, trusted)


def test_human_without_scoped_expertise_and_persisted_receipt(source, signed, tmp_path):
    path, manifest = source
    review, trusted = signed
    trusted["fixture"]["expert_scopes"] = ["other_strategy"]
    db = tmp_path / "db"
    result = import_reviewed(path, db, review, trusted)
    assert result["review_status"] == "human_reviewed"
    assert not result["expert_reviewed"]
    with sqlite3.connect(db) as conn:
        raw = conn.execute(
            "SELECT receipt_json FROM strategy_signed_reviews"
        ).fetchone()[0]
        assert json.loads(raw)["review"] == review
        assert canonical(json.loads(raw)["source_manifest"]) == canonical(manifest)
        provenance = json.loads(
            conn.execute(
                "SELECT provenance_json FROM reference_chunk_metadata"
            ).fetchone()[0]
        )
        assert provenance["review_manifest_sha256"] == manifest_digest(manifest)
        assert provenance["review_status"] == "human_reviewed"


def test_no_permission_or_mutated_content_enters_queue(source):
    path, manifest = source
    for changes in (
        {"permission_basis": "public_website"},
        {"text": "changed"},
        {"permission_basis": "open_license"},
    ):
        with pytest.raises(ValueError):
            assess_review({**manifest, **changes}, path.parent, None, {})


def test_review_never_promotes_another_section_with_same_lineage(
    source, signed, tmp_path
):
    path, manifest = source
    review, trusted = signed
    db = tmp_path / "sections.db"
    ingest_text(
        db,
        "Unreviewed second section.",
        document=manifest["document"],
        source_url=None,
        source_key=manifest["source_key"],
        version=manifest["version"],
        section="other",
        permission="Original fixture",
        review_status="pending",
    )
    import_reviewed(path, db, review, trusted)
    import_reviewed(path, db, review, trusted)
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT r.section, m.provenance_json FROM reference_chunks r JOIN reference_chunk_metadata m ON r.id=m.chunk_id"
        ).fetchall()
        assert {section: json.loads(raw)["review_status"] for section, raw in rows} == {
            "other": "pending",
            manifest["section"]: "expert_reviewed",
        }
        assert (
            conn.execute("SELECT COUNT(*) FROM strategy_signed_reviews").fetchone()[0]
            == 1
        )


def test_changes_requested_review_cannot_import(source, signed, tmp_path):
    path, _manifest = source
    review, trusted = signed
    changed = copy.deepcopy(review)
    changed["payload"]["decision"] = "changes_requested"
    with pytest.raises(ValueError, match="approved human"):
        import_reviewed(path, tmp_path / "db", changed, trusted)


def test_receipt_write_failure_leaves_explicit_pending_status(source, signed, tmp_path):
    path, _manifest = source
    review, trusted = signed
    db = tmp_path / "failed-receipt.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE strategy_signed_reviews (receipt_sha256 TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TRIGGER reject_receipt BEFORE INSERT ON strategy_signed_reviews BEGIN SELECT RAISE(ABORT, 'test write failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
        import_reviewed(path, db, review, trusted)
    with sqlite3.connect(db) as conn:
        provenance = json.loads(
            conn.execute(
                "SELECT provenance_json FROM reference_chunk_metadata"
            ).fetchone()[0]
        )
        assert provenance["review_status"] == "signed_review_pending_receipt"
        assert (
            conn.execute("SELECT COUNT(*) FROM strategy_signed_reviews").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"expertise_basis": " \t\n"},
        {"expertise_basis": True},
        {"expert_scopes": "prefix_nonland_mana_suffix"},
        {"expert_scopes": {"nonland_mana": True}},
        {"expert_scopes": [" "]},
        {"expert_scopes": [1]},
        {"actor_kind": "unknown"},
    ],
)
def test_malformed_trusted_expertise_fails_closed(source, signed, changes):
    path, manifest = source
    review, trusted = signed
    trusted["fixture"].update(changes)
    with pytest.raises(ValueError):
        assess_review(manifest, path.parent, review, trusted)


def test_scope_matching_is_exact_and_plain_human_needs_no_expertise(source, signed):
    path, manifest = source
    review, trusted = signed
    trusted["fixture"]["expert_scopes"] = ["prefix_nonland_mana_suffix"]
    assert not assess_review(manifest, path.parent, review, trusted)["expert_reviewed"]
    trusted["fixture"].pop("expert_scopes")
    trusted["fixture"].pop("expertise_basis")
    assert (
        assess_review(manifest, path.parent, review, trusted)["review_status"]
        == "human_reviewed"
    )


def imported_receipt(source, signed, tmp_path):
    path, _ = source
    review, trusted = signed
    db = tmp_path / "audit.db"
    import_reviewed(path, db, review, trusted)
    with sqlite3.connect(db) as conn:
        digest = conn.execute(
            "SELECT receipt_sha256 FROM strategy_signed_reviews"
        ).fetchone()[0]
    return db, digest, trusted


@pytest.mark.parametrize("source", ["inline", "file"], indirect=True)
def test_audit_is_offline_readonly_and_needs_no_source_files(source, signed, tmp_path):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    source[0].unlink()
    if "content_file" in source[1]:
        (tmp_path / source[1]["content_file"]).unlink()
    before = db.read_bytes()
    result = audit_receipt(db, digest, trusted)
    assert result["status"] == "binding_verified"
    assert result["manifest_sha256"] == manifest_digest(source[1])
    assert result["chunks"] == (3 if "content_file" in source[1] else 1)
    assert "expert_reviewed" not in result
    assert db.read_bytes() == before
    with pytest.raises(ValueError, match="signature or identity"):
        audit_receipt(db, digest, {})


@pytest.mark.parametrize(
    "mutation",
    [
        "content",
        "offset",
        "missing",
        "binding",
        "source_hash",
        "manifest",
        "signature",
        "receipt",
        "budget",
    ],
)
def test_audit_rejects_tampering(source, signed, tmp_path, mutation):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    with sqlite3.connect(db) as conn:
        if mutation == "content":
            conn.execute("UPDATE reference_chunks SET content='tampered'")
        elif mutation == "offset":
            conn.execute("UPDATE reference_chunk_metadata SET char_start=1")
        elif mutation == "missing":
            conn.execute("DELETE FROM reference_chunks")
        elif mutation == "binding":
            conn.execute("UPDATE reference_chunk_metadata SET provenance_json='{}'")
        elif mutation == "budget":
            conn.execute("UPDATE reference_chunks SET content=?", ("x" * 1_000_001,))
        else:
            raw = conn.execute(
                "SELECT receipt_json FROM strategy_signed_reviews"
            ).fetchone()[0]
            receipt = json.loads(raw)
            if mutation == "source_hash":
                # Keep chunk hashes/offsets self-consistent; signed full-source hash still rejects.
                content = "x" * len(source[1]["text"])
                conn.execute("UPDATE reference_chunks SET content=?", (content,))
                conn.execute(
                    "UPDATE reference_chunk_metadata SET content_hash=?",
                    (hashlib.sha256(content.encode()).hexdigest(),),
                )
            elif mutation == "manifest":
                receipt["source_manifest"]["permission"] = "tampered"
            elif mutation == "signature":
                receipt["review"]["payload"]["decision"] = "rejected"
            else:
                conn.execute("UPDATE strategy_signed_reviews SET receipt_json='{}'")
            if mutation in {"manifest", "signature"}:
                raw = canonical(receipt).decode()
                new_digest = hashlib.sha256(raw.encode()).hexdigest()
                conn.execute(
                    "UPDATE strategy_signed_reviews SET receipt_json=?, receipt_sha256=?",
                    (raw, new_digest),
                )
                digest = new_digest
    with pytest.raises(ValueError):
        audit_receipt(db, digest, trusted)


def test_legacy_receipt_is_explicitly_unauditable(source, signed, tmp_path):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    with sqlite3.connect(db) as conn:
        receipt = json.loads(
            conn.execute("SELECT receipt_json FROM strategy_signed_reviews").fetchone()[
                0
            ]
        )
        receipt.pop("source_manifest")
        raw = canonical(receipt).decode()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        conn.execute(
            "UPDATE strategy_signed_reviews SET receipt_json=?, receipt_sha256=?",
            (raw, digest),
        )
    assert audit_receipt(db, digest, trusted)["status"] == "legacy_unauditable"


@pytest.mark.parametrize("source", ["file"], indirect=True)
@pytest.mark.parametrize(
    "table,field,value",
    [
        ("reference_chunks", "document", "Different document"),
        ("reference_chunks", "section", "other"),
        ("reference_chunks", "tier", 1),
        ("reference_chunk_metadata", "source_key", "other-source"),
        ("reference_chunk_metadata", "source_url", "https://example.test/other"),
        ("reference_chunk_metadata", "version", "v2"),
        ("reference_chunk_metadata", "permission", "Different permission"),
        ("reference_chunk_metadata", "source_kind", "official_rules"),
        ("reference_chunk_metadata", "commander", "Other commander"),
        ("reference_chunk_metadata", "archetype", "Other archetype"),
        ("reference_chunk_metadata", "chunk_id", "unbound-chunk"),
    ],
)
def test_audit_binds_every_chunk_source_metadata(
    source, signed, tmp_path, table, field, value
):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    with sqlite3.connect(db) as conn:
        chunk_id = conn.execute(
            "SELECT chunk_id FROM reference_chunk_metadata ORDER BY char_start DESC LIMIT 1"
        ).fetchone()[0]
        identifier = "id" if table == "reference_chunks" else "chunk_id"
        conn.execute(
            f"UPDATE {table} SET {field}=? WHERE {identifier}=?", (value, chunk_id)
        )
    with pytest.raises(ValueError, match="metadata|content or offsets"):
        audit_receipt(db, digest, trusted)


@pytest.mark.parametrize(
    "field,value",
    [
        ("author", "Different author"),
        ("source_date", "2026-01-01"),
        ("source_class", "official_rules"),
        ("primary_links", [{"url": "https://example.test"}]),
        ("limitations", "Different limitations"),
        ("permission_basis", "author_permission"),
        ("license_url", "https://example.test/license"),
        ("attribution_url", "https://example.test/author"),
        ("reviewer_id", "another-reviewer"),
        ("review_scope", "other_strategy"),
        ("review_status", "human_reviewed"),
        ("reviewed_at", "2025-01-01"),
        ("decision", "rejected"),
        ("authorship", "human"),
        ("expert_reviewed", False),
        ("missing_coverage", ["expert_strategy_review"]),
    ],
)
def test_audit_binds_source_and_review_provenance(
    source, signed, tmp_path, field, value
):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    with sqlite3.connect(db) as conn:
        raw = conn.execute(
            "SELECT provenance_json FROM reference_chunk_metadata"
        ).fetchone()[0]
        provenance = json.loads(raw)
        provenance[field] = value
        conn.execute(
            "UPDATE reference_chunk_metadata SET provenance_json=?",
            (json.dumps(provenance),),
        )
    with pytest.raises(ValueError, match="metadata mismatch"):
        audit_receipt(db, digest, trusted)


@pytest.mark.parametrize(
    "changes",
    [
        {"review_status": "expert_reviewed"},
        {"expert_reviewed": True},
        {"missing_coverage": []},
    ],
)
def test_audit_rejects_plain_human_promotion(source, signed, tmp_path, changes):
    signed[1]["fixture"].pop("expert_scopes")
    signed[1]["fixture"].pop("expertise_basis")
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    assert audit_receipt(db, digest, trusted)["review_status"] == "human_reviewed"
    with sqlite3.connect(db) as conn:
        provenance = json.loads(
            conn.execute(
                "SELECT provenance_json FROM reference_chunk_metadata"
            ).fetchone()[0]
        )
        provenance.update(changes)
        conn.execute(
            "UPDATE reference_chunk_metadata SET provenance_json=?",
            (json.dumps(provenance),),
        )
    with pytest.raises(ValueError, match="metadata mismatch"):
        audit_receipt(db, digest, trusted)


def test_audit_current_registry_overrides_receipt_expertise(source, signed, tmp_path):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    # The persisted assessment/identity still claim scoped expertise. The operator's
    # current exact scope is authoritative, including substring-only mismatches.
    trusted["fixture"]["expert_scopes"] = ["prefix_nonland_mana_suffix"]
    with pytest.raises(ValueError, match="provenance metadata mismatch"):
        audit_receipt(db, digest, trusted)
    with sqlite3.connect(db) as conn:
        provenance = json.loads(
            conn.execute(
                "SELECT provenance_json FROM reference_chunk_metadata"
            ).fetchone()[0]
        )
        provenance["review_status"] = "human_reviewed"
        conn.execute(
            "UPDATE reference_chunk_metadata SET provenance_json=?",
            (json.dumps(provenance),),
        )
    assert audit_receipt(db, digest, trusted)["review_status"] == "human_reviewed"


def test_unknown_receipt_fails_explicitly(source, signed, tmp_path):
    db, _digest, trusted = imported_receipt(source, signed, tmp_path)
    with pytest.raises(ValueError, match="Unknown signed receipt"):
        audit_receipt(db, "unknown", trusted)


@pytest.mark.parametrize("provenance", [None, [], "human_reviewed"])
def test_audit_rejects_nonobject_provenance(source, signed, tmp_path, provenance):
    db, digest, trusted = imported_receipt(source, signed, tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE reference_chunk_metadata SET provenance_json=?",
            (json.dumps(provenance),),
        )
    with pytest.raises(ValueError, match="receipt binding mismatch"):
        audit_receipt(db, digest, trusted)
