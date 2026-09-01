// Pure Cytoscape styling helpers for the pipeline visualiser.
// Mirrors the Frontend GraphExplorer aesthetic: small thin nodes, hairline
// faint edges with tiny arrowheads, mono labels that auto-hide when zoomed out,
// risk-ladder colors. UMD: window.VizStyle in the browser, exports for node.
(function (global) {
  const PALETTE = ['#4c6ef5', '#0d9d72', '#c46a10', '#9333ea', '#0891b2', '#e11d48',
                   '#7c3aed', '#ca8a04', '#059669', '#2563eb', '#db2777', '#0d9488'];
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  function edgeWidth(w) { return clamp(Number(w) || 1, 1, 10); }

  function communityColor(id) {
    const s = String(id === null || id === undefined ? '—' : id);
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function gnnHeatColor(score) {
    if (score === null || score === undefined) return '#c3ccd8';   // unscored → faint grey
    const s = clamp(Number(score), 0, 1);
    // Risk ladder ramp: low/mint #0d9d72 → critical/red #d83a30.
    const r = Math.round(13 + s * (216 - 13));
    const g = Math.round(157 - s * (157 - 58));
    const b = Math.round(114 - s * (114 - 48));
    return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
  }

  function pagerankSize(score, min, max) {
    if (max <= min) return 16;
    const t = clamp((Number(score) - min) / (max - min), 0, 1);
    return 10 + t * (34 - 10);   // small range — 10–34px
  }

  function baseStyle() {
    return [
      { selector: 'node', style: {
          'label': 'data(label)', 'font-size': 5.5, 'font-family': 'ui-monospace, monospace',
          'color': '#8593a8', 'text-valign': 'bottom', 'text-margin-y': 2,
          'text-outline-color': '#fafbfc', 'text-outline-width': 1,
          'min-zoomed-font-size': 7,                       // labels fade in only when zoomed in
          'background-color': '#dbe1ea', 'background-opacity': 1,
          'border-width': 1, 'border-color': '#9aa6b8',
          'width': 13, 'height': 13 } },
      { selector: 'edge', style: {
          'width': 'mapData(weight, 1, 10, 0.6, 2.2)',     // hairline → thin, ∝ amount
          'line-color': 'rgba(20,33,56,0.16)',
          'target-arrow-shape': 'triangle',
          'target-arrow-color': 'rgba(20,33,56,0.24)', 'arrow-scale': 0.55,
          'curve-style': 'bezier', 'opacity': 0.85 } },
      { selector: 'node:selected', style: {
          'border-width': 2, 'border-color': '#0d9d72', 'width': 18, 'height': 18 } },
    ];
  }

  function styleForTab(tab) {
    const base = baseStyle();
    if (tab === 'cycle')
      return base.concat([{ selector: 'node[?in_cycle]',
        style: { 'background-color': '#d83a30', 'border-color': '#a52a22',
                 'width': 17, 'height': 17 } }]);
    if (tab === 'pagerank')
      return base.concat([{ selector: 'node',
        style: { 'width': 'data(_prSize)', 'height': 'data(_prSize)',
                 'background-color': '#4c6ef5', 'border-color': '#3b5bdb' } }]);
    if (tab === 'louvain')
      return base.concat([{ selector: 'node',
        style: { 'background-color': 'data(_communityColor)', 'border-color': 'rgba(20,33,56,0.18)' } }]);
    if (tab === 'gnn')
      return base.concat([{ selector: 'node',
        style: { 'background-color': 'data(_gnnColor)', 'border-color': 'rgba(20,33,56,0.18)' } }]);
    if (tab === 'marked')
      return base.concat([
        { selector: 'node', style: { 'opacity': 0.2 } },
        { selector: 'node[?marked]',
          style: { 'opacity': 1, 'background-color': '#fff', 'border-width': 2.5,
                   'border-color': '#d83a30', 'width': 15, 'height': 15 } }]);
    return base;
  }

  const api = { edgeWidth, communityColor, gnnHeatColor, pagerankSize, baseStyle, styleForTab };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.VizStyle = api;
})(typeof window !== 'undefined' ? window : globalThis);
