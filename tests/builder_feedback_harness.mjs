import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const feedbackPath = process.argv[3];
const scenario = process.argv[4] || "default";

const builderSource = fs.readFileSync(builderPath, "utf8");
const feedbackSource = fs.readFileSync(feedbackPath, "utf8");

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
    this.rows = 0;
    this.maxLength = 0;
    this.placeholder = "";
    this.returnValue = "";
    this.open = false;
    this.showModal = function () { this.open = true; };
    this.close = function () { this.open = false; };
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
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === documentElement; }
  focus() { document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = null; }
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
  id: "deck-fb-1", title: "Feedback Deck", revision: 0,
  zones: [
    { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 }
  ],
  entries: [
    { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [], oracle_id: "o-cmd", card_id: "c-cmd" },
    { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://cards.test/sol-ring.png", role: "", format_legal: true, commander_legal: true, validation_issues: [], oracle_id: "o-ring", card_id: "c-ring" },
    { id: "entry-lotus", name: "Lotus Petal", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 1, type_line: "Artifact", mana_cost: "{0}", mana_value: 0, oracle_text: "", color_identity: [], image_uri: "https://cards.test/lotus-petal.png", role: "", format_legal: true, commander_legal: true, validation_issues: [], oracle_id: "o-lotus", card_id: null },
    { id: "entry-custom", name: "Custom Card", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 2, type_line: "Creature", mana_cost: "{2}", mana_value: 2, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [], oracle_id: null, card_id: "c-custom" }
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  validation: { commander_count: 1, library_count: 3, library_target: 99, total_count: 4, legal: false, issues: [], entry_issues: {} },
  tags: [], tag_suggestions: [],
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": scenario === "shared" ? "true" : "false", "data-playmat-enabled": "true" }));
head.appendChild(el("meta", { name: "csrf-token", content: "test-csrf-token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));

const rail = el("aside", { className: "dl-stats-rail", "aria-label": "Deck overview" });
rail.appendChild(el("div", { className: "dl-builder-rail-head" }));
body.appendChild(rail);

const tableView = el("div", { id: "table-view" });
body.appendChild(tableView);

["mana-curve", "color-stats", "zone-stats"].forEach((id) => body.appendChild(el("div", { id })));
body.appendChild(el("div", { "data-tag-summary": "" }));

const store = {};
const fetches = [];
let fetchCount = 0;

async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };

await (async function boot() {
  vm.runInContext(builderSource, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetchCount++;
      fetches.push({ url: String(url), opts, count: fetchCount });
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify({
        cards: {
          "o-ring": { vote: "up", comment: "Great mana rock" },
          "o-lotus": { vote: "down", comment: null },
        },
        deck: { verdict: "good", comment: "Solid build" },
      })) });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-fb-1", origin: "http://deck.lab", pathname: "/build/deck/deck-fb-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  await flush();
})();

await (async function bootFeedback() {
  vm.runInContext(feedbackSource, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetchCount++;
      fetches.push({ url: String(url), opts, count: fetchCount });
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify({
        cards: {
          "o-ring": { vote: "up", comment: "Great mana rock" },
          "o-lotus": { vote: "down", comment: null },
        },
        deck: { verdict: "good", comment: "Solid build" },
      })) });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-fb-1", origin: "http://deck.lab", pathname: "/build/deck/deck-fb-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: feedbackPath });
  await flush();
  // Debug: check DOM state after builder boot
  const tv = document.getElementById("table-view");
  console.error("DEBUG: table-view =", !!tv, "children:", tv ? tv.children.length : -1);
  if (tv && tv.children[0]) {
    const c = tv.children[0];
    console.error("DEBUG: child tag:", c.tagName, "class:", c.className, "children:", c.children.length);
    if (c.children[0]) {
      console.error("DEBUG: grandchild tag:", c.children[0].tagName, "class:", c.children[0].className);
      if (c.children[0].children && c.children[0].children.length > 0) {
        console.error("DEBUG: great-grandchild tag:", c.children[0].children[0].tagName);
        const ggc = c.children[0].children[0];
        if (ggc.children) console.error("DEBUG: great-grandchild children:", ggc.children.length);
      }
    }
  }
})();

function walkAll(node, acc) {
  acc.push(node);
  (node.children || []).forEach((child) => walkAll(child, acc));
  return acc;
}

function getRows() {
  const tv = document.getElementById("table-view");
  if (!tv) return [];
  return walkAll(tv, []).filter((el) => el.getAttribute("data-entry-id"));
}

function getFeedbackGroup(row) {
  return row.querySelector("[data-card-feedback]");
}

