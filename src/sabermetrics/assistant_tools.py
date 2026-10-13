"""Typed, owner-scoped tools for the conversational Build assistant.

The model can inspect and preview. Experiments create isolated snapshots;
there is intentionally no tool for applying or adopting changes to a deck.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sabermetrics import db
from sabermetrics.card_search import search_cards
from sabermetrics.deck_documents import DeckDocumentRepo, RevisionConflict
from sabermetrics.deck_evidence import (
    DeckEvidenceService,
    _card_roles,
    _library_entries,
)
from sabermetrics.deck_simulation import simulation_view


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyArgs(StrictModel):
    pass


class SearchArgs(StrictModel):
    query: str = Field(default="", max_length=160)
    oracle_text: str = Field(default="", max_length=160)
    type_line: str = Field(default="", max_length=80)
    mana_max: float | None = Field(default=None, ge=0, le=30)
    limit: int = Field(default=8, ge=1, le=12)


class InspectArgs(StrictModel):
    query: str = Field(default="", max_length=160)
    offset: int = Field(default=0, ge=0, le=128)
    limit: int = Field(default=12, ge=1, le=12)


class CardArgs(StrictModel):
    card_id: str = Field(min_length=1, max_length=128)


class EvidenceArgs(StrictModel):
    oracle_id: str = Field(default="", max_length=128)
    window_days: Literal[0, 7, 30, 90, 365] = 30


class Replacement(StrictModel):
    outgoing_entry_id: str = Field(min_length=1, max_length=128)
    incoming_card_id: str = Field(min_length=1, max_length=128)


class Proposal(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    replacements: list[Replacement] = Field(min_length=1, max_length=3)


class ProposalArgs(StrictModel):
    proposal: Proposal


class ExperimentArgs(StrictModel):
    alternatives: list[Proposal] = Field(min_length=3, max_length=3)


class SearchCall(StrictModel):
    name: Literal["search_cards"]
    arguments: SearchArgs


class InspectCall(StrictModel):
    name: Literal["inspect_deck"]
    arguments: InspectArgs


class CardCall(StrictModel):
    name: Literal["card_details"]
    arguments: CardArgs


class EvidenceCall(StrictModel):
    name: Literal["research_evidence"]
    arguments: EvidenceArgs


class LatestCall(StrictModel):
    name: Literal["latest_goldfish"]
    arguments: EmptyArgs


class RunCall(StrictModel):
    name: Literal["run_goldfish"]
    arguments: EmptyArgs


class IntelligenceCall(StrictModel):
    name: Literal["generator_intelligence"]
    arguments: EmptyArgs


class ProposeCall(StrictModel):
    name: Literal["propose_replacements"]
    arguments: ProposalArgs


class ExperimentCall(StrictModel):
    name: Literal["start_experiment"]
    arguments: ExperimentArgs


ToolCall = Annotated[
    SearchCall
    | InspectCall
    | CardCall
    | EvidenceCall
    | LatestCall
    | RunCall
    | IntelligenceCall
    | ProposeCall
    | ExperimentCall,
    Field(discriminator="name"),
]
TOOL_ADAPTER: TypeAdapter[Any] = TypeAdapter(ToolCall)


class ToolStopped(Exception):
    """A mode change, cancellation or elapsed budget prevents another action."""


class AssistantTools:
    def __init__(
        self,
        db_path: str | Path,
        owner_id: str,
        deck_id: str,
        mode: str,
        constraints: dict[str, Any],
        should_stop: Callable[[], bool],
        *,
        experiment_service=None,
    ):
        self.path = Path(db_path)
        self.owner = owner_id
        self.deck_id = deck_id
        self.mode = mode
        self.constraints = constraints
        self.should_stop = should_stop
        self.repo = DeckDocumentRepo(self.path)
        document = self.repo.get(owner_id, deck_id)
        self.revision = int(document["revision"])
        if experiment_service is None:
            from sabermetrics.assistant_experiments import ExperimentService

            experiment_service = ExperimentService(self.path)
        self.experiments = experiment_service
        self.proposals: list[dict[str, Any]] = []
        self.simulations_started = 0
        self.experiments_started = 0

    def check(self) -> dict[str, Any]:
        if self.should_stop():
            raise ToolStopped(
                "The assistant was stopped or its execution mode changed."
            )
        document = self.repo.get(self.owner, self.deck_id)
        if int(document["revision"]) != self.revision:
            raise RevisionConflict(int(document["revision"]))
        return document

    def execute(self, raw: ToolCall | dict[str, Any]) -> dict[str, Any]:
        call = TOOL_ADAPTER.validate_python(raw)
        document = self.check()
        args = call.arguments
        if call.name == "inspect_deck":
            entries = _library_entries(document)
            curve: Counter[str] = Counter()
            roles: Counter[str] = Counter()
            for entry in entries:
                quantity = int(entry.get("quantity") or 1)
                mana = entry.get("mana_value")
                curve[str(mana) if mana is not None else "unknown"] += quantity
                for role in _card_roles(entry):
                    roles[role] += quantity
            matching = [
                entry
                for entry in document.get("entries", [])
                if args.query.casefold() in str(entry.get("name") or "").casefold()
            ]
            shown = matching[args.offset : args.offset + args.limit]
            return {
                "deck_id": self.deck_id,
                "revision": self.revision,
                "front_land_count": sum(
                    int(entry.get("quantity") or 1)
                    for entry in entries
                    if "Land" in str(entry.get("type_line") or "").split("//", 1)[0]
                ),
                "land_back_face_count": sum(
                    int(entry.get("quantity") or 1)
                    for entry in entries
                    if "Land" not in str(entry.get("type_line") or "").split("//", 1)[0]
                    and "//" in str(entry.get("type_line") or "")
                    and "Land" in str(entry.get("type_line") or "").split("//", 1)[1]
                ),
                "land_count_note": "Quantity-aware counts cover the full main-deck library, excluding commanders and sideboard. Back-land faces are counted separately; imported type lines do not establish that every back face can be played from hand. Do not call this a verified MDFC count.",
                "commanders": [
                    {
                        "name": entry.get("name"),
                        "card_id": entry.get("card_id"),
                        "oracle_id": entry.get("oracle_id"),
                    }
                    for entry in document.get("entries", [])
                    if entry.get("is_commander")
                ],
                "matching_entries": len(matching),
                "offset": args.offset,
                "next_offset": (
                    args.offset + len(shown)
                    if args.offset + len(shown) < len(matching)
                    else None
                ),
                "validation": document.get("validation"),
                "entries": [
                    {
                        key: entry.get(key)
                        for key in (
                            "id",
                            "card_id",
                            "oracle_id",
                            "name",
                            "quantity",
                            "role",
                            "is_commander",
                            "zone_name",
                            "mana_value",
                        )
                    }
                    for entry in shown
                ],
                "mana_curve": dict(curve),
                "role_counts": dict(roles),
                "role_note": "Stored roles and text tags are heuristic classifications, not simulated performance.",
            }
        if call.name == "search_cards":
            colors: set[str] = set()
            for entry in document.get("entries", []):
                if entry.get("is_commander"):
                    raw_colors = entry.get("color_identity") or []
                    colors.update(
                        json.loads(raw_colors)
                        if isinstance(raw_colors, str)
                        else raw_colors
                    )
            with sqlite3.connect(self.path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                results = search_cards(
                    conn,
                    db_key=str(self.path),
                    allowed_colors=colors,
                    **args.model_dump(),
                )
            return {
                "cards": results,
                "source": "Imported Commander-legal card catalog",
                "identity_filter": sorted(colors),
                "revision": self.revision,
            }
        if call.name == "card_details":
            with sqlite3.connect(self.path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM cards WHERE id=?", (args.card_id,)
                ).fetchone()
            if row is None:
                raise ValueError(
                    "The requested card ID is absent from the imported catalog."
                )
            card = dict(row)
            keys = (
                "id",
                "oracle_id",
                "name",
                "type_line",
                "mana_cost",
                "cmc",
                "oracle_text",
                "image_uri",
                "color_identity",
                "is_legal_in_99",
                "roles",
                "power",
                "toughness",
            )
            return {
                "card": {key: card.get(key) for key in keys},
                "source": "Imported card attributes; missing values remain unknown.",
            }
        if call.name == "research_evidence":
            evidence = DeckEvidenceService(self.path)
            result = (
                evidence.alternatives(
                    document, args.oracle_id, args.window_days, limit=8
                )
                if args.oracle_id
                else evidence.for_deck(document, args.window_days)
            )
            return {
                "evidence": result,
                "revision": self.revision,
                "note": "Recorded list inclusion is evidence of use, not a win forecast or proof of a swap's effect.",
            }
        if call.name == "latest_goldfish":
            view = simulation_view(self.path, self.deck_id)
            return {"view": view, **_goldfish_observations(view)}
        if call.name == "generator_intelligence":
            role_data = [
                {
                    "entry_id": entry["id"],
                    "name": entry.get("name"),
                    "stored_role": entry.get("role"),
                    "text_tags": _card_roles(entry),
                }
                for entry in _library_entries(document)
            ]
            source = None
            if document.get("source_kind") == "candidate" and document.get("source_id"):
                row = db.CedhCandidatesRepo(self.path).get(str(document["source_id"]))
                if row and row.get("owner_id") == self.owner:
                    source = {
                        key: row.get(key)
                        for key in (
                            "candidate_id",
                            "pack_id",
                            "deck_sha256",
                            "evidence_hash",
                            "explanation_json",
                            "warnings_json",
                        )
                    }
            from sabermetrics.cedh.fallback_builder import GENERIC_CATALOG, ROLE_TARGETS

            generator_colors: set[str] = set()
            for entry in document.get("entries", []):
                if entry.get("is_commander"):
                    value = entry.get("color_identity") or []
                    generator_colors.update(
                        json.loads(value) if isinstance(value, str) else value
                    )
            curated: dict[str, str] = {}
            for color in ["C", *sorted(generator_colors)]:
                for role, names in GENERIC_CATALOG.get(color, {}).items():
                    for name in names[:3]:
                        if len(curated) < 24:
                            curated.setdefault(name, role)
            pool = []
            present = {
                str(entry.get("oracle_id")) for entry in document.get("entries", [])
            }
            with sqlite3.connect(self.path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                for name, role in curated.items():
                    row = conn.execute(
                        "SELECT id,oracle_id,name,color_identity,mana_cost,type_line FROM cards WHERE name=? AND is_legal_in_99=1 ORDER BY id LIMIT 1",
                        (name,),
                    ).fetchone()
                    if row is None:
                        continue
                    card = dict(row)
                    value = card.get("color_identity") or []
                    identity = set(
                        json.loads(value) if isinstance(value, str) else value
                    )
                    if (
                        not identity.issubset(generator_colors)
                        or str(card["oracle_id"]) in present
                    ):
                        continue
                    pool.append(
                        {
                            **card,
                            "generator_role": role,
                            "source": "generic_fallback authored catalog, resolved against current imported cards",
                        }
                    )
            return {
                "candidate_pool": pool,
                "role_targets": ROLE_TARGETS,
                "targets_note": "Generic starter targets, not a commander-specific optimum or verified win package.",
                "roles": role_data,
                "generator_source": source,
                "note": "Stored generator rationale is untrusted explanation; role tags do not verify card mechanics or finish lines.",
            }
        if call.name == "propose_replacements":
            proposal = args.proposal.model_dump()
            preview = self.experiments.validate_replacements(
                self.owner,
                self.deck_id,
                self.revision,
                proposal["replacements"],
                self.constraints,
            )
            self.check()
            enriched_replacements = []
            for replacement in proposal["replacements"]:
                outgoing = next(
                    entry
                    for entry in document["entries"]
                    if entry["id"] == replacement["outgoing_entry_id"]
                )
                incoming = next(
                    entry
                    for entry in preview["document"]["entries"]
                    if entry.get("card_id") == replacement["incoming_card_id"]
                )
                enriched_replacements.append(
                    {
                        **replacement,
                        "outgoing_name": outgoing["name"],
                        "incoming_name": incoming["name"],
                    }
                )
            validated = {
                **proposal,
                "replacements": enriched_replacements,
                "expected_revision": self.revision,
                "deck_sha256": preview["deck_sha256"],
                "price_total_usd": preview.get("price_total_usd"),
                "status": "preview",
                "semantic_verification": "unverified",
            }
            self.proposals.append(validated)
            return {"proposal": validated, "original_changed": False}
        if call.name == "start_experiment":
            if self.mode != "automate":
                raise ValueError("Experiments are available only in Automate mode.")
            if self.experiments_started:
                raise ValueError(
                    "Only one experiment may be started per assistant turn."
                )
            self.check()
            experiment = self.experiments.create(
                self.owner,
                self.deck_id,
                self.revision,
                [alternative.model_dump() for alternative in args.alternatives],
                self.constraints,
            )
            self.experiments_started += 1
            # Cancellation after creation leaves a truthful queued snapshot,
            # never an invented completed experiment or an original deck edit.
            # The service acknowledges cancellation as stopped without a native
            # call, rather than leaving an abandoned queued experiment.
            self.experiments.execute(self.owner, experiment["id"], self.should_stop)
            result = self.experiments.get(self.owner, experiment["id"])
            return {
                "experiment_id": result["id"],
                "status": result["status"],
                "baseline": {
                    "revision": result["baseline"]["revision"],
                    "deck_sha256": result["baseline"]["deck_sha256"],
                },
                "metric_summary": _experiment_metrics(result),
                "comparison_note": "Before/after observations and held-out validation remain descriptive; unknowns and coverage differences cannot establish superiority.",
                "alternatives": [
                    {
                        "id": item.get("id"),
                        "name": str(item.get("name") or "")[:200],
                        "status": item.get("status"),
                        "replacements": item.get("replacements"),
                        "error": str(item.get("error") or "")[:300],
                        "coverage": {
                            key: (item.get("training_report") or {})
                            .get("coverage", {})
                            .get(key)
                            for key in (
                                "modeled_cards",
                                "inert_cards",
                                "unauthored_cards",
                            )
                        },
                        "comparison_comparable": (item.get("comparison") or {}).get(
                            "comparable"
                        ),
                        "comparison_reason": str(
                            (item.get("comparison") or {}).get("reason") or ""
                        )[:300],
                        "validation_status": (item.get("validation") or {}).get(
                            "status"
                        ),
                        "validation_reason": str(
                            (item.get("validation") or {}).get("reason") or ""
                        )[:300],
                    }
                    for item in result["alternatives"]
                ],
                "summary_note": "One selected actual metric per test per alternative; remaining metrics, complete reports and rationales are retained in the persisted experiment.",
                "error": result.get("error"),
                "original_changed": False,
            }
        if call.name == "run_goldfish":
            return self._run_goldfish(document)
        raise ValueError("Unknown assistant tool.")

    def _run_goldfish(self, document: dict[str, Any]) -> dict[str, Any]:
        if self.simulations_started:
            raise ValueError(
                "Only one current-deck Goldfish run may be started per turn."
            )
        from sabermetrics.cedh.settings import load_cedh_settings
        from sabermetrics.cedh.simulator import (
            HttpSimulatorClient,
            NotSimulated,
            SimulationResult,
        )
        from sabermetrics.deck_simulation import (
            _with_outcome,
            candidate_from_document,
            load_provenance,
            prepare_simulation,
        )

        if not document.get("validation", {}).get("legal"):
            raise ValueError("Goldfish requires a legal deck before running.")
        settings = load_cedh_settings().simulator
        if settings.mode != "http":
            raise ValueError(
                "Native HTTP Goldfish is unavailable; no simulation was run."
            )
        digest, frozen = prepare_simulation(self.path, document)
        payload = json.loads(frozen)
        payload["assistant_run"] = {
            "schema_version": "decklab-assistant-goldfish.v1",
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
        }
        frozen = json.dumps(payload)
        candidate = candidate_from_document(
            document, load_provenance(self.path, document)
        )
        self.check()
        repo = db.DeckDocumentSimulationsRepo(self.path)
        simulation_id = repo.insert(
            deck_id=self.deck_id,
            owner_id=self.owner,
            deck_sha256=digest,
            revision=self.revision,
            result_json=frozen,
        )
        self.simulations_started += 1
        repo.update(simulation_id, status="running")
        try:
            if self.should_stop():
                raise ToolStopped("Goldfish was stopped before its native request.")
            client = HttpSimulatorClient(
                settings.url,
                games=2000,
                objective_turn=settings.objective_turn,
                timeout_seconds=min(settings.timeout_seconds, 45),
                diagnostics=True,
                diagnostics_seed=20261012,
            )
            outcome = client.simulate(candidate)
            if isinstance(outcome, NotSimulated):
                repo.update(
                    simulation_id,
                    status="not_simulated",
                    reason=outcome.detail or outcome.reason,
                )
            elif (
                isinstance(outcome, SimulationResult) and outcome.deck_sha256 == digest
            ):
                repo.update(
                    simulation_id,
                    status="done",
                    result_json=_with_outcome(
                        repo, simulation_id, outcome.model_dump(mode="json")
                    ),
                )
            else:
                raise ValueError(
                    "The native simulator returned no valid matching-deck result."
                )
        except Exception:
            repo.update(
                simulation_id,
                status="not_simulated",
                reason="Assistant Goldfish request stopped or failed; no measured result was stored.",
            )
            raise
        view = simulation_view(self.path, self.deck_id)
        return {
            "simulation_id": simulation_id,
            "status": view.get("status"),
            "view": view,
            **_goldfish_observations(view),
            "original_changed": False,
        }


def _experiment_metrics(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten actual experiment values so bounded chat context preserves them."""
    baseline = result.get("baseline") or {}
    rows = []
    priorities = {
        "opening": "qualified_opening_rate",
        "development": "engine_online_rate",
        "commander_loss": "recovery_draw_rate",
        "creature_wipe": "recovery_board_rate",
        "finish": "positive_finish_proof_rate",
    }

    def value(report, test, metric):
        return (
            (report or {}).get("tests", {}).get(test, {}).get("metrics", {}).get(metric)
            or {}
        ).get("value")

    for alternative in result.get("alternatives", []):
        report = alternative.get("training_report") or {}
        selected = []
        for test, preferred in priorities.items():
            metrics = report.get("tests", {}).get(test, {}).get("metrics", {})
            if metrics:
                selected.append(
                    (test, preferred if preferred in metrics else next(iter(metrics)))
                )
        comparison = alternative.get("comparison") or {}
        validation = alternative.get("validation") or {}
        for test, metric in selected:
            delta = comparison.get("metric_deltas", {}).get(test, {}).get(metric, {})
            heldout = (
                (validation.get("comparison") or {})
                .get("metric_deltas", {})
                .get(test, {})
                .get(metric, {})
            )
            rows.append(
                {
                    "alternative_id": alternative.get("id"),
                    "alternative_name": alternative.get("name"),
                    "test": test,
                    "metric": metric,
                    "unit": report.get("tests", {})
                    .get(test, {})
                    .get("metrics", {})
                    .get(metric, {})
                    .get("unit"),
                    "before": value(baseline.get("training_report"), test, metric),
                    "after": value(report, test, metric),
                    "delta": delta.get("delta"),
                    "reason": str(
                        delta.get("reason") or comparison.get("reason") or ""
                    )[:240],
                    "comparable": delta.get("comparable", False),
                    "validation_before": value(
                        baseline.get("validation_report"), test, metric
                    ),
                    "validation_after": value(
                        alternative.get("validation_report"), test, metric
                    ),
                    "validation_delta": heldout.get("delta"),
                    "validation_status": validation.get("status"),
                    "validation_reason": str(
                        heldout.get("unvalidated_reason")
                        or validation.get("reason")
                        or ""
                    )[:240],
                }
            )
    return rows


