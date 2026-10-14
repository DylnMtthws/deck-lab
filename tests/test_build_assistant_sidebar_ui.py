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
    assert "data-assistant-scroll" in TEMPLATE.read_text()
    assert "function appendMarkdown(parent, text, cards)" in js
    assert "parent.appendChild(document.createTextNode" in js
    assert "innerHTML" not in js
    assert "data-known-card-id" in js
    assert 'className = "dl-assistant-card-preview"' in js
    assert "stopImmediatePropagation" in js
    assert 'report.status === "partial"' not in js
    assert 'item.metric === "verified_finish_rate"' not in js
    assert "positive_finish_proof_rate" in js
    assert "function appendComparisonFigure(host, comparison, label)" in js
    assert "Full experiment evidence" in js
    assert "data-assistant-starter" in TEMPLATE.read_text()


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
    assert (
        'appendComparisonFigure(section, alternative.comparison, "Training")'
        in assistant
    )
    assert (
        'appendComparisonFigure(section, alternative.validation.comparison, "Held-out")'
        in assistant
    )
    assert '"Unknown / suppressed"' in assistant


def test_observation_visualization_keeps_finish_proof_unknown_honest_and_compact():
    assistant = SCRIPT.read_text()
    css = STYLE.read_text()
    assert '"Finish proof · lower bound", "positive_finish_proof_rate"' in assistant
    assert "No proof observed; other outcomes unknown" in assistant
    assert "Partial observations use only the authored subset of cards." in assistant
    assert (
        ".dl-assistant-metric-grid { display:grid; grid-template-columns:minmax(0,1fr);"
        in css
    )
    assert ".dl-assistant-card-link img { width:64px; height:88px;" in css
    assert "Number(reportRevision) !== Number(state.revision)" not in assistant
    assert "figure.dataset.reportId === String(view.id)" in assistant
    assert 'figure.dataset.reportFreshness = "unknown"' in assistant
    assert "refreshChartStaleness();" in assistant
    assert "function printDelta(value)" in assistant
    assert (
        'value * 100).toFixed(1).replace(/\\.0$/, "") + " percentage points"'
        in assistant
    )


def test_strategy_sources_render_only_safe_receipt_links_and_bounded_literal_text():
    """Execute the actual renderer against a tiny DOM, including hostile sources."""
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the source-rendering harness")
    source = SCRIPT.read_text()
    renderer = source.split("  function appendStrategyEvidence(", 1)[1].split(
        "  function appendStructuredPresentation(", 1
    )[0]
    renderer = "function appendStrategyEvidence(" + renderer
    script = r"""
const assert = require("node:assert/strict");
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this.attrs[key] = value; }
}
const document = { createElement: tag => new Node(tag) };
function element(tag, text, cls) {
  const node = new Node(tag); node.textContent = text; node.className = cls; return node;
}
function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }
RENDERER
const host = new Node("article");
appendStrategyEvidence(host, { retrieval_mode: "lexical", excerpts: [
  { citation: { document: "Primer <img onerror=attack()>", section: "Engine", source_url: "https://example.test/primer" }, excerpt: "<script>attack()</script>" },
  { citation: { document: "Injected", source_url: "javascript:attack()" }, excerpt: "x".repeat(900) },
  { citation: { document: "Credentials", source_url: "https://user:secret@example.test" }, excerpt: "text" },
  { citation: { document: "Local", source_url: "file:///secret" }, excerpt: "text" },
  { citation: { document: "Fifth", source_url: "https://ignored.test" }, excerpt: "ignored" }
] });
const nodes = flatten(host);
const links = nodes.filter(n => n.tag === "a");
assert.equal(links.length, 1);
assert.equal(links[0].href, "https://example.test/primer");
assert.equal(links[0].rel, "noopener noreferrer");
assert.match(links[0].attrs["aria-label"], /Primer/);
const quotes = nodes.filter(n => n.tag === "blockquote");
assert.equal(quotes.length, 4);
assert.equal(quotes[0].textContent, "<script>attack()</script>");
assert.equal(quotes[1].textContent.length, 600);
assert(nodes.some(n => n.textContent === "Excerpt shortened; this is not the complete source."));
const authored = new Node("article");
appendStrategyEvidence(authored, { corpus_source: "bundled_authored_guidance", excerpts: [
  { source_kind: "authored_guidance", citation: { document: "Local notes" }, excerpt: "Guidance" }
] });
assert(flatten(authored).some(n => n.textContent === "Authored archetype guidance · not a retrieved primer"));
assert(flatten(authored).some(n => String(n.textContent).includes("No external strategy text is indexed yet")));
const empty = new Node("article");
appendStrategyEvidence(empty, { status: "empty_corpus", excerpts: [] });
assert(flatten(empty).some(n => n.textContent === "No strategy documents have been indexed yet."));
""".replace("RENDERER", renderer)
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "presentation.strategy_evidence" in source
    assert (
        'action.name === "retrieve_strategy_evidence" && action.ok === true' in source
    )
    for name in (
        "assess_deck_requirements",
        "find_replacements",
        "test_recommended_alternatives",
    ):
        assert f"{name}: " in source


