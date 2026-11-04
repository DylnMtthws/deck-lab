"""Source permissions and review claims remain provenance, never game facts."""

import hashlib
import json
from pathlib import Path

import pytest

from sabermetrics.assistant_source_import import (
    import_source_manifest,
    source_arguments,
)


def test_packaged_user_primer_and_revisioned_wiki_have_content_bound_permissions(
    tmp_path,
):
    root = Path(__file__).parents[1] / "src/sabermetrics/config_data"
    for name in (
        "crystal_storm_user_primer.json",
        "wiki_deck_archetypes_20261020.json",
    ):
        manifest = json.loads((root / name).read_text())
        args = source_arguments(manifest, root)
        assert hashlib.sha256(args["text"].encode()).hexdigest() == manifest["sha256"]
        # Packaged manifests are sufficient without an adjacent text file.
        assert source_arguments(manifest, tmp_path)["text"] == args["text"]
        assert (
            "not_expert" in manifest["review_status"]
            or "unreviewed" in manifest["review_status"]
        )
        altered = {**manifest, "text": args["text"] + " altered"}
        with pytest.raises(ValueError):
            source_arguments(altered, tmp_path)
    wiki = source_arguments(
        json.loads((root / "wiki_deck_archetypes_20261020.json").read_text()), root
    )
    assert "oldid=" in wiki["source_url"] and wiki["source_kind"] == "wiki_secondary"
    receipt = import_source_manifest(
        root / "crystal_storm_user_primer.json", tmp_path / "corpus.db"
    )
    assert receipt["chunks"] >= 4
    assert receipt["permission_basis"] == "direct_user_supplied_authorization"
    assert "unreviewed" in receipt["review_status"]
    review = source_arguments(
        json.loads((root / "crystal_storm_source_review.json").read_text()), root
    )
    assert review["source_kind"] == "authored_guidance"
    assert review["review_status"] == "ai_source_review_not_expert_reviewed"
    assert review["primary_links"] and "not an expert" in review["text"]


def test_missing_permission_or_wiki_revision_cannot_be_silently_imported(tmp_path):
    text = "Community strategy guidance."
    manifest = {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    with pytest.raises(ValueError):
        source_arguments(manifest, tmp_path)
    manifest.update(
        permission_basis="open_license",
        license_url="https://example.test/license",
        source_kind="wiki_secondary",
    )
    with pytest.raises(ValueError):
        source_arguments(manifest, tmp_path)
    manifest.update(content_file="../outside.txt")
    with pytest.raises(ValueError):
        source_arguments(manifest, tmp_path)
