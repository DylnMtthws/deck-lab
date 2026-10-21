"""Bounded independent discovery review, with exact original claim bindings.

``on_reviewed(corrected_parts, subset_receipt)`` runs only on
the calling/coordinator thread, in completion order. The list retains ORIGINAL
indices; the receipt, not list slicing, selects the published claims. The caller
owns publication and persistence. ``check_current`` is also coordinator-only.

At most two local batches plus one final global call; never a call per catalog
card. All jobs use the same original parts and source order, including the
reviewer's deterministic focused_sources basis. No verdict binds to a rewrite.
The final call sees the whole selection and reviews only remaining original IDs.
Only that call can supply whole-request fulfillment. Unsafe scopes use one batch.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from typing import Any

from sabermetrics.assistant_answer_review import (
    AnswerPatch,
    AnswerReview,
    apply_patches,
)
from sabermetrics.assistant_claim_fragments import VERSION, matching_judgment, text_hash
from sabermetrics.assistant_claim_review import (
    claim_units,
    focused_sources,
    review_claims,
)
from sabermetrics.assistant_provider_budget import (
    budget_gateway,
    check_active,
)

MAX_REVIEW_JOBS = 3
MAX_LOCAL_JOBS = 2

# Local admission is deliberately an allowlist of complete, card-local fact
# forms, not a blacklist of comparison synonyms. An unrecognized predicate or
# qualifier (including strategic/support/absence/ranking prose) stays global.
# These forms admit a review, not a verdict: the reviewer must still check the
# printed ability, cost and restrictions against the supplied evidence.
_MANA = r"(?:\{[0-9WUBRGCSXYZ/PH]+\})+"
_LOCAL_FACT = re.compile(
    rf"(?:Pay {_MANA} to draw (?:a card|\d+ cards)"
    rf"|Draws? (?:a card|\d+ cards)(?: only after paying {_MANA})?"
    rf"|(?:Activation|Mana) costs? {_MANA}"
    rf"|(?:This (?:card|ability) )?costs? {_MANA}"
    rf")(?:,? without tapping)?[.!]?",
    re.IGNORECASE,
)
# render_fit_reason appends these isolated clauses from ability compatibility
# and candidate_suitability. Keep the label, punctuation and fixed tap wording
# exact; arbitrary Oracle cost prose or appended strategy is not a local form.
_RENDERED_LOCAL_FACT = re.compile(
    rf"(?:Activation cost: (?:{_MANA}|\{{T\}}, Pay six \{{E\}})\."
    r"|Tap-symbol cost: this creature needs continuous control or haste; "
    r"untapping is not assumed\.)"
)


def _local_fact(claim: str) -> bool:
    claim = claim.strip()
    return (
        _LOCAL_FACT.fullmatch(claim) is not None
        or _RENDERED_LOCAL_FACT.fullmatch(claim) is not None
    )


def _local_rows(parts, sources, protected, context, units):
    if (
        protected
        or context.get("empirical_receipts")
        or context.get("package_analysis")
        or context.get("packages")
        or re.search(
            r"\b(?:package|joint.?cuts?)\b",
            json.dumps(context.get("task", {})),
            re.IGNORECASE,
        )
    ):
        return {}
    packets = context.get("recommendation_packets", [])
    if not isinstance(packets, list) or any(
        not isinstance(p, dict)
        or p.get("outgoing")
        or (p.get("changes") or {}).get("support_context")
        == "complete_prospective_package"
        for p in packets
    ):
        return {}
    names = list(
        dict.fromkeys(
            s.split("\n", 1)[0][6:] for s in sources if s.startswith("Card: ")
        )
    )
    rows = {}
    seen = set()
    from sabermetrics.assistant_answer_review import _mentions_card_name

    for index, part in enumerate(parts[1:], 1):
        if " — " not in part:
            # Global comparison prose is reserved for the last call.
            continue
        prefix, body = part.split(" — ", 1)
        matches = [
            name
            for name in names
            if re.fullmatch(
                r"(?:[^:\n]+: )?"
                + re.escape(name)
                + r" \((?:(?:\{[^}\n]+\})*|mana cost not retrieved)\)",
                prefix,
            )
        ]
        if len(matches) != 1 or not body.strip():
            return {}
        if re.search(r"\b(?:package|joint.?cuts?)\b", body, re.IGNORECASE):
            return {}
        name = matches[0]
        if name in seen:
            return {}
        seen.add(name)
        rows[index] = name
    if len(rows) < 2:
        return {}
    candidates = set(rows.values())
    local = {}
    for index, name in rows.items():
        ids = {
            cid
            for cid, unit in units.items()
            if unit["part_index"] == index
            and _local_fact(unit["claim"])
            and not any(
                other != name and _mentions_card_name(unit["claim"], other)
                for other in candidates
            )
        }
        if ids:
            local[index] = (name, ids)
    return local


def _job_context(context, names):
    """Keep compact review context, with only this batch's individual packets."""
    result = deepcopy(context)
    if "recommendation_packets" in result:
        result["recommendation_packets"] = [
            p
            for p in result["recommendation_packets"]
            if isinstance(p, dict)
            and (
                p.get("incoming", {}).get("name")
                if isinstance(p.get("incoming"), dict)
                else p.get("incoming")
            )
            in names
        ]
    return result