def _goldfish_observations(view: dict[str, Any]) -> dict[str, Any]:
    """Retain native values at a shallow depth in the model's evidence context."""
    diagnostics = (view.get("result") or {}).get("diagnostics") or {}
    priorities = {
        "opening": ("qualified_opening_rate", "mean_kept_cards"),
        "development": ("engine_online_rate", "mean_missed_land_drops"),
        "commander_loss": ("disruption_exercised_rate", "recovery_draw_rate"),
        "creature_wipe": ("disruption_exercised_rate", "recovery_board_rate"),
        "finish": ("positive_finish_proof_rate", "verified_finish_rate"),
    }
    observations = []
    statuses = []
    for test, names in priorities.items():
        body = diagnostics.get("tests", {}).get(test) or {}
        if not body:
            continue
        statuses.append(
            {
                "test": test,
                "status": body.get("status"),
                "limitations": body.get("limitations", [])[:2],
            }
        )
        for name in names:
            metric = body.get("metrics", {}).get(name)
            if isinstance(metric, dict):
                observations.append({"test": test, "metric": name, **metric})
    return {
        "observations": observations,
        "test_statuses": statuses,
        "settings": diagnostics.get("settings"),
        "coverage": diagnostics.get("coverage"),
        "stale": view.get("stale"),
        "report_status": view.get("status"),
        "report_id": view.get("id"),
        "report_revision": view.get("revision"),
        "report_deck_sha256": view.get("deck_sha256"),
        "report_reason": view.get("reason"),
        "report_note": "Null metrics are unknown. A stale report measures a previous list; do not treat it as current.",
    }


