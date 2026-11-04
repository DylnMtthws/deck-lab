"""Deck-plan catalog and explainable goldfish report recommendations.

Pure-Python, no AI or network calls. The catalog describes four fixed
profiles (two commanders x affordable / high-power). Recommendations
interpret a goldfish report conservatively: they explain what to inspect
and never assert a cause, a rate, or a card change.
"""

from __future__ import annotations

import copy
from typing import Any

VIVI_PRIMER_URL = "https://moxfield.com/decks/nBKRWuYyckqPxpCmWBjIBg/primer"
SAURON_PRIMER_URL = "https://moxfield.com/decks/WDJsYexc9EaHELI7vXeHjQ/primer"

_REPLACEMENT_REMINDER = (
    "Keep any deck change to at most three replacements at a time, "
    "then re-run the goldfish tests before changing more."
)

_KNOWN_TESTS = ("opening", "turns", "commander", "wipe", "finish")

_PLANS: dict[str, dict[str, Any]] = {
    "vivi-affordable": {
        "id": "vivi-affordable",
        "title": "Vivi Ornitier — Affordable Spells",
        "commander": "Vivi Ornitier",
        "summary": (
            "Affordable Vivi build around cheap instants and sorceries, "
            "spell-driven damage, and mana conversion. Preserves "
            "affordability; the budget is unknown so no cost claim is made."
        ),
        "opening_priorities": [
            "Keepable mana with an early play available.",
            "Early Vivi Ornitier when the hand supports casting spells.",
            "Cheap instants or sorceries to start the damage and mana engine.",
        ],
        "engine_description": (
            "Casting noncreature spells grows Vivi's power and "
            "converts spell casts into mana for follow-up spells."
        ),
        "finish_routes": [
            "Spell-triggered damage as an intended finish "
            "(intended route, not a verified win).",
            "Continued spell chains while mana conversion sustains casting.",
        ],
        "limitations": [
            "Goldfish only: no opponents, interaction, or combat modeled.",
            "Primer cards may not be present in the fixed list; no automatic "
            "additions and no assumption of coverage.",
            "Affordability is preserved; the budget is unknown so no cost "
            "comparison is made.",
        ],
        "primer_url": "",
    },
    "vivi-high-power": {
        "id": "vivi-high-power",
        "title": "Vivi Ornitier — High Power",
        "commander": "Vivi Ornitier",
        "summary": (
            "High-power Vivi built around the damage and card-draw engine to "
            "find its intended routes. Seeks a Curiosity, Ophidian Eye, or "
            "Tandem Lookout effect, or a tutor or draw spell, in the opening "
            "hand with early Vivi."
        ),
        "opening_priorities": [
            "A Curiosity, Ophidian Eye, or Tandem Lookout effect in the "
            "opening hand.",
            "Alternatively, a tutor or card-draw spell in the opening hand.",
            "Early Vivi Ornitier so damage triggers draw into the engine.",
        ],
        "engine_description": (
            "Vivi's damage triggers paired with a Curiosity-type draw effect "
            "turn noncreature spell casts into cards, digging toward the intended "
            "finish routes."
        ),
        "finish_routes": [
            "Underworld Breach plus Brain Freeze plus Lion's Eye Diamond "
            "(intended route per the primer; NOT a verified executable win).",
            "Hullbreaker Horror loops "
            "(intended route per the primer; NOT a verified executable win).",
        ],
        "limitations": [
            "Finish routes are intended routes from the primer, not verified "
            "executable wins.",
            "The primer describes cards not necessarily present in the fixed "
            "list; no automatic additions and no assumption of coverage.",
            "Goldfish only: no opponents, interaction, or combat modeled.",
        ],
        "primer_url": VIVI_PRIMER_URL,
    },
    "sauron-affordable": {
        "id": "sauron-affordable",
        "title": "Sauron, the Dark Lord — Affordable Theme",
        "commander": "Sauron, the Dark Lord",
        "summary": (
            "Affordable Sauron build preserving the LOTR theme with Army and "
            "Ring development and independent recovery lines."
        ),
        "opening_priorities": [
            "Keepable mana for early plays.",
            "Early Army and Ring-temptation development.",
            "Setup for independent recovery if the board is answered.",
        ],
        "engine_description": (
            "Grixis value built around Amass Army tokens and Ring temptation "
            "progression, with recovery that does not depend on a single piece."
        ),
        "finish_routes": [
            "Army combat pressure developed over the game "
            "(intended route, not a verified win).",
            "Incremental value from Ring temptation and themed payoffs.",
        ],
        "limitations": [
            "Sideboard is kept separate and is not mixed into the main-deck plan.",
            "Goldfish only: opponent spells, combat decisions, and ward are "
            "outside the strict model.",
            "Affordability is preserved; the budget is unknown so no cost "
            "comparison is made.",
        ],
        "primer_url": "",
    },
    "sauron-high-power": {
        "id": "sauron-high-power",
        "title": "Sauron, the Dark Lord — High Power",
        "commander": "Sauron, the Dark Lord",
        "summary": (
            "Adaptable Grixis midrange. Mulligan for mana plus an engine "
            "piece, tutor, or plan; do not force an early commander."
        ),
        "opening_priorities": [
            "Mana plus at least one of: engine piece, tutor, or defined plan.",
            "Do not force an early Sauron, the Dark Lord; the commander is "
            "an alternative advantage route.",
            "A defined route into Oracle plus Consultation-or-Pact or Breach "
            "lines where the hand supports it.",
        ],
        "engine_description": (
            "Midrange card-advantage engine with the commander as an "
            "alternative advantage route rather than a required early play."
        ),
        "finish_routes": [
            "Thassa's Oracle plus Demonic Consultation or Tainted Pact "
            "(intended route).",
            "Underworld Breach lines (intended route).",
            "Commander-led advantage route as the alternative path.",
        ],
        "limitations": [
            "Goldfish only: opponent spells, combat decisions, and ward "
            "triggers are outside the strict model.",
            "Finish routes are intended routes, not verified executable wins.",
            "The primer describes cards not necessarily present in the fixed "
            "list; no automatic additions and no assumption of coverage.",
        ],
        "primer_url": SAURON_PRIMER_URL,
    },
}

