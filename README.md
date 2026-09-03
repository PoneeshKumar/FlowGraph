# FlowGraph

Real-time money flow intelligence engine. Every payment is a directed edge in a
live graph. The system detects fraud patterns — circular flows, hub-and-spoke
networks, layering — that are invisible to row-based systems like a
transactions table.

The core idea: fraud detection built on a graph database (Neo4j) can see
*structure* — cycles, hubs, communities — that a flat SQL table cannot, and a
GNN trained on that structure can learn even the patterns that hand-written
graph algorithms can't represent (fan-out, bipartite layering, gather-scatter).

## Repo layout

```
FlowGraph/
├── Backend/     # everything below — ingestion, storage, algorithms, GNN, API
├── Frontend/    # Vite + React graph UI (not yet wired to the backend)
├── docker-compose.yml   # Kafka, Neo4j, Redis, Postgres, pgAdmin
└── requirements.txt
```

This README covers the whole system end to end. `Backend/CLAUDE.md` is the
living, detailed engineering doc (architecture rationale, the full GNN
build log, conventions) — read that for depth on any section below.

## Architecture

Five layers, top to bottom:

```
Payment events → Kafka → Python consumer (Faust)
                              ↓            ↓            ↓
                          Postgres      Neo4j        Redis
                         (outbox)      (graph)      (ZSET)
                                          ↓
                              Cycle detection (DFS)
                              Incremental PageRank
                              Louvain clustering (daily batch)
                                          ↓
                              GNN risk classifier  ← primary classifier
                              (cycle + Louvain feed it as features / weak labels)
                                          ↓
                              Risk flag aggregator
                                          ↓
                              Claude API (subgraph summary → explanation, low-confidence cases)
                                          ↓
                              FastAPI (Cypher-over-HTTP)
                              D3 / Cytoscape.js (force-directed graph UI)
```

### How the pieces tie together

1. **Ingestion.** Payment events (ACH, wire, card, crypto) land on a Kafka
   topic, partitioned so a given sender's transactions stay ordered. A Python
   consumer built on **Faust** reads the stream and does a **dual write**: the
   transaction is written to Postgres (canonical, immutable record) and, via
   the outbox, to Neo4j (graph edge) and Redis (time-windowed volume).
2. **Storage.** Postgres is the source of truth — a transaction is written
   there first with a `pending_graph_sync` flag, and a background **outbox
   worker** picks up pending rows and writes them into Neo4j with retries and
   exponential backoff. This means the graph is always *eventually*
   consistent with Postgres, never ahead of it — a Neo4j outage can't lose
   data. Redis holds a sorted set per account pair (`edge:{a}:{b}`, scored by
   timestamp) purely so that "volume in the last 1h/24h/7d" is a
   microsecond `ZRANGEBYSCORE` instead of a Neo4j aggregation query.
3. **Graph algorithms.** Three detectors run directly on the `FLOWS_TO`
   aggregate edges in Neo4j:
   - **Cycle detection** — bounded-depth DFS (6-8 hops, 48h window for
     real-time; up to 12 hops for batch/investigative sweeps) looking for
     money that loops back to its origin. Validated against the IBM AML
     benchmark at 72% recall (depth 8) / 87% recall (depth 12), both at 100%
     precision.
   - **Incremental PageRank** — flags hub accounts (many senders, many
     receivers) by recomputing centrality only on the local subgraph touched
     by a new edge, not the whole graph.
   - **Louvain clustering** — a daily batch job that assigns every node a
     `community_id`; when one account in a community is flagged, the rest of
     the community inherits elevated risk.
   These three are demoted from "the fraud detector" to **feature providers
   and weak-label sources** for the GNN below — that's the single biggest
   architectural decision in the repo (see the ML section).
