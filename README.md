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

## Architecture

```mermaid
%%{init: {'theme':'base','themeVariables':{
  'primaryColor':'#ffffff','primaryTextColor':'#131c2b','primaryBorderColor':'#c7cfdb',
  'lineColor':'#8b98ad','clusterBkg':'#f5f7fa','clusterBorder':'#e2e7ee',
  'edgeLabelBackground':'#f5f7fa'}}}%%
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

**The write path is the part worth reading.** Postgres is written first and the
graph follows through an outbox, so the graph is eventually consistent with
Postgres and never ahead of it — a Neo4j outage delays the graph rather than
corrupting it. Why that shape, and where it is easy to get wrong,
is [§2 below](#2-an-outbox-because-dual-writes-lie).

```mermaid
%%{init: {'theme':'base','themeVariables':{
  'primaryColor':'#ffffff','primaryTextColor':'#131c2b','primaryBorderColor':'#c7cfdb',
  'lineColor':'#8b98ad','signalColor':'#3e4d66','signalTextColor':'#131c2b',
  'actorBkg':'#ffffff','actorBorder':'#c7cfdb','actorTextColor':'#131c2b',
  'labelBoxBkg':'#f5f7fa','labelBoxBorderColor':'#e2e7ee','noteBkgColor':'#f5f7fa',
  'noteBorderColor':'#e2e7ee','noteTextColor':'#3e4d66'}}}%%
sequenceDiagram
    participant C as Consumer
    participant P as Postgres
    participant W as Outbox worker
    participant N as Neo4j
    participant S as Live scorer
    C->>P: INSERT transaction + outbox row (one transaction)
    W->>P: poll pending
    W->>N: MERGE accounts, increment FLOWS_TO aggregates
    W->>P: mark synced (or back off and retry)
    Note over W,S: optional — LIVE_SCORING_ENABLED
    W->>S: accounts touched this cycle
    S->>N: re-score bounded 3-hop neighbourhood
```

---

## How it works

Five decisions carry most of the system. Each one is a place where the obvious
approach breaks on real data.

### 1. Two edge types, because one would explode

Neo4j stores every payment twice, deliberately:

| Edge | Cardinality | Used for |
|---|---|---|
| `TRANSFER` | one per transaction, `MERGE`d on `txn_id` | audit, per-transaction tracing, the ledger view |
| `FLOWS_TO` | one per *directed account pair*, aggregates incremented on match | every graph algorithm, and the GNN's `edge_index` |

Variable-length traversal — `MATCH (a)-[:FLOWS_TO*1..8]->(b)` — branches on
*distinct neighbours*. Run the same query over `TRANSFER` and it branches on
parallel edges too, so a pair with 40 transactions between them multiplies the
search space by 40 at every hop. Collapsing them into one weighted edge keeps
depth-8 cycle detection tractable; `tx_count`, `total_amount`, `min`/`max` and
first/last timestamps ride along as properties, so nothing is lost.

`MERGE` rather than `CREATE` throughout, with aggregates incremented rather than
overwritten — which is only safe because the outbox below guarantees once-delivery.

### 2. An outbox, because dual-writes lie

Writing to Postgres and Neo4j inside one handler is a distributed transaction with
no coordinator: the second write can fail and there is no way to roll the first
back, so the two stores silently diverge. Instead the consumer records the payment
**and** a row in an `outbox` table in a single Postgres transaction, and
`OutboxSyncWorker` drains that table into Neo4j and Redis on a loop — retrying with
exponential backoff, tracking `retry_count` and `last_error`, and marking a row
synced only once the graph write succeeded. The Neo4j write is idempotent (`MERGE`
on `txn_id`), so a retry after a partial failure cannot double-count an aggregate.

The single transaction is the load-bearing part, and it is easy to get subtly
wrong: if the two rows commit separately, a crash in between leaves a payment in
the ledger that the sync worker never sees — not lost, not retried, just silently
absent from the graph forever. `PostgresClient.transaction()` hands one connection
to both writes so they land together or not at all:

```python
async with postgres_client.transaction() as conn:
    await postgres_client.save_transaction(..., conn=conn)
    await postgres_client.insert_outbox(..., conn=conn)
