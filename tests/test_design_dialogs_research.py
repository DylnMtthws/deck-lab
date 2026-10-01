"""D07 — Shortcuts and comment dialogs; Research query chips."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
FEEDBACK_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-feedback.js"
RESEARCH_FRAGMENT = (
    ROOT
    / "src"
    / "sabermetrics"
    / "ui"
    / "templates"
    / "deck_lab"
    / "research_fragment.html"
)
RESEARCH_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-research.js"
RESEARCH_ROUTES = ROOT / "src" / "sabermetrics" / "ui" / "research_routes.py"
DIALOGS_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-dialogs.css"

# ---------------------------------------------------------------------------
# AC-1: Keyboard shortcuts dialog is a two-column table with grouped keycaps
# ---------------------------------------------------------------------------


def test_shortcuts_dialog_two_column_table_with_grouped_keycaps() -> None:
    html = BUILDER_TEMPLATE.read_text()
    dialog = re.search(
        r"<dialog[^>]*data-hotkeys-help[^>]*>.*?</dialog>",
        html,
        re.DOTALL,
    )
    assert dialog, "hotkeys-help dialog not found in builder.html"
    markup = dialog.group(0)

    # Must use a <table> with a <thead> and <tbody>
    assert '<table class="dl-hotkeys-table">' in markup
    assert "<thead>" in markup and "<tbody>" in markup

    # Every T09 key listed — 12 rows in tbody
    rows = re.findall(r"<tr>.*?</tr>", markup, re.DOTALL)
    body_rows = [r for r in rows if "<th" not in r]
    assert len(body_rows) == 12, f"expected 12 shortcut rows, got {len(body_rows)}"

    # Grouped alternatives use <kbd> with the "or" separator
    assert "Undo" in markup
    assert "<kbd>⌘ Z</kbd>" in markup
    assert '<span class="dl-hotkeys-or">or</span>' in markup
    assert "<kbd>Ctrl Z</kbd>" in markup

    # Redo has three alternatives with two "or" separators
    assert "Redo" in markup
    assert markup.count('<span class="dl-hotkeys-or">or</span>') >= 3

    # All status bar / action keys are present
    assert "<kbd>/</kbd>" in markup  # Focus search
    assert "<kbd>m</kbd>" in markup  # Zone menu
    assert "<kbd>?</kbd>" in markup  # Show shortcuts
    assert "<kbd>Delete</kbd>" in markup
    assert "<kbd>Backspace</kbd>" in markup

    # Done button present
    assert "Done" in markup


# ---------------------------------------------------------------------------
# AC-2: Comment dialog has card name in title and styled controls
# ---------------------------------------------------------------------------


def test_comment_dialog_title_names_card_and_styled_controls() -> None:
    js = FEEDBACK_JS.read_text()

    # Dialog title uses card name from activeCommentCardName
    assert 'title.textContent = "Comment on " + (activeCommentCardName || "card")' in js

    # Title element selector for updating
    assert 'titleEl.textContent = "Comment on " + cardName' in js
    assert '"data-comment-dialog-title"' in js

    # Eyebrow label: "Card feedback"
    assert 'eyebrow.textContent = "Card feedback"' in js

    # Placeholder on textarea
    assert 'placeholder = "Private to you and the Deck Lab owner"' in js

    # Styled textarea class
    assert 'className = "dl-comment-field"' in js

    # Save button is primary
    assert 'className = "dl-button dl-button-primary"' in js


# ---------------------------------------------------------------------------
# AC-3: Research search field keeps the full syntax query (not just name terms)
# ---------------------------------------------------------------------------


def test_research_search_field_keeps_full_syntax_query() -> None:
    py_code = RESEARCH_ROUTES.read_text()

    # The function must compute a separate name_query, keeping query = raw_query
    assert "name_query = raw_query" in py_code
    assert "query = raw_query" in py_code

    # Data queries use name_query, not query
    assert "_research().cards(name_query" in py_code
    assert "list_public(\n            query=name_query" in py_code
    assert '"query": name_query' in py_code

    # The returned state dict still has "query" (the full raw query)
    assert '"query": query' in py_code


# ---------------------------------------------------------------------------
# AC-4: Applied terms render as removable chips, removal updates q
# ---------------------------------------------------------------------------


def test_applied_terms_render_as_removable_chips_and_removal_updates_q() -> None:
    html = RESEARCH_FRAGMENT.read_text()

    # Applied terms render as .dl-syntax-chip.is-ok with data-syntax-term
    assert 'class="dl-chip dl-syntax-chip is-ok" data-syntax-term="' in html

    # Each chip has a remove button with data-remove-syntax
    assert 'data-remove-syntax="' in html
    assert 'aria-label="Remove ' in html

    # JavaScript handler: click on [data-remove-syntax] removes term from q and navigates
    js = RESEARCH_JS.read_text()
    assert "data-remove-syntax" in js
    assert "currentQ.replace(pattern" in js
    assert 'url.searchParams.set("q", newQ)' in js
    assert "navigate(url, true)" in js


# ---------------------------------------------------------------------------
# AC-5: Unsupported terms render as warn chips with title
# ---------------------------------------------------------------------------


def test_unsupported_terms_render_as_warn_chips_with_title() -> None:
    html = RESEARCH_FRAGMENT.read_text()

    # Unsupported terms render as .dl-syntax-chip.is-warn with title
    assert 'class="dl-chip dl-syntax-chip is-warn"' in html
    assert 'title="Not supported yet"' in html
    assert (
        "data-remove-syntax"
        not in html.rsplit("is-warn", 1)[1].split("{% endfor %}", 1)[0]
    )


# ---------------------------------------------------------------------------
# AC-6: Plain query unchanged (T03 behaviour preserved)
# ---------------------------------------------------------------------------


def test_plain_query_unchanged() -> None:
    py_code = RESEARCH_ROUTES.read_text()

    # When there's no syntax detected, name_query == raw_query
    assert "name_query = raw_query" in py_code

    # When parsed has no filters and no unsupported, name_query stays as raw_query
    # (the if condition checks parsed.filters or parsed.unsupported)
    assert "if parsed.filters or parsed.unsupported:" in py_code

    # Both chip rows sit under the result count (in the template they're in
    # a .dl-syntax-chips wrapper within the section)
    html = RESEARCH_FRAGMENT.read_text()
    assert "dl-syntax-chips" in html

    # There are no plain-sentence notices for query terms (the old
    # data-query-applied / data-query-unsupported <p> elements are gone)
    assert "data-query-applied" not in html
    assert "data-query-unsupported" not in html


# ---------------------------------------------------------------------------
# CSS: Dialog and chip styles exist
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC-7: Dialog close buttons have no data-dl-tip (tooltip does not overlap edge)
# ---------------------------------------------------------------------------


def test_dialog_close_tooltip_does_not_overlap_dialog_edge() -> None:
    html = BUILDER_TEMPLATE.read_text()
    js = FEEDBACK_JS.read_text()

    # Hotkeys dialog close button in builder.html — no data-dl-tip
    assert (
        'aria-label="Close keyboard help">×</button>' in html
    ), "hotkeys close button must NOT have data-dl-tip"

    # Comment dialog close button in deck-lab-feedback.js — no data-dl-tip
    assert '"data-dl-tip"' not in js


def test_dialogs_css_has_styles() -> None:
    css = DIALOGS_CSS.read_text()
    assert ".dl-hotkeys-table" in css
    assert ".dl-hotkeys-keys" in css
    assert ".dl-hotkeys-or" in css
    assert ".dl-comment-dialog" in css
    assert ".dl-comment-field" in css
    assert ".dl-syntax-chip" in css
    assert ".dl-syntax-chips" in css
    assert ".dl-chip-remove" in css
