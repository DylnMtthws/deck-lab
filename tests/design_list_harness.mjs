import fs from "node:fs";
import vm from "node:vm";

const scenario = process.argv[2];
const root = process.argv[3];
const builderSource = fs.readFileSync(`${root}/src/sabermetrics/ui/static/deck-lab-builder.js`, "utf8");
const feedbackSource = fs.readFileSync(`${root}/src/sabermetrics/ui/static/deck-lab-feedback.js`, "utf8");
const iconsSource = fs.readFileSync(`${root}/src/sabermetrics/ui/static/deck-lab-icons.js`, "utf8");
const manaSource = fs.readFileSync(`${root}/src/sabermetrics/ui/static/deck-lab-mana.js`, "utf8");
const panelSource = fs.readFileSync(`${root}/src/sabermetrics/ui/static/deck-lab-card-panel.js`, "utf8");

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

function matchSelector(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    if (!part || part === "*") return part === "*";
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
  walk(root, []).forEach((node) => { if (node !== root && matchSelector(node, selector)) nodes.push(node); });
  nodes.forEach = Array.prototype.forEach;
  nodes.map = Array.prototype.map;
  nodes.filter = Array.prototype.filter;
  nodes.find = Array.prototype.find;
  return nodes;
}

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
    this._value = "";
    this.title = "";
    this.disabled = false;
    this.checked = false;
    this.indeterminate = false;
    this.open = false;
    this.selected = false;
    this.href = "";
    this.src = "";
    this.alt = "";
    this.rel = "";
    this.tabIndex = 0;
    this.width = 0;
    this.height = 0;
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
  set textContent(value) { this.children.length = 0; this._text = value == null ? "" : String(value); }
  set innerHTML(value) { this._text = String(value); this.children.length = 0; }
  get innerHTML() { return this._text; }
  get value() {
    if (this.tagName === "SELECT") {
      const selected = this.children.find((child) => child.selected);
      return selected ? (selected.value || selected.getAttribute("value") || "") : "";
    }
    return this._value;
  }
  set value(v) {
    this._value = v == null ? "" : String(v);
    if (this.tagName === "SELECT") {
      this.children.forEach((child) => { child.selected = (child.value || child.getAttribute("value")) === this._value; });
    }
  }
  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
    if (key === "title") this.title = String(value);
    if (key === "href") this.href = String(value);
    if (key === "src") this.src = String(value);
    if (key === "type") this.type = String(value);
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
  insertBefore(child, ref) {
    child.parentNode = this;
    const index = ref ? this.children.indexOf(ref) : this.children.length;
    this.children.splice(index < 0 ? this.children.length : index, 0, child);
    return child;
  }
  replaceChildren(...nodes) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children.length = 0;
    this._text = "";
    nodes.forEach((n) => this.appendChild(n));
  }
  replaceWith(node) {
    const parent = this.parentNode;
    if (!parent) return;
    const index = parent.children.indexOf(this);
    node.parentNode = parent;
    if (index >= 0) parent.children.splice(index, 1, node);
    this.parentNode = null;
  }
  closest(selector) { let node = this; while (node && node.tagName) { if (matchSelector(node, selector)) return node; node = node.parentNode; } return null; }
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === documentElement; }
  focus() { document.activeElement = this; }
  blur() {}
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    return true;
  }
}

function entry(partial) {
  return Object.assign({
    is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0,
    type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "",
    color_identity: [], image_uri: "https://cards.test/card.png", role: "",
    format_legal: true, commander_legal: true, validation_issues: [], card_id: "",
  }, partial);
}

