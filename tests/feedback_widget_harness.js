"use strict";
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const elements = {};
const document = {
  activeElement: null,
  getElementById(id) { return elements[id] || null; },
  querySelector(sel) {
    if (sel === 'meta[name="csrf-token"]') return elements.csrf;
    return null;
  }
};

function make(id, extras) {
  const el = {
    id: id || "",
    hidden: false,
    disabled: false,
    textContent: "",
    value: "",
    checked: false,
    className: "",
    parent: null,
    tag: "div",
    attributes: {},
    listeners: {},
    focus() { document.activeElement = el; },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === "aria-expanded") this.ariaExpanded = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name)
        ? this.attributes[name]
        : null;
    },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(type, fn) {
      (this.listeners[type] || (this.listeners[type] = [])).push(fn);
    },
    dispatchEvent(event) {
      event.target = event.target || this;
      (this.listeners[event.type] || []).forEach((fn) => fn.call(this, event));
      return !event.defaultPrevented;
    }
  };
  Object.assign(el, extras || {});
  if (id) elements[id] = el;
  return el;
}

function child(parent, id, extras) {
  const el = make(id, extras);
  el.parent = parent;
  return el;
}

const launcher = make("feedback-launcher", { tag: "button", hidden: true });
const panel = make("feedback-panel");
panel.open = false;
panel.showModal = function () { panel.open = true; };
panel.close = function () {
  panel.open = false;
  panel.dispatchEvent({ type: "close", preventDefault() {} });
};
panel.setAttribute("aria-labelledby", "feedback-title");

const title = child(panel, "feedback-title", { textContent: "Tell us what broke" });
const intro = child(panel, "feedback-intro", {
  textContent: "File a private issue for the Deck Lab team."
});
const dismiss = child(panel, "feedback-close", { tag: "button" });
const form = child(panel, "feedback-form", { action: "/feedback/submit" });
form.reportValidity = function () { return true; };
form.reset = function () {
  description.value = "";
  details.value = "";
};
const fields = child(form, "feedback-fields");
const description = child(form, "feedback-description", { tag: "textarea" });
const details = child(form, "feedback-details", { tag: "textarea" });
const includeContext = child(form, "feedback-context", { tag: "input", checked: true });
const input = child(form, "feedback-image", { tag: "input" });
const capture = child(form, "feedback-capture", { tag: "button", hidden: true });
const zone = child(form, "feedback-dropzone");
const preview = child(form, "feedback-preview", { hidden: true });
const previewImage = child(preview, "feedback-preview-image", { tag: "img" });
const status = child(form, "feedback-status");
const submit = child(form, "feedback-submit", { tag: "button", textContent: "Send feedback" });
const signin = child(form, "feedback-signin", { hidden: true });
child(form, "feedback-remove", { tag: "button" });
const success = child(panel, "feedback-success", { hidden: true });
success.setAttribute("data-success-title", "Thanks for your feedback");
success.setAttribute("tabindex", "-1");
const closeSuccess = child(success, "feedback-close-success", { tag: "button", textContent: "Close" });
const another = child(success, "feedback-another", {
  tag: "button",
  textContent: "Submit another comment"
});
elements.csrf = { content: "csrf-test-token" };

let uuidN = 0;
function randomUUID() {
  uuidN += 1;
  return "00000000-0000-4000-8000-" + String(uuidN).padStart(12, "0");
}

class FormDataStub {
  constructor() { this.entries = []; }
  append(key, value) { this.entries.push([String(key), String(value)]); }
  get(key) {
    const found = this.entries.filter((pair) => pair[0] === key);
    return found.length ? found[found.length - 1][1] : null;
  }
}

child(panel, "feedback-done", { tag: "button", hidden: true, textContent: "Done" });

const fetches = [];
const sandbox = {
  document,
  navigator: {},
  crypto: { randomUUID },
  FormData: FormDataStub,
  AbortController: class {
    constructor() { this.signal = { aborted: false }; }
    abort() { this.signal.aborted = true; }
  },
  URL: { createObjectURL() { return "blob:preview"; }, revokeObjectURL() {} },
  setTimeout() { return 1; },
  clearTimeout() {},
  fetches,
  pendingFetch: null,
  console,
  assert
};
sandbox.window = {
  crypto: sandbox.crypto,
  location: { pathname: "/profile" },
  innerWidth: 390,
  innerHeight: 844,
  addEventListener() {}
};

