import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const stacksPath = process.argv[3];
const scenario = process.argv[4] || "columns";
const builderSource = fs.readFileSync(builderPath, "utf8");
const stacksSource = fs.readFileSync(stacksPath, "utf8");

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
    this.open = false;
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
    if (key === "tabindex") this.tabIndex = Number(value);
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
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    return child;
  }
  contains(node) {
    let current = node;
    while (current) { if (current === this) return true; current = current.parentNode; }
    return false;
  }
  replaceChildren(...nodes) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children.length = 0;
    this._text = "";
    nodes.forEach((n) => this.appendChild(n));
  }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  focus() { document.activeElement = this; }
  scrollIntoView() {}
  blur() { if (document.activeElement === this) document.activeElement = null; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((h) => h !== fn); }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    let node = this;
    while (node) {
      event.currentTarget = node;
      (node.listeners[event.type] || []).slice().forEach((fn) => fn(event));
      if (event._stopped) break;
      node = node.parentNode;
    }
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
  return nodes;
}

const documentElement = new Element("html");
const head = new Element("head", null);
const body = new Element("body", null);
const document = {
  documentElement, body, head, activeElement: null,
  createElement(tag) { const node = new Element(tag, document); node.ownerDocument = document; return node; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  createTextNode(text) { const node = document.createElement("#text"); node.textContent = String(text == null ? "" : text); return node; },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  removeEventListener(type, fn) { documentElement.removeEventListener(type, fn); },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};
head.ownerDocument = document;
body.ownerDocument = document;
documentElement.ownerDocument = document;
documentElement.appendChild(head);
documentElement.appendChild(body);

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
  return Object.assign({ type, preventDefault() {}, stopPropagation() { this._stopped = true; } }, extra);
}

