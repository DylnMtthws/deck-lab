"""Explicitly seed a chosen QA/local database with packaged attributable evidence."""

import argparse
import json
from pathlib import Path

from sabermetrics.reference_layer.strategy_corpus import ingest_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--embed", action="store_true")
    args = parser.parse_args()
    # config_data may be a namespace package; resolve from main package instead.
    import sabermetrics

    bundle = json.loads(
        (
            Path(sabermetrics.__file__).parent / "config_data/strategy_source_seed.json"
        ).read_text()
    )
    count = sum(
        ingest_text(args.db_path, embed=args.embed, **source)
        for source in bundle["sources"]
    )
    from sabermetrics.assistant_source_import import import_source_manifest

    source_root = Path(sabermetrics.__file__).parent / "config_data"
    added = [
        import_source_manifest(source_root / name, args.db_path, embed=args.embed)
        for name in (
            "crystal_storm_user_primer.json",
            "wiki_deck_archetypes_20261020.json",
            "crystal_storm_source_review.json",
        )
    ]
    print(
        json.dumps(
            {
                "chunks_ingested": count + sum(r["chunks"] for r in added),
                "permissioned_sources": added,
                "limitations": bundle["limitations"],
            }
        )
    )


if __name__ == "__main__":
    main()
