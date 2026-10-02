import fs from "node:fs";
import vm from "node:vm";

const panelPath = process.argv[2];
const builderPath = process.argv[3];
const scenario = process.argv[4] || "initial";
const panelSource = fs.readFileSync(panelPath, "utf8");
const builderSource = fs.readFileSync(builderPath, "utf8");

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
    this.loading = "";
    this.decoding = "";
    this.open = false;
    this.returnValue = "";
    this.clientWidth = 800;
    this.clientHeight = 600;
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
    if (key === "alt") this.alt = String(value);
    if (key === "src") this.src = String(value);
    if (key === "loading") this.loading = String(value);
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
    if (typeof child === "string") {
      const text = this.ownerDocument.createTextNode(child);
      child = text;
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
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) { this.children.splice(idx, 1); child.parentNode = null; }
    return child;
  }
  closest(selector) {
    let node = this;
    while (node && node.tagName) {
      if (matches(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === this.ownerDocument.documentElement;
  }
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null; }
  click() {
    this.dispatchEvent({
      type: "click",
      bubbles: true,
      cancelable: true,
      metaKey: false,
      ctrlKey: false,
      shiftKey: false,
      altKey: false,
      button: 0,
      preventDefault() {},
      stopPropagation() { this._stop = true; },
    });
  }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn(event));
    if (event.bubbles !== false && this.parentNode && !event._stop) this.parentNode.dispatchEvent(event);
    return true;
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; }
  scrollIntoView() {}
  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentNode;
    }
    return false;
  }
}

function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    if (part === "*") return true;
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
  return nodes;
}

function createDocument() {
  const documentElement = new Element("html");
  const head = new Element("head", null);
  const body = new Element("body", null);
  const document = {
    documentElement, body, head, activeElement: null,
    createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
    createTextNode(text) { const el = document.createElement("#text"); el.textContent = String(text == null ? "" : text); return el; },
    createElementNS(_ns, tag) { return document.createElement(tag); },
    getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
    querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
    querySelectorAll(sel) { return queryAll(documentElement, sel); },
    addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
    removeEventListener(type, fn) {
      const list = documentElement.listeners[type];
      if (!list) return;
      const idx = list.indexOf(fn);
      if (idx >= 0) list.splice(idx, 1);
    },
    dispatchEvent(event) { return documentElement.dispatchEvent(event); },
  };
  head.ownerDocument = document;
  body.ownerDocument = document;
  documentElement.ownerDocument = document;
  documentElement.appendChild(head);
  documentElement.appendChild(body);
  return document;
}

function el(document, tag, attrs = {}) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "id") { node.id = value; node.setAttribute("id", value); }
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  return node;
}

function card(partial) {
  return Object.assign({
    id: "entry-card",
    name: "Card",
    is_commander: false,
    quantity: 1,
    zone_id: "zone-main",
    sort_order: 0,
    type_line: "Artifact",
    mana_cost: "{1}",
    mana_value: 1,
    oracle_text: "",
    color_identity: [],
    image_uri: "",
    role: "",
    format_legal: true,
    commander_legal: true,
    validation_issues: [],
  }, partial);
}

const COMMANDER = card({
  id: "entry-cmd",
  name: "Kinnan Test",
  is_commander: true,
  zone_id: null,
  type_line: "Legendary Creature — Human Druid",
  mana_cost: "{G}{U}",
  mana_value: 2,
  oracle_text: "Tap: add mana.",
  color_identity: ["G", "U"],
  image_uri: "https://cards.test/kinnan.png",
  role: "",
});
const RING = card({
  id: "entry-ring",
  name: "Sol Ring",
  quantity: 2,
  zone_id: "zone-ramp",
  sort_order: 0,
  type_line: "Artifact",
  mana_cost: "{1}",
  oracle_text: "Add {C}{C}.",
  image_uri: "https://cards.test/sol-ring.png",
  role: "ramp",
});
const LOTUS = card({
  id: "entry-lotus",
  name: "Lotus Petal",
  zone_id: "zone-ramp",
  sort_order: 1,
  type_line: "Artifact",
  mana_cost: "{0}",
  oracle_text: "Sacrifice {T}.",
  image_uri: "https://cards.test/lotus-petal.png",
  role: "free",
});
const ISLAND = card({
  id: "entry-island",
  name: "Island",
  zone_id: "zone-main",
  sort_order: 2,
  type_line: "Basic Land — Island",
  mana_cost: "",
  mana_value: 0,
  oracle_text: "",
  image_uri: "",
  role: "land",
});

