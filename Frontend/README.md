# FlowGraph — frontend

React 19 + Vite + Tailwind 4 + Cytoscape. Five views over the detection engine:
Overview, Graph, Alerts, Transactions, Pipeline (plus Upload in live mode).

See the [root README](../README.md) for what the system does and the results.

## Two modes, one build

The app runs against either a live backend or a committed snapshot of real
pipeline output. `DataSourceProvider` picks:

| `VITE_API_BASE_URL` | Mode | Source |
|---|---|---|
| set and `/health` answers | **live** | the FastAPI backend |
| set but unreachable | **demo** | snapshot, with a banner and a retry |
| unset | **demo** | snapshot |

```bash
npm install
npm run dev                                                # demo — no backend needed
VITE_API_BASE_URL=http://localhost:8000/api npm run dev    # live
```

Both implementations satisfy one interface (`src/services/dataSource.js`), so
views never branch on the mode. What the snapshot cannot serve — running the
pipeline, arbitrary accounts, uploads — rejects with `NotInSnapshotError`, which
views render as an inline "needs a live backend" note rather than an error.

## Scripts

| | |
|---|---|
| `npm run dev` | dev server with HMR |
| `npm run build` | production build into `dist/` |
| `npm test` | vitest unit tests (router, formatters, data source, graph style) |
| `npm run lint` | eslint — must be 0 errors |

## The snapshot

`src/data/snapshot/` is generated from a live backend and **committed** — it is
what the public demo serves. Regenerate after a pipeline run:

```bash
cd ../Backend
POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
  python3 scripts/export_snapshot.py --featured 30
```

It holds whole-graph stats, per-currency series, 200 alerts, 200 transfers, 30
featured accounts with their neighbourhoods and explanations, and the pipeline's
overview/communities/marked/threshold/precision-recall curve. The exporter fails
if the bundle exceeds 4 MB, so the demo stays fast; Vite splits each JSON into its
own lazy chunk, and Cytoscape only loads when a graph view is opened.

## Design system

"Liquid Glass Ledger", defined in `src/index.css` as CSS custom properties and
mapped into Tailwind's namespace. Use the tokens, never raw palette classes:

- ink: `text-ink`, `text-ink-2/3/4` · surfaces: `bg-base`, `bg-hover`, `glass`
- lines: `border-line`, `border-line-2` · brand: `text-accent`
- risk ladder: `RISK_VAR` / `RiskChip` from `src/components/ui.jsx`
- type: `font-display` (Fraunces), `font-sans`, `font-mono`; `tnum` for figures

Cytoscape parses colours itself and cannot resolve CSS variables, so the graph
palette mirrors these tokens as literals in `src/lib/graphStyle.js` — keep the two
in sync.

## Deploying

Vercel: root directory `Frontend`, framework Vite (`vercel.json` pins it). Leave
`VITE_API_BASE_URL` unset for the snapshot-backed demo. Routing is hash-based, so
deep links work on any static host with no rewrite rules.