def _job_evidence(parts, sources, indices, context):
    """Complete mentioned card records plus all deck/rules/general records.

    Keeping every non-card record is deliberately conservative: printed-card
    catalogs dominate these discovery packets, while deleting a deck or rules
    dependency would need a semantic judgment. No lines/quotes are rewritten.
    Unknown/deictic dependencies are left for the global call by admission.
    """
    from sabermetrics.assistant_answer_review import _mentions_card_name

    text = (
        " ".join(parts[i] for i in indices)
        + " "
        + json.dumps(context, ensure_ascii=False)
    )
    return {
        i
        for i, source in enumerate(sources)
        if not source.startswith("Card: ")
        or _mentions_card_name(text, source.split("\n", 1)[0][6:])
    }


def _merge(parts, receipts, sources, protected):
    patches = [
        AnswerPatch.model_validate(p) for r in receipts for p in r.get("patches", [])
    ]
    # Each input job already passed the <=6-patch schema. Revalidate evidence,
    # exact positions and overlaps across the bounded combined patch set.
    return apply_patches(
        parts,
        sources,
        AnswerReview.model_construct(patches=patches),
        protected,
        list(getattr(sources, "card_names", [])),
    )


def _bound_receipt(
    parts, corrected, receipt, ids, units, sources, protected, evidence_indices
):
    """Reject the entire job on duplicate IDs, stale hashes or source rebasing."""
    judgments = receipt.get("claim_judgments", [])
    if (
        receipt.get("claim_unit_version") != VERSION
        or len(judgments) != len(ids)
        or {j.get("claim_id") for j in judgments} != ids
        or receipt.get("source_sha256") != [text_hash(s) for s in sources]
        or receipt.get("evidence_indices") != sorted(evidence_indices)
        or receipt.get("missing_claim_ids")
    ):
        return False
    expected_parts = {
        str(units[cid]["part_index"]): parts[units[cid]["part_index"]] for cid in ids
    }
    if receipt.get("reviewed_parts") != expected_parts:
        return False
    for judgment in judgments:
        unit = units[judgment["claim_id"]]
        if (
            not matching_judgment(unit, judgment)
            or judgment.get("reviewed_claim") != unit["claim"]
        ):
            return False
        for citation in judgment.get("citations", []):
            index = citation.get("source_index")
            quote = citation.get("quote")
            if (
                not isinstance(index, int)
                or index not in evidence_indices
                or not isinstance(quote, str)
                or not quote
                or quote not in sources[index]
            ):
                return False
    try:
        patches = [AnswerPatch.model_validate(p) for p in receipt.get("patches", [])]
        if any(
            p.part_index
            not in {u["part_index"] for cid, u in units.items() if cid in ids}
            for p in patches
        ):
            return False
        expected = apply_patches(
            parts,
            sources,
            AnswerReview(patches=patches),
            protected,
            list(getattr(sources, "card_names", [])),
        )
    except (ValueError, TypeError):
        return False
    return corrected == expected


