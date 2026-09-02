// FlowGraph pipeline visualiser — viewer logic.
// Opens on a whole-graph Overview (top-N accounts by importance). Tabs re-colour
// the loaded elements without refetching; community / account search drills into
// a subgraph. Labels auto-hide on big graphs, layout switches to a fast one when
// the node count is high, and hover highlights a node's flows.
const V = window.VizStyle;
let cy = null;
const state = {
  tab: 'gnn',                       // GNN is the primary classifier + best-connected view
  view: 'overview',                 // 'overview' | 'subgraph'
  elements: { nodes: [], edges: [] },
  total: null,                      // full account count, for the "N of M" readout
  cutoff: 0.74,                     // GNN mark cutoff (seeded from the model's tuned value)
  head: null,                       // cached readout header (nodes/edges/overview)
};

const $ = (id) => document.getElementById(id);

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function decorate(elements) {
  const prs = elements.nodes.map(n => n.data.pagerank_score || 0);
  const min = prs.length ? Math.min(...prs) : 0;
  const max = prs.length ? Math.max(...prs) : 0;
  for (const n of elements.nodes) {
    n.data._prSize = V.pagerankSize(n.data.pagerank_score || 0, min, max);
    n.data._communityColor = V.communityColor(n.data.community_id);
    n.data._gnnColor = V.gnnHeatColor(n.data.gnn_risk_score);
  }
  return elements;
}

function layoutFor(n) {
  if (n <= 800)
    // Strong repulsion + wide component spacing so the many small flow clusters
    // spread across the whole canvas instead of packing into a tight block.
    // randomize avoids the grid-like initial seeding; fewer iterations keeps it snappy.
    return { name: 'cose', animate: false, randomize: true, padding: 60,
             nodeRepulsion: 220000, idealEdgeLength: 85, edgeElasticity: 120,
             gravity: 0.18, numIter: 500, componentSpacing: 200,
             coolingFactor: 0.95, initialTemp: 220 };
  // Very large graph: force layout is too slow — arrange in importance rings.
  return { name: 'concentric', animate: false, padding: 40, minNodeSpacing: 10,
           concentric: (ele) => ele.data('_prSize') || 1, levelWidth: () => 6 };
}

function render(elements) {
  state.elements = decorate(elements);
  const n = elements.nodes.length;
  const labels = n <= V.LABEL_LIMIT;
  $('loading').hidden = false;
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $('cy'),
    elements: state.elements,
    style: V.styleForTab(state.tab, { labels }),
    layout: layoutFor(n),
  });
  cy.one('layoutstop', () => { $('loading').hidden = true; cy.fit(undefined, 30); });
  cy.on('tap', 'node', (e) => onNodeTap(e.target));
  wireHover(cy);
  applyMarks();                       // recompute marked from the current cutoff
  renderLegend();
  const t = elements.truncated || {};
  state.head = { nN: elements.nodes.length, eN: elements.edges.length, truncated: t };
  renderReadout();
}

// Set each node's `marked` flag from the current cutoff (cycle OR GNN ≥ cutoff),
// so moving the slider recolours the comparison tabs without refetching.
function applyMarks() {
  if (!cy) return;
  cy.batch(() => cy.nodes().forEach((n) => n.data('marked', V.isMarked(n.data(), state.cutoff))));
}

function wireHover(cy) {
  if (cy.nodes().length > 800) return;    // skip on huge graphs — too costly per move
  cy.on('mouseover', 'node', (e) => {
    const n = e.target;
    cy.elements().addClass('faded');
    n.closedNeighborhood().removeClass('faded');
    n.addClass('hl'); n.connectedEdges().addClass('hl');
  });
  cy.on('mouseout', 'node', () => cy.elements().removeClass('faded hl'));
}

