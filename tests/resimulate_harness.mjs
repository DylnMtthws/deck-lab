import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(process.argv[2], "utf8");
const scenario = process.argv[3] || "states";

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this._text = "";
    this.hidden = false;
    this.id = "";
    this.type = "";
    this.content = "";
    this.disabled = false;
    this.title = "";
    this.open = false;
    const self = this;
    this.classList = {
      contains(name) { return String(self.className || "").split(/\s+/).includes(name); },
      toggle(name, force) {
        const names = new Set(String(self.className || "").split(/\s+/).filter(Boolean));
        const enabled = force === undefined ? !names.has(name) : !!force;
        if (enabled) names.add(name); else names.delete(name);
        self.className = Array.from(names).join(" ");
        self.attributes.class = self.className;
        return enabled;
      },
    };
    this.dataset = new Proxy({}, {
      get(_target, key) {
        if (typeof key !== "string") return undefined;
        const attr = "data-" + key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
        return self.attributes[attr];
      },
      set(_target, key, value) {
        const attr = "data-" + key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
        self.attributes[attr] = String(value);
        return true;
      },
    });
  }

  get textContent() {
    if (this.children.length) return this.children.map((child) => child.textContent).join("");
    return this._text;
  }

  set textContent(value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children.length = 0;
    this._text = String(value ?? "");
  }

  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
    if (key === "content") this.content = String(value);
  }

  getAttribute(name) {
    const key = String(name);
    return Object.prototype.hasOwnProperty.call(this.attributes, key) ? this.attributes[key] : null;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  showModal() { this.open = true; this.setAttribute("open", "open"); }
  close() { this.open = false; delete this.attributes.open; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, String(name)); }

  addEventListener(type, fn) {
    (this.listeners[type] || (this.listeners[type] = [])).push(fn);
  }

  dispatchEvent(event) {
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    return true;
  }

  querySelector(selector) {
    return walk(this).find((node) => node !== this && matches(node, selector)) || null;
  }

  querySelectorAll(selector) {
    return walk(this).filter((node) => node !== this && matches(node, selector));
  }
}

function walk(node, acc = []) {
  acc.push(node);
  (node.children || []).forEach((child) => walk(child, acc));
  return acc;
}

function matches(el, selector) {
  return String(selector).split(",").some((part) => {
    const piece = part.trim();
    if (!piece) return false;
    let rest = piece;
    let tag = null;
    let id = null;
    const classes = [];
    const attrs = [];
    rest = rest.replace(/^([a-zA-Z][\w-]*)/, (_, found) => { tag = found.toUpperCase(); return ""; });
    rest = rest.replace(/#([\w-]+)/g, (_, found) => { id = found; return ""; });
    rest = rest.replace(/\.([\w-]+)/g, (_, found) => { classes.push(found); return ""; });
    rest.replace(/\[([^\]=]+)(?:=["']?([^"'\]]*)["']?)?\]/g, (_, name, value) => {
      attrs.push([name, value === undefined ? null : value]);
      return "";
    });
    if (tag && el.tagName !== tag) return false;
    if (id && el.id !== id) return false;
    const classList = String(el.className || "").split(/\s+/).filter(Boolean);
    if (classes.some((name) => !classList.includes(name))) return false;
    return attrs.every(([name, value]) => {
      const actual = el.getAttribute(name);
      if (value == null) return actual != null;
      return String(actual) === String(value);
    });
  });
}

function el(tag, attrs) {
  const node = new Element(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (key === "id") node.id = value;
    if (key === "class") node.className = value;
    node.setAttribute(key === "class" ? "class" : key, value);
  });
  return node;
}

const html = new Element("html");
const head = el("head");
const body = el("body");
html.appendChild(head);
html.appendChild(body);
const meta = el("meta", { name: "csrf-token", content: "csrf-test" });
meta.content = "csrf-test";
head.appendChild(meta);

const builder = el("div", { class: "dl-builder" });
builder.dataset.shared = scenario === "shared" ? "true" : "false";
const rail = el("aside", { class: "dl-stats-rail" });
builder.appendChild(rail);
body.appendChild(builder);

const opener = el("button", { "data-goldfish-open": "", "aria-controls": "goldfish-dialog" });
const dialog = el("dialog", { id: "goldfish-dialog" });
const title = el("h2", { id: "goldfish-dialog-title" });
title.textContent = "Goldfish simulator";
dialog.appendChild(title);
dialog.appendChild(el("button", { "data-goldfish-close": "" }));
const readiness = el("p", { "data-goldfish-readiness": "" });
dialog.appendChild(readiness);
const slot = el("div", { "data-goldfish-sim-slot": "" });
dialog.appendChild(slot);
body.appendChild(opener);
body.appendChild(dialog);

