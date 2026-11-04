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
const wiki = new Node("article");
appendStrategyEvidence(wiki, {excerpts: [{source_kind: "wiki_secondary", excerpt: "Adapted wiki passage.",
  citation: {document: "Wiki", source_url: "https://example.test/wiki?oldid=123", provenance: {version: "revision-123"}},
  source_attribution: {author: "Wiki contributors <script>", source_date: "2026-10-20", review_status: "community_edited_not_expert_reviewed",
    license_url: "https://creativecommons.org/licenses/by-sa/4.0/", attribution_url: "https://example.test/history"}
}]});
const wikiNodes = flatten(wiki);
assert(wikiNodes.some(n => n.textContent === "Wiki contributors <script>"));
assert(wikiNodes.some(n => n.textContent === "Source version: revision-123"));
assert(wikiNodes.some(n => n.textContent === "community edited not expert reviewed"));
assert.equal(wikiNodes.filter(n => n.tag === "a").length, 3);
assert(wikiNodes.some(n => n.textContent === "Reuse license" && n.href === "https://creativecommons.org/licenses/by-sa/4.0/"));
const badLicense = new Node("article");
appendStrategyEvidence(badLicense, {excerpts: [{excerpt: "Text", citation: {document: "Test"}, source_attribution: {license_url: "javascript:attack()", attribution_url: "https://user:secret@example.test"}}]});
assert.equal(flatten(badLicense).filter(n => n.tag === "a").length, 0);
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


def test_collapsed_assistant_rail_can_be_reopened():
    css = STYLE.read_text()
    js = SCRIPT.read_text()
    # The heading is an <h2>; hiding it keeps the expand toggle inside the 44px strip.
    assert ".left-collapsed .dl-assistant-head > h2 { display:none; }" in css
    assert ".left-collapsed .dl-assistant-head > div:first-child" not in css
    # The header button is visible at every width and toggles the desktop rail.
    assert ".dl-assistant-mobile-open { display:inline-flex;" in css
    assert ".dl-assistant-mobile-open { display:none; }" not in css
    assert "railToggle.click()" in js
    assert 'mobile(!sidebar.classList.contains("is-open"))' in js


def _run_node(script):
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for renderer harness")
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _between(source, start, end):
    return (
        "function "
        + start
        + source.split("  function " + start, 1)[1].split("  function " + end, 1)[0]
    )


def test_progress_counts_finished_calls_for_the_current_turn_in_plain_words():
    source = SCRIPT.read_text()
    helpers = _between(source, "toolLabel(", "appendSwapReviews(")
    harness = r"""
const assert = require('node:assert/strict');
HELPERS
const start = (name) => ({role: 'tool', action: {type: 'tool_started', name}});
const done = (name, ok = true) => ({role: 'tool', action: {type: 'tool_result', name, ok}});
const earlier = [{role: 'user'}, start('inspect_deck'), done('inspect_deck'), {role: 'assistant'}];
// One start plus its result is one finished call, never two steps.
assert.deepEqual(toolActivity([start('find_replacements'), done('find_replacements')]), {finished: 1, pending: []});
assert.deepEqual(toolActivity([start('search_cards'), done('search_cards', false), start('card_details')]), {finished: 1, pending: ['card_details']});
assert.deepEqual(toolActivity([start('card_details'), {role: 'tool', action: {type: 'partial_finding', name: 'confirmed_finding', ok: true}}]), {finished: 0, pending: ['card_details']});
const now = 10000;
const live = earlier.concat([{role: 'user'}, start('search_cards'), done('search_cards'), start('find_replacements')]);
assert.equal(progressText(live, {chatBusy: true, startedAt: now - 30, now}), 'Working · Replacement research · 1 step finished');
const reviewing = earlier.concat([{role: 'user'}, start('search_cards'), done('search_cards'), start('card_details'), done('card_details')]);
assert.equal(progressText(reviewing, {chatBusy: true, startedAt: now - 125, now}), 'Working · 2 steps finished · 2 min so far');
assert.equal(progressText(earlier.concat([{role: 'user'}]), {chatBusy: true, now}), 'Working on your request…');
assert.equal(progressText(live, {chatBusy: true, stopping: true, now}), 'Stopping after the current step…');
assert.equal(progressText(live, {chatBusy: false, now}), '');
assert.equal(progressText([], {chatBusy: false, experimentsRunning: true}), 'Automated research is running…');
const milestone = {role: 'tool', action: {type: 'partial_finding', ok: true, data: {source_scope: 'research progress', stage: 'comparison', started_at: now - 31}}};
assert.deepEqual(toolActivity([milestone]), {finished: 0, pending: []});
assert.match(progressText([{role: 'user'}, milestone], {chatBusy: true, now}), /Comparing costs, support and tradeoffs.*no new findings yet/);
assert.doesNotMatch(progressText([{role: 'user'}, milestone], {chatBusy: true, now: now - 25}), /no new findings/);
assert.equal(progressText([{role: 'user'}, milestone, start('card_details')], {chatBusy: true, now}), 'Working · Card details');
const checking = {...milestone, action: {...milestone.action, data: {...milestone.action.data, stage: 'verification'}}};
assert.match(progressText([{role: 'user'}, checking, start('check_answer')], {chatBusy: true, now}), /Checking claims against card text.*no new findings yet/);
// Elapsed time changes at minute granularity, so a polite live region is not re-announced every poll.
assert.equal(progressText(live, {chatBusy: true, startedAt: now - 61, now}), progressText(live, {chatBusy: true, startedAt: now - 119, now}));
const unknown = progressText([{role: 'user'}, start('internal_lookup_v2')], {chatBusy: true, now});
assert.equal(unknown, 'Working · Research step');
for (const text of [progressText(live, {chatBusy: true, now}), unknown]) assert.doesNotMatch(text, /tool|_|poll|model|Message sent/i);
""".replace("HELPERS", helpers)
    _run_node(harness)
    assert '"Message sent."' not in source


