
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

// ============ Type bucket test scenarios ============
if (scenario === "typebucket") {
  const ctx = makeContext();
  vm.runInContext(source, ctx, { filename: statusPath });
  const status = ctx.window.DeckLabStatus;
  const cases = [
    ["Artifact Creature \u2014 Golem", "Creature"],
    ["Land Creature \u2014 Forest Dryad", "Land"],
    ["Legendary Enchantment Artifact", "Artifact"],
    ["Kindred Instant \u2014 Elf", "Instant"],
    ["Sorcery", "Sorcery"],
    ["Enchantment Creature \u2014 Pegasus", "Creature"],
    ["Planeswalker \u2014 Jace", "Planeswalker"],
    ["Battle \u2014 Siege", "Battle"],
    ["Legendary Creature \u2014 Human Wizard", "Creature"],
    ["Tribal Instant \u2014 Goblin", "Instant"],
    ["Land", "Land"],
    ["Artifact", "Artifact"],
    ["Kindred Enchantment", "Enchantment"],
    ["Phenomenon", "Other"],
    ["Scheme", "Other"],
    ["", "Other"],
  ];
  const results = cases.map(function (c) {
    return { typeLine: c[0], expected: c[1], actual: status.typeBucket(c[0]) };
  });
  console.log(JSON.stringify({ typeBucketResults: results }));
  process.exit(0);
}

// ============ Summarize test scenario ============
if (scenario === "summarize") {
  const ctx = makeContext();
  vm.runInContext(source, ctx, { filename: statusPath });
  const status = ctx.window.DeckLabStatus;

  // Test AC-2: exclude commanders and private zones
  var state2 = {
    id: "test",
    zones: [
      { id: "z1", name: "Unsorted" },
      { id: "z2", name: "Sideboard" },
      { id: "z3", name: "Maybeboard" }
    ],
    entries: [
      { id: "cmd", name: "Cmd", is_commander: true, quantity: 1, zone_id: "z1", type_line: "Legendary Creature", role: "" },
      { id: "e1", name: "Forest", is_commander: false, quantity: 3, zone_id: "z1", type_line: "Basic Land", role: "land" },
      { id: "e2", name: "Island", is_commander: false, quantity: 2, zone_id: "z1", type_line: "Basic Land", role: "land" },
      { id: "e3", name: "Bolt", is_commander: false, quantity: 4, zone_id: "z1", type_line: "Instant", role: "removal" },
      { id: "e4", name: "Side card", is_commander: false, quantity: 5, zone_id: "z2", type_line: "Instant", role: "" },
      { id: "e5", name: "Maybe card", is_commander: false, quantity: 1, zone_id: "z3", type_line: "Creature", role: "" }
    ],
    validation: { total_count: 10, legal: false, issues: ["test"], entry_issues: { "e1": [{ code: "test" }] } }
  };
  var s2 = status.summarize(state2);

  // Test AC-3: roles sorted with none bucket
  var state3 = {
    id: "test2",
    zones: [{ id: "z1", name: "Unsorted" }],
    entries: [
      { id: "e1", name: "A", is_commander: false, quantity: 2, zone_id: "z1", type_line: "Creature", role: "ramp" },
      { id: "e2", name: "B", is_commander: false, quantity: 1, zone_id: "z1", type_line: "Creature", role: "" },
      { id: "e3", name: "C", is_commander: false, quantity: 3, zone_id: "z1", type_line: "Creature", role: "draw" },
      { id: "e4", name: "D", is_commander: false, quantity: 1, zone_id: "z1", type_line: "Creature", role: "ramp" }
    ],
    validation: { total_count: 7, legal: false, issues: [], entry_issues: {} }
  };
  var s3 = status.summarize(state3);

  console.log(JSON.stringify({ summarize2: s2, summarize3: s3 }));
  process.exit(0);
}

// ============ UI rendering test scenarios ============

var result = {};

// --- Fixtures ---
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
  illegal: {
    id: "deck-illegal", title: "Illegal Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 2, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [{ code: "copies", message: "Commander singleton rule is exceeded." }] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 2, library_target: 99, total_count: 3, legal: false, issues: ["The library has 2 of 99 cards.", "Commander singleton rule is exceeded."], entry_issues: { "entry-ring": [{ code: "copies", message: "Commander singleton rule is exceeded." }] } },
    tags: [], tag_suggestions: [],
  },
};

