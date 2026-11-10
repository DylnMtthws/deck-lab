"""Execute the real public-event client reducer and timer without a browser."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src/sabermetrics/ui/static/deck-lab-assistant-sidebar.js"
)


def _client_helpers() -> str:
    source = SCRIPT.read_text()
    return (
        "function emptyPublicView("
        + source.split("  function emptyPublicView(", 1)[1].split(
            "  function showError(", 1
        )[0]
    )


def _run(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for public-event behavior tests")
    result = subprocess.run(
        [
            node,
            "-e",
            "const assert = require('node:assert/strict');\n"
            + _client_helpers()
            + script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


SETUP = r"""
const run = {run_id: 'a', generation: 2, deck_revision: 8, lifecycle: 'running', last_sequence: 2};
function snapshot(page, count, entities = {}, head = run) {
  return {contract_version: 'assistant.events.v1', run: head, mode: 'snapshot',
    operational: {cost_pending: true}, cursor: {last_sequence: head.last_sequence},
    snapshot_page: {token:'shared', page_index:page, page_count:count, through_sequence:2, entities}};
}
function delta(after, events, head = run) {
  return {contract_version:'assistant.events.v1', mode:'delta', run: head,
    cursor: {after_sequence: after, through_sequence: events.length ? events.at(-1).sequence : after}, events};
}
function event(sequence, kind = 'activity.finished', entity = {id:'op', run_id:'a'}) {
  return {run_id:'a', generation:2, sequence, kind, payload:{collection:'activity', entity}};
}
"""


def test_snapshot_pages_stage_atomically_and_cannot_mix_bundles() -> None:
    _run(SETUP + r"""
let result = reducePublicEvents(emptyPublicView(), snapshot(0, 2, {activity:[{id:'op',run_id:'a',state:'started'}]}));
assert.equal(result.view.run, null);
assert.equal(result.view.sequence, 0);
assert.equal(result.view.entities.activity, undefined);
assert.deepEqual(result.page, {token:'shared', index:1});
result = reducePublicEvents(result.view, snapshot(1, 2, {findings:[{id:'f',run_id:'a',text:'Exact fact'}]}));
assert.equal(result.view.sequence, 2);
assert.equal(result.view.run.lifecycle, 'running');
assert.equal(result.view.entities.activity.op.state, 'started');
assert.equal(result.view.entities.findings.f.text, 'Exact fact');
assert.equal(reducePublicEvents(emptyPublicView(), snapshot(1, 2)).reset, true);
const first = reducePublicEvents(emptyPublicView(), snapshot(0, 2));
const mixed = snapshot(1, 2); mixed.snapshot_page.token = 'other';
assert.equal(reducePublicEvents(first.view, mixed).reset, true);
""")


def test_terminal_head_waits_for_answer_events_and_deduplicates_replay() -> None:
    _run(SETUP + r"""
let view = reducePublicEvents(emptyPublicView(), snapshot(0, 1)).view;
const finished = {...run, lifecycle:'completed', last_sequence:4};
let result = reducePublicEvents(view, delta(2,[event(3)],finished));
assert.equal(result.more, true);
assert.equal(result.view.run.lifecycle, 'running', 'head alone cannot announce completion');
assert.equal(result.view.sequence, 3);
result = reducePublicEvents(result.view, delta(3,[event(3),event(4,'run.ended')],finished));
assert.equal(result.view.sequence, 4);
assert.equal(result.view.run.lifecycle, 'completed');
assert.equal(Object.keys(result.view.entities.activity).length, 1);
""")


def test_delta_gap_wrong_run_unknown_event_and_invalid_cursor_reset() -> None:
    _run(SETUP + r"""
