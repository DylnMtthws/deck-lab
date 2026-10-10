"""Targeted tests for the goldfish plan catalog and recommendations."""

from sabermetrics.goldfish_plans import get_plan, list_plans, recommendations

EXPECTED_IDS = {
    "vivi-affordable",
    "vivi-high-power",
    "sauron-affordable",
    "sauron-high-power",
}

REQUIRED_KEYS = {
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

VIVI_PRIMER = "https://moxfield.com/decks/nBKRWuYyckqPxpCmWBjIBg/primer"
SAURON_PRIMER = "https://moxfield.com/decks/WDJsYexc9EaHELI7vXeHjQ/primer"


def test_catalog_has_four_distinct_profiles():
    plans = list_plans()
    assert {p["id"] for p in plans} == EXPECTED_IDS
    assert len({p["title"] for p in plans}) == 4
    assert len({p["summary"] for p in plans}) == 4
    for plan in plans:
        assert REQUIRED_KEYS <= set(plan)
        assert plan["opening_priorities"]
        assert plan["finish_routes"]
        assert plan["limitations"]


def test_source_links_preserved():
    assert get_plan("vivi-high-power")["primer_url"] == VIVI_PRIMER
    assert get_plan("sauron-high-power")["primer_url"] == SAURON_PRIMER
    # Affordable profiles were given no primer link; nothing may be invented.
    assert get_plan("vivi-affordable")["primer_url"] == ""
    assert get_plan("sauron-affordable")["primer_url"] == ""


def test_get_plan_mutation_isolation():
    plan = get_plan("vivi-high-power")
    plan["title"] = "MUTATED"
    plan["finish_routes"].append("MUTATED")
    fresh = get_plan("vivi-high-power")
    assert fresh["title"] != "MUTATED"
    assert "MUTATED" not in fresh["finish_routes"]


def test_list_plans_mutation_isolation():
    plans = list_plans()
    plans[0]["summary"] = "MUTATED"
    assert get_plan(plans[0]["id"])["summary"] != "MUTATED"


def test_unknown_profile_rejected():
    try:
        get_plan("no-such-plan")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown plan id")


def _unsupported_report():
    return {
        "tests": {
            name: {
                "status": "unsupported",
                "metrics": {},
                "limitations": [f"{name} not modeled"],
            }
            for name in ("opening", "turns", "commander", "wipe", "finish")
        }
    }


def test_unsupported_produce_coverage_only_recommendations():
    recs = recommendations(_unsupported_report())
    assert len(recs) == 5
    banned = ("deck fails", "deck failure", "weak deck", "bad deck", "remove", "cut it")
    for rec in recs:
        assert set(rec) == {"test", "title", "reason", "next_step"}
        text = (rec["title"] + " " + rec["reason"]).lower()
        assert "not covered" in text or "no conclusion" in text
        for phrase in banned:
            assert phrase not in text
        assert "three replacements" in rec["next_step"].lower()


def test_opening_failures_suggest_mana_and_early_action_inspection():
    report = {
        "tests": {
            "opening": {
                "status": "measured",
                "metrics": {"failure_count": 3, "total_count": 10},
                "limitations": [],
            }
        }
    }
    recs = recommendations(report)
    assert len(recs) == 1
    assert recs[0]["test"] == "opening"
    combined = (recs[0]["reason"] + " " + recs[0]["next_step"]).lower()
    assert "mana" in combined
    assert "early action" in combined
    assert "three replacements" in recs[0]["next_step"].lower()


def test_no_recommendation_without_observed_failures():
    report = {
        "tests": {
            "opening": {
                "status": "measured",
                "metrics": {"failure_count": 0},
                "limitations": [],
            },
            "turns": {"status": "partial", "metrics": {}, "limitations": []},
        }
    }
    recs = recommendations(report)
    assert len(recs) == 1
    assert "incomplete coverage" in recs[0]["title"]


def test_partial_failures_also_suggest_inspection():
    report = {
        "tests": {
            "turns": {
                "status": "partial",
                "metrics": {"failure_count": 2},
                "limitations": [],
            },
        }
    }
    recs = recommendations(report)
    assert len(recs) == 2
    assert recs[0]["test"] == "turns"
    assert "three replacements" in recs[0]["next_step"].lower()


def test_recommendations_never_name_cards_or_rates():
    report = {
        "tests": {
            "opening": {
                "status": "measured",
                "metrics": {"failure_count": 1, "total_count": 5},
                "limitations": [],
            },
            "finish": {"status": "unsupported", "metrics": {}, "limitations": []},
        }
    }
    recs = recommendations(report)
    joined = " ".join(
        r["title"] + " " + r["reason"] + " " + r["next_step"] for r in recs
    )
    for card in ("Vivi", "Sauron", "Breach", "Oracle", "Curiosity", "Hullbreaker"):
        assert card.lower() not in joined.lower()
    assert "%" not in joined
    assert "remove" not in joined.lower()


def test_no_budgets_or_brackets_in_catalog_or_recommendations():
    for plan in list_plans():
        joined = " ".join(str(v) for v in plan.values()).lower()
        assert "$" not in joined
        assert "bracket" not in joined
    recs = recommendations(
        {
            "tests": {
                "opening": {
                    "status": "measured",
                    "metrics": {"failure_count": 1},
                    "limitations": [],
                }
            }
        }
    )
    joined = " ".join(
        r["title"] + " " + r["reason"] + " " + r["next_step"] for r in recs
    ).lower()
    assert "$" not in joined
    assert "bracket" not in joined


def test_vivi_high_power_routes_not_verified_wins():
    plan = get_plan("vivi-high-power")
    joined = " ".join(plan["finish_routes"]).lower()
    assert "not a verified" in joined or "not verified" in joined


def test_reference_commanders_are_exact():
    from sabermetrics.goldfish_plans import get_plan

    for variant in ("affordable", "high-power"):
        assert get_plan("sauron-" + variant)["commander"] == "Sauron, the Dark Lord"
        assert get_plan("vivi-" + variant)["commander"] == "Vivi Ornitier"