// ============ UI rendering scenarios (legal, illegal) ============
if (scenario !== "late-load") {

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
    var legalityEl = barEl.querySelector("[data-status-legality]");
    var typeEls = barEl.querySelectorAll("[data-status-type]");
    var roleEls = barEl.querySelectorAll("[data-status-role]");
    var issuesEl = barEl.querySelector("[data-status-issues]");
    return {
      countText: countEl ? countEl.textContent : "",
      countInvalid: countEl ? countEl.classList.contains("is-invalid") : false,
      legalText: legalityEl ? legalityEl.textContent : "",
      legalClass: legalityEl ? legalityEl.className : "",
      types: Array.from(typeEls).map(function (t) { return { name: t.getAttribute("data-status-type"), text: t.textContent }; }),
      roles: Array.from(roleEls).map(function (r) { return { name: r.getAttribute("data-status-role"), text: r.textContent }; }),
      hasIssuesList: !!issuesEl,
    };
  }

  result.snapshot = statusSnapshot();

  // For illegal scenario, test clicking the issue button
  if (scenario === "illegal") {
    var legalityEl = document.querySelector("[data-status-legality]");
    var issueBtn = legalityEl ? legalityEl.querySelector("button") : null;
    if (issueBtn) {
      issueBtn.dispatchEvent(makeEvent("click", { target: issueBtn, button: 0 }));
      await flush();
      result.issuesAfterClick = statusSnapshot();

      // Run the entry-button click test entirely inside the VM context
      // to avoid realm boundary issues with Element objects.
      result.focusEntryCheck = vm.runInContext(
        "(function() {" +
        "  var bar = document.querySelector('[data-status-bar]');" +
        "  var issuesList = bar.querySelector('[data-status-issues]');" +
        "  if (!issuesList) return {error: 'no issues list'};" +
        "  var btns = [];" +
        "  for (var ci = 0; ci < issuesList.children.length; ci += 1) {" +
        "    var child = issuesList.children[ci];" +
        "    var btn = child.querySelector('button');" +
        "    if (btn) btns.push(btn);" +
        "  }" +
        "  if (!btns.length) return {error: 'no entry buttons', childCount: issuesList.children.length, html: String(issuesList.textContent)};" +
        "  var targetedBtn = btns[0];" +
        "  targetedBtn.dispatchEvent(new CustomEvent('click'));" +
        "  return {buttonText: String(targetedBtn.textContent), focusedEntryId: String(window.DeckLabBuilder._lastFocusedEntryId || '')};" +
        "})()",
        ctx
      );

      // Hide issues for render test
      issueBtn.dispatchEvent(makeEvent("click", { target: issueBtn, button: 0 }));
      await flush();
    }
  }

  // Test update on render
  builderState.validation.total_count = 100;
  builderState.validation.legal = true;
  builderState.validation.issues = [];
  builderState.validation.entry_issues = {};
  ctx.window.DeckLabBuilder.render();
  await flush();
  result.afterRender = statusSnapshot();

  console.log(JSON.stringify(result));
})();
}

// ============ Late-load scenario (AC-9) ============
if (scenario === "late-load") {
  // Set up the bar in the DOM
  body.appendChild(el("footer", { className: "dl-status-bar", "data-status-bar": "", "aria-label": "Deck status" }));

  await (async function boot() {
    var builderState = JSON.parse(JSON.stringify(fixtures.legal));
    var renderListeners = [];
    var ctx = makeContext();
    ctx.window.DeckLabBuilder = {
      version: 1,
      get shared() { return false; },
      getState: function () { return builderState; },
      onRender: function (fn) {
        renderListeners.push(fn);
        return function () { var idx = renderListeners.indexOf(fn); if (idx >= 0) renderListeners.splice(idx, 1); };
      },
      focusEntry: function (entryId) { this._lastFocusedEntryId = entryId; return true; },
      render: function () {
        renderListeners.forEach(function (fn) { try { fn(builderState); } catch (e) { console.error(e); } });
      },
    };

    // Dispatch deck-lab:ready (builder already exists, ready fired, status.js not yet loaded)
    document.dispatchEvent({ type: "deck-lab:ready", detail: { api: ctx.window.DeckLabBuilder } });
    await flush();

    // Step 2: Load deck-lab-status.js AFTER deck-lab:ready has already fired
    vm.runInContext(source, ctx, { filename: statusPath });
    await flush();

    // Step 3: Bar must already be populated from the script's immediate-render-on-boot path
    var barEl = document.querySelector("[data-status-bar]");
    var countEl = barEl ? barEl.querySelector(".dl-status-count") : null;
    var typeEls = barEl ? barEl.querySelectorAll("[data-status-type]") : [];
    var snap = {
      countText: countEl ? countEl.textContent : "",
      typeCount: typeEls.length,
      typeNames: Array.from(typeEls).map(function (t) { return t.getAttribute("data-status-type"); }),
    };

    console.log(JSON.stringify({ lateLoadSnapshot: snap, stateTotal: builderState.validation.total_count }));
  })();
  process.exit(0);
}
