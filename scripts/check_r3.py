"""The R3 gate. Default mode includes the authoritative G2 claim.

    python scripts/check_r3.py             # completion gate
    python scripts/check_r3.py --portable  # code/fixture checks, never G2
    python scripts/check_r3.py --verbose

Portable mode exists for a machine without the two local models or a full
corpus. It can pass, and it prints that R3 is not complete; only the default
mode can print ``R3 DONE``.
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
#: Measured on a clean tree after R3's tests landed. Passed is a FLOOR, so
#: adding tests is progress; skipped is EXACT, because a test that starts
#: skipping is a test that stopped running.
BASELINE_PASSED = 1740
BASELINE_SKIPPED = 31
G2_OUTPUT = ROOT / ".research-dev" / "g2-scorecard.json"
R3_TESTS = (
    "tests/test_research_plan_ir.py",
    "tests/test_research_deck_context.py",
    "tests/test_research_executor.py",
    "tests/test_r3_plans.py",
    "tests/test_g2_scoring.py",
)
R3_SCRIPTS = (
    "scripts/check_r3.py",
    "scripts/run_g2.py",
    "scripts/map_deck_context_ids.py",
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
    """Run portable invariants and, unless excluded, authoritative G2."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-r2", action="store_true")
    parser.add_argument(
        "--r2-portable",
        action="store_true",
        help="chain to check_r2 in portable mode, for a machine without G1",
    )
    args = parser.parse_args()
    failures: list[str] = []

    if not args.skip_r2:
        # Chain to the FULL R2 gate by default, the way R2 chains to R1. A
        # portable chain would let "R3 DONE" print without R2's authoritative
        # G1 ever being re-verified, which is the whole point of chaining.
        chained = [PY, str(ROOT / "scripts" / "check_r2.py")]
        if args.r2_portable or args.portable:
            chained.append("--portable")
        ok, _ = run(chained, args.verbose)
        label = "portable" if (args.r2_portable or args.portable) else "authoritative"
        print(f"[{'PASS' if ok else 'FAIL'}] R2 gate still green ({label})")
        if not ok:
            failures.append("R2 gate")

    checks = (
        (
            "lint (ruff)",
            [PY, "-m", "ruff", "check", "src", "tests", *R3_SCRIPTS],
        ),
        (
            "format (black)",
            [PY, "-m", "black", "--check", "src", "tests", *R3_SCRIPTS],
        ),
        ("types (mypy)", [PY, "-m", "mypy", "src"]),
        ("package boundaries", _pytest("tests/test_package_boundaries.py")),
        ("R3 suite", _pytest(*R3_TESTS)),
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
            [PY, str(ROOT / "scripts" / "run_g2.py"), "--output", str(G2_OUTPUT)],
            args.verbose,
        )
        print(f"[{'PASS' if ok else 'FAIL'}] authoritative G2")
        if not ok:
            failures.append("authoritative G2")

    print()
    if failures:
        print(f"R3 NOT DONE — {len(failures)} failing: {', '.join(failures)}")
        return 1
    if args.portable:
        print("R3 PORTABLE CHECKS PASS — NOT G2 / NOT R3 COMPLETE")
        return 0
    print("R3 DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
