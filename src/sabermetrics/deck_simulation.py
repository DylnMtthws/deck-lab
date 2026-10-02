"""Re-simulate an edited deck document and describe the change.

The list hash is :attr:`sabermetrics.cedh.candidate.DeckCandidate.deck_sha256`.
Nothing in this module hashes the list itself. The simulator client comes
only from :func:`sabermetrics.cedh.factory.build_simulator_client`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from sabermetrics import db
from sabermetrics.cedh.candidate import (
    CandidateCard,
    CandidateProvenance,
    DeckCandidate,
)
from sabermetrics.cedh.domain import CommanderIdentity
from sabermetrics.cedh.factory import build_simulator_client
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.cedh.simulator import (
    NotSimulated,
    NotSimulatedReason,
    SimulationResult,
)
from sabermetrics.deck_documents import (
    DeckDocumentRepo,
    DeckNotFound,
    _is_public_library_zone,
)

logger = logging.getLogger(__name__)

#: Builder role ``other`` is the image of both cEDH ``flex`` and every role
#: the T10 map does not know. Mapping it back to a specific cEDH role would
#: invent a category, so ambiguous and missing roles use this neutral role.
#: ``flex`` is a legal :class:`CandidateCard` role in ``cedh.domain.ROLES``.
NEUTRAL_ROLE = "flex"

# Inverse of deck_documents._CANDIDATE_ROLE_TO_BUILDER for the keys that
# have exactly one cEDH source. ``other`` is intentionally absent.
_BUILDER_ROLE_TO_CEDH: dict[str, str] = {
    "ramp": "acceleration",
    "tutor": "tutor",
    "removal": "interaction",
    "protection": "protection",
    "draw": "card_advantage",
    "wincon": "win_package",
    "land": "land",
}

USER_EDITED_PACK_ID = "user-edited"
NO_BASELINE = "No earlier simulation to compare"
STALE_MESSAGE = "Out of date: the list changed since this run"
_ILLEGAL = "Not simulated: deck must be a legal 100-card list ({issue})"

_WUBRG = ("W", "U", "B", "R", "G")


def map_builder_role(role: object) -> str:
    """Map a builder role back to a cEDH role.

    This is the inverse of :func:`sabermetrics.deck_documents.map_candidate_role`.
    ``ramp``, ``tutor``, ``removal``, ``protection``, ``draw``, ``wincon``,
    and ``land`` each come from one cEDH role and map back to it. ``other``
    is ambiguous (``flex`` and every unknown role both become ``other``),
    and a missing role never had a cEDH role, so both become ``flex``.

    Args:
        role: Role string stored on a deck entry.

    Returns:
        A cEDH role string accepted by :class:`CandidateCard`.
    """
    return _BUILDER_ROLE_TO_CEDH.get(str(role or ""), NEUTRAL_ROLE)


def user_edited_provenance() -> CandidateProvenance:
    """Provenance for a list that did not come from a stored candidate."""
    return CandidateProvenance(
        pack_id=USER_EDITED_PACK_ID,
        pack_version="0",
        pack_source="user-edited deck",
    )


def illegal_list_reason(document: dict[str, Any]) -> str | None:
    """Return the refusal string when the list must not be simulated.

    A legal 100-card list returns None. Anything else returns
    ``Not simulated: deck must be a legal 100-card list (<first issue>)``.
    """
    validation = document.get("validation")
    if not isinstance(validation, dict):
        return _ILLEGAL.format(issue="deck validation is missing")
    issues_raw = validation.get("issues")
    issues = (
        [str(item) for item in issues_raw if str(item)]
        if isinstance(issues_raw, list)
        else []
    )
    total = validation.get("total_count")
    if total == 100 and bool(validation.get("legal")):
        return None
    first = issues[0] if issues else f"The deck has {total} cards."
    return _ILLEGAL.format(issue=first)


def candidate_from_document(
    document: dict[str, Any],
    provenance: CandidateProvenance,
) -> DeckCandidate:
    """Build a candidate from commander and public-library entries.

    Private zones are omitted. Roles use :func:`map_builder_role`. The
    returned object's :attr:`~DeckCandidate.deck_sha256` is the existing
    candidate hash; this function does not hash the list on its own.

    Raises:
        ValueError: The entries are not a commander plus a 99-card library.
    """
    commanders = _commander_entries(document)
    if not commanders:
        raise ValueError("the deck has no commander")
    if len(commanders) > 2:
        raise ValueError("the deck has more than two commanders")
    oracle_ids = tuple(_oracle_id(entry) for entry in commanders)
    names = tuple(str(entry.get("name") or "Unknown card") for entry in commanders)
    colors: list[str] = []
    for entry in commanders:
        for color in _colors(entry):
            if color not in colors:
                colors.append(color)
    identity = tuple(color for color in _WUBRG if color in colors)
    library = _merge_library(_library_entries(document))
    notes: tuple[str, ...] = ()
    if provenance.pack_id == USER_EDITED_PACK_ID:
        notes = ("user-edited deck",)
    counts: dict[str, int] = {}
    for card in library:
        counts[card.role] = counts.get(card.role, 0) + card.quantity
    return DeckCandidate(
        candidate_id=f"deck-{document.get('id') or 'edited'}",
        generated_at=datetime.now(UTC),
        commander=CommanderIdentity(
            oracle_ids=oracle_ids,
            names=names,
            color_identity=identity,
        ),
        cards=library,
        role_counts=counts,
        provenance=provenance,
        notes=notes,
    )


def load_provenance(db_path: Path, document: dict[str, Any]) -> CandidateProvenance:
    """Use the source candidate's provenance, or a user-edited marker."""
    if document.get("source_kind") == "candidate" and document.get("source_id"):
        row = db.CedhCandidatesRepo(db_path).get(str(document["source_id"]))
        raw = row.get("candidate_json") if row else None
        if isinstance(raw, str) and raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            prov = payload.get("provenance") if isinstance(payload, dict) else None
            if isinstance(prov, dict):
                try:
                    return CandidateProvenance.model_validate(prov)
                except ValidationError:
                    logger.warning(
                        "stored candidate %s has no usable provenance",
                        document.get("source_id"),
                    )
    return user_edited_provenance()