def recover_interrupted_goldfish(
    db_path: str | Path, owner_id: str, deck_id: str, now: datetime | None = None
) -> int:
    """Expire only unfinished assistant-owned requests past their recovery TTL.

    Native requests have a 45-second timeout. A fifteen-minute unfinished
    marker is therefore interrupted work, not a current run or a measured
    failure. Frozen snapshots/provenance and any evidence remain in the row.
    """
    DeckDocumentRepo(db_path).get(owner_id, deck_id)
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(minutes=15)
    recovered = 0
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id,status,result_json FROM deck_document_simulations WHERE owner_id=? AND deck_id=? AND status IN ('queued','running') AND CASE WHEN json_valid(result_json) THEN json_extract(result_json,'$.assistant_run.schema_version') END=? ORDER BY created_at,id LIMIT 50",
            (owner_id, deck_id, "decklab-assistant-goldfish.v1"),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["result_json"] or "null")
                marker = (
                    payload.get("assistant_run") if isinstance(payload, dict) else None
                )
                if (
                    not isinstance(marker, dict)
                    or marker.get("schema_version") != "decklab-assistant-goldfish.v1"
                ):
                    continue
                started = datetime.fromisoformat(str(marker.get("started_at") or ""))
                if started.tzinfo is None or started >= cutoff:
                    continue
            except (TypeError, ValueError):
                continue
            marker.update(status="interrupted", recovered_at=current.isoformat())
            updated = json.dumps(payload)
            cursor = conn.execute(
                "UPDATE deck_document_simulations SET status='not_simulated',reason=?,result_json=? WHERE id=? AND owner_id=? AND deck_id=? AND status=? AND result_json=?",
                (
                    "Not simulated: assistant Goldfish request was interrupted; no completed measurement is available.",
                    updated,
                    row["id"],
                    owner_id,
                    deck_id,
                    row["status"],
                    row["result_json"],
                ),
            )
            recovered += cursor.rowcount
        conn.commit()
    return recovered