function makeDeck(preferences) {
  return {
    id: "deck-d02", title: "List Deck", revision: 3,
    zones: [
      { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
      { id: "zone-considering", name: "Considering", x: 460, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 },
    ],
    entries: [
      entry({ id: "entry-cmd", name: "Kinnan", is_commander: true, zone_id: null, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "{T}: Add {C}.", color_identity: ["G", "U"], oracle_id: "o-cmd", image_uri: "https://cards.test/kinnan.png" }),
      entry({ id: "entry-sol", name: "Sol Ring", role: "ramp", oracle_id: "o-ring", card_id: "sol", sort_order: 0, oracle_text: "{T}: Add {C}{C}.", image_uri: "https://cards.test/sol.png" }),
      entry({ id: "entry-birds", name: "Birds of Paradise", role: "draw", quantity: 2, type_line: "Creature", mana_cost: "{G}", oracle_id: "o-birds", sort_order: 1, oracle_text: "{T}: Add {G} or {U}." }),
      entry({ id: "entry-land", name: "Island", role: "", type_line: "Basic Land — Island", mana_cost: "", mana_value: 0, oracle_id: "o-land", sort_order: 2, oracle_text: "({T}: Add {U}.)" }),
      entry({ id: "entry-hulk", name: "Hullbreaker Horror", role: "combo", zone_id: "zone-considering", type_line: "Creature", mana_cost: "{6}{U}{U}", mana_value: 8, oracle_id: "o-hulk", sort_order: 0, oracle_text: "Flash\n{U}{U}: Counter target spell." }),
    ],
    presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
    preferences: Object.assign({ view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" }, preferences),
    validation: { commander_count: 1, library_count: 4, library_target: 99, total_count: 5, legal: false, issues: [], entry_issues: {} },
    tags: [], tag_suggestions: [],
  };
}

const prefs = {
  columns: { group_mode: "zone", density: "compact", display_mode: "text" },
  feedback: { group_mode: "zone", density: "compact", display_mode: "text" },
  "zone-chip": { group_mode: "role", display_mode: "text" },
  "role-chip": { group_mode: "zone", display_mode: "text" },
  header: { group_mode: "role", display_mode: "text" },
  spoiler: { group_mode: "zone", display_mode: "spoiler" },
  click: { group_mode: "zone", display_mode: "text" },
}[scenario] || { group_mode: "zone" };

const deck = makeDeck(prefs);
const feedbackBody = {
  cards: {
    "o-ring": { vote: "up", comment: "Keep it" },
    "o-hulk": { vote: null, comment: null },
  },
  deck: { verdict: null, comment: null },
};

const documentElement = new Element("html");
const head = new Element("head");
const body = new Element("body");
const document = {
  documentElement, body, head, activeElement: null,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  createTextNode(text) { const el = document.createElement("#text"); el.textContent = String(text == null ? "" : text); return el; },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
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

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
const toolbar = el("div", { className: "dl-builder-toolbar" });
toolbar.appendChild(el("span", { className: "dl-deck-count", "data-deck-count": "", text: "0/100" }));
const groupSelect = el("select", { className: "dl-zone-select", "data-group": "", "aria-label": "Group cards" });
["zone", "type", "role"].forEach((value) => groupSelect.appendChild(el("option", { value, text: value })));
toolbar.appendChild(groupSelect);
body.appendChild(toolbar);
body.appendChild(el("div", { id: "table-view" }));
body.appendChild(el("div", { id: "playmat-view" }));
const stage = el("div", { id: "playmat-stage" });
stage.appendChild(el("div", { id: "playmat" }));
body.appendChild(stage);
body.appendChild(el("span", { id: "zoom-label" }));
["mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el("div", { id })));
body.appendChild(el("div", { className: "dl-stats-rail" }));
body.appendChild(el("div", { "data-card-panel": "" }));
const imageDialog = el("dialog", { id: "card-image-dialog", className: "dl-dialog" });
imageDialog.appendChild(el("h2", { "data-card-image-title": "", text: "Card image" }));
imageDialog.appendChild(el("img", { "data-card-image": "", alt: "" }));
imageDialog.appendChild(el("p", { "data-card-image-status": "", text: "Loading card image…" }));
imageDialog.appendChild(el("a", { "data-card-image-original": "", href: "#" }));
body.appendChild(imageDialog);

function apply(live, commands) {
  (commands || []).forEach((cmd) => {
    if (cmd.type === "update_view") {
      live.preferences = Object.assign({}, live.preferences);
      if (cmd.collapsed) live.preferences.collapsed_json = JSON.stringify(cmd.collapsed);
      ["density", "display_mode", "group_mode", "sort_mode", "view_mode"].forEach((key) => {
        if (cmd[key] != null) live.preferences[key] = cmd[key];
      });
    }
  });
  live.revision += 1;
}

const store = {};
let live = JSON.parse(JSON.stringify(deck));
const windowObj = {
  matchMedia() { return { matches: false, addEventListener() {}, addListener() {} }; },
  setTimeout, clearTimeout,
  crypto: { randomUUID: () => "uuid-d02" },
  DeckLabSelects: { refresh() {} },
  prompt() { return null; },
  Image: function Image() { this.addEventListener = function () {}; },
};
windowObj.window = windowObj;
const context = vm.createContext({
  console, document, window: windowObj, setTimeout, clearTimeout,
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  fetch(url, opts = {}) {
    const href = String(url);
    if (href.includes("/feedback")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(feedbackBody)) });
    }
    if (opts.body) apply(live, JSON.parse(opts.body).commands);
    return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(live)) });
  },
  AbortController, URL, URLSearchParams,
  location: { href: "http://deck.lab/build/deck/deck-d02", origin: "http://deck.lab", pathname: "/build/deck/deck-d02", search: "" },
  navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail || null; } },
  crypto: windowObj.crypto,
});

