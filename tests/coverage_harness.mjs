import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.text = ""; }
  set textContent(value) { this.text = String(value); }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(" "); }
  appendChild(child) { this.children.push(child); return child; }
}
const window = {};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {window, document: {createElement: tag => new Element(tag)}});
const categories = ["supported", "partial", "unsupported", "inactive"];
const root = new Element("div");
window.DeckLabCoverage.render(root, {card_coverage: {version: 1, unit: "card_slots", counts: Object.fromEntries(categories.map(k => [k, 1])), cards: categories.map((status, slot) => ({slot, status, name: slot ? status : "<img src=x onerror=alert(1)>", supported_effects: ["mana"], limitations: ["combat absent"]}))}});
assert.match(root.textContent, /1 supported · 1 partial · 1 unsupported · 1 intentionally inactive/);
assert.match(root.textContent, /Unknown outcomes are not failures/);
assert.match(root.textContent, /<img src=x onerror=alert\(1\)>/);
assert.equal(root.children[1].tagName, "details");
assert.equal(root.children[1].children[0].tagName, "summary");
assert.equal(root.children[1].children[1].children.length, 4);
const legacy = new Element("div");
window.DeckLabCoverage.render(legacy, {unauthored_cards: 75});
assert.match(legacy.textContent, /unauthored 75/);
assert.match(legacy.textContent, /do not distinguish partial from missing/);
assert.doesNotMatch(legacy.textContent, /75 unsupported/);
console.log("coverage categories, accessible disclosure, safe text and legacy uncertainty passed");
