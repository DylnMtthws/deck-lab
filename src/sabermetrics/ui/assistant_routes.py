"""Owner-scoped conversational assistant endpoints for the Build sidebar."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, g, jsonify, request
from flask_login import current_user

from sabermetrics.assistant_store import AssistantStore, QueueFull
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.deck_documents import (
    DeckDocumentRepo,
    DeckNotFound,
    InvalidCommand,
    RevisionConflict,
)

bp = Blueprint("assistant", __name__)
logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="build-assistant")
_WORKER_SLOTS = threading.BoundedSemaphore(2)
_DECK_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@bp.before_request
def gate():
    if not current_app.config.get("DECK_LAB_BUILDER_ENABLED"):
        abort(404)
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()
    # This application uses one waitress process. Serialize settings/adoption
    # requests for the same owner/deck so preferences cannot change between
    # validating a selected alternative and committing its commands.
    if request.method == "POST":
        key = (str(current_user.id), str((request.view_args or {}).get("deck_id")))
        with _LOCKS_GUARD:
            lock = _DECK_LOCKS.setdefault(key, threading.RLock())
        lock.acquire()
        g.assistant_deck_lock = lock
    # Persisted queued work must make progress even when the requested deck has
    # no job, or admission will reject a new request against a dormant queue.
    path = Path(current_app.config["DB_PATH"])
    _drain(path, AssistantStore(path))


@bp.teardown_request
def release_deck_lock(_error):
    lock = g.pop("assistant_deck_lock", None)
    if lock is not None:
        lock.release()


def context(deck_id):
    path = Path(current_app.config["DB_PATH"])
    owner = str(current_user.id)
    document = DeckDocumentRepo(path).get(owner, deck_id)
    return path, owner, document, AssistantStore(path)


def provider_status():
    settings = load_cedh_settings()
    configured = bool(os.environ.get(settings.model.credential_env, "").strip())
    enabled = bool(current_app.config.get("DECK_LAB_ASSISTANT_AI_ENABLED"))
    return {
        "enabled": enabled and configured,
        "configured": configured,
        "model": settings.model.model_id,
    }


def experiment_service(path):
    from sabermetrics.assistant_experiments import ExperimentService

    return ExperimentService(path)


@bp.errorhandler(DeckNotFound)
def not_found(_error):
    return jsonify(error="not_found"), 404


@bp.errorhandler(RevisionConflict)
def conflict(error):
    return (
        jsonify(
            error="revision_conflict",
            message="The deck changed. Reload before applying changes.",
            revision=error.current_revision,
        ),
        409,
    )


@bp.errorhandler(TypeError)
@bp.errorhandler(ValueError)
@bp.errorhandler(InvalidCommand)
def invalid(error):
    return jsonify(error="invalid_request", message=str(error)), 400


@bp.get("/api/decks/<deck_id>/assistant")
def session(deck_id):
    path, owner, document, store = context(deck_id)
    from sabermetrics.assistant_tools import recover_interrupted_goldfish

    recover_interrupted_goldfish(path, owner, deck_id)
    store.recover(owner, deck_id)
    _dispatch(path, owner, deck_id, store)
    payload = store.get(owner, deck_id)
    payload.update(
        provider=provider_status(),
        revision=document["revision"],
        experiments=experiment_service(path).list_for_deck(owner, deck_id),
    )
    return jsonify(payload)


def body_object():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise TypeError("Expected a JSON object.")
    return body


def expected_revision(body, document):
    revision = body.get("expected_revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise TypeError("An integer deck revision is required.")
    if revision != document["revision"]:
        raise RevisionConflict(document["revision"])
    return revision


@bp.post("/api/decks/<deck_id>/assistant/settings")
def settings(deck_id):
    _path, owner, document, store = context(deck_id)
    body = body_object()
    existing = store.get(owner, deck_id)
    constraints = body.get("constraints", existing["constraints"])
    if isinstance(constraints, dict):
        ids = {entry["card_id"] for entry in document["entries"]}
        protected = constraints.get("protected_cards", [])
        if isinstance(protected, list) and any(
            not isinstance(card, str) or card not in ids for card in protected
        ):
            raise ValueError("Protected cards must belong to this deck.")
    return jsonify(
        store.settings(owner, deck_id, body.get("mode", existing["mode"]), constraints)
    )


def _work(path, owner, deck_id, turn, app):
    store = AssistantStore(path)
    token = turn["turn_id"]

    def stopped():
        if not store.active(owner, deck_id, token):
            return True
        try:
            return (
                DeckDocumentRepo(path).get(owner, deck_id)["revision"]
                != turn["revision"]
            )
        except DeckNotFound:
            return True

    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(20):
            if not store.heartbeat(owner, deck_id, token):
                break

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()

    def event(value):
        if value.get("type") == "provider_checkpoint":
            if value.get("state") == "started" and stopped():
                raise RuntimeError("This research generation is no longer active.")
            store.checkpoint(owner, deck_id, token, value)
            return
        name = str(value.get("name") or "tool").replace("_", " ")
        if value.get("type") == "tool_started":
            content = "Working: " + name + "…"
        elif value.get("ok") is False:
            content = (
                name.capitalize()
                + ": "
                + str(value.get("error") or "Could not complete this action.")
            )
        else:
            content = str(
                value.get("content")
                or value.get("message")
                or (name.capitalize() + " completed.")
            )
        store.append(
            owner,
            deck_id,
            token,
            {"role": "tool", "content": content[:2000], "action": value},
        )

    try:
        from sabermetrics.assistant_runtime import run_turn

        if stopped():
            store.invalidate(owner, deck_id, token)
            return
        result = run_turn(
            path,
            owner,
            deck_id,
            turn["messages"],
            mode=turn["mode"],
            constraints=turn["constraints"],
            should_stop=stopped,
            on_event=event,
        )
        if stopped():
            store.invalidate(owner, deck_id, token)
            return
        store.finish(
            owner,
            deck_id,
            token,
            {
                "role": "assistant",
                "content": str(result.get("content") or "Task finished."),
                "presentation": result.get("presentation", {"cards": []}),
                "proposals": result.get("proposals", []),
                "actions": result.get("actions", []),
                "status": result.get("status"),
                "model": result.get("model_id"),
                "research": result.get("research"),
                "answer_review": result.get("answer_review"),
                "reason": result.get("reason"),
            },
        )
    except Exception:
        logger.exception("Build assistant turn failed")
        store.finish(
            owner,
            deck_id,
            token,
            {
                "role": "assistant",
                "content": "The assistant could not complete this request. Please retry. Your original deck was not changed.",
                "status": "failed",
            },
        )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        _WORKER_SLOTS.release()
        with app.app_context():
            _drain(path, store)


@bp.post("/api/decks/<deck_id>/assistant/messages")
def message(deck_id):
    path, owner, document, store = context(deck_id)
    body = body_object()
    revision = expected_revision(body, document)
    text = body.get("message")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise ValueError("Enter a message between 1 and 4,000 characters.")
    if not provider_status()["enabled"]:
        return (
            jsonify(
                error="provider_unavailable",
                message="Conversational AI is not configured in this environment.",
            ),
            503,
        )
    try:
        store.begin(owner, deck_id, text.strip(), revision)
    except QueueFull as exc:
        return jsonify(error="busy", message=str(exc)), 429
    except ValueError as exc:
        return jsonify(error="busy", message=str(exc)), 409
    _dispatch(path, owner, deck_id, store)
    return jsonify(store.get(owner, deck_id)), 202


def _drain(path, store):
    # queued() is bounded by the four-job admission limit. Claim validates the
    # persisted owner, current turn/generation and deck revision transactionally;
    # expired running work becomes interrupted and is never replayed here.
    if not provider_status()["enabled"]:
        return
    for queued_owner, queued_deck in store.queued():
        try:
            _dispatch(path, queued_owner, queued_deck, store)
        except DeckNotFound:
            continue


def _dispatch(path, owner, deck_id, store):
    if not provider_status()["enabled"]:
        return
    # Superseded providers may still be in flight. Keep their physical worker
    # occupied and leave replacement work durably queued until a worker is free.
    if not _WORKER_SLOTS.acquire(blocking=False):
        return
    try:
        turn = store.claim(owner, deck_id)
    except Exception:
        _WORKER_SLOTS.release()
        raise
    if turn is None:
        _WORKER_SLOTS.release()
        return
    try:
        _EXECUTOR.submit(
            _work, path, owner, deck_id, turn, current_app._get_current_object()
        )
    except RuntimeError:
        # No runtime started: release this claim back to the durable queue.
        store.release_claim(owner, deck_id, turn["turn_id"])
        _WORKER_SLOTS.release()


@bp.post("/api/decks/<deck_id>/assistant/steer")
def steer(deck_id):
    path, owner, document, store = context(deck_id)
    body = body_object()
    revision = expected_revision(body, document)
    if body.get("action") not in {"pin", "unpin"} and not provider_status()["enabled"]:
        return jsonify(error="provider_unavailable"), 503
    try:
        payload = store.steer(owner, deck_id, body, revision)
    except QueueFull as exc:
        return jsonify(error="busy", message=str(exc)), 429
    _dispatch(path, owner, deck_id, store)
    payload.update(
        provider=provider_status(),
        revision=document["revision"],
        experiments=experiment_service(path).list_for_deck(owner, deck_id),
    )
    return jsonify(payload), 200


@bp.post("/api/decks/<deck_id>/assistant/stop")
def stop(deck_id):
    _path, owner, _document, store = context(deck_id)
    return jsonify(store.stop(owner, deck_id))


@bp.post("/api/decks/<deck_id>/assistant/experiments/<experiment_id>/adopt")
def adopt(deck_id, experiment_id):
    path, owner, document, store = context(deck_id)
    body = body_object()
    revision = expected_revision(body, document)
    service = experiment_service(path)
    experiment = service.get(owner, experiment_id)
    if experiment["deck_id"] != deck_id:
        abort(404)
    alternative = body.get("alternative_id")
    if not isinstance(alternative, str):
        raise TypeError("Choose an alternative.")
    constraints = store.get(owner, deck_id)["constraints"]
    frozen = experiment["constraints"]
    if any(constraints.get(key) != frozen.get(key) for key in ("power_level", "theme")):
        raise ValueError(
            "Power or theme preferences changed. Test new alternatives before adopting."
        )
    selected = next(
        (item for item in experiment["alternatives"] if item["id"] == alternative), None
    )
    if selected is None:
        raise ValueError("Choose an alternative from this experiment.")
    service.validate_replacements(
        owner, deck_id, revision, selected["replacements"], constraints
    )
    store.stop(owner, deck_id)
    return jsonify(service.adopt(owner, experiment_id, alternative, revision))


@bp.post("/api/decks/<deck_id>/assistant/apply")
def apply(deck_id):
    path, owner, document, store = context(deck_id)
    body = body_object()
    revision = expected_revision(body, document)
    replacements = body.get("replacements")
    if not isinstance(replacements, list) or not 1 <= len(replacements) <= 3:
        raise ValueError("Select one to three replacements.")
    # The service validates real card identities, legality, protected cards,
    # budget and the prospective full deck before any original-deck mutation.
    service = experiment_service(path)
    prepared = service.validate_replacements(
        owner, deck_id, revision, replacements, store.get(owner, deck_id)["constraints"]
    )
    store.stop(owner, deck_id)
    return jsonify(
        DeckDocumentRepo(path).apply_commands(
            owner,
            deck_id,
            expected_revision=revision,
            mutation_id="assistant-" + uuid4().hex,
            commands=prepared["commands"],
        )
    )
