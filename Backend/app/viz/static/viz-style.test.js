const test = require('node:test');
const assert = require('node:assert');
const V = require('./viz-style.js');

test('edgeWidth clamps to [1,10]', () => {
  assert.equal(V.edgeWidth(2.5), 2.5);
  assert.equal(V.edgeWidth(0), 1);
  assert.equal(V.edgeWidth(999), 10);
});

test('communityColor is stable and hex', () => {
  assert.equal(V.communityColor('c1'), V.communityColor('c1'));
  assert.match(V.communityColor('c1'), /^#[0-9a-fA-F]{6}$/);
});

test('gnnHeatColor: null is grey, low != high', () => {
  assert.match(V.gnnHeatColor(null), /^#[0-9a-fA-F]{6}$/);
  assert.notEqual(V.gnnHeatColor(0.1), V.gnnHeatColor(0.9));
});

test('pagerankSize maps into [18,60]', () => {
  assert.equal(V.pagerankSize(5, 0, 10), 18 + 0.5 * (60 - 18));
  assert.equal(V.pagerankSize(3, 3, 3), 30);   // degenerate range
});

test('baseStyle: directed arrow + data(weight) width', () => {
  const edge = V.baseStyle().find(s => s.selector === 'edge');
  assert.equal(edge.style['target-arrow-shape'], 'triangle');
  assert.equal(edge.style['width'], 'data(weight)');
});

test('styleForTab returns a non-empty array per tab', () => {
  for (const t of ['cycle', 'pagerank', 'louvain', 'gnn', 'marked']) {
    const s = V.styleForTab(t);
    assert.ok(Array.isArray(s) && s.length >= 2);
  }
});
