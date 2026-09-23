"""DYL-68, DYL-70, and DYL-71 builder chrome regressions.

These assertions describe the post-fix markup and CSS. They fail on the
production baseline, where the toolbar still duplicates row density, the
playmat view still floats a fourth New zone button, and playmat-picker
checkboxes still stack under `.dl-dialog label { display: grid }`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
JS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)

# `.dl-density-comfortable` is the decklist row-height modifier, not the
# removed toolbar control. Match the class token only.
_DL_DENSITY_CLASS = re.compile(r"(?<![\w-])\.dl-density(?![\w-])")


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, f"missing CSS rule for {selector}"
    return match.group(1)


def test_floating_new_zone_button_is_removed() -> None:
    """DYL-71: keep toolbar, kebab, and zones-rail triggers only."""
    html = BUILDER_TEMPLATE.read_text()
    css = CSS_PATH.read_text()
    js = JS_PATH.read_text()

    assert "dl-new-zone-floating" not in html
    assert "dl-new-zone-floating" not in css
    assert "dl-new-zone-floating" not in js
    assert html.count("data-new-zone") == 3
    assert (
        '<button class="dl-button dl-toolbar-action" type="button" data-new-zone'
        in html
    )
    assert "data-new-zone>New zone</button>" in html
    assert 'class="dl-icon-button dl-add-zone" type="button" data-new-zone' in html
    assert 'aria-label="Create a new zone"' in html
    assert 'aria-label="Add zone"' in html
    assert ".dl-new-zone-floating" not in css
    assert 'closest(".dl-mat-zone,.dl-zoom,.dl-playmat-selection")' in js


def test_row_density_control_lives_only_in_deck_options() -> None:
    """DYL-68: drop the toolbar density segments; kebab still toggles."""
    html = BUILDER_TEMPLATE.read_text()
    css = CSS_PATH.read_text()
    js = JS_PATH.read_text()

    assert "dl-density" not in html
    assert ">Rows</span>" not in html
    assert 'aria-label="Row density"' not in html
    assert not _DL_DENSITY_CLASS.search(css)
    assert ".dl-density-comfortable .dl-deck-row" in css
    assert re.search(
        r'<details class="dl-decklist-more">[\s\S]*?'
        r'<span class="dl-decklist-more-label">Row density</span>'
        r'<button type="button" data-density="compact" aria-pressed="true">'
        r"Compact</button>"
        r'<button type="button" data-density="comfortable" aria-pressed="false">'
        r"Comfortable</button>",
        html,
    )
    assert html.count('data-density="compact"') == 1
    assert html.count('data-density="comfortable"') == 1
    assert (
        'document.querySelectorAll("[data-density]").forEach(function (button) '
        '{ button.setAttribute("aria-pressed", button.dataset.density === density '
        '? "true" : "false"); })' in js
    )


def test_playmat_picker_checkboxes_align_inline() -> None:
    """DYL-70: checkbox labels opt into a row layout; other dialogs stay stacked."""
    html = BUILDER_TEMPLATE.read_text()
    css = CSS_PATH.read_text()

    group = re.search(
        r'<div class="dl-checkbox-group">([\s\S]*?)</div>',
        html,
    )
    assert group, "playmat picker is missing .dl-checkbox-group"
    body = group.group(1)
    for setting in ("snap_to_grid", "show_zone_outlines", "dim_inactive"):
        assert f'<input type="checkbox" data-setting="{setting}"' in body
    assert body.count("<label>") == 3

    label_rule = _rule_body(css, ".dl-checkbox-group label")
    assert re.search(r"display\s*:\s*flex", label_rule)
    assert re.search(r"align-items\s*:\s*center", label_rule)
    assert re.search(r"gap\s*:\s*8px", label_rule)
    assert re.search(r"font-size\s*:\s*12px", label_rule)
    assert "outline" not in label_rule

    group_rule = _rule_body(css, ".dl-checkbox-group")
    assert re.search(r"gap\s*:\s*8px", group_rule)
    assert re.search(r"padding-block\s*:\s*0", group_rule)

    base = _rule_body(css, ".dl-dialog label")
    assert base == "display:grid;gap:7px;font-size:12px"
    assert ":focus-visible { outline: 2px solid var(--brand-text);" in css
