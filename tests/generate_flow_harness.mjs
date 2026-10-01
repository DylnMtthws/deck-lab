import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(process.argv[2], "utf8");

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this.hidden = false;
    this.id = "";
    this.type = "";
    this.value = "";
    this.content = "";
    this.href = "";
    this.checked = false;
    this.disabled = false;
    this.open = false;
  }
  get textContent() {
    if (this.children.length) return this.children.map((child) => child.textContent).join("");
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
    if (key === "content") this.content = String(value);
    if (key === "href") this.href = String(value);
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    return Object.prototype.hasOwnProperty.call(this.attributes, key) ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) { delete this.attributes[String(name)]; }
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
  closest(selector) {
    let node = this;
    while (node && node.tagName) {
      if (matches(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  focus() {}
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    let node = this;
    while (node) {
      (node.listeners[event.type] || []).forEach((fn) => fn(event));
      if (event.cancelBubble) break;
      node = node.parentNode;
    }
    return true;
  }
}

function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    if (part === "*") return true;
    let rest = part;
    let tag = null;
    let id = null;
    const attrs = [];
    rest = rest.replace(/^([a-zA-Z][\w-]*)/, (_, found) => { tag = found.toUpperCase(); return ""; });
    rest = rest.replace(/#([\w-]+)/g, (_, found) => { id = found; return ""; });
    rest.replace(/\[([^\]]+)\]/g, (_, raw) => {
      const eq = raw.indexOf("=");
      if (eq < 0) attrs.push([raw, null]);
      else attrs.push([raw.slice(0, eq), raw.slice(eq + 1).replace(/^["']|["']$/g, "")]);
      return "";
    });
    if (tag && el.tagName !== tag) return false;
    if (id && el.id !== id) return false;
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
  walk(root, []).forEach((node) => {
    if (node !== root && matches(node, selector)) nodes.push(node);
  });
  return nodes;
}

function makeEvent(type) {
  return { type, cancelBubble: false, preventDefault() {}, stopPropagation() { this.cancelBubble = true; } };
}

function createDocument() {
  const documentElement = new Element("html");
  const head = new Element("head");
  const body = new Element("body");
  documentElement.appendChild(head);
  documentElement.appendChild(body);
  const document = {
    documentElement,
    body,
    head,
    createElement(tag) { return new Element(tag); },
    createTextNode(text) {
      const node = new Element("#text");
      node.textContent = String(text == null ? "" : text);
      return node;
    },
    getElementById(id) { return walk(documentElement, []).find((node) => node.id === id) || null; },
    querySelector(selector) { return queryAll(documentElement, selector)[0] || null; },
    querySelectorAll(selector) { return queryAll(documentElement, selector); },
  };
  head.ownerDocument = document;
  body.ownerDocument = document;
  return document;
}

function el(document, tag, attrs = {}) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  return node;
}

function buildPage(document) {
  document.head.appendChild(el(document, "meta", { name: "csrf-token", content: "csrf-test" }));
  const dialog = el(document, "dialog", { id: "new-deck-dialog" });
  dialog.showModal = () => { dialog.open = true; };
  const form = el(document, "form", { "data-new-deck-form": "" });
  const empty = el(document, "input", { type: "radio", "data-start-empty": "" });
  empty.checked = true;
  const generate = el(document, "input", { type: "radio", "data-start-generate": "" });
  const fields = el(document, "div", { "data-generate-fields": "" });
  fields.hidden = true;
  const slot = el(document, "span", { "data-generate-pack-slot": "" });
  const emptyMsg = el(document, "p", { "data-generate-empty": "", text: "No strategy pack supports this commander yet." });
  emptyMsg.hidden = true;
  const intent = el(document, "textarea", { "data-generate-intent": "" });
  const commander = el(document, "input", { "data-commander-id": "" });
  const create = el(document, "button", { type: "submit", "data-create-deck": "", text: "Create deck" });
  const submit = el(document, "button", { type: "button", "data-generate-submit": "", text: "Generate" });
  submit.hidden = true;
  submit.disabled = true;
  fields.append(slot, emptyMsg, intent);
  form.append(empty, generate, fields, commander, create, submit);
  const progress = el(document, "div", {
    "data-generate-progress": "",
    role: "status",
    "aria-live": "polite",
  });
  progress.hidden = true;
  [
    ["queued", "Queued"],
    ["running", "Building"],
    ["simulating", "Simulating"],
    ["explaining", "Explaining"],
    ["done", "Done"],
  ].forEach(([status, label]) => {
    const item = el(document, "li", { "data-generate-step": status, "data-state": "upcoming" });
    item.append(el(document, "span", { "data-generate-mark": "" }), el(document, "span", { text: label }));
    progress.appendChild(item);
  });
  const spinner = el(document, "p", { "data-generate-spinner": "", text: "Building the list…" });
  const error = el(document, "p", { "data-generate-error": "" });
  error.hidden = true;
  const retry = el(document, "button", { type: "button", "data-generate-retry": "", text: "Retry" });
  retry.hidden = true;
  const timeout = el(document, "p", { "data-generate-timeout": "" });
  timeout.hidden = true;
  const link = el(document, "a", { "data-generate-job-link": "", href: "", text: "Open the job page" });
  timeout.append(document.createTextNode("This build timed out after 5 minutes. "), link);
  progress.append(spinner, error, retry, timeout);
  dialog.append(form, progress);
  document.body.appendChild(dialog);
  return { dialog, generate, submit, retry, progress, error, spinner, timeout, link };
}

function snapshot(document) {
  const steps = {};
  document.querySelectorAll("[data-generate-step]").forEach((item) => {
    const status = item.getAttribute("data-generate-step");
    const spans = item.querySelectorAll("span");
    steps[status] = {
      state: item.getAttribute("data-state"),
      label: spans[1] ? spans[1].textContent : "",
      mark: spans[0] ? spans[0].textContent : "",
      current: item.getAttribute("aria-current"),
    };
  });
  return steps;
}

async function flush() {
  for (let i = 0; i < 40; i += 1) await Promise.resolve();
}

async function run(handle) {
  const document = createDocument();
  const ui = buildPage(document);
  const delays = [];
  const assigned = [];
  const queue = [];
  let seq = 1;
  function setTimeout(fn, ms) {
    const id = seq;
    seq += 1;
    delays.push(ms);
    queue.push({ id, fn, ms, fired: false, cleared: false });
    return id;
  }
  function clearTimeout(id) {
    const item = queue.find((timer) => timer.id === id);
    if (item) item.cleared = true;
  }
  const fetches = [];
  function fetch(url, opts = {}) {
    const record = { url: String(url), method: String((opts && opts.method) || "GET"), opts };
    fetches.push(record);
    const result = handle(record);
    return Promise.resolve({
      ok: result.status < 400,
      status: result.status,
      json: () => Promise.resolve(result.body),
    });
  }
  const windowObj = { location: { assign(url) { assigned.push(String(url)); } } };
  vm.runInContext(source, vm.createContext({
    console,
    document,
    window: windowObj,
    fetch,
    setTimeout,
    clearTimeout,
    encodeURIComponent,
    JSON,
    Promise,
    Array,
    Object,
    String,
    Error,
    Math,
  }), { filename: "deck-lab-generate.js" });

  async function fireNext() {
    const item = queue.find((timer) => !timer.fired && !timer.cleared);
    if (!item) return null;
    item.fired = true;
    item.fn();
    await flush();
    return item.ms;
  }

  ui.generate.checked = true;
  ui.generate.dispatchEvent(makeEvent("change"));
  await flush();
  const packControls = readPackControls(document);
  ui.submit.dispatchEvent(makeEvent("click"));
  await flush();
  return {
    document,
    ui,
    delays,
    assigned,
    fetches,
    fireNext,
    packControls,
    snapshot: () => snapshot(document),
  };
}

function readPackControls(document) {
  const select = document.querySelector("[data-generate-pack]");
  const button = document.querySelector("[data-generate-submit]");
  const empty = document.querySelector("[data-generate-empty]");
  return {
    selectDisabled: !!(select && select.disabled),
    buttonDisabled: !!(button && button.disabled),
    messageHidden: !empty || empty.hidden === true,
    messageText: empty ? empty.textContent : "",
  };
}

function packs(record) {
  if (record.url.startsWith("/api/generate/packs")) {
    return {
      status: 200,
      body: {
        packs: [{
          pack_id: "kinnan_basalt",
          name: "Kinnan Basalt",
          supported: true,
          commander_names: ["Kinnan, Bonder Prodigy"],
          detail: "",
        }],
        message: null,
      },
    };
  }
  return null;
}

const results = {};

{
  const statuses = ["queued", "running", "simulating", "explaining", "done"];
  let index = 0;
  const session = await run((record) => {
    const listed = packs(record);
    if (listed) return listed;
    if (record.url === "/lab/build" && record.method === "POST") {
      return { status: 202, body: { job_id: "job-1", status: "queued", status_url: "/lab/build/job-1" } };
    }
    if (record.url === "/lab/build/job-1.json") {
      const status = statuses[Math.min(index, statuses.length - 1)];
      index += 1;
      return {
        status: 200,
        body: { status, candidate_id: status === "done" ? "cand-42" : null },
      };
    }
    if (record.url === "/build/import/candidate/cand-42") {
      return { status: 200, body: { id: "deck-9", url: "/build/deck/deck-9" } };
    }
    return { status: 500, body: { error: "unexpected", url: record.url } };
  });
  const packSelect = session.document.querySelector("[data-generate-pack]");
  results.packSelect = packSelect
    ? { tag: packSelect.tagName, label: packSelect.getAttribute("aria-label") }
    : null;
  results.supportedPack = session.packControls;
  const snapshots = [];
  for (let step = 0; step < statuses.length; step += 1) {
    await session.fireNext();
    snapshots.push({ status: statuses[step], steps: session.snapshot() });
  }
  results.progress = snapshots;
}

{
  let statusFetches = 0;
  const session = await run((record) => {
    const listed = packs(record);
    if (listed) return listed;
    if (record.url === "/lab/build" && record.method === "POST") {
      return { status: 202, body: { job_id: "job-1", status: "queued", status_url: "/lab/build/job-1" } };
    }
    if (record.url === "/lab/build/job-1.json") {
      statusFetches += 1;
      return { status: 200, body: { status: "simulating", candidate_id: null } };
    }
    return { status: 500, body: { error: "unexpected" } };
  });
  let guard = 0;
  while (session.ui.timeout.hidden && guard < 200) {
    const fired = await session.fireNext();
    if (fired == null) break;
    guard += 1;
  }
  results.timeout = {
    delays: session.delays,
    timeoutVisible: session.ui.timeout.hidden === false,
    timeoutText: session.ui.timeout.textContent,
    link: session.ui.link.getAttribute("href"),
    spinnerHidden: session.ui.spinner.hidden === true,
    statusFetches,
  };
}

{
  const session = await run((record) => {
    const listed = packs(record);
    if (listed) return listed;
    if (record.url === "/lab/build" && record.method === "POST") {
      return { status: 202, body: { job_id: "job-1", status: "queued", status_url: "/lab/build/job-1" } };
    }
    if (record.url === "/lab/build/job-1.json") {
      return {
        status: 200,
        body: {
          status: "failed",
          error_code: "build_failed",
          error_detail: "The fixture corpus was unavailable.",
        },
      };
    }
    return { status: 500, body: { error: "unexpected" } };
  });
  await session.fireNext();
  const postsBefore = session.fetches.filter((item) => item.url === "/lab/build").length;
  const pendingBefore = session.delays.length;
  const shown = {
    errorText: session.ui.error.textContent,
    errorHidden: session.ui.error.hidden === true,
    retryVisible: session.ui.retry.hidden === false,
    spinnerHidden: session.ui.spinner.hidden === true,
  };
  session.ui.retry.dispatchEvent(makeEvent("click"));
  await flush();
  results.failed = {
    ...shown,
    postsBefore,
    postsAfter: session.fetches.filter((item) => item.url === "/lab/build").length,
    delaysBeforeRetry: pendingBefore,
    delaysAfterRetry: session.delays.length,
  };
}

{
  const session = await run((record) => {
    const listed = packs(record);
    if (listed) return listed;
    if (record.url === "/lab/build" && record.method === "POST") {
      return { status: 202, body: { job_id: "job-1", status: "queued", status_url: "/lab/build/job-1" } };
    }
    if (record.url === "/lab/build/job-1.json") {
      return { status: 200, body: { status: "done", candidate_id: "cand-42" } };
    }
    if (record.url === "/build/import/candidate/cand-42") {
      return { status: 200, body: { id: "deck-9", url: "/build/deck/deck-9" } };
    }
    return { status: 500, body: { error: "unexpected", url: record.url } };
  });
  await session.fireNext();
  const imported = session.fetches.find((item) => item.url.startsWith("/build/import/candidate/"));
  results.done = {
    url: imported ? imported.url : "",
    method: imported ? imported.method : "",
    csrf: imported ? imported.opts.headers["X-CSRFToken"] : "",
    contentType: imported ? imported.opts.headers["Content-Type"] : "",
    body: imported ? imported.opts.body : "",
    assigned: session.assigned,
  };
}

{
  const message = "No strategy pack supports this commander yet.";
  const session = await run((record) => {
    if (record.url.startsWith("/api/generate/packs")) {
      return { status: 200, body: { packs: [], message } };
    }
    return { status: 500, body: { error: "unexpected", url: record.url } };
  });
  results.emptyPacks = session.packControls;
}

process.stdout.write(`${JSON.stringify(results)}\n`);
