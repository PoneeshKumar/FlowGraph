// FlowGraph pipeline visualiser — viewer logic.
// Consumes the /viz API; renders subgraphs with Cytoscape. Tab switches only
// re-style the loaded elements; clicking a node re-centres on its neighbourhood.
const V = window.VizStyle;
let cy = null;
const state = { tab: 'cycle', elements: { nodes: [], edges: [] } };

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

function render(elements) {
  state.elements = decorate(elements);
  if (cy) cy.destroy();
  cy = cytoscape({
    container: document.getElementById('cy'),
    elements: state.elements,
    style: V.styleForTab(state.tab),
    layout: { name: 'cose', animate: false, nodeRepulsion: 8000, padding: 30 },
  });
  cy.on('tap', 'node', (e) => onNodeTap(e.target));
}

function showSummary(elements) {
  const panel = document.getElementById('inspector');
  panel.hidden = false;
  panel.innerHTML = `<h3>Subgraph</h3>
    <div>${elements.nodes.length} nodes · ${elements.edges.length} edges</div>
    <div style="color:#64748b">click a node for details</div>`;
}

async function loadSubgraph(params) {
  try {
    const qs = new URLSearchParams(params).toString();
    const data = await getJSON(`/viz/subgraph?${qs}`);
    if (!data.nodes || !data.nodes.length) { alert('No nodes for that selection.'); return; }
    render(data);
    showSummary(data);
  } catch (err) { alert(`Load failed: ${err.message}`); }
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll('#tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  if (cy) cy.style(V.styleForTab(tab));   // re-style same elements, no refetch
}

function onNodeTap(node) {
  const d = node.data();
  const panel = document.getElementById('inspector');
  panel.hidden = false;
  const fmt = (v) => (v === null || v === undefined ? '—' : v);
  panel.innerHTML = `<h3>${fmt(d.label) || d.id}</h3>
    <div>id: <code>${d.id}</code></div>
    <div>type: ${fmt(d.node_type)}</div>
    <div>pagerank: ${(d.pagerank_score || 0).toExponential(2)}</div>
    <div>community: ${fmt(d.community_id)}</div>
    <div>GNN risk: ${fmt(d.gnn_risk_score)} (${fmt(d.gnn_risk_tier)})</div>
    <div>in cycle: ${!!d.in_cycle}</div>
    <div>marked: ${!!d.marked}</div>
    <button id="recenter">Re-center here</button>`;
  document.getElementById('recenter').onclick = () =>
    loadSubgraph({ account_id: d.id, hops: document.getElementById('hops').value });
}

async function populateCommunities() {
  try {
    const rows = await getJSON('/viz/communities?sort=risk&limit=100');
    const sel = document.getElementById('community-select');
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
  const box = document.getElementById('progress');
  const bar = document.getElementById('progress-bar');
  const label = document.getElementById('progress-label');
  box.hidden = false;
  const timer = setInterval(async () => {
    let s;
    try { s = await getJSON(`/viz/run/${runId}`); } catch (e) { return; }
    bar.style.setProperty('--p', `${Math.round((s.progress || 0) * 100)}%`);
    label.textContent = `${s.status}${s.stage ? ' · ' + s.stage : ''}`;
    if (s.status === 'completed' || s.status === 'failed') {
      clearInterval(timer);
      document.getElementById('run-btn').disabled = false;
      if (s.status === 'failed') label.textContent = `failed at ${s.stage}: ${s.error}`;
      else { label.textContent = 'done'; populateCommunities(); }
    }
  }, 1500);
}

function wireEvents() {
  document.getElementById('tabs').addEventListener('click', (e) => {
    if (e.target.dataset.tab) setTab(e.target.dataset.tab);
  });
  document.getElementById('community-select').addEventListener('change', (e) => {
    if (e.target.value) loadSubgraph({ community_id: e.target.value });
  });
  document.getElementById('explore-btn').addEventListener('click', () => {
    const acct = document.getElementById('search-account').value.trim();
    if (acct) loadSubgraph({ account_id: acct, hops: document.getElementById('hops').value });
  });
  document.getElementById('search-account').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('explore-btn').click();
  });
  document.getElementById('run-btn').addEventListener('click', async () => {
    const btn = document.getElementById('run-btn');
    btn.disabled = true;
    try {
      const { run_id } = await getJSON('/viz/run', { method: 'POST' });
      pollRun(run_id);
    } catch (err) { btn.disabled = false; alert('Run failed to start (already running?)'); }
  });
}

wireEvents();
populateCommunities();
