"""Structure contracts for the Build Research Assistant sidebar."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src/sabermetrics/ui/templates/deck_lab/builder.html"
SCRIPT = ROOT / "src/sabermetrics/ui/static/deck-lab-assistant-sidebar.js"
STYLE = ROOT / "src/sabermetrics/ui/static/deck-lab-assistant-sidebar.css"
SIMULATOR = ROOT / "src/sabermetrics/ui/static/deck-lab-simulate.js"


def test_build_sidebar_hosts_chat_analytics_goldfish_and_card_preview():
    html = TEMPLATE.read_text()
    assert 'data-assistant-tab="chat"' in html
    assert 'data-assistant-tab="analytics"' in html
    assert 'data-assistant-tab="goldfish"' in html
    assert 'id="goldfish-dialog" data-goldfish-inline' in html
    assert "data-goldfish-sim-slot" in html
    assert 'id="mana-curve"' in html and 'id="zone-stats"' in html
    assert "data-card-panel" in html
    assert "deck-lab-assistant-sidebar.js" in html
    assert "deck-lab-research-assistant.js" not in html


def test_sidebar_targets_persistent_assistant_contract_and_accessible_drawer():
    js = SCRIPT.read_text()
    for endpoint in ("/settings", "/messages", "/stop", "/experiments/", "/apply"):
        assert endpoint in js
    assert 'localStorage.setItem("deck-lab-assistant-tab:' in js
    assert 'role="separator"' in TEMPLATE.read_text()
    assert "height:100dvh" in STYLE.read_text()
    assert "overflow-x:hidden" in STYLE.read_text()
    css = STYLE.read_text()
    assert '.dl-assistant-pane[data-assistant-pane="chat"] { overflow:hidden; }' in css
    assert ".dl-assistant-transcript" in css and "min-height:0" in css
    assert ".dl-assistant-experiments,.dl-assistant-proposals" in css
    assert ".dl-assistant-composer" in css and "flex:0 0 auto" in css
    assert "@media (max-width: 430px)" in css
    assert ".dl-assistant-mobile-open { padding-inline:7px; }" in css
    assert ".dl-workspace-header > .push { display:none; }" in css
    assert "[data-undo]" in css and "[data-redo]" in css
    assert "function appendSearchResults(article, message)" in js
    assert 'action.name !== "search_cards"' in js
    assert "slice(0, 12)" in js


def test_existing_native_simulator_can_mount_inline():
    js = SIMULATOR.read_text()
    assert 'dialog.hasAttribute("data-goldfish-inline")' in js
    assert "(!opener && !inline)" in js


def test_adopted_server_document_updates_live_builder_history_without_reload():
    assistant = SCRIPT.read_text()
    builder = (ROOT / "src/sabermetrics/ui/static/deck-lab-builder.js").read_text()
    history = (ROOT / "src/sabermetrics/ui/static/deck-lab-history.js").read_text()
    assert "acceptUpdatedDocument(updated)" in assistant
    assert "api.replaceDocument(updated)" in assistant
    assert "window.location.reload()" not in assistant
    assert "replaceDocument: function (document)" in builder
    assert "render();\n      return true;" in builder
    assert "api.onRender(function () { observe(); })" in history
    assert 'comparisonLines(alternative.comparison, "Training")' in assistant
    assert 'comparisonLines(alternative.validation.comparison, "Held-out")' in assistant
    assert "unknown/suppressed" in assistant