function makeState(entries, extra = {}) {
  return Object.assign({
    id: "deck-1",
    title: "Panel Deck",
    revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-ramp", name: "Ramp", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries,
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: entries.length, library_target: 99, total_count: entries.length, legal: false, issues: [], entry_issues: {} },
    tags: [],
    tag_suggestions: [],
  }, extra);
}

function install(options = {}) {
  const document = createDocument();
  const preloads = [];
  const timers = [];
  let clock = 0;
  let seq = 1;
  const media = { narrow: !!options.narrow };
  const state = options.state || makeState([ISLAND, COMMANDER, RING, LOTUS]);
  const modalCalls = [];

  const root = el(document, "div", { className: options.collapsed ? "dl-builder right-collapsed" : "dl-builder", "data-shared": "false", "data-playmat-enabled": options.playmat ? "true" : "false" });
  document.body.appendChild(root);
  const rail = el(document, "aside", { className: "dl-stats-rail" });
  rail.appendChild(el(document, "div", { className: "dl-builder-rail-head" }));
  const panel = el(document, "aside", { className: "dl-card-panel", "data-card-panel": "", "aria-label": "Card preview", "aria-live": "polite" });
  rail.appendChild(panel);
  document.body.appendChild(rail);
  document.body.appendChild(Object.assign(el(document, "script", { id: "deck-document-data" }), { textContent: JSON.stringify(state) }));
  document.body.appendChild(el(document, "div", { id: "table-view" }));
  const playmatView = el(document, "div", { id: "playmat-view" });
  playmatView.appendChild(el(document, "div", { id: "playmat" }));
  document.body.appendChild(playmatView);
  const sentinel = el(document, "input", { id: "sentinel" });
  document.body.appendChild(sentinel);
  document.activeElement = sentinel;

  const imageDialog = el(document, "dialog", { id: "card-image-dialog", className: "dl-dialog dl-card-image-dialog" });
  imageDialog.showModal = function () { modalCalls.push(1); imageDialog.open = true; };
  imageDialog.close = function () { imageDialog.open = false; };
  const imageTitle = el(document, "h2", { id: "card-image-title", "data-card-image-title": "", text: "Card image" });
  const imageEl = el(document, "img", { "data-card-image": "", alt: "" });
  imageEl.hidden = true;
  const imageStatus = el(document, "p", { "data-card-image-status": "", text: "Loading card image…" });
  const imageOriginal = el(document, "a", { "data-card-image-original": "", href: "#" });
  imageDialog.append(imageTitle, imageEl, imageStatus, imageOriginal);
  document.body.appendChild(imageDialog);

  function matchMedia(query) {
    return {
      media: String(query),
      matches: media.narrow && String(query).includes("767"),
      addEventListener() {},
      removeEventListener() {},
    };
  }
  function setTimeout(fn, ms) {
    const id = seq++;
    timers.push({ id, fn, at: clock + Number(ms || 0) });
    return id;
  }
  function clearTimeout(id) {
    const idx = timers.findIndex((timer) => timer.id === id);
    if (idx >= 0) timers.splice(idx, 1);
  }
  function advance(ms) {
    clock += ms;
    let guard = 0;
    while (guard < 20) {
      guard += 1;
      const due = timers.filter((timer) => timer.at <= clock).sort((a, b) => a.at - b.at || a.id - b.id);
      if (!due.length) break;
      const next = due[0];
      const idx = timers.indexOf(next);
      if (idx >= 0) timers.splice(idx, 1);
      next.fn();
    }
  }
  function Image() {
    const img = document.createElement("img");
    preloads.push(img);
    return img;
  }
  class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail || null;
      this.bubbles = !!init.bubbles;
    }
    preventDefault() {}
    stopPropagation() {}
  }
  const windowObj = {
    matchMedia,
    Image,
    setTimeout,
    clearTimeout,
    crypto: { randomUUID: () => "uuid-1" },
    prompt() { return null; },
    DeckLabSelects: { refresh() {} },
  };
  if (options.mana) {
    windowObj.DeckLabMana = {
      symbol(symbol) {
        const node = document.createElement("img");
        node.className = "dl-ms";
        node.alt = "{" + symbol + "}";
        node.setAttribute("data-sym", symbol);
        return node;
      },
    };
  }
  if (!options.builder) {
    windowObj.DeckLabBuilder = {
      version: 1,
      shared: false,
      getState() { return state; },
    };
  }
  const context = {
    console,
    document,
    window: windowObj,
    setTimeout,
    clearTimeout,
    Image,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(state)) }); },
    AbortController,
    URL,
    URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {},
    Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt,
    Array, Object, String, Boolean, Error, RegExp,
    CustomEvent,
    crypto: windowObj.crypto,
  };
  const vmContext = vm.createContext(context);
  if (options.builder) vm.runInContext(builderSource, vmContext, { filename: builderPath });
  vm.runInContext(panelSource, vmContext, { filename: panelPath });

  function dispatch(type, entryId) {
    document.dispatchEvent({
      type,
      detail: { entryId },
      bubbles: false,
      preventDefault() {},
      stopPropagation() {},
    });
  }
  function emit(node, type) {
    (node.listeners[type] || []).slice().forEach((fn) => fn({ type, target: node, currentTarget: node }));
  }
  return { document, panel, preloads, advance, dispatch, emit, modalCalls, state, windowObj, media };
}