def execute_simulation(db_path: str, simulation_id: str) -> None:
    """Run one queued simulation and store the outcome.

    Illegal lists and unsupported commanders are stored as ``Not simulated``
    and do not receive an invented score. Exceptions stay in the row so the
    worker thread cannot kill the pool.
    """
    path = Path(db_path)
    repo = db.DeckDocumentSimulationsRepo(path)
    row = repo.get(simulation_id)
    if row is None:
        return
    repo.update(simulation_id, status="running")
    try:
        _execute_loaded(path, repo, row)
    except Exception as exc:
        logger.exception("deck simulation %s failed", simulation_id)
        repo.update(
            simulation_id,
            status="not_simulated",
            reason=f"Not simulated: {exc}",
        )


def simulation_view(db_path: Path | str, deck_id: str) -> dict[str, Any]:
    """Describe the latest run, its delta, and whether the list has changed."""
    path = Path(db_path)
    row = db.DeckDocumentSimulationsRepo(path).latest(deck_id)
    if row is None:
        return _empty_view()
    document = _load_document(path, deck_id)
    status = str(row.get("status") or "")
    stored_hash = str(row.get("deck_sha256") or "")
    reason = str(row.get("reason") or "")
    stale = False
    if status in {"done", "not_simulated"} and stored_hash and document is not None:
        current = _hash_of(path, document)
        stale = current != stored_hash
    result_payload: dict[str, Any] | None = None
    delta: dict[str, Any] | None = None
    baseline_message = ""
    if status == "done":
        outcome = parse_stored_result(row.get("result_json"))
        if outcome is None:
            status = "not_simulated"
            reason = reason or "Not simulated: stored result could not be read"
        else:
            result_payload = _result_payload(outcome)
            if document is not None:
                delta, baseline_message = _delta_for(
                    path, document, str(row["id"]), outcome
                )
            elif _baseline_result(path, {"id": deck_id}, str(row["id"])) is None:
                baseline_message = NO_BASELINE
    return {
        "id": row.get("id"),
        "status": status,
        "reason": reason if status == "not_simulated" else "",
        "stale": stale,
        "stale_message": STALE_MESSAGE if stale else "",
        "baseline_message": baseline_message,
        "result": result_payload,
        "delta": delta,
        "deck_sha256": stored_hash,
        "revision": row.get("revision"),
    }


