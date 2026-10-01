
import fs from "node:fs";
import vm from "node:vm";

const statusPath = process.argv[2];
const scenario = process.argv[3] || "legal";
const source = fs.readFileSync(statusPath, "utf8");

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
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    if (child) child.parentNode = null;
    return child;
  }
  remove() {
    if (this.parentNode && this.parentNode.removeChild) this.parentNode.removeChild(this);
  }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === documentElement; }
  focus() { document.activeElement = this; }
  get offsetWidth() {
    if (this.hasAttribute("data-status-issues")) return 280;
    return 0;
  }
  getBoundingClientRect() {
    if (this.hasAttribute("data-status-issues")) {
      var placed = parseFloat(this.style.left);
      var left = isFinite(placed) ? placed : 0;
      return { left: left, top: 800, right: left + 280, bottom: 856, width: 280, height: 56, x: left, y: 800 };
    }
    if (anchorRects && (this.classList.contains("dl-status-issues-toggle") || (this.tagName === "BUTTON" && this.closest("[data-status-legality]")))) {
      var toggleLeft = anchorRects.toggleLeft;
      return { left: toggleLeft, top: 868, right: toggleLeft + 72, bottom: 892, width: 72, height: 24, x: toggleLeft, y: 868 };
    }
    if (anchorRects && this.hasAttribute("data-status-bar")) {
      return { left: 0, top: anchorRects.barTop, right: anchorRects.vw, bottom: anchorRects.vh, width: anchorRects.vw, height: anchorRects.vh - anchorRects.barTop, x: 0, y: anchorRects.barTop };
    }
    return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
  }
  blur() { if (document.activeElement === this) document.activeElement = null; }
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
  return nodes;
}

let anchorRects = null;
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

// --- Shared VM setup ---
const store = {};
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

function makeContext(extra) {
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  return Object.assign(vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch() { return Promise.resolve({ ok: true, json: async () => ({}) }); },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), extra || {});
}

// ============ UI rendering scenarios ============
var result = {};

var fixtures = {
  legal: {
    id: "deck-legal", title: "Legal Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-side", name: "Sideboard", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-bird", name: "Birds of Paradise", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Creature", mana_cost: "{G}", mana_value: 1, oracle_text: "", color_identity: ["G"], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-mystic", name: "Mystic Remora", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Enchantment", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "draw", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-swan", name: "Swan Song", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 3, type_line: "Instant", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "counter", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-force", name: "Force of Will", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 4, type_line: "Instant", mana_cost: "{3}{U}{U}", mana_value: 5, oracle_text: "", color_identity: ["U"], image_uri: "", role: "free", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-side", name: "Side card", is_commander: false, quantity: 2, zone_id: "zone-side", sort_order: 0, type_line: "Instant", mana_cost: "{R}", mana_value: 1, oracle_text: "", color_identity: ["R"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 5, library_target: 99, total_count: 6, legal: false, issues: ["The library has 5 of 99 cards."], entry_issues: {} },
    tags: [], tag_suggestions: [],
  },
  withConsidering: {
    id: "deck-consider", title: "Considering Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-consider", name: "Considering", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-bird", name: "Birds of Paradise", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Creature", mana_cost: "{G}", mana_value: 1, oracle_text: "", color_identity: ["G"], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-consider", name: "Mana Crypt", is_commander: false, quantity: 1, zone_id: "zone-consider", sort_order: 0, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 2, library_target: 99, total_count: 3, legal: true, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  },
  withVerdict: {
    id: "deck-verdict", title: "Verdict Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 1, library_target: 99, total_count: 2, legal: true, issues: [], entry_issues: {} },
    feedback: { verdict: "mixed" },
    tags: [], tag_suggestions: [],
  },
  fullHouse: {
    id: "deck-full", title: "Full Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-land", name: "Forest", is_commander: false, quantity: 32, zone_id: "zone-main", sort_order: 0, type_line: "Basic Land", mana_cost: "", mana_value: 0, oracle_text: "", color_identity: ["G"], image_uri: "", role: "land", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-bird", name: "Birds of Paradise", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Creature", mana_cost: "{G}", mana_value: 1, oracle_text: "", color_identity: ["G"], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-mystic", name: "Mystic Remora", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 3, type_line: "Enchantment", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "draw", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-swan", name: "Swan Song", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 4, type_line: "Instant", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "counter", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-force", name: "Force of Will", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 5, type_line: "Instant", mana_cost: "{3}{U}{U}", mana_value: 5, oracle_text: "", color_identity: ["U"], image_uri: "", role: "free", format_legal: true, commander_legal: true, validation_issues: [] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 37, library_target: 99, total_count: 38, legal: true, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  },
};

// ============ Main rendering scenario ============
if (scenario === "issuesSurviveRerender") {
  // Special scenario: use the illegal/legal fixture, open popover, then re-render
  var deck = fixtures.legal;
  body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "false" }));
  head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
  body.appendChild(el("button", { id: "save-state", text: "Saved" }));
  body.appendChild(el("div", { className: "dl-builder-toolbar" }));
  var bar = el("footer", { className: "dl-status-bar", "data-status-bar": "", "aria-label": "Deck status" });
  body.appendChild(bar);
  body.appendChild(el("div", { id: "table-view" }));
  body.appendChild(el("div", { id: "playmat-view" }));
  body.appendChild(el("div", { "data-playmat-selection": "" }));
  body.appendChild(el("div", { "data-deck-tag-list": "" }));
  body.appendChild(el("div", { "data-tag-summary": "" }));
  body.appendChild(el("button", { "data-rail-tab": "tools", type: "button", text: "Tools" }));

  await (async function boot() {
    const ctx = makeContext();
    var builderState = JSON.parse(JSON.stringify(deck));
    var renderListeners = [];
    ctx.window.DeckLabBuilder = {
      version: 1,
      get shared() { return false; },
      getState: function () { return builderState; },
      onRender: function (fn) {
        renderListeners.push(fn);
        return function () { var idx = renderListeners.indexOf(fn); if (idx >= 0) renderListeners.splice(idx, 1); };
      },
      focusEntry: function () { return true; },
      render: function () {
        renderListeners.forEach(function (fn) { try { fn(builderState); } catch (e) { console.error(e); } });
      },
    };

    vm.runInContext(source, ctx, { filename: statusPath });
    await flush();
    document.dispatchEvent({ type: "deck-lab:ready", detail: { api: ctx.window.DeckLabBuilder } });
    await flush();

    // Open the popover by clicking the issues button
    var legalEl = document.querySelector("[data-status-legality]");
    if (!legalEl) {
      result.popoverSurvived = false;
    } else {
      var toggleBtn = legalEl.querySelector("button");
      if (!toggleBtn) {
        result.popoverSurvived = false;
      } else {
        // Click to open popover
        toggleBtn.dispatchEvent(makeEvent("click"));
        await flush();
        var popoverAfterOpen = document.querySelector("[data-status-issues]");
        var openAfterClick = !!popoverAfterOpen;

        // Trigger re-render
        ctx.window.DeckLabBuilder.render();
        await flush();
        var popoverAfterRerender = document.querySelector("[data-status-issues]");
        result.popoverSurvived = openAfterClick && !!popoverAfterRerender;
      }
    }
  })();
  console.log(JSON.stringify(result));
  process.exit(0);
}

