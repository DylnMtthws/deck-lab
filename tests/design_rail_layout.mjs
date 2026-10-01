import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const { chromium } = createRequire(import.meta.url)(
  "/Users/dylan/Projects/quarry/node_modules/playwright-core"
);

const root = fileURLToPath(new URL("../src/sabermetrics/ui/static/", import.meta.url));
const href = (name) => pathToFileURL(root + name).href;
const html = `<!doctype html>
<html><head>
<link rel="stylesheet" href="${href("deck-lab.css")}">
<link rel="stylesheet" href="${href("deck-lab-foundation.css")}">
<link rel="stylesheet" href="${href("deck-lab-rail.css")}">
</head>
<body class="dl-workspace-body">
<div class="dl-builder">
<aside class="dl-stats-rail" style="width:320px">
  <div class="dl-rail-panes">
    <div data-rail-pane="card"></div>
    <div data-rail-pane="deck"></div>
    <div data-rail-pane="tools"></div>
  </div>
</aside>
</div>
<div id="table-view"></div>
<script id="deck-document-data" type="application/json">{"id":"deck-1","entries":[],"tags":[]}</script>
<script>
window.DeckLabBuilder = {
  shared: false,
  getState: function () { return { id: "deck-1", entries: [], tags: [], zones: [] }; },
  railSection: function (id, title, opts) {
    var tab = (opts && opts.tab) || "deck";
    var pane = document.querySelector('[data-rail-pane="' + tab + '"]');
    var existing = document.querySelector('[data-ext-section="' + id + '"]');
    if (existing) return existing;
    var section = document.createElement("section");
    section.setAttribute("data-ext-section", id);
    var heading = document.createElement("h2");
    heading.className = "dl-eyebrow";
    heading.textContent = title;
    section.appendChild(heading);
    pane.appendChild(section);
    return section;
  },
  onRender: function (fn) { fn(this.getState()); }
};
</script>
<script src="${href("deck-lab-shell.js")}"></script>
<script src="${href("deck-lab-feedback.js")}"></script>
<script src="${href("deck-lab-evidence.js")}"></script>
</body></html>`;

const file = "/tmp/D03-rail-layout.html";
fs.writeFileSync(file, html);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.route("**/*", (route) => {
  const url = route.request().url();
  if (url.startsWith("file:")) return route.continue();
  return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
});
await page.goto(pathToFileURL(file).href);
await page.waitForSelector("[data-verdict-comment]");
await page.waitForSelector(".dl-evidence-window .dl-select-trigger");
const measured = await page.evaluate(() => {
  const feedback = document.querySelector('[data-ext-section="feedback"]');
  const segments = feedback.querySelector(".dl-segments");
  const area = feedback.querySelector("[data-verdict-comment]");
  const help = feedback.querySelector(".dl-fb-privacy");
  const sectionBox = feedback.getBoundingClientRect();
  const segmentBox = segments.getBoundingClientRect();
  const areaBox = area.getBoundingClientRect();
  const helpBox = help.getBoundingClientRect();
  const evidence = document.querySelector('[data-ext-section="evidence"]');
  const select = evidence.querySelector("[data-evidence-window]");
  const trigger = evidence.querySelector(".dl-select-trigger");
  const visible = trigger || select;
  const visibleBox = visible.getBoundingClientRect();
  const evidenceBox = evidence.getBoundingClientRect();
  const style = getComputedStyle(visible);
  return {
    textareaRatio: areaBox.width / sectionBox.width,
    stacked: segmentBox.bottom <= areaBox.top + 1 && areaBox.bottom <= helpBox.top + 1,
    rows: area.rows,
    windowFont: style.fontSize,
    windowRatio: visibleBox.width / evidenceBox.width,
    windowLabel: evidence.querySelector(".dl-evidence-window").textContent.replace(/\s+/g, " ").trim().slice(0, 6),
  };
});
await browser.close();
process.stdout.write(JSON.stringify(measured));
