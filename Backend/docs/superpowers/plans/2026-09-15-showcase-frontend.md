# Showcase Frontend (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every view of the React app show real backend data, add a native Pipeline view, and make the whole app work identically from a bundled snapshot (the Vercel demo) or a live backend.

**Architecture:** A `scripts/export_snapshot.py` (backend) writes `Frontend/src/data/snapshot/*.json` from the live stores. The frontend gets one data-source interface with two implementations (`createLiveSource` over axios, `createSnapshotSource` over lazily-imported JSON), chosen by `DataSourceProvider` from `VITE_API_BASE_URL` + a `/health` probe. A hash router replaces the `view` state. Views are adapted to the backend's shapes directly (no mock-shape mapping layer). The dark-themed graph components are restyled to the app's light "Liquid Glass Ledger" system.

**Tech Stack:** React 19, Vite 8, Tailwind 4 (theme tokens in `src/index.css`), `motion/react`, Cytoscape 3 + `cytoscape-cose-bilkent`, axios, vitest (added) for pure units. Python 3.9 for the export script.

**Spec:** `Backend/docs/superpowers/specs/2026-09-15-showcase-and-ship-design.md` — implements §8 (frontend), §9 (snapshot generator; the upload view §8.3 "Upload" is Plan C), and the frontend half of §11. Plan A (backend) is complete on this branch.

## Global Constraints

- Frontend commands run in `/Users/kavinnimalarajan/FlowGraph/Frontend`; backend/script commands in `/Users/kavinnimalarajan/FlowGraph/Backend`.
- Live backend for manual checks: `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 -m uvicorn app.api.main:app --port 8000` (Backend dir). Frontend live mode: `VITE_API_BASE_URL=http://localhost:8000/api npm run dev`; demo mode: `npm run dev` with the variable unset.
- `npm run lint` must end with **0 errors** and `npm run build` must succeed at the end of every task from Task 2 on (the baseline has 5 errors in the two files Task 6 rewrites; until then run lint on the files you touched: `npx eslint src/<file>`).
- Design system: colours only via tokens (`text-ink`, `text-ink-2/3/4`, `bg-base`, `bg-hover`, `border-line`, `border-line-2`, `text-accent`, `RISK_VAR`, `RISK_TEXT`), fonts via `font-display`/`font-sans`/`font-mono`, chips via `RiskChip`/`StatusChip` from `src/components/ui.jsx`. No slate/sky/amber Tailwind palette classes anywhere (that was the dark theme).
- Every commit ends with the trailer — define once per shell, then pass `-m "$TRAILER"`:
  ```bash
  TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
  ```
- Branch `showcase-and-ship`. `Frontend/src/data/snapshot/` **is committed** (it is the demo). Total snapshot ≤ 4 MB.
- Amounts: backend `amount_cents` / series `volume` are major units of the row's own currency (not FX-normalised). Show the currency code next to every amount; never sum across currencies.
- Time: format unix seconds with `fmtTs`/`fmtDay` from `src/lib/format.js`; the dataset is Sep 2022 — never render "now"/"today".

## File structure

| File | Responsibility |
|---|---|
| `Backend/scripts/export_snapshot.py` (new) | collect from services → write JSON bundle; enforce size budget |
| `Backend/tests/test_snapshot_export.py` (new) | pure pieces: featured selection, bundle writing, budget check |
| `Frontend/src/data/snapshot/**` (generated, committed) | the demo dataset |
| `Frontend/src/services/dataSource.js` (new) | `createLiveSource`, `createSnapshotSource`, `NotInSnapshotError`, `interpolateCurve` |
| `Frontend/src/services/DataSourceProvider.jsx` (new) | probe + context (`useDataSource`) |
| `Frontend/src/hooks/useAsync.js` (new) | async loader hook with loading/error/reload |
| `Frontend/src/router.js` (new) | hash routes: parse/build/`useRoute` |
| `Frontend/src/lib/format.js` (new) | money/time/id formatters, flag-type labels |
| `Frontend/src/components/ui.jsx` (modify) | + `Skeleton`, `ErrorNote`, `EmptyNote`, `DemoNote`, `settled` status tone |
| `Frontend/src/components/ModePill.jsx` (new) | Live / Demo indicator |
| `Frontend/src/components/Sidebar.jsx` (modify) | routed nav + ModePill + real badge |
| `Frontend/src/App.jsx`, `src/main.jsx` (modify) | provider + router |
| `Frontend/src/components/Dashboard.jsx` (modify) | real overview/series/feeds |
| `Frontend/src/components/AlertsView.jsx` (rewrite) | real flags, filters, status actions |
| `Frontend/src/components/TransactionsView.jsx` (rewrite) | history only, real rows |
| `Frontend/src/components/GraphCanvas.jsx` (rewrite) | light theme, lenses, hover highlight |
| `Frontend/src/components/InspectorSidebar.jsx` (rewrite) | light theme, signals, explanation, what-if |
| `Frontend/src/components/GraphTools.jsx` (new) | shortest path + flow |
| `Frontend/src/components/GraphExplorer.jsx` (rewrite) | search/depth/featured chips + tools |
| `Frontend/src/components/PipelineView.jsx` (new) | run, threshold, lenses, communities, marked |
| `Frontend/src/services/api.js`, `src/data/mockData.js`, `src/types/graph.ts` (delete) | superseded |
| `Frontend/src/**/*.test.js` (new) | vitest units for pure modules |

---

### Task 1: Snapshot export script

**Files:**
- Create: `Backend/scripts/export_snapshot.py`
- Test: `Backend/tests/test_snapshot_export.py`
- Generates: `Frontend/src/data/snapshot/**`

**Interfaces:**
- Consumes (Plan A): `stats_service.cache.build/overview/series`, `alerts_service.list_alerts`, `transactions_service.list_latest/list_for_account`, `GraphService.get_subgraph/get_shortest_path/get_flow_between/get_account`, `app.viz.store.load_overview/list_communities/list_marked`, `app.viz.metrics`, `app.viz.threshold`, `explanation_service`.
- Produces: pure `choose_featured(marked_rows, truth_typology_of, n) -> List[str]`, `write_bundle(bundle: Dict[str, Any], out_dir: Path) -> Dict[str, int]` (path → bytes), `budget_check(sizes, limit_bytes) -> None` (raises `SystemExit` over budget), and the bundle layout below.

Bundle layout (all paths relative to `Frontend/src/data/snapshot/`):

| file | content |
|---|---|
| `meta.json` | `{generated_at, dataset, llm: {provider, model}, featured_count, limits: {alerts: 200, transactions: 200}}` |
| `overview.json` | `/api/stats/overview` payload |
| `series.json` | `{ "<currency code>": { "24h": series, "7d": series, "all": series } }` for every currency |
| `alerts.json` | `{total, items: [200 alerts by risk_score desc, all types], counts_by_type}` |
| `transactions.json` | `{latest: [200], by_account: {"<id>": [≤50]}}` (by_account only for featured ids) |
| `featured.json` | `[ids]` — top marked accounts by combined score, diversified over truth typology when labelled |
| `graph/<id>.json` | depth-2 subgraph (`GraphElements`) per featured id |
| `accounts/<id>.json` | `{node: NodeData, explanation: ExplanationOut}` per featured id |
| `paths.json` | `{"<a>-><b>": GraphElements}` for 5 connected featured pairs |
| `flows.json` | `{"<a>-><b>|<window>": FlowSummary}` for those pairs × `24h/7d/30d` |
| `pipeline/overview_gnn.json`, `pipeline/overview_pagerank.json` | `/viz/overview` at `limit=600` |
| `pipeline/communities.json` | `/viz/communities?sort=risk&limit=100` |
| `pipeline/marked.json` | `/viz/marked?limit=200` |
| `pipeline/threshold.json`, `pipeline/metrics_curve.json` (46 pts), `pipeline/latest_run.json` | as the endpoints |
| `llm.json` | `/api/system/llm` payload used during export |

- [ ] **Step 1: Write the failing tests**

`Backend/tests/test_snapshot_export.py`:

```python
"""Pure pieces of the snapshot exporter: featured selection, bundle writing, budget."""
import json
import pytest

from scripts import export_snapshot as ex


def test_choose_featured_diversifies_over_typology_then_fills_by_score():
    rows = [{"account_id": f"a{i}", "combined_score": 1 - i / 100} for i in range(12)]
    typ = {"a0": "CYCLE", "a1": "CYCLE", "a2": "FAN-IN", "a3": "FAN-IN", "a4": "STACK"}
    out = ex.choose_featured(rows, typ.get, n=6)
    assert out[:3] == ["a0", "a2", "a4"]          # one per typology first, best score each
    assert len(out) == 6 and len(set(out)) == 6   # then filled by score, no duplicates
    assert ex.choose_featured(rows, lambda _i: None, n=3) == ["a0", "a1", "a2"]   # unlabelled → by score


def test_write_bundle_and_budget(tmp_path):
    bundle = {"meta.json": {"a": 1}, "graph/x.json": {"nodes": [], "edges": []}}
    sizes = ex.write_bundle(bundle, tmp_path)
    assert (tmp_path / "graph" / "x.json").exists()
    assert json.loads((tmp_path / "meta.json").read_text()) == {"a": 1}
    assert set(sizes) == set(bundle) and all(v > 0 for v in sizes.values())
    ex.budget_check(sizes, limit_bytes=10_000)                      # fine
    with pytest.raises(SystemExit):
        ex.budget_check(sizes, limit_bytes=10)                       # over budget


def test_connected_pairs_picks_pairs_with_edges():
    featured = ["a", "b", "c", "d"]
    edges = {("a", "b"), ("c", "a"), ("d", "d")}
    assert ex.connected_pairs(featured, edges, n=5) == [("a", "b"), ("c", "a")]
```

- [ ] **Step 2: Run to verify failure**

Run (Backend): `python3 -m pytest tests/test_snapshot_export.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.export_snapshot'` (add an empty `scripts/__init__.py` if `scripts` is not importable — check with `ls scripts/__init__.py`).

- [ ] **Step 3: Write the script**

`Backend/scripts/export_snapshot.py`:

```python
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


def connected_pairs(featured: List[str], edges: Iterable[Tuple[str, str]], n: int) -> List[Tuple[str, str]]:
    """Ordered (source, target) pairs among featured accounts that have a FLOWS_TO edge."""
    fset = set(featured)
    out = []
    for s, t in edges:
        if s in fset and t in fset and s != t and (s, t) not in out:
            out.append((s, t))
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
        counts = {}
        for a in alerts_all["items"]:
            counts[a["flag_type"]] = counts.get(a["flag_type"], 0) + 1
        alerts = {"total": alerts_all["total"], "items": items, "counts_by_type": counts}

        marked = await store.list_marked(pg, "score", None, 200, 0)
        featured = choose_featured(marked, truth.typology_of, featured_n)
        log.info("featured accounts: %d", len(featured))

        latest = await transactions_service.list_latest(session, 200, None)
        by_account = {}
        graph, accounts = {}, {}
        edge_set: Set[Tuple[str, str]] = set()
        for aid in featured:
            by_account[aid] = await transactions_service.list_for_account(session, aid, 50, None)
            sub = await GraphService.get_subgraph(aid, 2, 100)
            graph[f"graph/{aid}.json"] = sub.model_dump()
            for e in sub.edges:
                edge_set.add((e.data.source, e.data.target))
            node = await GraphService.get_account(aid)
            expl = await explanation_service.service.explain(aid)
            accounts[f"accounts/{aid}.json"] = {"node": node.model_dump() if node else None,
                                                "explanation": expl.model_dump()}
        pairs = connected_pairs(featured, sorted(edge_set), 5)
        paths, flows = {}, {}
        for a, b in pairs:
            paths[f"{a}->{b}"] = (await GraphService.get_shortest_path(a, b)).model_dump()
            for w in WINDOWS:
                flows[f"{a}->{b}|{w}"] = await GraphService.get_flow_between(a, b, w)

        pipeline = {
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
```

- [ ] **Step 4: Run the unit tests**

Run: `python3 -m pytest tests/test_snapshot_export.py -q -p no:cacheprovider` — Expected: 3 passed.

- [ ] **Step 5: Generate the snapshot from the live stores**

Run (Backend): `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 scripts/export_snapshot.py --featured 30`
Expected: log lines ending `snapshot: N files, X.XX MB` with X < 4, and `wrote …/Frontend/src/data/snapshot`. Then:
`ls ../Frontend/src/data/snapshot ../Frontend/src/data/snapshot/pipeline | head -30` shows the layout above; `python3 -c "import json; d=json.load(open('../Frontend/src/data/snapshot/meta.json')); print(d['featured_count'], d['llm'])"` → `30 {'provider': 'none', ...}` (rule-based unless Ollama/Anthropic is configured).

If the total is over budget, lower `--featured` to 20 and/or change the two `load_overview` calls to `limit=400` — the overview graphs are the largest files.

- [ ] **Step 6: Commit script, tests, and the generated snapshot**

```bash
cd /Users/kavinnimalarajan/FlowGraph
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add Backend/scripts/export_snapshot.py Backend/scripts/__init__.py Backend/tests/test_snapshot_export.py Frontend/src/data/snapshot
git commit -m "feat(demo): snapshot exporter + first generated snapshot for the static demo" -m "$TRAILER"
```

---

### Task 2: Frontend foundation — data source, router, formatters, shell

**Files:**
- Create: `src/services/dataSource.js`, `src/services/DataSourceProvider.jsx`, `src/hooks/useAsync.js`, `src/router.js`, `src/lib/format.js`, `src/components/ModePill.jsx`
- Modify: `src/components/ui.jsx`, `src/components/Sidebar.jsx`, `src/App.jsx`, `src/main.jsx`, `package.json`, `.env.example`
- Test: `src/services/dataSource.test.js`, `src/router.test.js`, `src/lib/format.test.js`

