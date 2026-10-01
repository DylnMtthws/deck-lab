import fs from "node:fs";
import vm from "node:vm";

const scenario = process.argv[2];
const root = new URL(".", import.meta.url);
const read = (name) => fs.readFileSync(new URL("../src/sabermetrics/ui/static/" + name, root), "utf8");
const builderSource = read("deck-lab-builder.js");
const statsSource = read("deck-lab-stats.js");
const panelSource = read("deck-lab-card-panel.js");
const feedbackSource = read("deck-lab-feedback.js");
const evidenceSource = read("deck-lab-evidence.js");
const simulateSource = read("deck-lab-simulate.js");

class ClassList {
  constructor(el) { this.el = el; }
  _parts() { return (this.el.className || "").split(/\s+/).filter(Boolean); }
  _set(parts) { this.el.className = [...new Set(parts)].join(" "); }
  add(...names) { this._set(this._parts().concat(names)); }
  remove(...names) { const drop = new Set(names); this._set(this._parts().filter((n) => !drop.has(n))); }
  contains(name) { return this._parts().includes(name); }
  toggle(name, force) {
    if (force === true) this.add(name);
    else if (force === false) this.remove(name);
    else if (this.contains(name)) this.remove(name);
    else this.add(name);
    return this.contains(name);
  }
}
class Style {
  constructor() { this._props = {}; }
  setProperty(name, value) { this._props[name] = String(value); this[name] = String(value); }
}
class Element {
  constructor(tag, owner) {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = owner;
    this.children = [];
    this.childNodes = this.children;
    this.parentNode = null;
    this.attributes = {};
    this.style = new Style();
    this.className = "";
    this.classList = new ClassList(this);
    this.listeners = {};
    this._text = "";
    this.hidden = false;
    this.id = "";
    this.type = "";
    this.value = "";
    this.disabled = false;
    this.tabIndex = 0;
    this.src = "";
    this.alt = "";
    const self = this;
    this.dataset = new Proxy({}, {
      get(obj, key) {
        if (typeof key !== "string") return undefined;
        return key in obj ? obj[key] : (self.attributes["data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase())] || "");
      },
      set(obj, key, value) {
        obj[key] = String(value);
        self.setAttribute("data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()), value);
        return true;
      },
    });
  }
  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(value) { this.children.length = 0; this._text = String(value); }
  set innerHTML(value) { this._text = String(value); }
  get innerHTML() { return this._text; }
  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
    if (key === "hidden") this.hidden = true;
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    delete this.attributes[String(name)];
    if (name === "hidden") this.hidden = false;
  }
  appendChild(child) {
    if (child == null) return child;
    if (typeof child === "string") child = this.ownerDocument.createTextNode(child);
    if (child.parentNode && child.parentNode.removeChild) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...nodes) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children.length = 0;
    this._text = "";
    nodes.forEach((n) => this.appendChild(n));
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) { this.children.splice(idx, 1); child.parentNode = null; }
    return child;
  }
  closest(selector) {
    let node = this;
    while (node && node.tagName) {
      if (matches(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  click() { this.dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} }); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn(event));
    if (event.bubbles && this.parentNode && this.parentNode.dispatchEvent) this.parentNode.dispatchEvent(event);
    return true;
  }
  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentNode;
    }
    return false;
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 80, height: 24, right: 80, bottom: 24 }; }
  scrollIntoView() {}
}

function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    if (!part || part === "*") return part === "*";
    let rest = part;
    let tag = null;
    let id = null;
    const classes = [];
    const attrs = [];
    rest = rest.replace(/^([a-zA-Z][\w-]*)/, (_, t) => { tag = t.toUpperCase(); return ""; });
    rest = rest.replace(/#([\w-]+)/g, (_, v) => { id = v; return ""; });
    rest = rest.replace(/\.([\w-]+)/g, (_, v) => { classes.push(v); return ""; });
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
  (node.children || []).forEach((child) => { if (child && child.tagName) walk(child, acc); });
  return acc;
}
function queryAll(root, selector) {
  const parts = String(selector).trim().split(/\s+/).filter(Boolean);
  let current = [root];
  parts.forEach((part) => {
    const next = [];
    current.forEach((node) => {
      walk(node, []).forEach((child) => {
        if (child !== node && matches(child, part) && !next.includes(child)) next.push(child);
      });
    });
    current = next;
  });
  current.forEach = Array.prototype.forEach;
  return current;
}