function nameOf(panel) {
  const node = panel.querySelector("[data-card-panel-name]");
  return node ? node.textContent : null;
}
function renders(panel) {
  const raw = panel.getAttribute("data-card-panel-render");
  return raw == null ? 0 : Number(raw);
}
function activeId(document) {
  return document.activeElement ? document.activeElement.id : null;
}

function runInitial() {
  const shown = install();
  const empty = install({ state: makeState([]) });
  return {
    shown: {
      name: nameOf(shown.panel),
      current: shown.windowObj.DeckLabCardPanel.current(),
      hint: shown.panel.textContent.includes("Hover or focus a card to preview it"),
      active: activeId(shown.document),
    },
    empty: {
      name: nameOf(empty.panel),
      current: empty.windowObj.DeckLabCardPanel.current(),
      hint: empty.panel.textContent,
      active: activeId(empty.document),
    },
  };
}

function runHover() {
  const page = install();
  const before = nameOf(page.panel);
  const active = activeId(page.document);
  page.dispatch("deck-lab:entry-hover", "entry-lotus");
  page.advance(59);
  const during = nameOf(page.panel);
  page.advance(1);
  const after = nameOf(page.panel);
  const afterRender = renders(page.panel);
  page.dispatch("deck-lab:entry-hover", "entry-lotus");
  page.advance(60);
  const sameRender = renders(page.panel);
  const interrupted = install();
  interrupted.dispatch("deck-lab:entry-hover", "entry-lotus");
  interrupted.advance(30);
  interrupted.dispatch("deck-lab:entry-hover", "entry-lotus");
  interrupted.advance(30);
  const stableSame = nameOf(interrupted.panel);
  const switched = install();
  switched.dispatch("deck-lab:entry-hover", "entry-lotus");
  switched.advance(40);
  switched.dispatch("deck-lab:entry-hover", "entry-ring");
  switched.advance(40);
  const midSwitch = nameOf(switched.panel);
  switched.advance(20);
  return {
    before,
    during,
    after,
    sameRender,
    afterRender,
    stableSame,
    midSwitch,
    switched: nameOf(switched.panel),
    active: activeId(page.document),
    activeStarted: active,
  };
}