if (scenario === "popoverAnchor") {
  var anchorDeck = fixtures.legal;
  body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "false" }));
  head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
  body.appendChild(el("button", { id: "save-state", text: "Saved" }));
  body.appendChild(el("div", { className: "dl-builder-toolbar" }));
  var anchorBar = el("footer", { className: "dl-status-bar", "data-status-bar": "", "aria-label": "Deck status" });
  body.appendChild(anchorBar);

  function insideStatusBar(node) {
    var current = node;
    while (current) {
      if (current.hasAttribute && current.hasAttribute("data-status-bar")) return true;
      current = current.parentNode;
    }
    return false;
  }

  await (async function boot() {
    const ctx = makeContext();
    ctx.window.innerWidth = 1440;
    ctx.window.innerHeight = 900;
    var builderState = JSON.parse(JSON.stringify(anchorDeck));
    var renderListeners = [];
    ctx.window.DeckLabBuilder = {
      version: 1,
      get shared() { return false; },
      getState: function () { return builderState; },
      onRender: function (fn) { renderListeners.push(fn); },
      focusEntry: function () { return true; },
      render: function () {
        renderListeners.forEach(function (fn) { try { fn(builderState); } catch (e) { console.error(e); } });
      },
    };
    anchorRects = { toggleLeft: 240, barTop: 864, vw: 1440, vh: 900 };
    vm.runInContext(source, ctx, { filename: statusPath });
    await flush();
    var legalEl = document.querySelector("[data-status-legality]");
    var toggleBtn = legalEl ? legalEl.querySelector("button") : null;
    if (!toggleBtn) {
      result.error = "no toggle";
      console.log(JSON.stringify(result));
      return;
    }
    toggleBtn.dispatchEvent(makeEvent("click"));
    await flush();
    var popover = document.querySelector("[data-status-issues]");
    result.insideStatusBar = popover ? insideStatusBar(popover) : true;
    result.position = popover ? popover.style.position : "";
    result.left = popover ? parseFloat(popover.style.left) : NaN;
    result.toggleLeft = anchorRects.toggleLeft;
    toggleBtn.dispatchEvent(makeEvent("click"));
    await flush();
    anchorRects = { toggleLeft: 1300, barTop: 864, vw: 1440, vh: 900 };
    toggleBtn.dispatchEvent(makeEvent("click"));
    await flush();
    var clamped = document.querySelector("[data-status-issues]");
    var gap = 8;
    var width = 280;
    var maxLeft = 1440 - gap - width;
    result.clampedLeft = clamped ? parseFloat(clamped.style.left) : NaN;
    result.clampedExpected = Math.min(Math.max(anchorRects.toggleLeft, gap), maxLeft);
    result.clampedPosition = clamped ? clamped.style.position : "";
    result.clampedInsideStatusBar = clamped ? insideStatusBar(clamped) : true;
  })();
  console.log(JSON.stringify(result));
  process.exit(0);
}

