"""Transport and presentation for the simulator-owned diagnostics artifact.

No game mechanics live here. Inputs are frozen by GoldfishExperimentsRepo;
the versioned subprocess receives only public card facts and run settings.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from sabermetrics.deck_documents import _is_public_library_zone
from sabermetrics.goldfish_experiments import GoldfishExperimentsRepo

ARTIFACT = Path(__file__).parent / "vendor" / "goldfish_diagnostics.pyz"
SCENARIOS = {
    "opening": "opening",
    "development": "turns",
    "commander_loss": "commander",
    "creature_wipe": "wipe",
    "finish": "finish",
}


def engine_identity() -> str:
    digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    manifest = json.loads(ARTIFACT.with_name("goldfish-manifest.json").read_text())
    if manifest.get("artifact_sha256") != digest:
        raise ValueError(
            "The simulator artifact does not match its reviewed source manifest."
        )
    return digest


def run_settings(values: dict[str, Any]) -> dict[str, Any]:
    from sabermetrics.goldfish_plans import get_plan

    plan_id = str(values.get("plan_id") or "")
    get_plan(plan_id)
    games = values.get("games", 200)
    seed = values.get("seed", 20261010)
    if isinstance(games, bool) or isinstance(seed, bool):
        raise ValueError("Trials and seed must be whole numbers.")  # noqa: TRY004
    try:
        games, seed = int(str(games)), int(str(seed))
    except (ValueError, TypeError):
        raise ValueError("Trials and seed must be whole numbers.")
    if not 20 <= games <= 1000 or not 0 <= seed < 2**63:
        raise ValueError("Use 20–1,000 trials and a nonnegative seed below 2^63.")
    return {
        "plan_id": plan_id,
        "games": games,
        "seed": seed,
        "engine_sha256": engine_identity(),
        "protocol": "goldfish-diagnostics-request.v1",
        "environment": "strict_goldfish",
        "checkpoints": {"development_turn": 5, "finish_turn": 7},
    }


def simulator_request(document: dict, settings: dict) -> dict:
    commanders: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for entry in document["entries"]:
        if not entry.get("is_commander") and not _is_public_library_zone(
            entry.get("zone_name")
        ):
            continue
        card: dict[str, Any] = {
            key: str(entry.get(key) or "")
            for key in ("oracle_id", "name", "mana_cost", "type_line")
        }
        if not card["oracle_id"]:
            card["oracle_id"] = str(entry.get("card_id") or "")
        card["quantity"] = int(entry.get("quantity") or 1)
        (commanders if entry.get("is_commander") else cards).append(card)
    if len(commanders) != 1:
        raise ValueError(
            "Diagnostics currently supports one commander; partners are not modeled yet."
        )
    return {
        "schema_version": settings["protocol"],
        "deck": {"commander": commanders[0], "cards": cards},
        "games": settings["games"],
        "seed": settings["seed"],
        "plan_id": settings["plan_id"],
        "checkpoints": settings["checkpoints"],
        "trace_limit": 3,
    }


def execute_experiment(db_path: str, owner_id: str, experiment_id: str) -> None:
    repo = GoldfishExperimentsRepo(db_path)
    row = repo.get(owner_id, experiment_id)
    if row is None:
        return
    try:
        if row["settings"]["engine_sha256"] != engine_identity():
            raise ValueError(
                "The simulator changed after submission. Start a new baseline."
            )
        payload = simulator_request(row["document"], row["settings"])
        with tempfile.TemporaryFile(mode="w+") as output:
            completed = subprocess.run(
                [sys.executable, "-I", str(ARTIFACT)],
                input=json.dumps(payload),
                stdout=output,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=90,
                env={"PATH": os.defpath, "PYTHONHASHSEED": "0"},
                check=False,
            )
            if output.tell() > 4_000_000:
                raise ValueError("The diagnostics result exceeded its size limit.")
            output.seek(0)
            raw_result = output.read(4_000_001)
        if completed.returncode:
            raise ValueError("The diagnostics engine could not evaluate this input.")
        result = json.loads(raw_result)
        if (
            not isinstance(result, dict)
            or result.get("schema_version") != "goldfish-diagnostics-result.v1"
            or result.get("games") != payload["games"]
            or result.get("seed") != payload["seed"]
            or result.get("deck_sha256") != "sha256:" + row["deck_sha256"]
            or {s.get("id") for s in result.get("scenarios", [])} != set(SCENARIOS)
        ):
            raise ValueError("The diagnostics engine returned an incompatible result.")
        validate_result(result)
        result["request_sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        result["tests"] = {
            SCENARIOS[s["id"]]: {
                **s,
                "status": "unsupported" if s["status"] == "unknown" else s["status"],
                "limitations": s.get("unknown_metrics", []),
            }
            for s in result["scenarios"]
        }
        repo.finish(owner_id, experiment_id, result)
    except (
        ValueError,
        OSError,
        subprocess.SubprocessError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        repo.fail(owner_id, experiment_id, str(exc))


def validate_result(result: dict) -> None:
    if not isinstance(result.get("coverage"), dict) or not result.get("engine_version"):
        raise ValueError("Missing engine coverage or version.")
    if len(result["scenarios"]) != 5:
        raise ValueError("Exactly five distinct scenarios are required.")
    for scenario in result["scenarios"]:
        if scenario.get("status") not in {"measured", "partial", "unknown"}:
            raise ValueError("Invalid scenario status.")
        if not isinstance(scenario.get("metrics"), dict) or not isinstance(
            scenario.get("unknown_metrics"), list
        ):
            raise ValueError(  # noqa: TRY004
                "Missing scenario metrics or coverage limitations."
            )
        if (
            scenario["status"] in {"partial", "unknown"}
            and not scenario["unknown_metrics"]
        ):
            raise ValueError(
                "Incomplete scenarios must explain their missing coverage."
            )
        for key, value in scenario["metrics"].items():
            if "win_rate" in key or (
                isinstance(value, float) and not math.isfinite(value)
            ):
                raise ValueError("Unsupported or non-finite metric.")


def experiment_view(
    row: dict, current_document: dict, repo: GoldfishExperimentsRepo
) -> dict:
    from sabermetrics.goldfish_plans import recommendations

    result = row.get("result") or {}
    view = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "deck_snapshot_json",
            "settings_json",
            "result_json",
            "document",
            "owner_id",
        }
    }
    view["stale"] = int(row["deck_revision"]) != int(current_document["revision"])
    view["recommendations"] = recommendations(result) if result else []
    comparisons = []
    for label, related_id in (
        ("Previous", row.get("parent_id")),
        ("Original", row.get("baseline_id")),
    ):
        if not related_id or related_id == row["id"]:
            continue
        related = repo.get(row["owner_id"], related_id)
        if not related or related["settings_sha256"] != row["settings_sha256"]:
            continue
        deltas = []
        for test, current in result.get("tests", {}).items():
            previous = (related.get("result") or {}).get("tests", {}).get(test, {})
            if (
                current.get("status") == "unsupported"
                or previous.get("status") == "unsupported"
            ):
                continue
            for metric, value in current.get("metrics", {}).items():
                before = previous.get("metrics", {}).get(metric)
                if type(value) in (int, float) and type(before) in (int, float):
                    deltas.append(
                        {
                            "test": test,
                            "metric": metric,
                            "before": before,
                            "after": value,
                            "delta": value - before,
                        }
                    )
                elif isinstance(value, dict) and isinstance(before, dict):
                    after_probability = value.get("probability", value.get("value"))
                    before_probability = before.get("probability", before.get("value"))
                    if isinstance(after_probability, (int, float)) and isinstance(
                        before_probability, (int, float)
                    ):
                        deltas.append(
                            {
                                "test": test,
                                "metric": metric + " (percentage points)",
                                "before": round(before_probability * 100, 2),
                                "after": round(after_probability * 100, 2),
                                "delta": round(
                                    (after_probability - before_probability) * 100, 2
                                ),
                            }
                        )
        comparisons.append(
            {
                "label": label,
                "id": related_id,
                "deltas": deltas,
                "note": "Same seed and settings. Differences are descriptive; confirm promising changes with fresh trials.",
            }
        )
    view["comparisons"] = comparisons
    return view
