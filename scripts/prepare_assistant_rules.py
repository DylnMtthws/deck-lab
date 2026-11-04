"""Import an explicit Wizards Comprehensive Rules text snapshot offline."""

import argparse
import json
import sys
from pathlib import Path

from sabermetrics.assistant_rules import ingest_rules


def main(argv: list[str] | None = None) -> int:
    """Validate and atomically publish a local rules index without downloads.

    The file is hashed as raw bytes, so ``--sha256`` is the value ``shasum -a
    256`` reports for the downloaded file. A rejected file leaves the previously
    imported snapshot in place.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--sha256", required=True, help="Expected SHA-256 of the text file"
    )
    args = parser.parse_args(argv)
    try:
        metadata = ingest_rules(
            args.db_path,
            args.text_file.read_bytes(),
            source_url=args.source_url,
            version=args.version,
            expected_sha256=args.sha256,
        )
    except (OSError, ValueError) as exc:
        print(f"rules import rejected; previous snapshot kept: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(metadata))
    return 0


if __name__ == "__main__":
    sys.exit(main())
