#!/usr/bin/env python3
"""Forge rules-engine adapter: stdin scenario JSON -> stdout receipt JSON.

Runs one scenario inside the official Forge 2.0.15 release through
``ForgeBridge.java`` (compiled with javac against the release jar). The
receipt carries what Forge reported -- zones, life, counters, mana, stack,
resolution order, Forge's own game log and event stream -- plus provenance
(jar and card-script SHA-256s) and measured wall time / peak RSS.

Nothing here predicts an outcome. If Forge cannot run, the receipt says so
with ``status`` != ``executed`` and no state.

Engine diagnostics go to a log file; stdout carries exactly one JSON object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

ADAPTER_SCHEMA = "forge-adapter-receipt.v1"
HERE = Path(__file__).resolve().parent
BRIDGE_SOURCE = HERE / "ForgeBridge.java"

ROOT = Path(os.environ.get("FORGE_ADAPTER_ROOT", "/tmp/decklab-mechanics-v3"))
FORGE_DIR = Path(os.environ.get("FORGE_DIR", ROOT / "engines/forge"))
JAVA_HOME = Path(
    os.environ.get(
        "FORGE_JAVA_HOME", ROOT / "engines/jdk/jdk-17.0.20.1+1/Contents/Home"
    )
)
FORGE_JAR = FORGE_DIR / "forge-gui-desktop-2.0.15-jar-with-dependencies.jar"
BUILD_DIR = Path(os.environ.get("FORGE_BRIDGE_BUILD", ROOT / "forge-build"))
FORGE_HOME = Path(os.environ.get("FORGE_BRIDGE_HOME", ROOT / "forge-home"))
LOG_DIR = Path(os.environ.get("FORGE_BRIDGE_LOGS", ROOT / "forge-logs"))
AUTH_FILE = Path(
    os.environ.get("FORGE_ADAPTER_AUTH_FILE", ROOT / "forge-run-authorized")
)
HEAP = os.environ.get("FORGE_BRIDGE_HEAP", "768m")
TIMEOUT_S = float(os.environ.get("FORGE_BRIDGE_TIMEOUT_S", "300"))

# The source tag the release jar was built from (GitHub Card-Forge/forge).
FORGE_TAG = "forge-2.0.15"
FORGE_TAG_COMMIT = "4ec5f1a2c32fa90ecb983a72b9eb47aa5c5d7676"

ZONE_KEYS = ("hand", "library", "battlefield", "graveyard", "exile", "command")
BRIDGE_OPS = frozenset(
    {
        "play_land",
        "activate_mana",
        "cast",
        "resolve",
        "advance_turn",
        "move_to_command_zone",
    }
)
BRIDGE_SCHEMA = "forge-bridge-receipt.v2"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jar_sha256() -> str:
    """Hash the 31 MB jar once per (size, mtime); cache beside the build."""
    st = FORGE_JAR.stat()
    cache = BUILD_DIR / "jar-sha256.json"
    key = f"{st.st_size}:{int(st.st_mtime)}"
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            return str(data["sha256"])
    digest = _sha256_file(FORGE_JAR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"key": key, "sha256": digest}))
    return digest


def _card_names(request: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for player in request["players"]:
        for cards in player.get("zones", {}).values():
            for spec in cards:
                names.add(spec.split("|", 1)[0].strip())
    return sorted(names)


def _script_provenance(names: list[str]) -> dict[str, Any]:
    """SHA-256 + text of the Forge card script each named card resolved to.

    Read straight out of the release's res/cardsfolder/cardsfolder.zip; this
    is the rules text Forge executes, not Oracle text.
    """
    out: dict[str, Any] = {}
    archive = FORGE_DIR / "res/cardsfolder/cardsfolder.zip"
    with zipfile.ZipFile(archive) as zf:
        by_name: dict[str, str] = {}
        wanted = set(names)
        for info in zf.infolist():
            if info.is_dir() or "/rebalanced/" in f"/{info.filename}":
                continue
            if not info.filename.endswith(".txt"):
                continue
            # Cheap filter: Forge filenames are lowercase name with _ for spaces.
            stem = Path(info.filename).stem
            if not any(stem == _forge_stem(n) for n in wanted):
                continue
            text = zf.read(info).decode("utf-8")
            first = text.splitlines()[0] if text else ""
            if first.startswith("Name:") and first[5:].strip() in wanted:
                by_name[first[5:].strip()] = info.filename
        for name in names:
            path = by_name.get(name)
            if path is None:
                out[name] = {"script": None}
                continue
            text = zf.read(path).decode("utf-8")
            out[name] = {
                "script": f"res/cardsfolder/cardsfolder.zip!{path}",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
            }
    return out


def _forge_stem(name: str) -> str:
    keep = []
    for ch in name.lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -":
            keep.append("_")
    return "".join(keep)


def ensure_bridge_compiled() -> Path:
    """javac ForgeBridge.java against the release jar when the source changed."""
    marker = BUILD_DIR / "ForgeBridge.class"
    stamp = BUILD_DIR / "source-sha256.txt"
    src_hash = _sha256_file(BRIDGE_SOURCE)
    if marker.exists() and stamp.exists() and stamp.read_text().strip() == src_hash:
        return BUILD_DIR
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(JAVA_HOME / "bin/javac"),
        "-J-Xmx256m",
        "-nowarn",
        "-cp",
        str(FORGE_JAR),
        "-d",
        str(BUILD_DIR),
        str(BRIDGE_SOURCE),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"javac failed: {proc.stderr[-2000:]}")
    stamp.write_text(src_hash)
    return BUILD_DIR


def java_command() -> list[str]:
    return [
        str(JAVA_HOME / "bin/java"),
        f"-Xmx{HEAP}",
        "-XX:+UseSerialGC",
        "-Djava.awt.headless=true",
        "-Dfile.encoding=UTF-8",
        "-Dio.netty.tryReflectionSetAccessible=true",
        f"-Duser.home={FORGE_HOME}",
        "-cp",
        f"{BUILD_DIR}{os.pathsep}{FORGE_JAR}",
        "ForgeBridge",
    ]


def validate_request(request: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not isinstance(request.get("scenario_id"), str):
        problems.append("scenario_id must be a string")
    players = request.get("players")
    if not isinstance(players, list) or not 1 < len(players) <= 8:
        problems.append("players must be a list of 2..8 players")
        return problems
    for i, player in enumerate(players):
        zones = player.get("zones")
        if not isinstance(zones, dict):
            problems.append(f"players[{i}].zones must be an object")
            continue
        for key, cards in zones.items():
            if key not in ZONE_KEYS:
                problems.append(f"players[{i}].zones.{key} is not a supported zone")
            elif not isinstance(cards, list) or not all(
                isinstance(c, str) and c.strip() for c in cards
            ):
                problems.append(f"players[{i}].zones.{key} must be card strings")
    if not isinstance(request.get("actions"), list):
        problems.append("actions must be a list")
    else:
        for j, action in enumerate(request["actions"]):
            if action.get("op") not in BRIDGE_OPS:
                problems.append(f"actions[{j}].op unsupported: {action.get('op')}")
            if "player" in action and not 0 <= int(action["player"]) < len(players):
                problems.append(f"actions[{j}].player out of range")
    return problems


def run_scenario(
    request: dict[str, Any], *, require_auth: bool = True
) -> dict[str, Any]:
    return run_batch([request], require_auth=require_auth)[0]


def run_batch(
    requests: list[dict[str, Any]], *, require_auth: bool = True
) -> list[dict[str, Any]]:
    """Run scenarios in ONE Forge JVM (one engine init); one receipt each."""
    receipts: list[dict[str, Any]] = []
    runnable: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for request in requests:
        receipt: dict[str, Any] = {
            "schema": ADAPTER_SCHEMA,
            "engine": "forge",
            "scenario_id": request.get("scenario_id"),
        }
        receipts.append(receipt)
        problems = validate_request(request)
        if problems:
            receipt.update(status="invalid_scenario", problems=problems)
        else:
            runnable.append((request, receipt))
    ids = [r["scenario_id"] for r, _ in runnable]
    if len(set(ids)) != len(ids):
        for _, receipt in runnable:
            receipt.update(
                status="invalid_scenario", problems=["duplicate scenario_id"]
            )
        return receipts
    if not runnable:
        return receipts

    def _fail(**fields: Any) -> list[dict[str, Any]]:
        for _, receipt in runnable:
            receipt.update(**fields)
        return receipts

    if require_auth and not AUTH_FILE.exists():
        return _fail(
            status="not_run",
            reason=f"heavy-slot authorization file absent: {AUTH_FILE}",
        )
    if not FORGE_JAR.exists() or not (JAVA_HOME / "bin/java").exists():
        return _fail(status="engine_unavailable", reason="Forge jar or JDK missing")

    ensure_bridge_compiled()
    common = {
        "engine_name": "Forge",
        "release": FORGE_TAG,
        "source_commit": FORGE_TAG_COMMIT,
        "build_txt": (FORGE_DIR / "build.txt").read_text().strip(),
        "jar": FORGE_JAR.name,
        "jar_sha256": _jar_sha256(),
        "bridge_source_sha256": _sha256_file(BRIDGE_SOURCE),
        "java": str(JAVA_HOME),
        "heap": HEAP,
    }
    all_names = sorted({n for r, _ in runnable for n in _card_names(r)})
    scripts = _script_provenance(all_names)
    for request, receipt in runnable:
        receipt["provenance"] = dict(
            common, card_scripts={n: scripts[n] for n in _card_names(request)}
        )

    FORGE_HOME.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tag = (
        runnable[0][0]["scenario_id"]
        if len(runnable) == 1
        else f"batch-{len(runnable)}"
    )
    log_path = LOG_DIR / f"{tag}.stderr.log"
    payload = (
        runnable[0][0] if len(runnable) == 1 else {"batch": [r for r, _ in runnable]}
    )
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    started = time.monotonic()
    with log_path.open("w") as log:
        try:
            proc = subprocess.run(
                java_command(),
                input=json.dumps(payload),
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                cwd=FORGE_DIR,  # GuiDesktop.getAssetsDir() is "" for release builds
                timeout=TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            proc = subprocess.CompletedProcess(exc.cmd, -9, partial, None)
    wall_ms = round((time.monotonic() - started) * 1000)
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # macOS reports ru_maxrss in bytes, Linux in KiB; it is the max over all
    # children so far, so exact only when this is the first child process.
    rss_mb = rss / (1 << 20) if sys.platform == "darwin" else rss / 1024
    measured = {
        "jvm_wall_ms": wall_ms,
        "jvm_scenarios": len(runnable),
        "peak_rss_mb": round(rss_mb, 1),
        "peak_rss_exact": before == 0,
        "exit_code": proc.returncode,
    }
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    by_id: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return _fail(
                status="bridge_protocol_error",
                reason="non-JSON stdout line",
                measured=measured,
                engine_log=str(log_path),
            )
        by_id[str(obj.get("scenario_id"))] = obj
    for request, receipt in runnable:
        receipt["measured"] = measured
        receipt["engine_log"] = str(log_path)
        bridge = by_id.get(request["scenario_id"])
        if bridge is None or bridge.get("schema") != BRIDGE_SCHEMA:
            receipt.update(
                status="timeout" if proc.returncode == -9 else "bridge_protocol_error",
                reason="no receipt for scenario",
            )
            continue
        receipt["status"] = bridge.get("status", "bridge_protocol_error")
        receipt["bridge"] = bridge
        if receipt["status"] == "executed":
            receipt["observed"] = summarize(bridge)
    return receipts


def _perm(card: dict[str, Any]) -> dict[str, Any]:
    """Forge names a token card "<name> Token"; report the token's name."""
    out = {k: v for k, v in card.items() if k != "forge_id"}
    if card.get("token") and card["name"].endswith(" Token"):
        out["name"] = card["name"][: -len(" Token")]
        out["forge_name"] = card["name"]
    return out


