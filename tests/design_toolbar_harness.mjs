import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const consideringPath = process.argv[3];
const source = fs.readFileSync(builderPath, "utf8");
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
    this.disabled = false;
    this.checked = false;
    this.open = false;
    this.tabIndex = 0;
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
    if (key === "hidden") this.hidden = true;
    if (key === "value") this.value = String(value);
    if (key === "disabled") this.disabled = true;
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    delete this.attributes[String(name)];
    if (name === "hidden") this.hidden = false;
    if (name === "disabled") this.disabled = false;
  }
  appendChild(child) {
    if (typeof child === "string") { const t = this.ownerDocument.createElement("#text"); t.textContent = child; child = t; }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  insertBefore(node, ref) {
    node.parentNode = this;
    const idx = ref ? this.children.indexOf(ref) : -1;
    if (idx >= 0) this.children.splice(idx, 0, node);
    else this.children.push(node);
    return node;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some((child) => child.contains && child.contains(node));
  }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  focus() { document.activeElement = this; }
  click() { this.dispatchEvent(makeEvent("click", { target: this })); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn(event));
    if (!event._stopped && this.parentNode && this.parentNode.dispatchEvent) this.parentNode.dispatchEvent(event);
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
  return nodes;
}
const documentElement = new Element("html");
const body = new Element("body");
const document = {
  documentElement, body, activeElement: null,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return queryAll(documentElement, sel)[0] || null; },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};
body.ownerDocument = document;
documentElement.ownerDocument = document;
documentElement.appendChild(body);
function el(tag, attrs = {}) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "id") { node.id = value; node.setAttribute("id", value); }
    else if (key === "text") node.textContent = value;
    else if (key === "hidden") { if (value) node.setAttribute("hidden", ""); else node.hidden = false; }
    else node.setAttribute(key, value);
  });
  return node;
}
function makeEvent(type, extra = {}) {
  return Object.assign({ type, _stopped: false, preventDefault() {}, stopPropagation() { this._stopped = true; } }, extra);
}
class Event {
  constructor(type) { this.type = type; this._stopped = false; }
  preventDefault() {}
  stopPropagation() { this._stopped = true; }
}