def test_recommended_experiment_receipt_renders_actual_metric_summary():
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the tool receipt harness")
    source = SCRIPT.read_text()
    renderer = (
        "function renderToolStep("
        + source.split("  function renderToolStep(", 1)[1].split(
            "  function renderMessages(", 1
        )[0]
    )
    script = r"""
const assert = require("node:assert/strict");
function element() { return { appendChild() {} }; }
function toolLabel() { return "Experiment"; }
let rendered = null;
function appendMetricSummary(host, metrics) { rendered = metrics; }
RENDERER
const actual = [{ metric: "opening", value: 0.5 }];
for (const name of ["start_experiment", "test_recommended_alternatives"]) {
  rendered = null;
  renderToolStep({action: {name, ok: true, data: {metric_summary: actual}}});
  assert.equal(rendered, actual);
}
rendered = null;
renderToolStep({action: {name: "test_recommended_alternatives", ok: false, data: {metric_summary: actual}}});
assert.equal(rendered, null);
""".replace("RENDERER", renderer)
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_swap_outcome_renderer_keeps_uncertainty_and_scope_visible():
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for renderer harness")
    source = SCRIPT.read_text()
    renderer = (
        "function appendSwapOutcome("
        + source.split("  function appendSwapOutcome(", 1)[1].split(
            "  function renderExperiments(", 1
        )[0]
    )
    harness = r"""
const assert = require('node:assert/strict');
function element(tag, text) { return {tag, text: text || '', children: [], appendChild(child) { this.children.push(child); }}; }
function text(node) { return [node.text, ...node.children.map(text)].join(' '); }
RENDERER
let host = element('section');
appendSwapOutcome(host, {verdict: 'indeterminate', objective: 'engine_online_rate', reason: 'Intervals overlap.', evidence: {scope: 'native_solo_profile', delta: .01, difference_interval: {low: -.03, high: .05}}, note: '<script>unsafe</script>'});
let rendered = text(host);
assert.match(rendered, /Improvement undetermined/);
assert.match(rendered, /Engine development/);
assert.match(rendered, /Intervals overlap/);
assert.match(rendered, /native solo profile/);
assert.match(rendered, /Interval toward improvement \(positive is better\): -0.030 to 0.050/);
assert.match(rendered, /Overall deck strength and other tradeoffs remain unverified/);
assert.match(rendered, /<script>unsafe<\/script>/);
host = element('section');
appendSwapOutcome(host, {verdict: 'improved', objective: 'qualified_opening_rate'});
assert.match(text(host), /Improved for selected objective/);
assert.doesNotMatch(text(host), /Overall upgrade|Demonstrated upgrade/);
host = element('section');
appendSwapOutcome(host, {verdict: 'improved', display_label: 'Improved opening-policy proxy', objective: 'qualified_opening_rate', evidence: {delta: .03}});
assert.match(text(host), /Improved opening-policy proxy/);
assert.match(text(host), /Modeled opening-policy proxy/);
host = element('section');
appendSwapOutcome(host, {verdict: 'improved', objective: 'mean_missed_land_drops', evidence: {delta: -.2, direction: -1, difference_interval: {low: .1, high: .3}}});
assert.match(text(host), /Raw metric change \(candidate − baseline\): -0.200/);
assert.match(text(host), /positive is better\): 0.100 to 0.300/);
assert.match(text(host), /Lower values are better/);
host = element('section'); appendSwapOutcome(host, null);
assert.match(text(host), /not available yet/);
""".replace("RENDERER", renderer)
    result = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_improvement_objective_settings_match_measurement_contract():
    from sabermetrics.swap_evaluation import OBJECTIVES

    html = TEMPLATE.read_text()
    for objective in OBJECTIVES:
        assert f'value="{objective}"' in html
    js = SCRIPT.read_text()
    assert 'objective.value = constraints.objective || ""' in js
    assert 'objective: sidebar.querySelector("[data-assistant-objective]").value' in js
    assert "appendSwapOutcome(section, alternative.outcome)" in js


def test_objective_menu_explains_proxy_and_coverage_limits():
    html = TEMPLATE.read_text()
    assert ">Modeled opening-policy proxy</option>" in html
    assert html.count("requires full native coverage</option>") == 5
    assert "incomplete coverage leaves improvement unknown" in html


