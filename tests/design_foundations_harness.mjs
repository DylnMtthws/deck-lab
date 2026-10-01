import fs from "node:fs";
import vm from "node:vm";

const scenario = process.argv[2];
const iconsPath = process.argv[3];
const builderPath = process.argv[4];

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

function createDocument() {
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
      this.disabled = false;
      this.checked = false;
      this.open = false;
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
        }
      });
    }
    get textContent() {
      if (this.children.length) return this.children.map((c) => c.textContent).join("");
      return this._text;
    }
    set textContent(value) { this.children.length = 0; this._text = String(value); }
    set innerHTML(value) { this._text = String(value); this.children.length = 0; }
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
      return this.attributes[key] != null ? this.attributes[key] : null;
    }
    hasAttribute(name) { return this.getAttribute(name) != null; }
    removeAttribute(name) { delete this.attributes[String(name)]; }
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
        if (matches(node, selector)) return node;
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
    querySelector(selector) { return queryAll(this, selector)[0] || null; }
    querySelectorAll(selector) { return queryAll(this, selector); }
    addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
    removeEventListener(type, fn) {
      const list = this.listeners[type];
      if (!list) return;
      const idx = list.indexOf(fn);
      if (idx >= 0) list.splice(idx, 1);
    }
    dispatchEvent(event) {
      event.target = event.target || this;
      event.currentTarget = this;
      (this.listeners[event.type] || []).forEach((fn) => fn(event));
      if (this.parentNode && this.parentNode.dispatchEvent && !event._stop) this.parentNode.dispatchEvent(event);
      return true;
    }
    getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 20, width: 100, height: 20 }; }
    scrollIntoView() {}
    setPointerCapture() {}
    releasePointerCapture() {}
  }

  function matches(el, selector) {
    return String(selector).split(",").map((part) => part.trim()).some((part) => {
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

  const documentElement = new Element("html");
  const head = new Element("head");
  const body = new Element("body");
  const document = {
    documentElement, body, head, activeElement: null,
    createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
    createElementNS(_ns, tag) { return document.createElement(tag); },
    createTextNode(text) {
      const el = document.createElement("#text");
      el.textContent = String(text == null ? "" : text);
      return el;
    },
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
  return document;
}

function describe(section) {
  if (!section) return null;
  const parent = section.parentNode;
  return {
    id: section.getAttribute("data-ext-section"),
    heading: section.querySelector("h2") ? section.querySelector("h2").textContent : null,
    parentPane: parent && parent.getAttribute ? parent.getAttribute("data-rail-pane") : null,
    parentClass: parent && parent.className ? parent.className : null,
  };
}

function bootBuilder(withPanes) {
  const document = createDocument();
  const deck = {
    id: "deck-1", title: "Foundations", revision: 0,
    zones: [{ id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 }],
    entries: [{
      id: "entry-cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0,
      type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"],
      image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [],
    }],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
    validation: { commander_count: 1, library_count: 0, library_target: 99, total_count: 1, legal: false, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  };
  const data = document.createElement("script");
  data.id = "deck-document-data";
  data.textContent = JSON.stringify(deck);
  document.body.appendChild(data);
  const root = document.createElement("div");
  root.className = "dl-builder";
  root.setAttribute("data-shared", "false");
  root.setAttribute("data-playmat-enabled", "true");
  document.body.appendChild(root);
  const meta = document.createElement("meta");
  meta.setAttribute("name", "csrf-token");
  meta.setAttribute("content", "token");
  document.head.appendChild(meta);
  const save = document.createElement("button");
  save.id = "save-state";
  document.body.appendChild(save);
  const toolbar = document.createElement("div");
  toolbar.className = "dl-builder-toolbar";
  document.body.appendChild(toolbar);
  const table = document.createElement("div");
  table.id = "table-view";
  document.body.appendChild(table);
  const playmatView = document.createElement("div");
  playmatView.id = "playmat-view";
  const stage = document.createElement("div");
  stage.id = "playmat-stage";
  const mat = document.createElement("div");
  mat.id = "playmat";
  stage.appendChild(mat);
  playmatView.appendChild(stage);
  document.body.appendChild(playmatView);
  ["mana-curve", "color-stats", "zone-stats"].forEach((id) => {
    const node = document.createElement("div");
    node.id = id;
    document.body.appendChild(node);
  });
  const rail = document.createElement("aside");
  rail.className = "dl-stats-rail";
  if (withPanes) {
    ["card", "deck", "tools"].forEach((tab) => {
      const pane = document.createElement("div");
      pane.setAttribute("data-rail-pane", tab);
      rail.appendChild(pane);
    });
  }
  document.body.appendChild(rail);
  const windowObj = {
    matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; },
    setTimeout, clearTimeout,
    crypto: { randomUUID: () => "uuid-foundations" },
    DeckLabSelects: { refresh() {} },
    prompt() { return null; },
  };
  vm.runInContext(fs.readFileSync(builderPath, "utf8"), vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    fetch() { return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) }); },
    URL, URLSearchParams, location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {},
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  return windowObj.DeckLabBuilder;
}

if (scenario === "icons") {
  const document = createDocument();
  const windowObj = {};
  vm.runInContext(fs.readFileSync(iconsPath, "utf8"), vm.createContext({
    document, window: windowObj,
  }), { filename: iconsPath });
  const api = windowObj.DeckLabIcons;
  const drawn = api.names.map((name) => {
    const el = api.svg(name, { size: 20 });
    return {
      name,
      tag: String(el.tagName).toLowerCase(),
      viewBox: el.getAttribute("viewBox"),
      fill: el.getAttribute("fill"),
      stroke: el.getAttribute("stroke"),
      width: el.getAttribute("width"),
      height: el.getAttribute("height"),
      strokeWidth: el.getAttribute("stroke-width"),
      linecap: el.getAttribute("stroke-linecap"),
      markup: el.innerHTML,
    };
  });
  let unknown = null;
  try { api.svg("not-an-icon"); }
  catch (error) { unknown = String(error && error.message || error); }
  const sized = api.svg("search");
  console.log(JSON.stringify({
    names: api.names,
    drawn,
    unknown,
    defaultSize: sized.getAttribute("width"),
  }));
} else if (scenario === "rail-panes") {
  const api = bootBuilder(true);
  const card = api.railSection("preview", "Preview", { tab: "card" });
  const again = api.railSection("preview", "Preview again", { tab: "tools" });
  const deck = api.railSection("curve", "Mana curve");
  const tools = api.railSection("odds", "Draw odds", { tab: "tools" });
  const missing = api.railSection("orphan", "Orphan", { tab: "nope" });
  console.log(JSON.stringify({
    card: describe(card),
    same: card === again,
    headingKept: again.querySelector("h2").textContent,
    count: documentQueryCount(card),
    deck: describe(deck),
    tools: describe(tools),
    missingPane: describe(missing),
  }));
} else if (scenario === "rail-fallback") {
  const api = bootBuilder(false);
  const first = api.railSection("x", "Filter Cards");
  const second = api.railSection("x", "Filter Cards");
  console.log(JSON.stringify({
    first: describe(first),
    same: first === second,
    onRail: first.parentNode.className === "dl-stats-rail",
    pane: first.parentNode.getAttribute("data-rail-pane"),
  }));
} else {
  throw new Error("unknown scenario " + scenario);
}

function documentQueryCount(section) {
  const rail = section.closest(".dl-stats-rail");
  return rail.querySelectorAll('[data-ext-section="preview"]').length;
}