Object.assign(sandbox, {
  launcher, panel, title, intro, dismiss, form, description, status, submit,
  success, closeSuccess, another, snap, click, visibleButtons
});
vm.createContext(sandbox);
vm.runInContext(`
function fetch(url, init) {
  fetches.push({ url: String(url), body: init && init.body });
  return new Promise(function (resolve) { pendingFetch = resolve; });
}
`, sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);

function isVisible(el) {
  let node = el;
  while (node) {
    if (node.hidden) return false;
    node = node.parent;
  }
  return true;
}
function inside(el, ancestor) {
  let node = el;
  while (node) {
    if (node === ancestor) return true;
    node = node.parent;
  }
  return false;
}
function visibleButtons() {
  return Object.values(elements)
    .filter((el) => el.tag === "button" && isVisible(el) && inside(el, panel))
    .map((el) => el.id);
}
function snap() {
  return {
    title: title.textContent,
    introHidden: intro.hidden,
    formHidden: !!form.hidden,
    successHidden: success.hidden,
    submitLabel: submit.textContent
  };
}
function click(el) {
  el.dispatchEvent({ type: "click", target: el, preventDefault() {} });
}

const result = vm.runInContext(`(${async function run() {
  async function flush() {
    for (let i = 0; i < 6; i++) await Promise.resolve();
  }
  assert.strictEqual(launcher.hidden, false);
  const idle = snap();
  click(launcher);
  assert.strictEqual(panel.open, true);
  assert.strictEqual(launcher.getAttribute("aria-expanded"), "true");
  description.value = "Cards snap back when I drag into Lands";
  form.dispatchEvent({
    type: "submit",
    preventDefault() { this.defaultPrevented = true; },
    defaultPrevented: false
  });
  const submitting = Object.assign(snap(), { status: status.textContent });
  const firstId = fetches[0].body.get("submission_id");
  assert.ok(firstId);
  pendingFetch({ ok: true, status: 200, json() { return { ok: true }; } });
  await flush();
  const succeeded = Object.assign(snap(), {
    dismissHidden: dismiss.hidden,
    focus: document.activeElement && document.activeElement.id,
    open: panel.open,
    labelledBy: panel.getAttribute("aria-labelledby"),
    visibleButtons: visibleButtons()
  });
  assert.strictEqual(succeeded.title, "Thanks for your feedback");
  assert.strictEqual(intro.hidden, true);
  assert.strictEqual(
    succeeded.visibleButtons.join(","),
    "feedback-close-success,feedback-another"
  );
  click(another);
  const returned = Object.assign(snap(), {
    focus: document.activeElement && document.activeElement.id,
    open: panel.open,
    description: description.value
  });
  assert.strictEqual(panel.open, true);
  assert.strictEqual(returned.title, "Tell us what broke");
  assert.strictEqual(returned.submitLabel, "Send feedback");
  assert.strictEqual(intro.textContent, "File a private issue for the Deck Lab team.");
  description.value = "The land pile still snaps back after retry.";
  form.dispatchEvent({
    type: "submit",
    preventDefault() { this.defaultPrevented = true; },
    defaultPrevented: false
  });
  const secondId = fetches[1].body.get("submission_id");
  pendingFetch({ ok: true, json() { return { ok: true }; } });
  await flush();
  assert.notStrictEqual(secondId, firstId);
  assert.strictEqual(panel.open, true);
  click(closeSuccess);
  const closed = panel.open === false && launcher.getAttribute("aria-expanded") === "false";
  click(launcher);
  const reopened = Object.assign(snap(), { open: panel.open });
  assert.strictEqual(reopened.title, "Tell us what broke");
  assert.strictEqual(reopened.introHidden, false);
  assert.strictEqual(reopened.formHidden, false);
  assert.strictEqual(reopened.successHidden, true);
  assert.strictEqual(dismiss.hidden, false);
  return {
    passed: true,
    idle,
    submitting,
    success: succeeded,
    another: Object.assign(returned, { freshRequest: secondId !== firstId }),
    closed,
    reopened
  };
}.toString()})()`, sandbox);

result.then((payload) => {
  console.log(JSON.stringify(payload));
}).catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
