"""Read helpers for the visualiser API.

Graph reads run over a Neo4j session *factory* (a zero-arg callable returning an
async session context) so this module never imports the app's client directly and
stays unit-testable. Marked accounts come from Postgres ``risk_flags`` (type
AGGREGATE), so ``list_marked`` takes any object exposing ``get_risk_flags``.
"""
import json
from typing import Any, Dict, List, Optional


def _edge_weight(amount) -> float:
    return max(1.0, min(10.0, float(amount or 0.0) / 100000.0))


def _tier(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.40:
        return "medium"
    return "low"


def shape_elements(nodes: List[dict], rels: List[dict]) -> Dict[str, Any]:
    """Assemble Cytoscape ``{nodes, edges, truncated}`` from raw records.

    Pure. Edge width is carried as ``weight`` (∝ total_amount); source→target is
    preserved so the client renders an arrowhead.
    """
    seen_n, out_n = set(), []
    for n in nodes:
        nid = n.get("id")
        if nid is None or nid in seen_n:
            continue
        seen_n.add(nid)
        out_n.append({"data": {
            "id": nid,
            "label": n.get("label", str(nid)[:8]),
            "node_type": n.get("node_type", "account"),
            "pagerank_score": n.get("pagerank_score", 0.0),
            "community_id": n.get("community_id"),
            "gnn_risk_score": n.get("gnn_risk_score"),
            "gnn_risk_tier": n.get("gnn_risk_tier"),
            "in_cycle": bool(n.get("in_cycle", False)),
            "marked": bool(n.get("marked", False)),          # our pipeline's mark
            "truth": bool(n.get("truth", False)),            # dataset ground-truth label
            "truth_typology": n.get("truth_typology"),
            "signals": n.get("signals"),
        }})
    seen_e, out_e = set(), []
    for r in rels:
        src, tgt = r.get("source"), r.get("target")
        if src is None or tgt is None:
            continue
        rid = f"{src}->{tgt}"
        if rid in seen_e:
            continue
        seen_e.add(rid)
        out_e.append({"data": {
            "id": rid, "source": src, "target": tgt,
            "total_amount": float(r.get("total_amount", 0.0)),
            "tx_count": r.get("tx_count", 1),
            "weight": _edge_weight(r.get("total_amount")),
        }})
    return {"nodes": out_n, "edges": out_e,
            "truncated": {"shown": len(out_n), "total": len(out_n)}}


def _shape_record(rec) -> Dict[str, Any]:
    """Turn a ``{nodes, rels}`` Neo4j record into Cytoscape elements.

    Shared by ``load_subgraph`` and ``load_overview``: unpacks node/relationship
    objects, derives ``source``/``target`` from the relationship endpoints, and
    sets the ``marked`` flag (in a cycle, or GNN risk ≥ 0.5).
    """
    from app.viz import truth, threshold
    tset = truth.truth_set()
    cutoff = threshold.model_threshold()   # the model's tuned cutoff, not a blunt 0.5
    nodes = [dict(n) for n in rec["nodes"]]
    rels = []
    for r in rec["rels"]:
        if r is None:
            continue
        rels.append({"source": r.start_node["id"], "target": r.end_node["id"], **dict(r)})
    for n in nodes:
        nid = n.get("id")
        n["marked"] = threshold.is_marked(n.get("gnn_risk_score"), n.get("in_cycle"), cutoff)
        n["truth"] = nid in tset
        n["truth_typology"] = truth.typology_of(nid)
    return shape_elements(nodes, rels)


# Property a whitelist maps to — never interpolate a raw metric name into Cypher.
_OVERVIEW_METRICS = {"pagerank": "pagerank_score", "gnn": "gnn_risk_score"}


async def load_overview(session, *, metric: str = "pagerank", limit: int = 600):
    """Whole-graph map: a *connected* backbone grown from the top accounts.

    The full graph (~514k nodes / ~1M edges) cannot be drawn in a browser. A naive
    "top-N by metric, induced edges" view is nearly all singletons — the highest
    PageRank accounts are self-transfer hubs with no counterparty in the set, so a
    force layout packs the lone dots into a grid. Instead we take the top hubs that
    *have* a real (non-self) counterparty, then pull in each one's strongest
    counterparties, yielding connected flow clusters that spread across the canvas.
    Self-loops are excluded throughout. ``total`` in ``truncated`` is the real
    account count so the UI can show "N of 514,000".
    """
    prop = _OVERVIEW_METRICS.get(metric, "pagerank_score")
    limit = min(max(int(limit), 10), 2000)
    seeds = max(30, min(limit // 4, 400))   # hubs to grow from
    fanout = 8                              # top counterparties pulled per hub
    # prop is from the whitelist above (not user text) → safe to interpolate.
    query = (
        f"MATCH (a:Account) WHERE a.{prop} IS NOT NULL "
        f"  AND EXISTS {{ MATCH (a)-[:FLOWS_TO]->(x:Account) WHERE x <> a }} "
        f"WITH a ORDER BY a.{prop} DESC LIMIT $seeds "
        "CALL (a) { "
        "  MATCH (a)-[r:FLOWS_TO]->(b:Account) WHERE b <> a "
        "  RETURN b ORDER BY r.total_amount DESC LIMIT $fanout "
        "} "
        "WITH collect(DISTINCT a) AS seed_ns, collect(DISTINCT b) AS nbr_ns "
        "WITH (seed_ns + nbr_ns)[0..$limit] AS ns "
        "UNWIND ns AS n "
        "OPTIONAL MATCH (n)-[r:FLOWS_TO]->(m:Account) WHERE m IN ns AND m <> n "
        "RETURN ns AS nodes, collect(DISTINCT r) AS rels"
    )
    async with session() as s:
        rec = await (await s.run(query, seeds=seeds, fanout=fanout, limit=limit)).single()
        total = (await (await s.run("MATCH (a:Account) RETURN count(a) AS n")).single())["n"]
    if not rec or not rec["nodes"]:
        return {"nodes": [], "edges": [], "truncated": {"shown": 0, "total": total}}
    out = _shape_record(rec)
    out["truncated"] = {"shown": len(out["nodes"]), "total": total}
    return out


async def list_communities(session, sort: str = "risk", limit: int = 100, offset: int = 0):
    from app.viz import threshold
    cutoff = threshold.model_threshold()   # match _shape_record's tuned cutoff, not a blunt 0.5
    order = "risk_score DESC" if sort == "risk" else "size DESC"
    query = (
        "MATCH (a:Account) WHERE a.community_id IS NOT NULL "
        "WITH a.community_id AS cid, count(a) AS size, "
        "     avg(coalesce(a.gnn_risk_score, 0.0)) AS risk_score, "
        "     sum(CASE WHEN a.in_cycle OR coalesce(a.gnn_risk_score, 0.0) >= $cutoff "
        "         THEN 1 ELSE 0 END) AS flagged "
        "RETURN cid AS community_id, size, risk_score, flagged AS flagged_count "
        f"ORDER BY {order} SKIP $offset LIMIT $limit"
    )
    async with session() as s:
        res = await s.run(query, offset=offset, limit=limit, cutoff=cutoff)
        rows = [dict(r) async for r in res]
    for r in rows:
        r["risk_tier"] = _tier(r.get("risk_score") or 0.0)
    return rows


async def load_subgraph(session, *, community_id: Optional[str] = None,
                        account_id: Optional[str] = None, hops: int = 2, limit: int = 150):
    hops = min(max(int(hops), 1), 4)
    if community_id is not None:
        query = (
            "MATCH (a:Account {community_id: $cid}) WITH a LIMIT $limit "
            "WITH collect(a) AS ns "
            "UNWIND ns AS a OPTIONAL MATCH (a)-[r:FLOWS_TO]->(b:Account) WHERE b IN ns "
            "RETURN ns AS nodes, collect(DISTINCT r) AS rels"
        )
        params = {"cid": community_id, "limit": limit}
    elif account_id is not None:
        # Depth bound cannot be parameterized; clamped to 1..4 above → injection-safe.
        query = (
            f"MATCH p=(start:Account {{id:$aid}})-[r:FLOWS_TO*1..{hops}]->(t:Account) "
            "WITH nodes(p) AS ns, relationships(p) AS rs LIMIT $limit "
            "UNWIND ns AS n UNWIND rs AS rel "
            "RETURN collect(DISTINCT n) AS nodes, collect(DISTINCT rel) AS rels"
        )
        params = {"aid": account_id, "limit": limit}
    else:
        raise ValueError("load_subgraph needs community_id or account_id")

    async with session() as s:
        rec = await (await s.run(query, **params)).single()
    if not rec or not rec["nodes"]:
        return {"nodes": [], "edges": [], "truncated": {"shown": 0, "total": 0}}
    return _shape_record(rec)


async def list_marked(pg, sort: str = "score", signal: Optional[str] = None,
                      limit: int = 100, offset: int = 0):
    flags = await pg.get_risk_flags(flag_type="AGGREGATE", limit=limit + offset)
    rows = []
    for f in flags:
        details = f.get("details") or {}
        if isinstance(details, str):
            details = json.loads(details)
        signals = details.get("signals", {})
        if signal and not signals.get(signal):
            continue
        rows.append({
            "account_id": (f.get("account_ids") or [None])[0],
            "combined_score": f.get("risk_score", 0.0),
            "signals": signals,
            "gnn_score": details.get("gnn_score"),
            "community_id": details.get("community_id"),
            "in_cycle": bool(signals.get("cycle")),
            "rationale": f.get("explanation", ""),
        })
    rows.sort(key=lambda r: r["combined_score"] or 0.0, reverse=(sort == "score"))
    return rows[offset:offset + limit]