const saveState = el("button", { id: "save-state", class: "is-ok" });
const saveLabel = el("span", { "data-save-label": "" });
saveLabel.textContent = "Saved";
saveState.appendChild(saveLabel);
body.appendChild(saveState);

const doneView = {
  status: "done",
  reason: "",
  stale: false,
  stale_message: "",
  baseline_message: "",
  result: {
    text: "goldfish_turns_to_assembly: 0.400 probability by turn 3",
    metric: "goldfish_turns_to_assembly",
    games: 20000,
    objective_turn: 3,
    modeled_cards: 26,
    inert_card_count: 2,
    unauthored_card_count: 58,
    unseen_card_count: 60,
    measures: "Chance of assembling the modeled plan by turn 3.",
    does_not_measure: "No opponent actions or deck-wide win probability.",
  },
  delta: {
    text: "goldfish_turns_to_assembly: 0.250 → 0.400 probability",
    metric: "goldfish_turns_to_assembly",
    unit: "probability",
    unseen_card_count: { text: "unseen_card_count: 7 → 4", before: 7, after: 4 },
  },
};

const diagnosticsView = {
  status: "done",
  result: {
    text: "Native solo simulation completed.",
    games: 200,
    diagnostics: {
      schema_version: "decklab.goldfish-diagnostics.v1",
      settings: { games: 200, seed: 17, checkpoints: { development_turn: 5, wipe_turn: 5, finish_turn: 7 }, engine_version: "test-engine", settings_fingerprint: "settings-abc" },
      coverage: { modeled_cards: 28, inert_cards: 3, unauthored_cards: 55 },
      comparison: {
        comparable: true, settings_fingerprint: "settings-abc",
        note: "Descriptive differences only; no winner verdict is inferred.",
        history_truncated: true,
        history: [
          { revision: 6, comparable: true, metric_deltas: {
            opening: { kept_hands: { before: 0.5, after: 0.75, delta: 0.25, unit: "probability", comparable: true } },
            commander_loss: { recovery: { before: null, after: null, delta: null, comparable: false, reason: "Metric is unmeasured in one or both runs." } },
          } },
          { revision: 5, comparable: false, reason: "Engine settings changed.", metric_deltas: {} },
        ],
      },
      tests: {
        opening: { status: "supported", metrics: { kept_hands: { value: 0.75, unit: "probability", numerator: 15, denominator: 20, ci95: [0.55, 0.9] } }, evidence: ["20 sampled opening hands"] },
        development: { status: "partial", metrics: { mean_missed_land_drops: { value: 1.25, unit: "land drops" } }, limitations: ["Only authored cards are modeled"] },
        commander_loss: { status: "unsupported", metrics: { recovery: { value: null, numerator: 0, denominator: 20 } }, limitations: ["Commander-loss scenario is unavailable"] },
        creature_wipe: { status: "unsupported", metrics: {} },
        finish: { status: "partial", metrics: { positive_finish_proof_rate: {value: .1, unit: "proportion", numerator: 2, denominator: 20}, verified_finish_rate: {value: 1, unit: "proportion", numerator: 2, denominator: 2}, win_conversion: { value: 0.2, unit: "probability", numerator: 4, denominator: 20 } }, evidence: ["Board-state conversion check"] },
      },
    },
  },
};

diagnosticsView.result.heldout_validation = {
  status: "descriptive", training_seed: 17, validation_seed: 456, note: "No improvement ranking is inferred.",
  baseline_report: {tests: {opening: {status: "supported", metrics: {keep: {value: .5, unit: "probability", denominator: 200, ci95: {low: .4, high: .6}}}}, finish: {status: "unsupported", metrics: {win: {value: null, unit: "probability"}}}}},
  candidate_report: {tests: {opening: {status: "supported", metrics: {keep: {value: .6, unit: "probability", denominator: 200, ci95: {low: .5, high: .7}}}}, finish: {status: "unsupported", metrics: {win: {value: null, unit: "probability"}}}}},
  comparison: {metric_deltas: {opening: {keep: {comparable: true, delta: .1, unit: "probability"}}, finish: {win: {comparable: false, reason: "Metric is unmeasured in one or both runs."}}}},
};

if (scenario === "diagnostics-zero") diagnosticsView.result.diagnostics.tests.finish.metrics.positive_finish_proof_rate.value = 0;