def test_transcript_scroll_reveals_reply_lead_and_keeps_reader_place():
    helpers = _between(SCRIPT.read_text(), "toolLabel(", "appendSwapReviews(")
    harness = r"""
const assert = require('node:assert/strict');
HELPERS
const view = {scrollHeight: 2000, clientHeight: 400};
assert.equal(transcriptScrollTop({...view, following: true}), 1600);
// A long new reply lands on its first line, not its last card.
assert.equal(transcriptScrollTop({...view, following: true, replyTop: 500}), 492);
// A short reply that fits still shows the latest content.
assert.equal(transcriptScrollTop({...view, following: true, replyTop: 1750}), 1600);
// Reading older messages: the same message stays at the same offset.
assert.equal(transcriptScrollTop({...view, following: false, anchorTop: 900, anchorOffset: -20}), 920);
assert.equal(transcriptScrollTop({...view, following: false}), null);
assert.equal(transcriptScrollTop({scrollHeight: 300, clientHeight: 400, following: true, replyTop: 100}), 0);
""".replace("HELPERS", helpers)
    _run_node(harness)


def test_transcript_renderer_pairs_steps_preserves_disclosures_and_scroll():
    """Run the real renderMessages against a small fake DOM with block layout."""
    source = SCRIPT.read_text()
    helpers = _between(source, "toolLabel(", "appendSwapReviews(")
    renderer = _between(source, "renderMessages(", "appendMetricSummary(")
    harness = r"""
const assert = require('node:assert/strict');
const HEIGHT = {user: 100, tool: 100, assistant: 900};
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attrs = {}; this.parentNode = null; this._text = ''; this.className = ''; this.open = false; }
  get classList() { return {contains: (name) => this.className.split(/\s+/).includes(name)}; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  replaceChild(next, old) { this.children[this.children.indexOf(old)] = next; next.parentNode = this; return old; }
  replaceChildren() { this.children = []; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  setAttribute(key, value) { this.attrs[key] = value; }
  all() { return this.children.flatMap((child) => [child, ...child.all()]); }
  querySelectorAll(selector) { return this.all().filter((node) => matches(node, selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getBoundingClientRect() {
    const siblings = transcript.children; const index = siblings.indexOf(this);
    const top = siblings.slice(0, index).reduce((sum, node) => sum + HEIGHT[node.dataset.role], 0) - surface.scrollTop;
    return {top, bottom: top + HEIGHT[this.dataset.role]};
  }
}
function matches(node, selector) {
  if (selector === '[data-pending-tool]') return 'pendingTool' in node.dataset;
  if (selector === 'details[data-disclosure-key]') return node.tagName === 'DETAILS' && 'disclosureKey' in node.dataset;
  if (selector === '.dl-assistant-activity > details') return node.tagName === 'DETAILS' && node.parentNode.classList.contains('dl-assistant-activity');
  if (selector === 'details') return node.tagName === 'DETAILS';
  if (selector === 'summary') return node.tagName === 'SUMMARY';
  throw new Error('unsupported selector ' + selector);
}
const document = {createElement: (tag) => new El(tag)};
function element(tag, text, cls) { const node = new El(tag); if (cls) node.className = cls; if (text !== undefined && text !== null) node.textContent = text; return node; }
const transcript = new El('div');
let writes = 0; let top = 0;
const surface = {
  clientHeight: 400,
  get scrollHeight() { return transcript.children.reduce((sum, node) => sum + HEIGHT[node.dataset.role], 0); },
  get scrollTop() { return top; },
  set scrollTop(value) { writes += 1; top = Math.max(0, Math.min(this.scrollHeight - this.clientHeight, value)); },
  getBoundingClientRect() { return {top: 0, bottom: 400}; }
};
const scrollSurface = surface;
var data = {busy: false, messages: []};
var renderedMessageSignature = null, renderedMessageCount = 0, renderedMessageIds = [];
var followingLatest = true, deferredScroll = false, deferredReply = null;
function renderToolStep(message) {
  const action = message.action || {}; const step = element('div');
  step.appendChild(element('span', toolLabel(action) + (action.type === 'tool_started' ? ' · working' : ' · complete')));
  return step;
}
function appendReply(article, message) { article.appendChild(element('p', message.content)); }
function messageContent(message) { return String(message.content || ''); }
function knownCards(cards) { return cards; }
let partialCards;
function appendMarkdown(body, content, cards) { partialCards = cards; body.appendChild(element('p', content)); }
function appendStructuredPresentation() {}
function refreshChartStaleness() {}
HELPERS
RENDERER
const start = (id, name) => ({id, role: 'tool', action: {type: 'tool_started', name}});
const done = (id, name) => ({id, role: 'tool', action: {type: 'tool_result', name, ok: true}});
const say = (id, role, content) => ({id, role, content: content || id});
const activities = () => transcript.querySelectorAll('.dl-assistant-activity > details');
const steps = (activity) => activity.children.filter((node) => node.tagName === 'DIV');

// Initial history: one call renders as one step, and the latest long answer opens at its lead.
data.messages = [say('u1', 'user'), start('t1', 'find_replacements'), done('t2', 'find_replacements'), say('a1', 'assistant')];
renderMessages();
assert.equal(steps(activities()[0]).length, 1);
assert.equal(activities()[0].querySelector('summary').textContent, 'Research activity · 1 step finished');
assert.equal(surface.scrollTop, 192);

// An identical poll writes nothing and does not move the reader.
let before = writes; data = {...data, messages: data.messages.slice()}; renderMessages();
assert.equal(writes, before);

// Live turn: the working line is replaced by its own result.
followingLatest = true;
data = {busy: true, messages: data.messages.concat([say('u2', 'user'), start('t3', 'search_cards')])};
renderMessages();
assert.equal(activities()[1].querySelector('summary').textContent, 'Research activity · now: Card catalog search');
assert.equal(surface.scrollTop, surface.scrollHeight - surface.clientHeight);
data = {busy: true, messages: data.messages.concat([done('t4', 'search_cards'), start('t5', 'card_details')])};
renderMessages();
assert.equal(steps(activities()[1]).length, 2);
assert.equal(activities()[1].querySelector('summary').textContent, 'Research activity · 1 step finished · now: Card details');
// The turn ended without that result: say so instead of "working" forever.
data = {busy: false, messages: data.messages.slice()};
syncActivities();
assert.equal(activities()[1].querySelector('summary').textContent, 'Research activity · 1 step finished · 1 did not finish');
assert.match(activities()[1].textContent, /Card details · did not finish/);

// Reading older messages while the window shifts: rebuild keeps the reader's message in place
// and keeps opened research details open.
activities()[1].open = true;
followingLatest = false; top = 250; // a1 is the first visible message, 50px above the viewport top
const anchorOffset = transcript.children.find((node) => node.dataset.messageIds === 'a1').getBoundingClientRect().top;
data = {busy: false, messages: data.messages.slice(1).concat([say('a2', 'assistant')])};
renderMessages();
const a1 = transcript.children.find((node) => node.dataset.messageIds === 'a1');
assert.equal(a1.getBoundingClientRect().top, anchorOffset);
assert.equal(activities()[1].open, true);
assert.equal(activities()[0].open, false);

// Interim findings are visible outside collapsed telemetry while the request is busy.
const card = {id: 'oracle-1', name: 'Divination'};
const finding = (id, scope, content) => ({id, role: 'tool', content: 'tool fallback', action: {
  type: 'partial_finding', name: 'confirmed_finding', ok: true, revision: 1,
  content, data: {source_scope: scope, cards: [card]}
}});
const partial = finding('f1', 'printed facts', 'Divination says: <script>attack()</script>');
followingLatest = true;
data = {busy: true, messages: [say('u3', 'user'), start('t6', 'card_details'), partial, partial]};
renderMessages();
const findingSections = () => transcript.all().filter(node => node.className === 'dl-assistant-partial-finding');
let findings = findingSections();
assert.equal(findings.length, 1); // Duplicate persisted message IDs never repeat content.
assert.equal(findings[0].parentNode.tagName, 'ARTICLE');
assert.equal(findings[0].parentNode.dataset.role, 'tool');
assert.equal(findings[0].dataset.messageIds, 'f1');
assert.equal(activities()[0].open, false);
assert.match(findings[0].textContent, /Confirmed findings/);
assert.match(findings[0].textContent, /Printed card facts; strategic fit is not established/);
assert.match(findings[0].textContent, /<script>attack\(\)<\/script>/);
assert.equal(partialCards[0], card);
assert.equal(steps(activities()[0]).length, 1);
assert.equal(activities()[0].querySelector('summary').textContent, 'Research activity · now: Card details');
assert.equal(transcript.children.filter(node => node.dataset.role === 'assistant').length, 0);
const sameFinding = findings[0];
renderMessages();
assert.equal(findingSections()[0], sameFinding);
data.messages = data.messages.concat([finding('f2', 'retrieved candidates', 'Candidate card text')]);
renderMessages();
assert.equal(findingSections().length, 2);
assert.match(findingSections()[1].textContent, /Retrieved candidates/);
assert.match(findingSections()[1].textContent, /suitability is not established by retrieval/);
// A revised persisted record rebuilds its content instead of appending stale or duplicate prose.
data.messages = data.messages.map(message => message.id === 'f1' ? {...message, action: {...message.action, revision: 2, content: 'Updated printed fact'}} : message);
renderMessages();
assert.equal(findingSections().length, 2);
assert.match(findingSections()[0].textContent, /Updated printed fact/);
assert.doesNotMatch(findingSections()[0].textContent, /attack/);
// Normal final prose remains its own assistant reply; tool telemetry stays separate.
data = {busy: false, messages: data.messages.concat([done('t7', 'card_details'), say('a3', 'assistant', 'Final grounded answer')])};
renderMessages();
assert.equal(findingSections().length, 2);
assert.equal(transcript.lastElementChild.dataset.role, 'assistant');
assert.equal(transcript.lastElementChild.textContent, 'Final grounded answer');
assert.equal(activities()[0].querySelector('summary').textContent, 'Research activity · 1 step finished');
data = {busy: true, messages: [say('u4', 'user'), finding('f3', 'research progress', 'Comparing retrieved evidence'), finding('f4', 'preliminary fit', 'Potential support remains unconfirmed.')]};
renderMessages();
assert.equal(findingSections().length, 2);
assert.match(findingSections()[0].textContent, /Research update.*Comparing retrieved evidence/);
assert.match(findingSections()[1].textContent, /Preliminary deck fit.*not a ranked recommendation/);
assert.equal(steps(activities()[0]).length, 0);
data.messages.push(finding('f5', 'reviewed fragments', 'Supported cost and conditional effect.'));
renderMessages();
assert.equal(findingSections().length, 3);
assert.match(findingSections()[2].textContent, /Checked findings/);
assert.match(findingSections()[2].textContent, /remaining claims and request completion are still under review/);
assert.match(findingSections()[2].textContent, /Supported cost and conditional effect/);
// Introducing workspace cards removes duplicated partial sections while retaining audit activity.
data.research_workspace = {cards: [{card_id: 'c1'}]};
const previousWrites = writes;
renderMessages();
assert.equal(findingSections().length, 1, 'only the research progress section remains');
assert.match(activities()[0].textContent, /Workspace finding.*preliminary fit/);
assert.match(activities()[0].textContent, /Potential support remains unconfirmed/);
assert.equal(writes, previousWrites, 'workspace readers are not scrolled to replies');
renderMessages();
assert.equal(findingSections().length, 1);
""".replace("HELPERS", helpers).replace("RENDERER", renderer)
    _run_node(harness)


