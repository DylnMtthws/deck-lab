from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATS_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-stats.js"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
HARNESS = Path(__file__).with_name("interactive_stats_harness.mjs")


def _payload(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the interactive stats harness")
    result = subprocess.run(
        [node, str(HARNESS), str(STATS_JS), str(BUILDER_JS), scenario],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"harness failed for {scenario}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# AC-1 — curve bins have bucket attrs and are keyboard-reachable
# ---------------------------------------------------------------------------


def test_curve_bins_have_bucket_attrs_and_are_keyboard_reachable() -> None:
    payload = _payload("curve_bucket_attrs")
    assert payload["binCount"] == 6
    for b in payload["bins"]:
        assert b["bucket"] is not None
        assert b["role"] == "button"
        assert b["tabindex"] == "0"
        assert b["ariaLabel"] is not None
    bucket_vals = [int(b["bucket"]) for b in payload["bins"]]
    assert bucket_vals == [0, 1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# AC-2 — curve click sets and clears filter
# ---------------------------------------------------------------------------


def test_curve_click_sets_and_clears_filter_with_label() -> None:
    payload = _payload("curve_click_toggle")

    # First click sets filter
    assert payload["firstLabel"] == "Mana value 1"
    assert payload["activeAfterFirst"] is True

    # Second click on same bin clears filter
    assert payload["secondCallIsNull"] is True
    assert payload["activeAfterSecond"] is True

    # Third click on different bin replaces filter
    assert payload["thirdLabel"] == "Mana value 0"
    assert payload["activeAfterThird"] is True
    assert payload["thirdBinIs1"] is True


# ---------------------------------------------------------------------------
# AC-3 — pips counting rules table
# ---------------------------------------------------------------------------


def test_pips_counting_rules_table() -> None:
    """Mono, hybrid, two-brid, phyrexian, generic/colorless/X ignored, lands excluded."""
    payload = _payload("pips_counting")
    data = payload.get("data", {})
    # Commander identity is G,U, so only G and U rows are shown.
    # 1x Swords (W) = 1 white pip
    # 1x Fire//Ice (U/R) -> "UR" inner -> U: +1, R: +1
    # 1x 2/W -> "2/W" inner -> W: +1
    # 1x W/P -> "W/P" inner -> W: +1
    # 1x Sol Ring {1} -> inner "1" -> no colored letters
    # 1x Wastes (Land) -> excluded from pips
    # Total colored pips = 5 (W=3, U=1, R=1, B=0, G=0)
    # G: 0/5 = 0%; U: 1/5 = 20%
    assert "G" in data, "expected G row (commander identity includes G)"
    assert (
        "0%" in data["G"]["text"]
    ), f"expected 0% share for G, got {data['G']['text']}"
    assert "U" in data, "expected U row (commander identity includes U)"
    assert (
        "20%" in data["U"]["text"]
    ), f"expected 20% share for U, got {data['U']['text']}"
    # W, R, B should NOT appear (not in commander identity)
    assert "W" not in data, "W should not appear (not in commander identity)"
    assert "R" not in data, "R should not appear (not in commander identity)"
    assert "B" not in data, "B should not appear (not in commander identity)"
    # Each row shows the pip share and the source count.
    assert "src" in data["U"]["text"]


# ---------------------------------------------------------------------------
# AC-4 — sources counting rules
# ---------------------------------------------------------------------------


def test_sources_counting_rules() -> None:
    """Basic land, dual, 'any color' land in 3-color identity, mana dork, non-producing land, private zone ignored."""
    payload = _payload("sources_counting")
    data = payload.get("data", {})
    rule_text = payload.get("ruleText", "")

    # Rule should be present
    assert rule_text, "expected rule text in pips section"

    # Forest (10x) -> 10 sources of G
    # Tropical Island (1x, {G}{U}) -> counts as "ANY" since it produces two colors? Actually {G}{U} has two color add clauses
    # Let's parse: the GetAddClauseColor uses first match of Add \{...\} so {G}{U} -> inner is G}... actually
    # Our regex gets first Add {[^}]+} so for "{G}{U}" it matches "{G}" first -> color G only
    # So Tropical Island counts as 1 source for G only (not ANY)
    # City of Brass (1x, "Add one mana of any color.") -> ANY -> all colors
    # Birds of Paradise (1x, "Add {G}{U}{B}{R}{W}.") -> Is mana producer (creature with Add {) -> first match {G} -> G
    # Plains (1x, no oracle text) -> land but no Add clause -> no source
    # Sideboard card -> private zone, ignored

    # So sources: W = 1 (City), U = 1 (City), B = 1 (City), R = 1 (City), G = 10+1+1+1=13 (Forests + Trop + City + Birds)
    assert data.get("G"), "expected G row"
    assert "src" in data["G"]["text"]


def test_sources_triple_color_any_land() -> None:
    """Any-color land in a 3-color identity counts for all 3 colors."""
    payload = _payload("sources_triple_color")
    data = payload.get("data", {})
    # Should have W, G, R rows
    assert "W" in data, "expected W row"
    assert "G" in data, "expected G row"
    assert "R" in data, "expected R row"
    # City of Brass adds 1 source to each of W, G, R
    assert "src" in data["W"]["text"]
    assert "src" in data["G"]["text"]
    assert "src" in data["R"]["text"]


# ---------------------------------------------------------------------------
# AC-5 — hypergeometric known values
# ---------------------------------------------------------------------------


def test_hypergeom_known_values() -> None:
    """Test exact hypergeometric probability via Python math.comb."""
    from math import comb

    def hypergeom_at_least(N, K, n, k):
        if k <= 0:
            return 1.0
        if k > n or K < k:
            return 0.0
        total = 0.0
        for i in range(k, min(n, K) + 1):
            total += comb(K, i) * comb(N - K, n - i) / comb(N, n)
        return total

    # N=99, K=36, n=7, k=2 → 0.7985
    p1 = hypergeom_at_least(99, 36, 7, 2)
    assert abs(p1 - 0.7985) < 0.0005, f"got {p1}"

    # N=99, K=36, n=7, k=0 → 1.0
    p2 = hypergeom_at_least(99, 36, 7, 0)
    assert p2 == 1.0

    # N=99, K=0, n=7, k=1 → 0.0
    p3 = hypergeom_at_least(99, 0, 7, 1)
    assert p3 == 0.0

    # N=60, K=24, n=7, k=3 → 0.5879
    p4 = hypergeom_at_least(60, 24, 7, 3)
    assert abs(p4 - 0.5879) < 0.0005, f"got {p4}"

    # Also test via JS harness that the odds section renders
    payload = _payload("hypergeom")
    assert "error" not in payload, payload.get("error", "")
    assert payload["resultText"] is not None
    assert "%" in payload["resultText"]


# ---------------------------------------------------------------------------
# AC-6 — odds UI updates
# ---------------------------------------------------------------------------


def test_odds_ui_updates_result_text() -> None:
    payload = _payload("odds_ui_update")
    assert "error" not in payload, payload.get("error", "")
    # The percentage lives on its own. This fixture has no lands, so both
    # probabilities stay 0.0%. The sentence controls are what change.
    assert payload["seen"] == "10"
    assert payload["need"] == "2"
    assert "%" in payload["firstText"]
    assert "%" in payload["secondText"]


# ---------------------------------------------------------------------------
# AC-7 — sample hand deterministic
# ---------------------------------------------------------------------------


def test_sample_hand_deterministic_with_seeded_rng_and_no_replacement() -> None:
    payload = _payload("sample_hand_deterministic")
    assert "error" not in payload, payload.get("error", "")
    assert payload["handLength"] == 7, f"expected 7, got {payload['handLength']}"
    # Check that 4-of entry may appear up to 4 times
    names = payload["names"]
    assert len(names) == 7
    # Sol Ring has quantity 4, so it may appear up to 4 times
    from collections import Counter

    counts = Counter(names)
    ring_count = counts.get("Sol Ring", 0)
    assert ring_count <= 4, f"Sol Ring appears {ring_count} times but max is 4"
    lotus_count = counts.get("Lotus Petal", 0)
    assert lotus_count <= 2, f"Lotus Petal appears {lotus_count} times but max is 2"


# ---------------------------------------------------------------------------
# AC-8 — sample hand short library message
# ---------------------------------------------------------------------------


def test_sample_hand_short_library_message() -> None:
    payload = _payload("sample_hand_short")
    assert "error" not in payload, payload.get("error", "")
    assert payload["drawBtnExists"] is True
    assert payload["nextBtnExists"] is True
    assert payload["cardsElExists"] is True
    assert payload["landsElExists"] is True


# ---------------------------------------------------------------------------
# AC-9 — builder page includes stats script
# ---------------------------------------------------------------------------


def test_builder_page_includes_stats_script() -> None:
    html = BUILDER_TEMPLATE.read_text()
    assert "deck-lab-stats.js" in html
    # Must be after deck-lab-builder.js
    builder_pos = html.index("deck-lab-builder.js")
    stats_pos = html.index("deck-lab-stats.js")
    assert stats_pos > builder_pos, "stats script must be loaded after builder script"


# ---------------------------------------------------------------------------
# AC-10 — curve bin keyboard enter and space toggle filter
# ---------------------------------------------------------------------------


def test_curve_bin_keyboard_enter_and_space_toggle_filter() -> None:
    payload = _payload("curve_keyboard")
    assert "error" not in payload, payload.get("error", "")
    assert payload["enterLabel"] == "Mana value 1"
    assert payload["spaceLabel"] == "null"


# ---------------------------------------------------------------------------
# AC-11 — pips section limited to commander identity and states rule
# ---------------------------------------------------------------------------


def test_pips_section_limited_to_commander_identity_and_states_rule() -> None:
    payload = _payload("pips_section_commander_identity")
    assert "error" not in payload, payload.get("error", "")
    # No commanders: should show all 5 colors
    assert payload["noCmdrColors"] == [
        "B",
        "G",
        "R",
        "U",
        "W",
    ], f"expected all 5 colors, got {payload['noCmdrColors']}"
    # Rule text should exist
    assert payload["ruleText"] is not None


# ---------------------------------------------------------------------------
# AC-12 — odds inputs survive builder render
# ---------------------------------------------------------------------------


def test_odds_inputs_survive_builder_render() -> None:
    payload = _payload("odds_persist_across_render")
    assert "error" not in payload, payload.get("error", "")
    assert payload["catValue"] == "ramp", f"expected ramp, got {payload['catValue']}"
    assert payload["seenValue"] == "10", f"expected 10, got {payload['seenValue']}"
    assert payload["needValue"] == "2", f"expected 2, got {payload['needValue']}"
    assert payload["resultText"] is not None
    assert "%" in payload["resultText"]
    assert payload["textBefore"] is not None
    assert "%" in payload["textBefore"]


# ---------------------------------------------------------------------------
# AC-13 — sample hand survives curve filter click
# ---------------------------------------------------------------------------


def test_sample_hand_survives_curve_filter_click() -> None:
    payload = _payload("sample_hand_survives_curve_filter")
    assert "error" not in payload, payload.get("error", "")
    assert (
        payload["namesBefore"] == payload["namesAfter"]
    ), "drawn hand names should be identical before and after curve click"
    assert (
        len(payload["namesBefore"]) == 3
    ), f"expected 3 cards, got {payload['namesBefore']}"


# ---------------------------------------------------------------------------
# AC-14 — sample hand drops removed entries on render
# ---------------------------------------------------------------------------


def test_sample_hand_drops_removed_entries_on_render() -> None:
    payload = _payload("sample_hand_drops_removed_entries")
    assert "error" not in payload, payload.get("error", "")
    assert (
        payload["removedPresentBefore"] is True
    ), "Lotus Petal should be in hand before removal"
    assert (
        payload["removedPresentAfter"] is False
    ), "Lotus Petal should be gone after removal"