def summarize(bridge: dict[str, Any]) -> dict[str, Any]:
    """Compact view of what Forge reported. Pure reshaping, no rules."""
    final = bridge["final_state"]
    players = []
    for p in final["players"]:
        zones = {
            k: (
                [c["name"] for c in v]
                if k == "library"
                else sorted(c["name"] for c in v)
            )
            for k, v in p["zones"].items()
        }
        players.append(
            {
                "label": p["label"],
                "life": p["life"],
                "lost": p["lost"],
                "mana_pool": p["mana_pool"],
                "commander_casts": p["commander_casts"],
                "zones": zones,
                "battlefield": [_perm(c) for c in p["zones"]["battlefield"]],
            }
        )
    steps = bridge["steps"]
    illegal = next((s for s in steps if s["outcome"] == "illegal"), None)
    return {
        "outcome": "illegal" if illegal else "legal",
        "illegal_at": illegal["index"] if illegal else None,
        "reason_code": illegal["reason_code"] if illegal else None,
        "reason_source": illegal["reason_source"] if illegal else None,
        "players": players,
        "stack": final["stack"],
        "turn": final["turn"],
        "active_player": final["active_player"],
        "trace": bridge["trace"],
        "decisions": bridge.get("decisions", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-auth-gate",
        action="store_true",
        help="skip the heavy-slot authorization file check",
    )
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument(
        "--contract",
        help="speak rules-engine-adapter.v1 (scripts/benchmark_rules_engines.py)",
    )
    parser.add_argument("command", nargs="?", choices=["capabilities", "run"])
    args = parser.parse_args(argv)
    if args.contract:
        sys.path.insert(0, str(HERE))  # -I drops the script directory
        import harness_contract as hc

        if args.contract != hc.CONTRACT or args.command is None:
            parser.error(f"--contract {hc.CONTRACT} needs 'capabilities' or 'run'")
        if args.command == "capabilities":
            out = hc.capabilities_doc()
        else:
            out = hc.run_contract(
                json.load(sys.stdin), require_auth=not args.no_auth_gate
            )
        sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
        return 0
    if args.compile_only:
        ensure_bridge_compiled()
        print(json.dumps({"compiled": str(BUILD_DIR)}))
        return 0
    request = json.load(sys.stdin)
    if "batch" in request:
        receipts = run_batch(request["batch"], require_auth=not args.no_auth_gate)
        json.dump({"receipts": receipts}, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 0 if all(r["status"] == "executed" for r in receipts) else 2
    receipt = run_scenario(request, require_auth=not args.no_auth_gate)
    json.dump(receipt, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if receipt["status"] == "executed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
