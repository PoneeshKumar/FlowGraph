# FlowGraph

**Real-time money-flow intelligence.** Every payment is a directed edge in a live
graph, and a graph neural network scores every account on the shape of the money
around it — catching laundering structures that row-based rules and cycle
detection cannot represent at any depth.

On the IBM AML benchmark (513,987 accounts, 5.0M transactions) it finds **55–59%
of laundering accounts at ~78% precision**, against **3.9%** for the cycle and
community detectors it replaces — including **1,657 confirmed laundering accounts
that no detector found**.

![Overview](docs/images/overview.png)

---

## Why a graph, and why a GNN

Laundering is a *shape*, not a transaction. A $9,800 transfer is unremarkable on
its own; a hundred of them fanning out from one account and re-converging two hops
later is the entire crime. Row-based systems never see the shape, and cycle
detection only sees one shape — a loop.

Of the eight laundering typologies in the benchmark, **seven are loop-free**. No
depth of cycle search can represent a FAN-OUT or a SCATTER-GATHER. That is the
case for the GNN, and it is where the numbers come from:

![Recall by typology](docs/images/recall-by-typology.png)

The GNN scores highest on exactly the structures cycle detection cannot see.

---

## What it does

| | |
|---|---|
| **Ingests** | payment events through Kafka into Postgres (source of truth), then a transactional outbox syncs them to Neo4j (graph) and Redis (time-windowed volume) |
| **Detects** | bounded-depth cycle DFS, incremental PageRank, Louvain communities — now demoted to *feature providers* for the GNN |
| **Scores** | a 3-layer bidirectional GraphSAGE over 47 node features, inductively — new accounts are scored with existing weights, no retraining |
| **Explains** | every flag carries a written reason (a regulatory requirement, not a nicety); a pluggable LLM turns the subgraph evidence into plain English |
| **Serves** | a FastAPI query API and a React app — graph explorer, alerts, transactions, and a live pipeline view |

---

## Architecture

```mermaid
flowchart TB
    subgraph ingest[Ingestion]
        E[Payment events<br/>ACH · wire · card · crypto] --> K[Kafka]
        K --> C[Faust consumer]
    end

    subgraph store[Storage]
        C -->|1· write first| P[(Postgres<br/>canonical + outbox)]
        P -->|2· outbox worker| N[(Neo4j<br/>accounts + FLOWS_TO)]
        P -->|2· outbox worker| R[(Redis<br/>edge time-series)]
    end

    subgraph detect[Detection]
        N --> CY[Cycle DFS]
        N --> PR[PageRank]
        N --> LV[Louvain]
        CY & PR & LV --> F[47-column feature matrix]
        F --> G[GraphSAGE risk classifier]
        G --> A[Risk aggregator]
    end

    subgraph serve[Serving]
        A --> API[FastAPI /api]
        A --> X[Explanation service<br/>Ollama · Claude · rule-based]
        X --> API
        API --> UI[React app]
    end

    style G fill:#0d9d72,color:#fff
    style P fill:#3b4bc0,color:#fff
```

**The write path is the important part.** Postgres is written first and the graph
follows through an outbox, so the graph is eventually consistent with Postgres and
never ahead of it. A Neo4j outage delays the graph; it never loses a payment.

```mermaid
sequenceDiagram
    participant C as Consumer
    participant P as Postgres
    participant W as Outbox worker
    participant N as Neo4j
    participant S as Live scorer
    C->>P: INSERT transaction + outbox row (one commit)
    W->>P: poll pending
    W->>N: MERGE accounts, increment FLOWS_TO aggregates
    W->>P: mark synced
    Note over W,S: optional — LIVE_SCORING_ENABLED
    W->>S: accounts touched this cycle
    S->>N: re-score bounded 3-hop neighbourhood
```

---

## Results