function makeDeck(overrides = {}) {
  const deck = {
    id: "deck-1", title: "Stacks Deck", revision: 0,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-ramp", name: "Ramp", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
      { id: "zone-cons", name: "Considering", x: 880, y: 18, width: 280, layout_mode: "spread", sort_order: 2, layer: 0 }
    ],
    entries: [
      { id: "entry-cmd", name: "Kinnan, Bonder Prodigy", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "https://cards.test/kinnan.png", oracle_id: "oracle-kinnan", role: "", card_id: "kinnan" },
      { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://cards.test/sol-ring.png", oracle_id: "oracle-ring", role: "ramp", card_id: "ring" },
      { id: "entry-birds", name: "Birds of Paradise", is_commander: false, quantity: 2, zone_id: "zone-main", sort_order: 1, type_line: "Creature — Bird", mana_cost: "{G}", mana_value: 1, oracle_text: "", color_identity: ["G"], image_uri: "https://cards.test/birds.png", oracle_id: "oracle-birds", role: "ramp", card_id: "birds", feedback: { vote: "up" } },
      { id: "entry-study", name: "Rhystic Study", is_commander: false, quantity: 1, zone_id: "zone-ramp", sort_order: 0, type_line: "Enchantment", mana_cost: "{2}{U}", mana_value: 3, oracle_text: "", color_identity: ["U"], image_uri: "https://cards.test/study.png", oracle_id: "oracle-study", role: "draw", card_id: "study", feedback: { comment: "Keep" } },
      { id: "entry-island", name: "Island", is_commander: false, quantity: 1, zone_id: "zone-cons", sort_order: 0, type_line: "Basic Land — Island", mana_cost: "", mana_value: 0, oracle_text: "", color_identity: ["U"], image_uri: "", oracle_id: "oracle-island", role: "", card_id: "island" }
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "stacks", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 4, library_target: 99, total_count: 5, legal: false, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [], playmats: []
  };
  if (overrides.group_mode) deck.preferences.group_mode = overrides.group_mode;
  if (overrides.display_mode) deck.preferences.display_mode = overrides.display_mode;
  return deck;
}

const mode = scenario === "drag-role" ? "role" : scenario === "drag-type" ? "type" : "zone";
const deck = makeDeck({ group_mode: mode });
const batches = [];
const events = [];
let readyCount = 0;

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
body.appendChild(el("div", { id: "table-view" }));
const stage = el("div", { id: "playmat-stage" });
stage.appendChild(el("div", { id: "playmat" }));
const playmatView = el("div", { id: "playmat-view" });
playmatView.appendChild(stage);
body.appendChild(playmatView);
["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
body.appendChild(el("div", { "data-playmat-selection": "" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));
const imageDialog = el("dialog", { id: "card-image-dialog" });
imageDialog.showModal = function () { imageDialog.open = true; };
imageDialog.close = function () { imageDialog.open = false; };
imageDialog.append(
  el("h2", { id: "card-image-title", "data-card-image-title": "", text: "Card image" }),
  el("img", { "data-card-image": "", alt: "" }),
  el("p", { "data-card-image-status": "", text: "Loading card image…" }),
  el("a", { "data-card-image-original": "", href: "#" })
);
body.appendChild(imageDialog);
const toolbar = el("div", { className: "dl-builder-toolbar" });
["text", "stacks", "grid", "spoiler"].forEach((modeName) => {
  toolbar.appendChild(el("button", { "data-display": modeName, text: modeName }));
});
body.appendChild(toolbar);

document.addEventListener("deck-lab:ready", () => { readyCount += 1; });
document.addEventListener("deck-lab:entry-hover", (event) => {
  events.push({ type: "deck-lab:entry-hover", entryId: event.detail.entryId });
});
document.addEventListener("deck-lab:focus-entry", (event) => {
  events.push({ type: "deck-lab:focus-entry", entryId: event.detail.entryId });
});

function applyCommands(commands) {
  (commands || []).forEach((cmd) => {
    if (cmd.type === "update_view") Object.assign(deck.preferences, cmd);
    if (cmd.type === "move_entry") {
      const entry = deck.entries.find((item) => item.id === cmd.entry_id);
      if (entry) { entry.zone_id = cmd.zone_id; entry.sort_order = cmd.sort_order; }
    }
    if (cmd.type === "set_role") {
      const entry = deck.entries.find((item) => item.id === cmd.entry_id);
      if (entry) entry.role = cmd.role;
    }
  });
}

const store = {};
const windowObj = {
  matchMedia() { return { matches: false, addEventListener() {} }; },
  setTimeout, clearTimeout,
  crypto: { randomUUID: () => "uuid-1" },
  DeckLabSelects: { refresh() {} },
  prompt() { return null; },
  document,
};
async function flush() {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}
const context = vm.createContext({
  console, document, window: windowObj, setTimeout, clearTimeout,
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  fetch(url, opts = {}) {
    const address = String(url);
    if (address.includes("/feedback")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ cards: {}, deck: null }) });
    }
    const body = opts.body ? JSON.parse(opts.body) : null;
    if (body && body.commands) {
      batches.push(body.commands);
      applyCommands(body.commands);
    }
    deck.revision = (deck.revision || 0) + 1;
    return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: async () => JSON.parse(JSON.stringify(deck)) });
  },
  AbortController, URL, URLSearchParams,
  location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
  navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail || null; } },
  crypto: windowObj.crypto,
});

function run(source, filename) {
  vm.runInContext(source, context, { filename });
}
function card(id) {
  return [...document.querySelectorAll(".dl-stack-card")].find((node) => node.getAttribute("data-entry-id") === id);
}
function column(id) {
  return [...document.querySelectorAll(".dl-stack-col")].find((node) => node.getAttribute("data-group-id") === id);
}
function summarize() {
  const view = document.getElementById("table-view");
  return {
    columns: [...view.querySelectorAll(".dl-stack-col")].map((col) => ({
      id: col.getAttribute("data-group-id"),
      name: (col.querySelector(".dl-stack-name") || {}).textContent || "",
      count: (col.querySelector(".dl-stack-count") || {}).textContent || "",
      swatch: (col.querySelector(".dl-stack-swatch") || { getAttribute() { return ""; } }).getAttribute("style"),
      drop: col.getAttribute("data-drop"),
      cards: [...col.querySelectorAll(".dl-stack-card")].map((node) => ({
        id: node.getAttribute("data-entry-id"),
        face: node.getAttribute("data-stack-face"),
        label: node.getAttribute("aria-label"),
        qty: (node.querySelector(".dl-stack-qty") || {}).textContent || "",
        feedback: node.querySelector(".dl-stack-feedback") ? node.querySelector(".dl-stack-feedback").getAttribute("data-feedback") : "",
        pressed: node.getAttribute("aria-pressed"),
        tabIndex: node.tabIndex,
      })),
    })),
    rows: view.querySelectorAll(".dl-deck-row").length,
  };
}
function transfer() {
  const data = {};
  return {
    effectAllowed: "",
    dropEffect: "",
    setData(type, value) { data[type] = String(value); },
    getData(type) { return data[type] || ""; },
    setDragImage() {},
  };
}
async function drag(entryId, groupId) {
  const source = card(entryId);
  const target = column(groupId);
  const dt = transfer();
  source.dispatchEvent(makeEvent("dragstart", { target: source, dataTransfer: dt, clientX: 12, clientY: 16 }));
  let overPrevented = false;
  target.dispatchEvent(makeEvent("dragover", {
    target,
    dataTransfer: dt,
    clientX: 80,
    clientY: 40,
    preventDefault() { overPrevented = true; },
  }));
  const during = {
    overPrevented,
    dropTarget: target.classList.contains("drop-target"),
    disallowed: target.classList.contains("is-drop-disallowed"),
    dragSource: source.classList.contains("drag-source"),
    preview: !!document.querySelector(".dl-drag-preview"),
    dropEffect: dt.dropEffect,
  };
  const before = batches.length;
  target.dispatchEvent(makeEvent("drop", { target, dataTransfer: dt, clientX: 80, clientY: 40 }));
  await flush();
  return { during, commands: batches.slice(before) };
}

