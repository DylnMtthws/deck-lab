
import fs from "node:fs";
import vm from "node:vm";

const manaJsPath = process.argv[2];
const builderJsPath = process.argv[3];
const manaSource = fs.readFileSync(manaJsPath, "utf8");
const builderSource = fs.readFileSync(builderJsPath, "utf8");

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
    this.width = 0;
    this.height = 0;
    this.loading = "";
    this.decoding = "";
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
    if (key === "src") this.src = String(value);
    if (key === "alt") this.alt = String(value);
    if (key === "width") this.width = Number(value);
    if (key === "height") this.height = Number(value);
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    if (key === "title") return this.title || null;
    if (key === "src") return this.src || null;
    if (key === "alt") return this.alt || null;
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
  replaceChild(newChild, oldChild) {
    const idx = this.children.indexOf(oldChild);
    if (idx >= 0) { oldChild.parentNode = null; newChild.parentNode = this; this.children[idx] = newChild; return oldChild; }
    return null;
  }
  querySelector(sel) { return queryAll(this, sel)[0] || null; }
  querySelectorAll(sel) { return queryAll(this, sel); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    return true;
  }
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === documentElement; }
  focus() { document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = null; }
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
  documentElement, head, body, activeElement: null,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
  createTextNode(text) { const el = document.createElement("#text"); el.textContent = String(text == null ? "" : text); return el; },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};

function makeEvent(type, extra = {}) {
  const ev = { type, target: null, preventDefault() {}, stopPropagation() {} };
  return Object.assign(ev, extra);
}

// Utility: run code in a shared context.
function createCtx(windowExtras = {}) {
  const w = Object.assign({
    matchMedia() { return { matches: false, addEventListener() {} }; },
    Set, Map, Promise, JSON, Math, Number, Date, String, Boolean, Array, Object, Error,
    setTimeout, clearTimeout,
  }, windowExtras);
  return vm.createContext({
    console, document, window: w,
    Set, Map, Promise, JSON, Math, Number, Date, String, Boolean, Array, Object, Error,
    setTimeout, clearTimeout,
  });
}

const output = {};

// ── AC-1: fileName mapping table ──
(function testFileName() {
  const ctx = createCtx();
  vm.runInContext(manaSource, ctx, { filename: manaJsPath });
  const dm = ctx.window.DeckLabMana;
  const cases = [
    ["W", "W.svg"],
    ["2", "2.svg"],
    ["10", "10.svg"],
    ["X", "X.svg"],
    ["W/U", "WU.svg"],
    ["2/W", "2W.svg"],
    ["W/P", "WP.svg"],
    ["G/U/P", "GUP.svg"],
    ["C", "C.svg"],
    ["S", "S.svg"],
    ["T", "T.svg"],
    ["Q", "Q.svg"],
    // Invalid: should return null
    ["", null],
    ["INVALID", null],
  ];
  output.fileNameCases = cases.map(([input, expected]) => {
    const actual = dm.fileName(input);
    return { input, expected, actual, pass: actual === expected };
  });
})();

// ── AC-2: symbol returns img with Scryfall src and alt ──
(function testSymbol() {
  const ctx = createCtx();
  vm.runInContext(manaSource, ctx, { filename: manaJsPath });
  const dm = ctx.window.DeckLabMana;

  const decorative = dm.symbol("W", { decorative: true });
  output.symbolDecorative = {
    tag: decorative.tagName,
    className: decorative.className,
    src: decorative.src,
    alt: decorative.alt,
    width: decorative.width,
    height: decorative.height,
    loading: decorative.loading,
    ariaHidden: decorative.getAttribute("aria-hidden"),
  };

  const nonDecorative = dm.symbol("U", {});
  output.symbolNonDecorative = {
    tag: nonDecorative.tagName,
    className: nonDecorative.className,
    src: nonDecorative.src,
    alt: nonDecorative.alt,
    ariaHidden: nonDecorative.getAttribute("aria-hidden"),
  };

  // Invalid symbol returns a span fallback
  const invalid = dm.symbol("INVALID", { decorative: true });
  output.symbolInvalid = {
    tag: invalid.tagName,
    className: invalid.className,
    text: invalid.textContent,
    ariaHidden: invalid.getAttribute("aria-hidden"),
  };
})();

// ── AC-3: error event replaces img with span fallback ──
(function testErrorFallback() {
  const ctx = createCtx();
  vm.runInContext(manaSource, ctx, { filename: manaJsPath });
  const dm = ctx.window.DeckLabMana;
  const el = dm.symbol("R", { decorative: true });
  const parent = document.createElement("div");
  parent.appendChild(el);
  el.dispatchEvent(makeEvent("error", { target: el }));
  const fallback = parent.querySelector(".dl-mana-symbol");
  output.errorFallback = {
    imgGone: parent.querySelector(".dl-ms") === null,
    spanExists: fallback !== null,
    spanClass: fallback ? fallback.className : null,
    spanText: fallback ? fallback.textContent : null,
  };
})();