def parse_stored_result(raw: object) -> SimulationResult | None:
    """Read a stored successful result, or None when it is not one."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and "outcome" in payload:
        payload = payload["outcome"]
    if not isinstance(payload, dict) or payload.get("status") != "simulated":
        return None
    try:
        return SimulationResult.model_validate(payload)
    except ValidationError:
        return None


def _execute_loaded(
    path: Path,
    repo: db.DeckDocumentSimulationsRepo,
    row: dict[str, Any],
) -> None:
    document = DeckDocumentRepo(path).get(str(row["owner_id"]), str(row["deck_id"]))
    provenance = load_provenance(path, document)
    refusal = illegal_list_reason(document)
    candidate: DeckCandidate | None
    try:
        candidate = candidate_from_document(document, provenance)
    except (ValueError, ValidationError):
        candidate = None
    revision = int(document.get("revision") or 0)
    digest = candidate.deck_sha256 if candidate is not None else ""
    simulation_id = str(row["id"])
    if refusal is not None or candidate is None:
        reason = refusal or _ILLEGAL.format(
            issue="the list is not a 99-card library beside its commander"
        )
        _store_absence(
            repo,
            simulation_id,
            reason=reason,
            deck_sha256=digest,
            revision=revision,
            code="invalid_request",
        )
        return

    client, _mode = build_simulator_client(load_cedh_settings())
    supported = client.supported_commander_keys()
    if supported and candidate.commander.key not in supported:
        _store_absence(
            repo,
            simulation_id,
            reason=_absence_text(
                NotSimulated(
                    reason="commander_unsupported",
                    detail=(
                        "commander_unsupported: no simulator model exists for "
                        f"{candidate.commander.display_name}"
                    ),
                )
            ),
            deck_sha256=digest,
            revision=revision,
            code="commander_unsupported",
        )
        return

    outcome = client.simulate(candidate)
    if isinstance(outcome, NotSimulated):
        _store_absence(
            repo,
            simulation_id,
            reason=_absence_text(outcome),
            deck_sha256=digest,
            revision=revision,
            code=outcome.reason,
            detail=outcome.detail,
        )
        return
    if outcome.deck_sha256 != candidate.deck_sha256:
        _store_absence(
            repo,
            simulation_id,
            reason=_absence_text(
                NotSimulated(
                    reason="deck_mismatch",
                    detail="deck_mismatch: the simulator ran a different list",
                )
            ),
            deck_sha256=digest,
            revision=revision,
            code="deck_mismatch",
        )
        return
    repo.update(
        simulation_id,
        status="done",
        deck_sha256=digest,
        revision=revision,
        reason="",
        result_json=json.dumps({"outcome": outcome.model_dump(mode="json")}),
    )


def _store_absence(
    repo: db.DeckDocumentSimulationsRepo,
    simulation_id: str,
    *,
    reason: str,
    deck_sha256: str,
    revision: int,
    code: str,
    detail: str = "",
) -> None:
    if reason.lower().startswith("not simulated"):
        shown = reason
    else:
        shown = f"Not simulated: {reason}"
    payload = NotSimulated(
        reason=cast(NotSimulatedReason, code),
        detail=detail or shown,
    )
    repo.update(
        simulation_id,
        status="not_simulated",
        deck_sha256=deck_sha256,
        revision=revision,
        reason=shown,
        result_json=json.dumps({"outcome": payload.model_dump(mode="json")}),
    )


def _absence_text(outcome: NotSimulated) -> str:
    detail = outcome.detail.strip()
    if detail.lower().startswith("not simulated"):
        return detail
    if detail:
        return f"Not simulated: {detail}"
    return f"Not simulated: {outcome.reason}"


def _result_payload(result: SimulationResult) -> dict[str, Any]:
    headline = result.headline
    payload: dict[str, Any] = {
        "metric": result.metric,
        "measures": result.measures,
        "does_not_measure": result.does_not_measure,
        "unseen_card_count": result.unseen_card_count,
        "probability": None,
        "turn": None,
        "text": result.metric,
    }
    if headline is not None:
        payload["probability"] = headline.probability
        payload["turn"] = headline.turn
        payload["text"] = (
            f"{result.metric}: {headline.probability:.3f} probability "
            f"by turn {headline.turn}"
        )
    return payload


def _delta_for(
    path: Path,
    document: dict[str, Any],
    simulation_id: str,
    after: SimulationResult,
) -> tuple[dict[str, Any] | None, str]:
    baseline = _baseline_result(path, document, simulation_id)
    if baseline is None:
        return None, NO_BASELINE
    before_head = baseline.headline
    after_head = after.headline
    unseen = {
        "before": baseline.unseen_card_count,
        "after": after.unseen_card_count,
        "text": (
            f"unseen_card_count: {baseline.unseen_card_count} → "
            f"{after.unseen_card_count}"
        ),
    }
    if before_head is None or after_head is None:
        return {
            "metric": after.metric,
            "unit": "probability",
            "unseen_card_count": unseen,
        }, ""
    text = (
        f"{after.metric}: {before_head.probability:.3f} → "
        f"{after_head.probability:.3f} probability"
    )
    return {
        "metric": after.metric,
        "unit": "probability",
        "before": before_head.probability,
        "after": after_head.probability,
        "text": text,
        "unseen_card_count": unseen,
    }, ""


def _baseline_result(
    path: Path,
    document: dict[str, Any],
    exclude_id: str,
) -> SimulationResult | None:
    """Candidate simulation when the deck came from one, else the prior run.

    A candidate deck does not fall through to an earlier document run when
    that candidate has no successful simulation.
    """
    if document.get("source_kind") == "candidate" and document.get("source_id"):
        row = db.CedhCandidatesRepo(path).get(str(document["source_id"]))
        raw = row.get("simulation_json") if row else None
        return parse_stored_result(raw)
    previous = db.DeckDocumentSimulationsRepo(path).previous_done(
        str(document.get("id") or ""), exclude_id
    )
    if previous is None:
        return None
    return parse_stored_result(previous.get("result_json"))


def _hash_of(path: Path, document: dict[str, Any]) -> str | None:
    try:
        return candidate_from_document(
            document, load_provenance(path, document)
        ).deck_sha256
    except (ValueError, ValidationError):
        return None


def _load_document(path: Path, deck_id: str) -> dict[str, Any] | None:
    with db.connect(path) as conn:
        owner = conn.execute(
            "SELECT owner_id FROM deck_documents WHERE id=?", (deck_id,)
        ).fetchone()
    if owner is None:
        return None
    try:
        return DeckDocumentRepo(path).get(str(owner["owner_id"]), deck_id)
    except DeckNotFound:
        return None


def _empty_view() -> dict[str, Any]:
    return {
        "id": None,
        "status": "none",
        "reason": "",
        "stale": False,
        "stale_message": "",
        "baseline_message": "",
        "result": None,
        "delta": None,
        "deck_sha256": "",
        "revision": None,
    }


def _commander_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in _entries(document) if _is_commander(entry)]


def _library_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    library: list[dict[str, Any]] = []
    for entry in _entries(document):
        if _is_commander(entry):
            continue
        zone = entry.get("zone_name") or "Unsorted"
        if not _is_public_library_zone(str(zone)):
            continue
        library.append(entry)
    return library


def _entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw = document.get("entries")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _is_commander(entry: dict[str, Any]) -> bool:
    return entry.get("is_commander") in (1, True, "1")


def _oracle_id(entry: dict[str, Any]) -> str:
    oracle = str(entry.get("oracle_id") or "").strip()
    if not oracle:
        raise ValueError("a card is missing an oracle_id")
    return oracle


def _quantity(entry: dict[str, Any]) -> int:
    try:
        quantity = int(entry.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 1
    return max(1, quantity)


def _colors(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("color_identity")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(color) for color in raw if str(color)]


def _merge_library(entries: list[dict[str, Any]]) -> tuple[CandidateCard, ...]:
    merged: dict[str, CandidateCard] = {}
    order: list[str] = []
    for entry in entries:
        oracle = _oracle_id(entry)
        role = map_builder_role(entry.get("role"))
        quantity = _quantity(entry)
        name = str(entry.get("name") or "Unknown card")
        current = merged.get(oracle)
        if current is None:
            merged[oracle] = CandidateCard(
                oracle_id=oracle,
                name=name,
                role=role,
                quantity=quantity,
                source="user_required",
            )
            order.append(oracle)
            continue
        combined_role = current.role if current.role == role else NEUTRAL_ROLE
        merged[oracle] = current.model_copy(
            update={"quantity": current.quantity + quantity, "role": combined_role}
        )
    return tuple(merged[oracle] for oracle in order)