```

`tests/test_outbox_atomicity.py` pins the behaviour: a simulated crash and a
foreign-key violation must each leave *no* payment row behind, and a redelivered
event must be a no-op rather than a duplicate.

The graph is therefore eventually consistent with Postgres and *never ahead of it*.
A Neo4j outage delays the graph; it cannot corrupt it.

### 3. Redis sorted sets for time windows

Each account pair gets a ZSET keyed `edge:{a}:{b}`, scored by unix timestamp.
"How much moved between these two in the last 24h" is then one `ZRANGEBYSCORE` —
microseconds, and no graph traversal at all. These feed the model's twelve
1h/24h/7d volume and count features.

### 4. GraphSAGE, because the model has to score accounts it has never seen

The classifier is a **bidirectional GraphSAGE** (`SAGEConv`) — an *inductive*
architecture. It learns aggregation *functions* over a neighbourhood, so weight
shapes depend on the feature width (47), never the node count. A brand-new account
is scored by existing weights with no retraining, which is the entire reason
per-event live scoring is possible.

Three choices inside it, each forced by the data:

- **`aggr="mean"`, not sum.** The largest hub has ~14,775 neighbours; a sum lets
  degree alone swamp the signal.
- **Bidirectional.** `FLOWS_TO` is directed, so a plain `SAGEConv` only ever sees
  an account's *payers* — and **45% of accounts have no in-neighbour at all**.
  Message passing told those nodes nothing, and FAN-IN looked identical to FAN-OUT.
  A second stream over reversed edges (the account's *payees*) is concatenated per
  layer.
- **Three layers.** Two is the usual ceiling before oversmoothing — but that turned
  out to be a *training* artefact here, not a depth limit. With neighbour sampling
  a third hop trains cleanly and is the champion.

### 5. Training on a 0.6% positive class

At 0.617% prevalence, "everything is low risk" scores **99.4% accuracy** and
catches nothing. So:

- **Headline metric is PR-AUC on the fraud class**, never accuracy.
- **Focal loss** (γ=2) concentrates gradient on the rare, hard examples, with a
  `class_balanced_alpha` vector rather than the paper's scalar (which only rescales
  when there are more than two classes).
- **Mini-batch neighbour sampling** replaced full-batch training, which was doing
  *one optimizer step per epoch* — and let batches be **class-balanced**, since a
  random batch at this prevalence holds about three positives.
- The sampler is **hand-written over CSR adjacency** in vectorised NumPy
  (`ml/sampler.py`), because `pyg-lib`/`torch-sparse` are a notorious install trap
  and a dependency that fails to build is worse than a hundred lines of indexing.
- **SMOTE, when used, interpolates on post-convolution embeddings** — never raw
  features, because a synthetic node has no edges and cannot message-pass. It stays
  in torch so autograd survives (`ml/imbalance.py`).

---

## The stack, and what each piece is actually doing

| Layer | Library | What it does here |
|---|---|---|
| Queue | **Kafka** | durable, replayable event log; partitioned by `sender_id` so one account's events stay ordered |
| Stream | **Faust** | Kafka consumer as async Python; normalises four payment rails into one schema |
| Graph | **Neo4j 5** + async driver | property graph, `MERGE` upserts, APOC subgraph expansion with a non-APOC fallback, range indexes on `Account.id`, `TRANSFER.ts`, `Account.community_id` |
| Relational | **Postgres** + asyncpg | source of truth, the outbox, `risk_flags`, `pipeline_runs`, `app_meta` |
| Cache | **Redis** | edge ZSETs for time-windowed volume |
| Model | **PyTorch** + **PyTorch Geometric** | `SAGEConv` stack split into `encode()`/`classify()` so SMOTE can operate between them |
| Features | **NumPy**, **pandas** | 47-column matrix; k-core, triangles and clustering computed in one pass over the edge list |
| Communities | **networkx** (Louvain), optional **igraph/leidenalg** | community assignment; three of the 47 features |
| Metrics | **scikit-learn**, **imbalanced-learn** | PR-AUC, ROC-AUC, confusion at arbitrary cutoffs; SMOTE |
| API | **FastAPI** + **Pydantic v2** | typed request/response models; Pydantic also validates the LLM's JSON |
| Explanations | **Ollama** / **anthropic** SDK | local or hosted model behind one interface, schema-enforced |
| Frontend | **React 19**, **Vite**, **Tailwind 4**, **Cytoscape.js**, **motion** | `cose-bilkent` force layout, lens-based restyling without relayout |
| Tests | **pytest**, **vitest** | ~560 backend tests, DB-dependent ones self-skipping |

**Things worth knowing that aren't in the list:**

- **Feature column order is a model contract.** New features append at the end;
  reordering silently invalidates every trained checkpoint, so the nine structural
  and motif columns were added *after* the original 38.
- **Scalers are fit on train only** and serialized with the checkpoint. Fitting
  across the whole matrix leaks test statistics — the same defect class as an
  `isFraud` column, just subtler. A checkpoint without its scaler is unusable: it
  would feed raw 1e14-scale amounts into weights fit on rank-transformed inputs.
- **Labels come from the dataset's ground truth, not from `risk_flags`.** Scoring
  the GNN against the detectors' own output only measures how well it imitates the
  cycle detector — and it is blind in exactly the places the detector is.
- **The threshold is chosen on validation**, never on test.

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

## Where things live

```
Backend/
  app/
    api/        FastAPI app + every /api route
    services/   stats (cached whole-graph aggregates), alerts, transactions,
                graph queries, explanations, dataset upload
    viz/        PipelineRunner (pagerank → louvain → cycle → gnn → aggregate),
                whole-graph confusion cache, tuned threshold, ground-truth labels
  ml/
    features.py   FeatureBuilder — live stores → 47-column FeatureSet
    model.py      GraphSAGERiskClassifier (encode/classify, bidirectional)
    sampler.py    mini-batch neighbour sampler over CSR adjacency
    train.py      full-batch + mini-batch training, threshold selection
    losses.py     FocalLoss, class_balanced_alpha
    imbalance.py  differentiable SMOTE on post-convolution embeddings
    split.py      temporal_split, QuantileScaler, FeatureScaler
    evaluate.py   ground truth, fraud_metrics, recall_by_typology
    ensemble.py   score-averaged ensemble      predict.py  inference + explanations
                  → RESULTS.md is the full build record
  fraud/        cycle detector (bounded DFS) + community detector
  db/           Neo4j, Postgres, Redis clients
  consumer/     Faust stream processor    worker/  outbox sync + live scoring
  scripts/      snapshot export, model fetch, result charts
  tests/        ~560 tests (DB-dependent ones self-skip)

Frontend/src/
  services/     dataSource.js — one interface, two implementations (live | snapshot)
  components/   five views + the shared design-system primitives
  lib/          formatters, Cytoscape style/lens rules
  data/snapshot/  the committed demo dataset

docs/           design specs, implementation plans, images, runbooks
```

Engineering notes, conventions and the full reasoning log:
[`Backend/CLAUDE.md`](Backend/CLAUDE.md).