def test_reply_card_gallery_is_folded_and_long_search_results_are_limited():
    css = STYLE.read_text()
    js = SCRIPT.read_text()
    assert 'tray.className = "dl-assistant-card-tray"' in js
    assert '"Cards in this answer · " + cards.length' in js
    assert "appendCardTray(article, message);" in js
    assert "appendCardResults(article, presentation.cards)" not in js
    assert '"Show " + (valid.length - limit) + " more"' in js
    assert (
        ".dl-assistant-card-tray .dl-assistant-card-link img { width:40px; height:56px; }"
        in css
    )


def _workspace_harness():
    source = SCRIPT.read_text()
    return r"""
const assert = require('node:assert/strict');
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attrs = {}; this.listeners = {}; this._text = ''; this.open = false; this.hidden = false; this.disabled = false; this.value = ''; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  insertBefore(child, before) { child.parentNode = this; this.children.splice(this.children.indexOf(before), 0, child); }
  replaceChildren() { this.children = []; }
  remove() { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); }
  get firstChild() { return this.children[0]; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  click() { if (!this.disabled) this.listeners.click(); }
}
function element(tag, text, cls) { const node = new El(tag); if (text != null) node.textContent = text; node.className = cls || ''; return node; }
const document = {activeElement: null};
const window = {crypto: {randomUUID: () => 'request-' + (++requestId)}};
let requestId = 0, calls = [], rejectNext = false;
var data = {}, state = {revision: 12}, base = '/assistant', status = element('p'), progressShown = '';
var workspaceNode = null, workspaceCards = new Map(), workspaceList, workspaceBadge, workspaceRequest, workspaceReceipt, workspaceRetry;
var sessionEpoch = 0, refreshSequence = 0, sessionRequestSequence = 0, acceptedSessionSequence = 0, pendingSubmission = null;
const transcript = element('div'), host = element('div'); host.appendChild(transcript);
const scrollSurface = {clientHeight: 0, scrollTop: 187};
let refreshes = 0;
function render() { renderWorkspace(); }
function refresh() { refreshes += 1; return Promise.resolve(); }
function request(url, options) {
  const payload = JSON.parse(options.body); calls.push({url, payload});
  if (rejectNext) { rejectNext = false; return Promise.reject(new Error('stale generation')); }
  const next = JSON.parse(JSON.stringify(data));
  const card = (next.research_workspace.cards || []).find(c => c.card_id === payload.card_id);
  if (card && payload.action === 'pin') card.pinned = true;
  if (card && payload.action === 'unpin') card.pinned = false;
  if (card && payload.action === 'exclude') card.excluded = true;
  if (payload.action === 'retry') { next.research_workspace.generation += 1; next.research_workspace.status = 'queued'; next.research_workspace.job = {state: 'queued'}; }
  return Promise.resolve({session: next});
}
HELPERS
const flush = () => new Promise(resolve => setImmediate(resolve));
""".replace("HELPERS", _between(source, "researchWorkspace(", "protectedCards("))


