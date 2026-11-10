"""Owner-scoped conversational assistant endpoints for the Build sidebar."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Blueprint, abort, current_app, g, jsonify, request
from flask_login import current_user

from sabermetrics import db
from sabermetrics.assistant_store import AssistantStore, QueueFull, RunConflict
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.deck_documents import (
    DeckDocumentRepo,
    DeckNotFound,
    InvalidCommand,
    RevisionConflict,
)
from sabermetrics.ui.assistant_cost import (
    CostUnavailable,
    admission,
    authority,
    close_turn,
    cost_status,
)

bp = Blueprint("assistant", __name__)
logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="build-assistant")
_WORKER_SLOTS = threading.BoundedSemaphore(2)
_DECK_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
# At most the bounded queued/running requests have live trace objects. This is
# observation state only, never an execution cache or admission authority.
_TURN_TRACES: dict[str, Any] = {}
_TRACE_GUARD = threading.Lock()


@bp.before_request
def gate():
    g.assistant_received_monotonic = time.monotonic()
    if not current_app.config.get("DECK_LAB_BUILDER_ENABLED"):
        abort(404)
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()
    # This application uses one waitress process. Serialize settings/adoption
    # requests for the same owner/deck so preferences cannot change between
    # validating a selected alternative and committing its commands.
    if request.method == "POST":
        if request.headers.get(
            "X-Assistant-Contract"
        ) == "assistant.events.v1" and request.endpoint in {
            "assistant.settings",
            "assistant.message",
            "assistant.steer",
            "assistant.stop",
        }:
            target = request.get_json(silent=True)
            if (
                not isinstance(target, dict)
                or not {"expected_run_id", "expected_generation", "expected_revision"}
                <= target.keys()
            ):
                return (
                    jsonify(
                        error="run_conflict",
                        message="Refresh research before changing this run; its exact target is required.",
                    ),
                    409,
                )
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
    try:
        control = authority()
        environment = current_app.config.get("AI_COST_ENVIRONMENT") or os.environ.get(
            "SABER_AI_ENV"
        )
        state = control.status()
        names = [
            name
            for env, provider, name in control.config.credentials
            if env == environment and provider == "deepseek"
        ]
        configured = bool(names) and all(
            os.environ.get(name, "").strip() for name in names
        )
        configured = (
            configured
            and control.config.authorized
            and not state["killed"]
            and environment not in state.get("killed_environments", [])
        )
    except Exception:
        configured = False
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


@bp.errorhandler(RunConflict)
def run_conflict(error):
    _path, owner, document, store = context(request.view_args["deck_id"])
    payload = store.get(owner, document["id"])
    return (
        jsonify(
            error="run_conflict",
            message=str(error),
            session=payload,
            public_events=store.public_events(owner, document["id"]),
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
        cost_control=cost_status(owner),
        provider=provider_status(),
        revision=document["revision"],
        experiments=experiment_service(path).list_for_deck(owner, deck_id),
    )
    if request.args.get("contract") == "assistant.events.v1":
        raw_cursor = request.args.get("after_sequence")
        try:
            cursor = int(raw_cursor) if raw_cursor is not None else None
        except ValueError:
            cursor = -1
        try:
            page = int(request.args.get("page_index", "0"))
        except ValueError:
            page = -1
        selected = request.args.get("run_id")
        if (
            request.args.get("follow_current") == "1"
            and selected
            and selected != payload.get("turn_id")
            and not request.args.get("snapshot_token")
        ):
            selected, cursor = None, None
        payload["public_events"] = store.public_events(
            owner,
            deck_id,
            run_id=selected,
            after_sequence=cursor,
            snapshot_token=request.args.get("snapshot_token"),
            page_index=page,
        )
        enrich_operational_cost(payload["public_events"])
    return jsonify(payload)


def enrich_operational_cost(projection):
    """Expose only pending/settled observation for this authorized opaque group."""
    from sabermetrics.assistant_events import digest

    group = (projection.get("run") or {}).get("budget_group_id")
    if not group:
        return
    try:
        state = authority().status()
        selected = {key for key in state["turns"] if digest(key) == group}
        if selected:
            projection["operational"]["cost_pending"] = any(
                attempt.get("settled") is not True
                for attempt in state["attempts"].values()
                if attempt.get("turn") in selected
            )
    except Exception:
        projection["operational"]["cost_pending"] = None


def replacement_uses_budget(replacement, turn):
    """Presentation metadata cannot change the identity of a shared allowance."""
    binding = turn.get("cost_turn") or {}
    return bool(
        binding
        and replacement
        and replacement.get("busy")
        and replacement.get("turn_id") != turn.get("turn_id")
        and all(
            (replacement.get("cost_turn") or {}).get(key) == binding.get(key)
            for key in ("id", "owner")
        )
    )


def public_binding(binding):
    """Attach trusted deadline metadata; never expose the authority owner token."""
    if not binding:
        return binding
    value = dict(binding)
    try:
        from sabermetrics.assistant_events import timestamp

        control = authority()
        state = control.status()["turns"][binding["id"]]
        deadline = state["deadline"]
        value["public_budget"] = {
            "deadline_at": timestamp(deadline),
            "started_at": timestamp(deadline - control.config.deadline),
        }
    except (KeyError, AttributeError, TypeError):
        pass  # Missing immutable metadata stays unavailable, never guessed.
    return value


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
    result = store.settings(
        owner,
        deck_id,
        body.get("mode", existing["mode"]),
        constraints,
        expected=body if "expected_run_id" in body else None,
    )
    if existing.get("busy") and not result.get("busy"):
        close_turn(existing.get("cost_turn"), cancel=True)
    return jsonify(result)


def _work(path, owner, deck_id, turn, app):
    store = None
    token = turn["turn_id"]
    from sabermetrics.assistant_latency import LatencyTrace

    with _TRACE_GUARD:
        trace = _TURN_TRACES.pop(token, None)
    trace = trace or LatencyTrace(token, origin_scope="worker")
    trace.mark("worker_started")
    useful_persisted = False

    def stopped():
        try:
            if not app.config.get("DECK_LAB_ASSISTANT_AI_ENABLED"):
                return True
            binding = turn.get("cost_turn")
            if binding:
                state = authority(app).status()["turns"][binding["id"]]
                import time

                if state["closed"] or time.time() >= state["deadline"]:
                    return True
            if not store.active(owner, deck_id, token):
                return True
            with db.connect(path) as conn:
                row = conn.execute(
                    "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
                    (deck_id, owner),
                ).fetchone()
            return row is None or row["revision"] != turn["revision"]
        except DeckNotFound:
            return True

    def retire_aborted_run():
        try:
            current = store.get(owner, deck_id)
        except DeckNotFound:
            store.retire_missing_context(owner, deck_id, token)
            return
        if current.get("turn_id") != token or not current.get("busy"):
            store.invalidate(owner, deck_id, token)
            return
        with db.connect(path) as conn:
            revision = conn.execute(
                "SELECT revision FROM deck_documents WHERE id=? AND owner_id=?",
                (deck_id, owner),
            ).fetchone()
        if not revision or revision[0] != turn["revision"]:
            store.invalidate(owner, deck_id, token)
            return
        code = (
            "turn_expired"
            if app.config.get("DECK_LAB_ASSISTANT_AI_ENABLED")
            else "spending_disabled"
        )
        store.finish(
            owner,
            deck_id,
            token,
            {
                "role": "assistant",
                "status": "failed",
                "error_code": code,
                "content": "Research ended because its original deadline or paid allowance is no longer available. Published findings remain available.",
            },
        )

    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(20):
            if not store.heartbeat(owner, deck_id, token):
                break

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)

    def event(value):
        nonlocal useful_persisted
        if value.get("type") == "provider_checkpoint":
            trace.provider_checkpoint(value)
            if value.get("state") == "started" and stopped():
                raise RuntimeError("This research generation is no longer active.")
            store.checkpoint(owner, deck_id, token, value)
            return
        if value.get("type") == "judgment_reused":
            trace.cache_hit()
            return None
        publication_nonce = uuid4().hex
        name = str(value.get("name") or "tool").replace("_", " ")
        if value.get("type") == "tool_started":
            content = "Working: " + name + "…"
        elif value.get("type") == "tool_result" and value.get("name") == "check_answer":
            receipt = value.get("data") or {}
            reviewed = [
                j
                for j in receipt.get("claim_judgments", [])
                if j.get("method") != "unreviewed_abstention"
                and j.get("verdict") in {"supported", "contradicted", "unverified"}
            ]
            unresolved = value.get("ok") is False or bool(receipt.get("unresolved"))
            if not reviewed:
                content = "Evidence review unavailable; claims remain unverified."
            elif unresolved:
                content = "Evidence review finished with unresolved claims."
                if any(j.get("verdict") == "supported" for j in reviewed):
                    content += " Supported findings remain available."
            else:
                content = "Evidence review finished; see the individual claim findings."
            content += (
                " Request fulfillment: "
                + str(receipt.get("fulfillment") or "unknown")
                + "."
            )
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
        persisted = store.append(
            owner,
            deck_id,
            token,
            {
                "role": "tool",
                "content": content[:2000],
                "action": value,
                "public_findings": value.get("public_findings", []),
                "publication_nonce": publication_nonce,
            },
        )
        if (
            not useful_persisted
            and persisted.get("turn_id") == token
            and persisted.get("public_findings_count", 0)
            and persisted.get("last_publication_nonce") == publication_nonce
        ):
            trace.mark("first_useful_persisted")
            useful_persisted = True
        return {
            "committed": persisted.get("last_publication_nonce") == publication_nonce,
            "run_id": token,
            "receipt_id": publication_nonce,
        }

    try:
        store = AssistantStore(path)
        heartbeat_thread.start()
        from sabermetrics.assistant_runtime import run_turn

        if stopped():
            retire_aborted_run()
            return
        binding = turn.get("cost_turn")
        if not binding:
            raise CostUnavailable(
                "Research has no budget admission; submit a new request."
            )
        from sabermetrics.ai_cost_control import bind_turn

        with bind_turn(authority(app), binding["id"], binding["owner"]):
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
            retire_aborted_run()
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
        public = store.public_events(owner, deck_id, run_id=token, after_sequence=0)
        if public.get("run", {}).get("lifecycle") == "completed":
            trace.mark("final_persisted")
    except DeckNotFound:
        if store is not None:
            store.retire_missing_context(owner, deck_id, token)
    except Exception as error:
        logger.exception("Build assistant turn failed")
        from sabermetrics.ai_cost_control import BudgetDenied

        content = "The assistant could not complete this request. Please retry. Your original deck was not changed."
        if isinstance(error, BudgetDenied):
            content = {
                "budget exhausted": "Research stopped because a spending allowance was reached. Check your remaining daily allowance and daily/monthly reset times in the research status.",
                "tool limit": "Research stopped at this turn's tool limit. Send a narrower follow-up to continue within your remaining daily allowance.",
                "model/token limit": "Research stopped because this request exceeds the configured model or token limits. Try a narrower request.",
                "turn unavailable": "Research stopped because this turn expired, was cancelled, or paid AI was disabled.",
            }.get(
                str(error),
                "Research stopped because paid AI admission was denied. Browsing and deck editing remain available.",
            )
        store.finish(
            owner,
            deck_id,
            token,
            {
                "role": "assistant",
                "content": content,
                "status": "failed",
                "error_code": (
                    {
                        "budget exhausted": "budget_exhausted",
                        "spending disabled": "spending_disabled",
                        "tool limit": "tool_limit",
                        "turn unavailable": "turn_expired",
                    }.get(str(error), "research_failed")
                    if isinstance(error, BudgetDenied)
                    else "research_failed"
                ),
            },
        )
    finally:
        if store is not None:
            try:
                store.record_latency(
                    owner, deck_id, token, trace.snapshot().model_dump(mode="json")
                )
            except DeckNotFound:
                pass  # Deleted runs need no retained timing receipt.
            except Exception:
                logger.warning(
                    "Assistant timing observation could not be persisted", exc_info=True
                )
        replacement = None
        try:
            replacement = store.get(owner, deck_id) if store is not None else None
        except Exception:
            pass
        if not replacement_uses_budget(replacement, turn):
            close_turn(turn.get("cost_turn"), app=app)
        heartbeat_stop.set()
        if heartbeat_thread.ident is not None:
            heartbeat_thread.join(timeout=1)
        _WORKER_SLOTS.release()
        with app.app_context():
            if store is not None:
                _drain(path, store)


@bp.post("/api/decks/<deck_id>/assistant/messages")
def message(deck_id):
    path, owner, document, store = context(deck_id)
    body = body_object()
    revision = body.get("expected_revision")
    if type(revision) is not int:
        raise TypeError("An integer deck revision is required.")
    text = body.get("message")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise ValueError("Enter a message between 1 and 4,000 characters.")
    binding = None
    received = g.assistant_received_monotonic

    def admit():
        nonlocal binding
        expected_revision(body, document)
        if not provider_status()["enabled"]:
            raise CostUnavailable(
                "Conversational AI is not configured in this environment.", 503
            )
        binding = public_binding(admission(owner, deck_id, request_id))
        return binding

    try:
        request_id = body.get("client_request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ValueError("A client request ID is required.")
        payload = store.begin(
            owner,
            deck_id,
            text.strip(),
            revision,
            request_id=request_id,
            admission=admit,
            expected=body if "expected_run_id" in body else None,
        )
    except QueueFull as exc:
        close_turn(binding, cancel=True)
        return jsonify(error="busy", message=str(exc)), 429
    except RunConflict:
        close_turn(binding, cancel=True)
        raise
    except ValueError as exc:
        close_turn(binding, cancel=True)
        return jsonify(error="busy", message=str(exc)), 409
    except Exception:
        close_turn(binding, cancel=True)
        raise
    from sabermetrics.assistant_latency import LatencyTrace

    with _TRACE_GUARD:
        if (
            payload.get("busy")
            and (payload.get("research_workspace") or {}).get("job", {}).get("state")
            == "queued"
            and payload["turn_id"] not in _TURN_TRACES
        ):
            trace = LatencyTrace(
                payload["turn_id"], origin_scope="request", origin_monotonic=received
            )
            trace.mark("request_received", at=received)
            trace.mark("accepted")
            _TURN_TRACES[payload["turn_id"]] = trace
            while len(_TURN_TRACES) > 8:
                _TURN_TRACES.pop(next(iter(_TURN_TRACES)))
    _dispatch(path, owner, deck_id, store)
    return jsonify(payload), 202


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
            copy_context().run,
            _work,
            path,
            owner,
            deck_id,
            turn,
            current_app._get_current_object(),
        )
    except RuntimeError:
        # A never-started worker must not strand the original allowance, or
        # close a concurrently accepted replacement sharing that allowance.
        try:
            try:
                replacement = store.release_claim(owner, deck_id, turn["turn_id"])
            except DeckNotFound:
                store.retire_missing_context(owner, deck_id, turn["turn_id"])
                replacement = None
            if not replacement_uses_budget(replacement, turn):
                close_turn(turn.get("cost_turn"), cancel=True)
        finally:
            _WORKER_SLOTS.release()


@bp.post("/api/decks/<deck_id>/assistant/steer")
def steer(deck_id):
    path, owner, document, store = context(deck_id)
    body = body_object()
    revision = body.get("expected_revision")
    if type(revision) is not int:
        raise TypeError("An integer deck revision is required.")
    existing = store.get(owner, deck_id)
    new_binding = None

    def steering_admission():
        nonlocal new_binding
        if not provider_status()["enabled"]:
            raise CostUnavailable(
                "Conversational AI is not configured in this environment.", 503
            )
        binding = existing.get("cost_turn")
        if body.get("action") == "retry":
            new_binding = public_binding(
                admission(owner, deck_id, body.get("client_request_id"))
            )
            return new_binding
        if binding:
            state = authority().status()["turns"][binding["id"]]
            import time

            if not state["closed"] and time.time() < state["deadline"]:
                return public_binding(binding)
        raise CostUnavailable(
            "The original research allowance ended. Submit a new request to continue.",
            409,
        )

    try:
        payload = store.steer(
            owner, deck_id, body, revision, admission=steering_admission
        )
    except QueueFull as exc:
        close_turn(new_binding, cancel=True)
        return jsonify(error="busy", message=str(exc)), 429
    except Exception:
        close_turn(new_binding, cancel=True)
        raise
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
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        raise TypeError("Expected a JSON object.")
    result = store.stop(
        owner, deck_id, expected=body if "expected_run_id" in body else None
    )
    # Binding comes from the successfully targeted local decision, never a
    # separate pre-mutation session read. Existing unknown reservations stay held.
    close_turn(result.get("cost_turn"), cancel=True)
    return jsonify(result)


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


@bp.errorhandler(CostUnavailable)
def cost_unavailable(error):
    return (
        jsonify(
            error="ai_budget_unavailable",
            message=str(error),
            retry_new_submission=error.retry_new_submission,
        ),
        error.status,
    )


@bp.get("/api/assistant/cost-status")
def budget_status():
    return jsonify(cost_status(str(current_user.id)))


@bp.get("/api/admin/assistant/cost-control")
@bp.post("/api/admin/assistant/cost-control")
def budget_admin():
    if not getattr(current_user, "is_admin", False):
        abort(403)
    control = authority()
    if request.method == "POST":
        body = body_object()
        if body.get("action") == "reset":
            control.reset()  # Clears kill only; spend and reservations survive.
        elif body.get("action") == "acknowledge" and isinstance(
            body.get("alert_id"), str
        ):
            control.ack_alert(body["alert_id"])
        elif body.get("action") == "kill" and isinstance(body.get("enabled"), bool):
            environment = body.get("environment")
            if environment is not None:
                if environment not in {"main", "preview"}:
                    raise ValueError("Unknown environment.")
                control.set_kill_switch(body["enabled"], environment=environment)
            else:
                control.set_kill_switch(body["enabled"])
        else:
            raise ValueError("Choose reset or kill with a boolean enabled value.")
    return jsonify(status=control.status(), alerts=control.alerts())
