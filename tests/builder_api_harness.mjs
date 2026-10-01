
import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const scenario = process.argv[3] || "contract";
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
    id: "deck-1", title: "API Deck", revision: 0,
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

const deck = buildDeck();
const fetches = [];
const store = {};
const customEvents = [];
const ctor = class CustomEvent2 { constructor(type, init = {}) { this.type = type; this.detail = init.detail; customEvents.push(this); } };

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
const builderRoot = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" });
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

// dl-stats-rail for railSection tests
const statsRail = el("div", { className: "dl-stats-rail" });
body.appendChild(statsRail);

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

async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

const readyEvents = [];
let api = null;

await (async function boot() {
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetches.push({ url: String(url), opts });
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp,
    CustomEvent: ctor,
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  await flush();
  api = windowObj.DeckLabBuilder;

  // Collect ready events
  customEvents.forEach((ev) => {
    if (ev.type === "deck-lab:ready") readyEvents.push(ev);
  });
})();

async function runScenario(name) {
  switch (name) {
    case "contract": {
      const keys = api ? Object.keys(api).sort() : [];
      return { exists: !!api, keys, version: api ? api.version : null };
    }
    case "ready": {
      return { count: readyEvents.length, hasApi: readyEvents.length > 0 && !!readyEvents[0].detail && !!readyEvents[0].detail.api };
    }
    case "onrender": {
      const results = [];
      let callCount = 0, callCount2 = 0;
      const unsub = api.onRender(function () { callCount++; });
      api.onRender(function () { callCount2++; });
      const throwListener = api.onRender(function () { throw new Error("listen error"); });
      api.render();
      results.push({ afterFirstRender: callCount, afterFirstRender2: callCount2 });
      unsub();
      api.render();
      results.push({ afterSecondRenderUnsubbed: callCount, afterSecondRender2: callCount2 });
      console.error = function () {};
      api.render();
      results.push({ afterThirdRender: callCount2 });
      return results;
    }
    case "entryfilter": {
      api.setEntryFilter(function (e) { return e.id === "entry-ring"; }, "Only Ring");
      await flush();
      const tv = document.getElementById("table-view");
      const entries = tv ? tv.querySelectorAll("[data-entry-id]") : [];
      const chip = document.querySelector("[data-entry-filter-chip]");
      const clearBtn = document.querySelector("[data-entry-filter-clear]");
      const entryIds = Array.from(entries).map((e) => e.getAttribute("data-entry-id"));
      const chipText = chip ? (chip.children[0] ? chip.children[0].textContent || chip.textContent : chip.textContent) : null;
      // Click clear
      if (clearBtn) clearBtn.dispatchEvent(makeEvent("click", { target: clearBtn }));
      await flush();
      const tv2 = document.getElementById("table-view");
      const entriesAfter = tv2 ? tv2.querySelectorAll("[data-entry-id]") : [];
      const chipAfter = document.querySelector("[data-entry-filter-chip]");
      return { filteredCount: entryIds.length, ids: entryIds, chipExists: !!chip, clearExists: !!clearBtn, chipText, totalAfter: entriesAfter.length, chipAfterExists: !!chipAfter };
    }
    case "entryfilter_check_after": {
      const tv2 = document.getElementById("table-view");
      const entriesAfter = tv2 ? tv2.querySelectorAll("[data-entry-id]") : [];
      const chipAfter = document.querySelector("[data-entry-filter-chip]");
      const totalAfter = entriesAfter.length;
      return { filteredCount: entryIds.length, ids: entryIds, chipExists: !!chip, clearExists: !!clearBtn, chipText, totalAfter, chipAfterExists: !!chipAfter };
    }
    case "setselection": {
      const events = [];
      const origDispatch = document.dispatchEvent.bind(document);
      const listener = function (ev) { if (ev.type === "deck-lab:selection") events.push({ ids: ev.detail ? ev.detail.ids : null }); };
      document.addEventListener("deck-lab:selection", listener);
      api.setSelection(["entry-ring", "entry-lotus", "entry-nonexistent"]);
      const sel = api.getSelection();
      document.removeEventListener("deck-lab:selection", listener);
      const eventDetail = events.length ? { ids: events[0].ids } : null;
      return { selection: sel, event: eventDetail };
    }
    case "shared_command": {
      // Build fresh DOM with shared=true, re-execute, confirm command resolves and fetch is untouched.
      const sharedDocEl = new Element("html");
      const sharedHead = new Element("head", null);
      const sharedBody = new Element("body", null);
      sharedHead.ownerDocument = { createElement(tag) { return new Element(tag); } };
      sharedBody.ownerDocument = sharedHead.ownerDocument;
      sharedDocEl.appendChild(sharedHead);
      sharedDocEl.appendChild(sharedBody);
      const sharedDoc = {
        documentElement: sharedDocEl, body: sharedBody, head: sharedHead, activeElement: null,
        createElement(tag) { const e2 = new Element(tag, sharedDoc); e2.ownerDocument = sharedDoc; return e2; },
        createTextNode(text) { const e2 = sharedDoc.createElement("#text"); e2.textContent = String(text == null ? "" : text); return e2; },
        createElementNS(_ns, tag) { return sharedDoc.createElement(tag); },
        getElementById(id) { return walk(sharedDocEl, []).find((e2) => e2.id === id) || null; },
        querySelector(sel) { return sel === "body" ? sharedBody : (queryAll(sharedDocEl, sel)[0] || null); },
        querySelectorAll(sel) { return queryAll(sharedDocEl, sel); },
        addEventListener(type, fn) { sharedDocEl.addEventListener(type, fn); },
        removeEventListener(type, fn) { const list = sharedDocEl.listeners[type]; if (list) { const idx = list.indexOf(fn); if (idx >= 0) list.splice(idx, 1); } },
        dispatchEvent(event) { return sharedDocEl.dispatchEvent(event); },
      };
      const sDeck = buildDeck();
      sharedBody.appendChild(Object.assign(sharedDoc.createElement("script"), { id: "deck-document-data", textContent: JSON.stringify(sDeck) }));
      var sBuildRoot = sharedDoc.createElement("div");
      sBuildRoot.className = "dl-builder";
      sBuildRoot.setAttribute("data-shared", "true");
      sBuildRoot.setAttribute("data-playmat-enabled", "true");
      sharedBody.appendChild(sBuildRoot);
      var sMeta = sharedDoc.createElement("meta");
      sMeta.setAttribute("name", "csrf-token");
      sMeta.setAttribute("content", "token");
      sharedHead.appendChild(sMeta);
      var sSaveBtn = sharedDoc.createElement("button");
      sSaveBtn.id = "save-state";
      sSaveBtn.textContent = "Saved";
      sharedBody.appendChild(sSaveBtn);
      var sToolbar = sharedDoc.createElement("div");
      sToolbar.className = "dl-builder-toolbar";
      sharedBody.appendChild(sToolbar);
      var sTableView = sharedDoc.createElement("div");
      sTableView.id = "table-view";
      sharedBody.appendChild(sTableView);
      var sRail = sharedDoc.createElement("div");
      sRail.className = "dl-stats-rail";
      sharedBody.appendChild(sRail);
      var sStage = sharedDoc.createElement("div");
      sStage.id = "playmat-stage";
      var sPlaymat = sharedDoc.createElement("div");
      sPlaymat.id = "playmat";
      sStage.appendChild(sPlaymat);
      var sPlaymatView = sharedDoc.createElement("div");
      sPlaymatView.id = "playmat-view";
      sPlaymatView.appendChild(sStage);
      sharedBody.appendChild(sPlaymatView);
      ["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach(function (id) {
        var sEl = sharedDoc.createElement(id === "zoom-label" ? "span" : "div");
        sEl.id = id;
        sharedBody.appendChild(sEl);
      });
      var sPlaymatSel = sharedDoc.createElement("div");
      sPlaymatSel.setAttribute("data-playmat-selection", "");
      sharedBody.appendChild(sPlaymatSel);
      var sTagList = sharedDoc.createElement("div");
      sTagList.setAttribute("data-deck-tag-list", "");
      sharedBody.appendChild(sTagList);
      var sTagSummary = sharedDoc.createElement("div");
      sTagSummary.setAttribute("data-tag-summary", "");
      sharedBody.appendChild(sTagSummary);
      var sTagOptions = sharedDoc.createElement("div");
      sTagOptions.setAttribute("data-tag-options", "");
      sharedBody.appendChild(sTagOptions);
      var sDialog = sharedDoc.createElement("dialog");
      sDialog.id = "card-image-dialog";
      sharedBody.appendChild(sDialog);

      let sharedFetches = 0;
      let sharedApi = null;
      const sWindowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-2" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
      vm.runInContext(source, vm.createContext({
        console, document: sharedDoc, window: sWindowObj, setTimeout, clearTimeout,
        localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
        fetch() { sharedFetches++; return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(sDeck)) }); },
        AbortController, URL, URLSearchParams,
        location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
        navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error, RegExp,
        CustomEvent: ctor,
        crypto: sWindowObj.crypto,
      }), { filename: builderPath });
      await flush();
      sharedApi = sWindowObj.DeckLabBuilder;
      var cmdResult = sharedApi.command([{ type: "move_entry", entry_id: "entry-ring", zone_id: "zone-ramp", sort_order: 999 }]);
      var isPromise = cmdResult && typeof cmdResult.then === "function";
      var resolved = false;
      if (isPromise) cmdResult.then(function () { resolved = true; });
      await flush();
      return { shared: true, commandPromise: isPromise, resolved: resolved, fetchCalls: sharedFetches };
    }
    case "railsection": {
      const first = api.railSection("x", "Filter Cards");
      const second = api.railSection("x", "Filter Cards");
      const sections = document.querySelectorAll('[data-ext-section="x"]');
      const heading = first ? first.querySelector("h2") : null;
      return { same: first === second, count: sections.length, headingText: heading ? heading.textContent : null };
    }
    case "entryhaspresent": {
      // Different display modes
      const textEntries = document.querySelectorAll(".dl-deck-row[data-entry-id]").length;
      const tv3 = document.getElementById("table-view");
      const allEntryElements = tv3 ? tv3.querySelectorAll("[data-entry-id]") : [];
      const commanderRows = document.querySelectorAll(".commander [data-entry-id]").length;

      return { totalEntryElements: allEntryElements.length, textRowCount: textEntries, commanderCount: commanderRows };
    }
    case "entryhover": {
      const hoverEvents = [];
      const listener2 = function (ev) { hoverEvents.push({ entryId: ev.detail ? ev.detail.entryId : null }); };
      document.addEventListener("deck-lab:entry-hover", listener2);
      const ringRow = document.querySelector('[data-entry-id="entry-ring"]');
      if (ringRow) {
        const child = ringRow.querySelector(".dl-card-name");
        if (child) {
          child.dispatchEvent(makeEvent("mouseover", { target: child, relatedTarget: null }));
        }
      }
      document.removeEventListener("deck-lab:entry-hover", listener2);
      return { hoverEvents: hoverEvents.length, firstId: hoverEvents.length > 0 ? hoverEvents[0].entryId : null };
    }
    case "focusentry": {
      const found = api.focusEntry("entry-ring");
      const notFound = api.focusEntry("entry-nonexistent");
      const withNumber = api.focusEntry(42);
      return { found, notFound, withNumber };
    }
    case "checkbox_selection": {
      const events = [];
      const listener = function (ev) { if (ev.type === "deck-lab:selection") events.push({ ids: ev.detail ? ev.detail.ids : null }); };
      document.addEventListener("deck-lab:selection", listener);
      // Click the first row checkbox (Sol Ring)
      const checkboxes = [...document.querySelectorAll(".dl-row-select")];
      if (checkboxes.length > 0) {
        checkboxes[0].checked = true;
        checkboxes[0].dispatchEvent(makeEvent("change", { target: checkboxes[0] }));
        await flush();
      }
      document.removeEventListener("deck-lab:selection", listener);
      return { eventCount: events.length, ids: events.length > 0 ? events[0].ids : null };
    }
    case "clear_selection_event": {
      // First select something
      const checkboxes2 = [...document.querySelectorAll(".dl-row-select")];
      if (checkboxes2.length > 0) {
        checkboxes2[0].checked = true;
        checkboxes2[0].dispatchEvent(makeEvent("change", { target: checkboxes2[0] }));
        await flush();
      }
      const events2 = [];
      const listener2 = function (ev) { if (ev.type === "deck-lab:selection") events2.push({ ids: ev.detail ? ev.detail.ids : null }); };
      document.addEventListener("deck-lab:selection", listener2);
      const clearBtn = document.querySelector("[data-clear-selection]");
      if (clearBtn) clearBtn.dispatchEvent(makeEvent("click", { target: clearBtn }));
      await flush();
      document.removeEventListener("deck-lab:selection", listener2);
      return { eventCount: events2.length, ids: events2.length > 0 ? events2[0].ids : null };
    }
    case "selection_unchanged_and_once": {
      const events3 = [];
      const listener3 = function (ev) { if (ev.type === "deck-lab:selection") events3.push({ ids: ev.detail ? ev.detail.ids : null }); };
      document.addEventListener("deck-lab:selection", listener3);
      // Select once via setSelection
      api.setSelection(["entry-ring", "entry-lotus"]);
      await flush();
      // Select the same set again — should NOT fire
      api.setSelection(["entry-ring", "entry-lotus"]);
      await flush();
      document.removeEventListener("deck-lab:selection", listener3);
      return { eventCount: events3.length, firstIds: events3.length > 0 ? events3[0].ids : null, lastIds: events3.length > 1 ? events3[1].ids : null };
    }
    case "filter_chip_no_label": {
      // Clear any existing filter
      api.setEntryFilter(null);
      await flush();
      api.setEntryFilter(function (e) { return e.id === "entry-ring"; });
      await flush();
      const chip2 = document.querySelector("[data-entry-filter-chip]");
      const chipText2 = chip2 ? chip2.textContent.replace(/×/g, "").trim() : null;
      // Clear it
      api.setEntryFilter(null);
      return { chipText: chipText2 };
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
