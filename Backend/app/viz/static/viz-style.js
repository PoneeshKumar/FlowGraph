// Pure Cytoscape styling helpers for the pipeline visualiser.
// UMD: attaches to window.VizStyle in the browser, exports for `node --test`.
(function (global) {
  const PALETTE = ['#38bdf8', '#a78bfa', '#f472b6', '#34d399', '#fbbf24', '#fb7185',
                   '#60a5fa', '#c084fc', '#4ade80', '#f59e0b', '#2dd4bf', '#e879f9'];
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  function edgeWidth(w) { return clamp(Number(w) || 1, 1, 10); }

  function communityColor(id) {
    const s = String(id === null || id === undefined ? '—' : id);
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function gnnHeatColor(score) {
    if (score === null || score === undefined) return '#475569';   // unscored → grey
    const s = clamp(Number(score), 0, 1);
    const r = Math.round(56 + s * (239 - 56));     // green → red ramp
    const g = Math.round(189 - s * (189 - 68));
    const b = Math.round(120 - s * (120 - 68));
    return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
  }

  function pagerankSize(score, min, max) {
    if (max <= min) return 30;
    const t = clamp((Number(score) - min) / (max - min), 0, 1);
    return 18 + t * (60 - 18);
  }

  function baseStyle() {
    return [
      { selector: 'node', style: {
          'label': 'data(label)', 'font-size': 7, 'color': '#e2e8f0',
          'text-valign': 'center', 'text-halign': 'center',
          'background-color': '#64748b', 'width': 26, 'height': 26 } },
      { selector: 'edge', style: {
          'width': 'data(weight)', 'line-color': '#334155',
          'target-arrow-shape': 'triangle', 'target-arrow-color': '#334155',
          'curve-style': 'bezier', 'arrow-scale': 0.9 } },
    ];
  }

  function styleForTab(tab) {
    const base = baseStyle();
    if (tab === 'cycle')
      return base.concat([{ selector: 'node[?in_cycle]',
        style: { 'background-color': '#ef4444', 'width': 34, 'height': 34 } }]);
    if (tab === 'pagerank')
      return base.concat([{ selector: 'node',
        style: { 'width': 'data(_prSize)', 'height': 'data(_prSize)', 'background-color': '#38bdf8' } }]);
    if (tab === 'louvain')
      return base.concat([{ selector: 'node', style: { 'background-color': 'data(_communityColor)' } }]);
    if (tab === 'gnn')
      return base.concat([{ selector: 'node', style: { 'background-color': 'data(_gnnColor)' } }]);
    if (tab === 'marked')
      return base.concat([
        { selector: 'node', style: { 'opacity': 0.25 } },
        { selector: 'node[?marked]',
          style: { 'opacity': 1, 'border-width': 3, 'border-color': '#f43f5e' } }]);
    return base;
  }

  const api = { edgeWidth, communityColor, gnnHeatColor, pagerankSize, baseStyle, styleForTab };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.VizStyle = api;
})(typeof window !== 'undefined' ? window : globalThis);
