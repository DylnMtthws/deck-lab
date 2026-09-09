"""Map synthetic fixture IDs in draft G1 labels to canonical Oracle IDs.

The R0 cEDH fixture intentionally uses UUID5 identifiers, which are suitable
for isolated tests but not for full-corpus retrieval evaluation. This command
joins fixture ID -> printed card name -> full corpus Oracle ID, rewrites only
draft labels, and emits a review artifact naming every mechanical replacement.
It never marks a label owner-verified.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from sabermetrics.substrate.evaluation import load_retrieval_labels

ROOT = Path(__file__).resolve().parent.parent


def _read_cards(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("cards") or ())


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    """Canonicalize label IDs and write the explicit owner-review mapping."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        type=Path,
        default=ROOT / "fixtures" / "research" / "g1_labels.yaml",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "fixtures" / "cedh" / "cards.json",
    )
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument(
        "--review-output",
        type=Path,
        default=ROOT / "fixtures" / "research" / "g1_label_review.json",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    labels = load_retrieval_labels(args.labels)
    fixture_by_id = {
        str(card["oracle_id"]): str(card["name"]) for card in _read_cards(args.fixture)
    }
    corpus_cards = _read_cards(args.corpus)
    corpus_by_id = {str(card["oracle_id"]): str(card["name"]) for card in corpus_cards}
    ids_by_name: dict[str, set[str]] = {}
    for oracle_id, name in corpus_by_id.items():
        ids_by_name.setdefault(name.casefold(), set()).add(oracle_id)

    raw = labels.model_dump(mode="json")
    replacements: list[dict[str, str]] = []
    prior_replacements: dict[tuple[str, str, str], dict[str, str]] = {}
    if args.review_output.is_file():
        prior_review = json.loads(args.review_output.read_text(encoding="utf-8"))
        prior_replacements = {
            (
                row["question_id"],
                row["kind"],
                row["canonical_oracle_id"],
            ): row
            for row in prior_review.get("replacements", ())
        }
    unresolved: list[str] = []
    for label in raw["labels"]:
        if label["review_status"] != "draft":
            raise SystemExit(
                f"{label['question_id']} is not draft; refusing mechanical rewrite"
            )
        for field, kind in (
            ("required_oracle_ids", "required"),
            ("forbidden_oracle_ids", "forbidden"),
        ):
            canonical_ids: list[str] = []
            for old_id in label[field]:
                if old_id in corpus_by_id:
                    canonical_ids.append(old_id)
                    prior = prior_replacements.get((label["question_id"], kind, old_id))
                    if prior is not None:
                        replacements.append(prior)
                    continue
                name = fixture_by_id.get(old_id)
                matches = ids_by_name.get((name or "").casefold(), set())
                if name is None or len(matches) != 1:
                    unresolved.append(
                        f"{label['question_id']}:{kind}:{old_id}:{name!r}:"
                        f"{sorted(matches)}"
                    )
                    continue
                canonical_id = next(iter(matches))
                canonical_ids.append(canonical_id)
                replacements.append(
                    {
                        "question_id": label["question_id"],
                        "kind": kind,
                        "card_name": name,
                        "fixture_id": old_id,
                        "canonical_oracle_id": canonical_id,
                    }
                )
            label[field] = canonical_ids
    if unresolved:
        raise SystemExit("unresolved label mappings:\n" + "\n".join(unresolved))

    review = {
        "schema_version": "research-g1-label-review.v1",
        "mapping_source": str(args.corpus),
        "status": "awaiting_owner_review",
        "replacement_count": len(replacements),
        "replacements": replacements,
    }
    if args.write:
        _atomic_text(
            args.labels,
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        )
        _atomic_text(
            args.review_output,
            json.dumps(review, indent=2, sort_keys=True) + "\n",
        )
        # Re-load after writing so a serialization defect cannot become review data.
        load_retrieval_labels(args.labels)
    print(json.dumps(review, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