const views = {
  states: { id: "deck-1", revision: 7, validation: { legal: true, total_count: 100 }, simulation_view: { status: "none" } },
  stale: {
    id: "deck-1",
    simulation_view: {
      status: "done",
      stale: true,
      stale_message: "Out of date: the list changed since this run",
      result: doneView.result,
      delta: doneView.delta,
      baseline_message: "",
      reason: "",
    },
  },
  absence: {
    id: "deck-1",
    simulation_view: {
      status: "not_simulated",
      reason: "Not simulated: commander_unsupported: no simulator model exists for Kinnan",
      stale: false,
      baseline_message: "No earlier simulation to compare",
      result: null,
      delta: null,
    },
  },
  shared: {
    id: "deck-1",
    revision: 7,
    validation: { legal: true, total_count: 100 },
    simulation_view: doneView,
  },
  invalid: { id: "deck-1", revision: 7, validation: { legal: false, total_count: 99 }, simulation_view: { status: "none" } },
  pending: { id: "deck-1", revision: 7, validation: { legal: true, total_count: 100 }, simulation_view: { status: "none" } },
  direct: { id: "deck-1", revision: 7, validation: { legal: true, total_count: 100 }, simulation_view: { status: "none" } },
  "refresh-stale": { id: "deck-1", revision: 7, validation: { legal: true, total_count: 100 }, simulation_view: doneView },
  "diagnostics-zero": { id: "deck-1", revision: 7, validation: { legal: true, total_count: 100 }, simulation_view: diagnosticsView },
  diagnostics: { id: "deck-1", revision: 7, validation: { legal: true, total_count: 100 }, simulation_view: diagnosticsView },
};

const initialState = views[scenario] || views.states;
const data = el("script", { id: "deck-document-data", type: "application/json" });
data.textContent = JSON.stringify(initialState);
body.appendChild(data);

const documentRef = {
  documentElement: html,
  getElementById(id) {
    return walk(html).find((node) => node.id === id) || null;
  },
  querySelector(selector) {
    return walk(html).find((node) => matches(node, selector)) || null;
  },
  createElement(tag) { return new Element(tag); },
  addEventListener(type, fn) { html.addEventListener(type, fn); },
};

const pending = [];
const fetches = [];
let fetchHandler = () => ({ status: 500, body: {} });

const context = {
  console,
  JSON,
  encodeURIComponent,
  document: documentRef,
  setTimeout(fn, ms) {
    const id = pending.length + 1;
    pending.push({ id, fn, ms, cleared: false, fired: false });
    return id;
  },
  clearTimeout(id) {
    const item = pending.find((timer) => timer.id === id);
    if (item) item.cleared = true;
  },
  addEventListener() {},
  location: { hash: scenario === "direct" ? "#goldfish" : "" },
  fetch(url, opts) {
    const record = {
      url: String(url),
      method: (opts && opts.method) || "GET",
      headers: (opts && opts.headers) || {},
      body: opts && opts.body,
    };
    fetches.push(record);
    const response = fetchHandler(record);
    return Promise.resolve({
      ok: response.status >= 200 && response.status < 300,
      status: response.status,
      json: () => Promise.resolve(response.body),
    });
  },
};
context.window = context;
const saveSnapshot = { pending: scenario === "pending", failed: scenario === "failed" };
context.window.DeckLabBuilder = {
  shared: scenario === "shared",
  getState() { return initialState; },
  getSaveState() { return saveSnapshot; },
};

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function fireNext() {
  const item = pending.find((timer) => !timer.cleared && !timer.fired);
  if (!item) return null;
  item.fired = true;
  item.fn();
  await flush();
  await flush();
  return item.ms;
}

vm.createContext(context);
vm.runInContext(source, context);

const section = slot;