function runFocus() {
  const page = install();
  page.dispatch("deck-lab:entry-hover", "entry-lotus");
  page.dispatch("deck-lab:focus-entry", "entry-ring");
  const immediate = nameOf(page.panel);
  const current = page.windowObj.DeckLabCardPanel.current();
  page.advance(80);
  return {
    immediate,
    current,
    afterWait: nameOf(page.panel),
    active: activeId(page.document),
  };
}

function fieldsOf(panel) {
  const img = panel.querySelector("img");
  return {
    name: nameOf(panel),
    cost: panel.querySelector("[data-card-panel-cost]").textContent,
    type: panel.querySelector("[data-card-panel-type]").textContent,
    oracle: panel.querySelector("[data-card-panel-oracle]").textContent,
    qty: panel.querySelector("[data-card-panel-qty]").textContent,
    zone: panel.querySelector("[data-card-panel-zone]").textContent,
    role: panel.querySelector("[data-card-panel-role]").textContent,
    alt: img ? img.alt : null,
    loading: img ? img.loading : null,
    src: img ? img.src : null,
    costSymbols: panel.querySelector("[data-card-panel-cost]").querySelectorAll("img.dl-ms").length,
    oracleSymbols: panel.querySelector("[data-card-panel-oracle]").querySelectorAll("img.dl-ms").length,
  };
}

function runFields() {
  const page = install();
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  const ring = fieldsOf(page.panel);
  page.windowObj.DeckLabCardPanel.show("entry-island");
  const island = fieldsOf(page.panel);
  return { ring, islandSrc: island.src };
}

function runFieldsSymbols() {
  const page = install({ mana: true });
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  return fieldsOf(page.panel);
}

function runIssues() {
  const state = makeState([COMMANDER, RING]);
  state.validation.entry_issues = {
    "entry-ring": [{ message: "Not legal in Commander" }, "Too many copies"],
  };
  const page = install({ state });
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  const list = page.panel.querySelector("[role=note]");
  return {
    role: list ? list.getAttribute("role") : null,
    items: list ? list.querySelectorAll("li").map((item) => item.textContent) : [],
  };
}

function runPin() {
  const page = install();
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  const pin = page.panel.querySelector("[data-card-panel-pin]");
  pin.dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  page.dispatch("deck-lab:entry-hover", "entry-lotus");
  page.advance(100);
  page.dispatch("deck-lab:focus-entry", "entry-lotus");
  const frozen = nameOf(page.panel);
  const pressed = pin.getAttribute("aria-pressed");
  const currentFrozen = page.windowObj.DeckLabCardPanel.current();
  pin.dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  page.dispatch("deck-lab:focus-entry", "entry-lotus");
  return {
    pressed,
    frozen,
    currentFrozen,
    pressedAfter: pin.getAttribute("aria-pressed"),
    after: nameOf(page.panel),
    label: pin.getAttribute("aria-label"),
  };
}

function runImageError() {
  const page = install();
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  const frame = page.panel.querySelector(".dl-card-panel-frame");
  const img = page.panel.querySelector("img");
  const before = {
    alt: img.alt,
    loading: img.loading,
    skeleton: !!page.panel.querySelector("[data-card-panel-skeleton]"),
    loadingClass: frame.classList.contains("is-loading"),
  };
  page.emit(img, "error");
  const fallback = page.panel.querySelector(".dl-card-panel-fallback");
  return {
    before,
    imgLeft: !!page.panel.querySelector("img"),
    fallback: fallback ? fallback.textContent : null,
    loadingClass: frame.classList.contains("is-loading"),
    errorClass: frame.classList.contains("is-error"),
  };
}

function inactiveSnapshot(page) {
  page.dispatch("deck-lab:entry-hover", "entry-ring");
  page.dispatch("deck-lab:focus-entry", "entry-lotus");
  page.advance(100);
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  return {
    children: page.panel.children.length,
    text: page.panel.textContent,
    preloads: page.preloads.map((img) => img.src),
    current: page.windowObj.DeckLabCardPanel.current(),
  };
}

