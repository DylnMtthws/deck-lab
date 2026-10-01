
import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const scenario = process.argv[3] || "role_groups";
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
    this.ownerDocument = null;
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
  closest(selector) { let node = this; while (node && node.tagName) { if (matchSelector(node, selector)) return node; node = node.parentNode; } return null; }
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

function matchSelector(el, selector) {
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
  walk(root, []).forEach((node) => { if (node !== root && matchSelector(node, selector)) nodes.push(node); });
  nodes.forEach = Array.prototype.forEach;
  nodes.map = Array.prototype.map;
  nodes.filter = Array.prototype.filter;
  nodes.find = Array.prototype.find;
  return nodes;
}

function makeDeck(overrides = {}) {
  return Object.assign({
    id: "deck-role", title: "Role Test Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-considering", name: "Considering", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-sol", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-arbor", name: "Birds of Paradise", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Creature", mana_cost: "{G}", mana_value: 1, oracle_text: "", color_identity: ["G"], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-river", name: "Rhystic Study", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Enchantment", mana_cost: "{2}{U}", mana_value: 3, oracle_text: "", color_identity: ["U"], image_uri: "", role: "draw", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-land", name: "Island", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 3, type_line: "Basic Land", mana_cost: "", mana_value: 0, oracle_text: "", color_identity: ["U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-hulk", name: "Hullbreaker Horror", is_commander: false, quantity: 1, zone_id: "zone-considering", sort_order: 0, type_line: "Creature", mana_cost: "{6}{U}{U}", mana_value: 8, oracle_text: "", color_identity: ["U"], image_uri: "", role: "combo", format_legal: true, commander_legal: true, validation_issues: [] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "role", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 4, library_target: 99, total_count: 5, legal: false, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  }, overrides);
}

async function runScenario() {
  const deckFixture = (function() {
    if (scenario === "sort_mana_value") {
      return makeDeck({ preferences: { view_mode: "table", display_mode: "text", group_mode: "role", sort_mode: "mana_value", density: "compact", collapsed_json: "[]" } });
    }
    if (scenario === "zone_group_snapshot") {
      return makeDeck({ preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" } });
    }
    if (scenario === "type_group_snapshot") {
      return makeDeck({ preferences: { view_mode: "table", display_mode: "text", group_mode: "type", sort_mode: "manual", density: "compact", collapsed_json: "[]" } });
    }
    if (scenario === "unknown_roles") {
      return makeDeck({
        preferences: { view_mode: "table", display_mode: "text", group_mode: "role", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-carddraw", name: "Mystic Remora", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Enchantment", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "card_draw", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-finisher", name: "Craterhoof Behemoth", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Creature", mana_cost: "{5}{G}{G}", mana_value: 7, oracle_text: "", color_identity: ["G"], image_uri: "", role: "finisher", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-saproling", name: "Saproling Burst", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Enchantment", mana_cost: "{4}{G}", mana_value: 5, oracle_text: "", color_identity: ["G"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
        ],
      });
    }
    if (scenario === "all_entries_visible") {
      return makeDeck({
        preferences: { view_mode: "table", display_mode: "text", group_mode: "role", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ramp", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-draw", name: "Rhystic Study", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Enchantment", mana_cost: "{2}{U}", mana_value: 3, oracle_text: "", color_identity: ["U"], image_uri: "", role: "draw", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-carddraw", name: "Mystic Remora", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Enchantment", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "card_draw", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-finisher", name: "Craterhoof Behemoth", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 3, type_line: "Creature", mana_cost: "{5}{G}{G}", mana_value: 7, oracle_text: "", color_identity: ["G"], image_uri: "", role: "finisher", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-norole", name: "Island", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 4, type_line: "Basic Land", mana_cost: "", mana_value: 0, oracle_text: "", color_identity: ["U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-private", name: "Hullbreaker Horror", is_commander: false, quantity: 1, zone_id: "zone-considering", sort_order: 0, type_line: "Creature", mana_cost: "{6}{U}{U}", mana_value: 8, oracle_text: "", color_identity: ["U"], image_uri: "", role: "combo", format_legal: true, commander_legal: true, validation_issues: [] },
        ],
      });
    }
    if (scenario === "other_and_unknown") {
      return makeDeck({
        preferences: { view_mode: "table", display_mode: "text", group_mode: "role", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-other-real", name: "Misc Card", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{3}", mana_value: 3, oracle_text: "", color_identity: [], image_uri: "", role: "other", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-carddraw", name: "Mystic Remora", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Enchantment", mana_cost: "{U}", mana_value: 1, oracle_text: "", color_identity: ["U"], image_uri: "", role: "card_draw", format_legal: true, commander_legal: true, validation_issues: [] },
        ],
      });
    }
    return makeDeck();
  })();

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
    querySelector(sel) {
      if (sel === "body") return body;
      if (sel === ".dl-builder") return walk(documentElement, []).find((el) => el.className && el.className.split(/\s+/).includes("dl-builder")) || null;
      return queryAll(documentElement, sel)[0] || null;
    },
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

  body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deckFixture) }));
  body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
  head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
  body.appendChild(el("button", { id: "save-state", text: "Saved" }));
  const toolbar = el("div", { className: "dl-builder-toolbar" });
  toolbar.appendChild(el("span", { className: "dl-deck-count", "data-deck-count": "", text: "0/100" }));
  const label = el("label", { className: "desktop-only dl-toolbar-select", "data-table-only": "" });
  const groupSelect = el("select", { className: "dl-zone-select", "data-group": "", "aria-label": "Group cards" });
  groupSelect.appendChild(el("option", { value: "zone", text: "Group: Zone" }));
  groupSelect.appendChild(el("option", { value: "type", text: "Group: Type" }));
  groupSelect.appendChild(el("option", { value: "role", text: "Group: Role" }));
  label.appendChild(groupSelect);
  toolbar.appendChild(label);
  body.appendChild(toolbar);
  body.appendChild(el("div", { id: "table-view" }));
  body.appendChild(el("div", { id: "playmat-view" }));
  const stage = el("div", { id: "playmat-stage" });
  stage.appendChild(el("div", { id: "playmat" }));
  body.appendChild(stage);
  body.appendChild(el("span", { id: "zoom-label" }));
  ["mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el("div", { id })));
  body.appendChild(el("div", { "data-playmat-selection": "" }));
  body.appendChild(el("div", { "data-deck-tag-list": "" }));
  body.appendChild(el("div", { "data-tag-summary": "" }));
  body.appendChild(el("div", { "data-tag-options": "" }));

  const store = {};
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => ({}) }); },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-role", origin: "http://deck.lab", pathname: "/build/deck/deck-role", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));

  // Use walk-based query for sections since the fake DOM doesn't support descendant selectors
  function getGroupInfo() {
    const tv = document.getElementById("table-view");
    if (!tv) return [];
    const sections = [];
    walk(tv, []).forEach(function(node) {
      if (node !== tv && node.className && node.className.split(/\s+/).includes("dl-zone-section")) {
        sections.push(node);
      }
    });
    return sections.map(function(sec) {
      var h2 = null;
      walk(sec, []).forEach(function(n) { if (n.tagName === "H2" && !h2) h2 = n; });
      var countEl = null;
      walk(sec, []).forEach(function(n) { if (n.className && n.className.split(/\s+/).includes("dl-zone-count") && !countEl) countEl = n; });
      var entryEls = [];
      walk(sec, []).forEach(function(n) { if (n.getAttribute && n.getAttribute("data-entry-id")) entryEls.push(n); });
      return {
        id: sec.id.replace(/^zone-/, ""),
        name: h2 ? h2.textContent : "",
        count: countEl ? parseInt(countEl.textContent || "0", 10) : 0,
        entryIds: entryEls.map(function(n) { return n.getAttribute("data-entry-id"); }),
      };
    });
  }

  if (scenario === "select_value") {
    const selectEl = document.querySelector("[data-group]");
    console.log(JSON.stringify({ selectValue: selectEl ? selectEl.value : null }));
  } else if (scenario === "sort_mana_value") {
    const groups = getGroupInfo();
    const ramp = groups.find((g) => g.id === "role-ramp");
    console.log(JSON.stringify({ rampEntryIds: ramp ? ramp.entryIds : [] }));
  } else if (scenario === "zone_group_snapshot" || scenario === "type_group_snapshot") {
    const groups = getGroupInfo();
    console.log(JSON.stringify(groups.map((g) => ({ id: g.id, name: g.name }))));
  } else if (scenario === "all_entries_visible") {
    const groups = getGroupInfo();
    const allEntryIds = groups.flatMap((g) => g.entryIds);
    console.log(JSON.stringify({ groups: groups.map((g) => ({ id: g.id, name: g.name, entryIds: g.entryIds })), allEntryIds }));
  } else {
    const groups = getGroupInfo();
    console.log(JSON.stringify(groups.map((g) => ({ id: g.id, name: g.name, count: g.count, entryIds: g.entryIds }))));
  }
}

await runScenario();