await (async function main() {
  run(builderSource, builderPath);
  await flush();
  const readyBeforeStacks = readyCount;
  const beforeStacks = summarize();
  if (scenario !== "late") {
    run(stacksSource, stacksPath);
    await flush();
  }
  let result = {};
  if (scenario === "late") {
    run(stacksSource, stacksPath);
    await flush();
    result = { readyBeforeStacks, before: beforeStacks, after: summarize() };
  } else if (scenario === "columns" || scenario === "badges") {
    result = summarize();
  } else if (scenario === "hover") {
    events.length = 0;
    const ring = card("entry-ring");
    ring.dispatchEvent(makeEvent("mouseenter", { target: ring }));
    ring.dispatchEvent(makeEvent("mouseover", { target: ring }));
    const lifted = ring.classList.contains("is-lifted");
    ring.dispatchEvent(makeEvent("mouseleave", { target: ring }));
    const afterLeave = ring.classList.contains("is-lifted");
    ring.dispatchEvent(makeEvent("focus", { target: ring }));
    const focusedLift = ring.classList.contains("is-lifted");
    ring.dispatchEvent(makeEvent("keydown", { target: ring, key: "Enter" }));
    result = { lifted, afterLeave, focusedLift, events: events.slice() };
  } else if (scenario === "drag-zone") {
    result = await drag("entry-ring", "zone-ramp");
  } else if (scenario === "drag-role") {
    result = await drag("entry-ring", "role-draw");
  } else if (scenario === "drag-type") {
    result = await drag("entry-ring", "type-Creature");
  } else if (scenario === "pointer") {
    const ring = card("entry-ring");
    const study = card("entry-study");
    ring.dispatchEvent(makeEvent("click", { target: ring, metaKey: true, ctrlKey: false }));
    const selected = context.window.DeckLabBuilder.getSelection();
    const pressed = ring.getAttribute("aria-pressed");
    ring.dispatchEvent(makeEvent("click", { target: ring, metaKey: true, ctrlKey: false }));
    const cleared = context.window.DeckLabBuilder.getSelection();
    study.dispatchEvent(makeEvent("dblclick", { target: study }));
    const title = document.querySelector("[data-card-image-title]");
    const image = document.querySelector("[data-card-image]");
    result = {
      selected, pressed, cleared,
      dialogOpen: !!imageDialog.open,
      title: title.textContent,
      src: image.src,
    };
  } else if (scenario === "keys") {
    const ring = card("entry-ring");
    ring.focus();
    function press(id, key) {
      card(id).dispatchEvent(makeEvent("keydown", { target: card(id), key }));
      return document.activeElement ? document.activeElement.getAttribute("data-entry-id") : null;
    }
    const down = press("entry-ring", "ArrowDown");
    const right = press("entry-birds", "ArrowRight");
    const left = press("entry-study", "ArrowLeft");
    const up = press("entry-birds", "ArrowUp");
    const enterOn = card("entry-study");
    events.length = 0;
    enterOn.dispatchEvent(makeEvent("keydown", { target: enterOn, key: "Enter" }));
    result = { down, right, left, up, enter: events.slice() };
  } else {
    throw new Error("unknown scenario " + scenario);
  }
  console.log(JSON.stringify(result));
})();
