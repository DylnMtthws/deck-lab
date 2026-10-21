#!/usr/bin/env python3
"""Capture offline production rank components, never reference answers.

Example (use an existing environment with project dependencies):
  python scripts/capture_assistant_quality_selection.py --output current.json
  python scripts/capture_assistant_quality_selection.py --source eaeeda91 --output baseline.json

Outputs are receipt lists accepted by evaluate_assistant_quality.py --receipts.
This deliberately bypasses discovery's novelty exclusion for retention cases.
It is NOT a capture of the live model selector or the full functional-search rank:
only strategic_rank_key, suitability priority, and production renderers run.
Baseline revisions without obligations use suitability priority alone.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/evaluations/assistant-quality-20261020"
SCOPE = "offline_selection_only"
LIMITATIONS = [
    "Rank-component retention adapter; not full live assistant or model quality.",
    "No discovery retrieval, novelty filter, search goal gate, or model selection.",
    "Stable input order breaks equal rank keys; no fabricated pairwise judgment.",
    "No cuts, package verdicts, factual probe answers, or certified topic coverage.",
    "Rendered prose is unreviewed; ingredient detection does not prove synergy.",
    "No live tokens; zero cost is an offline model, not measured API billing.",
]


def public_cases(split: str, corpus: Path = CORPUS) -> list[dict]:
    """Read only public inputs, verifying their bytes against the frozen manifest.

    Unlike benchmark verify_freeze, this does not even hash label files. The
    scorer remains responsible for verifying the complete benchmark freeze.
    """
    if split not in {"development", "heldout"}:
        raise ValueError("Unknown split")
    manifest = json.loads((corpus / "manifest.json").read_text())

    def read(name):
        raw = (corpus / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["files_sha256"][name]:
            raise ValueError(f"Public input changed: {name}")
        return json.loads(raw)

    cases = read(f"{split}.cases.json")
    evidence = {row["id"]: row for row in read(f"{split}.evidence.json")}
    rules = read("rules.evidence.json")
    evidence[rules["id"]] = rules
    for case in cases:
        case["evidence"] = [evidence[key] for key in case["evidence_ids"]]
    return cases


def code_freeze(source_root: Path) -> dict:
    """Hash source/config bytes and this adapter, including uncommitted changes."""
    files = {"capture_adapter": Path(__file__).resolve()}
    files.update(
        (p.relative_to(source_root).as_posix(), p)
        for p in (source_root / "src").rglob("*")
        if p.is_file() and p.suffix in {".py", ".json", ".txt"}
    )
    hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sorted(files.items())
    }
    digest = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "sha256": digest,
        "algorithm": "sha256 of sorted compact JSON path-to-sha256 map",
        "scope": "adapter plus all src .py/.json/.txt bytes; not a full environment lock",
        "files_sha256": hashes,
    }


def document_from_case(case: dict) -> tuple[dict, list[dict]]:
    context = case["context"]
    if context.get("recommendation_mode") != "prioritize_or_retain_from_current_shell":
        raise ValueError("Only current-shell retention cases are supported")
    pool = context["candidate_pool"]
    if len(pool) != len(set(pool)) or not set(pool).issubset(context["deck"]):
        raise ValueError("Candidates must be unique members of the current shell")
    evidence = {
        row["name"]: row
        for row in case["evidence"]
        if row.get("id", "").startswith("card:") and "name" in row
    }
    entries = []
    for name in context["deck"]:
        row = evidence.get(name)
        if not row or not row.get("oracle_text"):
            raise ValueError(f"Missing public card evidence: {name}")
        entry = {
            k: row[k]
            for k in ("name", "oracle_id", "oracle_text", "mana_cost", "type_line")
            if k in row
        }
        entry.update(id=row["oracle_id"], quantity=1, zone_name="Library")
        # Legendary is not proof of command-zone placement. None is inferred.
        entries.append(entry)
    by_name = {entry["name"]: entry for entry in entries}
    return {"revision": 0, "entries": entries}, [by_name[n] for n in pool]


def capture_case(case: dict, *, obligations: bool, metadata: dict) -> dict:
    from sabermetrics.assistant_suitability import (
        candidate_suitability,
        deck_support_index,
        render_fit_reason,
    )

    start = time.perf_counter()
    document, candidates = document_from_case(case)
    index = deck_support_index(document)
    ranked = []
    for card in candidates:
        profile = candidate_suitability(
            card, index, case["prompt"], include_packet=False
        )
        key = []
        contract = None
        reason = render_fit_reason(profile)
        if obligations:
            from sabermetrics.assistant_strategic_obligations import (
                build_strategic_obligations,
                render_strategic_obligations,
                strategic_rank_key,
            )

            contract = build_strategic_obligations(
                card,
                case["prompt"],
                suitability=profile,
                deck_entries=document["entries"],
            )
            key.extend(strategic_rank_key(contract))
            reason += " " + render_strategic_obligations(contract)
        key.append(-profile["strategic_judgment"]["priority"])
        ranked.append(
            {
                "card": card["name"],
                "rationale": reason,
                "rank_key": key,
                "suitability": profile,
                "strategic_obligations": contract.model_dump() if contract else None,
            }
        )
    ranked.sort(key=lambda row: row["rank_key"])
    count = case["requested"]["recommendations"]
    return {
        "case_id": case["id"],
        "status": "partial",
        "scope": SCOPE,
        "candidates": [c["name"] for c in candidates],
        "recommendations": [
            {"card": row["card"], "rationale": row["rationale"]}
            for row in ranked[:count]
        ],
        "ranked_candidates": ranked,
        "capture_metadata": metadata,
        "limitations": LIMITATIONS,
        "missing_dimensions": ["cuts", "packages", "claims", "addressed_topics"],
        "telemetry": {
            "latency_ms": (time.perf_counter() - start) * 1000,
            "cost_usd": 0,
            "input_tokens": None,
            "output_tokens": None,
        },
        "instrumentation": {
            "latency_scope": "document construction, rank components, renderers; excludes process/import/archive startup",
            "cost_basis": "modeled_zero_offline_only",
            "tokens_basis": "not_applicable_no_model_invoked",
        },
    }


def _deny_network(*args, **kwargs):
    raise RuntimeError("Network disabled for offline selection capture")


def capture_in_process(cases: list[dict], source_root: Path, source: str) -> list[dict]:
    # Worker runs with -I, so an editable install/PYTHONPATH cannot shadow source.
    sys.path.insert(0, str(source_root / "src"))
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    obligations = (
        source_root / "src/sabermetrics/assistant_strategic_obligations.py"
    ).exists()
    before = code_freeze(source_root)
    metadata = {
        "source": source,
        "code_freeze": before,
        "ranking": (
            "strategic_rank_key_then_negative_suitability_priority"
            if obligations
            else "baseline_negative_suitability_priority_only"
        ),
        "obligation_renderer_available": obligations,
        "packet_mode": "disabled; no prospective addition/cut packet for retention",
        "commander_policy": "not inferred from legendary type or card name",
    }
    receipts = [
        capture_case(case, obligations=obligations, metadata=metadata) for case in cases
    ]
    if code_freeze(source_root) != before:
        raise RuntimeError(
            "Source changed during capture; discard and recapture after freeze"
        )
    return receipts


def capture(
    cases: list[dict], source: str = "current", final_code_freeze: str | None = None
) -> list[dict]:
    def worker(root, resolved):
        freeze = code_freeze(root)["sha256"]
        if final_code_freeze is not None and final_code_freeze != freeze:
            raise ValueError(
                f"Freeze mismatch: expected current adapter/source hash {freeze}"
            )
        payload = {"cases": cases, "root": str(root), "source": resolved}
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return json.loads(result.stdout)

    if source == "current":
        return worker(ROOT, "current_worktree")
    resolved = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{source}^{{commit}}"], cwd=ROOT, text=True
    ).strip()
    archive = subprocess.check_output(["git", "archive", resolved, "src"], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="selection-capture-") as directory:
        root = Path(directory).resolve()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # Extract regular files only; never follow archived symlinks.
            for member in tar.getmembers():
                path = root / member.name
                if not path.resolve().is_relative_to(root) or not member.isfile():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as stream:
                    path.write_bytes(stream.read())
        return worker(root, resolved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="current",
        help="current or archived Git revision (e.g. eaeeda91)",
    )
    parser.add_argument(
        "--split", choices=["development", "heldout"], default="development"
    )
    parser.add_argument(
        "--final-code-freeze",
        help="Required for heldout; must match adapter/source hash",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-current-freeze", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        payload = json.load(sys.stdin)
        print(
            json.dumps(
                capture_in_process(
                    payload["cases"], Path(payload["root"]), payload["source"]
                )
            )
        )
        return
    if args.print_current_freeze:
        print(code_freeze(ROOT)["sha256"])
        return
    if args.split == "heldout" and not args.final_code_freeze:
        parser.error(
            "Heldout capture requires --final-code-freeze before loading inputs"
        )
    if args.output is None:
        parser.error("--output is required")
    receipts = capture(public_cases(args.split), args.source, args.final_code_freeze)
    args.output.write_text(json.dumps(receipts, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
