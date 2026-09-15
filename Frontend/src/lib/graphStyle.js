// Cytoscape parses colours itself and cannot resolve CSS custom properties, so the
// design-system tokens from src/index.css are mirrored here as literals.
export const COLORS = {
  neutral: '#a4b0c4',    // --ink-4
  accent: '#0d9d72',     // --accent
  critical: '#d83a30',   // --critical
  high: '#c46a10',       // --high
  medium: '#9c7c14',     // --medium
  low: '#0d9d72',        // --low
  truth: '#3b4bc0',      // dataset-label ring (matches the /viz viewer)
  label: '#3e4d66',      // --ink-2
  base: '#fafbfc',       // --bg-base
  edge: 'rgba(20,33,56,0.18)',
  arrow: 'rgba(20,33,56,0.28)',
}

export function communityColor(id) {
  if (id == null) return COLORS.neutral
  let h = 0
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return `hsl(${h % 360}, 55%, 55%)`
}

export function gnnHeat(score) {
  if (score == null) return COLORS.neutral
  if (score >= 0.85) return COLORS.critical
  if (score >= 0.65) return COLORS.high
  if (score >= 0.40) return COLORS.medium
  return COLORS.low
}

/** node size 18–54px by pagerank rank within the loaded elements */
export function prSize(score, min, max) {
  if (max <= min) return 18
  return 18 + 36 * ((score - min) / (max - min))
}

export function isMarked(d, cutoff) {
  return Boolean(d.in_cycle) || (d.gnn_risk_score ?? 0) >= cutoff
}

export function layoutFor(n) {
  if (n <= 400) return { name: 'cose-bilkent', animate: false, randomize: true, padding: 40, idealEdgeLength: 90, nodeRepulsion: 6000 }
  return { name: 'concentric', animate: false, padding: 40, minNodeSpacing: 8,
           concentric: (ele) => ele.data('_size') || 1, levelWidth: () => 6 }
}

const RISK_BG = { critical: COLORS.critical, high: COLORS.high, medium: COLORS.medium, low: COLORS.low }

/** Cytoscape stylesheet for a lens. Elements carry _size/_comm/_heat/_marked (see GraphCanvas.decorate). */
export function buildStyle(lens, { labels = true } = {}) {
  const base = [
    { selector: 'node', style: {
      label: labels ? 'data(label)' : '',
      color: COLORS.label, 'font-size': '10px', 'font-family': 'IBM Plex Mono, monospace',
      'text-valign': 'bottom', 'text-margin-y': 4,
      width: 'data(_size)', height: 'data(_size)',
      'background-color': COLORS.neutral, 'border-width': 1.5, 'border-color': COLORS.base,
    } },
    { selector: 'edge', style: {
      width: 'mapData(weight, 1, 10, 1, 5)', 'line-color': COLORS.edge,
      'target-arrow-color': COLORS.arrow, 'target-arrow-shape': 'triangle',
      'curve-style': 'bezier', 'arrow-scale': 0.8,
    } },
    { selector: '.faded', style: { opacity: 0.15 } },
    { selector: '.hl', style: { 'line-color': COLORS.accent, 'target-arrow-color': COLORS.accent, 'border-color': COLORS.accent, 'border-width': 3, opacity: 1 } },
    { selector: ':selected', style: { 'border-width': 4, 'border-color': COLORS.accent } },
  ]
  if (lens === 'risk') {
    for (const [tier, bg] of Object.entries(RISK_BG))
      base.push({ selector: `node[risk_tier = "${tier}"]`, style: { 'background-color': bg } })
  } else if (lens === 'gnn') {
    base.push({ selector: 'node', style: { 'background-color': 'data(_heat)' } })
  } else if (lens === 'community') {
    base.push({ selector: 'node', style: { 'background-color': 'data(_comm)' } })
  } else if (lens === 'marked') {
    base.push({ selector: 'node', style: { 'background-color': COLORS.neutral } })
    base.push({ selector: 'node[?_marked]', style: { 'background-color': COLORS.critical } })
    base.push({ selector: 'node[?truth]', style: { 'border-color': COLORS.truth, 'border-width': 3 } })
  } else { // pagerank: size only
    base.push({ selector: 'node', style: { 'background-color': COLORS.accent } })
  }
  return base
}
