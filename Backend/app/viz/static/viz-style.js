// Pure Cytoscape styling helpers for the pipeline visualiser.
// Colors match the Frontend "Liquid Glass Ledger" system (light canvas, ink text,
// mint accent, risk ladder). UMD: window.VizStyle in the browser, exports for node.
(function (global) {
  // Categorical palette for communities — mid-saturation, distinct on a light canvas.
  const PALETTE = ['#4c6ef5', '#0d9d72', '#c46a10', '#9333ea', '#d83a30', '#0891b2',
                   '#7c3aed', '#ca8a04', '#e11d48', '#059669', '#2563eb', '#db2777'];
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  function edgeWidth(w) { return clamp(Number(w) || 1, 1, 10); }

  function communityColor(id) {
    const s = String(id === null || id === undefined ? '—' : id);
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function gnnHeatColor(score) {
    if (score === null || score === undefined) return '#a4b0c4';   // unscored → ink-4 grey
    const s = clamp(Number(score), 0, 1);
    // Risk ladder ramp: low/mint #0d9d72 → critical/red #d83a30.
    const r = Math.round(13 + s * (216 - 13));
    const g = Math.round(157 - s * (157 - 58));
    const b = Math.round(114 - s * (114 - 48));
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
          'label': 'data(label)', 'font-size': 7, 'color': '#131c2b',
          'text-outline-color': '#fafbfc', 'text-outline-width': 1.4,
          'text-valign': 'center', 'text-halign': 'center',
          'background-color': '#a4b0c4', 'width': 26, 'height': 26,
          'border-width': 1, 'border-color': 'rgba(20,33,56,0.10)' } },
      { selector: 'edge', style: {
          'width': 'data(weight)', 'line-color': '#b2bccb', 'opacity': 0.9,
          'target-arrow-shape': 'triangle', 'target-arrow-color': '#b2bccb',
          'curve-style': 'bezier', 'arrow-scale': 0.9 } },
    ];
  }

  function styleForTab(tab) {
    const base = baseStyle();
    if (tab === 'cycle')
      return base.concat([{ selector: 'node[?in_cycle]',
        style: { 'background-color': '#d83a30', 'width': 34, 'height': 34,
                 'border-color': '#a52a22' } }]);
    if (tab === 'pagerank')
      return base.concat([{ selector: 'node',
        style: { 'width': 'data(_prSize)', 'height': 'data(_prSize)', 'background-color': '#4c6ef5' } }]);
    if (tab === 'louvain')
      return base.concat([{ selector: 'node', style: { 'background-color': 'data(_communityColor)' } }]);
    if (tab === 'gnn')
      return base.concat([{ selector: 'node', style: { 'background-color': 'data(_gnnColor)' } }]);
    if (tab === 'marked')
      return base.concat([
        { selector: 'node', style: { 'opacity': 0.22 } },
        { selector: 'node[?marked]',
          style: { 'opacity': 1, 'border-width': 3, 'border-color': '#d83a30' } }]);
    return base;
  }

  const api = { edgeWidth, communityColor, gnnHeatColor, pagerankSize, baseStyle, styleForTab };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.VizStyle = api;
})(typeof window !== 'undefined' ? window : globalThis);
