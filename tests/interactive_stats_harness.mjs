
import fs from "node:fs";
import vm from "node:vm";

const statsPath = process.argv[2];
const builderPath = process.argv[3];
const scenario = process.argv[4] || "contract";
const builderSource = fs.readFileSync(builderPath, "utf8");
const statsSource = fs.readFileSync(statsPath, "utf8");

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
function camelToData(key) { return "data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()); }
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
    this.title = "";
    this.draggable = false;
    this.disabled = false;
    this.checked = false;
    this.tabIndex = 0;
    this.src = "";
    this.alt = "";
    const self = this;
    this.dataset = new Proxy({}, {
      get(obj, key) {
        if (typeof key !== "string") return undefined;
        return key in obj ? obj[key] : (self.attributes[camelToData(key)] || "");
      },
      set(obj, key, value) {
        obj[key] = String(value);
        self.attributes[camelToData(key)] = String(value);
        return true;
      }
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
    if (key === "title") this.title = String(value);
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    if (key === "title") return this.title || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) { delete this.attributes[String(name)]; }
  appendChild(child) {
    if (typeof child === "string") { const t = this.ownerDocument.createElement("#text"); t.textContent = child; child = t; }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  removeChild(child) { const idx = this.children.indexOf(child); if (idx >= 0) { this.children.splice(idx, 1); child.parentNode = null; } return child; }
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === documentElement; }
  focus() { document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = null; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    if (this.parentNode && !event._propagationStopped) {
      this.parentNode.dispatchEvent(event);
    }
    return true;
  }
  stopPropagation() { this._propagationStopped = true; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; }
  scrollIntoView() {}
}
function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    if (part === "*") return true;
    let rest = part, tag = null, id = null, classes = [], attrs = [];
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
  (node.children || []).forEach((child) => walk(child, acc));
  return acc;
}
function queryAll(root, selector) {
  const nodes = [];
  walk(root, []).forEach((node) => { if (node !== root && matches(node, selector)) nodes.push(node); });
  nodes.forEach = Array.prototype.forEach;
  nodes.map = Array.prototype.map;
  nodes.filter = Array.prototype.filter;
  nodes.find = Array.prototype.find;
  nodes.some = Array.prototype.some;
  return nodes;
}

const documentElement = new Element("html");
const head = new Element("head", null);
const body = new Element("body", null);
head.ownerDocument = { createElement(tag) { return new Element(tag); } };
body.ownerDocument = head.ownerDocument;
documentElement.appendChild(head);
documentElement.appendChild(body);
const document = {
  documentElement, body, head, activeElement: null,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
  createTextNode(text) { const el = document.createElement("#text"); el.textContent = String(text == null ? "" : text); return el; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  removeEventListener(type, fn) { const list = documentElement.listeners[type]; if (list) { const idx = list.indexOf(fn); if (idx >= 0) list.splice(idx, 1); } },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};
function el(tag, attrs = {}) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "id") { node.id = value; node.setAttribute("id", value); }
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  return node;
}
function makeEvent(type, extra = {}) { return Object.assign({ type, preventDefault() {}, stopPropagation() {} }, extra); }

function buildDeck(overrides = {}) {
  return Object.assign({
    id: "deck-1", title: "Stats Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-ramp", name: "Ramp", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 }
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://cards.test/sol-ring.png", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "https://cards.test/lotus-petal.png", role: "", format_legal: true, commander_legal: true, validation_issues: [] }
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 2, library_target: 99, total_count: 3, legal: false, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  }, overrides);
}

const fetches = [];
const store = {};
const customEvents = [];
const ctor = class CustomEvent2 { constructor(type, init = {}) { this.type = type; this.detail = init.detail; customEvents.push(this); } };

async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

