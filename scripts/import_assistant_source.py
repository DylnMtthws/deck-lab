"""Import an approved local primer/wiki manifest; never fetch during chat."""

import argparse
import json
from pathlib import Path

from sabermetrics.assistant_source_import import import_source_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--embed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(import_source_manifest(args.manifest, args.db, embed=args.embed)))


if __name__ == "__main__":
    main()
