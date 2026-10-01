import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const scenario = process.argv[3];
const source = fs.readFileSync(builderPath, "utf8");

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
  getPropertyValue(name) { return this._props[name] || ""; }
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
    this.open = false;
    this.tabIndex = 0;
    this.src = "";
    this.alt = "";
    this.currentSrc = "";
    this.href = "";
    this.rel = "";
    this.offsetWidth = 240;
    this.offsetHeight = 200;
    this.returnValue = "";
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
      },
    });
  }
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(value) { this.children.length = 0; this._text = value == null ? "" : String(value); }
  get innerHTML() { return this._text; }
  set innerHTML(value) { this._text = String(value); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) {
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    return this.attributes[name] ?? null;
  }
  removeAttribute(name) { delete this.attributes[name]; }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  append(...nodes) { nodes.forEach((n) => this.appendChild(typeof n === "string" ? Object.assign(this.ownerDocument.createElement("#text"), { textContent: n }) : n)); }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) { this.children.splice(i, 1); child.parentNode = null; } return child; }
  contains(other) { return other === this || this.children.some((c) => c.contains(other)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((h) => h !== fn); }
  dispatchEvent(event) { event.target = event.target || this; event.currentTarget = this; (this.listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event)); return !event.defaultPrevented; }
  setPointerCapture() {}
  showModal() { this.open = true; }
  getBoundingClientRect() {
    if (this.id === "playmat" || this.classList.contains("dl-playmat")) {
      return { left: 0, top: 0, width: 1600, height: 900, right: 1600, bottom: 900 };
    }
    var left = parseFloat(this.style.left) || 0;
    var top = parseFloat(this.style.top) || 0;
    return { left: left, top: top, width: this.offsetWidth, height: this.offsetHeight, right: left + this.offsetWidth, bottom: top + this.offsetHeight };
  }
}

function tokenize(selector) { return selector.split(",").map((part) => part.trim()).filter(Boolean); }
function simpleMatch(el, sel) {
  const chunks = sel.trim().match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|[\w-]+/g) || [];
  let tag = null; const ids = []; const classes = []; const attrs = [];
  for (const chunk of chunks) {
    if (chunk.startsWith("#")) ids.push(chunk.slice(1));
    else if (chunk.startsWith(".")) classes.push(chunk.slice(1));
    else if (chunk.startsWith("[")) {
      const body = chunk.slice(1, -1); const eq = body.indexOf("=");
      if (eq < 0) attrs.push({ name: body, value: null });
      else attrs.push({ name: body.slice(0, eq).trim(), value: body.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "") });
    } else tag = chunk.toUpperCase();
  }
  if (tag && el.tagName !== tag) return false;
  if (ids.some((id) => el.id !== id)) return false;
  if (classes.some((c) => !el.classList.contains(c))) return false;
  for (const attr of attrs) {
    const actual = attr.name === "class" ? el.className : attr.name === "id" ? el.id : el.attributes[attr.name];
    if (attr.value == null) { if (actual == null) return false; }
    else if (String(actual) !== attr.value) return false;
  }
  return true;
}
function matches(el, selector) { return tokenize(selector).some((part) => simpleMatch(el, part.trim().split(/\s+/).pop())); }
function walk(el, acc) { acc.push(el); el.children.forEach((c) => walk(c, acc)); return acc; }
function queryAll(root, selector) {
  const all = []; root.children.forEach((c) => walk(c, all));
  const parts = tokenize(selector);
  return all.filter((el) => parts.some((part) => simpleMatch(el, part.trim().split(/\s+/).pop())));
}