function runInactive() {
  const collapsed = inactiveSnapshot(install({ collapsed: true }));
  const narrow = inactiveSnapshot(install({ narrow: true }));
  const live = install();
  const name = nameOf(live.panel);
  live.document.querySelector(".dl-builder").classList.add("right-collapsed");
  live.dispatch("deck-lab:entry-hover", "entry-ring");
  live.advance(100);
  live.media.narrow = true;
  live.document.querySelector(".dl-builder").classList.remove("right-collapsed");
  live.dispatch("deck-lab:entry-hover", "entry-lotus");
  live.advance(100);
  return {
    collapsed,
    narrow,
    live: { name: nameOf(live.panel), started: name, preloads: live.preloads.map((img) => img.src) },
  };
}

function runEvidence() {
  const page = install();
  const slot = page.panel.querySelector('[data-card-panel-slot="evidence"]');
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  const again = page.panel.querySelector('[data-card-panel-slot="evidence"]');
  return {
    exists: !!slot,
    sameNode: slot === again,
    children: again ? again.children.length : -1,
    text: again ? again.textContent : null,
  };
}

function runDialog() {
  const page = install({ builder: true, state: makeState([COMMANDER, RING, LOTUS]) });
  const before = page.modalCalls.length;
  const button = page.panel.querySelector(".dl-card-panel-image-btn");
  button.click();
  const dialog = page.document.getElementById("card-image-dialog");
  const img = dialog.querySelector("[data-card-image]");
  return {
    before,
    after: page.modalCalls.length,
    title: dialog.querySelector("[data-card-image-title]").textContent,
    src: img.src,
    alt: img.alt,
    panelSrc: page.panel.querySelector("img").src,
    active: activeId(page.document),
  };
}

function runPrefetch() {
  const page = install();
  const before = page.preloads.map((img) => img.src);
  page.dispatch("deck-lab:entry-hover", "entry-ring");
  const one = page.preloads.map((img) => img.src);
  page.dispatch("deck-lab:entry-hover", "entry-ring");
  const repeat = page.preloads.map((img) => img.src);
  page.dispatch("deck-lab:entry-hover", "entry-lotus");
  const two = page.preloads.map((img) => img.src);
  return {
    before,
    one,
    repeat,
    two,
    island: "https://api.scryfall.com/cards/named?format=image&version=normal&exact=Island",
    ring: "https://cards.test/sol-ring.png",
    lotus: "https://cards.test/lotus-petal.png",
    commander: "https://cards.test/kinnan.png",
  };
}

const THRASIOS = card({
  id: "entry-thrasios",
  name: "Thrasios, Triton Hero",
  is_commander: true,
  zone_id: null,
  type_line: "Legendary Creature — Merfolk Wizard",
  mana_cost: "{G}{U}",
  mana_value: 2,
  oracle_text: "{4}: Draw a card. Partner",
  image_uri: "https://cards.test/thrasios.png",
});
const TYMN = card({
  id: "entry-tymna",
  name: "Tymna the Weaver",
  is_commander: true,
  zone_id: null,
  type_line: "Legendary Creature — Human Cleric",
  mana_cost: "{1}{W}{B}",
  mana_value: 3,
  oracle_text: "Partner",
  image_uri: "https://cards.test/tymna.png",
});

function detailName(panel) {
  const node = panel.querySelector("h3[data-card-panel-name]");
  return node ? node.textContent : null;
}
function pairButtons(panel) {
  const strip = panel.querySelector("[data-card-panel-pair]");
  if (!strip) return [];
  return strip.querySelectorAll("button").map((button) => ({
    label: button.getAttribute("aria-label"),
    pressed: button.getAttribute("aria-pressed"),
    name: button.querySelector(".dl-card-panel-pair-name") ? button.querySelector(".dl-card-panel-pair-name").textContent : null,
    id: button.getAttribute("data-pair-entry"),
  }));
}
function bubble(node, type) {
  node.dispatchEvent({
    type,
    bubbles: true,
    target: node,
    preventDefault() {},
    stopPropagation() { this._stop = true; },
  });
}
function row(document, id) {
  return document.querySelectorAll('[data-entry-id="' + id + '"]').find((node) => node.closest("#table-view")) || null;
}
function matCard(document, id) {
  return document.querySelectorAll('[data-entry-id="' + id + '"]').find((node) => node.classList.contains("dl-mat-card")) || null;
}
function builderState(page) {
  return page.windowObj.DeckLabBuilder.getState();
}
function switchView(page, mode) {
  builderState(page).preferences.view_mode = mode;
  page.windowObj.DeckLabBuilder.render();
}

