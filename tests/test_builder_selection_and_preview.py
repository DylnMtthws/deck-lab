"""DYL-65, DYL-66, and DYL-67 builder regressions.

These assertions describe the post-fix behaviour and fail on the production
baseline, where the bulk action bar is a floating overlay that only appears
after a selection exists, the row "view card image" control is an anchor that
opens the raw Scryfall image in a new tab, and no icon-only control carries a
hover tip.

The shared ``[data-dl-tip]`` CSS primitive is delivered by the shell branch, so
these tests assert the attributes only -- never the styling.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
HARNESS = Path(__file__).with_name("builder_selection_harness.mjs")

# Icon-only controls in builder.html: (anchor substring, aria-label, tip).
TEMPLATE_TIPS = (
    ("dl-workspace-back", "Back to Build", "Back to Build"),
    ("dl-workspace-brand", "Deck Lab", "Deck Lab home"),
    ("data-clear-selection", "Clear selection", "Clear selection"),
    ("<summary", "Deck options", "Deck options"),
    ('data-zoom="out"', "Zoom out", "Zoom out"),
    ('data-zoom="in"', "Zoom in", "Zoom in"),
    ("commanders-dialog", "Close commanders", "Close commanders"),
    ("deck-tags-dialog", "Close tags", "Close tags"),
    ("zone-dialog-title", "Close", "Close zone editor"),
    ("delete-zone-title", "Close", "Close delete zone"),
    ("playmat-picker", "Close", "Close playmat settings"),
    ("export-fallback-dialog", "Close copy list", "Close copy list"),
    ("card-image-dialog", "Close card image", "Close card image"),
)

# Icon-only controls built by deck-lab-builder.js: the aria-label expression is
# reused verbatim as the tip expression.
JS_TIP_EXPRESSIONS = (
    '"Remove one " + entry.name',
    '"Add one " + entry.name',
    '"Remove " + entry.name',
    '(isCollapsed ? "Expand " : "Collapse ") + group.name',
    'zone.name + " actions"',
    '"Add a card to " + zone.name',
    '"Clear selection"',
    '"Add " + card.name + " to " + destinationName(addDestinationId())',
)


def _payload() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the builder harness")
    result = subprocess.run(
        [node, str(HARNESS), str(BUILDER_JS)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "builder harness failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# DYL-67 -- the bulk action bar is always visible and disabled at rest.
# --------------------------------------------------------------------------


def test_bulk_bar_ships_visible_and_disabled_in_the_toolbar() -> None:
    html = BUILDER_TEMPLATE.read_text()
    bar = re.search(r'<div class="dl-bulk-controls[^>]*>.*?</div>', html, re.DOTALL)
    assert bar, "missing .dl-bulk-controls group"
    markup = bar.group(0)

    # The container is no longer hidden; every action inside starts disabled.
    assert not re.search(r"\bhidden\b", markup.split(">", 1)[0])
    assert "is-empty" in markup
    assert "desktop-only" in markup and "data-table-only" in markup
    assert "0 cards selected" in markup
    assert re.search(r"data-bulk-zone[^>]*\bdisabled\b", markup)
    assert re.search(r"data-bulk-move\b[^>]*\bdisabled\b", markup)
    assert re.search(r"data-clear-selection[^>]*\bdisabled\b", markup)


def test_bulk_bar_css_is_an_inline_toolbar_group_not_a_floating_overlay() -> None:
    css = CSS_PATH.read_text()
    bodies = re.findall(r"\.dl-bulk-controls\s*\{([^}]*)\}", css)
    assert bodies, "missing .dl-bulk-controls rule"
    joined = ";".join(bodies)
    assert "position:absolute" not in joined.replace(" ", "")
    assert "position:static" in joined.replace(" ", "")
    empty = re.search(r"\.dl-bulk-controls\.is-empty\s*\{([^}]*)\}", css)
    assert empty, "missing muted resting state for .dl-bulk-controls"
    assert "opacity" in empty.group(1)


def test_sync_selection_drives_the_bar_through_zero_one_and_two() -> None:
    payload = _payload()

    zero = payload["zero"]
    assert zero["containerHidden"] is False
    assert "is-empty" in zero["containerClass"]
    assert zero["count"] == "0 cards selected"
    assert zero["move"] is True and zero["zone"] is True and zero["clear"] is True

    one = payload["one"]
    assert one["containerHidden"] is False
    assert "is-empty" not in one["containerClass"]
    assert one["count"] == "1 card selected"
    assert one["move"] is False and one["zone"] is False and one["clear"] is False

    two = payload["two"]
    assert two["containerHidden"] is False
    assert two["count"] == "2 cards selected"
    assert two["move"] is False and two["zone"] is False and two["clear"] is False

    back = payload["backToZero"]
    assert back["containerHidden"] is False
    assert "is-empty" in back["containerClass"]
    assert back["count"] == "0 cards selected"
    assert back["move"] is True and back["zone"] is True and back["clear"] is True


def test_playmat_selection_bar_stays_hidden_until_a_selection_exists() -> None:
    """Deliberate inconsistency: the playmat bar floats over the mat canvas."""
    js = BUILDER_JS.read_text()
    assert "bar.hidden = !selected.length;" in js
    css = CSS_PATH.read_text()
    playmat = re.search(r"\.dl-playmat-selection\s*\{([^}]*)\}", css)
    assert playmat and "position:absolute" in playmat.group(1).replace(" ", "")


# --------------------------------------------------------------------------
# DYL-66 -- view card image opens a modal instead of a new tab.
# --------------------------------------------------------------------------


def test_card_image_dialog_markup_exists_for_owners_and_shared_viewers() -> None:
    html = BUILDER_TEMPLATE.read_text()
    dialog = re.search(
        r'<dialog class="dl-dialog dl-card-image-dialog".*?</dialog>', html, re.DOTALL
    )
    assert dialog, "missing #card-image-dialog"
    markup = dialog.group(0)
    assert 'id="card-image-dialog"' in markup
    assert 'method="dialog"' in markup
    assert "data-card-image-title" in markup
    assert 'aria-labelledby="card-image-title"' in markup
    assert re.search(r"<img data-card-image[^>]*\balt=", markup)
    assert 'loading="lazy"' in markup
    assert "data-card-image-status" in markup  # loading / failure state
    assert re.search(
        r'data-card-image-original[^>]*target="_blank"[^>]*rel="noopener"', markup
    )
    # Shared (read-only) decks render preview controls too, so the dialog must
    # live outside the `{% if not shared %}` block.
    shared_only, _, rest = html.partition("{% if not shared %}")
    body = rest.split("{% endif %}")
    assert "card-image-dialog" in "".join(body[-1:]) or "card-image-dialog" in (
        shared_only
    )


def test_row_preview_is_a_button_that_opens_the_dialog() -> None:
    js = BUILDER_JS.read_text()
    assert 'node("button", "dl-icon-button dl-card-preview", "")' in js
    assert 'preview.target = "_blank"' not in js
    assert 'name.target = "_blank"' not in js
    assert "function openCardImage(name, url, opener)" in js
    assert "dialog.showModal()" in js

    payload = _payload()
    preview = payload["previewMarkup"]
    assert preview["tag"] == "BUTTON"
    assert preview["type"] == "button"
    assert preview["target"] == ""
    assert preview["aria"] == "View card image for Sol Ring"
    assert preview["tip"] == "View card image"

    opened = payload["opened"]
    assert opened["modalCalls"] == 1
    assert opened["title"] == "Sol Ring"
    assert opened["src"] == "https://cards.test/sol-ring.png"
    assert opened["alt"] == "Sol Ring"
    assert opened["original"] == "https://cards.test/sol-ring.png"
    assert opened["imgHidden"] is True
    assert "Loading" in opened["status"]

    assert payload["afterLoad"] == {"imgHidden": False, "statusHidden": True}
    after_error = payload["afterError"]
    assert after_error["imgHidden"] is True
    assert after_error["statusHidden"] is False
    assert "could not be loaded" in after_error["status"]


def test_card_name_link_opens_the_same_dialog() -> None:
    payload = _payload()
    name = payload["nameMarkup"]
    assert name["tag"] == "A"
    assert name["target"] == ""
    assert name["tip"] is None  # visible text: no tooltip
    assert name["aria"] == "Sol Ring. View card image"
    after = payload["afterNameClick"]
    assert after["prevented"] is True
    assert after["modalCalls"] == 2
    assert after["title"] == "Sol Ring"


# --------------------------------------------------------------------------
# DYL-65 -- icon-only controls carry both an aria-label and a hover tip.
# --------------------------------------------------------------------------


def test_template_icon_controls_pair_aria_label_with_a_tip() -> None:
    html = BUILDER_TEMPLATE.read_text()
    for anchor, aria, tip in TEMPLATE_TIPS:
        assert anchor in html, anchor
        pattern = re.compile(
            re.escape(f'aria-label="{aria}"') + r'\s+data-dl-tip="([^"]+)"'
        )
        assert any(match == tip for match in pattern.findall(html)), (anchor, tip)


def test_js_icon_controls_pair_aria_label_with_a_tip() -> None:
    js = BUILDER_JS.read_text()
    for expression in JS_TIP_EXPRESSIONS:
        assert f'setAttribute("aria-label", {expression})' in js, expression
        assert f'setAttribute("data-dl-tip", {expression})' in js, expression
    assert 'preview.setAttribute("data-dl-tip", "View card image")' in js
    assert (
        'preview.setAttribute("aria-label", "View card image for " + entry.name)'
        in (js)
    )


def test_no_tip_is_empty_and_no_labelled_control_lost_its_aria_label() -> None:
    html = BUILDER_TEMPLATE.read_text()
    js = BUILDER_JS.read_text()
    assert 'data-dl-tip=""' not in html
    assert 'setAttribute("data-dl-tip", "")' not in js
    # Every template tip sits on a control that still has an accessible name.
    for match in re.finditer(r"<(?:a|button|summary)\b[^>]*data-dl-tip=[^>]*>", html):
        assert "aria-label=" in match.group(0), match.group(0)
    # Controls with visible text keep their label only -- no duplicate tooltip.
    assert 'data-new-zone aria-label="Create a new zone"><span' in html
    assert "dl-card-name" in js and 'name.setAttribute("data-dl-tip"' not in js


def test_shared_tooltip_primitive_is_defined_exactly_once() -> None:
    """The `[data-dl-tip]` CSS ships once, from the shell work; never duplicated."""
    css = CSS_PATH.read_text()
    assert css.count("content: attr(data-dl-tip);") == 1
    assert "[data-dl-tip]:focus-visible::after" in css


def test_runtime_icon_controls_expose_tips_for_every_labelled_action() -> None:
    tips = _payload()["tips"]
    for label in (
        "Remove one Sol Ring",
        "Add one Sol Ring",
        "Remove Sol Ring",
        "Collapse Unsorted",
        "Unsorted actions",
        "Add a card to Unsorted",
        "Clear selection",
        "Close card image",
    ):
        assert tips.get(label) == label or tips.get(label), label
    assert tips["View card image for Sol Ring"] == "View card image"