**Interfaces:**
- Produces: `useDataSource() -> { source, mode: 'live'|'demo', status: 'probing'|'ready', meta, banner, retryLive }`; `useAsync(fn, deps) -> { data, error, loading, reload }`; `useRoute() -> { view, params, navigate(view, params) }`; format helpers; `NotInSnapshotError`.
- The **source interface** (both implementations):
  `stats.overview()`, `stats.series(currency, period)`, `alerts.list({flag_type, min_level, status, limit, offset})`, `alerts.setStatus(id, status)`, `transactions.list({limit, account_id, currency})`, `graph.subgraph(accountId, depth)`, `graph.shortestPath(a, b)`, `graph.flow(a, b, window)`, `graph.account(id)`, `risk.evaluate(id, params)`, `ai.explain(id)`, `pipeline.overview(metric, limit)`, `pipeline.communities({sort, limit, offset})`, `pipeline.subgraph(params)`, `pipeline.marked({sort, signal, limit, offset})`, `pipeline.threshold()`, `pipeline.metrics(cutoff)`, `pipeline.metricsCurve(points)`, `pipeline.run()`, `pipeline.runStatus(id)`, `pipeline.latestRun()`, `system.llm()`, `featured()` (demo: the list; live: `[]`).

- [ ] **Step 1: Add vitest and write the failing unit tests**

Run (Frontend): `npm install -D vitest@^3` and add to `package.json` scripts: `"test": "vitest run"`.

`src/router.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { parseHash, buildHash } from './router'

describe('router', () => {
  it('parses views and params, defaulting unknown views to overview', () => {
    expect(parseHash('#/graph?account=abc&depth=2')).toEqual({ view: 'graph', params: { account: 'abc', depth: '2' } })
    expect(parseHash('')).toEqual({ view: 'overview', params: {} })
    expect(parseHash('#/nope')).toEqual({ view: 'overview', params: {} })
  })
  it('builds hashes, dropping empty params', () => {
    expect(buildHash('alerts', { id: 7, x: '', y: null })).toBe('#/alerts?id=7')
    expect(buildHash('overview')).toBe('#/overview')
  })
})
```

`src/lib/format.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { fmtAmount, fmtTs, fmtDay, shortId, flagLabel, fmtCompact } from './format'

describe('format', () => {
  it('formats money in major units with no symbol', () => {
    expect(fmtAmount(123456)).toBe('1,234.56')
    expect(fmtAmount(500)).toBe('5.00')
  })
  it('formats dataset timestamps in UTC, never relative', () => {
    expect(fmtDay(1663517880)).toBe('Sep 18, 2022')
    expect(fmtTs(1661991600)).toBe('Sep 1, 23:40')
  })
  it('shortens ids and labels flag types', () => {
    expect(shortId('e5c379536a14f3eca471395d189aa2fc')).toBe('e5c37953')
    expect(flagLabel('AGGREGATE')).toBe('Aggregate risk')
    expect(flagLabel('WHATEVER')).toBe('Whatever')
    expect(fmtCompact(1895167)).toBe('1.9M')
  })
})
```

`src/services/dataSource.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { createSnapshotSource, interpolateCurve, NotInSnapshotError } from './dataSource'

const curve = { loaded: true, cutoffs: [0.5, 0.6, 0.7], precision: [0.2, 0.4, 0.8], recall: [0.9, 0.6, 0.3], tp: [90, 60, 30], fp: [360, 90, 8], marked: [450, 150, 38] }

describe('interpolateCurve', () => {
  it('interpolates linearly between cutoffs and clamps at the ends', () => {
    const m = interpolateCurve(curve, 0.65)
    expect(m.precision).toBeCloseTo(0.6) ; expect(m.recall).toBeCloseTo(0.45) ; expect(m.tp).toBe(45)
    expect(interpolateCurve(curve, 0.1).precision).toBe(0.2)
    expect(interpolateCurve(curve, 0.9).precision).toBe(0.8)
    expect(interpolateCurve({ loaded: false }, 0.5)).toEqual({ loaded: false })
  })
})

describe('createSnapshotSource', () => {
  const files = {
    'meta.json': { featured_count: 1 },
    'featured.json': ['a1'],
    'alerts.json': { total: 3, items: [
      { id: 1, flag_type: 'AGGREGATE', risk_level: 'critical', risk_score: 0.9 },
      { id: 2, flag_type: 'CYCLE', risk_level: 'high', risk_score: 0.7 },
      { id: 3, flag_type: 'AGGREGATE', risk_level: 'low', risk_score: 0.2 },
    ] },
    'transactions.json': { latest: [{ txn_id: 't', currency: 'Euro' }, { txn_id: 'u', currency: 'US Dollar' }], by_account: { a1: [{ txn_id: 'v' }] } },
    'graph/a1.json': { nodes: [], edges: [] },
    'accounts/a1.json': { node: { id: 'a1' }, explanation: { explanation: 'e' } },
    'pipeline/metrics_curve.json': curve,
    'pipeline/communities.json': [{ community_id: 'c', size: 3, risk_score: 0.5 }, { community_id: 'd', size: 9, risk_score: 0.1 }],
  }
  const src = createSnapshotSource(path => Promise.resolve(files[path]))

  it('filters and pages alerts in memory', async () => {
    const page = await src.alerts.list({ flag_type: 'AGGREGATE', min_level: 'low', limit: 1, offset: 0 })
    expect(page.total).toBe(2) ; expect(page.items[0].id).toBe(1)
    const high = await src.alerts.list({ min_level: 'high' })
    expect(high.items.map(a => a.id)).toEqual([1, 2])
  })
  it('serves featured graph/account data and rejects the rest', async () => {
    expect((await src.graph.subgraph('a1', 2)).nodes).toEqual([])
    expect((await src.ai.explain('a1')).explanation).toBe('e')
    await expect(src.graph.subgraph('zz', 2)).rejects.toBeInstanceOf(NotInSnapshotError)
    await expect(src.pipeline.run()).rejects.toBeInstanceOf(NotInSnapshotError)
  })
  it('filters transactions by currency and account', async () => {
    expect((await src.transactions.list({ currency: 'Euro' })).items.map(t => t.txn_id)).toEqual(['t'])
    expect((await src.transactions.list({ account_id: 'a1' })).items.map(t => t.txn_id)).toEqual(['v'])
  })
  it('sorts communities and interpolates metrics', async () => {
    expect((await src.pipeline.communities({ sort: 'size' }))[0].community_id).toBe('d')
    expect((await src.pipeline.metrics(0.65)).precision).toBeCloseTo(0.6)
  })
})
```

Run: `npm test` — Expected: failures with "Failed to resolve import" for the three modules.

- [ ] **Step 2: Formatters**

`src/lib/format.js`:

```js
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** amount_cents → "1,234.56" (major units of the row's own currency; no symbol). */
export function fmtAmount(cents) {
  return (Number(cents || 0) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** major-unit number → "1.2M" / "340K" / "12". */
export function fmtCompact(n) {
  const v = Number(n || 0)
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return `${Math.round(v)}`
}

const utc = (ts) => new Date(Number(ts) * 1000)

/** unix seconds → "Sep 1, 23:40" (UTC — the dataset's own clock). */
export function fmtTs(ts) {
  const d = utc(ts)
  const hh = String(d.getUTCHours()).padStart(2, '0'), mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${hh}:${mm}`
}

/** unix seconds → "Sep 18, 2022". */
export function fmtDay(ts) {
  const d = utc(ts)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`
}

/** unix seconds → "Sep 3" (chart axis). */
export function fmtShortDay(ts) {
  const d = utc(ts)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`
}

/** unix seconds → "14:30" (chart axis, 24h period). */
export function fmtClock(ts) {
  const d = utc(ts)
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
}

/** ISO string → "Sep 3, 04:55". */
export function fmtIso(iso) {
  if (!iso) return '—'
  return fmtTs(Date.parse(iso) / 1000)
}

export function shortId(id) {
  return String(id || '').slice(0, 8)
}

const FLAG_LABELS = {
  AGGREGATE: 'Aggregate risk',
  COMMUNITY: 'Community risk',
  CYCLE: 'Circular flow',
  LIVE_GNN: 'Live GNN score',
}

export function flagLabel(type) {
  if (FLAG_LABELS[type]) return FLAG_LABELS[type]
  const t = String(type || '').toLowerCase().replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}
```

- [ ] **Step 3: Router**

`src/router.js`:

```js
import { useCallback, useEffect, useState } from 'react'

export const VIEWS = ['overview', 'graph', 'alerts', 'transactions', 'pipeline', 'upload']

export function parseHash(hash) {
  const raw = String(hash || '').replace(/^#\/?/, '')
  const [path, qs] = raw.split('?')
  const view = VIEWS.includes(path) ? path : 'overview'
  const params = Object.fromEntries(new URLSearchParams(qs || ''))
  return { view, params }
}

export function buildHash(view, params = {}) {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  const qs = new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()
  return `#/${view}${qs ? `?${qs}` : ''}`
}

/** Hash routing: no server rewrites needed, deep links work on a static host. */
export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  const navigate = useCallback((view, params) => {
    window.location.hash = buildHash(view, params)
  }, [])
  return { view: route.view, params: route.params, navigate }
}
```

- [ ] **Step 4: Data sources**

`src/services/dataSource.js`:

```js
import axios from 'axios'

export class NotInSnapshotError extends Error {
  constructor(what) {
    super(`${what} needs a live backend — it isn't part of the demo snapshot.`)
    this.name = 'NotInSnapshotError'
  }
}

const LEVEL_RANK = { low: 1, medium: 2, high: 3, critical: 4 }

/** Linear interpolation over a precomputed precision/recall curve. */
export function interpolateCurve(curve, cutoff) {
  if (!curve || !curve.loaded) return { loaded: false }
  const xs = curve.cutoffs
  if (cutoff <= xs[0]) return pick(curve, 0)
  if (cutoff >= xs[xs.length - 1]) return pick(curve, xs.length - 1)
  let i = 0
  while (xs[i + 1] < cutoff) i++
  const t = (cutoff - xs[i]) / (xs[i + 1] - xs[i])
  const lerp = (arr) => arr[i] + (arr[i + 1] - arr[i]) * t
  return {
    loaded: true, cutoff,
    precision: lerp(curve.precision), recall: lerp(curve.recall),
    tp: Math.round(lerp(curve.tp)), fp: Math.round(lerp(curve.fp)), marked: Math.round(lerp(curve.marked)),
  }
}
function pick(curve, i) {
  return { loaded: true, cutoff: curve.cutoffs[i], precision: curve.precision[i], recall: curve.recall[i],
           tp: curve.tp[i], fp: curve.fp[i], marked: curve.marked[i] }
}

// ---------------------------------------------------------------- live -----

export function createLiveSource(baseUrl) {
  const base = String(baseUrl).replace(/\/+$/, '')
  const root = base.replace(/\/api$/, '')
  const http = axios.create({ baseURL: base, timeout: 30000 })
  const get = (url, params) => http.get(url, { params }).then(r => r.data)
  return {
    mode: 'live',
    health: () => axios.get(`${root}/health`, { timeout: 2500 }).then(r => r.data),
    featured: async () => [],
    stats: {
      overview: () => get('/stats/overview'),
      series: (currency, period) => get('/stats/volume-series', { currency, period }),
    },
    alerts: {
      list: (p = {}) => get('/alerts', p),
      setStatus: (id, status) => http.patch(`/alerts/${id}/status`, { status }).then(r => r.data),
    },
    transactions: { list: (p = {}) => get('/transactions', p) },
    graph: {
      subgraph: (account_id, depth = 2) => get('/graph/subgraph', { account_id, depth }),
      shortestPath: (account_a, account_b) => get('/graph/shortest-path', { account_a, account_b }),
      flow: (account_a, account_b, window = '7d') => get('/graph/flow', { account_a, account_b, window }),
      account: (id) => get(`/graph/account/${encodeURIComponent(id)}`),
    },
    risk: { evaluate: (id, params = {}) => http.post(`/risk/evaluate/${encodeURIComponent(id)}`, null, { params }).then(r => r.data) },
    ai: { explain: (id) => get(`/accounts/${encodeURIComponent(id)}/enrich`) },
    pipeline: {
      overview: (metric = 'gnn', limit = 600) => get('/pipeline/overview', { metric, limit }),
      communities: (p = {}) => get('/pipeline/communities', p),
      subgraph: (p = {}) => get('/pipeline/subgraph', p),
      marked: (p = {}) => get('/pipeline/marked', p),
      threshold: () => get('/pipeline/threshold'),
      metrics: (cutoff) => get('/pipeline/metrics', { cutoff }),
      metricsCurve: (points = 46) => get('/pipeline/metrics/curve', { points }),
      run: () => http.post('/pipeline/run').then(r => r.data),
      runStatus: (id) => get(`/pipeline/run/${id}`),
      latestRun: () => get('/pipeline/run/latest'),
    },
    system: { llm: () => get('/system/llm') },
  }
}

// ------------------------------------------------------------ snapshot -----

// Vite turns this into lazy chunks: one small import per JSON file, on demand.
const modules = import.meta.glob('../data/snapshot/**/*.json')

function defaultLoader(path) {
  const key = `../data/snapshot/${path}`
  const mod = modules[key]
  if (!mod) return Promise.resolve(undefined)
  return mod().then(m => m.default)
}