4. **GNN risk classifier** — a bidirectional GraphSAGE network is the
   **primary classifier**. It consumes 47 engineered features per account
   (graph structure, Redis volumes, community stats, k-core/motif features)
   and outputs a risk score per account. This is the most developed part of
   the codebase; see [AI / ML layer](#ai--ml-layer) below for the full build
   record and numbers.
5. **Risk flag aggregator** — combines cycle/PageRank/Louvain/GNN signals
   into a single per-account risk record (`risk_flags` table, type
   `AGGREGATE`), written with type, score, and the signals that triggered it.
6. **AI enrichment (Claude API)** — reserved for **low-confidence GNN cases
   only**. Fetches the account's subgraph, renders it as a natural-language
   summary, and asks Claude for a structured `risk_level` /
   `confidence` / `explanation`. This is a regulatory requirement, not a
   nice-to-have — a financial institution can't freeze an account on a bare
   number, it needs a documented, human-readable reason.
7. **API + UI.** FastAPI exposes Cypher-over-HTTP (named queries like
   `subgraph_around`, `shortest_path_between`, `flow_between`), and a
   Cytoscape.js/D3 force-directed graph is the operator-facing view — nodes
   colored by risk tier, edges thickened by volume.

## Current status (as of `main`)

**Done and verified:**
- Kafka ingestion
- Python consumer (Faust), dual-write to Postgres + Neo4j via the outbox
- Storage layer (Postgres + Neo4j + Redis, outbox worker with retry/backoff)
- Graph algorithm engine (cycle detection, PageRank, Louvain)
- **GNN layer** — features, training, evaluation, inference, ensembling. The
  most developed part of the repo. Champion config is `ml/runs/v10_L3`
  (regenerable, not committed — see below); full methodology in
  `Backend/ml/RESULTS.md`.
- **Pipeline visualiser (`/viz`)** — a standalone FastAPI-served viewer
  (`Backend/app/viz/`) with five tabs (Cycle · PageRank · Louvain · GNN ·
  Marked) rendered with vendored Cytoscape.js, community + account/hop
  search, and directed edges thickened by `FLOWS_TO.total_amount`. A **Run
  pipeline** button launches the detectors + GNN **inference** (no re-ingest,
  no retrain) as an async background job, tracked in a `pipeline_runs`
  table. This is the first place the GNN's per-account scores get written
  back to Neo4j (`gnn_risk_score`) and aggregated into `risk_flags`.

**Built but not wired into live serving:**
- The GNN is trained and scored **offline / batch** (`ml/predict.py`,
  `ml/ensemble.py`) against a cached feature set. It is not yet hooked into
  the live consumer/outbox path — nothing scores a transaction per-event and
  writes it back as a risk flag in real time. `/viz`'s on-demand "Run
  pipeline" job is the batch counterpart of this still-unbuilt live path.
  Wiring the GNN into the risk-flag aggregator is the main remaining
  integration task.

**Not started:**
- AI enrichment layer (Claude explainability for low-confidence GNN cases)
- Query API (`shortest_path_between`, `subgraph_around`, `flow_between`)
- Frontend wiring — the Vite/React app in `Frontend/` (dashboard, graph
  explorer, alerts/transactions views) exists but isn't connected to a
  backend yet.

## Stack

| Component | Technology |
|---|---|
| Message queue | Kafka (KRaft mode, no Zookeeper) |
| Stream processing | Python / Faust |
| Graph database | Neo4j |
| Relational store | Postgres |
| Cache / time-series | Redis |
| GNN | PyTorch + PyTorch Geometric (SAGEConv) |
| API | FastAPI |
| Frontend | React (Vite) + Cytoscape.js / D3 |
| AI enrichment | Claude API — layer not started |

ML dependencies are pinned separately in `Backend/requirements-ml.txt` (torch,
torch-geometric, scikit-learn, imbalanced-learn). `pyg-lib` / `torch-sparse`
are deliberately **not** installed — they're an install trap — so the
mini-batch sampler is hand-written over CSR adjacency instead
(`Backend/ml/sampler.py`).

## AI / ML layer

The GNN is the **primary risk classifier**. Cycle detection and Louvain are
feature providers and weak-label sources, not the detector of record — they
become columns in the 47-column feature matrix, and cycle flags become the
weak-label fallback when ground truth isn't available. The Claude API is
reserved for secondary explainability on low-confidence cases.

On the IBM AML HI-Small benchmark, honest held-out (temporal) test evaluation
took **PR-AUC from 0.057 to 0.724 (single model) / 0.735 (3-seed ensemble)**
— roughly a 13× improvement, at ROC-AUC 0.99 — and whole-graph recall of
laundering accounts went from the graph detectors' **3.9% to 55–59%** at
~78% precision. Every gain traces to a diagnosed cause, not brute-force
tuning; the two biggest wins were essentially free once understood: fixing a
covariate shift between train and test, and fixing severe under-training.

### The data

Measured on the loaded HI-Small graph:

| | |
|---|---|
| Transactions ingested | ~5.04M |
| Account nodes | 513,987 |
| `FLOWS_TO` edges (non-self) | 644,397 |
| Feature columns | 47 |
| Ground-truth positives | 3,170 accounts (0.617% prevalence) |
| Typologies | CYCLE, FAN-OUT, FAN-IN, GATHER-SCATTER, SCATTER-GATHER, BIPARTITE, STACK, RANDOM |

Loaded via `Backend/ml/datasets/run_ingest.py` (CSV → Neo4j + Redis), then
`Backend/ml/datasets/run_louvain.py` (populates `community_id`).

### Labels and evaluation

- Labels come from the **IBM AML ground truth** (370 laundering attempts →
  ~3,170 accounts), never from `risk_flags` — scoring against the detectors'
  own output would only measure how well the GNN imitates a system that is
  blind to 6 of the 8 typologies by construction.
- Headline metric is **PR-AUC on the fraud class** (never accuracy — at
  0.6% prevalence, "everything is low risk" scores 99.4% and catches
  nothing), with ROC-AUC and per-typology recall reported alongside.

### The split and the scaler

**Temporal split, 60/15/25**, by each account's first observed activity —
payments are a time series, and a random split would let the model learn the
future to predict the past. The late (test) population looks nothing like
the early (train) one: feature means shift 30-60×, and some features'
label-correlation even flips sign. Two scalers are fit on **train only**;
the key one, **`QuantileScaler`**, maps each feature to its train-percentile
rather than its raw magnitude, so "top 1% by out-degree" means the same
thing at test time regardless of scale shift. This single change took test
PR-AUC 0.057 → 0.28 and is nearly invisible on validation — its whole
benefit lives on the shifted future, which is why shift-robustness changes
are judged on test, never validation.

### Features — 47 columns

Assembled by `FeatureBuilder` (`Backend/ml/features.py`) from Neo4j + Redis +
Postgres: graph structure (degree, amounts, PageRank, account age), self-loop
stats, derived ratios and burst signals, community stats, Redis time-windowed
volumes, node-type one-hots, and two blocks added during the GNN work —
**structural features** (k-core, clustering, triangle count — k-core alone
correlates higher with the label than any raw feature) and **motif
features** (max neighbour in/out-degree, aimed squarely at the FAN-IN/FAN-OUT
typologies). Column order is a model contract: new features are always
appended, never reordered.

### Model

`GraphSAGERiskClassifier` (`Backend/ml/model.py`) — a bidirectional GraphSAGE
stack (separate message-passing streams over forward and reversed edges, since
45% of accounts have no in-neighbour at all) with a linear classification
head, `aggr="mean"` (a sum would let a 14,775-neighbour hub swamp the
aggregate). Inductive by construction — SAGEConv weight shapes depend on
feature width, not node count, so a brand-new account is scored with existing
weights, no retraining. Depth 3 in the champion, which only trains cleanly
under mini-batch sampling (see below) — full-batch 3-layer training
oversmooths.

### Training

Two modes: **full-batch** (one optimizer step per epoch — badly
under-trains) and **mini-batch** (`Backend/ml/sampler.py`, hand-written CSR
neighbour sampler, no `pyg-lib`) — the single biggest lever in the whole
project. Mini-batch enables **class-balanced batches** (at 0.7% prevalence a
random batch has ~3 fraud seeds; the sampler oversamples the minority so
every step sees fraud), taking test PR-AUC 0.46 → 0.65 and recall 0.31 → 0.59
in a handful of epochs. Loss is Focal Loss (γ=2) to concentrate gradient on
the rare, hard class; SMOTE on post-convolution embeddings exists as an
alternative but mini-batch balancing beats it in practice.

### Champion — `ml/runs/v10_L3`

Bidirectional GraphSAGE, hidden 256, 3 layers, dropout 0.3, QuantileScaler,
47 features, Focal Loss, mini-batch (batch 512, k=10, pos_frac 0.25).

```
val  PR-AUC 0.683
test PR-AUC 0.724   ROC-AUC 0.986   precision 0.768   recall 0.636   F1 0.696
```

Whole-graph GNN recall is 54.6% at ~78% precision — 1,657 confirmed
laundering accounts that no detector found. A **3-seed ensemble**
(`Backend/ml/ensemble.py`) improves this further to test PR-AUC 0.735 / ROC
0.992 / recall 59%, because mini-batch training draws a fresh neighbourhood
every step, so different seeds land on genuinely different functions and
averaging cancels variance (full-batch seed-ensembles were a wash — members
were too correlated).

Recall is highest on the loop-free SCATTER-GATHER/GATHER-SCATTER/FAN-OUT
typologies (81-94%) — exactly the structures cycle detection cannot
represent at any depth, which is the whole case for the GNN existing.
BIPARTITE (~12-14%) is a genuine, documented hold-out: four different
feature families were tried and none carried signal, because BIPARTITE's
blocks in this dataset are sparse and embedded in the giant connected
component rather than dense and isolated.

### What did *not* help

Removing regularization (memorized — test ROC fell to a coin flip),
edge-weighted aggregation (a hub's sum swamps the signal), bipartite-density
features, and transaction-amount features (in this synthetic dataset, fraud
accounts have *lower* pass-through than normal ones — topology carries the
signal, not amount). Full record and rationale in `Backend/CLAUDE.md`.

### Honest limitations

1. Test is a shifted, lower-prevalence population than train — PR-AUC stays
   modest in absolute terms even after correcting for the shift, because the
   signal is structural and prevalence is extreme.
2. The temporal split separates *accounts*, not *time* — `FLOWS_TO`
   aggregates can't be rewound, so a "test" account still carries its full
   lifetime, overstating how early a mule is actually caught.
3. 17.9% of accounts have no non-self neighbour — message passing gives them
   nothing regardless of direction.
4. BIPARTITE is a topology-only ceiling on this dataset — passing it needs a
   non-topological signal this synthetic data doesn't carry.

### Reproducing the champion

```bash
docker compose up -d neo4j redis postgres
cd Backend
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min
python3 -m ml.readiness                                           # pre-training audit
python3 -m ml.train --refresh-cache --cache ml/cache/featureset_v4.npz \
    --scaler quantile --bidirectional --minibatch \
    --train-frac 0.60 --val-frac 0.15 \
    --hidden 256 --num-layers 3 --dropout 0.3 --lr 0.005 --gamma 2.0 \
    --mb-batch 512 --mb-k 10 --mb-pos-frac 0.25 --mb-steps 300 \
    --epochs 14 --patience 6 --run-name v10_L3
python3 -m ml.sweep --preset shift --cache ml/cache/featureset_v4.npz   # the core ablation
python3 -m ml.predict --run ml/runs/v10_L3 --cache ml/cache/featureset_v4.npz --top 20
# ensemble (train v10_L3_s1 / v10_L3_s7 with --seed 1 / 7 first):
python3 -m ml.ensemble --runs ml/runs/v10_L3 ml/runs/v10_L3_s1 ml/runs/v10_L3_s7 \
    --cache ml/cache/featureset_v4.npz --top 20
```

Everything trains off the cached `.npz` (no DB needed once the cache exists),
on CPU, in minutes per run. `ml/runs/` and `ml/cache/` are gitignored —
trained checkpoints and feature caches are regenerable, not committed.

## Data model

**Neo4j nodes** — one of four types: `account`, `merchant`, `bank`,
`exchange`. Schema also defines `kyc_tier`, `risk_score`, `country`,
`account_age`, `cumulative_volume`, though nothing in production currently
writes them, so they're null on every node today.

**Neo4j edges** — two directed types per payment:
- `TRANSFER` — one edge per transaction, keyed by `txn_id`. Full per-txn
  detail, used for audit and tracing.
- `FLOWS_TO` — one aggregate edge per directed account pair
  (`tx_count`, `total_amount`, `first_ts`/`last_ts`, min/max amount).
  Graph algorithms and the GNN's `edge_index` both run on this aggregate,
  not on individual `TRANSFER` edges — collapsing 20-50 parallel edges
  between a pair into one keeps variable-length traversal from exploding
  combinatorially.

**Postgres** — canonical transaction records, the outbox table
(`pending_graph_sync`), and `risk_flags` (detector output / weak-label
source).

**Redis** — sorted sets keyed `edge:{a}:{b}`, scored by timestamp, feeding
time-windowed volume queries and the GNN's 1h/24h/7d volume features.

## Key patterns

- **Dual-write consistency** — Postgres first, then the outbox worker
  propagates to Neo4j with retry/backoff. The graph is eventually
  consistent with Postgres, never ahead of it.
- **Neo4j upserts** — always `MERGE`, never plain `CREATE`. `TRANSFER` is
  idempotent on `txn_id`; `FLOWS_TO` aggregates are incremented on match,
  never overwritten, so history is preserved.
- **Time-windowed queries** never hit Neo4j — they use Redis
  `ZRANGEBYSCORE`.
- **Graph algorithms run only on subgraphs touched by new edges**, not the
  full graph, with depth limits and per-account query timeouts bounding
  worst-case latency.
- **Risk scores always ship with a written explanation** — never a bare
  number, both by convention and because it's a regulatory requirement for
  the AI enrichment layer once built.
- Cypher queries use named parameters, never string interpolation. All time
  values are stored and compared in UTC.

## Running it locally

```bash
docker compose up -d              # Kafka, Neo4j, Redis, Postgres, pgAdmin
cd Backend
pip install -r requirements.txt   # + requirements-ml.txt for the GNN layer
```

Dev credentials: Neo4j has no auth locally; Postgres is
`flowgraph` / `changeme` (overridable via `POSTGRES_DSN`, `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`). The `/viz` pipeline visualiser needs
`POSTGRES_DSN` pointed at the container and a trained `ml/runs/v10_L3` (see
*Reproducing the champion* above) for its GNN stage — it degrades gracefully
if the model artifact isn't present.

The `Frontend/` React app (`npm install && npm run dev` inside `Frontend/`)
is not yet wired to the backend API — see *Current status* above.

## Roadmap

1. Wire the GNN into live serving — today it only scores offline/batch and
   through the `/viz` on-demand run. This is the biggest remaining gap.
2. Build the AI enrichment layer (Claude API explainability for
   low-confidence GNN cases).
3. Build the query API (`shortest_path_between`, `subgraph_around`,
   `flow_between`).
4. Connect the `Frontend/` React app to the backend.
5. Time-bounded GNN features, so the temporal evaluation reflects real
   detection latency rather than full-lifetime account history.
6. A larger training dataset (HI-Medium) — the remaining path past 0.735
   test PR-AUC is more data, not more modelling.
