
import fs from "node:fs";
import vm from "node:vm";

const exportPath = process.argv[2];
const source = fs.readFileSync(exportPath, "utf8");

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
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === documentElement; }
  focus() { document.activeElement = this; }
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

// Test fixture: a mixed document with commanders, duplicate names, private zones
const fixture = {
  id: "fixture-1",
  title: "Test Deck",
  revision: 0,
  zones: [
    { id: "z-main", name: "Main", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
    { id: "z-ramp", name: "Ramp", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    { id: "z-maybe", name: "Maybeboard", x: 880, y: 18, width: 400, layout_mode: "spread", sort_order: 2, layer: 0 },
    { id: "z-side", name: "Sideboard", x: 1300, y: 18, width: 400, layout_mode: "spread", sort_order: 3, layer: 0 },
    { id: "z-consider", name: "Considering", x: 1700, y: 18, width: 400, layout_mode: "spread", sort_order: 4, layer: 0 },
  ],
  entries: [
    { id: "e-cmd", name: "Kinnan, Bonder Prodigy", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-sol", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-ring2", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "z-ramp", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-isochron", name: "Isochron Scepter", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 1, type_line: "Artifact", mana_cost: "{2}", mana_value: 2, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-abrade", name: "Abrade", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 2, type_line: "Instant", mana_cost: "{1}{R}", mana_value: 2, oracle_text: "", color_identity: ["R"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-maybe1", name: "Jace, Wielder of Mysteries", is_commander: false, quantity: 1, zone_id: "z-maybe", sort_order: 0, type_line: "Legendary Planeswalker", mana_cost: "{1}{U}{U}", mana_value: 3, oracle_text: "", color_identity: ["U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-side1", name: "Leyline of Anticipation", is_commander: false, quantity: 1, zone_id: "z-side", sort_order: 0, type_line: "Enchantment", mana_cost: "{2}{U}{U}", mana_value: 4, oracle_text: "", color_identity: ["U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-cons1", name: "Questing Beast", is_commander: false, quantity: 1, zone_id: "z-consider", sort_order: 0, type_line: "Legendary Creature", mana_cost: "{2}{G}{G}", mana_value: 4, oracle_text: "", color_identity: ["G"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  validation: { commander_count: 1, library_count: 5, library_target: 99, total_count: 6, legal: false, issues: [], entry_issues: {} },
  tags: [], tag_suggestions: [],
};

// Plain-only fixture (no private zones)
const plainFixture = {
  id: "plain-1",
  title: "Plain Deck",
  revision: 0,
  zones: [
    { id: "z-main", name: "Main", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
  ],
  entries: [
    { id: "e-atraxa", name: "Atraxa, Praetors' Voice", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{W}{U}{B}", mana_value: 4, oracle_text: "", color_identity: ["G","W","U","B"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-chrome", name: "chrome mox", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 0, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-arcane", name: "arcane signet", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 1, type_line: "Artifact", mana_cost: "{2}", mana_value: 2, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-zetalpa", name: "Zetalpa, Primal Dawn", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 2, type_line: "Legendary Creature", mana_cost: "{6}{W}{W}", mana_value: 8, oracle_text: "", color_identity: ["W"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "e-baneslayer", name: "Baneslayer Angel", is_commander: false, quantity: 1, zone_id: "z-main", sort_order: 3, type_line: "Creature", mana_cost: "{3}{W}{W}", mana_value: 5, oracle_text: "", color_identity: ["W"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid" },
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  validation: { commander_count: 1, library_count: 4, library_target: 99, total_count: 5, legal: false, issues: [], entry_issues: {} },
  tags: [], tag_suggestions: [],
};

var windowObj = { setTimeout, clearTimeout };
vm.runInContext(source, vm.createContext({
  console, document, window: windowObj, setTimeout, clearTimeout,
  Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, Array, Object, String, Boolean, Error, RegExp,
}), { filename: exportPath });

// AC-6: plain server output == client copy-list lines + trailing newline
var exportApi = windowObj.DeckLabExport;
var clientPlain = (exportApi.exportLines(fixture).join("\n") + "\n");

// AC-8: client Archidekt output == server archidekt output
// The fixture has commanders named "Kinnan, Bonder Prodigy"
var commanderNames = {};
fixture.entries.forEach(function(e) { if (e.is_commander && e.name) commanderNames[e.name] = 1; });
var clientArchidekt = exportApi.exportArchidektText(fixture, Object.keys(commanderNames)) + "\n";

// Also test the plain fixture
var clientPlainSimple = (exportApi.exportLines(plainFixture).join("\n") + "\n");
var cmdNamesPlain = {};
plainFixture.entries.forEach(function(e) { if (e.is_commander && e.name) cmdNamesPlain[e.name] = 1; });
var clientArchidektSimple = exportApi.exportArchidektText(plainFixture, Object.keys(cmdNamesPlain)) + "\n";

console.log(JSON.stringify({
  clientPlain: clientPlain,
  clientArchidekt: clientArchidekt,
  clientPlainSimple: clientPlainSimple,
  clientArchidektSimple: clientArchidektSimple,
}));