function getVoteButtons(row) {
  const group = getFeedbackGroup(row);
  if (!group) return {};
  return {
    up: group.querySelector('[data-vote="up"]'),
    down: group.querySelector('[data-vote="down"]'),
    comment: group.querySelector('[data-card-comment]'),
  };
}

function clickBtn(btn) {
  if (!btn) return;
  btn.dispatchEvent(makeEvent("click", { target: btn, button: 0 }));
}

const rows = getRows();
const ringRow = rows.find((r) => r.getAttribute("data-entry-id") === "entry-ring");
const lotusRow = rows.find((r) => r.getAttribute("data-entry-id") === "entry-lotus");
const ringBtns = ringRow ? getVoteButtons(ringRow) : {};
const lotusBtns = lotusRow ? getVoteButtons(lotusRow) : {};
const nonCommanderRows = rows.filter((r) => r.getAttribute("data-entry-id") !== "entry-cmd");

let result = { scenario, rows: rows.length, nonCommanderRows: nonCommanderRows.length };

if (scenario === "vote-controls" || scenario === "default") {
  result.feedbackGroups = nonCommanderRows.filter((r) => getFeedbackGroup(r)).length;
  result.ringUpPressed = ringBtns.up ? ringBtns.up.getAttribute("aria-pressed") : null;
  result.ringDownPressed = ringBtns.down ? ringBtns.down.getAttribute("aria-pressed") : null;
  result.ringCommentHasMarker = ringBtns.comment ? ringBtns.comment.hasAttribute("data-has-comment") : null;
  result.ringCommentLabel = ringBtns.comment ? ringBtns.comment.getAttribute("aria-label") : null;
  result.ringUpLabel = ringBtns.up ? ringBtns.up.getAttribute("aria-label") : null;
  result.ringDownLabel = ringBtns.down ? ringBtns.down.getAttribute("aria-label") : null;

  if (lotusBtns.up) {
    result.lotusUpBefore = lotusBtns.up.getAttribute("aria-pressed");
    result.lotusDownBefore = lotusBtns.down.getAttribute("aria-pressed");
    clickBtn(lotusBtns.up);
    await flush();
    result.lotusUpAfter = lotusBtns.up.getAttribute("aria-pressed");
    result.lotusDownAfter = lotusBtns.down.getAttribute("aria-pressed");
  }
}

if (scenario === "toggle-clears" || scenario === "default") {
  if (ringBtns.up) {
    result.ringPressed = ringBtns.up.getAttribute("aria-pressed");
    clickBtn(ringBtns.up);
    await flush();
    result.ringAfterClear = ringBtns.up.getAttribute("aria-pressed");
  }
}

if (scenario === "verdict-section") {
  const section = document.querySelector('[data-ext-section="feedback"]');
  result.sectionExists = !!section;
  if (section) {
    result.sectionTitle = section.querySelector("h2") ? section.querySelector("h2").textContent : null;
    const verdictBtns = section.querySelectorAll("[data-verdict]");
    result.verdictBtnCount = verdictBtns.length;
    result.verdictLabels = [...verdictBtns].map((b) => b.textContent);
    result.verdictPressed = [...verdictBtns].map((b) => b.getAttribute("data-verdict") + ":" + b.getAttribute("aria-pressed"));
    const ta = section.querySelector("[data-verdict-comment]");
    result.hasTextarea = !!ta;
    const privacy = section.querySelector(".dl-fb-privacy");
    result.hasPrivacy = !!privacy;
    result.privacyText = privacy ? privacy.textContent : null;
  }
}

if (scenario === "comment-dialog") {
  result.dialogBefore = !!document.querySelector("[data-card-comment-dialog]");
  if (ringBtns.comment) {
    clickBtn(ringBtns.comment);
    await flush();
    const dialog = document.querySelector("[data-card-comment-dialog]");
    result.dialogOpen = dialog ? dialog.open : false;
    result.textareaValue = dialog ? dialog.querySelector("[data-card-comment-input]").value : null;
    const saveBtn = dialog ? dialog.querySelector('button[value="save"]') : null;
    if (saveBtn && dialog) {
      dialog.querySelector("[data-card-comment-input]").value = "Nice card!";
      clickBtn(saveBtn);
    }
    await flush();
    result.dialogAfterSave = dialog ? dialog.open : false;
    result.ringCommentHasMarker1 = ringBtns.comment.hasAttribute("data-has-comment");
  }
}

if (scenario === "revert-on-error") {
  result.note = "Revert-on-error test: use flask route test for full coverage";
}

console.log(JSON.stringify(result));