function renderReadout() {
  const box = $('count-readout');
  const h = state.head || { nN: 0, eN: 0, truncated: {} };
  const t = h.truncated || {};
  let head;
  if (state.view === 'overview' && t.total) {
    head = `<b>${h.nN.toLocaleString()}</b> of ${t.total.toLocaleString()} accounts`
      + ` · <b>${h.eN.toLocaleString()}</b> flows`
      + `<span class="hint">top hubs — full graph too large to draw</span>`;
  } else {
    head = `<b>${h.nN}</b> nodes · <b>${h.eN}</b> flows`;
  }
  // On the comparison tabs, quantify agreement in the current view (live marks).
  if (['marked', 'dataset', 'confirmed', 'compare'].includes(state.tab) && cy) {
    let ours = 0, truth = 0, tp = 0, total = 0;
    cy.nodes().forEach((n) => {
      const d = n.data(); total++;
      if (d.marked) ours++;
      if (d.truth) truth++;
      if (d.marked && d.truth) tp++;
    });
    if (state.tab === 'compare') {
      const fp = ours - tp, fn = truth - tp, tn = total - tp - fp - fn;
      head += `<span class="hint">`
        + `TP <b style="color:#0a8761">${tp}</b> · FP <b style="color:#c26a09">${fp}</b> · `
        + `FN <b style="color:#3b4bc0">${fn}</b> · TN <b>${tn}</b></span>`;
    } else {
      head += `<span class="hint">ours <b>${ours}</b> · dataset <b>${truth}</b> · `
        + `agree <b style="color:#0a8761">${tp}</b>`
        + (truth ? ` · caught ${Math.round(100 * tp / truth)}%` : '') + `</span>`;
    }
  }
  box.innerHTML = head;
  box.hidden = false;
}

// --- cutoff slider -------------------------------------------------------
let _metricsTimer = null;

async function loadThresholdConfig() {
  try {
    const c = await getJSON('/viz/threshold');
    state.cutoff = c.default;
    const el = $('cutoff');
    el.min = c.min; el.max = c.max; el.value = c.default;
    $('cutoff-val').textContent = Number(c.default).toFixed(2);
  } catch (err) { console.error('threshold', err); }
}

function onCutoff(value, commit) {
  state.cutoff = Number(value);
  $('cutoff-val').textContent = state.cutoff.toFixed(2);
  applyMarks();                                 // live recolour
  cy && cy.style(V.styleForTab(state.tab, { labels: cy.nodes().length <= V.LABEL_LIMIT }));
  renderReadout();                              // view-local counts
  if (commit) {                                 // whole-graph numbers on release (debounced)
    clearTimeout(_metricsTimer);
    _metricsTimer = setTimeout(() => fetchGlobalMetrics(state.cutoff), 150);
  }
}

async function fetchGlobalMetrics(cutoff) {
  const box = $('metrics-readout');
  try {
    const m = await getJSON(`/viz/metrics?cutoff=${cutoff}`);
    if (!m.loaded) { box.hidden = true; return; }
    box.innerHTML = `whole graph @ ${cutoff.toFixed(2)} — `
      + `precision <b>${(m.precision * 100).toFixed(0)}%</b> · `
      + `recall <b>${(m.recall * 100).toFixed(0)}%</b> · `
      + `<b>${m.fp.toLocaleString()}</b> false positives · `
      + `<b>${m.tp.toLocaleString()}</b> caught`;
    box.hidden = false;
  } catch (err) { box.hidden = true; }
}

function renderLegend() {
  const el = $('legend');
  const lg = V.legendFor(state.tab);
  const swatch = (s) => {
    if (s === 'grad-risk') return `<i class="sw grad-risk"></i>`;
    if (s === 'grad-comm') return `<i class="sw grad-comm"></i>`;
    if (s === 'ring-red') return `<i class="sw ring-red"></i>`;
    return `<i class="sw" style="background:${s}"></i>`;
  };
  el.innerHTML = `<div class="lg-title">${lg.title}</div>`
    + lg.items.map(it => `<div class="lg-row">${swatch(it.swatch)}<span>${it.label}</span></div>`).join('');
  el.hidden = false;
}

async function loadOverview() {
  try {
    $('loading').hidden = false;
    const gnnTabs = ['gnn', 'marked', 'dataset', 'confirmed', 'compare'];
    const metric = gnnTabs.includes(state.tab) ? 'gnn' : 'pagerank';
    const cap = $('node-cap').value;
    const data = await getJSON(`/viz/overview?metric=${metric}&limit=${cap}`);
    state.view = 'overview';
    if (!data.nodes || !data.nodes.length) { $('loading').hidden = true; return; }
    render(data);
  } catch (err) { $('loading').hidden = true; alert(`Overview failed: ${err.message}`); }
}