// ── AC-4: builder mana cost uses SVG when mana.js loaded ──
(function testBuilderWithMana() {
  const deck = {
    id: "deck-mana", title: "Mana Test", revision: 0,
    zones: [{ id: "z1", name: "Unsorted", x: 0, y: 0, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 }],
    entries: [
      {
        id: "e1", name: "Test Card", is_commander: false, quantity: 1,
        zone_id: "z1", sort_order: 0, type_line: "Creature",
        mana_cost: "{1}{U}{U}", mana_value: 3, oracle_text: "",
        color_identity: ["U"], image_uri: "", role: "",
        format_legal: true, commander_legal: true, validation_issues: [],
      },
    ],
    presentation: {}, preferences: {}, validation: { commander_count: 0, library_count: 1, total_count: 1 },
    tags: [], tag_suggestions: [],
  };
  const ctx = createCtx({ crypto: { randomUUID: () => "uuid-m" } });
  const contextDoc = ctx.document;
  const contextBody = contextDoc.body;
  contextBody.appendChild(Object.assign(contextDoc.createElement("script"), {
    id: "deck-document-data", textContent: JSON.stringify(deck),
  }));
  contextBody.appendChild(Object.assign(contextDoc.createElement("div"), {
    className: "dl-builder",
    dataset: { shared: "false", playmatEnabled: "false" },
  }));
  contextBody.appendChild(Object.assign(contextDoc.createElement("meta"), { name: "csrf-token", content: "tok" }));
  const saveBtn = contextDoc.createElement("button");
  saveBtn.id = "save-state";
  contextBody.appendChild(saveBtn);
  // Mini toolbar
  ["deck-count", "table-view", "playmat-stage", "mana-curve", "color-stats", "zone-stats",
   "zoom-label", "playmat-view"].forEach((id) => {
    const el = contextDoc.createElement("div");
    el.id = id;
    contextBody.appendChild(el);
  });
  const stage = contextDoc.getElementById("playmat-stage");
  stage.appendChild(contextDoc.createElement("div"));
  // data elements for render
  ["playmat-selection", "deck-tag-list", "tag-summary", "tag-options"].forEach((attr) => {
    const el = contextDoc.createElement("div");
    el.setAttribute("data-" + attr, "");
    contextBody.appendChild(el);
  });

  // Load mana.js first, then builder.js
  vm.runInContext(manaSource, ctx, { filename: manaJsPath });
  vm.runInContext(builderSource, ctx, { filename: builderJsPath });

  // The builder renders synchronously in its IIFE. Check rendered mana cost.
  const rows = contextDoc.querySelectorAll(".dl-deck-row");
  let imgCount = 0;
  if (rows.length) {
    const cost = rows[0].querySelector(".dl-mana-cost");
    if (cost) {
      imgCount = cost.querySelectorAll("img.dl-ms").length;
    }
  }
  output.builderWithMana = { rows: rows.length, imgCount };
})();

// ── AC-5: builder falls back without mana.js ──
(function testBuilderWithoutMana() {
  const deck = {
    id: "deck-nomana", title: "No Mana", revision: 0,
    zones: [{ id: "z1", name: "Unsorted", x: 0, y: 0, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 }],
    entries: [
      {
        id: "e2", name: "Sol Ring", is_commander: false, quantity: 1,
        zone_id: "z1", sort_order: 0, type_line: "Artifact",
        mana_cost: "{1}", mana_value: 1, oracle_text: "",
        color_identity: [], image_uri: "", role: "",
        format_legal: true, commander_legal: true, validation_issues: [],
      },
    ],
    presentation: {}, preferences: {}, validation: { commander_count: 0, library_count: 1, total_count: 1 },
    tags: [], tag_suggestions: [],
  };
  const ctx = createCtx({ crypto: { randomUUID: () => "uuid-n" } });
  const contextDoc = ctx.document;
  const contextBody = contextDoc.body;
  contextBody.appendChild(Object.assign(contextDoc.createElement("script"), {
    id: "deck-document-data", textContent: JSON.stringify(deck),
  }));
  contextBody.appendChild(Object.assign(contextDoc.createElement("div"), {
    className: "dl-builder",
    dataset: { shared: "false", playmatEnabled: "false" },
  }));
  contextBody.appendChild(Object.assign(contextDoc.createElement("meta"), { name: "csrf-token", content: "tok" }));
  const saveBtn = contextDoc.createElement("button");
  saveBtn.id = "save-state";
  contextBody.appendChild(saveBtn);
  ["deck-count", "table-view", "playmat-stage", "mana-curve", "color-stats", "zone-stats",
   "zoom-label", "playmat-view"].forEach((id) => {
    const el = contextDoc.createElement("div");
    el.id = id;
    contextBody.appendChild(el);
  });
  const stage = contextDoc.getElementById("playmat-stage");
  stage.appendChild(contextDoc.createElement("div"));
  ["playmat-selection", "deck-tag-list", "tag-summary", "tag-options"].forEach((attr) => {
    const el = contextDoc.createElement("div");
    el.setAttribute("data-" + attr, "");
    contextBody.appendChild(el);
  });

  // Load builder.js WITHOUT mana.js
  vm.runInContext(builderSource, ctx, { filename: builderJsPath });

  const rows = contextDoc.querySelectorAll(".dl-deck-row");
  let fallbackCount = 0;
  if (rows.length) {
    const cost = rows[0].querySelector(".dl-mana-cost");
    if (cost) {
      fallbackCount = cost.querySelectorAll(".dl-mana-symbol").length;
    }
  }
  output.builderWithoutMana = { rows: rows.length, fallbackCount };
})();

console.log(JSON.stringify(output));