/** `loader(path) -> Promise<json|undefined>`; injectable for tests. */
export function createSnapshotSource(loader = defaultLoader) {
  const cache = new Map()
  const load = (path) => {
    if (!cache.has(path)) cache.set(path, loader(path))
    return cache.get(path)
  }
  const need = async (path, what) => {
    const v = await load(path)
    if (v === undefined) throw new NotInSnapshotError(what)
    return v
  }
  const unsupported = (what) => () => Promise.reject(new NotInSnapshotError(what))

  return {
    mode: 'demo',
    meta: () => load('meta.json'),
    featured: () => load('featured.json').then(v => v || []),
    stats: {
      overview: () => need('overview.json', 'Overview'),
      series: async (currency, period) => {
        const all = await need('series.json', 'Volume series')
        const s = all[currency] && all[currency][period]
        if (!s) throw new NotInSnapshotError(`${currency} ${period} series`)
        return s
      },
    },
    alerts: {
      list: async ({ flag_type, min_level = 'low', limit = 100, offset = 0 } = {}) => {
        const { items } = await need('alerts.json', 'Alerts')
        const min = LEVEL_RANK[min_level] || 1
        const f = items.filter(a => (!flag_type || a.flag_type === flag_type) && (LEVEL_RANK[a.risk_level] || 0) >= min)
        return { total: f.length, items: f.slice(offset, offset + limit) }
      },
      setStatus: async (id, status) => ({ id, status, demo: true }),
    },
    transactions: {
      list: async ({ limit = 50, account_id, currency } = {}) => {
        const { latest, by_account } = await need('transactions.json', 'Transactions')
        let rows = account_id ? by_account[account_id] : latest
        if (account_id && !rows) throw new NotInSnapshotError(`Transactions for ${account_id}`)
        if (currency) rows = rows.filter(t => t.currency === currency)
        return { items: rows.slice(0, limit) }
      },
    },
    graph: {
      subgraph: (id) => need(`graph/${id}.json`, `Neighbourhood of ${id}`),
      shortestPath: async (a, b) => {
        const paths = await need('paths.json', 'Shortest paths')
        const p = paths[`${a}->${b}`]
        if (!p) throw new NotInSnapshotError(`Path ${a} → ${b}`)
        return p
      },
      flow: async (a, b, window = '7d') => {
        const flows = await need('flows.json', 'Flows')
        const f = flows[`${a}->${b}|${window}`]
        if (!f) throw new NotInSnapshotError(`Flow ${a} → ${b}`)
        return f
      },
      account: (id) => need(`accounts/${id}.json`, `Account ${id}`).then(v => v.node),
    },
    risk: { evaluate: unsupported('Risk evaluation') },
    ai: { explain: (id) => need(`accounts/${id}.json`, `Explanation for ${id}`).then(v => v.explanation) },
    pipeline: {
      overview: (metric = 'gnn') => need(`pipeline/overview_${metric === 'pagerank' ? 'pagerank' : 'gnn'}.json`, 'Pipeline overview'),
      communities: async ({ sort = 'risk', limit = 100, offset = 0 } = {}) => {
        const rows = [...await need('pipeline/communities.json', 'Communities')]
        rows.sort((a, b) => sort === 'size' ? b.size - a.size : b.risk_score - a.risk_score)
        return rows.slice(offset, offset + limit)
      },
      subgraph: async ({ account_id } = {}) => {
        if (account_id) return need(`graph/${account_id}.json`, `Neighbourhood of ${account_id}`)
        throw new NotInSnapshotError('Community subgraphs')
      },
      marked: async ({ signal, limit = 100, offset = 0 } = {}) => {
        const rows = await need('pipeline/marked.json', 'Marked accounts')
        const f = signal ? rows.filter(r => r.signals && r.signals[signal]) : rows
        return f.slice(offset, offset + limit)
      },
      threshold: () => need('pipeline/threshold.json', 'Threshold'),
      metrics: async (cutoff) => interpolateCurve(await need('pipeline/metrics_curve.json', 'Metrics'), cutoff),
      metricsCurve: () => need('pipeline/metrics_curve.json', 'Metrics curve'),
      run: unsupported('Running the pipeline'),
      runStatus: () => need('pipeline/latest_run.json', 'Run status'),
      latestRun: () => need('pipeline/latest_run.json', 'Run status'),
    },
    system: { llm: () => load('llm.json').then(v => v || { provider: 'none', model: null, reachable: false }) },
  }
}
```

- [ ] **Step 5: Run the unit tests**

Run: `npm test` — Expected: all tests in the three files pass.

- [ ] **Step 6: Provider and async hook**

`src/hooks/useAsync.js`:

```js
import { useCallback, useEffect, useState } from 'react'

/**
 * Run an async loader whenever `deps` change. Returns { data, error, loading, reload }.
 * State updates happen after an await, so the effect never calls setState synchronously
 * (react-hooks/set-state-in-effect).
 */
export function useAsync(fn, deps) {
  const key = JSON.stringify(deps)
  const [nonce, setNonce] = useState(0)
  const [state, setState] = useState({ key: null, data: null, error: null })

  useEffect(() => {
    let active = true
    Promise.resolve()
      .then(() => fn())
      .then(
        data => { if (active) setState({ key, data, error: null }) },
        error => { if (active) setState({ key, data: null, error }) },
      )
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce])

  const reload = useCallback(() => setNonce(n => n + 1), [])
  const loading = state.key !== key
  return { data: loading ? null : state.data, error: loading ? null : state.error, loading, reload }
}
```

`src/services/DataSourceProvider.jsx`:

```jsx
/* eslint-disable react-refresh/only-export-components -- provider + hook live together */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { createLiveSource, createSnapshotSource } from './dataSource'

const Ctx = createContext(null)

async function probe() {
  const base = import.meta.env.VITE_API_BASE_URL
  if (base) {
    const live = createLiveSource(base)
    try {
      await live.health()
      return { status: 'ready', mode: 'live', source: live, meta: null, banner: null }
    } catch {
      const snap = createSnapshotSource()
      return { status: 'ready', mode: 'demo', source: snap, meta: await snap.meta(),
               banner: `Backend at ${base} is unreachable — showing the bundled snapshot.` }
    }
  }
  const snap = createSnapshotSource()
  return { status: 'ready', mode: 'demo', source: snap, meta: await snap.meta(), banner: null }
}

export function DataSourceProvider({ children }) {
  const [state, setState] = useState({ status: 'probing', mode: null, source: null, meta: null, banner: null })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    probe().then(next => { if (active) setState(next) })
    return () => { active = false }
  }, [attempt])

  const retryLive = useCallback(() => setAttempt(a => a + 1), [])
  const value = useMemo(() => ({ ...state, retryLive }), [state, retryLive])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useDataSource() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useDataSource must be used inside DataSourceProvider')
  return v
}
```

- [ ] **Step 7: Shared UI additions**

Append to `src/components/ui.jsx` (and add `settled: 'low'` to `STATUS_TONE`, after `cleared:`):

```jsx
/** Grey placeholder block while data loads. */
export function Skeleton({ className = '' }) {
  return <div className={`animate-pulse rounded bg-hover ${className}`} aria-hidden="true" />
}

/** Inline error with optional retry. */
export function ErrorNote({ error, onRetry }) {
  const msg = error?.response?.data?.detail || error?.message || 'Something went wrong.'
  return (
    <div className="flex items-center gap-3 rounded-md border border-line-2 px-3 py-2 text-[12px] text-ink-2">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-critical" />
      <span className="min-w-0 flex-1">{msg}</span>
      {onRetry && <button type="button" onClick={onRetry} className="font-medium text-accent hover:opacity-70">Retry</button>}
    </div>
  )
}

export function EmptyNote({ children }) {
  return <p className="px-1 py-6 text-center text-[12px] text-ink-4">{children}</p>
}

/** Shown where a feature needs a live backend (demo mode). */
export function DemoNote({ children }) {
  return (
    <div className="rounded-md border border-dashed border-line-2 px-3 py-2 text-[12px] text-ink-3">
      {children} <a href="https://github.com/KavEn06/FlowGraph#run-it-yourself" className="font-medium text-accent hover:opacity-70">Self-host →</a>
    </div>
  )
}
```

- [ ] **Step 8: ModePill, Sidebar, App, main**

`src/components/ModePill.jsx`:

```jsx
import { useDataSource } from '../services/DataSourceProvider'
import { fmtDay } from '../lib/format'

export default function ModePill() {
  const { status, mode, meta, banner, retryLive } = useDataSource()
  if (status !== 'ready') return <span className="text-[11px] text-ink-4">connecting…</span>
  if (mode === 'live') {
    return (
      <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
        <span className="h-1.5 w-1.5 rounded-full bg-accent [animation:pulseSoft_2.4s_ease-in-out_infinite]" />
        Live
      </span>
    )
  }
  const end = meta?.dataset?.end_ts
  return (
    <span className="flex items-center gap-2 text-[11px] text-ink-3" title={banner || 'Bundled snapshot — no backend required'}>
      <span className="h-1.5 w-1.5 rounded-full bg-ink-4" />
      Demo{end ? ` · snapshot ${fmtDay(end)}` : ''}
      {banner && <button type="button" onClick={retryLive} className="font-medium text-accent hover:opacity-70">retry live</button>}
    </span>
  )
}
```

In `src/components/Sidebar.jsx`: replace the `NAV` constant and the component with:

```jsx
const NAV = [
  { id: 'overview',     label: 'Overview' },
  { id: 'graph',        label: 'Graph' },
  { id: 'alerts',       label: 'Alerts' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'pipeline',     label: 'Pipeline' },
  { id: 'upload',       label: 'Upload', liveOnly: true },
]
```

and

```jsx
export default function Sidebar({ active, onNav, alertCount }) {
  const [logoHover, setLogoHover] = useState(false)
  const { mode } = useDataSource()
  const items = NAV.filter(item => !item.liveOnly || mode === 'live')

  return (
    <header className="relative z-10 flex shrink-0 items-center gap-8 px-8 py-4">
      <button
        onClick={() => onNav('overview')}
        onMouseEnter={() => setLogoHover(true)}
        onMouseLeave={() => setLogoHover(false)}
        className="flex shrink-0 items-center gap-2.5"
      >
        <FlowLogo active={logoHover} />
        <span className="font-display text-[18px] font-medium tracking-tight text-ink">FlowGraph</span>
      </button>

      <nav className="flex min-w-0 flex-1 items-center gap-6">
        {items.map(item => {
          const isActive = active === item.id
          const badge = item.id === 'alerts' && alertCount ? alertCount : null
          return (
            <button
              key={item.id}
              onClick={() => onNav(item.id)}
              className={`relative flex shrink-0 items-center gap-2 pb-0.5 text-[13px] transition-colors duration-200
                ${isActive ? 'font-semibold text-ink' : 'font-normal text-ink-3 hover:text-ink-2'}`}
            >
              {item.label}
              {badge != null && (
                <span className="font-mono text-[10px] font-bold text-critical tnum">{badge.toLocaleString()}</span>
              )}
              {isActive && (
                <motion.span
                  layoutId="nav-underline"
                  className="absolute -bottom-1 left-0 right-0 h-[2px] rounded-full bg-accent"
                  transition={{ type: 'spring', stiffness: 480, damping: 36 }}
                />
              )}
            </button>
          )
        })}
      </nav>

      <div className="hidden shrink-0 items-center md:flex">
        <ModePill />
      </div>
    </header>
  )
}
```

with `import ModePill from './ModePill'` and `import { useDataSource } from '../services/DataSourceProvider'` added at the top (keep `FlowLogo` as is).

`src/App.jsx`:

```jsx
import { lazy, Suspense } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import AlertsView from './components/AlertsView'
import TransactionsView from './components/TransactionsView'
import { useRoute } from './router'
import { useDataSource } from './services/DataSourceProvider'
import { useAsync } from './hooks/useAsync'
import { Skeleton } from './components/ui'

// Cytoscape is ~1 MB minified — load the graph views only when opened.
const GraphExplorer = lazy(() => import('./components/GraphExplorer'))
const PipelineView = lazy(() => import('./components/PipelineView'))
const UploadView = lazy(() => import('./components/UploadView').catch(() => ({ default: () => null })))

const VIEWS = {
  overview:     Dashboard,
  graph:        GraphExplorer,
  alerts:       AlertsView,
  transactions: TransactionsView,
  pipeline:     PipelineView,
  upload:       UploadView,
}

export default function App() {
  const { view, params, navigate } = useRoute()
  const { status, source, banner } = useDataSource()
  const overview = useAsync(() => (status === 'ready' ? source.stats.overview() : Promise.resolve(null)), [status])
  const ActiveView = VIEWS[view]

  return (
    <div className="relative flex h-screen min-w-0 flex-col">
      <div className="backdrop" aria-hidden="true" />
      <Sidebar active={view} onNav={navigate} alertCount={overview.data?.open_flags?.total} />
      {banner && (
        <div className="relative z-10 mx-8 mb-2 rounded-md border border-line-2 bg-hover px-3 py-1.5 text-[12px] text-ink-2">{banner}</div>
      )}
      <main className="relative z-[1] min-h-0 flex-1 overflow-hidden">
        {status !== 'ready' ? (
          <div className="px-8 pt-4"><Skeleton className="h-6 w-48" /></div>
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              className="h-full"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            >
              <Suspense fallback={<div className="px-8 pt-4"><Skeleton className="h-6 w-48" /></div>}>
                <ActiveView onNav={navigate} params={params} />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        )}
      </main>
    </div>
  )
}
```

(`UploadView` is created in Plan C; the `.catch` keeps the demo build green until then — replace that line with a plain `lazy(() => import(...))` in Plan C.)

`src/main.jsx`:

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { DataSourceProvider } from './services/DataSourceProvider'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DataSourceProvider>
      <App />
    </DataSourceProvider>
  </StrictMode>,
)
```

`.env.example`:

```
# Leave unset for demo mode (bundled snapshot). Point at a running backend for live mode.
# VITE_API_BASE_URL=http://localhost:8000/api
```

- [ ] **Step 9: Temporary shims so the app compiles before Tasks 3–7**

Views still importing `mockData`/`api.js` compile unchanged. `PipelineView` does not exist yet — create a placeholder `src/components/PipelineView.jsx`:

```jsx
export default function PipelineView() {
  return <div className="px-8 pt-4 text-[13px] text-ink-3">Pipeline view — built in Task 7.</div>
}
```