function snapshot() {
  const status = section.querySelector("[data-simulate-status]");
  const result = section.querySelector("[data-simulate-result]");
  const delta = section.querySelector("[data-simulate-delta]");
  const unseen = section.querySelector("[data-simulate-unseen]");
  const baseline = section.querySelector("[data-simulate-baseline]");
  const stale = section.querySelector("[data-simulate-stale]");
  const run = section.querySelector("[data-simulate-run]");
  const rerun = section.querySelector("[data-simulate-rerun]");
  return {
    sectionId: dialog.id,
    title: dialog.querySelector("#goldfish-dialog-title") ? dialog.querySelector("#goldfish-dialog-title").textContent : "",
    statusRole: status ? status.getAttribute("role") : "",
    status: status && !status.hidden ? status.textContent : "",
    result: result && !result.hidden ? result.textContent : "",
    delta: delta && !delta.hidden ? delta.textContent : "",
    unseen: unseen && !unseen.hidden ? unseen.textContent : "",
    baseline: baseline && !baseline.hidden ? baseline.textContent : "",
    stale: stale && !stale.hidden ? stale.textContent : "",
    run: run ? run.textContent : "",
    runHidden: run ? run.hidden === true : true,
    rerun: rerun && rerun.hidden !== true ? rerun.textContent : "",
    buttonType: run ? run.type : "",
    runDisabled: run ? run.disabled : true,
    readiness: readiness.textContent,
    coverage: section.querySelector(".dl-sim-coverage")?.textContent || "",
    diagnosticsText: section.querySelector("[data-simulate-diagnostics]")?.textContent || "",
    diagnosticCardCount: section.querySelectorAll("[data-diagnostic-test]").length,
    missingDiagnostics: section.querySelector("[data-simulate-diagnostics-missing]")?.textContent || "",
    runMeta: section.querySelector(".dl-sim-note")?.textContent || "",
    dialogOpen: dialog.open,
    openerDisabled: opener.disabled,
  };
}

if (scenario === "states") {
  fetchHandler = (record) => {
    if (record.method === "POST") {
      return {
        status: 202,
        body: { id: "sim-1", status: "queued", status_url: "/api/decks/deck-1/simulations/latest" },
      };
    }
    const gets = fetches.filter((item) => item.method === "GET").length;
    if (gets === 1) return { status: 200, body: { status: "running" } };
    return { status: 200, body: doneView };
  };
  opener.dispatchEvent({ type: "click" });
  const run = section.querySelector("[data-simulate-run]");
  run.dispatchEvent({ type: "click" });
  await flush();
  await flush();
  const running = snapshot();
  html.dispatchEvent({ type: "deck-lab:render", detail: { state: initialState } });
  const runningAfterRender = snapshot();
  const firstDelay = await fireNext();
  const stillRunning = snapshot();
  const secondDelay = await fireNext();
  const finished = snapshot();
  html.dispatchEvent({ type: "deck-lab:render", detail: { state: initialState } });
  const afterRender = snapshot();
  process.stdout.write(JSON.stringify({
    sectionId: dialog.id,
    title: running.title,
    statusRole: running.statusRole,
    running: running.status,
    run: running.run,
    buttonType: running.buttonType,
    csrf: fetches[0] ? fetches[0].headers["X-CSRFToken"] : "",
    postUrl: fetches[0] ? fetches[0].url : "",
    postBody: fetches[0] ? fetches[0].body : "",
    delays: [firstDelay, secondDelay],
    polledStatus: stillRunning.status,
    result: finished.result,
    delta: finished.delta,
    unseen: finished.unseen,
    missingDiagnostics: finished.missingDiagnostics,
    rerunAfterResult: finished.rerun,
    resultAfterRender: afterRender.result,
    statusAfterRender: afterRender.status,
    runningAfterRender: runningAfterRender.status,
    disabledAfterRunningRender: runningAfterRender.runDisabled,
    dialogOpen: finished.dialogOpen,
  }));
} else if (scenario === "error") {
  fetchHandler = () => ({ status: 409, body: { detail: "Deck changed before the run started. Save and retry." } });
  opener.dispatchEvent({ type: "click" });
  section.querySelector("[data-simulate-run]").dispatchEvent({ type: "click" });
  await flush();
  await flush();
  process.stdout.write(JSON.stringify(snapshot()));
} else if (["invalid", "pending", "failed", "shared"].includes(scenario)) {
  opener.dispatchEvent({ type: "click" });
  const run = section.querySelector("[data-simulate-run]");
  if (run) run.dispatchEvent({ type: "click" });
  await flush();
  process.stdout.write(JSON.stringify({ ...snapshot(), postCount: fetches.filter((item) => item.method === "POST").length }));
} else if (scenario === "refresh-stale") {
  fetchHandler = () => ({ status: 200, body: { status: "done", stale: true, stale_message: "Out of date: the list changed since this run", result: doneView.result, delta: null } });
  initialState.revision = 8;
  documentRef.addEventListener("deck-lab:render", () => {});
  html.dispatchEvent({ type: "deck-lab:render", detail: { state: initialState } });
  await flush();
  await flush();
  process.stdout.write(JSON.stringify({ ...snapshot(), getUrl: fetches[0] && fetches[0].url }));
} else if (scenario === "diagnostics") {
  process.stdout.write(JSON.stringify(snapshot()));
} else {
  process.stdout.write(JSON.stringify(snapshot()));
}
