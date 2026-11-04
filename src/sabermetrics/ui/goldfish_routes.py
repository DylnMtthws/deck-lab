"""Owner-only diagnostics, reference imports, and immutable experiment history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    url_for,
)
from flask_login import current_user

from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound
from sabermetrics.deck_text_import import DeckTextImportError
from sabermetrics.goldfish_experiments import GoldfishExperimentsRepo
from sabermetrics.goldfish_service import experiment_view

bp = Blueprint("goldfish", __name__)


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
    return redirect("/build")


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
    _document(deck_id)
    return redirect(url_for("builder.deck", deck_id=deck_id) + "#goldfish")


@bp.get("/api/decks/<deck_id>/diagnostics")
def history(deck_id: str):
    document = _document(deck_id)
    return jsonify(
        history=_history(document, GoldfishExperimentsRepo(_path())),
        revision=document["revision"],
    )


@bp.post("/api/decks/<deck_id>/diagnostics")
def run(deck_id: str):
    _document(deck_id)
    return (
        jsonify(
            error="Goldfish now runs from Build using the native simulator. Open your deck and select Goldfish.",
            simulator_url=url_for("builder.simulate_deck", deck_id=deck_id),
        ),
        410,
    )
