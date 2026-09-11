"""Turn a labelling workflow's journal into the checked-in rules-support labels.

    python scripts/write_rules_support_labels.py --journal <path>/journal.jsonl

The derivation is a script rather than a one-off edit so that the provenance
chain is auditable: these labels are an answer key, and "where did this come
from" must have an answer better than "an agent said so once".

Two things it does NOT do. It does not invent quotes for near misses — an entry
whose analysis is prose stays prose, is written without a quote, and is reported
by the matcher as unevaluable rather than as absent. And it does not mark
anything reviewed: the set is written ``proposed`` and every downstream gate
treats it as unratified until the owner says otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "fixtures" / "research" / "rules_support_labels.yaml"
DEFAULT_SOURCE = ROOT / "data" / "reference" / "comprehensive_rules"
#: "605.3b — it reaches the right conclusion via the wrong branch"
_NEAR_MISS = re.compile(
    r"^\s*([\d][\d.]*[a-z]?(?:\s*/\s*[\d][\d.]*[a-z]?)*)\s*[—–-]\s*(.+)$", re.DOTALL
)


def _near_misses(entries: list[Any]) -> list[dict[str, Any]]:
    """Split "<rule> — <why>" prose into structured, quote-less annotations.

    An entry naming two rules ("707.9b / 707.9d — ...") becomes two entries
    sharing one explanation, because a reviewer adjudicates one rule at a time.
    """
    parsed: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            parsed.append(entry)
            continue
        match = _NEAR_MISS.match(str(entry))
        if match is None:
            # No leading rule number: keep the analysis rather than drop it.
            parsed.append({"rule": "unnumbered", "why": str(entry).strip()})
            continue
        rules = [part.strip() for part in match.group(1).split("/") if part.strip()]
        why = " ".join(match.group(2).split())
        for rule in rules:
            parsed.append({"rule": rule, "why": why})
    return parsed


def main() -> int:
    """Write the label set from the journal's reconciled results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--label-set", required=True)
    parser.add_argument("--labelled-on", required=True)
    parser.add_argument("--effective-date", default="")
    args = parser.parse_args()

    passes: dict[str, list[dict[str, Any]]] = {}
    for line in args.journal.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") != "result":
            continue
        result = record.get("result")
        if isinstance(result, dict) and "required_rules" in result:
            passes.setdefault(str(result["question_id"]), []).append(result)
    if not passes:
        raise SystemExit("LABELS REFUSED: no label-shaped results in that journal")

    effective = args.effective_date
    if not effective:
        dated = sorted(path.name for path in DEFAULT_SOURCE.glob("*/source.json"))
        candidates = sorted(p.parent.name for p in DEFAULT_SOURCE.glob("*/source.json"))
        if not candidates:
            raise SystemExit("LABELS REFUSED: no provisioned rules source")
        effective = candidates[-1]
        del dated
    source = json.loads(
        (DEFAULT_SOURCE / effective / "source.json").read_text(encoding="utf-8")
    )

    labels = []
    unreviewed = []
    for question_id in sorted(passes):
        history = passes[question_id]
        final = history[-1]
        reconciled = len(history) >= 2
        if not reconciled:
            unreviewed.append(question_id)
        # A rule that is both an answer and a trap is a contradiction, and
        # reconciliation produced one: rules-006 promoted 616.1c into the
        # alternatives and left it in the near misses. The answer side wins,
        # because it is the later and considered decision — and the conflict is
        # written into the rationale rather than quietly dropped, since a
        # reviewer adjudicating this label needs to know it happened.
        scored = set(final["required_rules"]) | set(final["sufficient_any_of"])
        near = [
            entry
            for entry in _near_misses(list(final["near_miss_rules"]))
            if entry["rule"] not in scored
        ]
        conflicts = sorted(
            {
                entry["rule"]
                for entry in _near_misses(list(final["near_miss_rules"]))
                if entry["rule"] in scored
            }
        )
        rationale = str(final["rationale"]).strip()
        if conflicts:
            rationale += (
                "\n\nDERIVATION NOTE: reconciliation left "
                + ", ".join(conflicts)
                + " listed both as an answer rule and as a near miss. The "
                "answer side was kept, being the later decision, and the near "
                "miss entry was dropped. A reviewer should confirm that is the "
                "right way round."
            )
        labels.append(
            {
                "question_id": question_id,
                "required_rules": list(final["required_rules"]),
                "sufficient_any_of": list(final["sufficient_any_of"]),
                "sufficient_support": str(final["sufficient_support"]).strip(),
                "near_miss_rules": near,
                "quoted_evidence": [
                    {
                        "rule": str(entry["rule"]),
                        "quote": str(entry["quote"]).strip(),
                        "why": " ".join(str(entry["why"]).split()),
                    }
                    for entry in final["quoted_evidence"]
                ],
                "rationale": rationale,
                "confidence": str(final["confidence"]),
                "verification": (
                    "adversarially_reconciled" if reconciled else "proposed_unreviewed"
                ),
            }
        )

    payload = {
        "schema_version": "research-rules-support-labels.v1",
        "label_set": args.label_set,
        "status": "proposed",
        "derived_from": {
            "document": source["document"],
            "effective_date": source["effective_date"],
            "content_sha256": source["content_sha256"],
        },
        "labelled_by": "Claude agent panel (10 labellers, 3 adversarial lenses each)",
        "labelled_on": args.labelled_on,
        "independence": (
            "Derived from the question text and the pinned Comprehensive Rules "
            "only. Every labeller and reviewer was denied .research-dev/, the "
            "owner-review packet, the reference index database and the index "
            "manifest, because an answer key read off the system it scores "
            "measures that system against itself. Quotes are checked against "
            "the pinned document mechanically; independence is not."
        ),
        # False for this round: it was labelled before the quote requirement
        # existed, so its near misses are annotations and the verdicts report
        # them as unevaluable rather than as absent.
        "near_miss_quotes_required": False,
        "labels": labels,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=88),
        encoding="utf-8",
    )
    print(f"wrote {len(labels)} labels to {args.output}")
    if unreviewed:
        print(
            "NOT adversarially reconciled (marked proposed_unreviewed): "
            + ", ".join(unreviewed)
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"LABELS REFUSED: {exc}")
        raise SystemExit(2) from None
