import fs from "node:fs";
import vm from "node:vm";

const scenario = process.argv[2];
const scriptPath = process.argv[3];
const source = fs.readFileSync(scriptPath, "utf8");

class ClassList {
  constructor(el) { this.el = el; }
  _parts() { return (this.el.className || "").split(/\s+/).filter(Boolean); }
  _set(parts) { this.el.className = [...new Set(parts)].join(" "); }
  add(...names) { this._set(this._parts().concat(names)); }
  remove(...names) { const drop = new Set(names); this._set(this._parts().filter((n) => !drop.has(n))); }
  contains(name) { return this._parts().includes(name); }
}
class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.classList = new ClassList(this);
    this.listeners = {};
    this._text = "";
    this.id = "";
    this.title = "";
    this.value = "";
    this.open = false;
    this.hidden = false;
  }
  get textContent() {
    const nested = this.children.map((child) => child.textContent).join("");
    if (this.children.length) return `${this._text || ""}${nested}`;
    return this._text;
  }
  set textContent(value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children.length = 0;
    this._text = String(value);
  }
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
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach((node) => this.appendChild(node)); }
  replaceChildren(...nodes) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children.length = 0;
    this._text = "";
    nodes.forEach((node) => this.appendChild(node));
  }
  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    return true;
  }
  click() { this.dispatchEvent({ type: "click", target: this, preventDefault() {} }); }
  showModal() { this.open = true; }
}
function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    let rest = part;
    let tag = null;
    let id = null;
    const classes = [];
    const attrs = [];
    rest = rest.replace(/^([a-zA-Z][\w-]*)/, (_, name) => { tag = name.toUpperCase(); return ""; });
    rest = rest.replace(/#([\w-]+)/g, (_, name) => { id = name; return ""; });
    rest = rest.replace(/\.([\w-]+)/g, (_, name) => { classes.push(name); return ""; });
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

function baseState() {
  return {
    id: "deck-1",
    zones: [
      { id: "zone-main", name: "Main" },
      { id: "zone-side", name: "Side Pile" }
    ],
    entries: [
      { id: "entry-cmd", oracle_id: "oracle-kinnan", name: "Kinnan", is_commander: true, role: "" },
      {
        id: "entry-ring",
        oracle_id: "oracle-ring",
        name: "Sol Ring",
        is_commander: false,
        zone_id: "zone-main",
        role: "ramp",
        quantity: 1
      }
    ]
  };
}

function diffBody() {
  return {
    window_days: 30,
    min_event_size: 16,
    denominator: 10,
    available: true,
    too_few_lists: false,
    missing_staples: [{
      oracle_id: "oracle-staple",
      card_id: "card-staple",
      name: "Staple Card",
      lists: 8,
      rate: 0.8,
      role_guess: "ramp"
    }],
    unplayed: []
  };
}

function altBody() {
  return {
    window_days: 30,
    min_event_size: 16,
    denominator: 10,
    role: "ramp",
    alternatives: [{
      oracle_id: "oracle-alt",
      card_id: "card-alt",
      name: "Alt Card",
      lists: 7,
      rate: 0.7
    }]
  };
}

async function drain() {
  for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function mount(state) {
  const html = new Element("html");
  const body = new Element("body");
  html.appendChild(body);
  const data = new Element("script");
  data.id = "deck-document-data";
  data.textContent = JSON.stringify(state);
  body.appendChild(data);
  const panel = new Element("aside");
  panel.setAttribute("data-card-panel", "");
  const slot = new Element("div");
  slot.setAttribute("data-card-panel-slot", "evidence");
  panel.appendChild(slot);
  body.appendChild(panel);
  const opener = new Element("button");
  opener.setAttribute("data-meta-compare-open", "");
  opener.textContent = "Compare to meta";
  body.appendChild(opener);
  const dialog = new Element("dialog");
  dialog.setAttribute("data-meta-compare", "");
  const host = new Element("div");
  host.setAttribute("data-meta-compare-body", "");
  dialog.appendChild(host);
  body.appendChild(dialog);
  const select = new Element("select");
  select.setAttribute("data-add-zone", "");
  select.value = "zone-side";
  body.appendChild(select);

  const calls = [];
  const commands = [];
  let readyBound = 0;
  let onRender = null;
  let absenceNext = false;
  const document = {
    documentElement: html,
    body,
    getElementById(id) { return walk(html, []).find((node) => node.id === id) || null; },
    querySelector(selector) { return queryAll(html, selector)[0] || null; },
    querySelectorAll(selector) { return queryAll(html, selector); },
    createElement(tag) { return new Element(tag); },
    addEventListener(type, fn) {
      if (type === "deck-lab:ready") readyBound += 1;
      html.addEventListener(type, fn);
    },
    dispatchEvent(event) { return html.dispatchEvent(event); }
  };
  const context = {
    console,
    document,
    fetch(url) {
      const href = String(url);
      calls.push(href);
      let payload = diffBody();
      if (href.includes("/alternatives/")) payload = altBody();
      else if (absenceNext) {
        payload = {
          window_days: 30,
          min_event_size: 16,
          denominator: 0,
          available: false,
          too_few_lists: true,
          missing_staples: [],
          unplayed: []
        };
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(JSON.parse(JSON.stringify(payload)))
      });
    },
    setTimeout(fn) { return setTimeout(fn, 0); },
    clearTimeout,
    encodeURIComponent,
    isFinite,
    Number,
    String,
    Math
  };
  context.window = {
    DeckLabBuilder: {
      shared: false,
      getState() { return state; },
      onRender(fn) { onRender = fn; return function () {}; },
      command(list) { commands.push(JSON.parse(JSON.stringify(list))); }
    },
    DeckLabCardPanel: { current() { return "entry-ring"; } },
    fetch: context.fetch
  };
  return { context, document, calls, commands, readyBound: () => readyBound, onRender: () => onRender, useAbsence() { absenceNext = true; } };
}

function percentNodes(root) {
  const start = root.documentElement || root;
  return walk(start, [])
    .filter((node) => `${node.textContent || ""} ${node.title || ""}`.includes("%"))
    .map((node) => ({ text: node.textContent || "", title: node.title || "" }));
}

function load(ui) {
  vm.createContext(ui.context);
  vm.runInContext(source, ui.context, { filename: scriptPath });
}

const scenarios = {
  async add() {
    const ui = mount(baseState());
    load(ui);
    ui.document.querySelector("[data-meta-compare-open]").click();
    await drain();
    const statement = ui.document.querySelector("[data-meta-statement]");
    const add = ui.document.querySelector("[data-meta-add]");
    add.click();
    return {
      headings: ui.document.querySelector("[data-meta-compare]").textContent,
      statement: statement ? statement.textContent : "",
      label: add.getAttribute("aria-label"),
      commands: ui.commands,
      calls: ui.calls
    };
  },
  async replace() {
    const ui = mount(baseState());
    load(ui);
    const button = ui.document.querySelector("[data-replace-with]");
    button.click();
    await drain();
    const option = ui.document.querySelector("[data-replace-option]");
    option.click();
    return {
      label: button.getAttribute("aria-label"),
      option: option.getAttribute("aria-label"),
      statement: ui.document.querySelector("[data-meta-statement]").textContent,
      commands: ui.commands,
      calls: ui.calls
    };
  },
  async rates() {
    const ui = mount(baseState());
    load(ui);
    ui.document.querySelector("[data-meta-compare-open]").click();
    ui.document.querySelector("[data-replace-with]").click();
    await drain();
    const rated = percentNodes(ui.document);
    ui.useAbsence();
    ui.document.querySelector("[data-meta-compare-open]").click();
    await drain();
    const dialog = ui.document.querySelector("[data-meta-compare]");
    return {
      elements: rated,
      absence: dialog.textContent,
      absencePercents: percentNodes(dialog).length
    };
  },
  async late() {
    const ui = mount(baseState());
    ui.document.dispatchEvent({ type: "deck-lab:ready" });
    const boundBefore = ui.readyBound();
    load(ui);
    const button = ui.document.querySelector("[data-replace-with]");
    return {
      text: button ? button.textContent : "",
      label: button ? button.getAttribute("aria-label") : "",
      tag: button ? button.tagName : "",
      readyBound: ui.readyBound() - boundBefore,
      rendered: Boolean(button)
    };
  }
};

const run = scenarios[scenario];
if (!run) {
  console.error(`unknown scenario ${scenario}`);
  process.exit(1);
}
const payload = await run();
console.log(JSON.stringify(payload));