const view = reducePublicEvents(emptyPublicView(), snapshot(0, 1)).view;
assert.equal(reducePublicEvents(view, delta(2,[event(4)], {...run,last_sequence:4})).reset,true);
assert.equal(reducePublicEvents(view, delta(1,[event(3)], {...run,last_sequence:3})).reset,true);
assert.equal(reducePublicEvents(view, delta(2,[event(3,'unknown')], {...run,last_sequence:3})).reset,true);
const alien = event(3); alien.run_id='other';
assert.equal(reducePublicEvents(view,delta(2,[alien],{...run,last_sequence:3})).reset,true);
const generation = event(3); generation.generation=1;
assert.equal(reducePublicEvents(view,delta(2,[generation],{...run,last_sequence:3})).reset,true);
assert.equal(reducePublicEvents(view,{contract_version:'assistant.events.v1',run,mode:'reset_required'}).reset,true);
assert.equal(reducePublicEvents(view,{contract_version:'future'}).legacy,true);
""")


def test_snapshot_enforces_page_and_entity_bounds() -> None:
    _run(SETUP + r"""
const alien = snapshot(0,1,{claims:[{id:'c',run_id:'other'}]});
assert.equal(reducePublicEvents(emptyPublicView(),alien).reset,true);
assert.equal(reducePublicEvents(emptyPublicView(),snapshot(0,65)).reset,true);
const huge=snapshot(0,1,{findings:[{id:'f',run_id:'a',text:'x'.repeat(128*1024)}]});
assert.equal(reducePublicEvents(emptyPublicView(),huge).reset,true);
""")


def test_timer_uses_server_anchor_not_local_wall_clock_and_freezes() -> None:
    _run(r"""
const run = {accepted_at:'2026-11-10T10:00:00Z',budget_started_at:'2026-11-10T09:59:45Z'};
const clock = {server:Date.parse('2026-11-10T10:01:00Z'), tick:1000};
Date.now = () => Date.parse('2040-01-01T00:00:00Z');
assert.equal(elapsedLabel(run,clock,1000),'1:15 elapsed');
assert.equal(elapsedLabel(run,clock,2000),'1:16 elapsed');
run.ended_at='2026-11-10T10:01:09Z';
assert.equal(elapsedLabel(run,clock,1000),'1:24 elapsed');
assert.equal(elapsedLabel(run,clock,100000),'1:24 elapsed');
assert.equal(elapsedLabel({...run,budget_started_at:null},clock,1000),'1:09 attempt elapsed');
assert.equal(elapsedLabel({...run,accepted_at:null,budget_started_at:null},clock,1000),'Timing unavailable');
assert.equal(elapsedLabel({...run,budget_started_at:'2099-01-01T00:00:00Z'},clock,1000),'Timing unavailable');
""")


def test_stop_targets_displayed_revision_but_other_mutations_use_current_deck() -> None:
    _run(r"""
var publicView = {run:{run_id:'a',generation:3,deck_revision:7}};
var data={turn_id:'legacy',revision:6};
var state={revision:9};
function researchWorkspace(){return {generation:2};}
assert.deepEqual(expectedTarget(true),{expected_run_id:'a',expected_generation:3,expected_revision:7});
assert.deepEqual(expectedTarget(false),{expected_run_id:'a',expected_generation:3,expected_revision:9});
assert.equal(terminalLabel({lifecycle:'completed',fulfillment:'complete',evidence_status:'partial'}),'Answer ready');
assert.equal(terminalLabel({lifecycle:'completed',fulfillment:'partial'}),'Answer incomplete');
assert.equal(terminalLabel({lifecycle:'completed',legacy_fulfillment:'nonanswer'}),'No substantive answer');
""")


def test_activity_timer_ticks_silently_and_terminal_review_remains_distinct() -> None:
    source = SCRIPT.read_text()
    activity = (
        "function publicRecords("
        + source.split("  function publicRecords(", 1)[1].split(
            "  function render()", 1
        )[0]
    )
    _run(
        r"""
