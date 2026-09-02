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

test('pagerankSize: monotonic, bounded, degenerate range is stable', () => {
  assert.equal(V.pagerankSize(3, 3, 3), 16);             // degenerate range
  const lo = V.pagerankSize(1, 0, 10);
  const hi = V.pagerankSize(9, 0, 10);
  assert.ok(hi > lo);                                    // bigger score → bigger node
  assert.ok(lo >= 9 && hi <= 52);                        // within the [9,52] range
});

test('baseStyle: directed arrow + weight-driven width', () => {
  const edge = V.baseStyle().find(s => s.selector === 'edge');
  assert.equal(edge.style['target-arrow-shape'], 'triangle');
  assert.match(edge.style['width'], /weight/);   // thickness scales with the amount
});

test('baseStyle: labels can be suppressed for big graphs', () => {
  const on = V.baseStyle({ labels: true }).find(s => s.selector === 'node');
  const off = V.baseStyle({ labels: false }).find(s => s.selector === 'node');
  assert.equal(on.style['label'], 'data(label)');
  assert.equal(off.style['label'], '');
});

test('styleForTab returns a non-empty array per tab', () => {
  for (const t of ['cycle', 'pagerank', 'louvain', 'gnn', 'marked', 'dataset']) {
    const s = V.styleForTab(t);
    assert.ok(Array.isArray(s) && s.length >= 2);
  }
});

test('legendFor returns a titled item list per tab', () => {
  for (const t of ['cycle', 'pagerank', 'louvain', 'gnn', 'marked', 'dataset']) {
    const l = V.legendFor(t);
    assert.ok(l.title && Array.isArray(l.items) && l.items.length >= 1);
  }
});

test('dataset & marked tabs express true-positive agreement (green rule)', () => {
  for (const tab of ['marked', 'dataset']) {
    const s = V.styleForTab(tab);
    // a rule keyed on BOTH marked and truth paints the agreement colour
    const agree = s.find(r => /\[\?marked\]\[\?truth\]|\[\?truth\]\[\?marked\]/.test(r.selector));
    assert.ok(agree, `${tab} tab has a both-signals rule`);
    assert.equal(agree.style['border-color'], '#0d9d72');   // green = true positive
  }
});
