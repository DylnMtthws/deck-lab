
import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
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


const deck = {
  id: "deck-1", title: "Selection Deck", revision: 0,
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
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));

// Toolbar: the DYL-67 bulk action bar ships visible and fully disabled.
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

// DYL-66 card image dialog.
const modalCalls = [];
const imageDialog = el("dialog", { id: "card-image-dialog", className: "dl-dialog dl-card-image-dialog" });
imageDialog.showModal = function () { modalCalls.push(imageDialog.returnValue); imageDialog.open = true; };
imageDialog.close = function () { imageDialog.open = false; imageDialog.dispatchEvent(makeEvent("close", { target: imageDialog })); };
const imageTitle = el("h2", { id: "card-image-title", "data-card-image-title": "", text: "Card image" });
const imageClose = el("button", { className: "dl-icon-button", value: "cancel", "aria-label": "Close card image", "data-dl-tip": "Close card image", text: "×" });
const imageEl = el("img", { "data-card-image": "", alt: "", loading: "lazy" });
imageEl.hidden = true;
const imageStatus = el("p", { className: "dl-card-image-status", "data-card-image-status": "", text: "Loading card image…" });
const imageOriginal = el("a", { className: "dl-text-button", "data-card-image-original": "", href: "#", target: "_blank", rel: "noopener", text: "Open original" });
imageDialog.append(imageTitle, imageClose, imageEl, imageStatus, imageOriginal);
body.appendChild(imageDialog);

const fetches = [];
const store = {};
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

await (async function boot() {
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetches.push({ url: String(url), opts });
      deck.revision = (deck.revision || 0) + 1;
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  await flush();
})();

function bulkState() {
  return {
    containerHidden: !!bulk.hidden,
    containerClass: bulk.className,
    count: bulkCount.textContent,
    move: !!bulkMove.disabled,
    zone: !!bulkZone.disabled,
    clear: !!bulkClear.disabled,
  };
}

const zero = bulkState();

const rowChecks = [...document.querySelectorAll(".dl-row-select")];
function check(index) {
  const box = rowChecks[index];
  box.checked = true;
  box.dispatchEvent(makeEvent("change", { target: box }));
}
check(0);
const one = bulkState();
check(1);
const two = bulkState();

// Clearing the selection returns the bar to its disabled resting state.
rowChecks.forEach((box) => { box.checked = false; box.dispatchEvent(makeEvent("change", { target: box })); });
const backToZero = bulkState();

// DYL-66: the row preview control opens the modal instead of a new tab.
const preview = [...document.querySelectorAll(".dl-card-preview")].find((n) => (n.getAttribute("aria-label") || "").includes("Sol Ring"));
const previewMarkup = {
  tag: preview.tagName,
  type: preview.type,
  target: String(preview.target || ""),
  href: String(preview.href || ""),
  aria: preview.getAttribute("aria-label"),
  tip: preview.getAttribute("data-dl-tip"),
};
preview.dispatchEvent(makeEvent("click", { target: preview, button: 0 }));
const opened = {
  modalCalls: modalCalls.length,
  title: imageTitle.textContent,
  src: imageEl.src,
  alt: imageEl.alt,
  imgHidden: !!imageEl.hidden,
  status: imageStatus.textContent,
  original: imageOriginal.href || imageOriginal.getAttribute("href"),
};
imageEl.dispatchEvent(makeEvent("load", { target: imageEl }));
const afterLoad = { imgHidden: !!imageEl.hidden, statusHidden: !!imageStatus.hidden };
imageEl.dispatchEvent(makeEvent("error", { target: imageEl }));
const afterError = { imgHidden: !!imageEl.hidden, statusHidden: !!imageStatus.hidden, status: imageStatus.textContent };

// The card-name link opens the same dialog and no longer targets a new tab.
const nameLink = document.querySelectorAll(".dl-card-name")[1];
const nameMarkup = { tag: nameLink.tagName, target: String(nameLink.target || ""), tip: nameLink.getAttribute("data-dl-tip"), aria: nameLink.getAttribute("aria-label") };
let prevented = false;
nameLink.dispatchEvent({ type: "click", target: nameLink, preventDefault() { prevented = true; }, stopPropagation() {} });
const afterNameClick = { modalCalls: modalCalls.length, prevented, title: imageTitle.textContent };

// DYL-65: every icon-only control rendered by the builder carries a tip.
const tips = {};
[...document.querySelectorAll("button"), ...document.querySelectorAll("summary")].forEach((el2) => {
  const aria = el2.getAttribute("aria-label");
  if (!aria) return;
  tips[aria] = el2.getAttribute("data-dl-tip");
});

console.log(JSON.stringify({
  zero, one, two, backToZero,
  previewMarkup, opened, afterLoad, afterError, nameMarkup, afterNameClick,
  tips,
  fetches: fetches.length,
}));
