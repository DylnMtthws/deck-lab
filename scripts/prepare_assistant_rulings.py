"""Import an explicit Scryfall bulk rulings snapshot offline."""

import argparse
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from sabermetrics.assistant_rulings_import import BulkDescriptor, import_rulings

MAX_DESCRIPTOR_BYTES = 64 * 1024


def main(argv: list[str] | None = None) -> int:
    """Validate and atomically replace the scryfall-bulk rulings without downloads.

    ``--sha256`` is the value ``shasum -a 256`` reports for the downloaded file.
    ``--descriptor`` is the saved ``/bulk-data`` object; when given, the source
    URL, file size and URL timestamp must agree with it. A rejected file leaves
    the previously imported rulings in place.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--rulings-file", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256", required=True, help="Expected SHA-256 of the file")
    parser.add_argument("--descriptor", type=Path, help="Saved bulk-data JSON object")
    args = parser.parse_args(argv)
    try:
        descriptor = None
        if args.descriptor is not None:
            raw = args.descriptor.read_bytes()
            if len(raw) > MAX_DESCRIPTOR_BYTES:
                raise ValueError("Descriptor exceeds the size bound")
            descriptor = BulkDescriptor.model_validate_json(raw)
        result = import_rulings(
            args.db_path,
            args.rulings_file,
            expected_sha256=args.sha256,
            source_url=args.source_url,
            version=args.version,
            descriptor=descriptor,
        )
    except ValidationError as exc:
        print(
            f"rulings import rejected; previous rulings kept: invalid descriptor: {exc.errors()[0]['msg']}",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"rulings import rejected; previous rulings kept: {exc}", file=sys.stderr)
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