var deck = fixtures[scenario] || fixtures.legal;

body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "false" }));
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
body.appendChild(el("div", { className: "dl-builder-toolbar" }));
var bar = el("footer", { className: "dl-status-bar", "data-status-bar": "", "aria-label": "Deck status" });
body.appendChild(bar);
body.appendChild(el("div", { id: "table-view" }));
body.appendChild(el("div", { id: "playmat-view" }));
body.appendChild(el("div", { "data-playmat-selection": "" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("button", { "data-rail-tab": "tools", type: "button", text: "Tools" }));

await (async function boot() {
  const ctx = makeContext();
  var builderState = JSON.parse(JSON.stringify(deck));
  var renderListeners = [];
  var toolsTabClicked = false;
  ctx.window.DeckLabBuilder = {
    version: 1,
    get shared() { return false; },
    getState: function () { return builderState; },
    onRender: function (fn) {
      renderListeners.push(fn);
      return function () { var idx = renderListeners.indexOf(fn); if (idx >= 0) renderListeners.splice(idx, 1); };
    },
    focusEntry: function (entryId) {
      this._lastFocusedEntryId = entryId;
      console.error("FOCUS_ENTRY_CALLED:" + entryId);
      return true;
    },
    render: function () {
      renderListeners.forEach(function (fn) { try { fn(builderState); } catch (e) { console.error(e); } });
    },
  };

  vm.runInContext(source, ctx, { filename: statusPath });
  await flush();

  // Simulate deck-lab:ready
  document.dispatchEvent({ type: "deck-lab:ready", detail: { api: ctx.window.DeckLabBuilder } });
  await flush();

  function statusSnapshot() {
    var barEl = document.querySelector("[data-status-bar]");
    if (!barEl) return { missing: true };
    var countEl = barEl.querySelector(".dl-status-count");
    var progressWrap = barEl.querySelector(".dl-status-progress");
    var progressFill = progressWrap ? progressWrap.querySelector(".dl-status-progress-fill") : null;
    var legalityEl = barEl.querySelector("[data-status-legality]");
    var typeEls = barEl.querySelectorAll("[data-status-type]");
    var dividerEl = barEl.querySelector(".dl-status-divider");
    var consideringEl = barEl.querySelector("[data-status-considering]");
    var verdictEl = barEl.querySelector("[data-status-verdict]");
    var issuesEl = barEl.querySelector("[data-status-issues]");
    return {
      countText: countEl ? countEl.textContent : "",
      countInvalid: countEl ? countEl.classList.contains("is-invalid") : false,
      hasProgress: !!progressWrap,
      progressFillWidth: progressFill ? progressFill.style.width : "",
      progressFillDisplay: progressFill ? progressFill.style.display : "",
      progressFillClass: progressFill ? progressFill.className : "",
      legalText: legalityEl ? legalityEl.textContent : "",
      legalClass: legalityEl ? legalityEl.className : "",
      types: Array.from(typeEls).map(function (t) {
        var labelEl = t.querySelector(".dl-status-type-label");
        var countEl = t.querySelector(".dl-status-type-count");
        return {
          name: t.getAttribute("data-status-type"),
          html: t.innerHTML,
          labelText: labelEl ? labelEl.textContent : "",
          countText: countEl ? countEl.textContent : "",
        };
      }),
      hasDivider: !!dividerEl,
      consideringText: consideringEl ? consideringEl.textContent : null,
      verdictText: verdictEl ? verdictEl.textContent : null,
      hasIssuesList: !!issuesEl,
      hasPopoverClass: issuesEl ? issuesEl.classList.contains("dl-popover") : false,
    };
  }

  result.snapshot = statusSnapshot();

  // Test progress track colors
  if (scenario === "fullHouse") {
    result.progressColorCheck = vm.runInContext(
      "(function() {" +
      "  var fill = document.querySelector('.dl-status-progress-fill');" +
      "  if (!fill) return {error: 'no fill'};" +
      "  return {" +
      "    width: fill.style.width," +
      "    isSuccess: fill.classList.contains('is-success')," +
      "    isWarning: fill.classList.contains('is-warning')," +
      "    isBrand: fill.classList.contains('is-brand')" +
      "  };" +
      "})()",
      ctx
    );
  }

  // Test issues popover (AC-2)
  if (scenario === "legal" || scenario === "illegal") {
    // For illegal, check the popover
  }

  // Test issues popover open/close behavior (AC-3)
  if (scenario === "issuesPopover") {
    // AC-3 scenario — separate test in harness
  }

  // Test considering and verdict (AC-4)
  if (scenario === "withConsidering" || scenario === "withVerdict") {
    // captured in snapshot
  }

  console.log(JSON.stringify(result));
})();