class El {
  constructor(tag) {this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';this.open=false;}
  appendChild(child) {child.parentNode=this;this.children.push(child);return child;}
  replaceChildren() {this.children=[];}
  setAttribute(k,v) {this.attrs[k]=v;}
  addEventListener(name,fn) {this.listeners=this.listeners||{};this.listeners[name]=fn;}
  get lastElementChild() {return this.children.at(-1);}
}
function element(tag,text,cls){const n=new El(tag);n.textContent=text || '';n.className=cls;return n;}
let tick=1000, announcements=[], intervals=0, clears=0;
const performance={now:()=>tick};
const window={setInterval:()=>++intervals,clearInterval:()=>clears++};
const document={hidden:false};
const sidebar={querySelector:()=>null};
const transcript=new El('div');
var publicView={entities:{activity:{op:{id:'op',label:'Checking claims',state:'started'}}},operational:{},
  run:{run_id:'a',lifecycle:'running',budget_started_at:'2026-11-10T10:00:00Z',evidence_status:'partial',fulfillment:'unknown'}};
var publicClock={server:Date.parse('2026-11-10T10:00:10Z'),tick:1000};
var activityNode=null,activityTimer,activityLabel,activityDetails,activityHistory,activitySignature='',activityAnnounced='',timerTick=0;
var connectionLost=false,stopRequested=false,pageAlive=true,operationalPollStarted=0,data={turn_id:'a'};
function researchWorkspace(){return {};}
function showProgress(text){announcements.push(text);}
function updateJumpToLatest(){}
function progressText(){return 'Working';}
function currentTurn(){return [];}
function toolLabel(){return 'Research';}
"""
        + activity
        + r"""
renderActivity();
assert.equal(activityNode.attrs['aria-live'],'off');
assert.equal(activityTimer.attrs['aria-hidden'],'true');
assert.equal(activityTimer.textContent,'0:10 elapsed');
assert.equal(activityLabel.textContent,'Checking claims');
assert.equal(announcements.length,1);
tick+=1000;updateActivityTimer();
assert.equal(activityTimer.textContent,'0:11 elapsed');
assert.equal(announcements.length,1,'timer tick must not update polite live region');
renderActivity();assert.equal(announcements.length,1,'poll repeat must not announce again');
publicView.run={...publicView.run,lifecycle:'completed',ended_at:'2026-11-10T10:00:15Z',fulfillment:'complete'};
renderActivity();
assert.match(activityLabel.textContent,/Answer ready.*evidence review partial/);
assert.equal(activityNode.open,false);
assert.equal(clears,1);
assert.equal(activityTimer.hidden,true,'collapsed activity hides its changing clock');
activityNode.open=true;activityNode.listeners.toggle();
tick+=100000;updateActivityTimer();assert.equal(activityTimer.textContent,'0:15 elapsed');
assert.equal(activityTimer.hidden,false);
activityNode.open=false;activityNode.listeners.toggle();assert.equal(activityTimer.hidden,true);
connectionLost=true;renderActivity();assert.equal(activityNode.dataset.active,'false');
assert.match(activityLabel.textContent,/Updates unavailable.*Answer ready/);
"""
    )


def test_early_findings_render_as_unranked_literal_sources_without_script_links() -> (
    None
):
    source = SCRIPT.read_text()
    renderer = (
        "function publicRecords("
        + source.split("  function publicRecords(", 1)[1].split(
            "  function renderActivity()", 1
        )[0]
    )
    evidence = (
        "function renderReadableEvidence("
        + source.split("  function renderReadableEvidence(", 1)[1].split(
            "  function cardId(", 1
        )[0]
    )
    _run(
        r"""
class El {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.textContent='';}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  replaceChildren(){this.children=[];}
}
function element(tag,text){const node=new El(tag);node.textContent=text||'';return node;}
function flatten(node){return [node,...node.children.flatMap(flatten)];}
var publicFindingRuns=new Map();
const transcript=new El('div');
var publicView={run:{run_id:'a',deck_revision:4},entities:{findings:{f:{id:'f',run_id:'a',
 type:'unranked_candidate',text:'<script>not executable</script>',review_state:'retrieved_unranked',
 limitations:['Currentness not verified'],sources:[{title:'Card text',relevant_excerpt:'Actual source',source_url:'javascript:attack()'}]}}}};
