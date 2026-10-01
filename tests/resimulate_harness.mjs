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
    const self = this;
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

const doneView = {
  status: "done",
  reason: "",
  stale: false,
  stale_message: "",
  baseline_message: "",
  result: { text: "goldfish_turns_to_assembly: 0.400 probability by turn 3", metric: "goldfish_turns_to_assembly" },
  delta: {
    text: "goldfish_turns_to_assembly: 0.250 → 0.400 probability",
    metric: "goldfish_turns_to_assembly",
    unit: "probability",
    unseen_card_count: { text: "unseen_card_count: 7 → 4", before: 7, after: 4 },
  },
};

const views = {
  states: { id: "deck-1", simulation_view: { status: "none" } },
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
    simulation_view: doneView,
  },
};

const data = el("script", { id: "deck-document-data", type: "application/json" });
data.textContent = JSON.stringify(views[scenario] || views.states);
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
context.window.DeckLabBuilder = {
  railSection(id, title) {
    const existing = rail.querySelector(`[data-ext-section="${id}"]`);
    if (existing) return existing;
    const section = context.document.createElement("section");
    section.setAttribute("data-ext-section", id);
    const heading = context.document.createElement("h2");
    heading.textContent = title;
    section.appendChild(heading);
    rail.appendChild(section);
    section._title = title;
    return section;
  },
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

const section = rail.querySelector('[data-ext-section="simulation"]');

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
    title: section.querySelector("h2") ? section.querySelector("h2").textContent : "",
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
  const run = section.querySelector("[data-simulate-run]");
  run.dispatchEvent({ type: "click" });
  await flush();
  await flush();
  const running = snapshot();
  const firstDelay = await fireNext();
  const stillRunning = snapshot();
  const secondDelay = await fireNext();
  const finished = snapshot();
  process.stdout.write(JSON.stringify({
    sectionId: section.getAttribute("data-ext-section"),
    title: running.title,
    statusRole: running.statusRole,
    running: running.status,
    run: running.run,
    buttonType: running.buttonType,
    csrf: fetches[0] ? fetches[0].headers["X-CSRFToken"] : "",
    postUrl: fetches[0] ? fetches[0].url : "",
    delays: [firstDelay, secondDelay],
    polledStatus: stillRunning.status,
    result: finished.result,
    delta: finished.delta,
    unseen: finished.unseen,
    rerunAfterResult: finished.rerun,
  }));
} else {
  process.stdout.write(JSON.stringify(snapshot()));
}
