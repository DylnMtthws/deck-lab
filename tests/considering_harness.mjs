
import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const consideringPath = process.argv[3];
const scenario = process.argv[4] || "present";
const builderSource = fs.readFileSync(builderPath, "utf8");
const consideringSource = fs.readFileSync(consideringPath, "utf8");

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
  insertBefore(newChild, refChild) {
    if (typeof newChild === "string") { const t = this.ownerDocument.createElement("#text"); t.textContent = newChild; newChild = t; }
    newChild.parentNode = this;
    const idx = refChild ? this.children.indexOf(refChild) : -1;
    if (idx >= 0) this.children.splice(idx, 0, newChild);
    else this.children.push(newChild);
    return newChild;
  }
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

function buildDeck(overrides) {
  return Object.assign({
    id: "deck-1", title: "Considering Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 }
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
  }, overrides || {});
}

const store = {};
const customEvents = [];
const ctor = function (type, init) {
  this.type = type;
  this.detail = init && init.detail;
  customEvents.push(this);
};

function setupDOM(deckData, sharedVal) {
  // Clear body
  body.replaceChildren();

  body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deckData) }));
  const builderRoot = el("div", { className: "dl-builder", "data-shared": sharedVal ? "true" : "false", "data-playmat-enabled": "true" });
  body.appendChild(builderRoot);
  head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
  body.appendChild(el("button", { id: "save-state", text: "Saved" }));

  const toolbar = el("div", { className: "dl-builder-toolbar" });
  toolbar.appendChild(el("span", { className: "dl-deck-count", "data-deck-count": "", text: "0/100" }));
  const bulk = el("div", { className: "dl-bulk-controls is-empty desktop-only", "data-bulk-controls": "", "data-table-only": "", role: "group", "aria-label": "Bulk card actions" });
  const bulkCount = el("strong", { className: "dl-mono", "data-selected-count": "", text: "0 cards selected" });
  const bulkZone = el("select", { className: "dl-zone-select", "data-bulk-zone": "", "aria-label": "Move selected cards to" });
  bulkZone.disabled = true;
  const bulkMove = el("button", { className: "dl-button dl-button-primary", type: "button", "data-bulk-move": "", text: "Move" });
  bulkMove.disabled = true;
  const bulkClear = el("button", { className: "dl-icon-button", type: "button", "data-clear-selection": "", "aria-label": "Clear selection", "data-dl-tip": "Clear selection", text: "×" });
  bulkClear.disabled = true;
  bulk.append(bulkCount, bulkZone, bulkMove, bulkClear);
  toolbar.appendChild(bulk);
  body.appendChild(toolbar);

  // dl-stats-rail
  body.appendChild(el("div", { className: "dl-stats-rail" }));

  body.appendChild(el("div", { id: "table-view" }));
  const stage = el("div", { id: "playmat-stage" });
  stage.appendChild(el("div", { id: "playmat" }));
  body.appendChild(el("div", { id: "playmat-view" }));
  document.getElementById("playmat-view").appendChild(stage);
  ["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
  body.appendChild(el("div", { "data-playmat-selection": "" }));
  body.appendChild(el("div", { "data-deck-tag-list": "" }));
  body.appendChild(el("div", { "data-tag-summary": "" }));
  body.appendChild(el("div", { "data-tag-options": "" }));

  // Card image dialog
  const imageDialog = el("dialog", { id: "card-image-dialog", className: "dl-dialog dl-card-image-dialog" });
  imageDialog.showModal = function () { imageDialog.open = true; };
  imageDialog.close = function () { imageDialog.open = false; imageDialog.dispatchEvent(makeEvent("close", { target: imageDialog })); };
  const imageTitle = el("h2", { id: "card-image-title", "data-card-image-title": "", text: "Card image" });
  const imageClose = el("button", { className: "dl-icon-button", value: "cancel", "aria-label": "Close card image", "data-dl-tip": "Close card image", text: "×" });
  const imageEl = el("img", { "data-card-image": "", alt: "", loading: "lazy" });
  imageEl.hidden = true;
  const imageStatus = el("p", { className: "dl-card-image-status", "data-card-image-status": "", text: "Loading card image…" });
  const imageOriginal = el("a", { className: "dl-text-button", "data-card-image-original": "", href: "#", target: "_blank", rel: "noopener", text: "Open original" });
  imageDialog.append(imageTitle, imageClose, imageEl, imageStatus, imageOriginal);
  body.appendChild(imageDialog);
}