function runPlaymatHover() {
  const page = install({ builder: true, playmat: true });
  const lotus = matCard(page.document, "entry-lotus");
  const commander = matCard(page.document, "entry-cmd");
  bubble(lotus, "mouseover");
  page.advance(59);
  const during = detailName(page.panel);
  page.advance(1);
  const after = detailName(page.panel);
  const ring = matCard(page.document, "entry-ring");
  bubble(ring, "focusin");
  page.advance(59);
  const focusDuring = detailName(page.panel);
  page.advance(1);
  return {
    foundLotus: !!lotus,
    lotusClass: lotus ? lotus.className : null,
    inPlaymat: !!(lotus && lotus.closest("#playmat-view")),
    during,
    after,
    focusDuring,
    focused: detailName(page.panel),
    commanderFound: !!commander,
    commanderZone: commander ? !!(commander.closest && commander.closest(".dl-mat-command")) : false,
  };
}

function runCommanderMatHover() {
  const state = makeState([THRASIOS, TYMN, RING]);
  const page = install({ builder: true, playmat: true, state });
  const second = matCard(page.document, "entry-tymna");
  bubble(second, "mouseover");
  page.advance(60);
  return {
    found: !!second,
    zone: second && second.closest(".dl-mat-command") ? "command" : null,
    name: detailName(page.panel),
    pair: !!page.panel.querySelector("[data-card-panel-pair]"),
  };
}

function runViewSwitch() {
  const page = install({ builder: true, playmat: true });
  page.dispatch("deck-lab:entry-hover", "entry-lotus");
  page.advance(60);
  const hovered = detailName(page.panel);
  builderState(page).preferences.display_mode = "grid";
  page.windowObj.DeckLabBuilder.render();
  const afterDisplay = detailName(page.panel);
  switchView(page, "playmat");
  const reset = detailName(page.panel);
  const hint = page.panel.textContent.includes("Hover or focus a card to preview it");
  page.windowObj.DeckLabCardPanel.show("entry-ring");
  const pin = page.panel.querySelector("[data-card-panel-pin]");
  pin.dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  switchView(page, "table");
  const pinnedName = detailName(page.panel);
  const pinnedPressed = pin.getAttribute("aria-pressed");
  return { hovered, afterDisplay, reset, hint, pinnedName, pinnedPressed, frame: !!page.panel.querySelector(".dl-card-panel-frame") };
}

function runRehover() {
  const page = install({ builder: true, playmat: true });
  const events = [];
  page.document.addEventListener("deck-lab:entry-hover", (event) => {
    events.push(event.detail && event.detail.entryId);
  });
  const first = row(page.document, "entry-lotus");
  bubble(first, "mouseover");
  bubble(row(page.document, "entry-lotus"), "mouseover");
  const beforeSwitch = events.slice();
  const beforeAdvance = detailName(page.panel);
  switchView(page, "playmat");
  page.advance(80);
  const afterSwitch = detailName(page.panel);
  bubble(row(page.document, "entry-lotus"), "mouseover");
  const afterDispatch = events.slice();
  page.advance(59);
  const during = detailName(page.panel);
  page.advance(1);
  return {
    beforeSwitch,
    beforeAdvance,
    afterSwitch,
    afterDispatch,
    during,
    after: detailName(page.panel),
  };
}

