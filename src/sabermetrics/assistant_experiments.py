"""Frozen, owner-scoped alternative experiments; the source changes only on adoption.

Alternatives live as document snapshots rather than library decks. Native solo
measurements remain descriptive; theme and power rationales are unverified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sabermetrics import db
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.cedh.simulator import (
    HttpSimulatorClient,
    NotSimulated,
    SimulationResult,
)
from sabermetrics.deck_documents import (
    DeckDocumentRepo,
    DeckNotFound,
    RevisionConflict,
    _is_public_library_zone,
)
from sabermetrics.deck_simulation import (
    candidate_from_document,
    prepare_simulation,
    user_edited_provenance,
)
from sabermetrics.goldfish_experiments import _check_lineage
from sabermetrics.goldfish_validation import compare_validation, validation_seed
from sabermetrics.native_diagnostics_comparison import compare_reports

EXPERIMENT_EXPIRY_SECONDS = 15 * 60
EXPERIMENT_EXPIRY_REASON = "Experiment interrupted or expired before completion. Partial measurements are preserved; no further trials will run. Start a new experiment to continue."

_TABLE = """CREATE TABLE IF NOT EXISTS assistant_experiments (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, deck_id TEXT NOT NULL REFERENCES deck_documents(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', payload_json TEXT NOT NULL
)"""


class ExperimentService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(_TABLE)
            conn.commit()

    def _constraints(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TypeError("Constraints must be an object.")
        budget = raw.get("budget")
        if budget is not None and (
            isinstance(budget, bool)
            or not isinstance(budget, (float, int))
            or not math.isfinite(budget)
            or budget < 0
        ):
            raise ValueError("Budget must be a nonnegative USD deck ceiling or null.")
        protected = raw.get("protected_cards", [])
        if not isinstance(protected, list) or any(
            not isinstance(item, str) or not item for item in protected
        ):
            raise ValueError("Protected cards must be a list of card ids.")
        return {
            "budget": budget,
            "power_level": str(raw.get("power_level") or "")[:200],
            "theme": str(raw.get("theme") or "")[:2000],
            "protected_cards": sorted(set(protected)),
            "semantic_verification": "unverified",
            "semantic_note": "Power level and theme are recorded preferences; model rationales are unverified.",
        }

    def _document(self, owner_id: str, deck_id: str, revision: int) -> dict[str, Any]:
        document = DeckDocumentRepo(self.db_path).get(owner_id, deck_id)
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision != int(document["revision"])
        ):
            raise RevisionConflict(int(document["revision"]))
        if (
            not document["validation"].get("legal")
            or document["validation"].get("total_count") != 100
        ):
            raise ValueError("Experiments require a legal 100-card baseline.")
        return cast(dict[str, Any], document)

    def _budget(self, document: dict[str, Any], cap: float | None) -> float | None:
        if cap is None:
            return None
        entries = [
            entry
            for entry in document["entries"]
            if entry.get("is_commander")
            or _is_public_library_zone(entry.get("zone_name"))
        ]
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(snapshot_date) AS date FROM card_prices"
            ).fetchone()
            date = row["date"] if row else None
            prices = {}
            if date:
                for entry in entries:
                    price = conn.execute(
                        "SELECT price_usd FROM card_prices WHERE card_id=? AND snapshot_date=?",
                        (entry["card_id"], date),
                    ).fetchone()
                    value = price["price_usd"] if price else None
                    if (
                        value is None
                        or not math.isfinite(float(value))
                        or float(value) < 0
                    ):
                        raise ValueError(
                            f"Budget cannot be verified: price is unknown for {entry['name']}."
                        )
                    prices[entry["id"]] = float(value)
            if len(prices) != len(entries):
                raise ValueError(
                    "Budget cannot be verified: recorded prices are unavailable."
                )
        total = sum(prices[entry["id"]] * int(entry["quantity"]) for entry in entries)
        if total > cap + 1e-9:
            raise ValueError(
                f"Alternative costs ${total:.2f}, above the ${cap:.2f} total deck ceiling."
            )
        return total

    def _candidate(
        self,
        document: dict[str, Any],
        replacements: object,
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(replacements, list) or not 1 <= len(replacements) <= 3:
            raise ValueError("Choose one to three replacements.")
        frozen = copy.deepcopy(document)
        entries = {str(entry["id"]): entry for entry in frozen["entries"]}
        commands: list[dict[str, Any]] = []
        seen = set()
        with db.connect(self.db_path) as conn:
            protected_oracles = {
                entries_id.get("oracle_id")
                for entries_id in entries.values()
                if entries_id.get("card_id") in constraints["protected_cards"]
            }
            for protected in constraints["protected_cards"]:
                row = conn.execute(
                    "SELECT oracle_id FROM cards WHERE id=?", (protected,)
                ).fetchone()
                if row:
                    protected_oracles.add(row["oracle_id"])
            for replacement in replacements:
                if not isinstance(replacement, dict):
                    raise TypeError("Each replacement must be an object.")
                outgoing = str(replacement.get("outgoing_entry_id") or "")
                incoming = str(replacement.get("incoming_card_id") or "")
                entry = entries.get(outgoing)
                if (
                    entry is None
                    or outgoing in seen
                    or entry.get("is_commander")
                    or not _is_public_library_zone(entry.get("zone_name"))
                ):
                    raise ValueError(
                        "Each replacement must target a distinct main-deck library entry."
                    )
                if (
                    entry.get("card_id") in constraints["protected_cards"]
                    or entry.get("oracle_id") in protected_oracles
                ):
                    raise ValueError(
                        f"Protected card cannot be replaced: {entry['name']}."
                    )
                row = conn.execute(
                    "SELECT * FROM cards WHERE id=?", (incoming,)
                ).fetchone()
                if row is None or not row["is_legal_in_99"] or not row["oracle_id"]:
                    raise ValueError("Incoming card is missing or not Commander-legal.")
                card = dict(row)
                if card["oracle_id"] == entry.get("oracle_id"):
                    raise ValueError(
                        "A different printing of the same card is not a replacement."
                    )
                seen.add(outgoing)
                quantity = int(entry["quantity"])
                if quantity > 1:
                    commands.append(
                        {
                            "type": "set_quantity",
                            "entry_id": outgoing,
                            "quantity": quantity - 1,
                        }
                    )
                    entry["quantity"] = quantity - 1
                else:
                    commands.append({"type": "remove_entry", "entry_id": outgoing})
                    frozen["entries"].remove(entry)
                commands.append(
                    {
                        "type": "add_card",
                        "card_id": incoming,
                        "quantity": 1,
                        "zone_id": entry.get("zone_id"),
                        "role": entry.get("role") or "",
                    }
                )
                added = {
                    **entry,
                    "id": f"alternative-{outgoing}-{incoming}",
                    "card_id": incoming,
                    "oracle_id": card["oracle_id"],
                    "name": card["name"],
                    "quantity": 1,
                    "type_line": card.get("type_line"),
                    "oracle_text": card.get("oracle_text"),
                    "mana_cost": card.get("mana_cost"),
                    "mana_value": card.get("cmc"),
                    "color_identity": card.get("color_identity"),
                    "format_legal": True,
                    "is_legal_in_99": 1,
                }
                frozen["entries"].append(added)
        frozen["validation"] = DeckDocumentRepo.validate(frozen["entries"])
        if not frozen["validation"].get("legal"):
            raise ValueError(
                "Alternative is illegal: " + "; ".join(frozen["validation"]["issues"])
            )
        _check_lineage(document, frozen)
        # Apply the existing cap against the last successful source run, too.
        # This helper only freezes/checks inputs; it does not queue or execute.
        prepare_simulation(self.db_path, frozen)
        digest = candidate_from_document(frozen, user_edited_provenance()).deck_sha256
        return {
            "document": frozen,
            "commands": commands,
            "deck_sha256": digest,
            "price_total_usd": self._budget(frozen, constraints["budget"]),
            "replacements": copy.deepcopy(replacements),
        }

    def validate_replacements(
        self,
        owner_id: str,
        deck_id: str,
        expected_revision: int,
        replacements: list[dict[str, Any]],
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        """Preview one proposal without writing or changing the source deck."""
        return self._candidate(
            self._document(owner_id, deck_id, expected_revision),
            replacements,
            self._constraints(constraints),
        )

    def create(
        self,
        owner_id: str,
        deck_id: str,
        expected_revision: int,
        alternatives: list[dict[str, Any]],
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        document = self._document(owner_id, deck_id, expected_revision)
        constraints = self._constraints(constraints)
        if not isinstance(alternatives, list) or len(alternatives) != 3:
            raise ValueError(
                "An experiment requires exactly three distinct alternatives."
            )
        baseline_hash = candidate_from_document(
            document, user_edited_provenance()
        ).deck_sha256
        candidates = []
        hashes = {baseline_hash}
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                raise TypeError("Each alternative must be an object.")
            candidate = self._candidate(
                document, alternative.get("replacements"), constraints
            )
            if candidate["deck_sha256"] in hashes:
                raise ValueError(
                    "All three alternatives must be distinct from the baseline and each other."
                )
            hashes.add(candidate["deck_sha256"])
            candidates.append(
                {
                    **candidate,
                    "id": db.new_id(),
                    "name": str(alternative.get("name") or "Alternative")[:200],
                    "rationale": str(alternative.get("rationale") or "")[:8000],
                    "status": "queued",
                    "training_report": None,
                    "validation_report": None,
                    "comparison": None,
                    "validation": None,
                    "error": "",
                }
            )
        experiment_id = db.new_id()
        seed = int.from_bytes(
            hashlib.sha256(f"assistant-training.v1:{experiment_id}".encode()).digest()[
                :4
            ],
            "big",
        )
        simulator = load_cedh_settings().simulator
        payload = {
            "id": experiment_id,
            "deck_id": deck_id,
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat(),
            "baseline": {
                "revision": expected_revision,
                "deck_sha256": baseline_hash,
                "document": document,
                "training_report": None,
                "validation_report": None,
            },
            "constraints": constraints,
            "settings": {
                "games": 2000,
                "training_seed": seed,
                "validation_seed": validation_seed(seed),
                "mode": simulator.mode,
                "timeout_seconds": min(max(simulator.timeout_seconds, 1.0), 45.0),
                "objective_turn": simulator.objective_turn,
            },
            "alternatives": candidates,
            "error": "",
            "adopted_alternative_id": None,
        }
        # Recheck after validation; creation never mutates the deck.
        self._document(owner_id, deck_id, expected_revision)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO assistant_experiments(id,owner_id,deck_id,created_at,payload_json) VALUES(?,?,?,?,?)",
                (
                    experiment_id,
                    owner_id,
                    deck_id,
                    payload["created_at"],
                    json.dumps(payload),
                ),
            )
            conn.commit()
        return copy.deepcopy(payload)

    def _expire(self, owner_id: str, raw: str) -> dict[str, Any]:
        payload = cast(dict[str, Any], json.loads(raw))
        if payload["status"] not in {"queued", "running"}:
            return payload
        try:
            created = datetime.fromisoformat(payload["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            overdue = (
                datetime.now(UTC) - created
            ).total_seconds() > EXPERIMENT_EXPIRY_SECONDS
        except (KeyError, TypeError, ValueError):
            overdue = True
        if not overdue:
            return payload
        payload.update(
            status="failed",
            error=EXPERIMENT_EXPIRY_REASON,
            expiry_reason="interrupted_expired",
        )
        for alternative in payload["alternatives"]:
            if alternative["status"] in {"queued", "running"}:
                alternative.update(status="failed", error=EXPERIMENT_EXPIRY_REASON)
        with db.connect(self.db_path) as conn:
            changed = conn.execute(
                "UPDATE assistant_experiments SET payload_json=?,state='failed' WHERE id=? AND owner_id=? AND payload_json=? AND state IN ('queued','running')",
                (json.dumps(payload), payload["id"], owner_id, raw),
            )
            conn.commit()
        # A concurrently completed run wins over the stale snapshot we read.
        return payload if changed.rowcount == 1 else self.get(owner_id, payload["id"])

    def get(self, owner_id: str, experiment_id: str) -> dict[str, Any]:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM assistant_experiments WHERE id=? AND owner_id=?",
                (experiment_id, owner_id),
            ).fetchone()
        if row is None:
            raise DeckNotFound()
        return self._expire(owner_id, row["payload_json"])

    def list_for_deck(self, owner_id: str, deck_id: str) -> list[dict[str, Any]]:
        DeckDocumentRepo(self.db_path).get(owner_id, deck_id)
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM assistant_experiments WHERE owner_id=? AND deck_id=? ORDER BY created_at DESC,id DESC LIMIT 20",
                (owner_id, deck_id),
            ).fetchall()
        return [self._expire(owner_id, row["payload_json"]) for row in rows]

    def _save(self, owner_id: str, payload: dict[str, Any]) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE assistant_experiments SET payload_json=?,state=? WHERE id=? AND owner_id=? "
                "AND (state IN ('queued','running') OR (state='done' AND ?='done'))",
                (
                    json.dumps(payload),
                    payload["status"],
                    payload["id"],
                    owner_id,
                    payload["status"],
                ),
            )
            conn.commit()

    def _simulate(
        self, document: dict[str, Any], seed: int, settings: dict[str, Any]
    ) -> dict[str, Any]:
        if settings["mode"] != "http":
            raise ValueError(
                "Native HTTP diagnostics are unavailable for this experiment."
            )
        # Connection details may contain credentials; never put them in the
        # public experiment payload. Effective report provenance controls
        # comparability if the native endpoint changes between calls.
        connection = load_cedh_settings().simulator
        if connection.mode != "http" or not connection.url:
            raise ValueError("Native HTTP diagnostics are currently unavailable.")
        client = HttpSimulatorClient(
            connection.url,
            games=settings["games"],
            objective_turn=settings["objective_turn"],
            timeout_seconds=settings["timeout_seconds"],
            diagnostics=True,
            diagnostics_seed=seed,
        )
        candidate = candidate_from_document(document, user_edited_provenance())
        outcome = client.simulate(candidate)
        if isinstance(outcome, NotSimulated):
            raise RuntimeError(outcome.detail or outcome.reason)  # noqa: TRY004
        if (
            not isinstance(outcome, SimulationResult)
            or outcome.deck_sha256 != candidate.deck_sha256
        ):
            raise ValueError("Simulator returned a different deck or no result.")
        report = (outcome.raw_document or {}).get("diagnostics")
        if not isinstance(report, dict):
            raise RuntimeError("Simulator omitted native diagnostics.")  # noqa: TRY004
        return report

    def execute(
        self,
        owner_id: str,
        experiment_id: str,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        payload = self.get(owner_id, experiment_id)
        if payload["status"] != "queued":
            return
        # A stopped run writes only its acknowledgement, never a run claim or result.
        if should_stop():
            payload["status"] = "stopped"
            self._save(owner_id, payload)
            return
        with db.connect(self.db_path) as conn:
            claimed = conn.execute(
                "UPDATE assistant_experiments SET state='running' WHERE id=? AND owner_id=? AND state='queued'",
                (experiment_id, owner_id),
            )
            conn.commit()
            if claimed.rowcount != 1:
                return

        def stopped() -> bool:
            # Read-side expiry or source deletion also terminates a late worker.
            with db.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT state FROM assistant_experiments WHERE id=? AND owner_id=?",
                    (experiment_id, owner_id),
                ).fetchone()
            if row is None or row["state"] != "running":
                return True
            if not should_stop():
                return False
            payload["status"] = "stopped"
            self._save(owner_id, payload)
            return True

        if stopped():
            return
        payload["status"] = "running"
        self._save(owner_id, payload)
        settings = payload["settings"]
        try:
            for phase, seed in (
                ("training_report", settings["training_seed"]),
                ("validation_report", settings["validation_seed"]),
            ):
                if stopped():
                    return
                result = self._simulate(payload["baseline"]["document"], seed, settings)
                if stopped():
                    return
                payload["baseline"][phase] = result
                self._save(owner_id, payload)
            for alternative in payload["alternatives"]:
                if stopped():
                    return
                alternative["status"] = "running"
                self._save(owner_id, payload)
                try:
                    for phase, seed in (
                        ("training_report", settings["training_seed"]),
                        ("validation_report", settings["validation_seed"]),
                    ):
                        if stopped():
                            return
                        result = self._simulate(alternative["document"], seed, settings)
                        if stopped():
                            return
                        alternative[phase] = result
                        self._save(owner_id, payload)
                    alternative["comparison"] = compare_reports(
                        alternative["training_report"],
                        payload["baseline"]["training_report"],
                    )
                    alternative["validation"] = compare_validation(
                        payload["baseline"]["validation_report"],
                        alternative["validation_report"],
                        training_seed=settings["training_seed"],
                    )
                    alternative["status"] = "done"
                except Exception as exc:
                    alternative.update(status="failed", error=str(exc))
                if stopped():
                    return
                self._save(owner_id, payload)
            payload["status"] = "done"
        except Exception as exc:
            payload.update(status="failed", error=str(exc))
        if not stopped():
            self._save(owner_id, payload)

    def adopt(
        self,
        owner_id: str,
        experiment_id: str,
        alternative_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        payload = self.get(owner_id, experiment_id)
        alternative = next(
            (item for item in payload["alternatives"] if item["id"] == alternative_id),
            None,
        )
        if alternative is None:
            raise ValueError("Alternative was not found in this experiment.")
        if payload["adopted_alternative_id"]:
            raise ValueError("This experiment already adopted an alternative.")
        if payload["status"] != "done" or alternative["status"] != "done":
            raise ValueError("Wait until the alternative experiment has completed.")
        document = self._document(owner_id, payload["deck_id"], expected_revision)
        if (
            expected_revision != payload["baseline"]["revision"]
            or candidate_from_document(document, user_edited_provenance()).deck_sha256
            != payload["baseline"]["deck_sha256"]
        ):
            raise RevisionConflict(int(document["revision"]))
        # Revalidate current legality, protected cards and prices before adoption.
        verified = self._candidate(
            document, alternative["replacements"], payload["constraints"]
        )
        if (
            verified["deck_sha256"] != alternative["deck_sha256"]
            or verified["commands"] != alternative["commands"]
        ):
            raise ValueError(
                "The selected swaps no longer match the frozen alternative."
            )
        updated = DeckDocumentRepo(self.db_path).apply_commands(
            owner_id,
            payload["deck_id"],
            expected_revision=expected_revision,
            mutation_id=f"assistant-adopt-{experiment_id}-{alternative_id}",
            commands=alternative["commands"],
        )
        payload["adopted_alternative_id"] = alternative_id
        self._save(owner_id, payload)
        return cast(dict[str, Any], updated)