function flush() { return new Promise((resolve) => setTimeout(resolve, 0)); }
function rows() { return queryAll(document.getElementById("table-view"), ".dl-deck-row"); }
function rowByName(name) {
  return rows().find((row) => (row.textContent || "").includes(name));
}
function childClasses(row) {
  return row.children.map((child) => child.className || child.tagName);
}

await (async function main() {
  vm.runInContext(iconsSource, context, { filename: "deck-lab-icons.js" });
  vm.runInContext(manaSource, context, { filename: "deck-lab-mana.js" });
  vm.runInContext(builderSource, context, { filename: "deck-lab-builder.js" });
  await flush();
  if (scenario === "feedback" || scenario === "columns") {
    vm.runInContext(feedbackSource, context, { filename: "deck-lab-feedback.js" });
    await flush();
    for (let i = 0; i < 6; i += 1) await flush();
  }
  if (scenario === "click") {
    vm.runInContext(panelSource, context, { filename: "deck-lab-card-panel.js" });
    await flush();
  }

  const surface = document.querySelector(".dl-decklist-surface");
  if (scenario === "columns") {
    const sol = rowByName("Sol Ring");
    const api = windowObj.DeckLabBuilder;
    await api.command([{ type: "update_view", density: "comfortable" }]);
    await flush();
    const comfortable = document.querySelector(".dl-decklist-surface");
    console.log(JSON.stringify({
      headers: queryAll(document.getElementById("table-view"), "[role=columnheader]").map((n) => n.textContent),
      compactClass: surface.className,
      comfortableClass: comfortable.className,
      children: childClasses(sol),
      selectLabel: sol.querySelector(".dl-row-select").getAttribute("aria-label"),
      steps: queryAll(sol, "button").map((n) => n.getAttribute("aria-label")).filter(Boolean),
      roleSelects: queryAll(sol, "select.dl-role-select").length,
      zoneSelect: sol.querySelector("select.dl-inline-zone-select") ? sol.querySelector("select.dl-inline-zone-select").className : "",
      entryId: sol.getAttribute("data-entry-id"),
    }));
    return;
  }
  if (scenario === "feedback") {
    const sol = rowByName("Sol Ring");
    const land = rowByName("Island");
    const group = sol.querySelector("[data-card-feedback]");
    const buttons = group ? queryAll(group, "button") : [];
    console.log(JSON.stringify({
      inActions: !!(group && group.parentNode && group.parentNode.classList.contains("dl-row-actions")),
      loadedAfterReady: true,
      buttons: buttons.map((button) => ({
        label: button.getAttribute("aria-label"),
        tip: button.getAttribute("data-dl-tip"),
        className: button.className,
        pressed: button.getAttribute("aria-pressed"),
        hasComment: button.hasAttribute("data-has-comment"),
        text: button.textContent,
        svg: !!button.querySelector("svg"),
      })),
      landOn: land.querySelector("[data-vote=up]") ? land.querySelector("[data-vote=up]").classList.contains("is-on") : null,
      checkbox: !!sol.querySelector(".dl-row-select"),
      steppers: queryAll(sol, ".dl-step").length,
    }));
    return;
  }
  if (scenario === "zone-chip") {
    const sol = rowByName("Sol Ring");
    const hulk = rowByName("Hullbreaker Horror");
    console.log(JSON.stringify({
      solChip: sol.querySelector(".dl-zone-chip") ? sol.querySelector(".dl-zone-chip").textContent : null,
      hulkChip: hulk.querySelector(".dl-zone-chip") ? hulk.querySelector(".dl-zone-chip").textContent : null,
      solRoleSelect: queryAll(sol, "select.dl-role-select").length,
      hulkRoleSelect: queryAll(hulk, "select.dl-role-select").length,
    }));
    return;
  }
  if (scenario === "role-chip") {
    const sol = rowByName("Sol Ring");
    const land = rowByName("Island");
    const hulk = rowByName("Hullbreaker Horror");
    console.log(JSON.stringify({
      solRole: sol.querySelector(".dl-role-chip") ? sol.querySelector(".dl-role-chip").textContent : null,
      landRole: land.querySelector(".dl-role-chip") ? land.querySelector(".dl-role-chip").textContent : null,
      hulkRole: hulk.querySelector(".dl-role-chip") ? hulk.querySelector(".dl-role-chip").textContent : null,
      roleSelects: queryAll(document.getElementById("table-view"), "select.dl-role-select").length,
      solZoneChip: sol.querySelector(".dl-zone-chip") ? sol.querySelector(".dl-zone-chip").textContent : null,
    }));
    return;
  }
  if (scenario === "header") {
    function groups() {
      return queryAll(document.getElementById("table-view"), ".dl-zone-section").map((section) => {
        const count = section.querySelector(".dl-zone-count");
        const share = section.querySelector(".dl-share");
        const fill = share ? share.querySelector("i") : null;
        const bodyEl = section.children.find((child) => child.tagName === "DIV" && !child.classList.contains("dl-zone-heading"));
        return {
          id: section.id,
          name: section.querySelector("h2") ? section.querySelector("h2").textContent : "",
          chip: section.querySelector(".dl-commander-chip") ? section.querySelector(".dl-commander-chip").textContent : null,
          count: count ? count.textContent : "",
          share: share ? (fill && (fill.style.width || fill.style._props.width)) : null,
          collapsed: section.classList.contains("collapsed"),
          hidden: !!(bodyEl && bodyEl.hidden),
        };
      });
    }
    const before = groups();
    const ramp = document.querySelector("#zone-role-ramp .dl-zone-collapse") || queryAll(document.getElementById("table-view"), ".dl-zone-collapse").find((button) => (button.getAttribute("aria-label") || "").includes("Ramp"));
    ramp.dispatchEvent({ type: "click", target: ramp, preventDefault() {}, stopPropagation() {} });
    await flush();
    const collapsed = groups().find((group) => group.id === "zone-role-ramp");
    const expand = queryAll(document.getElementById("table-view"), ".dl-zone-collapse").find((button) => (button.getAttribute("aria-label") || "").includes("Ramp"));
    expand.dispatchEvent({ type: "click", target: expand, preventDefault() {}, stopPropagation() {} });
    await flush();
    const restored = groups().find((group) => group.id === "zone-role-ramp");
    console.log(JSON.stringify({ before, collapsed, restored }));
    return;
  }
  if (scenario === "spoiler") {
    const symbols = queryAll(document.getElementById("table-view"), "img.dl-ms");
    const inline = symbols.filter((img) => img.closest(".dl-mana-inline"));
    console.log(JSON.stringify({
      symbols: inline.length,
      tag: inline[0] ? inline[0].tagName : null,
      className: inline[0] ? inline[0].className : "",
      inRules: inline.every((img) => !!img.closest("p")),
    }));
    return;
  }
  if (scenario === "click") {
    const sol = rowByName("Sol Ring");
    const name = sol.querySelector(".dl-card-name");
    const before = document.querySelector("[data-card-panel-name]");
    sol.dispatchEvent({ type: "click", target: name, preventDefault() {}, stopPropagation() {} });
    const afterClick = document.querySelector("[data-card-panel-name]");
    const dialog = document.getElementById("card-image-dialog");
    const openBefore = !!dialog.open;
    sol.dispatchEvent({ type: "dblclick", target: name, preventDefault() {}, stopPropagation() {} });
    console.log(JSON.stringify({
      before: before ? before.textContent : null,
      afterClick: afterClick ? afterClick.textContent : null,
      focused: document.activeElement && document.activeElement.getAttribute("data-entry-id"),
      openBefore,
      dialogOpen: !!dialog.open,
      title: dialog.querySelector("[data-card-image-title]").textContent,
    }));
  }
})();