def progressive_review(
    gateway,
    parts: list[str],
    sources: list[str],
    user_request: str,
    *,
    research_context: dict[str, Any] | None = None,
    protected_parts: set[int] | None = None,
    on_reviewed: Callable[[list[str], dict[str, Any]], Any] | None = None,
    check_current: Callable[[], Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    original = list(parts)
    sources = deepcopy(sources)
    context = deepcopy(research_context or {})
    protected = set(protected_parts or ()) | {
        i
        for i, p in enumerate(original)
        if re.search(
            r"\b(?:measured|experiment|goldfish|simulation|win.rate)\b|%",
            p,
            re.IGNORECASE,
        )
    }
    check_active(check_current)
    cancelled = threading.Event()
    checker = budget_gateway(gateway, check_current=lambda: not cancelled.is_set())
    units = {u["claim_id"]: u for u in claim_units(original, protected)}
    focused = focused_sources(original, sources)
    rows = _local_rows(original, sources, protected, context, units)
    jobs = []
    accepted: list[dict[str, Any]] = []
    corrected = original[:]

    def consume(result, ids, stage, evidence_indices=None):
        nonlocal corrected
        checked, receipt = result
        check_active(check_current)
        bound = _bound_receipt(
            original,
            checked,
            receipt,
            ids,
            units,
            focused,
            protected,
            set(range(len(focused))) if evidence_indices is None else evidence_indices,
        )
        jobs.append(
            {
                "stage": stage,
                "claim_ids": sorted(ids),
                "bound": bound,
                "status": receipt.get("status", "unverified"),
                "reason": receipt.get("reason"),
                "review_model": receipt.get("review_model"),
                "evidence_indices": receipt.get("evidence_indices", []),
                "evidence_bytes_sent": receipt.get("evidence_bytes_sent", 0),
            }
        )
        if bound:
            try:
                merged = _merge(original, [*accepted, receipt], focused, protected)
            except (ValueError, TypeError):
                bound = False
                jobs[-1]["bound"] = False
                jobs[-1]["reason"] = "patch_merge_rejected"
        if not bound:
            return False
        accepted.append(receipt)
        corrected = merged
        if on_reviewed and any(
            not j.get("unverified_remainder")
            and (
                j.get("verdict") == "supported"
                or (j.get("verdict") == "contradicted" and j.get("correction_applied"))
            )
            for j in receipt["claim_judgments"]
        ):
            check_active(check_current)
            # Subset fulfillment is never a whole-request publication verdict.
            publication = deepcopy(receipt)
            publication["fulfillment"] = "unknown"
            on_reviewed(list(checked), publication)
        return True

    def run(ids, job_context, evidence_indices=None):
        return review_claims(
            checker,
            original,
            sources,
            user_request,
            claim_ids=ids,
            research_context=job_context,
            protected_parts=protected,
            evidence_indices=evidence_indices,
        )

    global_receipt = None
    try:
        if rows:
            local = list(rows)
            local_ids = {cid for _, ids in rows.values() for cid in ids}
            # Prefer coupled/global claims as the final job. If none exist,
            # reserve one complete row so review_claims has nonempty units.
            if local_ids == set(units):
                local = local[:-1]
            count = min(MAX_LOCAL_JOBS, len(local))
            groups = [
                local[i * len(local) // count : (i + 1) * len(local) // count]
                for i in range(count)
            ]
            specifications: list[tuple[set[str], dict[str, Any], set[int]]] = []
            for group in groups:
                ids = {cid for index in group for cid in rows[index][1]}
                job_context = _job_context(context, {rows[index][0] for index in group})
                evidence = _job_evidence(original, focused, group, job_context)
                if (
                    not evidence
                    or evidence == set(range(len(focused)))
                    or len(ids) > 64
                ):
                    # No safe reduction: use one ordinary full-evidence batch.
                    specifications = []
                    rows = {}
                    break
                specifications.append((ids, job_context, evidence))
            if (
                len(set(units) - {cid for ids, _, _ in specifications for cid in ids})
                > 64
            ):
                specifications = []
                rows = {}
            with ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="claim-review"
            ) as executor:
                pending = {
                    executor.submit(run, ids, ctx, evidence): (ids, evidence)
                    for ids, ctx, evidence in specifications
                }
                try:
                    while pending:
                        check_active(check_current)
                        done, _ = wait(
                            pending, timeout=0.025, return_when=FIRST_COMPLETED
                        )
                        # Stable tie break when several finish between polls.
                        for future in sorted(done, key=lambda f: min(pending[f][0])):
                            ids, evidence = pending.pop(future)
                            consume(future.result(), ids, "local", evidence)
                except BaseException:
                    cancelled.set()
                    for future in pending:
                        future.cancel()
                    raise
        check_active(check_current)
        reviewed = {j["claim_id"] for r in accepted for j in r["claim_judgments"]}
        remaining = set(units) - reviewed
        global_context = context
        if rows:
            # Compact selection context, never conversation/tool transcripts.
            # Preserve original prose and disclose corrections to the final
            # whole-selection comparison without transferring their verdicts.
            global_part_indices = {units[cid]["part_index"] for cid in remaining}
            global_context = {
                **context,
                "progressive_selection": {
                    "additional_parent_parts": {
                        str(i): part
                        for i, part in enumerate(original)
                        if i not in global_part_indices
                    },
                    "selection_order": list(range(len(original))),
                    "local_corrections": [
                        p for r in accepted for p in r.get("patches", [])
                    ],
                    "instruction": "Combine parent_parts and additional_parent_parts in selection_order for the entire selection. Assess whole-request fulfillment and joint selection tradeoffs. Judge only supplied remaining claim IDs; local checks do not establish fulfillment.",
                },
            }
        final_result = run(remaining, global_context)
        if consume(final_result, remaining, "global"):
            global_receipt = final_result[1]
        check_active(check_current)
    finally:
        cancelled.set()

    judgments = sorted(
        [j for r in accepted for j in r["claim_judgments"]],
        key=lambda j: int(j["claim_id"][1:]),
    )
    missing = sorted(set(units) - {j["claim_id"] for j in judgments})
    unresolved = list(
        dict.fromkeys(
            [s for r in accepted for s in r.get("unresolved", [])]
            + [cid + ": no bound judgment supplied" for cid in missing]
        )
    )
    patches = [p for r in accepted for p in r.get("patches", [])]
    receipt = {
        **(
            global_receipt
            or {
                k: v
                for k, v in final_result[1].items()
                if k in {"reason", "review_model"}
            }
        ),
        "status": (
            "unverified"
            if unresolved or getattr(focused, "omitted", []) or global_receipt is None
            else "corrected" if patches else "no_supported_correction"
        ),
        "fulfillment": (
            global_receipt.get("fulfillment", "unknown")
            if global_receipt
            else "unknown"
        ),
        "claim_unit_version": VERSION,
        "reviewed_parts": {
            str(j["part_index"]): original[j["part_index"]] for j in judgments
        },
        "claim_judgments": judgments,
        "patches": patches,
        "unresolved": unresolved,
        "claim_count": len(units),
        "missing_claim_ids": missing,
        "source_sha256": [text_hash(s) for s in focused],
        "omitted_sources": len(getattr(focused, "omitted", [])),
        "omitted_source_labels": list(getattr(focused, "omitted", [])),
        "progressive_review": {
            "enabled": bool(rows),
            "job_limit": MAX_REVIEW_JOBS,
            "local_job_limit": MAX_LOCAL_JOBS,
            "jobs": jobs,
            "output_token_limit": MAX_REVIEW_JOBS * 4096,
            "full_evidence_bytes": sum(len(s.encode()) for s in focused),
            "evidence_bytes_sent": sum(j["evidence_bytes_sent"] for j in jobs),
            "input_chars": sum(
                (j.get("review_model") or {}).get("input_chars", 0) for j in jobs
            ),
            "job_count": len(jobs),
            "global_review_complete": global_receipt is not None,
        },
    }
    return corrected, receipt
