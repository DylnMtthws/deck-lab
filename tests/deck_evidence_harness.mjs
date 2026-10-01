import fs from "node:fs";
import vm from "node:vm";

const scenario = process.argv[2];
const scriptPath = process.argv[3];
const source = fs.readFileSync(scriptPath, "utf8");

class ClassList {
  constructor(el) { this.el = el; }
  _parts() { return (this.el.className || "").split(/\s+/).filter(Boolean); }
  _set(parts) { this.el.className = [...new Set(parts)].join(" "); }
  add(...names) { this._set(this._parts().concat(names)); }
  remove(...names) { const drop = new Set(names); this._set(this._parts().filter((n) => !drop.has(n))); }
  contains(name) { return this._parts().includes(name); }
}
class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.classList = new ClassList(this);
    this.listeners = {};
    this._text = "";
    this.id = "";
    this.title = "";
    this.value = "";
    this.selected = false;
    this._mo = null;
  }
  get textContent() {
    const nested = this.children.map((child) => child.textContent).join("");
    if (this.children.length) return `${this._text || ""}${nested}`;
    return this._text;
  }
  set textContent(value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children.length = 0;
    this._text = String(value);
  }
  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
    if (key === "title") this.title = String(value);
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    if (key === "title") return this.title || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(node, ref) {
    node.parentNode = this;
    const index = ref ? this.children.indexOf(ref) : this.children.length;
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
    return node;
  }
  replaceChildren(...nodes) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children.length = 0;
    this._text = "";
    nodes.forEach((node) => this.appendChild(node));
    if (this._mo) this._mo.cb();
  }
  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    return true;
  }
}
function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    let rest = part;
    let tag = null;
    let id = null;
    const classes = [];
    const attrs = [];
    rest = rest.replace(/^([a-zA-Z][\w-]*)/, (_, name) => { tag = name.toUpperCase(); return ""; });
    rest = rest.replace(/#([\w-]+)/g, (_, name) => { id = name; return ""; });
    rest = rest.replace(/\.([\w-]+)/g, (_, name) => { classes.push(name); return ""; });
    rest.replace(/\[([^\]]+)\]/g, (_, raw) => {
      const eq = raw.indexOf("=");
      if (eq < 0) attrs.push([raw, null]);
      else attrs.push([raw.slice(0, eq), raw.slice(eq + 1).replace(/^["']|["']$/g, "")]);
      return "";
    });
    if (tag && el.tagName !== tag) return false;
    if (id && el.id !== id) return false;
    if (classes.some((name) => !el.classList.contains(name))) return false;
    return attrs.every(([name, value]) => {
      const actual = el.getAttribute(name);
      if (value == null) return actual != null;
      return String(actual) === String(value);
    });
  });
}
function walk(node, acc) {
  acc.push(node);
  (node.children || []).forEach((child) => walk(child, acc));
  return acc;
}
function queryAll(root, selector) {
  const nodes = [];
  walk(root, []).forEach((node) => { if (node !== root && matches(node, selector)) nodes.push(node); });
  return nodes;
}

function evidenceBody(overrides) {
  return Object.assign({
    commander_ids: ["kinnan"],
    window_days: 30,
    min_event_size: 16,
    denominator: 143,
    available: true,
    cards: { "oracle-ring": { lists: 102, rate: 102 / 143 } },
    explanations: {
      "oracle-ring": { role: "Ramp", source: "pool", priority: 2.5, reason: "Fast mana" }
    }
  }, overrides || {});
}

function baseState() {
  return {
    id: "deck-1",
    entries: [
      { id: "entry-cmd", oracle_id: "oracle-kinnan", name: "Kinnan", is_commander: true, role: "" },
      { id: "entry-ring", oracle_id: "oracle-ring", name: "Sol Ring", is_commander: false, role: "Ramp" }
    ]
  };
}

async function drain() {
  for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function mount(state, responses) {
  const html = new Element("html");
  const body = new Element("body");
  html.appendChild(body);
  const data = new Element("script");
  data.id = "deck-document-data";
  data.textContent = JSON.stringify(state);
  body.appendChild(data);
  const rail = new Element("aside");
  rail.className = "dl-stats-rail";
  body.appendChild(rail);
  const panel = new Element("aside");
  panel.className = "dl-card-panel";
  panel.setAttribute("data-card-panel", "");
  const slot = new Element("div");
  slot.setAttribute("data-card-panel-slot", "evidence");
  panel.appendChild(slot);
  rail.appendChild(panel);
  const table = new Element("div");
  table.id = "table-view";
  body.appendChild(table);
  function addRow(entry) {
    const row = new Element("div");
    row.className = "dl-deck-row";
    row.setAttribute("data-entry-id", entry.id);
    const name = new Element("span");
    name.className = "dl-card-name";
    name.textContent = entry.name;
    const actions = new Element("div");
    actions.className = "dl-row-actions";
    row.appendChild(name);
    row.appendChild(actions);
    table.appendChild(row);
    return row;
  }
  state.entries.forEach(addRow);

  const calls = [];
  const timers = [];
  let timerId = 1;
  const document = {
    documentElement: html,
    body,
    getElementById(id) {
      return walk(html, []).find((node) => node.id === id) || null;
    },
    querySelector(selector) { return queryAll(html, selector)[0] || null; },
    querySelectorAll(selector) { return queryAll(html, selector); },
    createElement(tag) { return new Element(tag); },
    addEventListener(type, fn) { html.addEventListener(type, fn); },
    dispatchEvent(event) { return html.dispatchEvent(event); }
  };
  const context = {
    console,
    document,
    MutationObserver: class {
      constructor(cb) { this.cb = cb; this.target = null; }
      observe(target) { this.target = target; target._mo = this; }
      disconnect() { if (this.target) this.target._mo = null; this.target = null; }
    },
    CustomEvent: class {
      constructor(type) { this.type = type; }
    },
    DeckLabBuilder: {
      getState() { return state; },
      railSection(id, title) {
        const existing = rail.querySelector(`[data-ext-section="${id}"]`);
        if (existing) return existing;
        const section = new Element("section");
        section.setAttribute("data-ext-section", id);
        const heading = new Element("h2");
        heading.textContent = title;
        section.appendChild(heading);
        rail.appendChild(section);
        return section;
      }
    },
    DeckLabCardPanel: { current() { return "entry-ring"; } },
    fetch(url) {
      calls.push(String(url));
      const body = responses[Math.min(calls.length - 1, responses.length - 1)];
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(JSON.parse(JSON.stringify(body)))
      });
    },
    setTimeout(fn, ms) {
      const id = timerId;
      timerId += 1;
      timers.push({ id, fn, ms: Number(ms) || 0 });
      return id;
    },
    clearTimeout(id) {
      const index = timers.findIndex((timer) => timer.id === id);
      if (index >= 0) timers.splice(index, 1);
    },
    encodeURIComponent,
    isFinite,
    Number,
    String,
    Math
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: scriptPath });
  return {
    document,
    state,
    calls,
    timers,
    panel,
    addRow,
    flushTimers() {
      const due = timers.splice(0, timers.length);
      due.forEach((timer) => timer.fn());
    }
  };
}

