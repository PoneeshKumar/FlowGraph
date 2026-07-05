"""
FlowGraph dev tool — visualise the Neo4j graph and Louvain communities.

Pulls Account nodes + FLOWS_TO edges out of Neo4j and writes a single
self-contained HTML file: an interactive, force-directed graph where each node
is coloured by its `community_id`. Open it in any browser to *see* how the
Louvain batch clustered the money-flow graph — distinct colours are distinct
communities.

The full graph is far too big to draw (hundreds of thousands of nodes), so by
default this shows the "interesting" communities — those in a visualisable size
range — capped at a node budget. Use the flags to focus.

Usage:
    # default: interesting communities (size 3-60), up to 25 of them, <=800 nodes
    NEO4J_PASSWORD=changeme python -m tools.visualize_neo4j --open

    # one specific community by id
    NEO4J_PASSWORD=changeme python -m tools.visualize_neo4j --community 93285395866a --open

    # just the demo cluster seeded by `python -m fraud.community_detector`
    NEO4J_PASSWORD=changeme python -m tools.visualize_neo4j --prefix DEMO_LV_ --open

    # everything with a community, bounded by the node cap (may be dense)
    NEO4J_PASSWORD=changeme python -m tools.visualize_neo4j --all --max-nodes 1200 --open

Requires a running Neo4j (docker compose up neo4j) and, ideally, a prior
`python -m fraud.community_detector` run so nodes carry community_id.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from neo4j import GraphDatabase

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE

# Curated categorical palette — distinct hues that read well on a dark canvas.
_PALETTE = [
    "#4f9cff", "#ff6b6b", "#51cf66", "#ffd43b", "#cc5de8", "#ff922b",
    "#22d3ee", "#f783ac", "#a9e34b", "#748ffc", "#ffa94d", "#63e6be",
    "#e599f7", "#ff8787", "#69db7c", "#ffe066", "#9775fa", "#3bc9db",
    "#faa2c1", "#c0eb75", "#5c7cfa", "#ffc078", "#38d9a9", "#da77f2",
]
_UNASSIGNED_COLOR = "#6b7280"  # grey for nodes with no community_id


def _select_community_ids(session, args) -> list[str]:
    """Decide which community_ids to include based on the CLI flags."""
    if args.community:
        return [args.community]

    if args.all:
        rows = session.run(
            "MATCH (a:Account) WHERE a.community_id IS NOT NULL "
            "RETURN a.community_id AS cid, count(*) AS n ORDER BY n DESC"
        )
        return [r["cid"] for r in rows]

    # Default: communities whose size is in a drawable window, largest first.
    rows = session.run(
        "MATCH (a:Account) WHERE a.community_id IS NOT NULL "
        "WITH a.community_id AS cid, count(*) AS n "
        "WHERE n >= $lo AND n <= $hi "
        "RETURN cid, n ORDER BY n DESC LIMIT $k",
        lo=args.min_size, hi=args.max_size, k=args.communities,
    )
    return [r["cid"] for r in rows]


def _fetch(session, args):
    """Return (nodes, links) dicts within the chosen scope, capped at max_nodes."""
    if args.prefix:
        node_rows = session.run(
            "MATCH (a:Account) WHERE a.id STARTS WITH $p "
            "RETURN a.id AS id, a.community_id AS cid LIMIT $cap",
            p=args.prefix, cap=args.max_nodes,
        )
        nodes = [{"id": r["id"], "cid": r["cid"]} for r in node_rows]
    else:
        cids = _select_community_ids(session, args)
        if not cids:
            return [], []
        node_rows = session.run(
            "MATCH (a:Account) WHERE a.community_id IN $cids "
            "RETURN a.id AS id, a.community_id AS cid LIMIT $cap",
            cids=cids, cap=args.max_nodes,
        )
        nodes = [{"id": r["id"], "cid": r["cid"]} for r in node_rows]

    ids = [n["id"] for n in nodes]
    id_set = set(ids)
    link_rows = session.run(
        "MATCH (a:Account)-[f:FLOWS_TO]->(b:Account) "
        "WHERE a.id IN $ids AND b.id IN $ids "
        "RETURN a.id AS s, b.id AS t, f.total_amount AS amount, f.tx_count AS tx",
        ids=ids,
    )
    links = [
        {"s": r["s"], "t": r["t"], "amount": r["amount"] or 0, "tx": r["tx"] or 0}
        for r in link_rows
        if r["s"] in id_set and r["t"] in id_set
    ]
    return nodes, links


def _build_payload(nodes, links):
    """Assign colours per community and compute per-node degree for sizing."""
    # Stable community ordering by member count (biggest gets first palette hue).
    counts: dict = {}
    for n in nodes:
        counts[n["cid"]] = counts.get(n["cid"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))

    color_of: dict = {}
    communities = []
    for i, (cid, size) in enumerate(ordered):
        if cid is None:
            color = _UNASSIGNED_COLOR
            label = "(unassigned)"
        else:
            color = _PALETTE[i % len(_PALETTE)]
            label = str(cid)
        color_of[cid] = color
        communities.append({"cid": label, "color": color, "size": size})

    degree: dict = {n["id"]: 0 for n in nodes}
    for l in links:
        degree[l["s"]] = degree.get(l["s"], 0) + 1
        degree[l["t"]] = degree.get(l["t"], 0) + 1

    out_nodes = [
        {
            "id": n["id"],
            "cid": ("(unassigned)" if n["cid"] is None else str(n["cid"])),
            "color": color_of[n["cid"]],
            "deg": degree.get(n["id"], 0),
        }
        for n in nodes
    ]
    out_links = [{"s": l["s"], "t": l["t"], "amount": l["amount"], "tx": l["tx"]} for l in links]
    return out_nodes, out_links, communities


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--community", help="Focus a single community_id")
    ap.add_argument("--prefix", help="Only nodes whose id starts with this (e.g. DEMO_LV_)")
    ap.add_argument("--all", action="store_true",
                    help="All communities (bounded by --max-nodes); may be dense")
    ap.add_argument("--communities", type=int, default=25,
                    help="How many communities to show in default mode (default 25)")
    ap.add_argument("--min-size", type=int, default=3,
                    help="Default mode: skip communities smaller than this (default 3)")
    ap.add_argument("--max-size", type=int, default=60,
                    help="Default mode: skip communities bigger than this (default 60)")
    ap.add_argument("--max-nodes", type=int, default=800,
                    help="Hard cap on nodes drawn (default 800)")
    ap.add_argument("--output", default="flowgraph_graph.html",
                    help="Output HTML path (default flowgraph_graph.html)")
    ap.add_argument("--open", action="store_true", help="Open the HTML when done")
    args = ap.parse_args()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            nodes, links = _fetch(session, args)
    finally:
        driver.close()

    if not nodes:
        print("No nodes matched. Is Neo4j running, and have you run "
              "`python -m fraud.community_detector` so nodes carry community_id?\n"
              "Tip: try --all, or --prefix DEMO_LV_ after running the demo.")
        return

    out_nodes, out_links, communities = _build_payload(nodes, links)
    payload = {
        "nodes": out_nodes,
        "links": out_links,
        "communities": communities,
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "nodeCount": len(out_nodes),
            "linkCount": len(out_links),
            "communityCount": len(communities),
            "capped": len(out_nodes) >= args.max_nodes,
            "maxNodes": args.max_nodes,
        },
    }

    html = _HTML_TEMPLATE.replace("__GRAPH_DATA__", json.dumps(payload))
    out_path = Path(args.output).resolve()
    out_path.write_text(html, encoding="utf-8")

    m = payload["meta"]
    print(f"Wrote {out_path}")
    print(f"  {m['nodeCount']} nodes · {m['linkCount']} edges · "
          f"{m['communityCount']} communities"
          + ("  (node cap hit — narrow the scope to see more per community)" if m["capped"] else ""))
    if args.open:
        webbrowser.open(out_path.as_uri())
    else:
        print(f"  open it with:  open {out_path}")


# ---------------------------------------------------------------------------
# Self-contained HTML + vanilla-JS force-directed renderer (no external deps,
# works offline via file://). __GRAPH_DATA__ is replaced with the JSON payload.
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FlowGraph · Neo4j communities</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #0b0f1a; color: #e5e7eb;
    font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    overflow: hidden; }
  #canvas { position: fixed; inset: 0; display: block; cursor: grab; }
  #canvas:active { cursor: grabbing; }
  .panel { position: fixed; background: rgba(17,24,39,.86); border: 1px solid #263041;
    border-radius: 10px; padding: 12px 14px; backdrop-filter: blur(6px); }
  #hud { top: 14px; left: 14px; max-width: 300px; }
  #hud h1 { margin: 0 0 6px; font-size: 15px; font-weight: 650; letter-spacing: .2px; }
  #hud .stat { color: #9ca3af; font-size: 12px; }
  #hud .stat b { color: #e5e7eb; font-weight: 600; }
  #hud .hint { margin-top: 8px; color: #6b7280; font-size: 11px; }
  #controls { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
  button { background: #1f2937; color: #e5e7eb; border: 1px solid #374151;
    border-radius: 7px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
  button:hover { background: #374151; }
  #legend { top: 14px; right: 14px; max-height: 78vh; overflow-y: auto; width: 220px; }
  #legend h2 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase;
    letter-spacing: .6px; color: #9ca3af; font-weight: 600; }
  .lrow { display: flex; align-items: center; gap: 8px; padding: 2px 0; cursor: pointer;
    border-radius: 4px; }
  .lrow:hover { background: rgba(255,255,255,.05); }
  .lrow.dim { opacity: .35; }
  .sw { width: 12px; height: 12px; border-radius: 3px; flex: 0 0 auto; }
  .lrow code { font-size: 11px; color: #cbd5e1; }
  .lrow .n { margin-left: auto; color: #6b7280; font-size: 11px; }
  #tip { position: fixed; pointer-events: none; background: #111827; border: 1px solid #374151;
    border-radius: 7px; padding: 7px 9px; font-size: 12px; display: none; max-width: 280px;
    box-shadow: 0 8px 24px rgba(0,0,0,.5); z-index: 5; }
  #tip .id { color: #93c5fd; word-break: break-all; }
  #tip .k { color: #9ca3af; }
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<div id="hud" class="panel">
  <h1>FlowGraph · communities</h1>
  <div class="stat"><b id="s-nodes"></b> accounts · <b id="s-links"></b> flows · <b id="s-comms"></b> communities</div>
  <div class="stat" id="s-cap"></div>
  <div class="stat" id="s-gen"></div>
  <div id="controls">
    <button id="btn-reheat">Re-heat</button>
    <button id="btn-labels">Labels: auto</button>
    <button id="btn-fit">Fit</button>
  </div>
  <div class="hint">scroll = zoom · drag background = pan · drag node = move · hover node/edge = details (edge thickness = $ moved) · click a legend row to isolate</div>
</div>
<div id="legend" class="panel">
  <h2>Communities</h2>
  <div id="legend-rows"></div>
</div>
<div id="tip"></div>
<script>
const DATA = __GRAPH_DATA__;
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
let W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2);
function resize() {
  W = window.innerWidth; H = window.innerHeight;
  canvas.width = W * DPR; canvas.height = H * DPR;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}
window.addEventListener('resize', () => { resize(); draw(); });
resize();

// ---- model ----
const nodes = DATA.nodes.map(n => ({...n, x: 0, y: 0, vx: 0, vy: 0, fixed: false}));
const byId = new Map(nodes.map(n => [n.id, n]));
const links = DATA.links
  .map(l => ({source: byId.get(l.s), target: byId.get(l.t), amount: l.amount, tx: l.tx}))
  .filter(l => l.source && l.target);

// seed positions clustered per community so the layout converges fast and legibly
const commAngle = new Map();
DATA.communities.forEach((c, i) => commAngle.set(c.cid, (i / DATA.communities.length) * Math.PI * 2));
const R0 = Math.min(W, H) * 0.32;
for (const n of nodes) {
  const a = commAngle.has(n.cid) ? commAngle.get(n.cid) : Math.random() * Math.PI * 2;
  const jitter = 60 + Math.random() * 40;
  n.x = W / 2 + Math.cos(a) * R0 + (Math.random() - 0.5) * jitter;
  n.y = H / 2 + Math.sin(a) * R0 + (Math.random() - 0.5) * jitter;
}
const radius = n => 3 + Math.sqrt(n.deg) * 1.7;
// Edge thickness reflects total_amount moved on that corridor. Real transaction
// data spans a huge range (cents to a single aggregated outlier in the billions),
// so normalising against the raw max crushes every normal edge to near-zero
// width. Instead, anchor the scale to the 10th/90th percentile of amounts seen —
// that band gets the full visible width range, and anything beyond it clamps
// rather than compressing the middle of the distribution into invisibility.
function _percentile(sortedArr, p) {
  if (!sortedArr.length) return 0;
  return sortedArr[Math.min(sortedArr.length - 1, Math.floor(p * sortedArr.length))];
}
const _amountsSorted = links.map(l => l.amount || 0).sort((a, b) => a - b);
const _logLo = Math.log1p(_percentile(_amountsSorted, 0.10));
const _logHi = Math.max(_logLo + 0.01, Math.log1p(_percentile(_amountsSorted, 0.90)));
const edgeWidth = l => {
  const t = (Math.log1p(l.amount || 0) - _logLo) / (_logHi - _logLo);
  return 0.7 + Math.max(0, Math.min(1, t)) * 6.3; // 0.7px (bottom decile) -> 7px (top decile+)
};
const fmtUsd = cents => '$' + (cents / 100).toLocaleString(undefined, {maximumFractionDigits: 0});

// ---- force simulation: Fruchterman-Reingold, temperature-capped ----
// Repulsion k^2/d diverges as nodes approach (they can never collapse to a
// point); displacement is normalised and capped by a cooling temperature (they
// can never explode). Gravity is a gentle centre pull so disconnected
// components stay on screen — repulsion keeps them from imploding regardless.
const AREA = W * H;
const K = 0.9 * Math.sqrt(AREA / Math.max(nodes.length, 1)); // ideal node spacing
const GRAVITY = 0.06, REPULSE_CUTOFF = 700;
let temp = Math.min(W, H) / 8;
function step() {
  if (temp < 0.4) return false;
  for (const p of nodes) { p.dx = 0; p.dy = 0; }
  const n = nodes.length;
  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y; let d = Math.hypot(dx, dy);
      if (d < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d = 0.5; }
      if (d > REPULSE_CUTOFF) continue;
      const f = (K * K) / d, ux = dx / d, uy = dy / d;   // repulsion
      a.dx += ux * f; a.dy += uy * f; b.dx -= ux * f; b.dy -= uy * f;
    }
  }
  for (const l of links) {                                // attraction along edges
    const a = l.source, b = l.target;
    let dx = a.x - b.x, dy = a.y - b.y; const d = Math.hypot(dx, dy) || 0.01;
    const f = (d * d) / K, ux = dx / d, uy = dy / d;
    a.dx -= ux * f; a.dy -= uy * f; b.dx += ux * f; b.dy += uy * f;
  }
  for (const p of nodes) {
    p.dx += (W/2 - p.x) * GRAVITY;                        // gentle centre pull
    p.dy += (H/2 - p.y) * GRAVITY;
    if (p.fixed) continue;
    const d = Math.hypot(p.dx, p.dy) || 1;
    p.x += (p.dx / d) * Math.min(d, temp);                // move, capped by temperature
    p.y += (p.dy / d) * Math.min(d, temp);
  }
  temp *= 0.985;
  return true;
}

// ---- view transform (pan/zoom) ----
let scale = 1, tx = 0, ty = 0;
function toWorld(sx, sy) { return {x: (sx - tx) / scale, y: (sy - ty) / scale}; }
function fit() {
  let minX=1e9, minY=1e9, maxX=-1e9, maxY=-1e9;
  for (const n of nodes) { minX=Math.min(minX,n.x); minY=Math.min(minY,n.y); maxX=Math.max(maxX,n.x); maxY=Math.max(maxY,n.y); }
  const pad = 60; const gw = (maxX-minX)||1, gh = (maxY-minY)||1;
  scale = Math.min((W-2*pad)/gw, (H-2*pad)/gh, 2.5);
  tx = W/2 - ((minX+maxX)/2) * scale; ty = H/2 - ((minY+maxY)/2) * scale;
}

// ---- rendering ----
let labelMode = 'auto'; // auto | all | none
let isolated = null;     // community cid to isolate, or null
let hovered = null;      // hovered node
let hoveredLink = null;  // hovered edge (only checked when no node is hovered)
function nodeVisible(n) { return isolated === null || n.cid === isolated; }
function draw() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(tx, ty); ctx.scale(scale, scale);
  // links — thickness encodes total_amount moved on that corridor (log scale)
  for (const l of links) {
    const vis = nodeVisible(l.source) && nodeVisible(l.target);
    const highlight = l === hoveredLink;
    ctx.lineWidth = (highlight ? edgeWidth(l) + 1.5 : edgeWidth(l)) / scale;
    ctx.strokeStyle = highlight ? 'rgba(255,255,255,0.9)'
      : vis ? 'rgba(148,163,184,0.32)' : 'rgba(148,163,184,0.05)';
    ctx.beginPath(); ctx.moveTo(l.source.x, l.source.y); ctx.lineTo(l.target.x, l.target.y); ctx.stroke();
  }
  // nodes
  for (const n of nodes) {
    const vis = nodeVisible(n);
    ctx.globalAlpha = vis ? 1 : 0.12;
    ctx.beginPath(); ctx.arc(n.x, n.y, radius(n), 0, Math.PI*2);
    ctx.fillStyle = n.color; ctx.fill();
    if (n === hovered) { ctx.lineWidth = 2/scale; ctx.strokeStyle = '#fff'; ctx.stroke(); }
  }
  ctx.globalAlpha = 1;
  // labels
  if (labelMode !== 'none') {
    ctx.fillStyle = '#e5e7eb'; ctx.font = `${11/scale}px sans-serif`;
    ctx.textAlign = 'center';
    for (const n of nodes) {
      if (!nodeVisible(n)) continue;
      const big = n.deg >= 4;
      if (labelMode === 'all' || (labelMode === 'auto' && big) || n === hovered) {
        const short = n.id.length > 14 ? n.id.slice(0, 12) + '…' : n.id;
        ctx.fillText(short, n.x, n.y - radius(n) - 3/scale);
      }
    }
  }
  ctx.restore();
}

let fitted = false;
function frame() {
  const moving = step();
  draw();
  if (moving) requestAnimationFrame(frame);
  else if (!fitted) { fitted = true; fit(); draw(); }  // frame the graph once it settles
}

// ---- interaction ----
let drag = null; // {node} or {pan, startX, startY, tx0, ty0}
function pick(sx, sy) {
  const w = toWorld(sx, sy); let best = null, bestD = 14 / scale;
  for (const n of nodes) {
    if (!nodeVisible(n)) continue;
    const dx = n.x - w.x, dy = n.y - w.y; const d = Math.sqrt(dx*dx+dy*dy) - radius(n);
    if (d < bestD) { bestD = d; best = n; }
  }
  return best;
}
function pickLink(sx, sy) {
  const w = toWorld(sx, sy); let best = null, bestD = 6 / scale;
  for (const l of links) {
    if (!nodeVisible(l.source) || !nodeVisible(l.target)) continue;
    const {x: ax, y: ay} = l.source, {x: bx, y: by} = l.target;
    const dx = bx - ax, dy = by - ay; const len2 = dx*dx + dy*dy || 1;
    let t = ((w.x - ax) * dx + (w.y - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const px = ax + t * dx, py = ay + t * dy;
    const d = Math.hypot(w.x - px, w.y - py);
    if (d < bestD) { bestD = d; best = l; }
  }
  return best;
}
canvas.addEventListener('mousedown', e => {
  const n = pick(e.clientX, e.clientY);
  if (n) { drag = {node: n}; n.fixed = true; }
  else drag = {pan: true, startX: e.clientX, startY: e.clientY, tx0: tx, ty0: ty};
});
window.addEventListener('mousemove', e => {
  const tip = document.getElementById('tip');
  if (drag && drag.node) {
    const w = toWorld(e.clientX, e.clientY); drag.node.x = w.x; drag.node.y = w.y;
    temp = Math.max(temp, 12); requestAnimationFrame(frame); return;
  }
  if (drag && drag.pan) {
    tx = drag.tx0 + (e.clientX - drag.startX); ty = drag.ty0 + (e.clientY - drag.startY);
    draw(); return;
  }
  const n = pick(e.clientX, e.clientY);
  const l = n ? null : pickLink(e.clientX, e.clientY);
  if (n !== hovered || l !== hoveredLink) { hovered = n; hoveredLink = l; draw(); }
  if (n) {
    tip.style.display = 'block';
    tip.style.left = (e.clientX + 14) + 'px'; tip.style.top = (e.clientY + 14) + 'px';
    tip.innerHTML = `<div class="id">${n.id}</div>`
      + `<div><span class="k">community</span> <code>${n.cid}</code></div>`
      + `<div><span class="k">connections</span> ${n.deg}</div>`;
  } else if (l) {
    tip.style.display = 'block';
    tip.style.left = (e.clientX + 14) + 'px'; tip.style.top = (e.clientY + 14) + 'px';
    tip.innerHTML = `<div><span class="k">${l.source.id.slice(0,10)}… → ${l.target.id.slice(0,10)}…</span></div>`
      + `<div><span class="k">total moved</span> ${fmtUsd(l.amount)}</div>`
      + `<div><span class="k">transactions</span> ${l.tx}</div>`;
  } else tip.style.display = 'none';
});
window.addEventListener('mouseup', () => {
  if (drag && drag.node) drag.node.fixed = false;
  drag = null;
});
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const factor = Math.exp(-e.deltaY * 0.0015);
  const wx = (e.clientX - tx) / scale, wy = (e.clientY - ty) / scale;
  scale *= factor; tx = e.clientX - wx * scale; ty = e.clientY - wy * scale;
  draw();
}, {passive: false});

// ---- HUD + legend ----
document.getElementById('s-nodes').textContent = DATA.meta.nodeCount;
document.getElementById('s-links').textContent = DATA.meta.linkCount;
document.getElementById('s-comms').textContent = DATA.meta.communityCount;
document.getElementById('s-gen').textContent = 'as of ' + DATA.meta.generated;
if (DATA.meta.capped)
  document.getElementById('s-cap').innerHTML =
    `<span style="color:#fbbf24">node cap (${DATA.meta.maxNodes}) reached — narrow the scope for full communities</span>`;

const rows = document.getElementById('legend-rows');
DATA.communities.forEach(c => {
  const row = document.createElement('div'); row.className = 'lrow';
  row.innerHTML = `<span class="sw" style="background:${c.color}"></span>`
    + `<code>${c.cid.length > 14 ? c.cid.slice(0,12)+'…' : c.cid}</code>`
    + `<span class="n">${c.size}</span>`;
  row.addEventListener('click', () => {
    isolated = (isolated === c.cid) ? null : c.cid;
    [...rows.children].forEach((el, i) =>
      el.classList.toggle('dim', isolated !== null && DATA.communities[i].cid !== isolated));
    draw();
  });
  rows.appendChild(row);
});

document.getElementById('btn-reheat').onclick = () => { temp = Math.min(W, H) / 10; requestAnimationFrame(frame); };
document.getElementById('btn-fit').onclick = () => { fit(); draw(); };
document.getElementById('btn-labels').onclick = (e) => {
  labelMode = labelMode === 'auto' ? 'all' : labelMode === 'all' ? 'none' : 'auto';
  e.target.textContent = 'Labels: ' + labelMode; draw();
};

// run — fit() is called automatically once the layout settles (see frame())
frame();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
