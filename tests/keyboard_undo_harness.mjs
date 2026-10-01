import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const builderPath = process.argv[2];
const historyPath = process.argv[3];
const hotkeysPath = process.argv[4];
const scenario = process.argv[5] || "roundtrip";
const builderSource = fs.readFileSync(builderPath, "utf8");
const historySource = fs.readFileSync(historyPath, "utf8");
const hotkeysSource = fs.readFileSync(hotkeysPath, "utf8");

if (scenario === "inverse") {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const win = {};
  const documentStub = {
    addEventListener() {},
    removeEventListener() {},
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  vm.runInContext(historySource, vm.createContext({
    window: win,
    document: documentStub,
    console,
  }));
  const planned = win.DeckLabHistory.planInverse(input.before, input.commands, input.after);
  console.log(JSON.stringify(planned));
  process.exit(0);
}

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

function splitTop(selector) {
  const parts = [];
  let buf = "";
  let depth = 0;
  for (const ch of String(selector)) {
    if (ch === "[") depth += 1;
    else if (ch === "]") depth -= 1;
    else if (ch === "," && depth === 0) { parts.push(buf); buf = ""; continue; }
    buf += ch;
  }
  if (buf.trim()) parts.push(buf);
  return parts;
}
function compoundMatch(el, compound) {
  const part = String(compound).trim();
  if (!part || part === "*") return true;
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
    if (eq < 0) attrs.push([raw.trim(), null]);
    else attrs.push([raw.slice(0, eq).trim(), raw.slice(eq + 1).replace(/^["']|["']$/g, "")]);
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
}
function complexMatch(el, selector) {
  return splitTop(selector).some((alt) => {
    const parts = alt.trim().split(/\s+/).filter(Boolean);
    if (!parts.length || !compoundMatch(el, parts[parts.length - 1])) return false;
    let node = el.parentNode;
    for (let i = parts.length - 2; i >= 0; i -= 1) {
      while (node && !compoundMatch(node, parts[i])) node = node.parentNode;
      if (!node) return false;
      node = node.parentNode;
    }
    return true;
  });
}

let documentElement = null;
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
    this.captureListeners = {};
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
    this.selected = false;
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
    if (this.tagName === "DIALOG") {
      this.showModal = () => { this.open = true; this.setAttribute("open", ""); };
      this.close = () => {
        this.open = false;
        this.removeAttribute("open");
        this.dispatchEvent(makeEvent("close", { target: this }));
      };
    }
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
    if (key === "value") this.value = String(value);
    if (key === "open") this.open = true;
    if (key === "disabled") this.disabled = true;
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    if (key === "title") return this.title || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    const key = String(name);
    delete this.attributes[key];
    if (key === "disabled") this.disabled = false;
    if (key === "open") this.open = false;
  }
  appendChild(child) {
    if (typeof child === "string") {
      const t = this.ownerDocument.createElement("#text");
      t.textContent = child;
      child = t;
    }
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
  closest(selector) {
    let node = this;
    while (node && node.tagName) {
      if (complexMatch(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) { this.children.splice(idx, 1); child.parentNode = null; }
    return child;
  }
  focus() { document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = null; }
  click() { this.dispatchEvent(makeEvent("click", { target: this })); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn, options) {
    const capture = options === true || (options && options.capture);
    const bag = capture ? this.captureListeners : this.listeners;
    (bag[type] || (bag[type] = [])).push(fn);
  }
  removeEventListener(type, fn) {
    for (const bag of [this.listeners, this.captureListeners]) {
      const list = bag[type];
      if (!list) continue;
      const idx = list.indexOf(fn);
      if (idx >= 0) list.splice(idx, 1);
    }
  }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    if (!event.preventDefault) event.preventDefault = () => { event.defaultPrevented = true; };
    if (!event.stopPropagation) event.stopPropagation = () => { event._propagationStopped = true; };
    const chain = [];
    let cursor = this;
    while (cursor) { chain.push(cursor); cursor = cursor.parentNode; }
    chain.reverse();
    const fire = (node, bag) => {
      const list = ((bag && bag[event.type]) || []).slice();
      for (const fn of list) {
        fn(event);
        if (event._immediateStopped) return true;
      }
      return !!event._propagationStopped;
    };
    for (let i = 0; i < chain.length; i += 1) {
      const current = chain[i];
      event.currentTarget = current;
      if (current !== this) {
        if (fire(current, current.captureListeners)) return true;
      } else {
        if (fire(current, current.captureListeners)) return true;
        if (fire(current, current.listeners)) return true;
      }
    }
    for (let i = chain.length - 2; i >= 0; i -= 1) {
      const current = chain[i];
      event.currentTarget = current;
      if (fire(current, current.listeners)) return true;
    }
    return true;
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; }
  scrollIntoView() {}
}
function walk(node, acc) {
  acc.push(node);
  (node.children || []).forEach((child) => walk(child, acc));
  return acc;
}
function queryAll(root, selector) {
  const nodes = [];
  walk(root, []).forEach((node) => {
    if (node !== root && node.tagName && complexMatch(node, selector)) nodes.push(node);
  });
  nodes.forEach = Array.prototype.forEach;
  nodes.map = Array.prototype.map;
  nodes.filter = Array.prototype.filter;
  nodes.find = Array.prototype.find;
  nodes.some = Array.prototype.some;
  return nodes;
}

documentElement = new Element("html");
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
  addEventListener(type, fn, options) { documentElement.addEventListener(type, fn, options); },
  removeEventListener(type, fn) { documentElement.removeEventListener(type, fn); },
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
function makeEvent(type, extra = {}) {
  const event = {
    type,
    defaultPrevented: false,
    _propagationStopped: false,
    preventDefault() { event.defaultPrevented = true; },
    stopPropagation() { event._propagationStopped = true; },
    stopImmediatePropagation() { event._propagationStopped = true; event._immediateStopped = true; },
  };
  return Object.assign(event, extra);
}

function buildDeck() {
  return {
    id: "deck-1", title: "API Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-ramp", name: "Ramp", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries: [
      { id: "entry-cmd", card_id: "commander", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-ring", card_id: "ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
      { id: "entry-lotus", card_id: "lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 2, library_target: 99, total_count: 3, legal: false, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  };
}

function helpText() {
  const templatePath = path.join(path.dirname(builderPath), "../templates/deck_lab/builder.html");
  const html = fs.readFileSync(templatePath, "utf8");
  const match = html.match(/<dialog\b[^>]*\bdata-hotkeys-help\b[^>]*>[\s\S]*?<\/dialog>/);
  if (!match) return "";
  return match[0].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function mount(doc, shared) {
  body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(doc) }));
  const root = el("div", { className: "dl-builder", "data-shared": shared ? "true" : "false", "data-playmat-enabled": "true" });
  body.appendChild(root);
  head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
  const save = el("button", { id: "save-state", text: "Saved", title: "Click to retry a failed save" });
  body.appendChild(save);
  const undo = el("button", { className: "dl-history-button", type: "button", "data-undo": "", "aria-label": "Undo", "data-dl-tip": "Undo", text: "Undo" });
  undo.disabled = true;
  const redo = el("button", { className: "dl-history-button", type: "button", "data-redo": "", "aria-label": "Redo", "data-dl-tip": "Redo", text: "Redo" });
  redo.disabled = true;
  body.appendChild(undo);
  body.appendChild(redo);
  const toolbar = el("div", { className: "dl-builder-toolbar" });
  const combo = el("div", { className: "dl-card-combobox", "data-card-combobox": "" });
  const search = el("input", { id: "card-search", type: "search", "data-card-search": "" });
  const addZone = el("select", { id: "card-add-zone", "data-add-zone": "", "aria-label": "Add found cards to category" });
  const results = el("ul", { id: "card-search-list", "data-card-results": "" });
  results.hidden = true;
  combo.append(search, addZone, results, el("p", { "data-add-destination-hint": "", text: "Cards you add go to Unsorted." }), el("div", { "data-search-status": "" }));
  toolbar.appendChild(combo);
  toolbar.appendChild(el("span", { className: "dl-deck-count", "data-deck-count": "", text: "0/100" }));
  body.appendChild(toolbar);
  body.appendChild(el("input", { id: "other-field", type: "text" }));
  body.appendChild(el("button", { id: "plain-button", type: "button", text: "Plain" }));
  body.appendChild(el("div", { className: "dl-stats-rail" }));
  body.appendChild(el("div", { id: "table-view" }));
  const stage = el("div", { id: "playmat-stage" });
  stage.appendChild(el("div", { id: "playmat" }));
  const playmat = el("div", { id: "playmat-view" });
  playmat.appendChild(stage);
  body.appendChild(playmat);
  ["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
  body.appendChild(el("div", { "data-playmat-selection": "" }));
  body.appendChild(el("div", { "data-deck-tag-list": "" }));
  body.appendChild(el("div", { "data-tag-summary": "" }));
  body.appendChild(el("div", { "data-tag-options": "" }));
  const help = el("dialog", { className: "dl-dialog dl-hotkeys-help", "data-hotkeys-help": "", "aria-labelledby": "hotkeys-help-title" });
  help.textContent = helpText();
  body.appendChild(help);
  const imageDialog = el("dialog", { id: "card-image-dialog" });
  body.appendChild(imageDialog);
}

const doc = buildDeck();
let entrySeq = 0;
let conflictArmed = false;
const fetches = [];
const searchResults = [
  { id: "sol", name: "Sol Ring", type_line: "Artifact" },
  { id: "petal", name: "Lotus Petal", type_line: "Artifact" },
];
const catalog = {
  sol: { name: "Sol Ring", type_line: "Artifact" },
  petal: { name: "Lotus Petal", type_line: "Artifact" },
  ring: { name: "Sol Ring", type_line: "Artifact" },
  lotus: { name: "Lotus Petal", type_line: "Artifact" },
  commander: { name: "Kinnan Test", type_line: "Legendary Creature" },
};

function applyOne(command) {
  const type = command.type;
  const entries = doc.entries;
  if (type === "adjust_quantity" || type === "set_quantity") {
    const entry = entries.find((item) => item.id === command.entry_id);
    if (!entry) throw new Error("That card is no longer in this deck.");
    let quantity;
    if (type === "adjust_quantity") {
      const delta = Number(command.delta);
      if (delta !== 1 && delta !== -1) throw new Error("Quantity adjustments must be one card at a time.");
      quantity = Number(entry.quantity) + delta;
    } else quantity = Number(command.quantity);
    if (quantity <= 0) doc.entries = entries.filter((item) => item.id !== entry.id);
    else if (quantity <= 99) entry.quantity = quantity;
    else throw new Error("Quantity must be between 0 and 99.");
    return;
  }
  if (type === "remove_entry") {
    if (!entries.some((item) => item.id === command.entry_id)) throw new Error("That card is no longer in this deck.");
    doc.entries = entries.filter((item) => item.id !== command.entry_id);
    return;
  }
  if (type === "add_card") {
    const cardId = String(command.card_id || "");
    const commander = !!command.is_commander;
    const zoneId = commander ? null : command.zone_id;
    const qty = Math.max(1, Math.min(99, Number(command.quantity || 1)));
    const existing = entries.find((item) => item.card_id === cardId && !!item.is_commander === commander && String(item.zone_id || "") === String(zoneId || ""));
    if (existing) existing.quantity = Math.min(99, Number(existing.quantity) + qty);
    else {
      const known = catalog[cardId] || { name: cardId, type_line: "Card" };
      entrySeq += 1;
      entries.push({
        id: "entry-added-" + entrySeq,
        card_id: cardId,
        name: known.name,
        is_commander: commander,
        quantity: qty,
        zone_id: zoneId,
        sort_order: entries.length,
        type_line: known.type_line,
        mana_cost: "",
        mana_value: 0,
        oracle_text: "",
        color_identity: [],
        image_uri: "",
        role: command.role || "",
        format_legal: true,
        commander_legal: true,
        validation_issues: [],
      });
    }
    return;
  }
  if (type === "move_entry") {
    const entry = entries.find((item) => item.id === command.entry_id);
    if (!entry) throw new Error("That card is no longer in this deck.");
    entry.zone_id = command.zone_id;
    entry.sort_order = Number(command.sort_order || 0);
    return;
  }
  if (type === "set_role") {
    const entry = entries.find((item) => item.id === command.entry_id);
    if (!entry) throw new Error("That card is no longer in this deck.");
    entry.role = command.role || "";
    return;
  }
  if (type === "rename_zone") {
    const zone = doc.zones.find((item) => item.id === command.zone_id);
    if (!zone) throw new Error("Choose a valid zone and name.");
    zone.name = command.name;
    return;
  }
  if (type === "create_zone") {
    doc.zones.push({
      id: command.zone_id || ("zone-added-" + (doc.zones.length + 1)),
      name: command.name || "New zone",
      x: command.x || 0,
      y: command.y || 0,
      width: 400,
      layout_mode: "spread",
      sort_order: doc.zones.length,
      layer: 0,
    });
    return;
  }
  if (type === "delete_zone") {
    const unsorted = doc.zones.find((item) => item.name === "Unsorted") || doc.zones[0];
    entries.forEach((entry) => { if (entry.zone_id === command.zone_id) entry.zone_id = unsorted.id; });
    doc.zones = doc.zones.filter((item) => item.id !== command.zone_id);
    return;
  }
  if (type === "rename_deck") {
    if (!String(command.title || "").trim()) throw new Error("Deck name cannot be empty.");
    doc.title = command.title;
    return;
  }
  if (type === "update_view") {
    doc.preferences = Object.assign({}, doc.preferences, command);
  }
}

function boot(shared) {
  mount(doc, shared);
  const store = {};
  let uuid = 0;
  const windowObj = {
    matchMedia() { return { matches: false, addEventListener() {} }; },
    setTimeout, clearTimeout,
    crypto: { randomUUID: () => "uuid-" + (++uuid) },
    DeckLabSelects: { refresh() {} },
    prompt() { return null; },
  };
  const ctx = vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: {
      getItem: (k) => store[k] ?? null,
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    fetch(url, opts = {}) {
      const target = String(url);
      const packet = opts.body ? JSON.parse(opts.body) : null;
      fetches.push({ url: target, method: opts.method || "GET", body: packet });
      if (target.includes("/commands")) {
        if (conflictArmed) {
          conflictArmed = false;
          return Promise.resolve({ ok: false, status: 409, json: async () => ({ detail: "conflict" }) });
        }
        try {
          (packet.commands || []).forEach(applyOne);
          doc.revision = Number(doc.revision || 0) + 1;
        } catch (error) {
          return Promise.resolve({ ok: false, status: 400, json: async () => ({ detail: error.message }) });
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(doc)) });
      }
      if (target.includes("/api/cards")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ results: searchResults }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(doc)) });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp,
    CustomEvent: function CustomEvent(type, init = {}) { this.type = type; this.detail = init.detail; },
    crypto: windowObj.crypto,
  });
  vm.runInContext(builderSource, ctx, { filename: builderPath });
  const commandBefore = windowObj.DeckLabBuilder.command;
  vm.runInContext(historySource, ctx, { filename: historyPath });
  vm.runInContext(hotkeysSource, ctx, { filename: hotkeysPath });
  return { windowObj, commandBefore, commandAfter: windowObj.DeckLabBuilder.command };
}

async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 12; i += 1) await new Promise((r) => setTimeout(r, 0));
}
function commandBodies() {
  return fetches.filter((item) => item.url.includes("/commands")).map((item) => item.body && item.body.commands);
}
function press(key, extra = {}) {
  const target = extra.target || document.documentElement;
  target.dispatchEvent(makeEvent("keydown", Object.assign({ key, target }, extra)));
}
function buttonState() {
  const undo = document.querySelector("[data-undo]");
  const redo = document.querySelector("[data-redo]");
  const save = document.getElementById("save-state");
  return {
    undoDisabled: !!(undo && undo.disabled),
    redoDisabled: !!(redo && redo.disabled),
    undoTip: undo ? undo.getAttribute("data-dl-tip") : null,
    redoTip: redo ? redo.getAttribute("data-dl-tip") : null,
    undoLabel: undo ? undo.getAttribute("aria-label") : null,
    redoLabel: redo ? redo.getAttribute("aria-label") : null,
    title: save ? save.title : null,
  };
}
async function main() {
  const shared = scenario === "shared";
  const booted = boot(shared);
  await flush();
  const api = booted.windowObj.DeckLabBuilder;
  let result;
  if (scenario === "roundtrip") {
    await api.command([{ type: "remove_entry", entry_id: "entry-ring" }]);
    await flush();
    const afterRemove = buttonState();
    document.querySelector("[data-undo]").click();
    await flush();
    const afterUndo = buttonState();
    document.querySelector("[data-redo]").click();
    await flush();
    result = { bodies: commandBodies(), afterRemove, afterUndo, afterRedo: buttonState() };
  } else if (scenario === "buttons") {
    await api.command([{ type: "remove_entry", entry_id: "entry-ring" }]);
    await flush();
    const after = buttonState();
    document.querySelector("[data-undo]").click();
    await flush();
    result = { after, afterUndo: buttonState(), stats: booted.windowObj.DeckLabHistory.stats() };
  } else if (scenario === "conflict") {
    await api.command([{ type: "set_role", entry_id: "entry-ring", role: "ramp" }]);
    await flush();
    const before = booted.windowObj.DeckLabHistory.stats();
    conflictArmed = true;
    await api.command([{ type: "set_role", entry_id: "entry-ring", role: "draw" }]);
    await flush();
    result = { before, after: booted.windowObj.DeckLabHistory.stats(), buttons: buttonState() };
  } else if (scenario === "nonundoable") {
    await api.command([{ type: "set_role", entry_id: "entry-ring", role: "ramp" }]);
    await flush();
    document.querySelector("[data-undo]").click();
    await flush();
    const before = booted.windowObj.DeckLabHistory.stats();
    await api.command([{ type: "rename_deck", title: "Renamed" }]);
    await flush();
    result = { before, after: booted.windowObj.DeckLabHistory.stats(), buttons: buttonState() };
  } else if (scenario === "cap") {
    for (let i = 0; i <= 50; i += 1) {
      await api.command([{ type: "set_role", entry_id: "entry-ring", role: "step-" + i }]);
      await flush();
    }
    result = booted.windowObj.DeckLabHistory.stats();
  } else if (scenario === "slash") {
    document.getElementById("plain-button").focus();
    press("/");
    const fromButton = document.activeElement && document.activeElement.id;
    document.getElementById("other-field").focus();
    press("/");
    const fromField = document.activeElement && document.activeElement.id;
    result = { fromButton, fromField };
  } else if (scenario === "shiftenter") {
    const input = document.querySelector("[data-card-search]");
    input.focus();
    input.value = "sol";
    press("ArrowDown", { target: input });
    await flush();
    press("ArrowDown", { target: input });
    press("ArrowDown", { target: input });
    press("Enter", { target: input, shiftKey: true });
    await flush();
    result = { bodies: commandBodies(), active: document.activeElement && document.activeElement.id };
  } else if (scenario === "navigate") {
    const focused = [];
    function note() {
      const node = document.activeElement;
      focused.push(node && node.getAttribute ? node.getAttribute("data-entry-id") : null);
    }
    press("j");
    note();
    press("j");
    note();
    press("k");
    note();
    press("ArrowDown");
    note();
    press("ArrowUp");
    note();
    const ringRow = document.querySelector('[data-entry-id="entry-ring"]');
    const child = ringRow.querySelector(".dl-card-name");
    child.dispatchEvent(makeEvent("mouseover", { target: child }));
    press("j");
    note();
    press("k");
    press("+");
    await flush();
    const afterPlus = api.getState().entries.find((entry) => entry.id === "entry-ring");
    press("-");
    await flush();
    const afterMinus = api.getState().entries.find((entry) => entry.id === "entry-ring");
    press("-");
    await flush();
    const removed = !api.getState().entries.some((entry) => entry.name === "Sol Ring");
    const undoTip = buttonState().undoTip;
    document.querySelector("[data-undo]").click();
    await flush();
    const restored = api.getState().entries.find((entry) => entry.name === "Sol Ring");
    press("j");
    press("j");
    press("Delete");
    await flush();
    result = {
      focused,
      afterPlus: afterPlus && afterPlus.quantity,
      afterMinus: afterMinus && afterMinus.quantity,
      removed,
      undoTip,
      restored: restored ? restored.quantity : null,
      deleted: commandBodies().at(-1),
      bodies: commandBodies(),
    };
  } else if (scenario === "selection") {
    press("j");
    press("j");
    press("x");
    const selected = api.getSelection();
    press("x");
    const cleared = api.getSelection();
    press(" ");
    const spaced = api.getSelection();
    press("m");
    const active = document.activeElement;
    result = {
      selected,
      cleared,
      spaced,
      tag: active ? active.tagName : null,
      className: active ? active.className : null,
    };
  } else if (scenario === "help") {
    press("?");
    const dialog = document.querySelector("[data-hotkeys-help]");
    result = { open: !!(dialog && dialog.open), text: dialog ? dialog.textContent : "" };
  } else if (scenario === "shared") {
    const search = document.querySelector("[data-card-search]");
    search.focus();
    const before = document.activeElement && document.activeElement.id;
    document.getElementById("plain-button").focus();
    press("/");
    result = {
      sameCommand: booted.commandBefore === booted.commandAfter,
      undoInstalled: typeof booted.windowObj.DeckLabHistory.undo === "function",
      stayed: document.activeElement && document.activeElement.id,
      searchFocused: document.activeElement === search,
      before,
    };
  } else if (scenario === "internal") {
    const plus = [...document.querySelectorAll("button")].find((button) => button.getAttribute("aria-label") === "Add one Sol Ring");
    plus.click();
    await flush();
    const afterPlus = api.getState().entries.find((entry) => entry.id === "entry-ring");
    const tipped = buttonState();
    document.querySelector("[data-undo]").click();
    await flush();
    const afterUndo = api.getState().entries.find((entry) => entry.id === "entry-ring");
    result = {
      quantity: afterPlus && afterPlus.quantity,
      undoTip: tipped.undoTip,
      restored: afterUndo && afterUndo.quantity,
      bodies: commandBodies(),
    };
  } else result = { error: "unknown scenario " + scenario };
  console.log(JSON.stringify(result));
}

main().catch((error) => {
  console.error(error && error.stack || error);
  process.exit(1);
});