const deck = {
  id: "deck-1", title: "Toolbar Deck", revision: 0,
  zones: [
    { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread" },
    { id: "zone-ramp", name: "Ramp", x: 460, y: 18, width: 400, layout_mode: "spread" }
  ],
  entries: [
    { id: "entry-cmd", name: "Kinnan", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G", "U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "ramp", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] }
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  validation: { total_count: 3, legal: false, issues: [], entry_issues: {} },
  tags: [], tag_suggestions: [], playmats: []
};
const commands = [];
const clipboard = [];
function apply(list) {
  list.forEach((cmd) => {
    commands.push(cmd);
    if (cmd.type === "update_view") {
      ["view_mode", "display_mode", "group_mode", "sort_mode", "density"].forEach((key) => {
        if (cmd[key] != null) deck.preferences[key] = cmd[key];
      });
    }
    if (cmd.type === "update_presentation") {
      Object.keys(cmd).forEach((key) => { if (key !== "type") deck.presentation[key] = cmd[key]; });
    }
    if (cmd.type === "move_entry") {
      const entry = deck.entries.find((item) => item.id === cmd.entry_id);
      if (entry) { entry.zone_id = cmd.zone_id; entry.sort_order = cmd.sort_order; }
    }
    if (cmd.type === "set_role") {
      const entry = deck.entries.find((item) => item.id === cmd.entry_id);
      if (entry) entry.role = cmd.role;
    }
    if (cmd.type === "remove_entry") deck.entries = deck.entries.filter((item) => item.id !== cmd.entry_id);
    if (cmd.type === "create_zone") deck.zones.push({ id: cmd.zone_id, name: cmd.name, x: cmd.x || 0, y: cmd.y || 0 });
  });
  deck.revision += 1;
}

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
body.appendChild(el("button", { id: "save-state" }));
const main = el("div", { "data-toolbar-main": "" });
const search = el("input", { "data-card-search": "" });
const addZone = el("select", { "data-add-zone": "", "aria-label": "Add found cards to category" });
const pillName = el("span", { "data-add-destination-name": "", text: "Unsorted" });
const pill = el("button", { type: "button", "data-add-destination-toggle": "", "aria-expanded": "false" });
pill.appendChild(pillName);
const destMenu = el("div", { id: "add-destination-menu", "data-add-destination-menu": "", hidden: true, role: "menu" });
const hint = el("p", { "data-add-destination-hint": "", text: "Cards you add go to Unsorted." });
main.append(search, addZone, pill, destMenu, hint);
const viewBtn = el("button", { type: "button", "data-view-options": "", "aria-expanded": "false" });
const viewLabel = el("span", { "data-view-label": "", text: "View · Zone · Manual" });
viewBtn.appendChild(viewLabel);
const popover = el("div", { id: "view-popover", "data-view-popover": "", hidden: true, role: "dialog" });
const decklistPane = el("div", { "data-view-decklist": "" });
["text", "stacks", "grid", "spoiler"].forEach((mode) => {
  decklistPane.appendChild(el("button", { type: "button", "data-display": mode, text: mode }));
});
const group = el("select", { "data-group": "", "aria-label": "Group cards" });
[["zone", "Zone"], ["type", "Type"], ["role", "Role"]].forEach(([value, text]) => group.appendChild(el("option", { value, text })));
group.value = "zone";
const sort = el("select", { "data-sort": "", "aria-label": "Sort cards" });
[["manual", "Manual"], ["name", "Name"], ["mana_value", "Mana value"]].forEach(([value, text]) => sort.appendChild(el("option", { value, text })));
sort.value = "manual";
decklistPane.append(group, sort, el("button", { type: "button", "data-density": "compact", text: "Compact" }), el("button", { type: "button", "data-density": "comfortable", text: "Comfy" }));
const playmatPane = el("div", { "data-view-playmat": "", hidden: true });
["slate-grid", "felt-weave", "void"].forEach((key) => playmatPane.appendChild(el("button", { type: "button", "data-surface": key, text: key })));
playmatPane.appendChild(el("div", { "data-my-playmats": "" }));
playmatPane.appendChild(el("input", { type: "checkbox", "data-setting": "show_zone_outlines" }));
playmatPane.appendChild(el("input", { type: "checkbox", "data-setting": "dim_inactive" }));
playmatPane.appendChild(el("button", { type: "button", "data-zoom": "fit", text: "Fit" }));
popover.append(decklistPane, playmatPane);
const viewWrap = el("div");
viewWrap.append(viewBtn, popover);
main.appendChild(viewWrap);
body.appendChild(el("div", { className: "dl-builder-toolbar" })).appendChild(main);
const selection = el("div", { "data-selection-bar": "", hidden: true });
const count = el("span", { className: "dl-chip is-brand", "data-selected-count": "", text: "0 selected" });
const bulkZone = el("select", { className: "dl-select", "data-bulk-zone": "", "aria-label": "Move selected cards to" });
const slot = el("span", { "data-considering-slot": "" });
const role = el("select", { className: "dl-select", "data-selection-role": "", "aria-label": "Set role for selected cards" });
const remove = el("button", { type: "button", "data-selection-remove": "", text: "Remove" });
const clear = el("button", { type: "button", "data-clear-selection": "", "aria-label": "Clear selection", text: "Clear" });
selection.append(count, bulkZone, slot, role, remove, clear);
document.querySelector(".dl-builder-toolbar").appendChild(selection);
body.appendChild(el("span", { "data-deck-count": "", text: "0/100" }));
body.appendChild(el("button", { type: "button", "data-view": "playmat", text: "Playmat" }));
body.appendChild(el("button", { type: "button", "data-view": "table", text: "Decklist" }));
body.appendChild(el("div", { id: "table-view" }));
const stage = el("div", { id: "playmat-stage" });
stage.clientWidth = 800;
stage.clientHeight = 600;
const playmatView = el("div", { id: "playmat-view" });
playmatView.appendChild(stage);
playmatView.appendChild(el("div", { id: "playmat" }));
body.appendChild(playmatView);
["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el("div", { id })));
body.appendChild(el("div", { "data-playmat-selection": "" }));
const exportMenu = el("details", { "data-export-menu": "" });
const copyBtn = el("button", { type: "button", "data-export-copy": "", text: "Copy list" });
const archBtn = el("button", { type: "button", "data-export-copy-archidekt": "", text: "Copy for Archidekt" });
const download = el("a", { "data-export-download": "", href: "/build/deck/deck-1/export.txt?format=plain", text: "Download .txt" });
const buy = el("a", { "data-export-buy": "", text: "Buy deck" });
const status = el("p", { "data-export-status": "" });
exportMenu.append(copyBtn, archBtn, download, buy, status);
body.appendChild(exportMenu);
const menu = el("div", { role: "menu", "data-deck-options": "" });
["Tags…", "New zone", "Commanders…", "Compare to meta", "Share link", "Delete deck"].forEach((label) => menu.appendChild(el("button", { type: "button", text: label })));

const copied = [];
const windowObj = {
  matchMedia() { return { matches: false, addEventListener() {} }; },
  setTimeout, clearTimeout,
  crypto: { randomUUID: () => "uuid-1" },
  DeckLabSelects: { refresh() {} },
  DeckLabExport: {
    exportText() { return "1 Sol Ring"; },
    exportArchidektText() { return "1 Sol Ring"; },
    manaPoolUrl(text) { return "https://manapool.com/add-deck?deck=" + text; }
  },
  prompt() { return null; },
  confirm() { return true; }
};
const context = {
  console, document, window: windowObj, setTimeout, clearTimeout, Event,
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  fetch(url, opts = {}) {
    const bodyText = opts.body ? JSON.parse(opts.body) : { commands: [] };
    apply(bodyText.commands || []);
    return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
  },
  navigator: { clipboard: { writeText(text) { clipboard.push(String(text)); copied.push(String(text)); return Promise.resolve(); } } },
  AbortController, URL, URLSearchParams,
  location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
  Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
  crypto: windowObj.crypto
};
context.window = windowObj;
windowObj.document = document;
await (async function boot() {
  vm.runInContext(source, vm.createContext(context), { filename: builderPath });
  vm.runInContext(consideringSource, vm.createContext(context), { filename: consideringPath });
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
})();

function flush() { return new Promise((r) => setTimeout(r, 0)); }
const before = commands.length;
group.value = "role";
group.dispatchEvent(makeEvent("change", { target: group }));
await flush();
sort.value = "name";
sort.dispatchEvent(makeEvent("change", { target: sort }));
await flush();
document.querySelector('[data-display="stacks"]').dispatchEvent(makeEvent("click", { target: document.querySelector('[data-display="stacks"]') }));
await flush();
document.querySelector('[data-density="comfortable"]').dispatchEvent(makeEvent("click"));
await flush();
const viewCommands = commands.slice(before).filter((cmd) => cmd.type === "update_view");

document.querySelector('[data-view="playmat"]').dispatchEvent(makeEvent("click"));
await flush();
const playmatPaneNow = document.querySelector("[data-view-playmat]");
const playmatShown = {
  paneHidden: !!playmatPaneNow.hidden,
  decklistHidden: !!document.querySelector("[data-view-decklist]").hidden,
  label: viewLabel.textContent,
  surfaces: playmatPaneNow.querySelectorAll("[data-surface]").length,
  outlines: !!playmatPaneNow.querySelector('[data-setting="show_zone_outlines"]'),
  dim: !!playmatPaneNow.querySelector('[data-setting="dim_inactive"]'),
  fit: !!playmatPaneNow.querySelector('[data-zoom="fit"]')
};
document.querySelector('[data-view="table"]').dispatchEvent(makeEvent("click"));
await flush();

viewBtn.focus();
viewBtn.dispatchEvent(makeEvent("click", { target: viewBtn }));
const opened = !popover.hidden;
const inner = popover.querySelector("button");
inner.focus();
document.dispatchEvent(makeEvent("keydown", { key: "Escape", target: inner }));
const afterEscape = { hidden: !!popover.hidden, focus: document.activeElement === viewBtn };
viewBtn.dispatchEvent(makeEvent("click", { target: viewBtn }));
body.dispatchEvent(makeEvent("click", { target: body }));
const afterOutside = { hidden: !!popover.hidden, focus: document.activeElement === viewBtn };

const atRest = { barHidden: !!selection.hidden, mainHidden: !!main.hidden };
const boxes = document.querySelectorAll(".dl-row-select");
boxes[0].checked = true;
boxes[0].dispatchEvent(makeEvent("change", { target: boxes[0] }));
const one = { barHidden: !!selection.hidden, mainHidden: !!main.hidden, count: count.textContent };
boxes[1].checked = true;
boxes[1].dispatchEvent(makeEvent("change", { target: boxes[1] }));
const mark = commands.length;
bulkZone.value = "zone-ramp";
bulkZone.dispatchEvent(makeEvent("change", { target: bulkZone }));
await flush();
const moved = commands.slice(mark);
boxes.forEach((box) => { box.checked = false; });
document.querySelectorAll(".dl-row-select").forEach((box, index) => {
  if (index > 1) return;
  box.checked = true;
  box.dispatchEvent(makeEvent("change", { target: box }));
});
const roleMark = commands.length;
role.value = "draw";
role.dispatchEvent(makeEvent("change", { target: role }));
await flush();
const roles = commands.slice(roleMark);
const consider = document.querySelector("[data-move-considering]");
const considerMark = commands.length;
consider.dispatchEvent(makeEvent("click", { target: consider }));
await flush();
const considered = commands.slice(considerMark);
document.querySelectorAll(".dl-row-select").forEach((box) => {
  box.checked = true;
  box.dispatchEvent(makeEvent("change", { target: box }));
});
const removeMark = commands.length;
remove.dispatchEvent(makeEvent("click", { target: remove }));
await flush();
const removed = commands.slice(removeMark);
clear.dispatchEvent(makeEvent("click", { target: clear }));
const cleared = { barHidden: !!selection.hidden, mainHidden: !!main.hidden, count: count.textContent };

const destMark = commands.length;
pill.dispatchEvent(makeEvent("click", { target: pill }));
const destItems = [...destMenu.querySelectorAll("[role=menuitem]")].map((item) => item.textContent);
const ramp = [...destMenu.querySelectorAll("[role=menuitem]")].find((item) => item.textContent === "Ramp");
ramp.dispatchEvent(makeEvent("click", { target: ramp }));
await flush();
const destination = {
  opened: !destMenu.hidden || destItems.length > 0,
  items: destItems,
  value: addZone.value,
  pill: pillName.textContent,
  hint: hint.textContent
};

const downloadEvent = makeEvent("click", { target: download });
download.dispatchEvent(downloadEvent);
copyBtn.dispatchEvent(makeEvent("click", { target: copyBtn }));
await flush();
await flush();

console.log(JSON.stringify({
  label: viewLabel.textContent,
  viewCommands,
  displayPressed: document.querySelector('[data-display="stacks"]').getAttribute("aria-pressed"),
  densityPressed: document.querySelector('[data-density="comfortable"]').getAttribute("aria-pressed"),
  playmatShown,
  opened,
  afterEscape,
  afterOutside,
  atRest,
  one,
  moved,
  roles,
  removed,
  considered,
  considerLabel: consider.textContent,
  cleared,
  destination,
  copied,
  downloadPrevented: !!downloadEvent.defaultPrevented,
  menuLabels: [...menu.querySelectorAll("button")].map((item) => item.textContent)
}));