Everything below was measured on the loaded graph, not estimated. Full
methodology, ablations and dead ends: **[`Backend/ml/RESULTS.md`](Backend/ml/RESULTS.md)**.

![PR-AUC progression](docs/images/pr-auc-progression.png)

| | champion (`v10_L3`) | 3-seed ensemble |
|---|---|---|
| Test PR-AUC | **0.724** | **0.735** |
| Test ROC-AUC | 0.986 | 0.992 |
| Whole-graph recall | 54.6% | 59% |
| Precision at the tuned cutoff | ~78% | ~78% |

At 0.32% test prevalence, PR-AUC 0.72 is roughly **225× a random ranker**.

**The two biggest gains were free once diagnosed**, and neither was a bigger model:

- **Quantile normalization** (0.082 → 0.282). Accounts appearing late in the window
  look nothing like early ones — column means shift 30–60×, and some features'
  label correlation flips sign. Rank-transforming each feature against the train
  distribution makes "top 1% by out-degree" mean the same thing in both
  populations. Its entire benefit lives on the shifted future, so it is nearly
  **invisible to validation** — which is why shift-robustness changes here are
  judged on test, never val.
- **Mini-batch training** (0.460 → 0.650). Full-batch training does *one optimizer
  step per epoch*, which badly under-trains the model — and at 0.7% prevalence a
  random batch holds ~3 fraud seeds. Sampling bounded k-hop subgraphs gives
  hundreds of updates per epoch *and* class-balanced batches.

### Honest limitations

1. The temporal split separates **accounts, not time** — `FLOWS_TO` aggregates
   can't be rewound, so a "test" account arrives carrying its full lifetime. Not
   label leakage, but it overstates how *early* a mule is caught.
2. Test is a shifted, lower-prevalence population (0.32% vs 0.70% train).
3. **BIPARTITE (~12%) is a topology-only ceiling** on this dataset — four distinct
   feature families showed no signal. Passing it needs non-topological data this
   synthetic set doesn't carry.
4. 412 test positives, so single-run PR-AUC carries noise; ROC-AUC is steadier.

---

## The app

Five views, all reading the real graph. The public demo runs entirely from a
bundled snapshot — no backend, nothing to keep running.

| | |
|---|---|
| **Overview** | cumulative volume per currency against a uniform-pace baseline, graph-wide counts, newest flags and transfers |
| **Graph** | neighbourhood explorer with risk / GNN-heat / community / PageRank lenses, shortest-path and flow tools, and a per-account inspector carrying the written explanation |
| **Alerts** | every open flag with its reason, filterable by detector and severity, with review actions |
| **Transactions** | the settled-transfer ledger, per currency or per account |
| **Pipeline** | run the whole detection chain and watch it, with a threshold slider showing live whole-graph precision/recall |

![Pipeline](docs/images/pipeline.png)

The threshold slider is the honest part: drag it and watch precision trade against
recall across all 513,987 accounts. At the model's tuned cutoff (0.738) it marks
1,859 accounts — 1,577 of them real laundering accounts, 282 false positives.

![Graph explorer](docs/images/graph.png)

---

## Run it on your own machine

**Prerequisites:** Docker, Python 3.9, Node 20+.

```bash
git clone https://github.com/PoneeshKumar/FlowGraph.git
cd FlowGraph
docker compose up -d neo4j postgres redis      # Kafka only needed for live ingestion
```

**Backend:**

```bash
cd Backend
pip install -r requirements.txt                 # + requirements-ml.txt for the GNN
python3 scripts/fetch_models.py                 # trained checkpoints (~52 MB)

# The IBM AML graph — download the CSV from Kaggle into benchmarks/data/ first
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min

POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
  python3 -m uvicorn app.api.main:app --port 8000
```

> The container's Postgres password is `changeme`, which differs from the config
> default — hence the explicit `POSTGRES_DSN`. On startup the API warms two
> whole-graph caches (~30 s); until they are ready, stats endpoints answer 503 and
> the UI shows a skeleton.

