// Computed layout for D01 amendment 1. Loads the real builder stylesheets.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { launchChromiumOrSkip } from "./browser_probe.mjs";

const [builderHtml, staticDir] = process.argv.slice(2);


const raw = fs.readFileSync(builderHtml, "utf8");
const start = raw.indexOf('<div class="dl-builder-toolbar"');
if (start < 0) {
  console.error("toolbar markup missing");
  process.exit(1);
}
let depth = 0;
let end = start;
for (let i = start; i < raw.length; i += 1) {
  if (raw.startsWith("<div", i)) depth += 1;
  else if (raw.startsWith("</div>", i)) {
    depth -= 1;
    if (depth === 0) {
      end = i + 6;
      break;
    }
  }
}
const toolbar = raw
  .slice(start, end)
  .replace(/\{%[\s\S]*?%\}/g, "")
  .replace(/\{\{[\s\S]*?\}\}/g, "");

const sheets = [
  "deck-lab.css",
  "deck-lab-foundation.css",
  "deck-lab-chrome.css",
  "deck-lab-list.css",
  "deck-lab-rail.css",
  "deck-lab-statusbar.css",
].map((name) => pathToFileURL(path.join(staticDir, name)).href);

const pageHtml = `<!doctype html>
<html>
<head>
<meta charset="utf-8">
${sheets.map((href) => `<link rel="stylesheet" href="${href}">`).join("\n")}
</head>
<body class="dl-workspace-body" style="margin:0">
<div class="dl-builder">
  <section class="dl-builder-main">${toolbar}</section>
  <aside class="dl-stats-rail" aria-label="Deck overview"></aside>
</div>
</body>
</html>`;

const file = path.join(os.tmpdir(), `d01-toolbar-layout-${process.pid}.html`);
fs.writeFileSync(file, pageHtml);

function measure() {
  const icon = document.querySelector(".dl-decklist-search > svg").getBoundingClientRect();
  const input = document.querySelector("#card-search");
  const inputRect = input.getBoundingClientRect();
  const textStart = inputRect.left + parseFloat(getComputedStyle(input).paddingLeft);
  const field = document.querySelector(".dl-decklist-search").getBoundingClientRect();
  const view = document.querySelector("[data-view-options]").getBoundingClientRect();
  const combobox = document.querySelector("[data-card-combobox]");
  const controls = [...document.querySelectorAll("button, input, select, textarea, a, summary")];
  let lastInside = -1;
  controls.forEach((el, index) => {
    if (combobox.contains(el)) lastInside = index;
  });
  const next = controls[lastInside + 1];
  return {
    iconRight: icon.right,
    textStart,
    gap: view.left - field.right,
    nextIsView: next === document.querySelector("[data-view-options]"),
    fieldWidth: field.width,
  };
}

const browser = await launchChromiumOrSkip();
const result = {};
try {
  for (const width of [1440, 1280]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    await page.goto(pathToFileURL(file).href);
    await page.waitForSelector("[data-view-options]");
    result[String(width)] = await page.evaluate(measure);
    await context.close();
  }
} finally {
  await browser.close();
  fs.unlinkSync(file);
}
process.stdout.write(`${JSON.stringify(result)}\n`);