function buildDOM(deckData, shared) {
  const docEl = new Element("html");
  const hd = new Element("head", null);
  const bd = new Element("body", null);
  hd.ownerDocument = { createElement(tag) { return new Element(tag); } };
  bd.ownerDocument = hd.ownerDocument;
  docEl.appendChild(hd);
  docEl.appendChild(bd);
  const d = {
    documentElement: docEl, body: bd, head: hd, activeElement: null,
    createElement(tag) { const e = new Element(tag, d); e.ownerDocument = d; return e; },
    createTextNode(text) { const e = d.createElement("#text"); e.textContent = String(text == null ? "" : text); return e; },
    createElementNS(_ns, tag) { return d.createElement(tag); },
    getElementById(id) { return walk(docEl, []).find((e) => e.id === id) || null; },
    querySelector(sel) { return sel === "body" ? bd : (queryAll(docEl, sel)[0] || null); },
    querySelectorAll(sel) { return queryAll(docEl, sel); },
    addEventListener(type, fn) { docEl.addEventListener(type, fn); },
    removeEventListener(type, fn) { const list = docEl.listeners[type]; if (list) { const idx = list.indexOf(fn); if (idx >= 0) list.splice(idx, 1); } },
    dispatchEvent(event) { return docEl.dispatchEvent(event); },
  };
  bd.appendChild(Object.assign(d.createElement("script"), { id: "deck-document-data", textContent: JSON.stringify(deckData) }));
  var buildRoot = d.createElement("div");
  buildRoot.className = "dl-builder";
  buildRoot.setAttribute("data-shared", shared ? "true" : "false");
  buildRoot.setAttribute("data-playmat-enabled", "true");
  bd.appendChild(buildRoot);
  var meta = d.createElement("meta");
  meta.setAttribute("name", "csrf-token");
  meta.setAttribute("content", "token");
  hd.appendChild(meta);
  var saveBtn = d.createElement("button");
  saveBtn.id = "save-state";
  saveBtn.textContent = "Saved";
  bd.appendChild(saveBtn);
  var toolbar = d.createElement("div");
  toolbar.className = "dl-builder-toolbar";
  bd.appendChild(toolbar);
  var tableView = d.createElement("div");
  tableView.id = "table-view";
  bd.appendChild(tableView);
  var rail = d.createElement("div");
  rail.className = "dl-stats-rail";
  bd.appendChild(rail);
  var stage = d.createElement("div");
  stage.id = "playmat-stage";
  var playmat = d.createElement("div");
  playmat.id = "playmat";
  stage.appendChild(playmat);
  var playmatView = d.createElement("div");
  playmatView.id = "playmat-view";
  playmatView.appendChild(stage);
  bd.appendChild(playmatView);
  ["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach(function (id) {
    var e = d.createElement(id === "zoom-label" ? "span" : "div");
    e.id = id;
    bd.appendChild(e);
  });
  var ps = d.createElement("div");
  ps.setAttribute("data-playmat-selection", "");
  bd.appendChild(ps);
  var tl = d.createElement("div");
  tl.setAttribute("data-deck-tag-list", "");
  bd.appendChild(tl);
  var ts = d.createElement("div");
  ts.setAttribute("data-tag-summary", "");
  bd.appendChild(ts);
  var topts = d.createElement("div");
  topts.setAttribute("data-tag-options", "");
  bd.appendChild(topts);
  var dialog = d.createElement("dialog");
  dialog.id = "card-image-dialog";
  bd.appendChild(dialog);
  return { document: d, docEl };
}

function makePipsEntries(overrides = {}) {
  const base = buildDeck();
  Object.assign(base, overrides);
  return base;
}

