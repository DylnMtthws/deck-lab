import fs from "node:fs";
import vm from "node:vm";

const scenario = process.argv[2];
const iconsPath = process.argv[3];
const builderPath = process.argv[4];
const consideringPath = process.argv[5];

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
    this._measure = false;
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
  set innerHTML(value) { this._text = String(value); this.children.length = 0; }
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
  appendChild(child) {
    if (child == null) return child;
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(newChild, refChild) {
    newChild.parentNode = this;
    const index = refChild ? this.children.indexOf(refChild) : -1;
    if (index >= 0) this.children.splice(index, 0, newChild);
    else this.children.push(newChild);
    return newChild;
  }
  append(...nodes) {
    nodes.forEach((n) => this.appendChild(typeof n === "string" ? Object.assign(this.ownerDocument.createElement("#text"), { textContent: n }) : n));
  }
  replaceChildren(...nodes) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children.length = 0;
    this._text = "";
    nodes.forEach((n) => this.appendChild(n));
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) { this.children.splice(i, 1); child.parentNode = null; }
    return child;
  }
  contains(other) { return other === this || this.children.some((c) => c.contains && c.contains(other)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((h) => h !== fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event));
    return !event.defaultPrevented;
  }
  setPointerCapture() {}
  showModal() { this.open = true; }
  focus() { document.activeElement = this; }
  scrollIntoView() {}
  getBoundingClientRect() {
    if (this.id === "playmat" || this.classList.contains("dl-mat-zone")) {
      const mat = this.id === "playmat" ? this : this.closest("#playmat");
      if (mat && mat._measure) return measuredRect(this, mat);
      if (this.id === "playmat") return { left: 0, top: 0, width: 0, height: 0 };
    }
    const index = Number((this.style._props && this.style._props["--card-index"]) || 0);
    if (this.classList.contains("dl-mat-card")) {
      const fan = this.closest(".fan");
      if (fan) return { left: 80 + index * 3, top: 160 + index * 3, width: 132, height: 184 };
      return { left: 20 + index * 139, top: 40, width: 132, height: 184 };
    }
    return { left: 0, top: 0, width: this.offsetWidth, height: this.offsetHeight };
  }
}

