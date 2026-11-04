// Run: node --test tests/js/goldfish_metrics.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync('src/sabermetrics/ui/static/deck-lab-goldfish.js', 'utf8');
const context = {
  document: {
    querySelector: () => null,
    createElement: () => ({ style: {}, children: [], setAttribute() {}, appendChild(child) { this.children.push(child); } })
  }
};
vm.runInNewContext(source.replace('if (!root) return;', 'if (!root) { globalThis.metrics = { formatValue, metricRows, renderCurve }; return; }'), context);
const p = { value: 0.4, numerator: 40, denominator: 100, ci_low: 0.31, ci_high: 0.5 };
test('actual simulator proportion renders counts, percentage and interval together', () => {
  assert.equal(context.metrics.formatValue(p), '40/100 · 40% · 95% CI 31%–50%');
  assert.equal(context.metrics.metricRows({ starts: p }).length, 1);
  assert.equal(context.metrics.formatValue({ ...p, value: null, denominator: 0 }), 'Not measured (no exercised trials)');
});
test('actual simulator turn proportions produce visible curve values', () => {
  const curve = context.metrics.renderCurve({ key: 'engine_active_by_turn', value: [{ turn: 1, ...p }, { turn: 2, ...p, value: 0.55 }] });
  assert.equal(curve.children[0].children[0].textContent, '40%');
  assert.equal(curve.children[1].children[0].textContent, '55%');
  assert.equal(curve.children[0].children[1].style.height, '40%');
});
