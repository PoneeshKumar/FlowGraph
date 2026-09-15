"""Export a static snapshot of the showcase data for the Vercel demo.

Runs against the live stores (not HTTP) using the same service functions the
API uses, and writes Frontend/src/data/snapshot/. Idempotent. Fails if the bundle
exceeds the size budget so the demo stays fast to load.

    POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
        python3 scripts/export_snapshot.py --featured 30 [--llm none|ollama|anthropic]
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("export_snapshot")

DEFAULT_OUT = Path(__file__).resolve().parent.parent.parent / "Frontend" / "src" / "data" / "snapshot"
BUDGET_BYTES = 4 * 1024 * 1024
WINDOWS = ("24h", "7d", "30d")
PERIODS = ("24h", "7d", "all")


# --- pure helpers (unit-tested) ---------------------------------------------------

def choose_featured(marked_rows: List[Dict[str, Any]], typology_of: Callable[[str], Optional[str]],
                    n: int) -> List[str]:
    """Top marked accounts by combined score, taking the best one of each ground-truth
    typology first (so the demo shows every pattern), then filling by score."""
    rows = sorted(marked_rows, key=lambda r: r.get("combined_score") or 0.0, reverse=True)
    chosen: List[str] = []
    seen_typ: Set[str] = set()
    for r in rows:
        t = typology_of(r["account_id"])
        if t and t not in seen_typ:
            seen_typ.add(t)
            chosen.append(r["account_id"])
    for r in rows:
        if len(chosen) >= n:
            break
        if r["account_id"] not in chosen:
            chosen.append(r["account_id"])
    return chosen[:n]


def pick_pairs(featured: List[str], subgraphs: Dict[str, Dict[str, Any]], n: int) -> List[Tuple[str, str]]:
    """(source, target) pairs for the demo's path/flow tool: for each featured
    account, the farthest node reachable in its exported neighbourhood (a 2-hop
    target when one exists, else a direct payee) — so the shortest-path tool has a
    real multi-hop path to draw. Featured accounts rarely touch each other directly."""
    out: List[Tuple[str, str]] = []
    for aid in featured:
        sub = subgraphs.get(aid)
        if not sub:
            continue
        adj: Dict[str, List[str]] = {}
        for e in sub.get("edges", []):
            d = e["data"]
            adj.setdefault(d["source"], []).append(d["target"])
        dist: Dict[str, int] = {aid: 0}
        frontier = [aid]
        while frontier:
            nxt: List[str] = []
            for u in frontier:
                for v in adj.get(u, []):
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        nxt.append(v)
            frontier = nxt
        reachable = [v for v, d in dist.items() if d >= 1]
        if not reachable:
            continue
        out.append((aid, max(reachable, key=lambda v: dist[v])))
        if len(out) >= n:
            break
    return out


def write_bundle(bundle: Dict[str, Any], out_dir: Path) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    for rel, payload in bundle.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, separators=(",", ":"), default=str)
        path.write_text(text)
        sizes[rel] = len(text.encode("utf-8"))
    return sizes


def budget_check(sizes: Dict[str, int], limit_bytes: int = BUDGET_BYTES) -> None:
    total = sum(sizes.values())
    log.info("snapshot: %d files, %.2f MB", len(sizes), total / 1e6)
    for rel, n in sorted(sizes.items(), key=lambda kv: -kv[1])[:8]:
        log.info("  %8.1f KB  %s", n / 1024, rel)
    if total > limit_bytes:
        log.error("snapshot exceeds budget: %.2f MB > %.2f MB", total / 1e6, limit_bytes / 1e6)
        raise SystemExit(2)


# --- collection --------------------------------------------------------------------

async def collect(featured_n: int, llm: Optional[str]) -> Dict[str, Any]:
    from app.core.config import settings
    from app.db.neo4j import neo4j_client
    from app.viz import deps as viz_deps, store, metrics, threshold, truth
    from app.services import stats_service, alerts_service, transactions_service, explanation_service
    from app.services.graph_service import GraphService

    if llm:
        settings.LLM_PROVIDER = llm
    neo4j_client.connect()
    await viz_deps.startup()
    session = neo4j_client.driver.session
    pg = viz_deps.pg()
    try:
        log.info("building stats cache …")
        await stats_service.cache.build(session, pg)
        await explanation_service.configure(settings)
        llm_status = explanation_service.service.status()
        log.info("explanations via %s", llm_status)

        overview = stats_service.cache.overview()
        series = {c["code"]: {p: stats_service.cache.series(c["code"], p) for p in PERIODS}
                  for c in overview["currencies"]}

        alerts_all = await alerts_service.list_alerts(pg, None, "low", "open", 500, 0)
        items = sorted(alerts_all["items"], key=lambda a: a["risk_score"], reverse=True)[:200]
        counts: Dict[str, int] = {}
        for a in alerts_all["items"]:
            counts[a["flag_type"]] = counts.get(a["flag_type"], 0) + 1
        alerts = {"total": alerts_all["total"], "items": items, "counts_by_type": counts}

        marked = await store.list_marked(pg, "score", None, 200, 0)
        featured = choose_featured(marked, truth.typology_of, featured_n)
        log.info("featured accounts: %d", len(featured))

        latest = await transactions_service.list_latest(session, 200, None)
        by_account: Dict[str, Any] = {}
        graph: Dict[str, Any] = {}
        accounts: Dict[str, Any] = {}
        for i, aid in enumerate(featured, 1):
            by_account[aid] = await transactions_service.list_for_account(session, aid, 50, None)
            sub = await GraphService.get_subgraph(aid, 2, 100)
            graph[f"graph/{aid}.json"] = sub.model_dump()
            node = await GraphService.get_account(aid)
            expl = await explanation_service.service.explain(aid)
            accounts[f"accounts/{aid}.json"] = {"node": node.model_dump() if node else None,
                                                "explanation": expl.model_dump()}
            if i % 10 == 0:
                log.info("  featured %d/%d", i, len(featured))
        # Richest neighbourhoods first: the demo's Graph tab opens on featured[0],
        # and a 2-node cycle member makes a thin first impression.
        featured.sort(key=lambda aid: -len(graph[f"graph/{aid}.json"]["nodes"]))
        pairs = pick_pairs(featured, {aid: graph[f"graph/{aid}.json"] for aid in featured}, 5)
        paths: Dict[str, Any] = {}
        flows: Dict[str, Any] = {}
        for a, b in pairs:
            paths[f"{a}->{b}"] = (await GraphService.get_shortest_path(a, b)).model_dump()
            for w in WINDOWS:
                flows[f"{a}->{b}|{w}"] = await GraphService.get_flow_between(a, b, w)
        log.info("paths: %d pairs", len(pairs))

        pipeline: Dict[str, Any] = {
            "pipeline/overview_gnn.json": await store.load_overview(session, metric="gnn", limit=600),
            "pipeline/overview_pagerank.json": await store.load_overview(session, metric="pagerank", limit=600),
            "pipeline/communities.json": await store.list_communities(session, "risk", 100, 0),
            "pipeline/marked.json": marked,
            "pipeline/threshold.json": {"default": threshold.model_threshold(),
                                        "min": threshold.MIN_CUTOFF, "max": threshold.MAX_CUTOFF},
            "pipeline/latest_run.json": await pg.get_latest_pipeline_run() or {"status": "none"},
        }
        await metrics.ensure_loaded(session)
        lo, hi = threshold.MIN_CUTOFF, threshold.MAX_CUTOFF
        cutoffs = [round(lo + (hi - lo) * i / 45, 4) for i in range(46)]
        rows = [metrics.confusion_at(c) for c in cutoffs]
        if rows and rows[0].get("loaded"):
            pipeline["pipeline/metrics_curve.json"] = {
                "loaded": True, "cutoffs": cutoffs,
                "precision": [r["precision"] for r in rows], "recall": [r["recall"] for r in rows],
                "tp": [r["tp"] for r in rows], "fp": [r["fp"] for r in rows], "marked": [r["marked"] for r in rows]}
        else:
            pipeline["pipeline/metrics_curve.json"] = {"loaded": False}

        bundle: Dict[str, Any] = {
            "meta.json": {"generated_at": int(time.time()), "dataset": overview["dataset"],
                          "llm": llm_status, "featured_count": len(featured),
                          "limits": {"alerts": 200, "transactions": 200}},
            "overview.json": overview,
            "series.json": series,
            "alerts.json": alerts,
            "transactions.json": {"latest": latest, "by_account": by_account},
            "featured.json": featured,
            "paths.json": paths,
            "flows.json": flows,
            "llm.json": llm_status,
        }
        bundle.update(graph)
        bundle.update(accounts)
        bundle.update(pipeline)
        return bundle
    finally:
        await viz_deps.shutdown()
        await neo4j_client.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--featured", type=int, default=30)
    ap.add_argument("--llm", choices=["none", "ollama", "anthropic"], default=None,
                    help="force the explanation provider (default: the service's own selection)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    bundle = asyncio.run(collect(args.featured, args.llm))
    sizes = write_bundle(bundle, args.out)
    budget_check(sizes)
    log.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
