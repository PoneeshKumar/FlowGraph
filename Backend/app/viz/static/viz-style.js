// Pure Cytoscape styling helpers for the pipeline visualiser.
// Clean network-map aesthetic: soft nodes sized by importance, hairline arrowed
// edges whose thickness ∝ money moved, labels that only appear when the graph is
// small enough to read (their overlap is what turns a big graph to mush), a
// hover fade/highlight, and a risk-ladder colour ramp.
// UMD: window.VizStyle in the browser, module.exports for node --test.
(function (global) {
  const PALETTE = ['#4c6ef5', '#0d9d72', '#c46a10', '#9333ea', '#0891b2', '#e11d48',
                   '#7c3aed', '#ca8a04', '#059669', '#2563eb', '#db2777', '#0d9488'];
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  // Above this node count, per-node labels overlap into noise — hide them and
  // let hover/selection reveal identity instead.
  const LABEL_LIMIT = 55;

  function edgeWidth(w) { return clamp(Number(w) || 1, 1, 10); }

  // A node is "marked" (our detection) when it sits on a detected cycle OR its
  // GNN risk clears the cutoff — the same rule the backend uses, recomputed
  // client-side so the cutoff slider recolours instantly.
  function isMarked(data, cutoff) {
    if (data && data.in_cycle) return true;
    const s = data ? data.gnn_risk_score : null;
    return s !== null && s !== undefined && Number(s) >= cutoff;
  }

  function communityColor(id) {
    const s = String(id === null || id === undefined ? '—' : id);
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function gnnHeatColor(score) {
    if (score === null || score === undefined) return '#cbd5e1';   // unscored → faint slate
    const s = clamp(Number(score), 0, 1);
    // Risk ladder: low/mint #0d9d72 → critical/red #d83a30.
    const r = Math.round(13 + s * (216 - 13));
    const g = Math.round(157 - s * (157 - 58));
    const b = Math.round(114 - s * (114 - 48));
    return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
  }

  function pagerankSize(score, min, max) {
    if (max <= min) return 16;
    const t = clamp((Number(score) - min) / (max - min), 0, 1);
    // Perceptual: sqrt so mid-rank hubs stay visible; range 9–52px so real hubs pop.
    return 9 + Math.sqrt(t) * (52 - 9);
  }

  function baseStyle(opts) {
    const labels = !opts || opts.labels !== false;
    return [
      { selector: 'node', style: {
          'label': labels ? 'data(label)' : '',
          'font-size': 6, 'font-family': 'ui-monospace, monospace',
          'color': '#66748c', 'text-valign': 'bottom', 'text-margin-y': 2,
          'text-outline-color': '#ffffff', 'text-outline-width': 1.5,
          'min-zoomed-font-size': 8,
          'background-color': '#c3ccdb', 'background-opacity': 0.95,
          'border-width': 1, 'border-color': '#8b97ad', 'border-opacity': 0.9,
          'width': 14, 'height': 14,
          'transition-property': 'background-color, border-color, width, height, opacity',
          'transition-duration': '140ms' } },
      { selector: 'edge', style: {
          'width': 'mapData(weight, 1, 10, 0.5, 3.2)',     // hairline → thin, ∝ amount
          'line-color': 'rgba(30,44,74,0.13)',
          'target-arrow-shape': 'triangle',
          'target-arrow-color': 'rgba(30,44,74,0.22)', 'arrow-scale': 0.55,
          'curve-style': 'bezier', 'opacity': 0.7 } },
      { selector: 'node:selected', style: {
          'border-width': 3, 'border-color': '#0d9d72', 'border-opacity': 1,
          'background-color': '#0d9d72' } },
      // hover interaction — driven by classes toggled in app.js
      { selector: '.faded', style: { 'opacity': 0.08 } },
      { selector: 'node.hl', style: { 'border-width': 2.5, 'border-color': '#0d9d72', 'z-index': 99 } },
      { selector: 'edge.hl', style: {
          'line-color': '#0d9d72', 'target-arrow-color': '#0d9d72', 'opacity': 1, 'z-index': 99 } },
    ];
  }

  function styleForTab(tab, opts) {
    const base = baseStyle(opts);
    if (tab === 'cycle')
      return base.concat([{ selector: 'node[?in_cycle]',
        style: { 'background-color': '#d83a30', 'border-color': '#a52a22',
                 'width': 18, 'height': 18 } }]);
    if (tab === 'pagerank')
      return base.concat([{ selector: 'node',
        style: { 'width': 'data(_prSize)', 'height': 'data(_prSize)',
                 'background-color': '#4c6ef5', 'background-opacity': 0.82,
                 'border-color': '#3b5bdb' } }]);
    if (tab === 'louvain')
      return base.concat([{ selector: 'node',
        style: { 'background-color': 'data(_communityColor)', 'background-opacity': 0.9,
                 'border-color': 'rgba(30,44,74,0.2)' } }]);
    if (tab === 'gnn')
      return base.concat([{ selector: 'node',
        style: { 'background-color': 'data(_gnnColor)', 'background-opacity': 0.92,
                 'width': 'data(_prSize)', 'height': 'data(_prSize)',
                 'border-color': 'rgba(30,44,74,0.2)' } }]);
    if (tab === 'marked')
      // Our pipeline's marks. Green where the dataset agrees (true positive),
      // orange where it doesn't (our mark, not a labelled account).
      return base.concat([
        { selector: 'node', style: { 'opacity': 0.14 } },
        { selector: 'node[?marked]',
          style: { 'opacity': 1, 'background-color': '#fff3e6', 'border-width': 2.5,
                   'border-color': '#e8830c', 'width': 16, 'height': 16 } },
        { selector: 'node[?marked][?truth]',
          style: { 'background-color': '#e7f7ef', 'border-color': '#0d9d72' } }]);
    if (tab === 'dataset')
      // The dataset's ground-truth labels. Green where we also caught it (true
      // positive), indigo where we missed it (false negative).
      return base.concat([
        { selector: 'node', style: { 'opacity': 0.14 } },
        { selector: 'node[?truth]',
          style: { 'opacity': 1, 'background-color': '#eef0fe', 'border-width': 2.5,
                   'border-color': '#4c5fd5', 'width': 16, 'height': 16 } },
        { selector: 'node[?truth][?marked]',
          style: { 'background-color': '#e7f7ef', 'border-color': '#0d9d72' } }]);
    if (tab === 'confirmed')
      // Only the true positives: real frauds our pipeline actually caught
      // (marked AND dataset-labelled). Everything else is dimmed away.
      return base.concat([
        { selector: 'node', style: { 'opacity': 0.09 } },
        { selector: 'node[?marked][?truth]',
          style: { 'opacity': 1, 'background-color': '#e7f7ef', 'border-width': 3,
                   'border-color': '#0d9d72', 'width': 17, 'height': 17 } }]);
    if (tab === 'compare')
      // Full confusion matrix — every account coloured by (our mark × dataset truth).
      // The four selectors are mutually exclusive, so order doesn't matter.
      return base.concat([
        { selector: 'node[!marked][!truth]',                 // TN: both say clean
          style: { 'background-color': '#d7dee8', 'border-color': '#aab6c6', 'opacity': 0.55 } },
        { selector: 'node[?marked][!truth]',                 // FP: we say fraud, dataset clean
          style: { 'opacity': 1, 'background-color': '#fff3e6', 'border-width': 2.5,
                   'border-color': '#e8830c', 'width': 15, 'height': 15 } },
        { selector: 'node[!marked][?truth]',                 // FN: we say clean, dataset fraud
          style: { 'opacity': 1, 'background-color': '#eef0fe', 'border-width': 2.5,
                   'border-color': '#4c5fd5', 'width': 15, 'height': 15 } },
        { selector: 'node[?marked][?truth]',                 // TP: both say fraud
          style: { 'opacity': 1, 'background-color': '#e7f7ef', 'border-width': 2.5,
                   'border-color': '#0d9d72', 'width': 16, 'height': 16 } }]);
    return base;
  }

  // What the current tab encodes → rendered as a small legend in the corner.
  function legendFor(tab) {
    if (tab === 'cycle')
      return { title: 'Cycle detection', items: [
        { swatch: '#d83a30', label: 'in a detected cycle' },
        { swatch: '#c3ccdb', label: 'not in a cycle' }] };
    if (tab === 'pagerank')
      return { title: 'PageRank', items: [
        { swatch: '#4c6ef5', label: 'account · size = PageRank (hub weight)' }] };
    if (tab === 'louvain')
      return { title: 'Louvain communities', items: [
        { swatch: 'grad-comm', label: 'colour = community' }] };
    if (tab === 'gnn')
      return { title: 'GNN risk score', items: [
        { swatch: 'grad-risk', label: 'mint (low) → red (critical)' }] };
    if (tab === 'marked')
      return { title: 'Marked — ours', items: [
        { swatch: '#0d9d72', label: 'we flagged & dataset agrees (true positive)' },
        { swatch: '#e8830c', label: 'we flagged, not labelled (false positive)' },
        { swatch: '#e5e9f0', label: 'not flagged (dimmed)' }] };
    if (tab === 'dataset')
      return { title: 'Dataset — ground truth', items: [
        { swatch: '#0d9d72', label: 'labelled & we caught it (true positive)' },
        { swatch: '#4c5fd5', label: 'labelled, we missed it (false negative)' },
        { swatch: '#e5e9f0', label: 'not labelled (dimmed)' }] };
    if (tab === 'confirmed')
      return { title: 'Confirmed fraud', items: [
        { swatch: '#0d9d72', label: 'we detected it & the dataset confirms it' },
        { swatch: '#e5e9f0', label: 'everything else (dimmed)' }] };
    if (tab === 'compare')
      return { title: 'Compare — ours × dataset', items: [
        { swatch: '#0d9d72', label: 'both say fraud (true positive)' },
        { swatch: '#e8830c', label: 'we say fraud, dataset clean (false positive)' },
        { swatch: '#4c5fd5', label: 'we say clean, dataset fraud (false negative)' },
        { swatch: '#aab6c6', label: 'both say clean (true negative)' }] };
    return { title: '', items: [] };
  }

  const api = { edgeWidth, isMarked, communityColor, gnnHeatColor, pagerankSize,
                baseStyle, styleForTab, legendFor, LABEL_LIMIT };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.VizStyle = api;
})(typeof window !== 'undefined' ? window : globalThis);