"""
        + evidence
        + renderer
        + r"""
renderPublicFindings();
const group=publicFindingRuns.get('a');
assert.equal(transcript.children.length,1);
assert(flatten(group.node).some(node=>node.textContent==='Retrieved candidate · unranked'));
assert(flatten(group.node).some(node=>node.textContent==='<script>not executable</script>'));
assert(flatten(group.node).some(node=>node.textContent==='Actual source'));
assert.equal(flatten(group.node).filter(node=>node.tag==='a').length,0);
const before=group.records.get('f').node;
renderPublicFindings();
assert.equal(group.records.get('f').node,before,'finding identity survives repeated polls');
assert.equal(transcript.children.length,1);
publicView.entities.findings.f={...publicView.entities.findings.f,status:'withdrawn',review_state:'source_rendered'};
renderPublicFindings();
assert.equal(group.records.get('f').node.dataset.findingStatus,'withdrawn');
assert(flatten(group.node).some(node=>/This finding is withdrawn/.test(node.textContent)));
publicView.entities.findings.f={...publicView.entities.findings.f,status:'superseded'};
renderPublicFindings();
assert.equal(group.records.get('f').node.dataset.findingStatus,'superseded');
assert(flatten(group.node).some(node=>/This finding is superseded/.test(node.textContent)));
"""
    )


def test_shared_backend_trace_replays_to_matching_terminal_state():
    import json

    root = SCRIPT.parents[4]
    trace = json.loads(
        (root / "fixtures/assistant-events-v1/contract-trace.json").read_text()
    )
    _run("const fixture = " + json.dumps(trace) + r""";
let view=reducePublicEvents(emptyPublicView(),{
  contract_version:'assistant.events.v1',mode:'snapshot',run:fixture.baseline.run,
  snapshot_page:{token:'fixture',page_index:0,page_count:1,through_sequence:0,entities:fixture.baseline.entities},cursor:{last_sequence:0}
}).view;
view=reducePublicEvents(view,{
  contract_version:'assistant.events.v1',mode:'delta',run:fixture.expected.run,
  events:fixture.events,cursor:{after_sequence:0,through_sequence:3,last_sequence:3}
}).view;
assert.equal(view.run.lifecycle,fixture.expected.run.lifecycle);
assert.equal(view.run.fulfillment,'partial');
assert.equal(view.run.evidence_status,'unavailable');
assert.deepEqual(view.run.missing_obligations,['comparison']);
""")


def test_public_answer_segments_require_terminal_contiguous_exact_identity():
    _run(r"""
const hash='a'.repeat(64);
const first={id:'b0',run_id:'a',answer_id:'answer-a',answer_version:1,logical_text_hash:hash,segment_count:2,segment_index:0,text:'A complete ',review_state:'unverified'};
const second={...first,id:'b1',segment_index:1,text:'answer.'};
const view={run:{run_id:'a',lifecycle:'completed'},entities:{answer_blocks:{first,second}}};
assert.equal(completePublicAnswers({...view,run:{...view.run,lifecycle:'running'}}).length,0,'unreviewed model prose never publishes early');
assert.equal(completePublicAnswers({...view,entities:{answer_blocks:{first}}}).length,0,'missing transport segment is not a final answer');
assert.equal(completePublicAnswers({...view,entities:{answer_blocks:{first,second:{...second,run_id:'other'}}}}).length,0);
assert.equal(completePublicAnswers({...view,entities:{answer_blocks:{first,second:{...second,logical_text_hash:'b'.repeat(64)}}}}).length,0);
assert.equal(completePublicAnswers({...view,entities:{answer_blocks:{first,second,newer:{...first,id:'newer',answer_version:2}}}}).length,0,'incomplete new version must not fall back to old answer');
const [answer]=completePublicAnswers(view);
assert.equal(answer.text,'A complete answer.');
assert.equal(answer.reviewed,false);
assert(legacyAnswerMatches({role:'assistant',run_id:'a',content:answer.text},answer));
assert.equal(legacyAnswerMatches({role:'assistant',run_id:'other',content:answer.text},answer),false);
assert.equal(legacyAnswerMatches({role:'assistant',run_id:'a',content:'Different'},answer),false);
""")


def test_public_answer_terminal_before_legacy_race_renders_then_deduplicates():
    source = SCRIPT.read_text()
    renderer = (
        "function renderPublicAnswers("
        + source.split("  function renderPublicAnswers(", 1)[1].split(
            "  function renderActivity()", 1
        )[0]
    )
    _run(
        r"""