function deck() {
  return {
    id: "deck-1",
    title: "Rail Deck",
    revision: 1,
    zones: [
      { id: "zone-main", name: "Main deck", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-considering", name: "Considering", x: 80, y: 40, width: 200, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "Tap: add mana.", color_identity: ["G", "U"], image_uri: "https://cards.test/kinnan.png", role: "", oracle_id: "oracle-cmd" },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "Add {C}{C}.", color_identity: [], image_uri: "https://cards.test/sol-ring.png", role: "ramp", oracle_id: "oracle-ring" },
      { id: "entry-land", name: "Tropical Island", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Land", mana_cost: "", mana_value: 0, oracle_text: "Add {G} or {U}.", color_identity: ["G", "U"], image_uri: "https://cards.test/trop.png", role: "land", oracle_id: "oracle-land" },
      { id: "entry-tutor", name: "Worldly Tutor", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Instant", mana_cost: "{G}", mana_value: 1, oracle_text: "Search.", color_identity: ["G"], image_uri: "https://cards.test/tutor.png", role: "tutor", oracle_id: "oracle-tutor" },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: {
      commander_count: 1, library_count: 3, library_target: 99, total_count: 4, legal: false, issues: [],
      entry_issues: { "entry-ring": [{ message: "Not legal in Commander" }] },
    },
    tags: [{ id: "tag-1", name: "Turbo" }],
    tag_suggestions: [],
  };
}

function makeDocument(store) {
  const document = {
    activeElement: null,
    createElement(tag) { return new Element(tag, document); },
    createTextNode(text) { const node = new Element("#text", document); node.textContent = String(text == null ? "" : text); return node; },
    createElementNS(_ns, tag) { return document.createElement(tag); },
    getElementById(id) { return walk(document.documentElement, []).find((el) => el.id === id) || null; },
    querySelector(sel) { return queryAll(document.documentElement, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(document.documentElement, sel); },
    addEventListener(type, fn) { document.documentElement.addEventListener(type, fn); },
    dispatchEvent(event) { return document.documentElement.dispatchEvent(event); },
  };
  const root = new Element("html", document);
  const body = new Element("body", document);
  document.documentElement = root;
  document.body = body;
  root.appendChild(body);
  const data = document.createElement("script");
  data.id = "deck-document-data";
  data.textContent = JSON.stringify(deck());
  body.appendChild(data);
  const builder = document.createElement("div");
  builder.className = "dl-builder";
  builder.setAttribute("data-shared", "false");
  builder.setAttribute("data-playmat-enabled", "false");
  const rail = document.createElement("aside");
  rail.className = "dl-stats-rail";
  const head = document.createElement("div");
  head.className = "dl-builder-rail-head";
  head.setAttribute("data-rail-tabs", "");
  const list = document.createElement("div");
  list.className = "dl-tabs";
  list.setAttribute("role", "tablist");
  ["card", "deck", "tools"].forEach((name, index) => {
    const tab = document.createElement("button");
    tab.className = "dl-tab";
    tab.setAttribute("data-rail-tab", name);
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", index === 0 ? "true" : "false");
    tab.tabIndex = index === 0 ? 0 : -1;
    tab.textContent = name;
    list.appendChild(tab);
  });
  head.appendChild(list);
  const pin = document.createElement("button");
  pin.setAttribute("data-card-panel-pin", "");
  pin.className = "dl-icon-button";
  head.appendChild(pin);
  rail.appendChild(head);
  const panes = document.createElement("div");
  panes.className = "dl-rail-panes";
  const cardPane = document.createElement("div");
  cardPane.setAttribute("data-rail-pane", "card");
  const panel = document.createElement("aside");
  panel.className = "dl-card-panel";
  panel.setAttribute("data-card-panel", "");
  cardPane.appendChild(panel);
  const deckPane = document.createElement("div");
  deckPane.setAttribute("data-rail-pane", "deck");
  deckPane.setAttribute("hidden", "");
  const curve = document.createElement("div");
  curve.id = "mana-curve";
  curve.className = "dl-mana-curve";
  const heading = document.createElement("h2");
  heading.id = "mana-curve-heading";
  heading.textContent = "Mana curve";
  deckPane.appendChild(heading);
  deckPane.appendChild(curve);
  const zones = document.createElement("div");
  zones.id = "zone-stats";
  deckPane.appendChild(zones);
  const colors = document.createElement("div");
  colors.id = "color-stats";
  colors.setAttribute("hidden", "");
  deckPane.appendChild(colors);
  const toolsPane = document.createElement("div");
  toolsPane.setAttribute("data-rail-pane", "tools");
  toolsPane.setAttribute("hidden", "");
  panes.append(cardPane, deckPane, toolsPane);
  rail.appendChild(panes);
  builder.appendChild(rail);
  const table = document.createElement("div");
  table.id = "table-view";
  builder.appendChild(table);
  body.appendChild(builder);
  const meta = document.createElement("meta");
  meta.setAttribute("name", "csrf-token");
  meta.setAttribute("content", "token");
  body.appendChild(meta);
  const title = document.createElement("button");
  title.id = "deck-title";
  body.appendChild(title);
  return { document, store };
}

function contextFor(document, store, fetches) {
  const win = {
    matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; },
    crypto: { randomUUID: () => "uuid-1" },
    DeckLabSelects: { refresh() {} },
    prompt() { return null; },
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {},
  };
  const ctx = {
    console,
    document,
    setTimeout,
    clearTimeout,
    localStorage: {
      getItem: (key) => (key in store ? store[key] : null),
      setItem: (key, value) => { store[key] = String(value); },
      removeItem: (key) => { delete store[key]; },
    },
    fetch(url, opts) {
      fetches.push({ url: String(url), method: (opts && opts.method) || "GET", body: opts && opts.body });
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ cards: {}, deck: null, available: false, denominator: 0, status: "not_simulated" }),
      });
    },
    AbortController,
    URL,
    URLSearchParams,
    location: win.location,
    navigator: {},
    Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: win.crypto,
  };
  ctx.window = win;
  win.document = document;
  win.setTimeout = setTimeout;
  win.clearTimeout = clearTimeout;
  const context = vm.createContext(ctx);
  win.localStorage = ctx.localStorage;
  return context;
}