function readPx(value, fallback) {
  if (value == null || value === "") return fallback;
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : fallback;
}
function transformOf(mat) {
  const text = String((mat.style && mat.style.transform) || "");
  const translate = text.match(/translate\(\s*(-?[\d.]+)px\s*,\s*(-?[\d.]+)px\s*\)/);
  const scale = text.match(/scale\(\s*(-?[\d.]+)\s*\)/);
  return {
    panX: translate ? Number(translate[1]) : 0,
    panY: translate ? Number(translate[2]) : 0,
    zoom: scale && Number(scale[1]) ? Number(scale[1]) : 1,
  };
}
function measuredRect(el, mat) {
  const t = transformOf(mat);
  if (el.id === "playmat") {
    const width = readPx(el.style.width, el.offsetWidth);
    const height = readPx(el.style.height, el.offsetHeight);
    return { left: t.panX, top: t.panY, width: width * t.zoom, height: height * t.zoom };
  }
  const left = readPx(el.style.left, 0);
  const top = readPx(el.style.top, 0);
  const width = readPx(el.style.width, el.offsetWidth);
  const height = readPx(el.style.height, el.offsetHeight);
  return {
    left: t.panX + left * t.zoom,
    top: t.panY + top * t.zoom,
    width: width * t.zoom,
    height: height * t.zoom,
  };
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
function walk(el, acc) { acc.push(el); (el.children || []).forEach((c) => walk(c, acc)); return acc; }
function queryAll(root, selector) {
  const all = []; (root.children || []).forEach((c) => walk(c, all));
  const parts = tokenize(selector);
  return all.filter((el) => parts.some((part) => simpleMatch(el, part.trim().split(/\s+/).pop())));
}

class DataTransferMock {
  constructor() { this._data = {}; this.types = []; this.effectAllowed = "uninitialized"; this.dropEffect = "none"; }
  setData(type, value) { this._data[type] = String(value); if (!this.types.includes(type)) this.types.push(type); }
  getData(type) { return this._data[type] || ""; }
  setDragImage() {}
}
function makeEvent(type, props = {}) {
  return { type, ...props, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
}

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head);
documentElement.appendChild(body);
const document = {
  documentElement, body, head,
  activeElement: body,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
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

function entry(id, name, zoneId, sort) {
  return {
    id, name, is_commander: false, quantity: 1, zone_id: zoneId, sort_order: sort,
    type_line: "Instant", mana_cost: "{U}", mana_value: 1, oracle_text: "",
    color_identity: [], image_uri: "https://img.test/" + id + ".jpg", role: "",
  };
}

const decks = {
  header: {
    zones: [
      { id: "zone-lands", name: "Lands", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0 },
      { id: "zone-stack", name: "Stack", x: 480, y: 18, width: 220, layout_mode: "fan", sort_order: 1 },
    ],
    entries: [
      { id: "cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "https://img.test/kinnan.jpg", role: "" },
      entry("forest", "Forest", "zone-lands", 0),
      entry("island", "Island", "zone-lands", 1),
      entry("s0", "Swan Song", "zone-stack", 0),
    ],
  },
  stack: {
    zones: [{ id: "zone-stack", name: "Stack", x: 40, y: 18, width: 240, layout_mode: "fan", sort_order: 0 }],
    entries: Array.from({ length: 10 }, (_, i) => entry("c" + i, "Card " + i, "zone-stack", i)),
  },
  peek: {
    zones: [{ id: "zone-stack", name: "Stack", x: 40, y: 18, width: 240, layout_mode: "fan", sort_order: 0 }],
    entries: Array.from({ length: 8 }, (_, i) => entry("p" + i, "Peek " + i, "zone-stack", i)),
  },
  drag: {
    zones: [
      { id: "zone-stack", name: "Stack", x: 40, y: 18, width: 240, layout_mode: "fan", sort_order: 0 },
      { id: "zone-lands", name: "Lands", x: 400, y: 18, width: 400, layout_mode: "spread", sort_order: 1 },
    ],
    entries: Array.from({ length: 5 }, (_, i) => entry("d" + i, "Drag " + i, "zone-stack", i)),
  },
  flip: {
    zones: [{ id: "zone-main", name: "Main", x: 40, y: 18, width: 500, layout_mode: "spread", sort_order: 0 }],
    entries: [entry("a", "Alpha", "zone-main", 0), entry("b", "Beta", "zone-main", 1), entry("c", "Gamma", "zone-main", 2)],
  },
  considering: {
    zones: [{ id: "zone-main", name: "Unsorted", x: 80, y: 120, layout_mode: "spread", sort_order: 0 }],
    entries: [entry("ring", "Sol Ring", "zone-main", 0)],
  },
  "new-zone": {
    zones: [{ id: "zone-wide", name: "Unsorted", x: 18, y: 18, layout_mode: "spread", sort_order: 0 }],
    entries: [
      { id: "cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "https://img.test/kinnan.jpg", role: "" },
      ...Array.from({ length: 5 }, (_, i) => entry("w" + i, "Wide " + i, "zone-wide", i)),
    ],
  },
  "considering-free": {
    zones: [{ id: "zone-wide", name: "Unsorted", x: 18, y: 18, layout_mode: "spread", sort_order: 0 }],
    entries: Array.from({ length: 5 }, (_, i) => entry("c" + i, "Card " + i, "zone-wide", i)),
  },
};

const chosen = decks[scenario];
if (!chosen) {
  console.error("unknown scenario " + scenario);
  process.exit(1);
}
const measured = scenario === "new-zone" || scenario === "considering-free";
const deck = {
  id: "deck-1", title: "Test Deck", revision: 3,
  zones: chosen.zones, entries: chosen.entries,
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: measured ? 0.5 : 1, pan_x: measured ? 36 : 0, pan_y: measured ? 48 : 0, surface: "slate-grid", show_zone_outlines: true, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "playmat", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  tags: [], tag_suggestions: [], playmats: [],
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
body.appendChild(el("div", { id: "table-view" }));
const stage = el("div", { id: "playmat-stage" });
const playmat = el("div", { id: "playmat" });
if (measured) playmat._measure = true;
stage.appendChild(playmat);
const playmatView = el("div", { id: "playmat-view" });
playmatView.appendChild(stage);
body.appendChild(playmatView);
["zoom-label", "mana-curve", "color-stats", "zone-stats", "deck-count"].forEach((id) => body.appendChild(el("span", { id })));
body.appendChild(el("div", { "data-playmat-selection": "" }));
body.appendChild(el("input", { "data-card-search": "" }));
body.appendChild(el("div", { "data-card-results": "" }));
body.appendChild(el("span", { "data-search-scope": "" }));
body.appendChild(el("span", { "data-card-total": "" }));
body.appendChild(el("select", { "data-add-zone": "" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));
body.appendChild(el("div", { className: "dl-builder-toolbar" }));
if (scenario === "considering" || scenario === "considering-free") {
  const bulk = el("div", { "data-bulk-controls": "" });
  bulk.appendChild(el("button", { "data-clear-selection": "", text: "Clear" }));
  body.appendChild(bulk);
}
if (scenario === "new-zone") {
  const zoneDialog = el("dialog", { id: "zone-dialog" });
  zoneDialog.appendChild(el("h2", { text: "New zone" }));
  zoneDialog.appendChild(el("input", { "data-zone-name": "" }));
  zoneDialog.appendChild(el("output", { "data-zone-error": "" }));
  body.appendChild(zoneDialog);
}

const fetches = [];
const store = {};
const motion = { matches: false };
async function flush() { for (let i = 0; i < 16; i++) await new Promise((r) => setTimeout(r, 0)); }
function commandBodies() {
  return fetches.filter((f) => String(f.url).includes("/commands")).map((f) => JSON.parse(f.opts.body || "{}").commands);
}
function zone(id) { return document.querySelectorAll(".dl-mat-zone").find((z) => z.dataset.zoneId === id); }
function svgBox(node) {
  const svg = node && node.querySelector("svg");
  return svg ? svg.getAttribute("viewBox") : "";
}

const windowObj = {
  matchMedia(query) {
    if (String(query).includes("prefers-reduced-motion")) return motion;
    return { matches: false, addEventListener() {} };
  },
  setTimeout, clearTimeout,
  requestAnimationFrame(fn) { return setTimeout(fn, 0); },
  crypto: { randomUUID: () => "uuid-playmat" },
  DeckLabSelects: { refresh() {} },
  prompt() { return null; },
};
const context = vm.createContext({
  console, document, window: windowObj, setTimeout, clearTimeout,
  requestAnimationFrame: windowObj.requestAnimationFrame,
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  fetch(url, opts = {}) {
    fetches.push({ url: String(url), opts });
    if (String(url).includes("/commands")) {
      try {
        const packet = JSON.parse(opts.body || "{}");
        (packet.commands || []).forEach((cmd) => {
          if (cmd.type === "set_zone_layout") {
            const found = deck.zones.find((item) => item.id === cmd.zone_id);
            if (found && cmd.layout) found.layout_mode = cmd.layout;
          } else if (cmd.type === "move_entry") {
            const found = deck.entries.find((item) => item.id === cmd.entry_id);
            if (found && cmd.zone_id) found.zone_id = cmd.zone_id;
          }
        });
        deck.revision += 1;
      } catch (_) {}
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ results: [] }) });
  },
  AbortController, URL, URLSearchParams,
  location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
  navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
  crypto: windowObj.crypto,
});

vm.runInContext(fs.readFileSync(iconsPath, "utf8"), context, { filename: iconsPath });
vm.runInContext(fs.readFileSync(builderPath, "utf8"), context, { filename: builderPath });
if ((scenario === "considering" || scenario === "considering-free") && consideringPath) {
  vm.runInContext(fs.readFileSync(consideringPath, "utf8"), context, { filename: consideringPath });
}
await flush();

function buttonInfo(node) {
  const svg = node && node.querySelector("svg");
  return {
    label: node.getAttribute("aria-label"),
    tip: node.getAttribute("data-dl-tip"),
    viewBox: svgBox(node),
    pressed: node.getAttribute("aria-pressed"),
    className: node.className,
    markup: svg ? svg.innerHTML : "",
  };
}

let result;
if (scenario === "header") {
  const lands = zone("zone-lands");
  const stack = zone("zone-stack");
  const bar = lands.querySelector(".dl-mat-zone-bar");
  const title = bar.querySelector("h2");
  const count = bar.querySelector(".dl-mat-count");
  result = {
    title: title.textContent,
    countClass: count.className,
    countText: count.textContent,
    grid: buttonInfo(lands.querySelector(".dl-zone-grid-toggle")),
    stackToggle: buttonInfo(lands.querySelector(".dl-zone-layout-toggle")),
    more: buttonInfo(lands.querySelector(".dl-zone-menu summary")),
    spreadToggle: buttonInfo(stack.querySelector(".dl-zone-layout-toggle")),
    menuItems: [...lands.querySelectorAll(".dl-zone-menu-popover button")].map((b) => b.textContent),
  };
} else if (scenario === "stack") {
  const cards = [...zone("zone-stack").querySelectorAll(".dl-mat-card")];
  const badge = zone("zone-stack").querySelector(".dl-stack-more");
  result = {
    count: cards.length,
    offsets: cards.map((card) => ({ x: card.style._props["--stack-x"], y: card.style._props["--stack-y"], peek: card.getAttribute("data-peek-index") })),
    badge: badge ? badge.textContent : "",
    badgeLabel: badge ? badge.getAttribute("aria-label") : "",
  };
} else if (scenario === "peek") {
  const box = zone("zone-stack");
  const cards = box.querySelector(".dl-mat-cards");
  const before = fetches.length;
  cards.dispatchEvent(makeEvent("pointerenter", { target: cards }));
  const immediate = cards.classList.contains("is-peek");
  await new Promise((r) => setTimeout(r, 200));
  const early = cards.classList.contains("is-peek");
  await new Promise((r) => setTimeout(r, 160));
  const opened = cards.classList.contains("is-peek");
  const peekedIndexes = [...cards.querySelectorAll(".dl-mat-card")].map((card) => card.getAttribute("data-peek-index"));
  const layout = deck.zones[0].layout_mode;
  cards.dispatchEvent(makeEvent("pointerleave", { target: cards }));
  const closed = cards.classList.contains("is-peek");
  box.dispatchEvent(makeEvent("focusin", { target: box }));
  const focused = cards.classList.contains("is-peek");
  result = {
    immediate, early, opened, closed, focused,
    peekedIndexes,
    layout,
    fetches: fetches.length - before,
    commands: commandBodies(),
  };
} else if (scenario === "drag") {
  const box = zone("zone-stack");
  const cards = box.querySelector(".dl-mat-cards");
  const card = [...cards.querySelectorAll(".dl-mat-card")].find((node) => node.getAttribute("data-peek-index") === "2");
  box.dispatchEvent(makeEvent("focusin", { target: box }));
  const beforeFetches = fetches.length;
  const dt = new DataTransferMock();
  card.dispatchEvent(makeEvent("dragstart", { dataTransfer: dt, clientX: 20, clientY: 20, target: card }));
  const during = { dragSource: card.classList.contains("drag-source"), peek: cards.classList.contains("is-peek"), preview: !!document.querySelector(".dl-drag-preview") };
  const lands = zone("zone-lands");
  lands.dispatchEvent(makeEvent("dragover", { dataTransfer: dt, clientX: 80, clientY: 40, target: lands }));
  const over = { dropTarget: lands.classList.contains("drop-target"), dropEffect: dt.dropEffect };
  lands.dispatchEvent(makeEvent("drop", { dataTransfer: dt, clientX: 80, clientY: 40, target: lands }));
  card.dispatchEvent(makeEvent("dragend", { dataTransfer: dt, target: card }));
  await flush();
  result = { during, over, commands: commandBodies(), fetchesBeforeDrop: beforeFetches, entryId: card.dataset.entryId };
} else if (scenario === "flip") {
  const button = () => zone("zone-main").querySelector(".dl-zone-layout-toggle");
  button().dispatchEvent(makeEvent("click", { target: button(), button: 0 }));
  await flush();
  const animated = [...zone("zone-main").querySelectorAll(".dl-mat-card")].map((card) => ({
    flip: card.classList.contains("dl-flip"),
    run: card.classList.contains("dl-flip-run"),
    dataFlip: card.getAttribute("data-flip"),
  }));
  motion.matches = true;
  const again = zone("zone-main").querySelector(".dl-zone-layout-toggle");
  again.dispatchEvent(makeEvent("click", { target: again, button: 0 }));
  await flush();
  const reduced = [...zone("zone-main").querySelectorAll(".dl-mat-card")].map((card) => ({
    flip: card.classList.contains("dl-flip"),
    run: card.classList.contains("dl-flip-run"),
  }));
  result = { commands: commandBodies(), animated, reduced, layout: deck.zones[0].layout_mode };
} else if (scenario === "considering") {
  const api = context.window.DeckLabBuilder;
  api.setSelection(["ring"]);
  const button = document.querySelector("[data-move-considering]");
  button.dispatchEvent(makeEvent("click", { target: button, button: 0 }));
  await flush();
  const commands = commandBodies().flat();
  result = { create: commands.find((command) => command.type === "create_zone") || null };
} else if (scenario === "new-zone" || scenario === "considering-free") {
  const api = context.window.DeckLabBuilder;
  const spot = api.freeZonePosition(360, 240);
  const mat = document.getElementById("playmat").getBoundingClientRect();
  const zones = [...document.querySelectorAll(".dl-mat-zone")].map((node) => node.getBoundingClientRect());
  if (scenario === "new-zone") {
    const zoneDialog = document.getElementById("zone-dialog");
    zoneDialog.querySelector("[data-zone-name]").value = "Ramp";
    zoneDialog.returnValue = "save";
    zoneDialog.dispatchEvent(makeEvent("close", { target: zoneDialog }));
  } else {
    api.setSelection(["c0"]);
    const button = document.querySelector("[data-move-considering]");
    button.dispatchEvent(makeEvent("click", { target: button, button: 0 }));
  }
  await flush();
  const commands = commandBodies().flat();
  result = {
    create: commands.find((command) => command.type === "create_zone") || null,
    spot,
    mat,
    zoom: deck.presentation.zoom,
    zones,
  };
}

console.log(JSON.stringify(result));
