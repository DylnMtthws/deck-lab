
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
    else this._set(this._parts().includes(name) ? this._parts().filter((n) => n !== name) : this._parts().concat([name]));
  }
}

function el(tag, attrs = {}) {
  const node = {
    tagName: String(tag).toUpperCase(),
    attrs: { ...attrs },
    children: [],
    parentNode: null,
    className: attrs.className || "",
    hidden: attrs.hidden != null,
    value: attrs.value || "",
    style: { _props: {}, setProperty(k, v) { this._props[k] = v; } },
    classList: null,
    dataset: {},
    _text: attrs.text || "",
    setAttribute(name, value) { this.attrs[name] = String(value); if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value); },
    getAttribute(name) { return this.attrs[name] == null ? null : String(this.attrs[name]); },
    removeAttribute(name) { delete this.attrs[name]; },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    append(...nodes) { nodes.forEach((n) => this.appendChild(typeof n === "string" ? el("span", { text: n }) : n)); },
    replaceChildren(...nodes) { this.children = []; nodes.forEach((n) => this.appendChild(n)); },
    addEventListener(type, fn) { (this.listeners || (this.listeners = {}))[type] = (this.listeners[type] || []).concat([fn]); },
    dispatchEvent(event) { event.target = event.target || this; (this.listeners && this.listeners[event.type] || []).forEach((fn) => fn(event)); },
    querySelector(sel) { return queryAll(this, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(this, sel); },
    closest(sel) { let n = this; while (n) { if (matches(n, sel)) return n; n = n.parentNode; } return null; },
    focus() { document.activeElement = this; },
  };
  node.classList = new ClassList(node);
  Object.defineProperty(node, "textContent", {
    get() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; },
    set(value) { this.children = []; this._text = value == null ? "" : String(value); },
  });
  Object.keys(attrs).forEach((key) => {
    if (key === "text" || key === "className") return;
    if (key.startsWith("data-")) node.setAttribute(key, attrs[key] === "" ? "" : attrs[key]);
    else if (key === "hidden") node.hidden = true;
    else node.setAttribute(key, attrs[key]);
  });
  return node;
}

function matches(node, selector) {
  if (selector.startsWith(".")) return (node.className || "").split(/\s+/).includes(selector.slice(1));
  if (selector.startsWith("#")) return node.attrs.id === selector.slice(1);
  if (selector.startsWith("[")) {
    const body = selector.slice(1, -1);
    const eq = body.indexOf("=");
    if (eq < 0) return Object.prototype.hasOwnProperty.call(node.attrs, body) || node.attrs[body] === "";
    const name = body.slice(0, eq), value = body.slice(eq + 1).replace(/^"|"$/g, "");
    return node.getAttribute(name) === value;
  }
  if (selector.includes("[")) {
    const tag = selector.slice(0, selector.indexOf("["));
    return node.tagName === tag.toUpperCase() && matches(node, selector.slice(selector.indexOf("[")));
  }
  return node.tagName === selector.toUpperCase();
}

function queryAll(root, selector) {
  const out = [];
  const parts = selector.split(" ");
  function walk(node) {
    if (node !== root && matches(node, parts[parts.length - 1])) {
      if (parts.length === 1 || (node.parentNode && matches(node.parentNode, parts[0])) || parts.every((part, i) => i === parts.length - 1 ? true : true)) out.push(node);
      if (parts.length === 1) { /* keep */ }
    }
    if (node !== root && matches(node, selector)) out.push(node);
    (node.children || []).forEach(walk);
  }
  if (selector.includes(" ")) {
    const [parent, child] = [parts[0], parts.slice(1).join(" ")];
    queryAll(root, parent).forEach((node) => queryAll(node, child).forEach((hit) => out.push(hit)));
    return out;
  }
  (root.children || []).forEach(walk);
  if (root !== document && matches(root, selector)) out.unshift(root);
  return [...new Set(out)];
}

const document = {
  body: el("body"),
  head: el("head"),
  activeElement: null,
  getElementById(id) { return queryAll(this.body, "#" + id)[0] || queryAll(this.head, "#" + id)[0] || null; },
  querySelector(sel) { return this.getElementById(sel.slice(1)) && sel.startsWith("#") ? this.getElementById(sel.slice(1)) : (queryAll(this.body, sel)[0] || queryAll(this.head, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(this.body, sel).concat(queryAll(this.head, sel)); },
  addEventListener() {},
  createElement(tag) { return el(tag); },
};
document.body.parentNode = document;
const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} } };