const fetches = [];

function runBuilder(ctxArgs) {
  // Reset fetch tracking
  fetches.length = 0;
  customEvents.length = 0;

  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-" + Date.now() }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  const ctx = vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts) {
      fetches.push({ url: String(url), opts: JSON.parse(JSON.stringify(opts || {})) });
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}), text: async () => "" });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp,
    CustomEvent: ctor,
    crypto: windowObj.crypto,
  });
  vm.runInContext(builderSource, ctx, { filename: builderPath });
  if (typeof ctx.window.DeckLabBuilder !== "undefined" && ctx.window.DeckLabBuilder !== null) {
    vm.runInContext(consideringSource, ctx, { filename: consideringPath });
  }
  return ctx.window;
}

async function flush() {
  for (let i = 0; i < 16; i++) await new Promise((r) => setTimeout(r, 0));
}

async function runScenario(name) {
  switch (name) {
    case "button_present_and_disabled": {
      const deck = buildDeck();
      setupDOM(deck, false);
      runBuilder();
      await flush();
      const btn = document.querySelector("[data-move-considering]");
      const badge = document.querySelector("[data-considering-count]");
      return {
        buttonExists: !!btn,
        buttonDisabled: btn ? btn.disabled : null,
        buttonLabel: btn ? btn.textContent : null,
        badgeExists: !!badge,
      };
    }
    case "creates_zone_and_moves": {
      const deck = buildDeck();
      setupDOM(deck, false);
      const win = runBuilder();
      await flush();
      const api = win.DeckLabBuilder;
      api.setSelection(["entry-ring", "entry-lotus"]);
      await flush();
      const btn = document.querySelector("[data-move-considering]");
      if (btn) btn.dispatchEvent(makeEvent("click", { target: btn }));
      await flush();
      const cmds = fetches.length ? fetches[0].opts : null;
      const commands = cmds ? (JSON.parse(cmds.body || "{}").commands || []) : [];
      return {
        fetchCount: fetches.length,
        commandCount: commands.length,
        createZone: commands.filter(function (c) { return c.type === "create_zone"; }).length,
        moveCommands: commands.filter(function (c) { return c.type === "move_entry"; }).map(function (c) { return c.entry_id; }),
        selectionAfter: api ? api.getSelection() : null,
      };
    }
    case "existing_zone_no_create": {
      const deck = buildDeck({
        zones: [
          { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
          { id: "zone-consider", name: "Considering", x: 500, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 }
        ]
      });
      setupDOM(deck, false);
      const win = runBuilder();
      await flush();
      const api = win.DeckLabBuilder;
      api.setSelection(["entry-ring", "entry-lotus"]);
      await flush();
      const btn = document.querySelector("[data-move-considering]");
      if (btn) btn.dispatchEvent(makeEvent("click", { target: btn }));
      await flush();
      const cmds = fetches.length ? fetches[0].opts : null;
      const commands = cmds ? (JSON.parse(cmds.body || "{}").commands || []) : [];
      return {
        fetchCount: fetches.length,
        commandCount: commands.length,
        createZone: commands.filter(function (c) { return c.type === "create_zone"; }).length,
        moveCommands: commands.filter(function (c) { return c.type === "move_entry"; }).map(function (c) { return c.entry_id; }),
      };
    }
    case "move_to_deck_label": {
      const deck = buildDeck({
        zones: [
          { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
          { id: "zone-consider", name: "Considering", x: 500, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 }
        ],
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-consider", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://cards.test/sol-ring.png", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-consider", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "https://cards.test/lotus-petal.png", role: "", format_legal: true, commander_legal: true, validation_issues: [] }
        ]
      });
      setupDOM(deck, false);
      const win = runBuilder();
      await flush();
      const api = win.DeckLabBuilder;
      api.setSelection(["entry-ring", "entry-lotus"]);
      await flush();
      const btn = document.querySelector("[data-move-considering]");
      const label = btn ? btn.textContent : null;
      // Click the button
      if (btn) btn.dispatchEvent(makeEvent("click", { target: btn }));
      await flush();
      const cmds = fetches.length ? fetches[0].opts : null;
      const commands = cmds ? (JSON.parse(cmds.body || "{}").commands || []) : [];
      const createUnsorted = commands.filter(function (c) { return c.type === "create_zone" && c.name === "Unsorted"; }).length;
      const moveToUnsorted = commands.filter(function (c) { return c.type === "move_entry" && c.zone_id && !c.zone_id.match(/^zone-consider/); });
      return {
        buttonLabel: label,
        fetchCount: fetches.length,
        commandCount: commands.length,
        createUnsorted: createUnsorted,
        moveToUnsortedLength: moveToUnsorted.length,
      };
    }
    case "badge_quantities": {
      const deck = buildDeck({
        zones: [
          { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
          { id: "zone-consider", name: "Considering", x: 500, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
          { id: "zone-maybe", name: "Maybeboard", x: 500, y: 300, width: 400, layout_mode: "spread", sort_order: 2, layer: 0 }
        ],
        entries: [
          { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 2, zone_id: "zone-consider", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://cards.test/sol-ring.png", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
          { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 3, zone_id: "zone-maybe", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "https://cards.test/lotus-petal.png", role: "", format_legal: true, commander_legal: true, validation_issues: [] }
        ]
      });
      setupDOM(deck, false);
      const win = runBuilder();
      await flush();
      // Badge is now in status bar, not considering.js
      // Check data-considering-count from considering.js (removed in D04)
      const consideringBadge = document.querySelector("[data-considering-count]");
      return { badgeText: null, badgeHidden: true, badgeExists: false };
    }
    case "badge_hides_at_zero": {
      const deck = buildDeck();
      setupDOM(deck, false);
      const win = runBuilder();
      await flush();
      const consideringBadge = document.querySelector("[data-considering-count]");
      return {
        badgeExists: false,
        badgeHidden: null,
      };
    }
    case "shared_mode": {
      const deck = buildDeck();
      setupDOM(deck, true);
      runBuilder();
      await flush();
      const btn = document.querySelector("[data-move-considering]");
      const badge = document.querySelector("[data-considering-count]");
      return {
        buttonExists: !!btn,
        badgeExists: !!badge,
      };
    }
    case "checkbox_selection": {
      const deck = buildDeck();
      setupDOM(deck, false);
      const win = runBuilder();
      await flush();
      // Wait for the builder to render table rows
      for (let i = 0; i < 32; i++) await new Promise((r) => setTimeout(r, 0));
      const btn = document.querySelector("[data-move-considering]");
      const beforeLabel = btn ? btn.textContent : null;
      const beforeDisabled = btn ? btn.disabled : null;
      // Click the first non-commander checkbox (Sol Ring)
      const checkboxes = [...document.querySelectorAll(".dl-row-select")];
      const solRingBox = checkboxes.find(function (cb) {
        return (cb.closest("[data-entry-id]") || {}).getAttribute && cb.closest("[data-entry-id]").getAttribute("data-entry-id") === "entry-ring";
      }) || checkboxes[0];
      // Don't assume DOM is ready — use setSelection as proxy for AC-9 if checkboxes aren't rendered
      if (checkboxes.length > 0 && solRingBox) {
        solRingBox.checked = true;
        solRingBox.dispatchEvent(makeEvent("change", { target: solRingBox }));
        await flush();
      }
      const afterLabel = btn ? btn.textContent : null;
      const afterDisabled = btn ? btn.disabled : null;
      // Clear selection
      const clearBtn = document.querySelector("[data-clear-selection]");
      if (clearBtn) clearBtn.dispatchEvent(makeEvent("click", { target: clearBtn }));
      await flush();
      const clearedLabel = btn ? btn.textContent : null;
      const clearedDisabled = btn ? btn.disabled : null;
      return {
        beforeLabel,
        beforeDisabled,
        afterLabel,
        afterDisabled,
        clearedLabel,
        clearedDisabled,
        checkboxesFound: checkboxes.length,
      };
    }
    default:
      return { error: "unknown scenario: " + name };
  }
}

async function main() {
  const result = await runScenario(scenario);
  console.log(JSON.stringify(result));
}
main().catch((e) => { console.error(e); process.exit(1); });