function runPartners() {
  const state = makeState([THRASIOS, TYMN, RING]);
  const page = install({ builder: true, playmat: true, state });
  const initial = {
    name: detailName(page.panel),
    buttons: pairButtons(page.panel),
    frame: !!page.panel.querySelector(".dl-card-panel-frame"),
    type: page.panel.querySelector("[data-card-panel-type]").textContent,
    oracle: page.panel.querySelector("[data-card-panel-oracle]").textContent,
  };
  const inactive = page.panel.querySelector('[data-pair-entry="entry-tymna"]');
  inactive.dispatchEvent({ type: "mouseenter", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const preview = {
    name: detailName(page.panel),
    buttons: pairButtons(page.panel),
    frame: !!page.panel.querySelector(".dl-card-panel-frame"),
  };
  inactive.click();
  const clicked = {
    name: detailName(page.panel),
    buttons: pairButtons(page.panel),
    current: page.windowObj.DeckLabCardPanel.current(),
  };
  const first = page.panel.querySelector('[data-pair-entry="entry-thrasios"]');
  first.dispatchEvent({ type: "mouseenter", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const previewBack = detailName(page.panel);
  const pressedWhilePreview = pairButtons(page.panel).map((button) => button.pressed);
  const pin = page.panel.querySelector("[data-card-panel-pin]");
  pin.dispatchEvent({ type: "click", bubbles: true, preventDefault() {}, stopPropagation() {} });
  const pinned = {
    name: detailName(page.panel),
    current: page.windowObj.DeckLabCardPanel.current(),
    pressed: pin.getAttribute("aria-pressed"),
  };
  page.dispatch("deck-lab:entry-hover", "entry-thrasios");
  page.advance(80);
  pinned.afterHover = detailName(page.panel);
  return { initial, preview, clicked, previewBack, pressedWhilePreview, pinned };
}

function runUnchanged() {
  const single = install({ builder: true, playmat: true });
  single.windowObj.DeckLabCardPanel.show("entry-ring");
  const partnerState = makeState([THRASIOS, TYMN, RING]);
  const partner = install({ builder: true, playmat: true, state: partnerState });
  partner.windowObj.DeckLabCardPanel.show("entry-ring");
  function snap(panel) {
    return {
      name: detailName(panel),
      frame: !!panel.querySelector(".dl-card-panel-frame"),
      pair: !!panel.querySelector("[data-card-panel-pair]"),
      cost: panel.querySelector("[data-card-panel-cost]").textContent,
      type: panel.querySelector("[data-card-panel-type]").textContent,
      oracle: panel.querySelector("[data-card-panel-oracle]").textContent,
      qty: panel.querySelector("[data-card-panel-qty]").textContent,
      zone: panel.querySelector("[data-card-panel-zone]").textContent,
      role: panel.querySelector("[data-card-panel-role]").textContent,
    };
  }
  const commander = install();
  return {
    singleCommander: {
      name: detailName(commander.panel),
      frame: !!commander.panel.querySelector(".dl-card-panel-frame"),
      pair: !!commander.panel.querySelector("[data-card-panel-pair]"),
      buttons: pairButtons(commander.panel).length,
    },
    nonCommander: snap(single.panel),
    partnerNonCommander: snap(partner.panel),
  };
}

function runLatePartner() {
  const state = makeState([THRASIOS, TYMN, RING]);
  const page = install({ builder: true, playmat: true, state });
  return {
    name: detailName(page.panel),
    buttons: pairButtons(page.panel),
    frame: !!page.panel.querySelector(".dl-card-panel-frame"),
    current: page.windowObj.DeckLabCardPanel.current(),
  };
}

const runners = {
  initial: runInitial,
  hover: runHover,
  focus: runFocus,
  fields: runFields,
  fields_symbols: runFieldsSymbols,
  issues: runIssues,
  pin: runPin,
  image_error: runImageError,
  inactive: runInactive,
  evidence: runEvidence,
  dialog: runDialog,
  prefetch: runPrefetch,
  playmat_hover: runPlaymatHover,
  commander_mat: runCommanderMatHover,
  view_switch: runViewSwitch,
  rehover: runRehover,
  partners: runPartners,
  unchanged: runUnchanged,
  late_partner: runLatePartner,
};

const runner = runners[scenario];
if (!runner) {
  console.error("unknown scenario " + scenario);
  process.exit(1);
}
console.log(JSON.stringify(runner()));