def test_strategic_review_receipt_exposes_tradeoffs_without_upgrade_claim():
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for renderer harness")
    source = SCRIPT.read_text()
    renderer = (
        "function appendSwapReviews("
        + source.split("  function appendSwapReviews(", 1)[1].split(
            "  function renderToolStep(", 1
        )[0]
    )
    harness = r"""
const assert = require('node:assert/strict');
function element(tag, text) { return {tag, text: text || '', children: [], appendChild(child) { this.children.push(child); }}; }
function text(node) { return [node.text, ...node.children.map(text)].join(' '); }
RENDERER
const host = element('section');
appendSwapReviews(host, [{candidate_index: 7, outgoing_name: 'Old Card', incoming_name: 'New Card', citations_verified: true, decision: 'uncertain', reason: 'Theme fit unknown.', benefit: 'Lower casting cost.', tradeoffs: ['Lose flash.'], validation_reason: 'Missing text.', citations: [{side: 'outgoing', quote: 'Flash'}]}]);
const rendered = text(host);
for (const phrase of ['Cut Old Card; add New Card', 'Fit uncertain', 'Theme fit unknown.', 'Lose flash.', 'Missing text.', 'Card-text evidence', 'outgoing: “Flash”', 'measured improvement remain unverified']) assert.ok(rendered.includes(phrase), phrase);
assert.doesNotMatch(rendered, /Demonstrated upgrade|Independent review|Candidate 8/);
for (const verification of [false, undefined, 'true']) {
  const unverified = element('section');
  appendSwapReviews(unverified, [{candidate_index: 3, outgoing_name: 'Known Out', incoming_name: 'Known In', citations_verified: verification, decision: 'uncertain', citations: [{side: 'incoming', quote: 'Invented mana ability'}]}]);
  const content = text(unverified);
  assert.match(content, /Cut Known Out; add Known In/);
  assert.match(content, /Quoted evidence not verified/);
  assert.doesNotMatch(content, /Invented mana ability|Card-text evidence|Candidate 4/);
}
const legacy = element('section');
appendSwapReviews(legacy, [{candidate_index: 7, decision: 'uncertain'}]);
assert.match(text(legacy), /Swap under review/);
assert.doesNotMatch(text(legacy), /Candidate 8|undefined/);
const math = element('section');
const supported = {status: 'supported_from_hand_clause_accounting', requirements: ['card_in_hand_for_optional_discard'], branches: [{condition: 'optional_discard_paid', cards_drawn: 2, cards_discarded: 1, net_hand_delta: 0}, {condition: 'optional_discard_declined', cards_drawn: 0, cards_discarded: 0, net_hand_delta: -1}]};
appendSwapReviews(math, [{outgoing_name: "Witch's Mark", incoming_name: 'Divination', outgoing_card_economy: supported, incoming_card_economy: {status: supported.status, branches: [{condition: 'normal_resolution', cards_drawn: 2, cards_discarded: 0, net_hand_delta: 1}]}}]);
const mathText = text(math);
assert.match(mathText, /Hand-card accounting/);
assert.ok(mathText.includes("Cut Witch's Mark · optional discard paid: 2 drawn − 1 discarded − 1 spell = 0 hand cards"));
assert.ok(mathText.includes('optional discard declined: 0 drawn − 0 discarded − 1 spell = -1 hand cards'));
assert.ok(mathText.includes('Add Divination · normal resolution: 2 drawn − 0 discarded − 1 spell = +1 hand cards'));
assert.match(mathText, /Cast from hand and resolve with legal targets/);
assert.match(mathText, /excludes board value, commander triggers and graveyard reuse/);
assert.doesNotMatch(mathText, /net overall card advantage/);
function details(node) { return [node, ...node.children.flatMap(details)].filter(item => item.tag === 'details'); }
assert.ok(details(math).every(item => item.open !== true));
for (const profile of [
  {status: 'unknown', branches: supported.branches},
  {status: supported.status, branches: [{cards_drawn: '2', cards_discarded: 1, net_hand_delta: 0}]},
  {status: supported.status, branches: [{cards_drawn: NaN, cards_discarded: 1, net_hand_delta: 0}]},
  {status: supported.status, branches: [{cards_drawn: 2, cards_discarded: 1, net_hand_delta: 1}]},
  {status: supported.status, branches: [{cards_drawn: 2, cards_discarded: -1, net_hand_delta: 2}]},
  {status: supported.status, branches: [{cards_drawn: 2.5, cards_discarded: 1, net_hand_delta: .5}]}
]) {
  const malformed = element('section');
  appendSwapReviews(malformed, [{incoming_card_economy: profile}]);
  assert.doesNotMatch(text(malformed), /Hand-card accounting|1 spell =/);
}

""".replace("RENDERER", renderer)
    result = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
