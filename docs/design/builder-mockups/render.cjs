const { chromium } = require('/Users/dylan/Projects/quarry/node_modules/playwright-core');
const path = require('path');
const jobs = process.argv.slice(2).map(a => a.split(':')); // file:state:out:width
(async () => {
  const b = await chromium.launch();
  for (const [file, state, out, width] of jobs) {
    const p = await (await b.newContext({ viewport: { width: +(width || 1440), height: 900 } })).newPage();
    const errs = []; p.on('pageerror', e => errs.push(String(e)));
    await p.goto('file://' + path.resolve(file) + '?state=' + state, { waitUntil: 'networkidle', timeout: 60000 });
    await p.waitForTimeout(800);
    await p.screenshot({ path: path.resolve('shots', out + '.png') });
    console.log(out, errs.length ? errs : 'ok');
  }
  await b.close();
})();