function makeEvent(type, props) {
  return { type: type, ...(props || {}), defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
}

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head); documentElement.appendChild(body);
var document = {
  documentElement: documentElement, body: body, head: head,
  createElement(tag) { var el = new Element(tag, document); el.ownerDocument = document; return el; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  getElementById(id) { return walk(documentElement, []).find(function (el) { return el.id === id; }) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};

function el(tag, attrs) {
  var node = document.createElement(tag);
  if (attrs) Object.keys(attrs).forEach(function (key) {
    if (key === "className") node.className = attrs[key];
    else if (key === "id") { node.id = attrs[key]; node.setAttribute("id", attrs[key]); }
    else if (key === "text") node.textContent = attrs[key];
    else node.setAttribute(key, attrs[key]);
  });
  return node;
}

var decks = {
  "zone-overlap": {
    zones: [{ id: "zone-main", name: "Unsorted", x: 80, y: 120, layout_mode: "spread", sort_order: 0 }],
    entries: [
      { id: "cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "https://img.test/kinnan.jpg", role: "" },
      { id: "ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://img.test/solring.jpg", role: "" },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid" },
    preferences: { view_mode: "playmat" },
  },
  "zone-no-overlap": {
    zones: [{ id: "zone-main", name: "Unsorted", x: 600, y: 400, layout_mode: "spread", sort_order: 0 }],
    entries: [
      { id: "cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "https://img.test/kinnan.jpg", role: "" },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid" },
    preferences: { view_mode: "playmat" },
  },
  "free-zone": {
    zones: [{ id: "zone-other", name: "Other", x: 600, y: 400, layout_mode: "spread", sort_order: 0 }],
    entries: [
      { id: "cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "https://img.test/kinnan.jpg", role: "" },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid" },
    preferences: { view_mode: "playmat" },
  },
};

var chosen = decks[scenario];
if (!chosen) { console.error("unknown scenario " + scenario); process.exit(1); }

// Build DOM
body.appendChild(el("script", { id: "deck-document-data", text: JSON.stringify(chosen) }));
var builder = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" });
body.appendChild(builder);
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
body.appendChild(el("div", { id: "table-view" }));
var stage = el("div", { id: "playmat-stage" });
stage.appendChild(el("div", { id: "playmat" }));
var playmatView = el("div", { id: "playmat-view" });
playmatView.appendChild(stage);
body.appendChild(playmatView);
["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach(function (id) {
  body.appendChild(el(id === "zoom-label" ? "span" : "div", { id: id }));
});
body.appendChild(el("div", { "data-playmat-selection": "" }));
body.appendChild(el("input", { "data-card-search": "" }));
body.appendChild(el("div", { "data-card-results": "" }));
body.appendChild(el("span", { "data-search-scope": "" }));
body.appendChild(el("span", { "data-card-total": "" }));
body.appendChild(el("select", { "data-add-zone": "" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));
body.appendChild(el("dialog", { "data-card-preview": "" }));
// zone dialog for freeZonePosition test
var zoneDialog = el("dialog", { "data-zone-dialog": "" });
zoneDialog.appendChild(el("input", { "data-zone-name": "" }));
zoneDialog.appendChild(el("span", { "data-zone-error": "" }));
body.appendChild(zoneDialog);

var store = {};
var fetches = [];
var windowObj = {
  matchMedia: function () { return { matches: false, addEventListener: function () {} }; },
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  crypto: { randomUUID: function () { return "uuid-" + (++revision); } },
  DeckLabSelects: { refresh: function () {} },
  requestAnimationFrame: function (cb) { return setTimeout(cb, 0); },
};
async function flush() { for (var i = 0; i < 8; i++) await new Promise(function (r) { setTimeout(r, 0); }); }

var source_state = { ...chosen };
var revision = 0;

async function boot() {
  vm.runInContext(source, vm.createContext({
    console: console,
    document: document,
    window: windowObj,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    localStorage: {
      getItem: function (k) { return store[k] || null; },
      setItem: function (k, v) { store[k] = String(v); },
      removeItem: function (k) { delete store[k]; },
    },
    fetch: function (url, opts) {
      fetches.push({ url: String(url), opts: opts });
      return Promise.resolve({ ok: true, status: 200, json: async function () { return { revision: revision }; } });
    },
    AbortController: AbortController,
    URL: URL,
    URLSearchParams: URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1" },
    navigator: {},
    Set: Set, Map: Map, Promise: Promise, JSON: JSON, Math: Math, Number: Number, Date: Date,
    encodeURIComponent: encodeURIComponent, parseFloat: parseFloat, parseInt: parseInt,
    Array: Array, Object: Object, String: String, Boolean: Boolean, Error: Error,
    CustomEvent: class CustomEvent2 { constructor(type, init) { this.type = type; this.detail = (init || {}).detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  await flush();
}

// Run scenario
if (scenario === "zone-overlap" || scenario === "zone-no-overlap") {
  await boot();
  var result = { zones: [] };
  document.querySelectorAll(".dl-mat-zone").forEach(function (zone) {
    result.zones.push({
      id: zone.dataset.zoneId,
      left: zone.style.left,
      top: zone.style.top,
      isCommander: zone.classList.contains("dl-mat-command"),
    });
  });
  console.log(JSON.stringify(result));

} else if (scenario === "free-zone") {
  await boot();
  var api = windowObj.DeckLabBuilder;
  var freePos = null;
  if (api && typeof api.freeZonePosition === "function") {
    freePos = api.freeZonePosition(360, 240);
  }
  var cmdBox = null;
  document.querySelectorAll(".dl-mat-zone").forEach(function (zone) {
    if (zone.classList.contains("dl-mat-command")) {
      cmdBox = {
        left: parseFloat(zone.style.left) || 0,
        top: parseFloat(zone.style.top) || 0,
        width: parseFloat(zone.style.width) || 0,
      };
    }
  });
  var result = { freeZonePosition: freePos, commandBox: cmdBox };
  console.log(JSON.stringify(result));
}