async function loadSubgraph(params) {
  try {
    $('loading').hidden = false;
    const qs = new URLSearchParams(params).toString();
    const data = await getJSON(`/viz/subgraph?${qs}`);
    state.view = 'subgraph';
    if (!data.nodes || !data.nodes.length) { $('loading').hidden = true; alert('No nodes for that selection.'); return; }
    render(data);
  } catch (err) { $('loading').hidden = true; alert(`Load failed: ${err.message}`); }
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll('#tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  renderLegend();
  if (cy) {
    const labels = cy.nodes().length <= V.LABEL_LIMIT;
    cy.style(V.styleForTab(tab, { labels }));   // re-style same elements, no refetch
  }
  renderReadout();                              // refresh the comparison counts
}

function onNodeTap(node) {
  const d = node.data();
  const panel = $('inspector');
  panel.hidden = false;
  const fmt = (v) => (v === null || v === undefined ? '—' : v);
  const risk = (d.gnn_risk_score === null || d.gnn_risk_score === undefined)
    ? '—' : Number(d.gnn_risk_score).toFixed(3);
  panel.innerHTML = `<h3>${fmt(d.label) || d.id}</h3>
    <div>id: <code>${d.id}</code></div>
    <div>type: ${fmt(d.node_type)}</div>
    <div>pagerank: ${(d.pagerank_score || 0).toExponential(2)}</div>
    <div>community: <code>${fmt(d.community_id)}</code></div>
    <div>GNN risk: ${risk} (${fmt(d.gnn_risk_tier)})</div>
    <div>in cycle: ${!!d.in_cycle}</div>
    <div>marked (ours): ${!!d.marked}</div>
    <div>dataset label: ${d.truth ? '⚠ laundering — ' + fmt(d.truth_typology) : 'none'}</div>
    <button id="recenter">Explore neighbourhood</button>`;
  $('recenter').onclick = () =>
    loadSubgraph({ account_id: d.id, hops: $('hops').value });
}

async function populateCommunities() {
  try {
    const rows = await getJSON('/viz/communities?sort=risk&limit=100');
    const sel = $('community-select');
    sel.length = 1;   // keep the placeholder option
    for (const r of rows) {
      const o = document.createElement('option');
      o.value = r.community_id;
      o.textContent = `${r.community_id} · ${r.size} accts · ${r.risk_tier}`;
      sel.appendChild(o);
    }
  } catch (err) { console.error('communities', err); }
}

async function pollRun(runId) {
  const box = $('progress'), bar = $('progress-bar'), label = $('progress-label');
  box.hidden = false;
  const timer = setInterval(async () => {
    let s;
    try { s = await getJSON(`/viz/run/${runId}`); } catch (e) { return; }
    bar.style.setProperty('--p', `${Math.round((s.progress || 0) * 100)}%`);
    label.textContent = `${s.status}${s.stage ? ' · ' + s.stage : ''}`;
    if (s.status === 'completed' || s.status === 'failed') {
      clearInterval(timer);
      $('run-btn').disabled = false;
      if (s.status === 'failed') label.textContent = `failed at ${s.stage}: ${s.error}`;
      else { label.textContent = 'done'; populateCommunities(); loadOverview();
             fetchGlobalMetrics(state.cutoff); }
    }
  }, 1500);
}

function wireEvents() {
  $('tabs').addEventListener('click', (e) => {
    if (e.target.dataset.tab) setTab(e.target.dataset.tab);
  });
  $('overview-btn').addEventListener('click', loadOverview);
  $('node-cap').addEventListener('input', (e) => { $('node-cap-val').textContent = e.target.value; });
  $('node-cap').addEventListener('change', () => { if (state.view === 'overview') loadOverview(); });
  $('cutoff').addEventListener('input', (e) => onCutoff(e.target.value, false));   // live recolour
  $('cutoff').addEventListener('change', (e) => onCutoff(e.target.value, true));   // + whole-graph metrics
  $('community-select').addEventListener('change', (e) => {
    if (e.target.value) loadSubgraph({ community_id: e.target.value });
  });
  $('explore-btn').addEventListener('click', () => {
    const acct = $('search-account').value.trim();
    if (acct) loadSubgraph({ account_id: acct, hops: $('hops').value });
  });
  $('search-account').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') $('explore-btn').click();
  });
  $('run-btn').addEventListener('click', async () => {
    const btn = $('run-btn');
    btn.disabled = true;
    try {
      const { run_id } = await getJSON('/viz/run', { method: 'POST' });
      pollRun(run_id);
    } catch (err) { btn.disabled = false; alert('Run failed to start (already running?)'); }
  });
}

async function init() {
  wireEvents();
  await loadThresholdConfig();     // seed the cutoff from the model's tuned value first
  populateCommunities();
  await loadOverview();            // open on the whole-graph map, not a blank canvas
  fetchGlobalMetrics(state.cutoff);
}
init();