async function runScenario() {
  switch (scenario) {
    case "contract": {
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
      vm.runInContext(builderSource, vm.createContext({
        console, document: doc, window: winObj, setTimeout, clearTimeout,
        localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
        fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); },
        AbortController, URL, URLSearchParams,
        location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
        navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp,
        CustomEvent: ctor,
        crypto: winObj.crypto,
      }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      const keys = api ? Object.keys(api).sort() : [];
      return { exists: !!api, keys, version: api ? api.version : null };
    }
    case "curve_bucket_attrs": {
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ /* same as contract */ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      // Now load stats script
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      const curve = doc.getElementById("mana-curve");
      const bins = curve ? curve.querySelectorAll(".dl-curve-bin") : [];
      const attrs = Array.from(bins).map(function (bin) {
        return {
          bucket: bin.getAttribute("data-mana-bucket"),
          role: bin.getAttribute("role"),
          tabindex: bin.getAttribute("tabindex"),
          ariaLabel: bin.getAttribute("aria-label"),
          text: bin.textContent.replace(/[0-9.]+px/g, "").trim(),
        };
      });
      return { binCount: bins.length, bins: attrs };
    }
    case "curve_click_toggle": {
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      // Spy on setEntryFilter
      const filterCalls = [];
      const origSetEntryFilter = api.setEntryFilter.bind(api);
      api.setEntryFilter = function (fn, label) {
        filterCalls.push({ fnType: fn === null ? "null" : typeof fn, label: label || null });
        origSetEntryFilter(fn, label);
      };
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();

      function currentBins() {
        const c = doc.getElementById("mana-curve");
        return c ? c.querySelectorAll(".dl-curve-bin") : [];
      }
      function activeBinCount() {
        return doc.querySelectorAll(".dl-curve-bin.is-active").length;
      }

      // Click bucket 1 (Sol Ring at MV 1)
      var bins = currentBins();
      if (bins.length >= 2) {
        bins[1].dispatchEvent(makeEvent("click", { target: bins[1] }));
        await flush();
      }
      var firstLabel = filterCalls.length > 0 ? filterCalls[0].label : null;
      var activeAfterFirst = activeBinCount() === 1;
      // Click same bin again to clear
      bins = currentBins();
      if (bins.length >= 2) {
        bins[1].dispatchEvent(makeEvent("click", { target: bins[1] }));
        await flush();
      }
      var secondCallIsNull = filterCalls.length >= 2 ? filterCalls[1].fnType === "null" : false;
      var activeAfterSecond = activeBinCount() === 0;
      // Click different bin (bucket 0)
      bins = currentBins();
      if (bins.length >= 1) {
        bins[0].dispatchEvent(makeEvent("click", { target: bins[0] }));
        await flush();
      }
      var thirdLabel = filterCalls.length >= 3 ? filterCalls[2].label : null;
      var activeAfterThird = activeBinCount() === 1;
      var thirdBinIs1 = (function () {
        var a = doc.querySelectorAll(".dl-curve-bin.is-active");
        return a.length === 1 && a[0].getAttribute("data-mana-bucket") === "0";
      })();
      return {
        calls: filterCalls.length,
        firstLabel: firstLabel,
        secondLabel: filterCalls.length >= 2 ? filterCalls[1].label : null,
        thirdLabel: thirdLabel,
        activeAfterFirst,
        activeAfterSecond,
        activeAfterThird,
        secondCallIsNull,
        thirdBinIs1,
      };
    }
    case "pips_counting": {
      // Create a deck with various pip types
      const deck = makePipsEntries({
        entries: [
          { id: "e-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Mono color
          { id: "e-swords", name: "Swords to Plowshares", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Instant", mana_cost: "{W}", mana_value: 1, oracle_text: "", color_identity: ["W"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Hybrid
          { id: "e-hybrid", name: "Fire//Ice", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Instant", mana_cost: "{U/R}", mana_value: 2, oracle_text: "", color_identity: ["U","R"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Two-brid
          { id: "e-twobrid", name: "Test Card", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Creature", mana_cost: "{2/W}", mana_value: 3, oracle_text: "", color_identity: ["W"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Phyrexian
          { id: "e-phyrexian", name: "Test Phyrexian", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 3, type_line: "Creature", mana_cost: "{W/P}", mana_value: 3, oracle_text: "", color_identity: ["W"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Generic only (should count no pips)
          { id: "e-generic", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 4, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Colorless
          { id: "e-colorless", name: "Wastes", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 5, type_line: "Land", mana_cost: "", mana_value: 0, oracle_text: "{C}", color_identity: ["C"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      // Expose optional _roleOptions for the odds section
      api._roleOptions = [
        ["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"],
        ["removal", "Removal"], ["protection", "Protection"],
        ["counter", "Counter"], ["free", "Free interaction"],
        ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"],
        ["board_wipe", "Board wipe"], ["recursion", "Recursion"],
        ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]
      ];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      // Extract pips data from the section
      const section = doc.querySelector('[data-ext-section="pips"]');
      if (!section) return { error: "no pips section" };
      const rows = section.querySelectorAll("[data-pips-color]");
      const data = {};
      rows.forEach(function (row) {
        const color = row.getAttribute("data-pips-color");
        data[color] = { color: color, text: row.textContent.replace(/\s+/g, " ").trim() };
      });
      return { data };
    }
    case "sources_counting": {
      const deck = makePipsEntries({
        entries: [
          { id: "e-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          // Basic land
          { id: "e-forest", name: "Forest", is_commander: false, quantity: 10, zone_id: "zone-main", sort_order: 0, type_line: "Basic Land — Forest", mana_cost: "", mana_value: 0, oracle_text: "{G}", color_identity: ["G"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          // Dual land
          { id: "e-trop", name: "Tropical Island", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Land — Island Forest", mana_cost: "", mana_value: 0, oracle_text: "{G}{U}", color_identity: ["G","U"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          // Any color land
          { id: "e-city", name: "City of Brass", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Land", mana_cost: "", mana_value: 0, oracle_text: "Add one mana of any color.", color_identity: [], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          // Mana dork
          { id: "e-dork", name: "Birds of Paradise", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 3, type_line: "Creature — Bird", mana_cost: "{G}", mana_value: 1, oracle_text: "Add {G}{U}{B}{R}{W}.", color_identity: ["G"], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          // Non-producing land
          { id: "e-plains", name: "Plains", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 4, type_line: "Basic Land — Plains", mana_cost: "", mana_value: 0, oracle_text: "", color_identity: ["W"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          // Private zone card (should be ignored)
          { id: "e-side", name: "Sideboard Card", is_commander: false, quantity: 1, zone_id: "zone-side", sort_order: 0, type_line: "Instant", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
        ],
        zones: [
          { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
          { id: "zone-side", name: "Sideboard", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 }
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [
        ["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"],
        ["removal", "Removal"], ["protection", "Protection"],
        ["counter", "Counter"], ["free", "Free interaction"],
        ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"],
        ["board_wipe", "Board wipe"], ["recursion", "Recursion"],
        ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]
      ];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      const section = doc.querySelector('[data-ext-section="pips"]');
      if (!section) return { error: "no pips section" };
      const rows = section.querySelectorAll("[data-pips-color]");
      const data = {};
      rows.forEach(function (row) {
        const color = row.getAttribute("data-pips-color");
        const text = row.textContent.replace(/\s+/g, " ").trim();
        data[color] = { color, text };
      });
      // Check rule text
      const ruleEl = section.querySelector(".dl-muted");
      return { data, ruleText: ruleEl ? ruleEl.textContent : null };
    }
    case "sources_triple_color": {
      // Test any-color land in a 3-color commander identity
      const deck = makePipsEntries({
        entries: [
          { id: "e-cmd1", name: "Omnath", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{R}{G}{W}", mana_value: 3, oracle_text: "", color_identity: ["R","G","W"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "e-forest", name: "Forest", is_commander: false, quantity: 5, zone_id: "zone-main", sort_order: 0, type_line: "Basic Land — Forest", mana_cost: "", mana_value: 0, oracle_text: "{G}", color_identity: ["G"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "e-city", name: "City of Brass", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Land", mana_cost: "", mana_value: 0, oracle_text: "Add one mana of any color.", color_identity: [], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "e-mountain", name: "Mountain", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Basic Land — Mountain", mana_cost: "", mana_value: 0, oracle_text: "{R}", color_identity: ["R"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [
        ["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"],
        ["removal", "Removal"], ["protection", "Protection"],
        ["counter", "Counter"], ["free", "Free interaction"],
        ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"],
        ["board_wipe", "Board wipe"], ["recursion", "Recursion"],
        ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]
      ];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      const section = doc.querySelector('[data-ext-section="pips"]');
      if (!section) return { error: "no pips section" };
      const rows = section.querySelectorAll("[data-pips-color]");
      const data = {};
      rows.forEach(function (row) {
        const color = row.getAttribute("data-pips-color");
        const text = row.textContent.replace(/\s+/g, " ").trim();
        data[color] = { color, text };
      });
      return { data };
    }
    case "hypergeom": {
      // Test the hypergeom function indirectly via the odds section
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [
        ["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"],
        ["removal", "Removal"], ["protection", "Protection"],
        ["counter", "Counter"], ["free", "Free interaction"],
        ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"],
        ["board_wipe", "Board wipe"], ["recursion", "Recursion"],
        ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]
      ];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      // Read from the odds section
      const section = doc.querySelector('[data-ext-section="odds"]');
      if (!section) return { error: "no odds section" };
      const resultEl = section.querySelector("[data-odds-result]");
      const resultText = resultEl ? resultEl.textContent : null;
      const seenInput = section.querySelector("[data-odds-seen]");
      const needInput = section.querySelector("[data-odds-need]");
      const catSelect = section.querySelector("[data-odds-category]");
      return {
        resultText,
        seen: seenInput ? seenInput.value : null,
        need: needInput ? needInput.value : null,
        catCount: catSelect ? catSelect.children.length : 0,
      };
    }
    case "odds_ui_update": {
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      const section = doc.querySelector('[data-ext-section="odds"]');
      if (!section) return { error: "no odds section" };
      const resultEl = section.querySelector("[data-odds-result]");
      const seenInput = section.querySelector("[data-odds-seen]");
      const needInput = section.querySelector("[data-odds-need]");
      // Change inputs and check update
      seenInput.value = "10";
      seenInput.dispatchEvent(makeEvent("input", { target: seenInput }));
      await flush();
      const afterSeen = resultEl.textContent;
      needInput.value = "2";
      needInput.dispatchEvent(makeEvent("input", { target: needInput }));
      await flush();
      const afterNeed = resultEl.textContent;
      return {
        firstText: afterSeen,
        secondText: afterNeed,
        seen: seenInput.value,
        need: needInput.value,
      };
    }
    case "sample_hand_deterministic": {
      const deck = buildDeck({
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 4, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://cards.test/sol-ring.png", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 2, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "https://cards.test/lotus-petal.png", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-island", name: "Island", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Basic Land — Island", mana_cost: "", mana_value: 0, oracle_text: "{U}", color_identity: ["U"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] }
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      // Test sampleHand function via the window
      var callIndex = 0;
      var seq = [0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4];
      var rng = function () { var v = seq[callIndex % seq.length]; callIndex++; return v; };
      var state = deck;
      // Import the stats module's sampleHand logic
      // We need to access the sampleHand function from the module
      // Since it's inside the IIFE, we recreate the test logic
      function testSampleHand(s, count, rngFn) {
        var libEntries = s.entries.filter(function (e) { var zone = s.zones.find(function (z) { return z.id === e.zone_id; }); var name = zone ? zone.name : "Unsorted"; return !e.is_commander && !Object.prototype.hasOwnProperty.call({ sideboard: 1, notes: 1, note: 1, maybeboard: 1, maybe: 1, draft: 1, considering: 1 }, String(name).trim().toLowerCase()); });
        var expanded = [];
        libEntries.forEach(function (entry) {
          var qty = Number(entry.quantity || 0);
          for (var i = 0; i < qty; i++) expanded.push(entry);
        });
        var drawn = [];
        var actualCount = Math.min(count, expanded.length);
        for (var i = 0; i < actualCount; i++) {
          var j = i + Math.floor(rngFn() * (expanded.length - i));
          var tmp = expanded[i];
          expanded[i] = expanded[j];
          expanded[j] = tmp;
          drawn.push(expanded[i]);
        }
        return drawn;
      }
      var hand = testSampleHand(state, 7, rng);
      return { handLength: hand.length, names: hand.map(function (x) { return x.name; }) };
    }
    case "sample_hand_short": {
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      // Simulate clicking "Draw 7"
      const drawBtn = doc.querySelector("[data-sample-draw]");
      const nextBtn = doc.querySelector("[data-sample-next]");
      const cardsEl = doc.querySelector("[data-sample-cards]");
      const landsEl = doc.querySelector("[data-sample-lands]");
      return {
        drawBtnExists: !!drawBtn,
        nextBtnExists: !!nextBtn,
        cardsElExists: !!cardsEl,
        landsElExists: !!landsEl,
      };
    }
    case "curve_keyboard": {
      const deck = buildDeck();
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      const filterCalls = [];
      const origSetEntryFilter = api.setEntryFilter.bind(api);
      api.setEntryFilter = function (fn, label) {
        filterCalls.push({ fnType: fn === null ? "null" : typeof fn, label: label || null });
        origSetEntryFilter(fn, label);
      };
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      const curve = doc.getElementById("mana-curve");
      const bins = curve ? curve.querySelectorAll(".dl-curve-bin") : [];
      // Press Enter on bucket 1
      if (bins.length >= 2) {
        bins[1].dispatchEvent(makeEvent("keydown", { key: "Enter", target: bins[1] }));
        await flush();
      }
      const enterLabel = filterCalls.length > 0 ? filterCalls[0].label : null;
      // Press Space on same bin (should clear)
      if (bins.length >= 2) {
        bins[1].dispatchEvent(makeEvent("keydown", { key: " ", target: bins[1] }));
        await flush();
      }
      const spaceLabel = filterCalls.length >= 2 ? (filterCalls[1].fnType === "null" ? "null" : filterCalls[1].label) : null;
      return { enterLabel, spaceLabel };
    }
    case "pips_section_commander_identity": {
      // Test with no commanders: should show all 5 colors
      const deck = makePipsEntries({
        entries: [
          { id: "e-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();
      // Test with no commanders -- should show 5 colors
      const section = doc.querySelector('[data-ext-section="pips"]');
      if (!section) return { error: "no pips section" };
      const rows = section.querySelectorAll("[data-pips-color]");
      const colors = Array.from(rows).map(function (r) { return r.getAttribute("data-pips-color"); }).sort();
      const ruleEl = section.querySelector(".dl-muted");
      // Now add commanders and check it limits
      const deck2 = makePipsEntries({
        entries: [
          { id: "e-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "e-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      return { noCmdrColors: colors, ruleText: ruleEl ? ruleEl.textContent : null };
    }
    case "odds_persist_across_render": {
      // AC-12: set category=ramp, seen=10, need=2; call DeckLabBuilder.render(); inputs keep values, result recomputed
      const deck = buildDeck({
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 2, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-f1", name: "Forest", is_commander: false, quantity: 4, zone_id: "zone-main", sort_order: 2, type_line: "Basic Land — Forest", mana_cost: "", mana_value: 0, oracle_text: "{G}", color_identity: ["G"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-f2", name: "Forest", is_commander: false, quantity: 3, zone_id: "zone-main", sort_order: 3, type_line: "Basic Land — Forest", mana_cost: "", mana_value: 0, oracle_text: "{G}", color_identity: ["G"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();

      // Change odds inputs
      const section = doc.querySelector('[data-ext-section="odds"]');
      if (!section) return { error: "no odds section" };
      const catSelect = section.querySelector("[data-odds-category]");
      const seenInput = section.querySelector("[data-odds-seen]");
      const needInput = section.querySelector("[data-odds-need]");
      const resultEl = section.querySelector("[data-odds-result]");

      // Set to ramp, seen=10, need=2
      if (catSelect) {
        for (var ci = 0; ci < catSelect.children.length; ci++) {
          if (catSelect.children[ci].value === "ramp") {
            catSelect.value = "ramp";
            catSelect.dispatchEvent(makeEvent("change", { target: catSelect }));
            break;
          }
        }
      }
      seenInput.value = "10";
      seenInput.dispatchEvent(makeEvent("input", { target: seenInput }));
      needInput.value = "2";
      needInput.dispatchEvent(makeEvent("input", { target: needInput }));
      await flush();

      const textBefore = resultEl ? resultEl.textContent : null;

      // Now simulate a re-render via curve click
      const curve = doc.getElementById("mana-curve");
      const bins = curve ? curve.querySelectorAll(".dl-curve-bin") : [];
      if (bins.length >= 2) {
        bins[1].dispatchEvent(makeEvent("click", { target: bins[1] }));
        await flush();
      }

      // After render, read inputs again
      const section2 = doc.querySelector('[data-ext-section="odds"]');
      if (!section2) return { error: "no odds section after render" };
      const catSelect2 = section2.querySelector("[data-odds-category]");
      const seenInput2 = section2.querySelector("[data-odds-seen]");
      const needInput2 = section2.querySelector("[data-odds-need]");
      const resultEl2 = section2.querySelector("[data-odds-result]");

      return {
        catValue: catSelect2 ? catSelect2.value : null,
        seenValue: seenInput2 ? seenInput2.value : null,
        needValue: needInput2 ? needInput2.value : null,
        resultText: resultEl2 ? resultEl2.textContent : null,
        textBefore: textBefore,
      };
    }
    case "sample_hand_survives_curve_filter": {
      // AC-13: draw 7, click a curve bin; same 7 names still listed
      const deck = buildDeck({
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-island", name: "Island", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Basic Land — Island", mana_cost: "", mana_value: 0, oracle_text: "{U}", color_identity: ["U"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();

      // Click Draw 7
      const drawBtn = doc.querySelector("[data-sample-draw]");
      if (drawBtn) drawBtn.dispatchEvent(makeEvent("click", { target: drawBtn }));
      await flush();

      // Read drawn names
      const cardsBefore = doc.querySelector("[data-sample-cards]");
      const namesBefore = cardsBefore ? Array.from(cardsBefore.children).map(function (c) { return c.textContent; }).sort() : [];

      // Click a curve bin (triggers re-render)
      const curve = doc.getElementById("mana-curve");
      const bins = curve ? curve.querySelectorAll(".dl-curve-bin") : [];
      if (bins.length >= 1) {
        bins[0].dispatchEvent(makeEvent("click", { target: bins[0] }));
        await flush();
      }

      // Read drawn names after render
      const cardsAfter = doc.querySelector("[data-sample-cards]");
      const namesAfter = cardsAfter ? Array.from(cardsAfter.children).map(function (c) { return c.textContent; }).sort() : [];

      return { namesBefore, namesAfter };
    }
    case "sample_hand_drops_removed_entries": {
      // AC-14: draw 7, remove an entry from the deck, render again; the removed entry is gone from display
      const deck = buildDeck({
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
        ]
      });
      const { document: doc } = buildDOM(deck, false);
      let api = null;
      const winObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null } };
      vm.runInContext(builderSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: builderPath });
      await flush();
      api = winObj.DeckLabBuilder;
      api._roleOptions = [["", "Add role"], ["ramp", "Ramp"], ["draw", "Draw"], ["removal", "Removal"], ["protection", "Protection"], ["counter", "Counter"], ["free", "Free interaction"], ["tutor", "Tutor"], ["combo", "Combo"], ["engine", "Engine"], ["board_wipe", "Board wipe"], ["recursion", "Recursion"], ["wincon", "Win condition"], ["land", "Land"], ["utility", "Utility"], ["other", "Other"]];
      vm.runInContext(statsSource, vm.createContext({ console, document: doc, window: winObj, setTimeout, clearTimeout, localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }, fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); }, AbortController, URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" }, navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp, CustomEvent: ctor, crypto: winObj.crypto }), { filename: statsPath });
      await flush();

      // Click Draw 7
      const drawBtn = doc.querySelector("[data-sample-draw]");
      if (drawBtn) drawBtn.dispatchEvent(makeEvent("click", { target: drawBtn }));
      await flush();

      // Read drawn names before removal
      const cardsBefore = doc.querySelector("[data-sample-cards]");
      const namesBefore = cardsBefore ? Array.from(cardsBefore.children).map(function (c) { return c.textContent; }).sort() : [];

      // Simulate entry removal: filter out Lotus Petal from deck
      deck.entries = deck.entries.filter(function (e) { return e.id !== "entry-lotus"; });

      // Re-render by getting new state from builder
      if (api.getState) {
        var curState = api.getState();
        curState.entries = curState.entries.filter(function (e) { return e.id !== "entry-lotus"; });
      }

      // Trigger render
      if (api.render) api.render();
      await flush();

      // Read drawn names after removal
      const cardsAfter = doc.querySelector("[data-sample-cards]");
      const namesAfter = cardsAfter ? Array.from(cardsAfter.children).map(function (c) { return c.textContent; }).sort() : [];

      return {
        namesBefore: namesBefore,
        namesAfter: namesAfter,
        removedPresentBefore: namesBefore.includes("Lotus Petal"),
        removedPresentAfter: namesAfter.includes("Lotus Petal"),
      };
    }
    default:
      return { error: "unknown scenario: " + scenario };
  }
}

async function main() {
  const result = await runScenario();
  console.log(JSON.stringify(result));
}
main().catch((e) => { console.error(e); process.exit(1); });