Run: `npx eslint src/App.jsx src/main.jsx src/router.js src/lib src/services src/hooks src/components/Sidebar.jsx src/components/ModePill.jsx src/components/ui.jsx src/components/PipelineView.jsx` — Expected: no errors.
Run: `npm run build` — Expected: success. Run: `npm run dev` and open `http://localhost:5173/#/overview` — the header shows **Demo · snapshot Sep 18, 2022** and the Alerts badge shows `11,878`; with `VITE_API_BASE_URL=http://localhost:8000/api npm run dev` (backend up) it shows **Live**.

- [ ] **Step 10: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add package.json package-lock.json .env.example src/App.jsx src/main.jsx src/router.js src/router.test.js src/lib src/hooks src/services src/components/Sidebar.jsx src/components/ModePill.jsx src/components/ui.jsx src/components/PipelineView.jsx
git commit -m "feat(frontend): live/snapshot data-source layer, hash routing, mode pill" -m "$TRAILER"
```

---

### Task 3: Overview (Dashboard) on real data

**Files:**
- Modify: `src/components/Dashboard.jsx`

**Interfaces:**
- Consumes: `source.stats.overview()`, `source.stats.series(currency, period)`, `source.alerts.list({limit: 6, min_level: 'high'})`, `source.transactions.list({limit: 6})`.

- [ ] **Step 1: Replace the data plumbing**

At the top of `Dashboard.jsx` replace

```jsx
import { METRICS, RECENT_ALERTS, RECENT_TRANSACTIONS, VOLUME_SERIES } from '../data/mockData'
import { RiskChip, StatusChip, RISK_VAR, useCountUp, useTweenValue } from './ui'
```

with

```jsx
import { RiskChip, StatusChip, RISK_VAR, useCountUp, useTweenValue, Skeleton, ErrorNote, EmptyNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { fmtAmount, fmtClock, fmtShortDay, fmtDay, fmtIso, flagLabel, shortId, fmtCompact } from '../lib/format'
```

Replace the `PERIODS` constant with:

```jsx
const PERIODS = [
  { id: '24h', label: '24H' },
  { id: '7d',  label: '7D'  },
  { id: 'all', label: 'ALL' },
]
```

Delete `SNAP_MINUTES_24H`, `formatTimeLabel`, `build24hAxisMarks`, `sampleSeriesAtFrac`, `buildDense24hSeries` (the backend already returns 48 half-hour points for 24h). Add, after `tickIndices`:

```jsx
/** Backend series → chart series: labels per bucket, volume in millions, per-bucket txns. */
function toChartSeries(s, period) {
  const fmt = period === '24h' ? fmtClock : fmtShortDay
  const txns = s.txns.map((v, i) => (i === 0 ? v : v - s.txns[i - 1]))
  return {
    labels: s.t.map(fmt),
    volume: s.volume.map(v => v / 1e6),
    baseline: s.baseline.map(v => v / 1e6),
    txns,
    end: s.anchor_ts,
  }
}
```

Replace `const STAT_CARDS = [...]` with:

```jsx
const STAT_CARDS = [
  { key: 'accounts',        label: 'Accounts',            sub: 'in the graph' },
  { key: 'scored_accounts', label: 'GNN-scored',          sub: 'with a risk score' },
  { key: 'communities',     label: 'Communities',         sub: 'Louvain clusters' },
  { key: 'in_cycle',        label: 'On detected cycles',  sub: 'cycle detector', tone: 'text-critical' },
]
```

- [ ] **Step 2: Make `VolumeChart` take a prepared series**

Change the signature `function VolumeChart({ period, onHover, onSelect })` to `function VolumeChart({ series, period, onHover, onSelect })` and delete the first four lines of its body (the `baseSeries`/`useMemo` block) — `series` now comes from props. Replace the block that starts `{axisMarks24h ? (` … through its closing `)}` (the 24h tick strip) with the plain tick row that was in its `else` branch:

```jsx
      <div className="mt-1 flex justify-between px-0.5 font-mono text-[10px] text-ink-4 tnum">
        {tickIndices(series.labels.length).map(i => (
          <span key={`${i}-${series.labels[i]}`} className={hoverIndex === i ? 'font-semibold text-ink' : undefined}>
            {series.labels[i]}
          </span>
        ))}
      </div>
```

and delete the line `const axisMarks24h = period === '24h' ? build24hAxisMarks() : null`. Change the hover tooltip's secondary label `settled` to `cumulative` and the baseline caption `baseline` to `uniform pace`.

- [ ] **Step 3: `StatCard` without fake deltas**

Replace `StatCard` with:

```jsx
function StatCard({ label, value, sub, tone = 'text-ink' }) {
  const displayed = useCountUp(value ?? 0, 800)
  return (
    <div className="min-w-0 flex-1 px-1 py-3">
      <div className="text-[11px] text-ink-3">{label}</div>
      <div className={`mt-0.5 font-mono text-[17px] font-semibold tnum ${tone}`}>{displayed.toLocaleString()}</div>
      <div className="mt-0.5 text-[11px] text-ink-4">{sub}</div>
    </div>
  )
}
```

- [ ] **Step 4: Rows on backend shapes**

Replace `AlertRow` with:

```jsx
function AlertRow({ alert, isOpen, onToggle, onExpand }) {
  const tone = RISK_VAR[alert.risk_level]
  const signals = Object.entries(alert.details?.signals || {}).filter(([, v]) => v).map(([k]) => k)
  return (
    <article className="relative pr-9">
      <button type="button" onClick={onToggle} className="w-full cursor-pointer py-3.5 text-left transition-colors hover:bg-hover/60">
        <div className="flex items-start gap-2.5">
          <span className="mt-1.5 h-[5px] w-[5px] shrink-0 rounded-full" style={{ background: tone }} />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[13px] font-medium text-ink">{flagLabel(alert.flag_type)}</span>
              <span className="shrink-0 font-mono text-[10px] text-ink-4">{fmtIso(alert.last_detected_at)}</span>
            </div>
            <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-ink-2">{alert.explanation}</p>
          </div>
        </div>
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="ml-3.5 pb-3 pr-1">
              <p className="font-mono text-[11px] text-ink-4 tnum">
                {shortId(alert.primary_account)}{alert.account_ids.length > 1 ? ` +${alert.account_ids.length - 1}` : ''} · score {(alert.risk_score * 100).toFixed(0)}%
                {signals.length > 0 && ` · ${signals.join(', ')}`}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <ExpandLink onClick={e => { e.stopPropagation(); onExpand() }} label={`Open alert ${alert.id}`} />
    </article>
  )
}
```

Replace `ActivityRow` with:

```jsx
function ActivityRow({ tx, isOpen, onToggle, onExpand }) {
  return (
    <article className="relative pr-9">
      <button type="button" onClick={onToggle} className="flex w-full cursor-pointer items-center gap-3 py-3.5 text-left transition-colors hover:bg-hover/60">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[12px] text-ink-2">{shortId(tx.sender_id)} → {shortId(tx.receiver_id)}</div>
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-4">
            <span className="font-mono tnum">{fmtTs(tx.ts)}</span>
            <span>{tx.currency}</span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-[13px] font-semibold text-ink tnum">{fmtAmount(tx.amount_cents)}</div>
          <div className="mt-0.5 flex justify-end gap-1.5">
            <RiskChip level={tx.risk_tier} />
            <StatusChip status={tx.flagged ? 'flagged' : 'settled'} />
          </div>
        </div>
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="pb-3">
              <p className="font-mono text-[11px] text-ink-3 tnum">{tx.txn_id}</p>
              <p className="mt-1.5 text-[12px] leading-relaxed text-ink-3">
                {tx.event_type || 'Transfer'} of {fmtAmount(tx.amount_cents)} {tx.currency}. Sender {tx.sender_tier}, receiver {tx.receiver_tier}.
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <ExpandLink onClick={e => { e.stopPropagation(); onExpand() }} label={`Open ${tx.txn_id} in Transactions`} />
    </article>
  )
}
```

Add `fmtTs` to the format import.

- [ ] **Step 5: The component body**

Replace everything from `export default function Dashboard({ onNav }) {` to the end of the file with:

```jsx
export default function Dashboard({ onNav }) {
  const { source } = useDataSource()
  const [period, setPeriod] = useState('7d')
  const [currency, setCurrency] = useState(null)
  const [openAlertId, setOpenAlertId] = useState(null)
  const [openTxId, setOpenTxId] = useState(null)
  const [hoverPoint, setHoverPoint] = useState(null)
  const [selectRange, setSelectRange] = useState(null)

  const overview = useAsync(() => source.stats.overview(), [source.mode])
  const currencies = overview.data?.currencies ?? []
  const activeCurrency = currency ?? currencies[0]?.code ?? null
  const seriesQ = useAsync(
    () => (activeCurrency ? source.stats.series(activeCurrency, period) : Promise.resolve(null)),
    [source.mode, activeCurrency, period],
  )
  const alerts = useAsync(() => source.alerts.list({ limit: FEED_LIMIT, min_level: 'high' }), [source.mode])
  const txns = useAsync(() => source.transactions.list({ limit: FEED_LIMIT }), [source.mode])

  const series = useMemo(() => (seriesQ.data ? toChartSeries(seriesQ.data, period) : null), [seriesQ.data, period])
  const totalM = series ? series.volume[series.volume.length - 1] : 0
  const totalTxns = series ? series.txns.reduce((a, b) => a + b, 0) : 0

  const heroLabel = selectRange ? `${selectRange.startLabel} – ${selectRange.endLabel}`
    : hoverPoint ? hoverPoint.label
    : `${activeCurrency ?? ''} volume · ${PERIODS.find(p => p.id === period)?.label}`
  const targetVolume = selectRange ? selectRange.endVolume : hoverPoint ? hoverPoint.volume : totalM
  const targetTxns = selectRange ? selectRange.txnsTotal : hoverPoint ? hoverPoint.txns : totalTxns
  const pct = selectRange ? selectRange.pctChange
    : hoverPoint && hoverPoint.baseline ? ((hoverPoint.volume - hoverPoint.baseline) / hoverPoint.baseline) * 100 : null
  const ds = overview.data?.dataset

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <section className="shrink-0 px-8 pb-6 pt-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[13px] text-ink-3">{heroLabel}</p>
            <div className="mt-1 flex flex-wrap items-baseline gap-3">
              <RollingNumber value={targetVolume} format={v => `${v.toFixed(2)}M`}
                className="font-mono text-[36px] font-semibold leading-none tracking-tight text-ink tnum sm:text-[42px]" />
              {pct != null && (
                <span className={`inline-flex items-center text-[14px] font-medium leading-none tnum ${pct >= 0 ? 'text-accent' : 'text-critical'}`}>
                  <RollingNumber value={pct} format={v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`} />
                  <span className="ml-1 font-normal leading-none text-ink-4">{selectRange ? 'in range' : 'vs uniform pace'}</span>
                </span>
              )}
            </div>
            <p className="mt-2 text-[12px] text-ink-4">
              <RollingNumber value={targetTxns} format={v => Math.round(v).toLocaleString()} className="font-mono text-ink-3 tnum" />
              {' '}transactions{selectRange ? ' in this window' : hoverPoint ? ' in this bucket' : ''}
            </p>
          </div>

          <div className="flex items-center gap-5">
            <select value={activeCurrency ?? ''} onChange={e => { setCurrency(e.target.value); setHoverPoint(null); setSelectRange(null) }}
              className="border-b border-line bg-transparent pb-1 text-[13px] text-ink-2 outline-none">
              {currencies.map(c => <option key={c.code} value={c.code}>{c.code}</option>)}
            </select>
            {PERIODS.map(p => (
              <button key={p.id} type="button" onClick={() => { setPeriod(p.id); setHoverPoint(null); setSelectRange(null) }}
                className={`relative pb-1 text-[13px] font-medium transition-colors ${period === p.id ? 'text-ink' : 'text-ink-3 hover:text-ink-2'}`}>
                {p.label}
                {period === p.id && (
                  <motion.span layoutId="volume-period" className="absolute -bottom-0.5 left-0 right-0 h-[2px] rounded-full bg-accent"
                    transition={{ type: 'spring', stiffness: 480, damping: 36 }} />
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-6">
          {seriesQ.error ? <ErrorNote error={seriesQ.error} onRetry={seriesQ.reload} />
            : !series ? <Skeleton className="h-[220px] w-full sm:h-[260px] lg:h-[280px]" />
            : <VolumeChart series={series} period={period} onHover={setHoverPoint} onSelect={setSelectRange} />}
          <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-ink-4">
            <span className="flex items-center gap-2"><span className="h-0.5 w-4 rounded-full bg-accent" />Cumulative volume</span>
            <span className="flex items-center gap-2"><span className="h-0 w-4 border-t border-dashed border-ink-4" />Uniform pace</span>
            {series && <span className="font-mono tnum">ends {fmtDay(series.end)}</span>}
            <button type="button" onClick={() => onNav('graph')} className="ml-auto text-[12px] font-medium text-accent hover:opacity-70">
              Explore network graph →
            </button>
          </div>
        </div>
      </section>

      <section className="border-y border-line/60 px-8">
        {overview.error ? <div className="py-3"><ErrorNote error={overview.error} onRetry={overview.reload} /></div> : (
          <div className="flex flex-wrap divide-x divide-line/60">
            {STAT_CARDS.map(({ key, label, sub, tone }) => (
              <StatCard key={key} label={label} value={overview.data?.[key]} sub={sub} tone={tone} />
            ))}
            <StatCard label="Open flags" value={overview.data?.open_flags?.total} sub="across all detectors" tone="text-high" />
            <div className="min-w-0 flex-1 px-1 py-3">
              <div className="text-[11px] text-ink-3">Dataset</div>
              <div className="mt-0.5 font-mono text-[13px] font-semibold text-ink tnum">{ds ? `${fmtDay(ds.start_ts)} – ${fmtDay(ds.end_ts)}` : '—'}</div>
              <div className="mt-0.5 text-[11px] text-ink-4">{ds?.name ?? ''} · {fmtCompact(overview.data?.transactions ?? 0)} transactions</div>
            </div>
          </div>
        )}
      </section>

      <section className="flex-1 border-t border-line-2 px-8 py-8">
        <div className="grid grid-cols-1 md:grid-cols-2 md:gap-0">
          <div className="min-w-0 md:border-r md:border-line-2 md:pr-8">
            <FeedSectionHeader title="Risk alerts" count={alerts.data?.items.length ?? 0}
              countLabel={`of ${(alerts.data?.total ?? 0).toLocaleString()} high or critical`} countTone="text-critical"
              subtitle="Highest-scoring open flags" onViewAll={() => onNav('alerts')} />
            {alerts.error ? <ErrorNote error={alerts.error} onRetry={alerts.reload} />
              : alerts.loading ? <Skeleton className="h-40 w-full" />
              : alerts.data.items.length === 0 ? <EmptyNote>No open alerts.</EmptyNote> : (
              <div className="divide-y divide-line/70">
                {alerts.data.items.map(alert => (
                  <AlertRow key={alert.id} alert={alert} isOpen={openAlertId === alert.id}
                    onToggle={() => setOpenAlertId(prev => (prev === alert.id ? null : alert.id))}
                    onExpand={() => onNav('alerts', { id: alert.id })} />
                ))}
              </div>
            )}
          </div>

          <div className="min-w-0 border-t border-line-2 pt-8 md:border-t-0 md:pl-8 md:pt-0">
            <FeedSectionHeader title="Latest transfers" count={txns.data?.items.length ?? 0} countLabel="most recent" countTone="text-accent"
              subtitle="Newest settled transfers in the dataset" onViewAll={() => onNav('transactions')} />
            {txns.error ? <ErrorNote error={txns.error} onRetry={txns.reload} />
              : txns.loading ? <Skeleton className="h-40 w-full" />
              : txns.data.items.length === 0 ? <EmptyNote>No transactions.</EmptyNote> : (
              <div className="divide-y divide-line/70">
                {txns.data.items.map(tx => (
                  <ActivityRow key={tx.txn_id} tx={tx} isOpen={openTxId === tx.txn_id}
                    onToggle={() => setOpenTxId(prev => (prev === tx.txn_id ? null : tx.txn_id))}
                    onExpand={() => onNav('transactions', { account: tx.sender_id })} />
                ))}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
```

Remove the now-unused `useRef`/`useEffect` imports only if eslint reports them unused (the chart still uses them).

- [ ] **Step 6: Verify**

Run: `npx eslint src/components/Dashboard.jsx` — 0 errors. `npm run build` — success. `npm run dev` → `#/overview` in demo mode shows the USD chart with real buckets, the stat strip (513,989 accounts etc.), 6 real alerts and 6 real transfers; switching currency/period refetches; hover shows "vs uniform pace". Same in live mode.

- [ ] **Step 7: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add src/components/Dashboard.jsx
git commit -m "feat(frontend): overview on real stats, per-currency anchored series, live feeds" -m "$TRAILER"
```

---

### Task 4: Alerts on real flags

**Files:**
- Rewrite: `src/components/AlertsView.jsx`

**Interfaces:**
- Consumes: `source.alerts.list({flag_type, min_level, limit, offset})`, `source.alerts.setStatus(id, status)`; route params `{ id }`.

- [ ] **Step 1: Rewrite the file**

```jsx
import { useEffect, useMemo, useState } from 'react'
import { LayoutGroup, motion, AnimatePresence } from 'motion/react'
import { RISK_VAR, PageHeader, useToast, Skeleton, ErrorNote, EmptyNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { flagLabel, fmtIso, shortId } from '../lib/format'

const LEVELS = ['all', 'critical', 'high', 'medium', 'low']
const TYPES = ['ALL', 'AGGREGATE', 'COMMUNITY', 'CYCLE', 'LIVE_GNN']
const PAGE = 50

const ACTIONS = [
  { label: 'Escalate',       status: 'escalated', tone: 'text-critical hover:opacity-80' },
  { label: 'Mark reviewed',  status: 'reviewed',  tone: 'text-high hover:opacity-80' },
  { label: 'Dismiss',        status: 'dismissed', tone: 'text-ink-3 hover:text-ink-2' },
]

function AlertRow({ alert, isOpen, onToggle, onAction, onOpenGraph }) {
  const color = RISK_VAR[alert.risk_level]
  const signals = Object.entries(alert.details?.signals || {}).filter(([, v]) => v).map(([k]) => k)
  return (
    <article className="py-5">
      <button type="button" onClick={onToggle} className="w-full cursor-pointer text-left">
        <div className="flex items-start gap-3">
          <span className="mt-2 h-[6px] w-[6px] shrink-0 rounded-full" style={{ background: color }} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="text-[14px] font-semibold text-ink">{flagLabel(alert.flag_type)}</span>
              <span className="text-[11px] font-bold uppercase tracking-[0.06em]" style={{ color }}>{alert.risk_level}</span>
              <span className="font-mono text-[11px] text-ink-4">{fmtIso(alert.last_detected_at)}</span>
              {alert.status !== 'open' && <span className="font-mono text-[10px] uppercase text-ink-4">{alert.status}</span>}
            </div>
            <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{alert.explanation}</p>
            <p className="mt-2 font-mono text-[12px] text-ink-3 tnum">
              {shortId(alert.primary_account)}{alert.account_ids.length > 1 ? ` +${alert.account_ids.length - 1} accounts` : ''}
              {' · '}score {(alert.risk_score * 100).toFixed(0)}%{alert.detection_count > 1 ? ` · seen ${alert.detection_count}×` : ''}
            </p>
          </div>
        </div>
      </button>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="ml-[18px] mt-4 max-w-3xl">
              <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">
                Signals · <span className="font-mono font-normal normal-case">#{alert.id}</span>
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {signals.length === 0 && <span className="text-[12px] text-ink-4">no structured signals recorded</span>}
                {signals.map(s => <span key={s} className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink-2">{s}</span>)}
                {alert.details?.gnn_score != null && <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink-2">gnn {Number(alert.details.gnn_score).toFixed(3)}</span>}
                {alert.details?.community_id && <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink-2">community {alert.details.community_id}</span>}
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-5">
                {ACTIONS.map(a => (
                  <button key={a.status} type="button" onClick={e => { e.stopPropagation(); onAction(alert, a.status) }}
                    className={`text-[13px] font-medium ${a.tone}`}>{a.label}</button>
                ))}
                <button type="button" onClick={e => { e.stopPropagation(); onOpenGraph(alert.primary_account) }}
                  className="ml-auto text-[13px] font-medium text-accent hover:opacity-70">Open in Graph →</button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  )
}

export default function AlertsView({ onNav, params }) {
  const { source, mode } = useDataSource()
  const [level, setLevel] = useState('all')
  const [type, setType] = useState('ALL')
  const [page, setPage] = useState(0)
  const [openId, setOpenId] = useState(params?.id ? Number(params.id) : null)
  const [local, setLocal] = useState({})            // id → status after an action
  const { show, ToastHost } = useToast()

  const query = useMemo(() => ({
    flag_type: type === 'ALL' ? undefined : type,
    min_level: level === 'all' ? 'low' : level,
    limit: PAGE, offset: page * PAGE,
  }), [type, level, page])
  const q = useAsync(() => source.alerts.list(query), [source.mode, query])

  useEffect(() => {
    if (!params?.id) return
    const t = window.setTimeout(() => document.getElementById(`alert-${params.id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 150)
    return () => window.clearTimeout(t)
  }, [params?.id, q.data])

  const items = (q.data?.items ?? []).filter(a => level === 'all' || a.risk_level === level)
  const total = q.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  const act = async (alert, status) => {
    try {
      await source.alerts.setStatus(alert.id, status)
      setLocal(l => ({ ...l, [alert.id]: status }))
      show(mode === 'demo' ? `Marked ${status} (demo — not persisted)` : `Alert #${alert.id} marked ${status}`)
    } catch (e) {
      show(e?.message || 'Update failed')
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Risk Alerts" subtitle="Every flag carries a written reason — a regulatory requirement, not a nicety">
        <span className="font-mono text-[12px] text-ink-3 tnum">{total.toLocaleString()} open</span>
      </PageHeader>

      <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 px-8 pb-4">
        {LEVELS.map(f => (
          <button key={f} type="button" onClick={() => { setLevel(f); setPage(0) }}
            className={`text-[13px] capitalize transition-colors ${level === f ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}
            style={level === f && f !== 'all' ? { color: RISK_VAR[f] } : undefined}>{f}</button>
        ))}
        <span className="text-ink-4">·</span>
        {TYPES.map(t => (
          <button key={t} type="button" onClick={() => { setType(t); setPage(0) }}
            className={`text-[12px] transition-colors ${type === t ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>
            {t === 'ALL' ? 'All types' : flagLabel(t)}
          </button>
        ))}
        <span className="ml-auto text-[12px] text-ink-4 tnum">page {page + 1} of {pages}</span>
      </div>

      <div className="flex-1 overflow-y-auto px-8">
        {q.error ? <ErrorNote error={q.error} onRetry={q.reload} />
          : q.loading ? <div className="space-y-4 py-4"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
          : items.length === 0 ? <EmptyNote>No alerts match these filters.</EmptyNote> : (
          <LayoutGroup>
            <motion.div layout className="divide-y divide-line/70">
              {items.map(alert => (
                <div key={alert.id} id={`alert-${alert.id}`}>
                  <AlertRow alert={{ ...alert, status: local[alert.id] ?? alert.status }} isOpen={openId === alert.id}
                    onToggle={() => setOpenId(prev => (prev === alert.id ? null : alert.id))}
                    onAction={act} onOpenGraph={(acct) => onNav('graph', { account: acct })} />
                </div>
              ))}
            </motion.div>
          </LayoutGroup>
        )}
        <div className="flex items-center justify-end gap-3 py-4 text-[12px]">
          <button type="button" disabled={page === 0} onClick={() => setPage(p => p - 1)} className="text-ink-3 hover:text-ink disabled:opacity-40">← Newer</button>
          <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)} className="text-ink-3 hover:text-ink disabled:opacity-40">Older →</button>
        </div>
      </div>
      <ToastHost />
    </div>
  )
}
```

- [ ] **Step 2: Verify and commit**

`npx eslint src/components/AlertsView.jsx` → 0 errors; `npm run build` ok. Demo: `#/alerts` lists real AGGREGATE flags; type/level filters change the list; the action buttons toast "(demo — not persisted)". Live: an action PATCHes and the row shows the new status; `#/alerts?id=<id>` scrolls to that alert.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add src/components/AlertsView.jsx
git commit -m "feat(frontend): alerts view on real risk_flags with type/level filters and status actions" -m "$TRAILER"
```

---

### Task 5: Transactions (history only)

**Files:**
- Rewrite: `src/components/TransactionsView.jsx`

**Interfaces:**
- Consumes: `source.transactions.list({limit, account_id, currency})`, `source.stats.overview()` (currency list); route params `{ account }`.

- [ ] **Step 1: Rewrite the file**

```jsx
import { useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { RiskChip, StatusChip, RISK_VAR, PageHeader, TH, Skeleton, ErrorNote, EmptyNote, DemoNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { fmtAmount, fmtTs, shortId, fmtCompact } from '../lib/format'

const COLS = [
  { label: 'Transaction', col: 'txn_id' },
  { label: 'From',        col: 'sender_id' },
  { label: 'To',          col: 'receiver_id' },
  { label: 'Amount',      col: 'amount_cents' },
  { label: 'Currency',    col: 'currency' },
  { label: 'Risk',        col: 'risk_tier' },
  { label: 'Status',      col: 'flagged' },
  { label: 'Time',        col: 'ts' },
]
const TIER_RANK = { low: 0, medium: 1, high: 2, critical: 3 }
const cellId   = 'px-4 py-2.5 font-mono text-[10.5px] text-ink-3 first:pl-7'
const cellAcct = 'px-4 py-2.5 font-mono text-[11px] text-ink-2'
const cellAmt  = 'whitespace-nowrap px-4 py-2.5 font-mono text-xs font-semibold text-ink tnum'
const rowBase  = 'transition-colors duration-150 hover:bg-hover'

export default function TransactionsView({ onNav, params }) {
  const { source, mode } = useDataSource()
  const [account, setAccount] = useState(params?.account ?? '')
  const [submitted, setSubmitted] = useState(params?.account ?? '')
  const [currency, setCurrency] = useState('')
  const [risk, setRisk] = useState('all')
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState('desc')

  const overview = useAsync(() => source.stats.overview(), [source.mode])
  const q = useAsync(
    () => source.transactions.list({ limit: 200, account_id: submitted || undefined, currency: currency || undefined }),
    [source.mode, submitted, currency],
  )

  const rows = useMemo(() => {
    const items = (q.data?.items ?? []).filter(t => risk === 'all' || t.risk_tier === risk)
    const dir = sortDir === 'asc' ? 1 : -1
    return [...items].sort((a, b) => {
      const va = sortBy === 'risk_tier' ? TIER_RANK[a.risk_tier] : a[sortBy]
      const vb = sortBy === 'risk_tier' ? TIER_RANK[b.risk_tier] : b[sortBy]
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir
    })
  }, [q.data, risk, sortBy, sortDir])

  const toggleSort = (col) => {
    if (sortBy === col) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortBy(col); setSortDir('desc') }
  }
  const counts = (q.data?.items ?? []).reduce((acc, t) => { acc[t.risk_tier] = (acc[t.risk_tier] || 0) + 1; return acc }, {})
  const currencies = overview.data?.currencies ?? []

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Transactions"
        subtitle={<><span className="font-mono text-ink-2 tnum">{fmtCompact(overview.data?.transactions ?? 0)}</span> settled transfers in the dataset</>}>
        <form onSubmit={e => { e.preventDefault(); setSubmitted(account.trim()); onNav('transactions', { account: account.trim() }) }}
          className="flex items-center gap-2 border-b border-line py-1 focus-within:border-line-2">
          <input value={account} onChange={e => setAccount(e.target.value)} placeholder="Account id…"
            className="w-64 bg-transparent font-mono text-xs text-ink outline-none placeholder:text-ink-4" />
          {submitted && <button type="button" onClick={() => { setAccount(''); setSubmitted(''); onNav('transactions') }} className="text-[11px] text-ink-3 hover:text-ink">clear</button>}
        </form>
      </PageHeader>

      {/* by-currency strip */}
      <div className="flex shrink-0 flex-wrap gap-x-6 gap-y-1 px-8 pb-3">
        <button type="button" onClick={() => setCurrency('')} className={`text-[12px] ${currency === '' ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>All currencies</button>
        {currencies.slice(0, 8).map(c => (
          <button key={c.code} type="button" onClick={() => setCurrency(c.code)}
            className={`text-[12px] tnum ${currency === c.code ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>
            {c.code} <span className="font-mono text-[10px] text-ink-4">{fmtCompact(c.tx_count)}</span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 px-8 py-2.5">
        {[['all', 'All'], ['critical', 'Critical'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']].map(([f, label]) => (
          <button key={f} onClick={() => setRisk(f)}
            className={`relative flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] transition-colors ${risk === f ? 'font-semibold' : 'text-ink-3 hover:text-ink-2'}`}
            style={risk === f ? { color: f === 'all' ? 'var(--ink)' : RISK_VAR[f] } : undefined}>
            {risk === f && <motion.span layoutId="history-filter-pill" className="absolute inset-0 rounded-full bg-hover" transition={{ type: 'spring', stiffness: 420, damping: 34 }} />}
            <span className="relative z-[1]">{label}</span>
            <span className="relative z-[1] font-mono text-[10px] tnum opacity-70">{f === 'all' ? (q.data?.items.length ?? 0) : (counts[f] || 0)}</span>
          </button>
        ))}
        {submitted && <span className="ml-auto font-mono text-[11px] text-ink-3">account {shortId(submitted)} · both directions</span>}
      </div>

      <div className="flex-1 overflow-y-auto">
        {q.error instanceof NotInSnapshotError ? <div className="px-8 py-4"><DemoNote>{q.error.message}</DemoNote></div>
          : q.error ? <div className="px-8 py-4"><ErrorNote error={q.error} onRetry={q.reload} /></div>
          : q.loading ? <div className="space-y-2 px-8 py-4"><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /></div>
          : rows.length === 0 ? <EmptyNote>No transactions match.</EmptyNote> : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                {COLS.map((h, i) => (
                  <TH key={h.col} onClick={() => toggleSort(h.col)}
                    className={`cursor-pointer select-none transition-colors hover:text-ink-2 ${sortBy === h.col ? '!text-accent' : ''} ${i === 0 ? 'pl-7' : ''}`}>
                    {h.label}{' '}
                    {sortBy === h.col ? <span className="text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span> : <span className="text-[9px] text-ink-4">⇅</span>}
                  </TH>
                ))}
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false} mode="popLayout">
                {rows.map((tx, i) => (
                  <motion.tr key={tx.txn_id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, transition: { duration: 0.12 } }} transition={{ duration: 0.3, delay: Math.min(i, 20) * 0.015 }} className={rowBase}>
                    <td className={cellId}>{shortId(tx.txn_id)}</td>
                    <td className={cellAcct}><button type="button" onClick={() => onNav('graph', { account: tx.sender_id })} className="hover:text-accent">{shortId(tx.sender_id)}</button></td>
                    <td className={cellAcct}><button type="button" onClick={() => onNav('graph', { account: tx.receiver_id })} className="hover:text-accent">{shortId(tx.receiver_id)}</button></td>
                    <td className={cellAmt}>{fmtAmount(tx.amount_cents)}</td>
                    <td className="px-4 py-2.5 font-mono text-[10px] text-ink-3">{tx.currency}</td>
                    <td className="px-4 py-2.5"><RiskChip level={tx.risk_tier} /></td>
                    <td className="px-4 py-2.5"><StatusChip status={tx.flagged ? 'flagged' : 'settled'} /></td>
                    <td className="px-4 py-2.5 font-mono text-[10.5px] text-ink-3 tnum">{fmtTs(tx.ts)}</td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
        )}
        {mode === 'demo' && !submitted && q.data && (
          <p className="px-8 py-3 text-[11px] text-ink-4">Demo snapshot: the {q.data.items.length} most recent transfers. Per-account history is available for featured accounts.</p>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify and commit**

`npx eslint src/components/TransactionsView.jsx` → 0; `npm run build` ok. Demo: `#/transactions` shows 200 real rows; the currency strip filters; clicking a sender opens Graph. Live: `#/transactions?account=<id>` shows that account's transfers.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add src/components/TransactionsView.jsx
git commit -m "feat(frontend): transactions history on TRANSFER edges; drop the fictional in-flight tab" -m "$TRAILER"
```

---

### Task 6: Graph Explorer — light theme, lenses, tools, inspector

**Files:**
- Rewrite: `src/components/GraphCanvas.jsx`, `src/components/InspectorSidebar.jsx`, `src/components/GraphExplorer.jsx`
- Create: `src/components/GraphTools.jsx`, `src/lib/graphStyle.js`
- Test: `src/lib/graphStyle.test.js`

**Interfaces:**
- Produces: `GraphCanvas({ elements, lens, cutoff, selectedId, onSelectNode, highlightIds })` with lenses `'risk' | 'gnn' | 'pagerank' | 'community' | 'marked'`; `graphStyle.js` exports `communityColor(id)`, `gnnHeat(score)`, `prSize(score, min, max)`, `buildStyle(lens, opts)`, `layoutFor(n)`.
- Consumes: `source.graph.*`, `source.risk.evaluate`, `source.ai.explain`, `source.featured()`, `source.system.llm()`.

- [ ] **Step 1: Failing style tests**

`src/lib/graphStyle.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { communityColor, gnnHeat, prSize, layoutFor } from './graphStyle'

describe('graphStyle', () => {
  it('assigns stable, distinct-ish colours per community', () => {
    expect(communityColor('abc')).toBe(communityColor('abc'))
    expect(communityColor('abc')).not.toBe(communityColor('abd'))
    expect(communityColor(null)).toBe('#a4b0c4')
  })
  it('maps gnn score to the risk ladder', () => {
    expect(gnnHeat(0.9)).toBe('var(--critical)'); expect(gnnHeat(0.7)).toBe('var(--high)')
    expect(gnnHeat(0.5)).toBe('var(--medium)'); expect(gnnHeat(0.1)).toBe('var(--low)'); expect(gnnHeat(null)).toBe('#a4b0c4')
  })
  it('sizes by pagerank rank within the view', () => {
    expect(prSize(0, 0, 1)).toBe(18); expect(prSize(1, 0, 1)).toBe(54); expect(prSize(5, 5, 5)).toBe(18)
  })
  it('picks a force layout for small graphs and concentric for big ones', () => {
    expect(layoutFor(100).name).toBe('cose-bilkent'); expect(layoutFor(2000).name).toBe('concentric')
  })
})
```

- [ ] **Step 2: Style module**

`src/lib/graphStyle.js`:

```js
const NEUTRAL = '#a4b0c4'   // --ink-4

export function communityColor(id) {
  if (id == null) return NEUTRAL
  let h = 0
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return `hsl(${h % 360} 55% 55%)`
}

export function gnnHeat(score) {
  if (score == null) return NEUTRAL
  if (score >= 0.85) return 'var(--critical)'
  if (score >= 0.65) return 'var(--high)'
  if (score >= 0.40) return 'var(--medium)'
  return 'var(--low)'
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

const RISK_BG = {
  critical: 'var(--critical)', high: 'var(--high)', medium: 'var(--medium)', low: 'var(--low)',
}

/** Cytoscape stylesheet for a lens. Elements carry _size/_comm/_heat/_marked (see GraphCanvas.decorate). */
export function buildStyle(lens, { labels = true } = {}) {
  const base = [
    { selector: 'node', style: {
      label: labels ? 'data(label)' : '',
      color: '#3e4d66', 'font-size': '10px', 'font-family': 'IBM Plex Mono, monospace',
      'text-valign': 'bottom', 'text-margin-y': 4,
      width: 'data(_size)', height: 'data(_size)',
      'background-color': NEUTRAL, 'border-width': 1.5, 'border-color': '#fafbfc',
    } },
    { selector: 'edge', style: {
      width: 'mapData(weight, 1, 10, 1, 5)', 'line-color': 'rgba(20,33,56,0.18)',
      'target-arrow-color': 'rgba(20,33,56,0.28)', 'target-arrow-shape': 'triangle',
      'curve-style': 'bezier', 'arrow-scale': 0.8,
    } },
    { selector: '.faded', style: { opacity: 0.15 } },
    { selector: '.hl', style: { 'line-color': 'var(--accent)', 'target-arrow-color': 'var(--accent)', 'border-color': 'var(--accent)', 'border-width': 3, opacity: 1 } },
    { selector: ':selected', style: { 'border-width': 4, 'border-color': 'var(--accent)' } },
  ]
  if (lens === 'risk') {
    for (const [tier, bg] of Object.entries(RISK_BG))
      base.push({ selector: `node[risk_tier = "${tier}"]`, style: { 'background-color': bg } })
  } else if (lens === 'gnn') {
    base.push({ selector: 'node', style: { 'background-color': 'data(_heat)' } })
  } else if (lens === 'community') {
    base.push({ selector: 'node', style: { 'background-color': 'data(_comm)' } })
  } else if (lens === 'marked') {
    base.push({ selector: 'node', style: { 'background-color': NEUTRAL } })
    base.push({ selector: 'node[?_marked]', style: { 'background-color': 'var(--critical)' } })
    base.push({ selector: 'node[?truth]', style: { 'border-color': '#3b4bc0', 'border-width': 3 } })
  } else { // pagerank: size only
    base.push({ selector: 'node', style: { 'background-color': 'var(--accent)' } })
  }
  return base
}
```

Run `npm test` → graphStyle tests pass.

- [ ] **Step 3: GraphCanvas**

`src/components/GraphCanvas.jsx`:

```jsx
import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import coseBilkent from 'cytoscape-cose-bilkent'
import { buildStyle, communityColor, gnnHeat, isMarked, layoutFor, prSize } from '../lib/graphStyle'

try { cytoscape.use(coseBilkent) } catch { /* registered under HMR already */ }

const LABEL_LIMIT = 200

function decorate(elements, cutoff) {
  const prs = elements.nodes.map(n => n.data.pagerank_score || 0)
  const min = prs.length ? Math.min(...prs) : 0, max = prs.length ? Math.max(...prs) : 0
  const nodes = elements.nodes.map(n => ({ ...n, data: {
    ...n.data,
    _size: prSize(n.data.pagerank_score || 0, min, max),
    _comm: communityColor(n.data.community_id),
    _heat: gnnHeat(n.data.gnn_risk_score),
    _marked: isMarked(n.data, cutoff),
  } }))
  return [...nodes, ...(elements.edges || [])]
}

/**
 * Light-themed Cytoscape canvas. `lens` picks the colouring; `cutoff` drives the
 * marked lens; `highlightIds` (e.g. a shortest path) are emphasised.
 */
export function GraphCanvas({ elements, lens = 'risk', cutoff = 0.74, selectedId, onSelectNode, highlightIds }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)

  // (re)build on new elements
  useEffect(() => {
    if (!containerRef.current) return
    const n = elements.nodes?.length || 0
    const cy = cytoscape({
      container: containerRef.current,
      elements: decorate(elements, cutoff),
      style: buildStyle(lens, { labels: n <= LABEL_LIMIT }),
      layout: layoutFor(n),
      wheelSensitivity: 0.2,
    })
    cy.on('tap', 'node', evt => onSelectNode?.(evt.target.data()))
    cy.on('tap', evt => { if (evt.target === cy) onSelectNode?.(null) })
    if (n <= 800) {
      cy.on('mouseover', 'node', e => {
        cy.elements().addClass('faded'); e.target.closedNeighborhood().removeClass('faded')
        e.target.addClass('hl'); e.target.connectedEdges().addClass('hl')
      })
      cy.on('mouseout', 'node', () => cy.elements().removeClass('faded hl'))
    }
    cyRef.current = cy
    return () => { cy.destroy(); cyRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements])

  // restyle without relayout
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => cy.nodes().forEach(nd => nd.data('_marked', isMarked(nd.data(), cutoff))))
    cy.style(buildStyle(lens, { labels: cy.nodes().length <= LABEL_LIMIT }))
  }, [lens, cutoff])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().unselect()
    if (selectedId) cy.getElementById(selectedId).select()
  }, [selectedId])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().removeClass('hl faded')
    if (highlightIds?.length) {
      const set = new Set(highlightIds)
      cy.elements().addClass('faded')
      cy.nodes().filter(nd => set.has(nd.id())).removeClass('faded').addClass('hl')
      cy.edges().filter(e => set.has(e.source().id()) && set.has(e.target().id())).removeClass('faded').addClass('hl')
    }
  }, [highlightIds])

  return <div ref={containerRef} className="h-full w-full bg-base" />
}
```

- [ ] **Step 4: GraphTools**

`src/components/GraphTools.jsx`:

```jsx
import { useState } from 'react'
import { DemoNote, ErrorNote } from './ui'
import { NotInSnapshotError } from '../services/dataSource'
import { fmtAmount, shortId } from '../lib/format'

const WINDOWS = ['24h', '7d', '30d']

/** Shortest path + flow between two accounts. Calls back with path elements to draw. */
export default function GraphTools({ source, defaultA = '', onPath }) {
  const [a, setA] = useState(defaultA)
  const [b, setB] = useState('')
  const [window, setWindow] = useState('7d')
  const [path, setPath] = useState(null)
  const [flow, setFlow] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    if (!a.trim() || !b.trim()) return
    setBusy(true); setError(null); setPath(null); setFlow(null)
    try {
      const [p, f] = await Promise.all([source.graph.shortestPath(a.trim(), b.trim()), source.graph.flow(a.trim(), b.trim(), window)])
      setPath(p); setFlow(f); onPath?.(p)
    } catch (e) { setError(e) } finally { setBusy(false) }
  }

  const field = 'w-full border-b border-line bg-transparent py-1 font-mono text-[11px] text-ink outline-none placeholder:text-ink-4 focus:border-line-2'
  return (
    <div className="space-y-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Path & flow between two accounts</p>
      <input value={a} onChange={e => setA(e.target.value)} placeholder="from account id" className={field} />
      <input value={b} onChange={e => setB(e.target.value)} placeholder="to account id" className={field} />
      <div className="flex items-center gap-3">
        {WINDOWS.map(w => (
          <button key={w} type="button" onClick={() => setWindow(w)} className={`text-[11px] ${window === w ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>{w}</button>
        ))}
        <button type="button" onClick={run} disabled={busy} className="ml-auto text-[12px] font-medium text-accent hover:opacity-70 disabled:opacity-40">{busy ? 'Searching…' : 'Find'}</button>
      </div>
      {error instanceof NotInSnapshotError ? <DemoNote>{error.message}</DemoNote> : error ? <ErrorNote error={error} /> : null}
      {path && (
        <p className="text-[12px] text-ink-2">
          {path.nodes.length ? <>Path of <span className="font-mono">{path.nodes.length - 1}</span> hop{path.nodes.length === 2 ? '' : 's'}: {path.nodes.map(n => shortId(n.data.id)).join(' → ')}</> : 'No directed path within 10 hops.'}
        </p>
      )}
      {flow && (
        <p className="font-mono text-[11px] text-ink-3 tnum">
          {flow.tx_count} direct transfer{flow.tx_count === 1 ? '' : 's'} in {flow.window} · {fmtAmount(flow.total_volume_cents)} total
        </p>
      )}
    </div>
  )
}
```

- [ ] **Step 5: InspectorSidebar**

`src/components/InspectorSidebar.jsx`:

```jsx
import { useState } from 'react'
import { RiskChip, Skeleton, ErrorNote, DemoNote } from './ui'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { shortId } from '../lib/format'

function Row({ k, v, mono = true }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-[12px]">
      <span className="text-ink-3">{k}</span>
      <span className={`${mono ? 'font-mono tnum' : ''} text-right text-ink`}>{v ?? '—'}</span>
    </div>
  )
}

export function InspectorSidebar({ source, node, onClose, onExplore }) {
  const [whatIf, setWhatIf] = useState(null)
  const [whatIfError, setWhatIfError] = useState(null)
  const explain = useAsync(() => (node ? source.ai.explain(node.id) : Promise.resolve(null)), [source.mode, node?.id])
  if (!node) return null

  const simulateCycle = async () => {
    setWhatIfError(null)
    try { setWhatIf(await source.risk.evaluate(node.id, { gnn_score: node.gnn_risk_score ?? 0.5, has_cycle: true, cycle_length: 3 })) }
    catch (e) { setWhatIfError(e) }
  }
  const ex = explain.data

  return (
    <aside className="glass-soft flex h-full w-[380px] shrink-0 flex-col overflow-y-auto border-l border-line px-6 py-5">
      <div className="flex items-start justify-between gap-3 border-b border-line pb-4">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Account</p>
          <p className="mt-1 break-all font-mono text-[12px] text-ink">{node.id}</p>
        </div>
        <button type="button" onClick={onClose} className="text-ink-4 hover:text-ink" aria-label="Close">✕</button>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <span className="font-mono text-[28px] font-semibold leading-none text-ink tnum">{((node.gnn_risk_score ?? node.risk_score ?? 0) * 100).toFixed(0)}%</span>
        <div className="flex flex-col gap-1">
          <RiskChip level={node.gnn_risk_tier || node.risk_tier || 'low'} />
          <span className="text-[10px] text-ink-4">GNN risk score</span>
        </div>
      </div>

      <div className="mt-4 divide-y divide-line/70">
        <Row k="On detected cycle" v={node.in_cycle ? 'yes' : 'no'} />
        <Row k="Marked by pipeline" v={node.marked ? 'yes' : 'no'} />
        <Row k="Community" v={node.community_id ? shortId(node.community_id) : '—'} />
        <Row k="PageRank" v={node.pagerank_score ? Number(node.pagerank_score).toExponential(2) : '—'} />
        {node.risk_score != null && node.gnn_risk_score != null && node.risk_score !== node.gnn_risk_score && (
          <Row k="Aggregated verdict" v={`${(node.risk_score * 100).toFixed(0)}% · ${node.risk_tier}`} />
        )}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <div className="flex items-center justify-between">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Explanation</p>
          {ex && <span className="font-mono text-[10px] text-ink-4">{ex.provider === 'none' ? 'rule-based' : `${ex.provider}${ex.model ? ` · ${ex.model}` : ''}`}</span>}
        </div>
        {explain.loading ? <div className="mt-2 space-y-2"><Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-5/6" /></div>
          : explain.error instanceof NotInSnapshotError ? <div className="mt-2"><DemoNote>{explain.error.message}</DemoNote></div>
          : explain.error ? <div className="mt-2"><ErrorNote error={explain.error} onRetry={explain.reload} /></div>
          : ex ? (
            <div className="mt-2 space-y-2">
              <div className="flex items-center gap-2 text-[11px] text-ink-3">
                <RiskChip level={ex.risk_level} /><span className="font-mono tnum">confidence {(ex.confidence * 100).toFixed(0)}%</span>
                {ex.detected_typology && <span className="rounded bg-hover px-1.5 py-px font-mono text-[10px] text-ink-2">{ex.detected_typology}</span>}
              </div>
              <p className="text-[13px] leading-relaxed text-ink-2">{ex.explanation}</p>
              <p className="font-mono text-[10.5px] leading-relaxed text-ink-4">{ex.compliance_summary}</p>
            </div>
          ) : null}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">What-if</p>
        <button type="button" onClick={simulateCycle} className="mt-2 text-[12px] font-medium text-accent hover:opacity-70">
          Re-evaluate as if on a 3-hop cycle →
        </button>
        {whatIfError instanceof NotInSnapshotError ? <div className="mt-2"><DemoNote>{whatIfError.message}</DemoNote></div>
          : whatIfError ? <div className="mt-2"><ErrorNote error={whatIfError} /></div> : null}
        {whatIf && (
          <div className="mt-2 text-[12px] text-ink-2">
            <div className="flex items-center gap-2"><RiskChip level={whatIf.risk_tier} /><span className="font-mono tnum">{(whatIf.risk_score * 100).toFixed(0)}% · confidence {(whatIf.confidence * 100).toFixed(0)}%</span></div>
            <p className="mt-1 text-ink-3">{whatIf.explanation}</p>
          </div>
        )}
      </div>

      <button type="button" onClick={() => onExplore(node.id)} className="mt-6 text-[12px] font-medium text-accent hover:opacity-70">Center on this account →</button>
    </aside>
  )
}
```

- [ ] **Step 6: GraphExplorer**

`src/components/GraphExplorer.jsx`:

```jsx
import { useMemo, useState } from 'react'
import { GraphCanvas } from './GraphCanvas'
import { InspectorSidebar } from './InspectorSidebar'
import GraphTools from './GraphTools'
import { Skeleton, ErrorNote, DemoNote, EmptyNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { shortId } from '../lib/format'

const LENSES = [['risk', 'Risk tier'], ['gnn', 'GNN heat'], ['community', 'Community'], ['pagerank', 'PageRank']]

export default function GraphExplorer({ onNav, params }) {
  const { source, mode } = useDataSource()
  const target = params?.account || ''
  const depth = Number(params?.depth || 2)
  const [query, setQuery] = useState(target)
  const [lens, setLens] = useState('risk')
  const [selected, setSelected] = useState(null)
  const [pathIds, setPathIds] = useState(null)

  const featured = useAsync(() => source.featured(), [source.mode])
  const graph = useAsync(() => (target ? source.graph.subgraph(target, depth) : Promise.resolve(null)), [source.mode, target, depth])
  const elements = useMemo(() => graph.data || { nodes: [], edges: [] }, [graph.data])

  const go = (id, d = depth) => { setSelected(null); setPathIds(null); onNav('graph', { account: id, depth: d }) }
  const onPath = (p) => setPathIds(p.nodes.map(n => n.data.id))

  return (
    <div className="flex h-full min-w-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-4 px-8 pb-3 pt-1">
          <form onSubmit={e => { e.preventDefault(); if (query.trim()) go(query.trim()) }}
            className="flex items-center gap-2 border-b border-line py-1 focus-within:border-line-2">
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Account id…"
              className="w-72 bg-transparent font-mono text-xs text-ink outline-none placeholder:text-ink-4" />
            <select value={depth} onChange={e => target && go(target, Number(e.target.value))} className="bg-transparent text-[11px] text-ink-3 outline-none">
              {[1, 2, 3].map(d => <option key={d} value={d}>{d}-hop</option>)}
            </select>
            <button type="submit" className="text-[12px] font-medium text-accent hover:opacity-70">Explore</button>
          </form>
          <div className="flex items-center gap-4">
            {LENSES.map(([id, label]) => (
              <button key={id} type="button" onClick={() => setLens(id)} className={`text-[12px] ${lens === id ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>{label}</button>
            ))}
          </div>
          {graph.data && <span className="ml-auto font-mono text-[11px] text-ink-4 tnum">{elements.nodes.length} accounts · {elements.edges.length} flows</span>}
        </div>

        {featured.data?.length > 0 && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 px-8 pb-3">
            <span className="text-[11px] text-ink-4">{mode === 'demo' ? 'Featured accounts in this snapshot:' : 'Try:'}</span>
            {featured.data.slice(0, 12).map(id => (
              <button key={id} type="button" onClick={() => { setQuery(id); go(id) }}
                className={`rounded-full px-2 py-0.5 font-mono text-[10.5px] ${id === target ? 'bg-accent/10 text-accent' : 'bg-hover text-ink-2 hover:text-ink'}`}>{shortId(id)}</button>
            ))}
          </div>
        )}

        <div className="relative min-h-0 flex-1 border-t border-line">
          {graph.error instanceof NotInSnapshotError ? <div className="p-8"><DemoNote>{graph.error.message}</DemoNote></div>
            : graph.error ? <div className="p-8"><ErrorNote error={graph.error} onRetry={graph.reload} /></div>
            : graph.loading && target ? <div className="p-8"><Skeleton className="h-full min-h-[300px] w-full" /></div>
            : !target ? <EmptyNote>Search an account id, or pick a featured account, to draw its neighbourhood.</EmptyNote>
            : elements.nodes.length === 0 ? <EmptyNote>No flows found around {shortId(target)}.</EmptyNote>
            : <GraphCanvas elements={elements} lens={lens} selectedId={selected?.id} onSelectNode={setSelected} highlightIds={pathIds} />}
          <div className="glass absolute bottom-4 left-4 w-[300px] rounded-lg p-4">
            <GraphTools source={source} defaultA={target} onPath={onPath} />
          </div>
        </div>
      </div>

      <InspectorSidebar source={source} node={selected} onClose={() => setSelected(null)} onExplore={(id) => { setQuery(id); go(id) }} />
    </div>
  )
}
```

- [ ] **Step 7: Verify and commit**

Run `npm run lint` — **0 errors now** (the five baseline errors were in the rewritten files). `npm run build` ok. Demo: `#/graph` shows featured chips; clicking one draws a light-themed neighbourhood coloured by risk; lens buttons recolour without relayout; tapping a node opens the inspector with the rule-based explanation; the what-if shows the demo note; GraphTools with a featured pair from `paths.json` highlights the path. Live: any account id works; the what-if returns a verdict.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add src/components/GraphCanvas.jsx src/components/InspectorSidebar.jsx src/components/GraphExplorer.jsx src/components/GraphTools.jsx src/lib/graphStyle.js src/lib/graphStyle.test.js
git commit -m "feat(frontend): graph explorer on the light design system with lenses, path/flow tools, real inspector" -m "$TRAILER"
```

---

### Task 7: Pipeline view

**Files:**
- Rewrite: `src/components/PipelineView.jsx` (replace the placeholder)
- Create: `src/components/PrCurve.jsx`

**Interfaces:**
- Consumes: `source.pipeline.*`, `source.stats.overview()` (labelled flag), `GraphCanvas` with lens `'gnn' | 'pagerank' | 'community' | 'marked'`.

- [ ] **Step 1: PR curve (SVG, no chart library)**

`src/components/PrCurve.jsx`:

```jsx
/** Precision & recall vs cutoff — two lines on a 300×120 SVG; a marker at `cutoff`. */
export default function PrCurve({ curve, cutoff }) {
  if (!curve?.loaded) return null
  const W = 300, H = 120, P = 6
  const xs = curve.cutoffs, x0 = xs[0], x1 = xs[xs.length - 1]
  const X = (c) => P + ((c - x0) / (x1 - x0)) * (W - 2 * P)
  const Y = (v) => H - P - v * (H - 2 * P)
  const path = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[120px] w-full" role="img" aria-label="Precision and recall by cutoff">
      <path d={path(curve.precision)} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <path d={path(curve.recall)} fill="none" stroke="var(--high)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <line x1={X(cutoff)} x2={X(cutoff)} y1={P} y2={H - P} stroke="var(--ink-4)" strokeDasharray="3 4" />
    </svg>
  )
}
```

- [ ] **Step 2: The view**

`src/components/PipelineView.jsx`:

```jsx
import { useEffect, useMemo, useState } from 'react'
import { GraphCanvas } from './GraphCanvas'
import PrCurve from './PrCurve'
import { PageHeader, RiskChip, Skeleton, ErrorNote, DemoNote, EmptyNote, useToast } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { fmtIso, shortId } from '../lib/format'

const LENSES = [['gnn', 'GNN heat'], ['pagerank', 'PageRank'], ['community', 'Louvain'], ['marked', 'Marked vs dataset']]
const STAGES = ['pagerank', 'louvain', 'cycle', 'gnn', 'aggregate']

function RunControls({ source, mode, onDone }) {
  const latest = useAsync(() => source.pipeline.latestRun(), [source.mode])
  const [run, setRun] = useState(null)
  const { show, ToastHost } = useToast()

  useEffect(() => {
    if (!run || run.status === 'completed' || run.status === 'failed') return
    const t = window.setInterval(async () => {
      try { const s = await source.pipeline.runStatus(run.id); setRun(s); if (s.status === 'completed' || s.status === 'failed') { window.clearInterval(t); onDone() } }
      catch { /* keep polling */ }
    }, 1500)
    return () => window.clearInterval(t)
  }, [run, source, onDone])

  const start = async () => {
    try { const { run_id } = await source.pipeline.run(); setRun({ id: run_id, status: 'queued', progress: 0 }) }
    catch (e) { show(e?.response?.status === 409 ? 'A run is already active' : e?.message || 'Could not start') }
  }
  const r = run || latest.data
  const pct = Math.round(((r?.progress) || 0) * 100)
  return (
    <div className="flex items-center gap-4">
      {r && r.status !== 'none' && (
        <div className="flex items-center gap-3 text-[12px] text-ink-3">
          <span className="font-mono">{r.status}{r.stage ? ` · ${r.stage}` : ''}</span>
          {r.status === 'running' || r.status === 'queued' ? <span className="h-1 w-32 overflow-hidden rounded bg-hover"><span className="block h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></span>
            : r.finished_at ? <span className="font-mono text-[11px] text-ink-4">{fmtIso(r.finished_at)}</span> : null}
          {r.status === 'failed' && <span className="text-critical">{r.error}</span>}
        </div>
      )}
      {mode === 'live'
        ? <button type="button" onClick={start} disabled={r && (r.status === 'running' || r.status === 'queued')} className="text-[13px] font-medium text-accent hover:opacity-70 disabled:opacity-40">Run pipeline →</button>
        : <span className="text-[11px] text-ink-4">precomputed run (demo)</span>}
      <div className="flex gap-1">{STAGES.map(s => <span key={s} className={`font-mono text-[9px] uppercase ${r?.stage === s ? 'text-accent' : 'text-ink-4'}`}>{s}</span>)}</div>
      <ToastHost />
    </div>
  )
}

export default function PipelineView({ onNav }) {
  const { source, mode } = useDataSource()
  const [lens, setLens] = useState('gnn')
  const [cap, setCap] = useState(600)
  const [cutoff, setCutoff] = useState(null)
  const [selected, setSelected] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  const overview = useAsync(() => source.stats.overview(), [source.mode, reloadKey])
  const threshold = useAsync(() => source.pipeline.threshold(), [source.mode, reloadKey])
  const curve = useAsync(() => source.pipeline.metricsCurve(46), [source.mode, reloadKey])
  const metricLens = lens === 'pagerank' || lens === 'community' ? 'pagerank' : 'gnn'
  const graph = useAsync(() => source.pipeline.overview(metricLens, cap), [source.mode, metricLens, cap, reloadKey])
  const communities = useAsync(() => source.pipeline.communities({ sort: 'risk', limit: 30 }), [source.mode, reloadKey])
  const marked = useAsync(() => source.pipeline.marked({ limit: 50 }), [source.mode, reloadKey])
  const effective = cutoff ?? threshold.data?.default ?? 0.74
  const metrics = useAsync(() => (curve.data?.loaded ? source.pipeline.metrics(effective) : Promise.resolve(null)), [source.mode, effective, curve.data?.loaded])

  const labelled = Boolean(overview.data?.dataset?.labelled)
  const elements = useMemo(() => graph.data || { nodes: [], edges: [] }, [graph.data])
  const total = graph.data?.truncated?.total

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Pipeline" subtitle="PageRank → Louvain → cycle detection → GNN inference → risk aggregation, on the real graph">
        <RunControls source={source} mode={mode} onDone={() => setReloadKey(k => k + 1)} />
      </PageHeader>

      <div className="flex min-h-0 flex-1 gap-0 border-t border-line">
        {/* left: threshold + tables */}
        <div className="flex w-[360px] shrink-0 flex-col gap-6 overflow-y-auto border-r border-line px-6 py-5">
          <section>
            <div className="flex items-baseline justify-between">
              <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">GNN mark cutoff</p>
              <span className="font-mono text-[12px] text-ink tnum">{effective.toFixed(2)}</span>
            </div>
            <input type="range" min={threshold.data?.min ?? 0.5} max={threshold.data?.max ?? 0.95} step="0.01" value={effective}
              onChange={e => setCutoff(Number(e.target.value))} className="mt-2 w-full accent-[var(--accent)]" />
            {threshold.data && <p className="mt-1 text-[10.5px] text-ink-4">model's tuned value {threshold.data.default.toFixed(3)} · <button type="button" onClick={() => setCutoff(null)} className="text-accent">reset</button></p>}
            {!labelled ? <p className="mt-3 text-[11px] text-ink-4">This dataset has no ground-truth labels, so precision/recall are unavailable.</p>
              : metrics.data?.loaded ? (
              <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px]">
                <span className="text-ink-3">precision</span><span className="font-mono text-ink tnum">{(metrics.data.precision * 100).toFixed(0)}%</span>
                <span className="text-ink-3">recall</span><span className="font-mono text-ink tnum">{(metrics.data.recall * 100).toFixed(0)}%</span>
                <span className="text-ink-3">caught</span><span className="font-mono text-ink tnum">{metrics.data.tp.toLocaleString()}</span>
                <span className="text-ink-3">false positives</span><span className="font-mono text-critical tnum">{metrics.data.fp.toLocaleString()}</span>
                <span className="text-ink-3">marked</span><span className="font-mono text-ink tnum">{metrics.data.marked.toLocaleString()}</span>
              </div>
            ) : metrics.loading ? <Skeleton className="mt-3 h-16 w-full" /> : null}
            {labelled && curve.data?.loaded && (
              <div className="mt-3">
                <PrCurve curve={curve.data} cutoff={effective} />
                <div className="flex gap-4 text-[10px] text-ink-4"><span><span className="mr-1 inline-block h-0.5 w-3 bg-accent align-middle" />precision</span><span><span className="mr-1 inline-block h-0.5 w-3 bg-high align-middle" />recall</span></div>
              </div>
            )}
          </section>

          <section>
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Riskiest communities</p>
            {communities.error ? <ErrorNote error={communities.error} /> : communities.loading ? <Skeleton className="mt-2 h-24 w-full" /> : (
              <div className="mt-2 divide-y divide-line/70">
                {communities.data.map(c => (
                  <div key={c.community_id} className="flex items-center gap-3 py-1.5 text-[12px]">
                    <span className="font-mono text-ink-2">{shortId(c.community_id)}</span>
                    <span className="text-ink-4 tnum">{c.size} accts</span>
                    <span className="ml-auto font-mono text-ink tnum">{(c.risk_score * 100).toFixed(0)}%</span>
                    <RiskChip level={c.risk_tier} />
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Marked accounts</p>
            {marked.error ? <ErrorNote error={marked.error} /> : marked.loading ? <Skeleton className="mt-2 h-24 w-full" /> : marked.data.length === 0 ? <EmptyNote>No marks yet — run the pipeline.</EmptyNote> : (
              <div className="mt-2 divide-y divide-line/70">
                {marked.data.map(m => (
                  <button key={m.account_id} type="button" onClick={() => onNav('graph', { account: m.account_id })} className="flex w-full items-center gap-3 py-1.5 text-left text-[12px] hover:bg-hover/60">
                    <span className="font-mono text-ink-2">{shortId(m.account_id)}</span>
                    <span className="font-mono text-[10px] text-ink-4">{Object.entries(m.signals || {}).filter(([, v]) => v).map(([k]) => k).join(' · ')}</span>
                    <span className="ml-auto font-mono text-ink tnum">{((m.combined_score || 0) * 100).toFixed(0)}%</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* right: graph */}
        <div className="relative min-w-0 flex-1">
          <div className="flex items-center gap-4 px-6 py-3">
            {LENSES.filter(([id]) => id !== 'marked' || labelled).map(([id, label]) => (
              <button key={id} type="button" onClick={() => setLens(id)} className={`text-[12px] ${lens === id ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>{label}</button>
            ))}
            <label className="ml-auto flex items-center gap-2 text-[11px] text-ink-4">
              nodes <input type="range" min="100" max="2000" step="100" value={cap} onChange={e => setCap(Number(e.target.value))} disabled={mode === 'demo'} className="w-24 accent-[var(--accent)]" /> <span className="font-mono tnum">{cap}</span>
            </label>
            {graph.data && <span className="font-mono text-[11px] text-ink-4 tnum">{elements.nodes.length.toLocaleString()}{total ? ` of ${total.toLocaleString()}` : ''} accounts</span>}
          </div>
          <div className="absolute inset-x-0 bottom-0 top-12 border-t border-line">
            {graph.error instanceof NotInSnapshotError ? <div className="p-6"><DemoNote>{graph.error.message}</DemoNote></div>
              : graph.error ? <div className="p-6"><ErrorNote error={graph.error} onRetry={graph.reload} /></div>
              : graph.loading ? <div className="p-6"><Skeleton className="h-full min-h-[300px] w-full" /></div>
              : <GraphCanvas elements={elements} lens={lens} cutoff={effective} selectedId={selected?.id} onSelectNode={setSelected} />}
            {selected && (
              <div className="glass absolute right-4 top-4 w-[260px] rounded-lg p-4 text-[12px]">
                <p className="break-all font-mono text-ink">{selected.id}</p>
                <p className="mt-1 text-ink-3">GNN {selected.gnn_risk_score != null ? Number(selected.gnn_risk_score).toFixed(3) : '—'} · {selected.gnn_risk_tier ?? '—'}{selected.in_cycle ? ' · on cycle' : ''}{selected.truth ? ` · dataset: ${selected.truth_typology}` : ''}</p>
                <button type="button" onClick={() => onNav('graph', { account: selected.id })} className="mt-2 font-medium text-accent hover:opacity-70">Open in Graph →</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Verify and commit**

`npm run lint` → 0; `npm run build` ok. Demo: `#/pipeline` shows the precomputed run, a working slider driving the interpolated P/R readout + curve, the GNN-heat overview of 600 hubs, lens switches, communities and marked tables; the node-cap slider is disabled. Live: **Run pipeline** starts a job (409 toast if one is active), the stage strip and bar advance, and tables reload on completion.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add src/components/PipelineView.jsx src/components/PrCurve.jsx
git commit -m "feat(frontend): native pipeline view — run + progress, threshold with P/R curve, lenses, communities, marked" -m "$TRAILER"
```

---

### Task 8: Clean-up, both-mode end-to-end pass, screenshots

**Files:**
- Delete: `src/data/mockData.js`, `src/services/api.js`, `src/types/graph.ts`, `src/components/GraphCanvas.jsx` leftovers (none), `README.md` untouched (Plan D)
- Create: `docs/images/*.png` (repo root `docs/`)

- [ ] **Step 1: Delete superseded files and prove nothing imports them**

Run: `git rm -q src/data/mockData.js src/services/api.js src/types/graph.ts && grep -rn "mockData\|services/api'\|types/graph" src || echo "(no references)"` — Expected `(no references)`.

- [ ] **Step 2: Full gates**

Run: `npm test && npm run lint && npm run build` — all green, 0 lint errors; note the build's chunk list now shows separate chunks for GraphExplorer/PipelineView (Cytoscape lazy-loaded) and per-JSON snapshot chunks.

- [ ] **Step 3: End-to-end pass, both modes, with screenshots**

Demo mode: `npm run dev` (no env). Live mode: backend up, `VITE_API_BASE_URL=http://localhost:8000/api npm run dev -- --port 5174`. For each mode open `#/overview`, `#/graph?account=<a featured id>`, `#/alerts`, `#/transactions`, `#/pipeline`; verify the checks listed in Tasks 3–7 and that the mode pill reads Demo / Live. Capture screenshots of the five views in demo mode to `/Users/kavinnimalarajan/FlowGraph/docs/images/{overview,graph,alerts,transactions,pipeline}.png` (browser tooling or macOS screenshot; 1440×900 window). These are the README images for Plan D.

- [ ] **Step 4: Commit**

```bash
cd /Users/kavinnimalarajan/FlowGraph
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add -A Frontend/src docs/images
git commit -m "chore(frontend): retire mock data and the old api client; add view screenshots" -m "$TRAILER"
```

---

## Self-review against the spec

- §8.1 data-source layer + provider + mode pill + banner → Task 2. §8.2 routing → Task 2. §8.3 Overview/Alerts/Transactions/Graph/Pipeline → Tasks 3–7 (Upload → Plan C). §9 snapshot generator + budget → Task 1 (verdicts deliberately not baked: the aggregator writes to Neo4j; the demo inspector shows the what-if as live-only). §11 frontend testing → vitest units (Tasks 2, 6) + lint/build gates + the both-mode pass (Task 8). Screenshots for §12 → Task 8.
- Names consistent across tasks: `useDataSource`, `useAsync`, `useRoute/navigate(view, params)`, `NotInSnapshotError`, `source.<group>.<fn>` exactly as the interface table; `GraphCanvas` props `{elements, lens, cutoff, selectedId, onSelectNode, highlightIds}`; `GraphTools({source, defaultA, onPath})`; `InspectorSidebar({source, node, onClose, onExplore})`; view props `{onNav, params}`; format helpers as defined in Task 2.
- Route params used: graph `{account, depth}`, alerts `{id}`, transactions `{account}` — matching every `onNav` call in Tasks 3–7.