// DYL-69: drive the real builder IIFE and observe where searched cards actually land.
// "Unsorted" is deliberately NOT the first zone, so an index-based default would fail here.
const deck = {
  id: "deck-1", revision: 0,
  entries: [{ id: "e1", card_id: "kinnan", name: "Kinnan", is_commander: true, quantity: 1, zone_id: "z-ramp", type_line: "Creature", mana_value: 2, color_identity: ["G", "U"] }],
  zones: [
    { id: "z-ramp", name: "Ramp", layout_mode: "spread", x: 0, y: 0 },
    { id: "z-unsorted", name: "Unsorted", layout_mode: "spread", x: 0, y: 0 },
  ],
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact" },
  presentation: {}, tags: [], playmats: [],
};

document.head.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
const root = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "false" });
document.body.appendChild(root);
document.head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
document.body.appendChild(el("button", { id: "save-state", text: "Saved" }));
document.body.appendChild(el("div", { id: "table-view" }));
document.body.appendChild(el("div", { id: "playmat-view" }));
["zoom-label", "mana-curve", "color-stats", "zone-stats"].forEach((id) => document.body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
const combobox = el("div", { className: "dl-card-combobox", "data-card-combobox": "" });
combobox.appendChild(el("input", { "data-card-search": "", role: "combobox" }));
combobox.appendChild(el("select", { "data-add-zone": "", id: "card-add-zone" }));
combobox.appendChild(el("ul", { id: "card-search-list", "data-card-results": "", hidden: "" }));
combobox.appendChild(el("p", { id: "card-add-destination-hint", "data-add-destination-hint": "" }));
combobox.appendChild(el("div", { "data-search-status": "" }));
document.body.appendChild(combobox);
const densityButton = el("button", { "data-density": "comfortable" });
document.body.appendChild(densityButton);

const sent = [];
function makeEvent(type, extra = {}) {
  return { type, preventDefault() {}, stopPropagation() {}, ...extra };
}
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0));
}

await (async function boot() {
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch(url, opts = {}) {
      if (String(url).startsWith("/api/cards")) {
        return Promise.resolve({ ok: true, json: async () => ({ scope: "Commander identity", results: [{ id: "sol-ring", name: "Sol Ring", type_line: "Artifact" }] }) });
      }
      if (String(url).includes("/commands")) {
        const packet = JSON.parse(opts.body);
        sent.push(...packet.commands);
        deck.revision += 1;
        return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
      }
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

const input = document.querySelector("[data-card-search]");
const destination = document.querySelector("[data-add-zone]");
const results = document.querySelector("[data-card-results]");
const hint = document.querySelector("[data-add-destination-hint]");
const status = document.querySelector("[data-search-status]");
const addButton = () => results.querySelectorAll("[data-add-result]")[0];

const booted = {
  value: destination.value,
  options: destination.children.map((o) => o.textContent),
  hint: hint.textContent,
};

async function search() {
  input.value = "sol";
  input.dispatchEvent(makeEvent("input", { target: input }));
  await flush(240);
}

await search();
const defaultResults = { label: addButton().getAttribute("aria-label"), status: status.textContent };

// Switching the destination must retarget results that are already on screen.
destination.value = "z-ramp";
destination.dispatchEvent(makeEvent("change", { target: destination }));
const switched = { value: destination.value, label: addButton().getAttribute("aria-label"), hint: hint.textContent };

addButton().dispatchEvent(makeEvent("click", { target: addButton() }));
await flush(40);
const added = { commands: sent.slice(), status: status.textContent, resultsHidden: results.hidden, query: input.value };

// The chosen destination is deleted server-side; its cards move to the permanent Unsorted
// zone. A round-trip command re-renders the builder with the shortened zone list.
deck.zones = deck.zones.filter((zone) => zone.id !== "z-ramp");
deck.entries = deck.entries.map((entry) => ({ ...entry, zone_id: "z-unsorted" }));
densityButton.dispatchEvent(makeEvent("click", { target: densityButton }));
await flush(60);
const afterDelete = { value: destination.value, hint: hint.textContent, status: status.textContent };

sent.length = 0;
await search();
addButton().dispatchEvent(makeEvent("click", { target: addButton() }));
await flush(60);
const readded = { commands: sent.slice(), label: afterDelete.value };

console.log(JSON.stringify({ booted, defaultResults, switched, added, afterDelete, readded }));
