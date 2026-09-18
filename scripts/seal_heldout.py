"""Seal a held-out question file by hash, or verify a published one.

    python scripts/seal_heldout.py seal --file /path/outside/repo/rules_heldout.yaml \\
        --name rules-heldout --sealed-by "Dylan Matthews" \\
        --published-path fixtures/research/heldout/rules_heldout.yaml

    python scripts/seal_heldout.py verify --file fixtures/research/heldout/rules_heldout.yaml

``seal`` writes fixtures/research/heldout_commitment.yaml carrying the file's
sha256, its question count, the pinned document's content hash and the active
rules index generation — and nothing from the file itself. Commit that, run G2
against the file with ``--questions``/``--rules-support-labels``, then copy the
file to its published path and run ``verify``.

The seal proves the set predates the run. It cannot prove independence; that
is a property of who wrote it and what they had read.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sabermetrics.assistant.eval.heldout import (
    DEFAULT_COMMITMENT,
    HeldoutCommitment,
    SealError,
    count_questions,
    file_sha256,
    load_commitment,
    verify_commitment,
)

RULES_MANIFEST = ROOT / "fixtures" / "research" / "rules_index.json"


def _manifest() -> dict:
    if not RULES_MANIFEST.is_file():
        raise SealError(
            f"{RULES_MANIFEST} is missing; a held-out set must be sealed against "
            "a built index"
        )
    return dict(json.loads(RULES_MANIFEST.read_text(encoding="utf-8")))


def _seal(args: argparse.Namespace) -> int:
    if DEFAULT_COMMITMENT.is_file() and not args.force:
        raise SealError(
            f"{DEFAULT_COMMITMENT} already exists. A seal is made once; pass "
            "--force to replace it and say why in the commit"
        )
    manifest = _manifest()
    commitment = HeldoutCommitment(
        name=args.name,
        sealed_on=date.today(),
        file_sha256=file_sha256(args.file),
        question_count=count_questions(args.file),
        document_content_sha256=str(manifest["source"]["content_sha256"]),
        rules_index_generation_id=str(manifest["generation_id"]),
        published_path=args.published_path,
        sealed_by=args.sealed_by,
    )
    DEFAULT_COMMITMENT.write_text(
        yaml.safe_dump(
            commitment.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )
    print(
        f"sealed {commitment.question_count} questions as {commitment.name}: "
        f"{commitment.file_sha256[:12]} -> {DEFAULT_COMMITMENT}"
    )
    print("commit the commitment file BEFORE running anything against the set")
    return 0


def _verify(args: argparse.Namespace) -> int:
    commitment = load_commitment()
    if commitment is None:
        raise SealError(f"nothing is sealed: {DEFAULT_COMMITMENT} does not exist")
    problems = verify_commitment(commitment, args.file)
    manifest = _manifest()
    if str(manifest["generation_id"]) != commitment.rules_index_generation_id:
        problems.append(
            "the active rules index generation is not the one sealed against: "
            f"sealed {commitment.rules_index_generation_id[:12]}, active "
            f"{str(manifest['generation_id'])[:12]}. A run now is a different "
            "measurement from the one the seal was made for"
        )
    if problems:
        print("SEAL DOES NOT HOLD:\n  - " + "\n  - ".join(problems))
        return 1
    print(
        f"seal holds: {args.file} is the {commitment.question_count}-question "
        f"file sealed on {commitment.sealed_on} by {commitment.sealed_by}"
    )
    return 0


def main() -> int:
    """Seal or verify."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--file", type=Path, required=True)
    seal.add_argument("--name", required=True)
    seal.add_argument("--sealed-by", required=True)
    seal.add_argument("--published-path", required=True)
    seal.add_argument("--force", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    try:
        return _seal(args) if args.command == "seal" else _verify(args)
    except SealError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