function snapshot(document) {
  return walk(document.documentElement, []).map((node) => ({
    text: node.textContent || "",
    title: node.title || ""
  }));
}

function badges(document) {
  return document.querySelectorAll("[data-evidence-badge]").map((node) => ({
    text: node.textContent,
    title: node.title
  }));
}

const scenarios = {
  async badge() {
    const ui = mount(baseState(), [evidenceBody()]);
    await drain();
    const recorded = mount(baseState(), [evidenceBody({ min_event_size: null, window_days: 30 })]);
    await drain();
    return {
      badges: badges(ui.document),
      statement: ui.document.querySelector("[data-evidence-statement]").textContent,
      role: ui.document.querySelector("[data-evidence-role]").textContent,
      added: ui.document.querySelector("[data-evidence-added]").textContent,
      reason: ui.document.querySelector("[data-evidence-reason]").textContent,
      unrecorded: badges(recorded.document)[0].title
    };
  },
  async absence() {
    const ui = mount(baseState(), [evidenceBody({
      available: false,
      denominator: 0,
      min_event_size: null,
      cards: {},
      explanations: {},
      window_days: 30
    })]);
    await drain();
    const before = ui.document.querySelector('[data-card-panel-slot="evidence"]').textContent;
    const slot = new Element("div");
    slot.setAttribute("data-card-panel-slot", "evidence");
    ui.panel.replaceChildren(slot);
    const after = ui.document.querySelector('[data-card-panel-slot="evidence"]').textContent;
    return { before, after, badges: badges(ui.document), elements: snapshot(ui.document) };
  },
  async window() {
    const first = evidenceBody({
      denominator: 10,
      min_event_size: 16,
      cards: { "oracle-ring": { lists: 5, rate: 0.5 } },
      explanations: {}
    });
    const refetched = evidenceBody({
      denominator: 10,
      cards: { "oracle-ring": { lists: 5, rate: 0.5 } },
      explanations: {}
    });
    const widened = evidenceBody({
      window_days: 90,
      denominator: 4,
      cards: { "oracle-ring": { lists: 1, rate: 0.25 } },
      explanations: {}
    });
    const ui = mount(baseState(), [first, refetched, widened]);
    await drain();
    const initial = {
      urls: ui.calls.slice(),
      basis: ui.document.querySelector("[data-evidence-basis]").textContent,
      badge: badges(ui.document)[0],
      label: ui.document.querySelector("[data-evidence-window]").getAttribute("aria-label"),
      options: ui.document.querySelector("[data-evidence-window]").children.map((node) => ({
        value: node.value,
        label: node.textContent
      })),
      heading: ui.document.querySelector('[data-ext-section="evidence"]').querySelector("h2").textContent
    };
    ui.document.dispatchEvent({ type: "deck-lab:render" });
    await drain();
    const sameRender = { urls: ui.calls.length, timers: ui.timers.length };
    ui.state.entries.push({ id: "entry-new", oracle_id: "oracle-new", name: "New", is_commander: false });
    ui.document.dispatchEvent({ type: "deck-lab:render" });
    ui.state.entries.push({ id: "entry-new-2", oracle_id: "oracle-new-2", name: "Newer", is_commander: false });
    ui.document.dispatchEvent({ type: "deck-lab:render" });
    await drain();
    const beforeTimer = { urls: ui.calls.length, timers: ui.timers.map((timer) => timer.ms) };
    ui.flushTimers();
    await drain();
    const afterOracle = { urls: ui.calls.slice(), badge: badges(ui.document)[0] };
    const select = ui.document.querySelector("[data-evidence-window]");
    select.value = "90";
    select.dispatchEvent({ type: "change" });
    await drain();
    return {
      initial,
      sameRender,
      beforeTimer,
      afterOracle,
      afterWindow: {
        urls: ui.calls.slice(),
        basis: ui.document.querySelector("[data-evidence-basis]").textContent,
        badge: badges(ui.document)[0]
      }
    };
  },
  async rates() {
    const ui = mount(baseState(), [evidenceBody()]);
    await drain();
    return { elements: snapshot(ui.document), denominator: "143" };
  }
};

const run = scenarios[scenario];
if (!run) {
  console.error(`unknown scenario ${scenario}`);
  process.exit(1);
}
run().then((result) => {
  process.stdout.write(`${JSON.stringify(result)}\n`);
}).catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
