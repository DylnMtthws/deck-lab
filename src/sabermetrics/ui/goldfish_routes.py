"""Owner-only diagnostics, reference imports, and immutable experiment history."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound
from sabermetrics.deck_simulation import candidate_from_document, user_edited_provenance
from sabermetrics.deck_text_import import DeckTextImportError
from sabermetrics.goldfish_experiments import GoldfishExperimentsRepo
from sabermetrics.goldfish_plans import get_plan, list_plans
from sabermetrics.goldfish_service import (
    execute_experiment,
    experiment_view,
    run_settings,
    simulator_request,
)

bp = Blueprint("goldfish", __name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goldfish-diagnostics")
_CAPACITY = threading.BoundedSemaphore(2)


def _path() -> Path:
    return Path(current_app.config["DB_PATH"])


def _reference_root() -> Path:
    # Container layout installs the package but keeps fixtures in /app/fixtures.
    source = Path(__file__).resolve().parents[3] / "fixtures" / "goldfish"
    return source if source.exists() else Path("/app/fixtures/goldfish")


@bp.before_request
def gate():
    if not (
        current_app.config.get("DECK_LAB_BUILDER_ENABLED")
        and current_app.config.get("DECK_LAB_GOLDFISH_ENABLED")
    ):
        abort(404)
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()


@bp.get("/goldfish")
def home():
    manifest = json.loads((_reference_root() / "manifest.json").read_text())
    references = [
        {**d, "title": d["label"], "filename": d["fixture"]} for d in manifest["decks"]
    ]
    return render_template(
        "deck_lab/goldfish_home.html",
        references=references,
        decks=DeckDocumentRepo(_path()).list_for_owner(current_user.id),
    )


@bp.post("/goldfish/references/<reference_id>")
def import_reference(reference_id: str):
    manifest = json.loads((_reference_root() / "manifest.json").read_text())
    reference = next((d for d in manifest["decks"] if d["id"] == reference_id), None)
    if reference is None:
        abort(404)
    try:
        deck_id = DeckDocumentRepo(_path()).import_text(
            current_user.id,
            (_reference_root() / reference["fixture"]).read_text(),
            title=reference["label"],
        )
    except DeckTextImportError as exc:
        flash(
            "This reference could not be resolved against the card catalog. "
            + str(exc),
            "error",
        )
        return redirect(url_for("goldfish.home"))
    return redirect(url_for("goldfish.deck", deck_id=deck_id))


def _document(deck_id: str) -> dict:
    try:
        return DeckDocumentRepo(_path()).get(current_user.id, deck_id)
    except DeckNotFound:
        abort(404)


def _history(document: dict, repo: GoldfishExperimentsRepo) -> list[dict]:
    for row in repo.history(current_user.id, document["id"]):
        if (
            row["status"] in {"queued", "running"}
            and (
                datetime.now(UTC) - datetime.fromisoformat(row["created_at"])
            ).total_seconds()
            > 300
        ):
            repo.fail(
                current_user.id,
                row["id"],
                "This run was interrupted. Start a new run; no result was recorded.",
            )
    return [
        experiment_view(row, document, repo)
        for row in repo.history(current_user.id, document["id"])[:50]
    ]


@bp.get("/build/deck/<deck_id>/diagnostics")
def deck(deck_id: str):
    document = _document(deck_id)
    commanders = {
        entry["name"] for entry in document["entries"] if entry.get("is_commander")
    }
    plans = [plan for plan in list_plans() if plan["commander"] in commanders]
    high_power = (
        "high power" in document["title"].lower()
        or "high-power" in document["title"].lower()
    )
    plans.sort(key=lambda plan: ("high-power" in plan["id"]) != high_power)
    return render_template(
        "deck_lab/goldfish.html",
        document=document,
        plans=plans,
        history=_history(document, GoldfishExperimentsRepo(_path())),
    )


@bp.get("/api/decks/<deck_id>/diagnostics")
def history(deck_id: str):
    document = _document(deck_id)
    return jsonify(
        history=_history(document, GoldfishExperimentsRepo(_path())),
        revision=document["revision"],
    )


def _execute_and_release(path: str, owner: str, experiment_id: str) -> None:
    try:
        execute_experiment(path, owner, experiment_id)
    finally:
        _CAPACITY.release()


@bp.post("/api/decks/<deck_id>/diagnostics")
def run(deck_id: str):
    document = _document(deck_id)
    if request.content_length and request.content_length > 8192:
        return jsonify(error="Request is too large."), 413
    values = request.get_json(silent=True)
    if not isinstance(values, dict):
        return jsonify(error="Send a JSON experiment request."), 400
    if values.get("expected_revision") != document["revision"]:
        return jsonify(error="This deck changed. Reload before starting a run."), 409
    if not _CAPACITY.acquire(blocking=False):
        return (
            jsonify(
                error="Two diagnostics runs are already active. Try again shortly."
            ),
            429,
        )
    repo = GoldfishExperimentsRepo(_path())
    row = None
    submitted = False
    try:
        settings = run_settings(values)
        commanders = {e["name"] for e in document["entries"] if e.get("is_commander")}
        if get_plan(settings["plan_id"])["commander"] not in commanders:
            raise ValueError("Choose a plan for this deck's commander.")
        simulator_request(document, settings)
        hypothesis = values.get("hypothesis", "")
        if not isinstance(hypothesis, str) or len(hypothesis) > 1000:
            raise ValueError("Keep the experiment hypothesis under 1,000 characters.")
        parent_id = values.get("parent_id") or None
        if parent_id is not None and (
            not isinstance(parent_id, str) or len(parent_id) > 100
        ):
            raise ValueError("Invalid comparison run.")
        if parent_id is None:
            previous = next(
                (
                    r
                    for r in repo.history(current_user.id, deck_id)
                    if r["status"] == "done"
                ),
                None,
            )
            if previous:
                if previous["settings"] == settings:
                    parent_id = previous["id"]
                elif (
                    previous["deck_sha256"]
                    != candidate_from_document(
                        document, user_edited_provenance()
                    ).deck_sha256
                ):
                    raise ValueError(
                        "Compare card changes with the previous settings first (maximum three replacements). Change the seed or settings on an unchanged list for a fresh baseline."
                    )
        row = repo.create(
            current_user.id,
            document,
            settings,
            hypothesis=hypothesis,
            parent_id=parent_id,
        )
        _EXECUTOR.submit(
            _execute_and_release, str(_path()), str(current_user.id), row["id"]
        )
        submitted = True
    except (ValueError, OSError, RuntimeError) as exc:
        if row:
            repo.fail(current_user.id, row["id"], "The run could not be queued.")
        return jsonify(error=str(exc)), 400
    finally:
        if not submitted:
            _CAPACITY.release()
    return jsonify(id=row["id"], status="queued"), 202
