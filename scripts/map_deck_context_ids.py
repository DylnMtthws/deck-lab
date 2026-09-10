"""Map the cEDH fixture's synthetic Oracle ids to canonical corpus ids.

The golden question set labels its answers with the fixture's uuid5 ids, which
are suitable for isolated tests and are not the ids a full corpus carries. This
command joins fixture id -> printed card name -> corpus Oracle id and writes the
bijection as an explicit review artefact.

It refuses on any name that does not resolve or that resolves to more than one
card: dropping one would silently shorten the deck and quietly change what a G2
run is measured against. It never marks the artefact reviewed.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sabermetrics.substrate.retrieval import CardRetrievalFacade
from sabermetrics.substrate.settings import load_research_settings

ROOT = Path(__file__).resolve().parent.parent
ID_MAP_SCHEMA = "research-deck-context-id-map.v1"
DEFAULT_FIXTURE = ROOT / "fixtures" / "cedh" / "cards.json"
DEFAULT_OUTPUT = ROOT / "fixtures" / "research" / "deck_context_id_map.json"


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON durably, so a killed run cannot leave a partial artefact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    """Resolve every fixture card name against the active bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--generated-on",
        default="",
        help="ISO date recorded as provenance. Passed in so the artefact is "
        "reproducible from identical inputs.",
    )
    args = parser.parse_args()

    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    cards = list(payload.get("cards") or ())
    if not cards:
        raise SystemExit(f"ID MAP REFUSED: no cards in {args.fixture}")

    settings = load_research_settings(args.config)
    with CardRetrievalFacade(settings) as facade:
        manifest = facade.manifest
        names = [str(card["name"]) for card in cards]
        resolved = facade.resolve_names(names)

    unresolved = sorted(name for name in names if name not in resolved)
    ambiguous = sorted(name for name, ids in resolved.items() if len(ids) != 1)
    if unresolved or ambiguous:
        raise SystemExit(
            "ID MAP REFUSED: "
            + "; ".join(
                part
                for part in (
                    f"unresolved: {', '.join(unresolved)}" if unresolved else "",
                    f"ambiguous: {', '.join(ambiguous)}" if ambiguous else "",
                )
                if part
            )
        )

    entries = [
        {
            "name": str(card["name"]),
            "fixture_oracle_id": str(card["oracle_id"]),
            "canonical_oracle_id": resolved[str(card["name"])][0],
        }
        for card in cards
    ]
    canonical = {entry["canonical_oracle_id"] for entry in entries}
    if len(canonical) != len(entries):
        raise SystemExit("ID MAP REFUSED: two fixture cards resolve to one corpus card")

    _write_atomic(
        args.output,
        {
            "schema_version": ID_MAP_SCHEMA,
            "status": "awaiting_owner_review",
            "reviewer": None,
            "generated_on": args.generated_on,
            "mapping_source": str(args.fixture.relative_to(ROOT)),
            "bundle_id": manifest.bundle_id,
            "corpus_source_view": manifest.corpus.source_view,
            "corpus_row_count": manifest.corpus.row_count,
            "corpus_sha256": manifest.corpus.content_sha256,
            "entry_count": len(entries),
            "entries": sorted(entries, key=lambda entry: entry["name"]),
        },
    )
    print(f"wrote {len(entries)} mappings to {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ID MAP REFUSED: {exc}")
        raise SystemExit(2) from None
