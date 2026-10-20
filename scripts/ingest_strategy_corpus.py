"""Ingest licensed/user-supplied UTF-8 text with explicit source provenance.

Example: python scripts/ingest_strategy_corpus.py --db-path data/test.db
  --text-file primer.txt --document 'Primer title' --source-url https://...
  --version 2026-10 --permission 'Author supplied for this project'
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sabermetrics.reference_layer.strategy_corpus import ingest_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--document", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--version", required=True)
    parser.add_argument("--permission", required=True)
    parser.add_argument(
        "--source-kind",
        choices=[
            "user_supplied",
            "licensed_strategy",
            "official_rules",
            "strategy_excerpt",
            "authored_guidance",
            "wiki_secondary",
        ],
        default="user_supplied",
    )
    parser.add_argument("--author", default="")
    parser.add_argument("--source-date", default="")
    parser.add_argument("--source-class", default="")
    parser.add_argument("--limitations", default="")
    parser.add_argument("--section", default="")
    parser.add_argument(
        "--source-key",
        default="",
        help="Stable lineage across changed source URLs/titles; defaults to document and section",
    )
    parser.add_argument(
        "--archive",
        dest="activate",
        action="store_false",
        help="Retain historical snapshot without retrieving it",
    )
    parser.add_argument("--commander", default="")
    parser.add_argument("--archetype", default="")
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Use configured local ONNX or sentence-transformer; never download",
    )
    args = vars(parser.parse_args())
    text = args.pop("text_file").read_text(encoding="utf-8")
    print(json.dumps({"chunks_ingested": ingest_text(text=text, **args)}))


if __name__ == "__main__":
    main()
