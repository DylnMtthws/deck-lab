"""The R2 retrieval gate. Default mode includes authoritative full-corpus G1.

    python scripts/check_r2.py             # completion gate
    python scripts/check_r2.py --portable  # code/fixture checks, never G1
    python scripts/check_r2.py --verbose

Portable mode exists for machines without the two local models or mtg_v1
access. It can pass, but prints that R2 is not complete; only the default mode
can print ``R2 DONE``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BASELINE_PASSED = 1563
BASELINE_SKIPPED = 31
G1_OUTPUT = ROOT / ".research-dev" / "g1-scorecard.json"
RETRIEVAL_TESTS = tuple(
    str(path.relative_to(ROOT))
    for path in sorted((ROOT / "tests").glob("test_retrieval_*.py"))
)
R2_SCRIPTS = (
    "scripts/check_r2.py",
    "scripts/run_g1.py",
    "scripts/build_retrieval_bundle.py",
    "scripts/provision_retrieval_models.py",
    "scripts/canonicalize_g1_labels.py",
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT / "src"), env.get("PYTHONPATH", "")))
    )
    return env


def run(argv: list[str], verbose: bool) -> tuple[bool, str]:
    """Run one gate command and capture its combined output."""
    process = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
    )
    output = process.stdout + process.stderr
    if verbose:
        print(output)
    return process.returncode == 0, output


def _pytest(*paths: str) -> list[str]:
    return [PY, "-m", "pytest", "-q", *paths]


def _check_suite(verbose: bool) -> tuple[bool, str]:
    ok, output = run(_pytest(), verbose)
    if not ok:
        return False, "suite is red"
    match = re.search(r"(\d+) passed(?:, (\d+) skipped)?", output)
    if match is None:
        return False, "could not parse pytest summary"
    passed = int(match.group(1))
    skipped = int(match.group(2) or 0)
    if passed < BASELINE_PASSED:
        return False, f"{passed} passed, below the {BASELINE_PASSED} floor"
    if skipped != BASELINE_SKIPPED:
        return False, f"{skipped} skipped, expected exactly {BASELINE_SKIPPED}"
    return True, f"{passed} passed (>= {BASELINE_PASSED}), {skipped} skipped"


def main() -> int:
    """Run portable invariants and, unless excluded, authoritative G1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-r1", action="store_true")
    args = parser.parse_args()
    failures: list[str] = []

    if not args.skip_r1:
        ok, _ = run([PY, str(ROOT / "scripts" / "check_r1.py")], args.verbose)
        print(f"[{'PASS' if ok else 'FAIL'}] R1 gate still green")
        if not ok:
            failures.append("R1 gate")

    checks = (
        (
            "lint (ruff)",
            [PY, "-m", "ruff", "check", "src", "tests", *R2_SCRIPTS],
        ),
        (
            "format (black)",
            [PY, "-m", "black", "--check", "src", "tests", *R2_SCRIPTS],
        ),
        ("types (mypy)", [PY, "-m", "mypy", "src"]),
        ("package boundaries", _pytest("tests/test_package_boundaries.py")),
        ("portable retrieval suite", _pytest(*RETRIEVAL_TESTS)),
    )
    for name, command in checks:
        ok, _ = run(command, args.verbose)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            failures.append(name)

    ok, detail = _check_suite(args.verbose)
    print(f"[{'PASS' if ok else 'FAIL'}] full suite — {detail}")
    if not ok:
        failures.append("full suite")

    if not args.portable:
        ok, _ = run(
            [
                PY,
                str(ROOT / "scripts" / "run_g1.py"),
                "--output",
                str(G1_OUTPUT),
            ],
            args.verbose,
        )
        print(f"[{'PASS' if ok else 'FAIL'}] authoritative full-corpus G1")
        if not ok:
            failures.append("authoritative G1")

    print()
    if failures:
        print(f"R2 NOT DONE — {len(failures)} failing: {', '.join(failures)}")
        return 1
    if args.portable:
        print("R2 PORTABLE CHECKS PASS — NOT G1 / NOT R2 COMPLETE")
        return 0
    print("R2 DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