def test_workspace_preserves_card_identity_disclosures_and_acknowledged_controls():
    _run_node(_workspace_harness() + r"""
(async () => {
data = {constraints: {theme: 'Artifacts', budget: null, protected_cards: []}, research_workspace: {generation: 4, request_text: 'Find support', status: 'running', job: {state: 'running'}, cards: [{card_id: 'c1', name: '<script>literal</script>', status: 'retrieved', version: 1, printed: {oracle_text: 'Exact text', oracle_text_truncated: true}, claims: [{claim_id: 'q1', text: 'Supports the deck', status: 'unverified'}]}]}};
renderWorkspace();
const card = workspaceCards.get('c1'), printed = card.printed.node, claim = card.claimNodes.get('q1').node;
printed.open = true; claim.open = true;
assert.equal(workspaceList.children.length, 1);
assert.equal(card.name.textContent, '<script>literal</script>');
assert.match(card.node.textContent, /fit unverified/);
assert.match(card.node.textContent, /Printed text is incomplete/);
assert.equal(workspaceReceipt.textContent, 'Captured settings: theme: Artifacts');
workspaceRequest.value = 'Unsaved edit'; document.activeElement = workspaceRequest;
renderWorkspace();
assert.equal(workspaceRequest.value, 'Unsaved edit');
card.buttons.pin.click();
assert.equal(card.card.pinned, undefined, 'no optimistic pin before acknowledgment');
await flush();
assert.equal(card.card.pinned, true);
assert.equal(card.buttons.pin.attrs['aria-pressed'], 'true');
assert.deepEqual(calls[0].payload, {expected_revision: 12, expected_generation: 4, action: 'pin', card_id: 'c1', client_request_id: 'request-1'});
assert.equal(calls[0].url, '/assistant/steer');
card.buttons.pin.click(); await flush(); assert.equal(card.card.pinned, false);
card.buttons.exclude.click(); await flush(); assert.equal(card.card.excluded, true);
rejectNext = true; card.buttons.prioritize.click(); await flush();
assert.equal(card.buttons.prioritize.attrs['aria-pressed'], 'false');
assert.equal(status.textContent, 'stale generation');
assert.equal(card.buttons.prioritize.disabled, false);
const nextCard = data.research_workspace.cards[0];
nextCard.version = 2; nextCard.status = 'withdrawn'; nextCard.history = [{version: 2, reason: 'Incorrect interaction retracted', claims: [{text: 'Earlier unsafe interaction', status: 'supported', evidence: {quote: 'Old quote'}}]}];
nextCard.claims[0].status = 'contradicted'; nextCard.claims[0].evidence = {quote: 'Actual condition'};
renderWorkspace();
assert.equal(workspaceCards.get('c1'), card);
assert.equal(card.printed.node, printed); assert.equal(printed.open, true);
assert.equal(card.claimNodes.get('q1').node, claim); assert.equal(claim.open, true);
assert.match(card.node.textContent, /Withdrawn.*do not rely/);
assert.match(card.node.textContent, /This claim conflicts/);
assert.match(card.node.textContent, /Incorrect interaction retracted/);
assert.match(card.node.textContent, /Earlier unsafe interaction/);
assert.match(card.node.textContent, /Old quote/);
assert.equal(scrollSurface.scrollTop, 187);
assert.equal(workspaceList.children.length, 1);
assert.deepEqual(Object.keys(card.buttons), ['pin', 'exclude', 'prioritize']);
data.research_workspace.cards = []; renderWorkspace();
assert.equal(workspaceCards.has('c1'), false);
assert.equal(workspaceList.children.length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_workspace_partial_interrupted_retry_and_missing_keys_are_honest():
    _run_node(_workspace_harness() + r"""
(async () => {
data = {research_workspace: {}}; renderWorkspace();
assert.equal(workspaceRetry.hidden, true);
assert.match(workspaceBadge.textContent, /state unavailable/);
data.research_workspace = {generation: 2, status: 'partial', job: {state: 'completed', checkpoint: 'claims reviewed'}, cards: []};
renderWorkspace(); assert.match(workspaceBadge.textContent, /Research finished.*requirements or claims remain unresolved/);
assert.equal(researchRunning(), false);
data.research_workspace = {generation: 2, status: 'running', job: {state: 'interrupted', retry_allowed: true}, cards: []};
renderWorkspace(); assert.match(workspaceBadge.textContent, /interrupted.*no active work confirmed/);
assert.equal(researchRunning(), false); assert.equal(workspaceRetry.hidden, false);
workspaceRetry.click(); await flush();
assert.equal(calls[0].payload.action, 'retry'); assert.equal(calls[0].payload.expected_generation, 2);
assert.equal(data.research_workspace.generation, 3); assert.equal(researchRunning(), true);
assert.match(workspaceBadge.textContent, /queued/);
assert.equal(refreshes, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_busy_composer_steers_and_idle_composer_posts_messages_without_losing_failed_draft():
    source = SCRIPT.read_text()
    composer = source.split(
        '  var composer = sidebar.querySelector("[data-assistant-composer]");', 1
    )[1].split('  var stop = sidebar.querySelector("[data-assistant-stop]");', 1)[0]
    _run_node(_workspace_harness() + r"""
(async () => {
var busy = true, sending = false, followingLatest = false;
const composer = new El('form'), input = new El('textarea'), send = new El('button');
const sidebar = {querySelector: selector => selector === '[data-assistant-message]' ? input : send};
data = {research_workspace: {generation: 8, status: 'running', job: {state: 'running'}, cards: [{card_id: 'a'}]}};
COMPOSER
input.value = 'Focus on draw'; composer.listeners.submit({preventDefault() {}});
await flush();
assert.equal(calls[0].url, '/assistant/steer');
assert.equal(calls[0].payload.action, 'message'); assert.equal(calls[0].payload.message, 'Focus on draw');
assert.equal(calls[0].payload.expected_generation, 8); assert.equal(calls[0].payload.expected_revision, 12);
assert.equal(input.value, ''); assert.equal(send.disabled, false); assert.equal(followingLatest, false);
input.value = 'Keep this draft'; rejectNext = true; composer.listeners.submit({preventDefault() {}}); await flush();
assert.equal(input.value, 'Keep this draft'); assert.equal(status.textContent, 'stale generation'); assert.equal(sending, false);
busy = false; input.value = 'New request'; composer.listeners.submit({preventDefault() {}}); await flush();
assert.equal(calls[2].url, '/assistant/messages');
assert.equal(typeof calls[2].payload.client_request_id, 'string');
const {client_request_id, ...submitted} = calls[2].payload;
assert.deepEqual(submitted, {message: 'New request', expected_revision: 12});
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("COMPOSER", composer))


def test_workspace_growth_above_visible_card_preserves_reading_position():
    _run_node(_workspace_harness() + r"""
data = {research_workspace: {cards: [{card_id: 'above', printed: {oracle_text: 'Short'}}, {card_id: 'reading'}]}};
renderWorkspace();
const above = workspaceCards.get('above'), reading = workspaceCards.get('reading');
let addedHeight = 0;
scrollSurface.clientHeight = 120;
scrollSurface.getBoundingClientRect = () => ({top: 0});
above.node.getBoundingClientRect = () => ({top: -200, bottom: -20 + addedHeight});
reading.node.getBoundingClientRect = () => ({top: addedHeight, bottom: 100 + addedHeight});
const replace = above.printed.body.replaceChildren.bind(above.printed.body);
above.printed.body.replaceChildren = () => { addedHeight = 40; replace(); };
data.research_workspace.cards[0].printed.oracle_text = 'More rules text';
renderWorkspace();
assert.equal(scrollSurface.scrollTop, 227, 'compensates for content growth above the visible card');
assert.equal(workspaceCards.get('reading').node, reading.node);
""")


def test_workspace_mutation_acknowledgments_never_restore_older_generations():
    _run_node(_workspace_harness() + r"""
(async () => {
data = {updated_at: 40, research_workspace: {generation: 4, request_text: 'Old request', cards: [{card_id: 'old', pinned: false}]}};
renderWorkspace();
workspaceRequest.value = 'Unsaved revised request';
const pending = [];
request = (url, options) => { calls.push({url, payload: JSON.parse(options.body)}); return new Promise(resolve => pending.push(resolve)); };
const oldPin = steer('pin', {card_id: 'old'}).catch(error => error);
const message = steer('message', {message: 'New request'});
pending[1]({updated_at: 50, research_workspace: {generation: 5, request_text: 'New request', cards: [{card_id: 'new'}]}});
await message;
assert.equal(workspaceRequest.value, 'Unsaved revised request', 'blurred workspace draft survives a newer generation');
pending[0]({updated_at: 60, research_workspace: {generation: 4, request_text: 'Old request', cards: [{card_id: 'old', pinned: true}]}});
assert.match((await oldPin).message, /Research changed/);
assert.equal(data.research_workspace.generation, 5);
assert.deepEqual([...workspaceCards.keys()], ['new']);
assert.notEqual(status.textContent, 'Research update acknowledged.');
assert.equal(refreshes, 1, 'stale acknowledgment does not refresh as success');
const nextPin = steer('pin', {card_id: 'new'});
assert.equal(calls[2].payload.expected_generation, 5);
pending[2]({updated_at: 61, research_workspace: {generation: 5, cards: [{card_id: 'new', pinned: true}]}});
await nextPin;
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_same_generation_admission_uses_server_snapshot_order_then_request_order():
    _run_node(_workspace_harness() + r"""
(async () => {
data = {updated_at: 40, research_workspace: {generation: 4, cards: [{card_id: 'a'}]}};
const pending = [];
request = () => new Promise(resolve => pending.push(resolve));
const snapshot = (updated, fields) => ({updated_at: updated, research_workspace: {generation: 4, cards: [{card_id: 'a', ...fields}]}});
const pin = steer('pin', {card_id: 'a'}).catch(error => error);
const exclude = steer('exclude', {card_id: 'a'});
pending[1](snapshot(42, {pinned: true, excluded: true})); await exclude;
pending[0](snapshot(41, {pinned: true}));
assert.match((await pin).message, /Research changed/);
assert.equal(data.research_workspace.cards[0].excluded, true);
// Server commit order may differ from client issue order; the newer snapshot wins.
const earlierRequest = steer('unpin', {card_id: 'a'});
const laterRequest = steer('prioritize', {card_id: 'a'});
pending[3](snapshot(43, {pinned: true})); await laterRequest;
pending[2](snapshot(44, {pinned: false, excluded: true})); await earlierRequest;
assert.equal(data.updated_at, 44); assert.equal(data.research_workspace.cards[0].pinned, false);
// Equal timestamps (including absent timestamps) fall back to issue order.
const tiedEarlier = steer('pin', {card_id: 'a'}).catch(error => error);
const tiedLater = steer('exclude', {card_id: 'a'});
pending[5](snapshot(44, {excluded: true})); await tiedLater;
pending[4](snapshot(44, {pinned: true}));
assert.match((await tiedEarlier).message, /Research changed/);
assert.equal(data.research_workspace.cards[0].excluded, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_stale_message_acknowledgment_preserves_composer_draft():
    source = SCRIPT.read_text()
    composer = source.split(
        '  var composer = sidebar.querySelector("[data-assistant-composer]");', 1
    )[1].split('  var stop = sidebar.querySelector("[data-assistant-stop]");', 1)[0]
    _run_node(_workspace_harness() + r"""
(async () => {
var busy = true, sending = false, followingLatest = false;
const composer = new El('form'), input = new El('textarea'), send = new El('button');
const sidebar = {querySelector: selector => selector === '[data-assistant-message]' ? input : send};
data = {updated_at: 40, research_workspace: {generation: 4, cards: [{card_id: 'a'}]}};
let resolveMessage;
request = () => new Promise(resolve => { resolveMessage = resolve; });
COMPOSER
input.value = 'Keep this request'; composer.listeners.submit({preventDefault() {}});
acceptSession({updated_at: 60, research_workspace: {generation: 6, cards: [{card_id: 'current'}]}}, ++sessionRequestSequence);
resolveMessage({updated_at: 50, research_workspace: {generation: 5, cards: [{card_id: 'stale'}]}});
await flush();
assert.equal(input.value, 'Keep this request');
assert.equal(data.research_workspace.generation, 6);
assert.deepEqual([...workspaceCards.keys()], ['current']);
assert.match(status.textContent, /Research changed/);
assert.equal(sending, false); assert.equal(send.disabled, false); assert.equal(refreshes, 0);
// The idle /messages path uses the same admission check and draft protection.
busy = false; composer.listeners.submit({preventDefault() {}});
acceptSession({updated_at: 80, research_workspace: {generation: 8, cards: []}}, ++sessionRequestSequence);
resolveMessage({updated_at: 70, research_workspace: {generation: 7, cards: []}});
await flush();
assert.equal(input.value, 'Keep this request'); assert.equal(data.research_workspace.generation, 8);
assert.equal(sending, false); assert.equal(refreshes, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("COMPOSER", composer))


def test_stale_mode_acknowledgment_cannot_restore_old_workspace():
    handler = (
        SCRIPT.read_text()
        .rsplit('  sidebar.querySelectorAll("[data-assistant-mode]").forEach', 1)[1]
        .split("  var constraintToggle =", 1)[0]
    )
    _run_node(_workspace_harness() + r"""
(async () => {
const button = new El('button'); button.dataset.assistantMode = 'suggest';
const sidebar = {querySelectorAll: () => [button]};
data = {updated_at: 40, research_workspace: {generation: 4, cards: [{card_id: 'old'}]}};
let resolveMode;
request = () => new Promise(resolve => { resolveMode = resolve; });
sidebar.querySelectorAll('[data-assistant-mode]').forEachHANDLER
button.listeners.click();
acceptSession({updated_at: 50, research_workspace: {generation: 5, cards: [{card_id: 'current'}]}}, ++sessionRequestSequence);
resolveMode({updated_at: 60, mode: 'suggest', research_workspace: {generation: 4, cards: [{card_id: 'old'}]}});
await flush();
assert.equal(data.research_workspace.generation, 5);
assert.equal(steerPayload('pin', {card_id: 'current'}).expected_generation, 5);
assert.deepEqual([...workspaceCards.keys()], ['current']);
assert.match(status.textContent, /Research changed/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("HANDLER", handler))


def test_partial_review_activity_distinguishes_unresolved_claims_from_unavailable():
    renderer = _between(SCRIPT.read_text(), "renderToolStep(", "renderMessages(")
    _run_node(r"""
const assert = require("node:assert/strict");
function element(tag, text) { return {text, children: [], appendChild(child) {this.children.push(child);}}; }
function toolLabel() { return "Evidence review"; }
function appendMarkdown() {}
function messageContent(message) { return message.content; }
function cardsFrom() { return []; }
RENDERER
function title(action) { return renderToolStep({action, content: "Review details"}).children[0].text; }
assert.match(title({name: "check_answer", ok: false, data: {status: "unverified", claim_judgments: [{verdict: "supported"}]}}), /complete with unresolved claims/);
assert.match(title({name: "check_answer", ok: false, data: {status: "not_checked", reason: "cost_ceiling"}}), /unavailable/);
assert.match(title({name: "search_cards", ok: false}), /unavailable/);
assert.match(title({name: "check_answer", ok: false, data: {claim_judgments: [{verdict: "unverified", method: "unreviewed_abstention"}]}}), /unavailable/);
assert.match(title({name: "check_answer", ok: false, data: {claim_judgments: []}}), /unavailable/);
assert.match(title({name: "check_answer", ok: true, data: {claim_judgments: [{verdict: "supported"}]}}), /complete$/);
""".replace("RENDERER", renderer))