class El {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';this.clientHeight=100;}
  appendChild(child){if(child.parentNode) child.remove();child.parentNode=this;this.children.push(child);return child;}
  replaceChildren(){this.children.forEach(child=>child.parentNode=null);this.children=[];}
  setAttribute(key,value){this.attrs[key]=value;}
  remove(){if(this.parentNode){const index=this.parentNode.children.indexOf(this);if(index>=0)this.parentNode.children.splice(index,1);this.parentNode=null;}}
}
function element(tag,text,cls){const node=new El(tag);node.textContent=text||'';node.className=cls;return node;}
function flatten(node){return [node,...node.children.flatMap(flatten)];}
const transcript=new El('div');
const scrollSurface=transcript;
let scrollCalls=[];
function scrollTranscript(surface,newReply,anchor){scrollCalls.push({newReply,anchor});}
function transcriptAnchor(){return {ids:['older'],offset:10};}
function appendMarkdown(host,text,cards){host.appendChild(element('p',text));}
let followingLatest=false;
var publicAnswerRuns=new Map();
var data={messages:[{role:'user',run_id:'a',content:'Question'}]};
var publicView={run:{run_id:'a',lifecycle:'completed',deck_revision:2},entities:{answer_blocks:{
 b0:{id:'b0',run_id:'a',answer_id:'answer-a',answer_version:1,logical_text_hash:'a'.repeat(64),segment_count:2,segment_index:0,text:'Answer <script>',review_state:'unverified'},
 b1:{id:'b1',run_id:'a',answer_id:'answer-a',answer_version:1,logical_text_hash:'a'.repeat(64),segment_count:2,segment_index:1,text:' remains literal.',review_state:'unverified'}
}}};
"""
        + renderer
        + r"""
renderPublicAnswers();
assert.equal(transcript.children.length,1,'public final answer is visible even while legacy messages lag');
assert(flatten(transcript).some(node=>node.textContent==='Answer <script> remains literal.'));
assert(flatten(transcript).some(node=>/claim review incomplete or unavailable/.test(node.textContent)));
assert.equal(scrollCalls.length,1);
assert.deepEqual(scrollCalls[0].anchor,{ids:['older'],offset:10},'reader anchor survives public append');
const answerNode=transcript.children[0];
renderPublicAnswers();
assert.equal(transcript.children[0],answerNode,'unchanged polling preserves the reply node and focus targets');
assert.equal(scrollCalls.length,1);
data.messages.push({role:'assistant',run_id:'a',content:'Answer <script> remains literal.'});
renderPublicAnswers();
assert.equal(transcript.children.length,0,'legacy handoff removes only the fallback copy');
assert.equal(scrollCalls.length,2);
publicView={run:{run_id:'b',lifecycle:'running',steered_from_run_id:'a'},entities:{answer_blocks:{}}};
data.messages=[];
renderPublicAnswers();
assert.equal(transcript.children.length,1,'bounded previous-run answer history survives transcript rebuilding');
assert.equal(transcript.children[0].dataset.superseded,'true');
"""
    )