**Frontend:**

```bash
cd Frontend
npm install
VITE_API_BASE_URL=http://localhost:8000/api npm run dev    # live mode
npm run dev                                                # demo mode (bundled snapshot)
```

Without the dataset you can still run the whole app in **demo mode** — it serves a
committed snapshot of real pipeline output and needs no backend at all.

### Run it on your own data

The Upload view (live mode only) takes a transaction CSV, replaces the loaded
graph with it, and runs the full pipeline — PageRank → Louvain → cycles → GNN →
aggregation — with staged progress. Download the template from the view for the
exact column order. A patterns file is optional: supply one and the Pipeline view
can show precision/recall; without it every account still gets scored.

This works because the model is **inductive** — a graph it has never seen is scored
with the existing weights, no retraining.

### Reproducing the champion

```bash
python3 -m ml.train --refresh-cache --cache ml/cache/featureset_v4.npz \
    --scaler quantile --bidirectional --minibatch \
    --hidden 256 --num-layers 3 --dropout 0.3 --lr 0.005 --gamma 2.0 \
    --mb-batch 512 --mb-k 10 --mb-pos-frac 0.25 --mb-steps 300 \
    --epochs 14 --patience 6 --run-name v10_L3
python3 -m ml.sweep --preset shift --cache ml/cache/featureset_v4.npz   # the core ablation
```

Everything trains off the cached `.npz` (no DB needed once it exists), on CPU, in
minutes per run.

---

## Explanations

Every risk flag carries a written reason. Who writes it is configurable:

| `LLM_PROVIDER` | Runs | Cost |
|---|---|---|
| `ollama` | a local model (default when Ollama is reachable) | free |
| `anthropic` | the Claude API — set `ANTHROPIC_API_KEY` | ~$0.02 per explanation |
| `none` | deterministic text from the signals | free |

Selection is automatic: an explicit `LLM_PROVIDER` wins, else an API key, else a
reachable Ollama, else rule-based. A provider that errors or returns off-schema
JSON falls back to rule-based — the endpoint never fails for lack of a model.

---

## Deploying the demo

The frontend is a static site; the backend is not serverless (PyTorch alone
exceeds the function size limit, and a pipeline run takes minutes), so:

- **Frontend → Vercel.** Root directory `Frontend`, framework Vite. Leave
  `VITE_API_BASE_URL` unset and it serves the bundled snapshot.
- **Backend → any container host** (Railway, Render, Fly, a VM) alongside Neo4j,
  Postgres and Redis. Point `VITE_API_BASE_URL` at it for live mode.

Regenerate the snapshot after a pipeline run:

```bash
POSTGRES_DSN='…' python3 scripts/export_snapshot.py --featured 30
```

---

## Repo layout

```
Backend/
  app/          FastAPI: api/ endpoints, services/ (stats, alerts, transactions,
                graph, explanations, dataset upload), viz/ (pipeline runner + JSON)
  ml/           GNN: features, model, training, evaluation, inference, ensembling
                → ml/RESULTS.md is the full build record
  fraud/        cycle + community detectors
  db/           Neo4j, Postgres, Redis clients
  consumer/     Faust stream processor      worker/  outbox sync + live scoring
  scripts/      snapshot export, model fetch, result charts
  tests/        ~560 tests (DB-dependent ones self-skip)
Frontend/       React 19 + Vite + Tailwind 4 + Cytoscape
  src/services/ the data-source layer: live API or bundled snapshot
  src/data/snapshot/   the committed demo dataset
docs/           design specs, implementation plans, images
```

## Stack

Kafka · Faust · Neo4j · Postgres · Redis · PyTorch + PyTorch Geometric (SAGEConv)
· FastAPI · React + Cytoscape.js · Ollama / Claude

Engineering notes and conventions: [`Backend/CLAUDE.md`](Backend/CLAUDE.md).