function run(source, context, filename) {
  vm.runInContext(source, context, { filename });
  return context;
}

function indexOf(root, selector) {
  const node = root.querySelector(selector);
  if (!node) return -1;
  const all = [];
  walk(root, all);
  return all.indexOf(node);
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

const store = {};
const fetches = [];
const { document } = makeDocument(store);
const context = contextFor(document, store, fetches);
run(builderSource, context, "deck-lab-builder.js");
const api = context.window.DeckLabBuilder;

let result = {};
if (scenario === "tabs") {
  run(statsSource, context, "deck-lab-stats.js");
  const tabs = document.querySelectorAll("[data-rail-tab]");
  const before = tabs.map((tab) => tab.getAttribute("data-rail-tab") + ":" + tab.getAttribute("aria-selected") + ":" + document.querySelector('[data-rail-pane="' + tab.getAttribute("data-rail-tab") + '"]').hasAttribute("hidden"));
  tabs.find((tab) => tab.getAttribute("data-rail-tab") === "deck").dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const afterClick = document.querySelector('[data-rail-tab="deck"]').getAttribute("aria-selected");
  const deckHidden = document.querySelector('[data-rail-pane="deck"]').hasAttribute("hidden");
  const cardHidden = document.querySelector('[data-rail-pane="card"]').hasAttribute("hidden");
  const saved = store["deck-lab-rail-tab:deck-1"];
  document.querySelector('[data-rail-tab="deck"]').dispatchEvent({ type: "keydown", key: "ArrowRight", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const afterArrow = document.querySelector('[data-rail-tab="tools"]').getAttribute("aria-selected");
  const focused = document.activeElement && document.activeElement.getAttribute("data-rail-tab");
  const toolsHidden = document.querySelector('[data-rail-pane="tools"]').hasAttribute("hidden");
  const again = Object.assign({}, store);
  const secondFetches = [];
  const secondDoc = makeDocument(again).document;
  const second = contextFor(secondDoc, again, secondFetches);
  run(builderSource, second, "deck-lab-builder.js");
  run(statsSource, second, "deck-lab-stats.js");
  result = {
    before,
    afterClick,
    deckHidden,
    cardHidden,
    saved,
    afterArrow,
    focused,
    toolsHidden,
    restored: secondDoc.querySelector('[data-rail-tab="tools"]').getAttribute("aria-selected"),
    restoredDeckHidden: secondDoc.querySelector('[data-rail-pane="deck"]').hasAttribute("hidden"),
  };
} else if (scenario === "routing") {
  run(statsSource, context, "deck-lab-stats.js");
  run(evidenceSource, context, "deck-lab-evidence.js");
  run(simulateSource, context, "deck-lab-simulate.js");
  run(feedbackSource, context, "deck-lab-feedback.js");
  await flush();
  function parentPane(id) {
    const section = document.querySelector('[data-ext-section="' + id + '"]');
    const pane = section && section.closest("[data-rail-pane]");
    return pane ? pane.getAttribute("data-rail-pane") : null;
  }
  const pips = document.querySelector('[data-ext-section="pips"]');
  result = {
    pips: parentPane("pips"),
    odds: parentPane("odds"),
    sample: parentPane("sample-hand"),
    feedback: parentPane("feedback"),
    evidence: parentPane("evidence"),
    simulation: parentPane("simulation"),
    bar: !!(pips && pips.querySelector(".dl-pip-bar")),
    src: !!(pips && pips.textContent.includes("src")),
  };
} else if (scenario === "card-order" || scenario === "role" || scenario === "late") {
  if (scenario !== "late") {
    run(panelSource, context, "deck-lab-card-panel.js");
  }
  if (scenario === "late") {
    run(panelSource, context, "deck-lab-card-panel.js");
    run(statsSource, context, "deck-lab-stats.js");
    const panel = document.querySelector("[data-card-panel]");
    const bins = document.querySelectorAll(".dl-curve-bin");
    result = {
      name: panel.querySelector("[data-card-panel-name]") && panel.querySelector("[data-card-panel-name]").textContent,
      bins: bins.length,
      count: bins[1] && bins[1].querySelector("b") && bins[1].querySelector("b").textContent,
    };
  } else if (scenario === "card-order") {
    const panel = document.querySelector("[data-card-panel]");
    context.window.DeckLabCardPanel.show("entry-ring");
    const order = [
      "[data-card-panel-name]",
      "[data-card-panel-cost]",
      "[data-card-panel-type]",
      "[data-card-panel-oracle]",
      "[data-card-panel-meta]",
      "[data-card-panel-feedback]",
      "[role=note]",
      '[data-card-panel-slot="evidence"]',
    ].map((selector) => indexOf(panel, selector));
    result = {
      order,
      rising: order.every((value, index) => index === 0 || value > order[index - 1]),
      roleControl: panel.querySelector("select.dl-select") ? panel.querySelector("select.dl-select").className : null,
      feedbackLabels: panel.querySelectorAll("[data-card-panel-feedback] button").map((btn) => btn.getAttribute("aria-label")),
    };
  } else {
    const panel = document.querySelector("[data-card-panel]");
    const commands = [];
    api.command = function (cmds) { commands.push(cmds); return Promise.resolve(true); };
    context.window.DeckLabCardPanel.show("entry-ring");
    const select = panel.querySelector("select.dl-role-select");
    select.value = "draw";
    select.dispatchEvent({ type: "change", bubbles: true, preventDefault() {}, stopPropagation() {} });
    result = { commands, value: select.value };
  }
} else if (scenario === "curve") {
  const filters = [];
  const orig = api.setEntryFilter.bind(api);
  api.setEntryFilter = function (fn, label) { filters.push(label == null ? null : label); return orig(fn, label); };
  run(statsSource, context, "deck-lab-stats.js");
  const heading = document.getElementById("mana-curve-heading").textContent;
  const bins = document.querySelectorAll(".dl-curve-bin");
  const counts = bins.map((bin) => bin.querySelector("b") && bin.querySelector("b").textContent);
  const labels = bins.map((bin) => bin.querySelector("span") && bin.querySelector("span").textContent);
  bins[1].dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const active = document.querySelectorAll(".dl-curve-bin.is-active").length;
  bins[1].dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  result = { heading, counts, labels, filters, activeAfterFirst: active, activeAfterSecond: document.querySelectorAll(".dl-curve-bin.is-active").length };
} else if (scenario === "odds") {
  run(statsSource, context, "deck-lab-stats.js");
  const section = document.querySelector('[data-ext-section="odds"]');
  const sentence = section.querySelector(".dl-odds-sentence");
  result = {
    resultText: section.querySelector("[data-odds-result]").textContent,
    resultClass: section.querySelector("[data-odds-result]").className,
    sentence: sentence.textContent.replace(/\s+/g, " ").trim(),
    needInside: sentence.contains(section.querySelector("[data-odds-need]")),
    seenInside: sentence.contains(section.querySelector("[data-odds-seen]")),
    categoryInside: sentence.contains(section.querySelector("[data-odds-category]")),
    detail: section.querySelector("[data-odds-detail]").textContent,
  };
} else if (scenario === "hand") {
  const focused = [];
  api.focusEntry = function (id) { focused.push(id); return true; };
  run(statsSource, context, "deck-lab-stats.js");
  document.querySelector("[data-sample-draw]").dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const cards = document.querySelector("[data-sample-cards]");
  const first = cards.children[0];
  first.dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  result = {
    count: cards.children.length,
    className: first.className,
    image: !!(first.querySelector("img") && first.querySelector("img").src),
    name: first.textContent,
    entryId: first.getAttribute("data-entry-id"),
    focused,
    summary: document.querySelector("[data-sample-lands]").textContent,
  };
} else if (scenario === "verdict") {
  run(feedbackSource, context, "deck-lab-feedback.js");
  await flush();
  const section = document.querySelector('[data-ext-section="feedback"]');
  const group = section.querySelector("[data-feedback-verdict-group]");
  const area = section.querySelector("[data-verdict-comment]");
  area.value = "Needs more interaction";
  area.dispatchEvent({ type: "input", bubbles: true, preventDefault() {}, stopPropagation() {} });
  await new Promise((resolve) => setTimeout(resolve, 1700));
  await flush();
  result = {
    segmented: Boolean(group.querySelector(".dl-segments")),
    label: group.getAttribute("aria-label"),
    buttons: section.querySelectorAll("[data-verdict]").map((btn) => btn.textContent),
    saves: fetches.filter((call) => call.method === "PUT" && String(call.url).includes("/feedback/deck")),
  };
} else {
  throw new Error("unknown scenario " + scenario);
}

process.stdout.write(JSON.stringify(result));
process.exit(0);