def resolve_presentation_cards(
    db_path: str | Path, known_ids: set[str], content: str, requested_ids: list[str]
) -> list[dict[str, Any]]:
    """Select only resolved known catalog cards; the model supplies no metadata."""
    import re

    from sabermetrics.card_search import search_needle

    known = sorted(known_ids)[:512]
    if not known:
        return []
    with sqlite3.connect(db_path, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        marks = ",".join("?" for _ in known)
        rows = conn.execute(
            f"SELECT id,oracle_id,name,mana_cost,type_line,oracle_text,image_uri FROM cards WHERE id IN ({marks}) ORDER BY name,id",
            known,
        ).fetchall()
    cards = {str(row["id"]): dict(row) for row in rows}
    chosen = [card_id for card_id in requested_ids if card_id in cards]
    normalized = search_needle(content).casefold()
    for card_id, card in cards.items():
        aliases = [str(card["name"]), str(card["name"]).split(" // ", 1)[0]]
        if any(
            re.search(
                r"(?<!\w)" + re.escape(search_needle(alias).casefold()) + r"(?!\w)",
                normalized,
            )
            for alias in aliases
            if search_needle(alias)
        ):
            chosen.append(card_id)
    selected = []
    seen = set()
    for card_id in chosen:
        card = cards[card_id]
        identity = str(card.get("oracle_id") or card_id)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(
            {
                key: card.get(key)
                for key in (
                    "id",
                    "name",
                    "mana_cost",
                    "type_line",
                    "oracle_text",
                    "image_uri",
                )
            }
        )
        if len(selected) >= 12:
            break
    return selected
