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
<aside class="dl-stats-rail">
  <div class="dl-builder-rail-head" data-rail-tabs>
    <div class="dl-tabs" role="tablist">
      <button class="dl-tab" type="button" data-rail-tab="card">Card</button>
      <button class="dl-tab" type="button" data-rail-tab="deck">Deck</button>
      <button class="dl-tab" type="button" data-rail-tab="tools">Tools</button>
    </div>
    <button class="dl-icon-button" type="button" data-card-panel-pin aria-label="Pin card preview"></button>
    <button class="dl-icon-button" type="button" data-toggle-rail="right" aria-label="Collapse Deck sidebar"></button>
  </div>
  <div class="dl-rail-panes">
    <div data-rail-pane="card">
      <aside class="dl-card-panel" data-card-panel>
        <div class="dl-card-panel-frame"><button class="dl-card-panel-image-btn" type="button" aria-label="View card image for Sol Ring"></button></div>
        <div class="dl-card-panel-meta">
          <select class="dl-select dl-role-select" aria-label="Role for Sol Ring"><option>Ramp</option><option>Draw</option></select>
        </div>
      </aside>
    </div>
    <div data-rail-pane="deck">
      <button class="dl-curve-bin" type="button">0</button>
      <button class="dl-zone-stat" type="button">Main <b>2</b></button>
      <button class="dl-icon-button dl-add-zone" type="button" aria-label="Add zone">+</button>
      <section class="dl-rail-tags">
        <h2 class="dl-eyebrow">Tags</h2>
        <div class="dl-builder-tag-summary"><span class="dl-muted">No tags added</span></div>
        <button class="dl-button is-ghost is-sm" type="button" data-tags-open>Manage</button>
      </section>
      <label class="dl-evidence-window">Window<select class="dl-select" data-evidence-window aria-label="Tournament window"><option>Last 30 days</option></select></label>
      <button class="dl-button is-primary is-sm" type="button">Run simulation</button>
    </div>
    <div data-rail-pane="tools">
      <div class="dl-odds-sentence">
        <input class="dl-field dl-inline-field" type="number" data-odds-need aria-label="Copies to see" value="1">
        <select class="dl-select" data-odds-category aria-label="Card category"><option>Lands</option></select>
        <input class="dl-field dl-inline-field" type="number" data-odds-seen aria-label="Cards seen" value="7">
      </div>
      <button class="dl-button is-primary is-sm" type="button" data-sample-draw>Draw 7</button>
      <button class="dl-button is-sm" type="button" data-sample-next>Draw a card</button>
      <button class="dl-hand-card" type="button" aria-label="Focus Sol Ring"></button>
      <div class="dl-feedback-verdict" data-feedback-verdict-group>
        <div class="dl-segments">
          <button class="dl-fb-btn dl-fb-verdict-btn" type="button">Good</button>
          <button class="dl-fb-btn dl-fb-verdict-btn" type="button">Mixed</button>
          <button class="dl-fb-btn dl-fb-verdict-btn" type="button">Bad</button>
        </div>
        <label class="dl-fb-verdict-comment-label"><textarea class="dl-field" rows="3" aria-label="Note"></textarea></label>
        <p class="dl-fb-privacy">Your feedback is private to you and the Deck Lab owner.</p>
      </div>
      <button class="dl-button" type="button" data-meta-compare-open>Compare to meta</button>
    </div>
  </div>
</aside>
</div>
<script src="${href("deck-lab-shell.js")}"></script>
</body></html>`;

const file = "/tmp/D03-rail-native.html";
fs.writeFileSync(file, html);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(pathToFileURL(file).href);
await page.waitForTimeout(200);
const native = await page.evaluate(() => {
  const probe = {};
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-999px;top:0";
  document.documentElement.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  for (const [tag, type] of [["button"], ["select"], ["input", "text"], ["input", "number"], ["textarea"]]) {
    const element = document.createElement(tag);
    if (type) element.type = type;
    root.appendChild(element);
    const style = getComputedStyle(element);
    probe[tag + (type || "")] = [style.backgroundColor, style.borderTopStyle, style.borderTopColor, style.borderRadius].join("|");
  }
  host.remove();
  const vis = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && el.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  };
  const found = [];
  for (const el of document.querySelectorAll(".dl-stats-rail button, .dl-stats-rail select, .dl-stats-rail input, .dl-stats-rail textarea")) {
    if (!vis(el)) continue;
    const style = getComputedStyle(el);
    const key = el.tagName.toLowerCase() + (el.tagName === "INPUT" ? (el.type === "number" ? "number" : "text") : "");
    const signature = [style.backgroundColor, style.borderTopStyle, style.borderTopColor, style.borderRadius].join("|");
    if (probe[key] && signature === probe[key]) found.push(el.tagName + ":" + (el.getAttribute("aria-label") || el.className || el.textContent.trim().slice(0, 24)));
  }
  return found;
});
await browser.close();
process.stdout.write(JSON.stringify({ native }));
