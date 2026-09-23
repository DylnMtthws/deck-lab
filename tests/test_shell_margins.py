"""Shell margins (DYL-55/59/60) and the shared `data-dl-tip` tooltip primitive.

The header, page content and footer used to carry four independent hard-coded
horizontal inset formulas, so the nav never lined up with the page body. They
now all resolve one shared expression built from `--dl-page-gutter` and
`--dl-page-max`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"

SHELL_RULES = (
    ".dl-nav",
    ".dl-main",
    ".dl-research-main",
    ".dl-library-main",
    ".dl-footer",
)
RESEARCH_RULES = (
    ".dl-research-layout",
    ".dl-research-search",
    ".dl-research-main .dl-page-head",
    "[data-research-tabs]",
)


@pytest.fixture(scope="module")
def css() -> str:
    return CSS_PATH.read_text()


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, f"missing CSS rule for {selector}"
    return match.group(1)


def _media_block(css: str, prelude: str) -> str:
    """Return the body of an @media block, matching nested braces."""
    start = css.index(prelude) + len(prelude)
    depth, index = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        index += 1
    return css[start : index - 1]


# --- DYL-59 / DYL-55: one shared inset ------------------------------------


def test_root_defines_the_layout_tokens(css: str) -> None:
    root = _rule_body(css, ":root")
    assert re.search(r"--dl-page-max:\s*1600px", root)
    assert re.search(r"--dl-page-gutter:\s*50px", root)
    assert re.search(r"--dl-content-max:\s*1320px", root)
    # The pre-existing colour/type tokens are untouched.
    assert re.search(r"--radius:\s*13px", root)
    assert re.search(r"--brand:\s*#e94560", root)


@pytest.mark.parametrize("selector", SHELL_RULES)
def test_shell_rules_share_one_inset(css: str, selector: str) -> None:
    body = _rule_body(css, selector)
    assert "var(--dl-page-gutter)" in body
    assert "var(--dl-page-max)" in body
    assert re.search(
        r"padding-inline:\s*max\(\s*var\(--dl-page-gutter\),\s*"
        r"calc\(\(100vw - var\(--dl-page-max\)\)\s*/\s*2\)\s*\)",
        body,
    ), f"{selector} does not use the shared inset expression"


@pytest.mark.parametrize("selector", SHELL_RULES)
def test_shell_rules_drop_the_hard_coded_tracks(css: str, selector: str) -> None:
    body = _rule_body(css, selector)
    for literal in ("1240px", "1440px"):
        assert literal not in body, f"{selector} still hard-codes {literal}"
    # No horizontal literal survives in a padding shorthand either.
    assert not re.search(r"padding:\s*[^;]*\d+px\s+\d+px", body)


def test_main_no_longer_double_insets(css: str) -> None:
    body = _rule_body(css, ".dl-main")
    assert "min(1240px" not in body
    assert "margin: 0 auto" not in body


def test_footer_divider_matches_the_shared_inset(css: str) -> None:
    body = _rule_body(css, ".dl-footer::before")
    inset = (
        r"max\(var\(--dl-page-gutter\), calc\(\(100vw - var\(--dl-page-max\)\) / 2\)\)"
    )
    assert re.search(r"left:\s*" + inset, body)
    assert re.search(r"right:\s*" + inset, body)
    assert "76px" not in body


def test_builder_shell_is_still_exempt(css: str) -> None:
    body = _rule_body(css, ".dl-main-wide")
    assert re.search(r"padding:\s*0", body)
    assert "var(--dl-page-gutter)" not in body


def test_login_chrome_is_untouched(css: str) -> None:
    """Auth chrome is out of scope for this batch and keeps its own track."""
    body = _rule_body(css, ".dl-auth-nav")
    assert "max(24px,calc((100vw - 1240px)/2))" in body


# --- DYL-60: the research content track ------------------------------------


@pytest.mark.parametrize("selector", RESEARCH_RULES)
def test_research_rules_use_the_content_token(css: str, selector: str) -> None:
    body = _rule_body(css, selector)
    assert "var(--dl-content-max)" in body


def test_research_track_is_left_aligned(css: str) -> None:
    for selector in (".dl-research-layout", ".dl-research-search"):
        assert "margin-inline: 0" in _rule_body(css, selector)


def test_research_main_keeps_overflow_guard(css: str) -> None:
    assert "overflow-x: hidden" in _rule_body(css, ".dl-research-main")


# --- mobile reconciliation --------------------------------------------------


def test_mobile_block_overrides_the_gutter(css: str) -> None:
    block = _media_block(css, "@media (max-width: 767px) {")
    assert re.search(r":root\s*\{\s*--dl-page-gutter:\s*16px", block)


def test_mobile_paddings_do_not_fight_the_token(css: str) -> None:
    """Mobile only restates vertical padding; the gutter comes from the token."""
    block = _media_block(css, "@media (max-width: 767px) {")
    assert ".dl-main{padding-block:24px 86px}" in block
    assert ".dl-library-main{padding-block:24px 100px}" in block
    assert "padding:24px 16px" not in block
    assert "padding:0 16px" not in block


def test_narrow_research_override_uses_the_token(css: str) -> None:
    block = _media_block(css, "@media (max-width: 430px) {")
    assert re.search(r"\.dl-research-main\s*\{[^}]*--dl-page-gutter:\s*14px", block)
    assert "padding-left" not in block


# --- shared tooltip primitive (supports DYL-61 / DYL-65) --------------------


def test_tooltip_bubble_renders_the_attribute(css: str) -> None:
    body = _rule_body(css, "[data-dl-tip]::after")
    assert "content: attr(data-dl-tip)" in body
    assert "visibility: hidden" in body
    assert "opacity: 0" in body
    assert "pointer-events: none" in body
    assert "white-space: nowrap" in body
    match = re.search(r"z-index:\s*(\d+)", body)
    assert match and int(match.group(1)) > 35


def test_tooltip_reveals_on_hover_and_keyboard_focus(css: str) -> None:
    assert "[data-dl-tip]:hover::after" in css
    assert "[data-dl-tip]:focus-visible::after" in css
    body = _rule_body(
        css, "[data-dl-tip]:hover::after,\n[data-dl-tip]:focus-visible::after"
    )
    assert "opacity: 1" in body
    assert "visibility: visible" in body


def test_tooltip_anchors_itself(css: str) -> None:
    assert "position: relative" in _rule_body(css, "[data-dl-tip]")


def test_tooltip_is_suppressed_on_coarse_pointers(css: str) -> None:
    block = _media_block(css, "@media (hover: none) {")
    assert "data-dl-tip" in block
    assert "content: none" in block


def test_tooltip_respects_reduced_motion(css: str) -> None:
    block = _media_block(
        css, "@media (prefers-reduced-motion: reduce) {\n  [data-dl-tip]"
    )
    assert "transition: none" in block