_REQUIRED_PLAN_KEYS = frozenset(
    {
        "id",
        "title",
        "commander",
        "summary",
        "opening_priorities",
        "engine_description",
        "finish_routes",
        "limitations",
        "primer_url",
    }
)


def get_plan(plan_id: str) -> dict[str, Any]:
    """Return a deep copy of the catalog entry for ``plan_id``.

    Args:
        plan_id: One of the four catalog ids.

    Returns:
        A deep copy of the plan dict, so callers cannot mutate the catalog.

    Raises:
        ValueError: If ``plan_id`` is not a known profile.
    """
    try:
        plan = _PLANS[plan_id]
    except KeyError as exc:
        raise ValueError(f"unknown goldfish plan: {plan_id!r}") from exc
    return copy.deepcopy(plan)


def list_plans() -> list[dict[str, Any]]:
    """Return deep copies of every catalog plan.

    Returns:
        A list of plan dicts; mutating any of them leaves the catalog intact.
    """
    return [copy.deepcopy(plan) for plan in _PLANS.values()]


def _tests_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Extract the per-test mapping from either report shape.

    Accepts ``{"tests": {...}}`` or a bare ``{test_name: entry}`` mapping so
    callers are not punished for a thin wrapper difference.

    Args:
        report: The goldfish report dict.

    Returns:
        The mapping of test name to ``{status, metrics, limitations}`` entry.
    """
    tests = report.get("tests", report)
    if not isinstance(tests, dict):
        return {}
    return tests


def _coverage_recommendation(test: str, entry: dict[str, Any]) -> dict[str, str]:
    """Build a coverage-only recommendation for an unmodeled test.

    Args:
        test: Test name (opening, turns, commander, wipe, finish).
        entry: The test entry with status, metrics, and limitations.

    Returns:
        A recommendation dict describing the coverage gap only.
    """
    limitations = entry.get("limitations", []) or []
    if limitations:
        detail = " Noted limits: " + "; ".join(str(item) for item in limitations) + "."
    else:
        detail = ""
    return {
        "test": test,
        "title": f"{test} is not covered by the goldfish model",
        "reason": (
            f"The {test} test status is unsupported, so this report draws no "
            f"conclusion about the deck from it.{detail} This is a gap in "
            "measurement, not evidence about deck quality."
        ),
        "next_step": (
            f"Inspect {test} by hand outside the goldfish run (opening hands, "
            "sequencing notes, or a manual play sample) if that area matters "
            f"to you. {_REPLACEMENT_REMINDER}"
        ),
    }


def _inspection_recommendation(
    test: str, entry: dict[str, Any], failure_count: int
) -> dict[str, str]:
    """Build an observed-failure inspection recommendation.

    Quotes only counts already present in the report; never computes rates
    and never asserts a cause.

    Args:
        test: Test name.
        entry: The test entry with metrics and limitations.
        failure_count: Observed failure count from the metrics.

    Returns:
        A recommendation dict pointing at what to inspect.
    """
    metrics = entry.get("metrics", {}) or {}
    total = metrics.get("total_count", metrics.get("sample_size"))
    if total is None:
        observed = (
            f"{failure_count} observed hand(s) did not meet the modeled criterion."
        )
    else:
        observed = (
            f"{failure_count} of {total} observed hand(s) did not meet the "
            "modeled criterion."
        )
    if test == "opening":
        focus = "Review the failing hands for mana counts and early actions present."
    elif test == "turns":
        focus = "Review the failing sequences for mana and sequencing present in each."
    elif test == "commander":
        focus = "Review the observed commander timing and board presence in each case."
    elif test == "wipe":
        focus = "Review the observed recovery sequences present after the wipe."
    else:
        focus = f"Review what was observed in each {test} case."
    return {
        "test": test,
        "title": f"{test} shows observed failures — inspect the failing cases",
        "reason": (
            f"The {test} test recorded {observed} This describes observed "
            "goldfish cases only and does not assert why they occurred."
        ),
        "next_step": f"{focus} {_REPLACEMENT_REMINDER}",
    }


def recommendations(report: dict[str, Any]) -> list[dict[str, str]]:
    """Derive conservative, explainable recommendations from a report.

    Rules:
      * ``unsupported`` tests yield a coverage-only recommendation, never a
        deck verdict.
      * ``measured`` or ``partial`` tests yield a recommendation only when
        ``metrics["failure_count"]`` is positive; the text points at what to
        inspect and never asserts a cause.
      * No card is ever named, recommended, or recommended for removal, and no
        rate is computed or quoted beyond counts already in the report.

    Args:
        report: Mapping with a ``tests`` mapping of test name to
            ``{status, metrics, limitations}`` entries, or the bare test
            mapping itself. Each entry's ``status`` is one of ``measured``,
            ``partial``, or ``unsupported``.

    Returns:
        A list of ``{test, title, reason, next_step}`` dicts.
    """
    tests = _tests_from_report(report)
    recs: list[dict[str, str]] = []
    for test in _KNOWN_TESTS:
        entry = tests.get(test)
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        metrics = entry.get("metrics", {}) or {}
        if status == "unsupported":
            recs.append(_coverage_recommendation(test, entry))
            continue
        if status in ("measured", "partial"):
            if status == "partial":
                recs.append(
                    {
                        "test": test,
                        "title": f"{test} has incomplete coverage",
                        "reason": "Only supported mechanics were measured. Missing mechanics can change these results. "
                        + "; ".join(str(item) for item in entry.get("limitations", [])),
                        "next_step": "Inspect the coverage and traces before interpreting these measurements. "
                        + _REPLACEMENT_REMINDER,
                    }
                )
            failure_count = metrics.get("failure_count", 0)
            try:
                failures = int(failure_count)
            except (TypeError, ValueError):
                failures = 0
            if failures > 0:
                recs.append(_inspection_recommendation(test, entry, failures))
            continue
        # Unknown status: stay conservative and report a coverage gap rather
        # than guessing at deck quality.
        recs.append(_coverage_recommendation(test, entry))
    return recs
