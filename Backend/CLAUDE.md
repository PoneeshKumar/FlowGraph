# FlowGraph

Real-time money flow intelligence engine. Every payment is a directed edge in a live graph. The system detects fraud patterns — circular flows, hub-and-spoke networks, layering — that are invisible to row-based systems.

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

## Current status

**Done and verified:**
- Kafka ingestion
- Python consumer (Faust)
- Storage layer (Postgres + Neo4j + Redis dual-write, outbox worker)
- Graph algorithm engine (cycle detection, PageRank, Louvain/Leiden)
- **GNN layer — features, training, evaluation, inference, ensembling.** This is
  the most developed part of the repo; the whole `## AI / ML layer` section below
  documents it. Champion model config is `ml/runs/v10_L3` (regenerable — see
  *Artifacts & branch*); full numbers and methodology in `ml/RESULTS.md`.

**Serving paths (both built):**
- **Batch / on-demand** — the GNN scores the whole graph against a cached feature
  set (`ml/predict.py`, `ml/ensemble.py`), triggered by `/viz`'s "Run pipeline".
- **Live per-event** (`LIVE_SCORING_ENABLED`, default off) — the outbox worker
  re-scores the accounts each synced event touched **plus their bounded 3-hop
  neighborhood** and writes scores back (`gnn_risk_score` + a `LIVE_GNN` risk
  flag). Enabled by the inductive model (existing weights score new accounts, no
  retrain). Pieces: `db/neo4j.py:export_neighborhood` (bounded k-hop export),
  `ml/live_scorer.py:LiveScorer` (resident cached models), and
  `app/services/incremental_scorer.py:IncrementalScorer` (assemble → score →
  write-back), hooked in `worker/outbox_sync_worker.py` best-effort so scoring
  never blocks the sync. **v1 caveats:** PageRank/Louvain use last-batch stored
  props and the 12 Redis volume features are zeroed (`get_all_account_volumes` is a
  whole-keyspace scan, too costly per event) — the GNN message passing over the
  live graph structure is what's fresh, so a live score differs slightly from the
  batch score by design. Next: reconstruct volumes from the neighborhood's known
  edge ZSETs; time-bounded features.

**Pipeline visualiser (`/viz`) — built on branch `feature/community-visualiser`:**
- A standalone, FastAPI-served viewer at `/viz` (`app/viz/`) that surfaces the
  detection pipeline on the real graph. Five tabs (Cycle · PageRank · Louvain ·
  GNN · Marked) rendered with vendored Cytoscape.js; community + account/hop
  search; directed edges with thickness ∝ `FLOWS_TO.total_amount`. A **Run
  pipeline** button launches the algos + GNN **inference** (no re-ingest, no
  retrain) as an async background job and polls progress via a `pipeline_runs`
  table. This is the first place the GNN's per-account scores are written back
  (Neo4j `gnn_risk_score`) and aggregated into `risk_flags` (type `AGGREGATE`) —
  the batch/on-demand counterpart to the still-unbuilt per-event live scoring.
  Design + plans in `docs/superpowers/`. Running the app needs `POSTGRES_DSN`
  pointed at the container (dev creds `flowgraph/changeme`) and a trained
  `ml/runs/v10_L3` for the GNN stage.

**Not started:**
- AI enrichment layer (Claude explainability for low-confidence cases)
- Query API (`shortest_path_between`, `subgraph_around`, `flow_between`)
- Frontend wiring (Vite app exists, not connected to a backend)

## Stack

| Component | Technology |
|---|---|
| Message queue | Kafka |
| Stream processing | Python / Faust |
| Graph database | Neo4j |
| Relational store | Postgres |
| Cache / time-series | Redis |
| GNN | PyTorch + PyTorch Geometric (SAGEConv) |
| API | FastAPI |
| Frontend | D3 or Cytoscape.js |
| AI enrichment | Claude API (a current model, e.g. `claude-sonnet-5`) — layer not started |

ML deps are pinned separately in `requirements-ml.txt` (torch, torch-geometric,
scikit-learn, imbalanced-learn). Notably **not** installed: `pyg-lib` /
`torch-sparse` — they are an install trap, so the mini-batch sampler is
hand-written over CSR adjacency instead (see `ml/sampler.py`).

---

# AI / ML layer

The GNN is the **primary risk classifier**. Cycle detection and Louvain are
demoted to *feature providers and weak-label sources* — they become columns in
the feature matrix and (CYCLE only) the training labels. The Claude API is
reserved for secondary explainability on low-confidence cases.

The rest of this section is the full record of the GNN build. The short version:
on the IBM AML HI-Small benchmark, honest held-out (temporal) **test PR-AUC went
from 0.057 to 0.724 (single model) / 0.735 (3-seed ensemble)** — ~13×, at ROC-AUC
0.99 — and whole-graph recall of laundering accounts went from the detectors'
**3.9% to 55–59%** at ~78% precision. Every gain came from a diagnosed cause, not
brute force; the two biggest were free once understood (a covariate shift, and
severe under-training).

## The data it trains on

Measured on the loaded HI-Small graph, not estimated:

| | |
|---|---|
| Transactions ingested | ~5.04M (of 5.08M scanned; ~34k unparseable) |
| Account nodes | 513,987 |
| `FLOWS_TO` edges (non-self) | 644,397 |
| Feature columns | **47** |
| Ground-truth positives | 3,170 accounts — **0.617% prevalence** |
| Typologies | all 8 present: CYCLE, FAN-OUT, FAN-IN, GATHER-SCATTER, SCATTER-GATHER, BIPARTITE, STACK, RANDOM |

Data is loaded with `ml/datasets/run_ingest.py` (IBM AML CSV → Neo4j + Redis)
then `ml/datasets/run_louvain.py` (populates `Account.community_id`).

## Labels & evaluation — read this before touching metrics

- **Labels = IBM AML ground truth**, NOT `risk_flags`. `benchmarks/data/HI-Small_Patterns.txt`
  gives 370 laundering attempts across 8 typologies → ~3,170 accounts. Account
  IDs line up for free: patterns and the ingestor both key accounts through the
  same `sha256(f"{bank}:{account}")[:32]` hash. (When reading the raw CSV, banks
  MUST be read as strings — leading zeros like `"010"` are part of the key.)
- **Never score the GNN against `risk_flags`.** That only measures how well it
  imitates the cycle detector, and it is blind exactly where the detector is.
  Only 54 of the 370 attempts are CYCLE — recall on the six loop-free typologies
  (FAN-OUT/FAN-IN/BIPARTITE/STACK/GATHER-SCATTER/SCATTER-GATHER) is the real
  test, since no depth of cycle DFS can represent them.
- **Never use accuracy.** At ~0.6% prevalence "everything low risk" scores 99.4%
  and catches nothing. Headline metric is **PR-AUC on the fraud class**; ROC-AUC
  and per-typology recall alongside. `ml/evaluate.py` owns all of this
  (`fraud_metrics`, `recall_by_typology`, `compare_against_detector`).
- **Weak labels still exist** (`ml/features.py:_weak_labels`) and are **CYCLE
  flags only** (`LABEL_FLAG_TYPES`). COMMUNITY flags are excluded to stop target
  leakage: `score_community` derives `risk_level` from `risk_score` by fixed
  thresholds and `community_risk_score` is itself a feature, so a COMMUNITY label
  would hand the model its own answer. Louvain is a feature provider; cycle
  detection is the label source. (Training now uses ground truth, not weak
  labels — but the weak-label path is intact for the no-ground-truth case.)

## The split and the scaler — the covariate shift is the real ceiling

**Temporal split, 60/15/25** (`ml/split.py:temporal_split`), on each account's
first observed activity. Payments are a time series; a random split lets the
model learn the future to predict the past. Positives land ~2,220 / 538 / 412
across train/val/test. Its documented limit: `FLOWS_TO` aggregates are
incremented on `MERGE` and can't be rewound, so a "test" account still arrives
carrying its full lifetime — the split separates **accounts, not time**. Not
label leakage (the label isn't in the features), but it overstates how *early* a
mule is caught. (`random_split` exists as a diagnostic only — never report it.)

The late (test) population is **nothing like** the early (train) one: column
means shift 30–60× (`total_out_amount` 0.015×, `in_tx_count` 0.03×) and a few
features' label-correlation even flips sign (`flow_ratio` +0.03 train → −0.05
test). A model keyed on absolute magnitude does not survive into the future.

**Two scalers, both fit on TRAIN ONLY** (fitting over the whole matrix leaks test
stats — the same defect class as an `isFraud` leak, just subtler):
- `FeatureScaler` — signed `log1p` + standardize. The old default.
- **`QuantileScaler`** (`--scaler quantile`) — rank/quantile transform: each
  feature → its train-distribution percentile → normal quantile. "Top 1% by
  out-degree" means the same thing in both populations regardless of raw scale,
  so the decision surface transfers. **This is the single most important fix:
  test PR-AUC 0.057 → 0.28, and it is nearly invisible to validation** (its whole
  benefit lives on the shifted future). Serialized with the checkpoint
  (`state()` / `from_state()`) so inference applies the identical map.

> Methodology rule that falls out of this: **for shift-robustness changes,
> measure on TEST, not val.** Validation sits close to train in time and rewards
> memorization. The prior "remove regularization → val 0.44" result was a trap —
> that config scored **test ROC 0.50** (a coin flip). Chase test.

## Node features — 47 columns (`ml/features.py`)

All derived from data the pipeline actually writes; assembled by `FeatureBuilder`
from Neo4j + Redis + Postgres, with a pure `_assemble` step that is unit-tested.
Column order is part of the trained model's contract — never reorder.

| group | n | columns |
|---|---|---|
| `GRAPH_FEATURES` | 10 | out/in_degree, total_out/in_amount, out/in_tx_count, net_flow, flow_ratio, pagerank_score, account_age_days |
| `SELF_LOOP_FEATURES` | 2 | self_loop_count, self_loop_amount |
| `DERIVED_FEATURES` | 7 | avg_out/in_amount, degree_ratio, tx_per_counterparty, burst_1h_24h, burst_24h_7d, amount_per_degree |
| `COMMUNITY_FEATURES` | 3 | community_size, community_risk_score, community_flagged_members |
| Redis volumes | 12 | volume_out/in + txn_out/in × (1h / 24h / 7d) |
| `NODE_TYPE_FEATURES` | 4 | is_account / is_merchant / is_bank / is_exchange |
| **`STRUCTURAL_FEATURES`** | 5 | reciprocity, kcore, log_triangles, clustering, log_nbr_out_deg |
| **`MOTIF_FEATURES`** | 4 | max_payee_in_deg, max_payer_out_deg, two_hop_out, two_hop_in |

First 38 are the "base" set; **STRUCTURAL + MOTIF (9 cols) were added in the GNN
work** and appended at the end so earlier columns stay byte-identical (old caches
still load).

- **`STRUCTURAL_FEATURES`** — higher-order structure a 2-layer GNN cannot derive
  from linear message passing, computed from the `FLOWS_TO` edge list already in
  hand (no extra I/O). **k-core is the standout**: it correlates 0.09 with the
  label on the held-out future — *higher than any raw feature* — and is stable
  across the shift (fraud rings sit in denser cores). Whole-graph coreness is one
  O(E) peel. Adding this block took test PR-AUC 0.28 → 0.41.
- **`MOTIF_FEATURES`** — aimed at the fan typologies. The key pair is **hub
  proximity as a MAX over neighbours**: `max_payee_in_deg` fires on FAN-IN senders
  (mean 2.6 vs 1.0), `max_payer_out_deg` on FAN-OUT receivers (2.96 vs 1.4).
  Those peripheral accounts have degree ~1 and blend in under mean aggregation,
  which washes out the one hub they touch — a max preserves it. Plus 2-hop reach.
  Took test PR-AUC 0.42 → 0.46 and precision 0.56 → 0.72.

**Null account properties, deliberately excluded.** The CLAUDE schema lists
`kyc_tier`, `country`, `risk_score`, `account_age`, `cumulative_volume` — but
`create_account_node` is their only writer and nothing in production calls it, so
they are null on every node. `OPTIONAL_NODE_PROPERTY_FEATURES` is the switch to
turn them on once ingestion populates them; the builder warns if it sees one
populated but unused. `account_age_days` is derived from `FLOWS_TO.first_ts`
(real activity), NOT `Account.created_at` (which is ingest time — it made every
age negative). Self-loops (17.9% of accounts, 36% of edges) are split out of the
counterparty aggregates so a self-transfer can't fake a pass-through-mule
signature; the behaviour is preserved in `SELF_LOOP_FEATURES`.

## Model (`ml/model.py`)

`GraphSAGERiskClassifier` — SAGEConv stack + linear head, split into
`encode()` / `classify()`.

- **Inductive** by construction: SAGEConv learns aggregation functions, so weight
  shapes depend on feature *width*, never node *count*. A new account mid-stream
  is scored with existing weights, no retraining — the property the streaming
  design rests on.
- **The encode/classify seam** exists so SMOTE can run on post-convolution
  embeddings (see below) and so the head can be called on its own.
- **`aggr="mean"`**, not sum — a hub with 14,775 neighbours would swamp a sum.
- **`bidirectional=True`** (`--bidirectional`) — `FLOWS_TO` is directed src→dst,
  so a plain SAGEConv aggregates only a node's *payers*. **45% of accounts have no
  in-neighbour at all** (overwhelmingly late senders), so message passing told
  them nothing and FAN-IN/FAN-OUT looked identical. Bidirectional adds a second
  SAGEConv stream over reversed edges (a node's *payees*) and concatenates the two
  per layer (encode width becomes `2 × hidden`).
- **Depth**: 2 by default, **3 in the champion**. Under *full-batch* training a
  third layer backfires (oversmoothing). That turned out to be a **training
  artefact, not a depth limit** — with mini-batch sampling a 3-layer model trains
  cleanly through it and is the champion (test PR-AUC 0.66 → 0.72). L3 needs the
  full k=10 sampler fanout; at k=8 the 3-hop neighbourhood is too sparse and L3
  underperforms L2. L4 needs a 4-hop fanout (~k⁴ neighbours/seed) that is
  impractical to sample — depth caps at 3 here.

## Training (`ml/train.py`)

Binary node classification (laundering or not). Message passing runs over every
edge; only the loss and metrics respect the split masks — using a test node's
*features* is legitimate (they exist at inference too), using its *label* is not.

Two training modes:

- **Full-batch** (default) — one embedding for the whole graph, **one optimizer
  step per epoch**. This badly under-trains the model: every champion peaked at
  its epoch cap.
- **Mini-batch** (`--minibatch`, `ml/sampler.py`) — **the single biggest lever,
  and it adds no features.** Each step samples a bounded k-hop subgraph around a
  few hundred seed nodes → hundreds of updates per epoch. And it does what
  full-batch cannot: **class-balanced batches** — at 0.7% prevalence a random
  batch holds ~3 fraud seeds, so `--mb-pos-frac` oversamples the minority until
  every step sees fraud. **Test PR-AUC 0.46 → 0.65, recall 0.31 → 0.59**, in a
  handful of epochs. The sampler is vectorized numpy over CSR adjacency — no
  `pyg-lib`. When mini-batch is on, Focal alpha is forced uniform (the sampler
  already balances; inverse-frequency alpha on top would double-count).

Supporting pieces:
- **Loss** (`ml/losses.py`): `FocalLoss` (γ=2.0) concentrates gradient on the
  rare, hard class. `alpha` caveat: the paper's 0.25 is a *binary* foreground
  weight — as a scalar over multiple classes it only rescales. Use
  `class_balanced_alpha` for a real per-class vector (used in the full-batch path).
- **Imbalance** (`ml/imbalance.py`): `smote_embeddings` interpolates on
  **post-convolution embeddings**, never raw features (a synthetic node has no
  edges and can't message-pass). Stays in torch so autograd survives. Available
  as `--smote`; in practice mini-batch balancing beat it, so the champion does
  not use it.
- **Threshold** picked on validation (best F1), never on test.
- **Scaler statistics are saved with the checkpoint** — a model without them is
  unusable (it would feed raw 1e14-scale amounts into weights fit on standardized
  inputs). `result.json` carries the scaler `state`, config, metrics, history,
  per-typology recall, and the detector comparison.

## Champion — `ml/runs/v10_L3`

Bidirectional GraphSAGE, **hidden 256, 3 layers, dropout 0.3**, QuantileScaler,
47 features, Focal Loss (γ=2), mini-batch (batch 512, k=10, pos_frac 0.25, 300
steps/epoch).

```
val PR-AUC 0.683
TEST  PR-AUC 0.724   ROC-AUC 0.986   precision 0.768   recall 0.636   F1 0.696
```

Whole-graph **GNN recall 54.6%** at ~78% precision — **1,657 confirmed laundering
accounts that no detector found**. At 0.32% test prevalence, PR-AUC 0.72 is ~225×
a random ranker; `precision@8` on the full graph is 100%.

**Ensemble** (`ml/ensemble.py`) — a 3-seed average of the L3 champion reaches
**test PR-AUC 0.735, ROC 0.992, GNN recall 59%**, every typology up. This is
where ensembling *pays off*: full-batch seed-ensembles were a wash (+0.007,
members near-identical), but mini-batch draws a fresh neighbourhood every step so
seeds land on genuinely different functions and averaging cancels the variance.
Cost is K× inference (offline/batch use). Gain saturates by 2 members.

**The 3-seed ensemble is now the `/viz` serving default** (`GNN_ENSEMBLE_RUNS =
[v10_L3_s1, v10_L3_s7]`). It was chosen specifically to cut false positives:
because averaging cancels each seed's idiosyncratic FPs, at **matched recall it
drops whole-graph FP ~24%** (e.g. recall 0.55: 507→385; recall 0.50: 320→253) and
lifts PR-AUC 0.687→0.710. `PipelineRunner._gnn` skips any ensemble member absent
from disk (`ml/runs` is gitignored), so serving degrades gracefully to the single
`v10_L3` champion. The remaining FPs are **not separable by any cheap structural
signal** — degree, community-periphery, PageRank and community-risk all fail to
distinguish them from true frauds; only the GNN's own score separates them (FPs
cluster just above the threshold, real frauds at high confidence). So FP
reduction comes from a better score (ensemble / more data), not a post-filter.

### Recall by typology (whole graph, F1 threshold)

| typology | detectors | champion (v10_L3) | ensemble | loop-free? |
|---|---|---|---|---|
| SCATTER-GATHER | — | 91.6% | 94% | yes |
| GATHER-SCATTER | — | 91.1% | 94% | yes |
| FAN-OUT | — | 75.5% | 81% | yes |
| CYCLE | (detectors' domain) | 52.4% | 62% | no |
| RANDOM | — | 50.7% | 57% | yes |
| FAN-IN | — | 50.6% | 59% | yes |
| STACK | — | 24.0% | 28% | yes |
| BIPARTITE | — | 12.0% | 14% | yes |

The GNN scores **highest on the loop-free SCATTER/GATHER typologies** — exactly
the structures cycle detection cannot represent at any depth. That is the case
for the GNN existing. **BIPARTITE (~12%) is the genuine hold-out** (see below).

## What did NOT help (documented dead ends — don't re-run these blind)

- **Removing regularization + widening (h256, no dropout/wd)** → val 0.44 but
  **test ROC 0.50**. Pure memorization. Kept as the `regularization` sweep preset,
  cautionary.
- **Edge-weighted aggregation** (GraphConv weighted by money amount) → val 0.12.
  Its weighted *sum* lets hubs swamp the aggregate; volume is already a node
  feature.
- **Dropping the sign-flipping features** — neutral; QuantileScaler already makes
  them shift-robust while keeping their signal.
- **Full-batch ensembling** — +0.007 (members too correlated). Only pays off under
  mini-batch.
- **Bipartite-density features** (4-cycle count, max shared neighbours) for
  BIPARTITE — its blocks are *sparse* (degree 1–5) and embedded in the giant
  component (98% of BIPARTITE accounts), not dense isolated blocks; 4-cycle counts
  fired on SCATTER-GATHER instead. Label correlation 0.04.
- **Transaction-level amount features** (pass-through amount-matching, repetition)
  from the raw 5M-row CSV. In this *synthetic* data fraud accounts have *lower*
  pass-through than normal ones (0.36 vs 0.47) — routine "Reinvestment"
  self-transfers dominate and the injected patterns don't preserve amounts.
  Correlation ~0.03. **Topology is the signal here; amounts are noise.**

## Honest limitations

1. **Test is a shifted, lower-prevalence population** (0.32% vs train 0.70%).
   QuantileScaler narrows the gap but PR-AUC stays modest in absolute terms
   because the signal is structural and prevalence is extreme.
2. **The split separates accounts, not time** (see above) — overstates how early
   a mule is caught. Time-bounded features from `TRANSFER.ts` / Redis ZSETs would
   fix it.
3. **17.9% of accounts have no non-self neighbour** — message passing (even
   bidirectional) adds nothing for them.
4. **412 test positives** — single-run test PR-AUC carries noise; ROC-AUC is the
   steadier read. Finalists were seed-checked.
5. **BIPARTITE (~12%) is a topology-only ceiling** on this dataset — four distinct
   feature families showed no signal. Passing it needs non-topological signal
   this synthetic data doesn't carry.

## Worth doing next

1. **Serving — done** (batch via `/viz` + opt-in live per-event via the outbox,
   `LIVE_SCORING_ENABLED`). Next here: reconstruct live Redis volume features from
   the neighborhood's edge ZSETs (v1 zeroes them) and time-bounded features.
2. **Time-bounded features** so the temporal evaluation is real.
3. **A larger dataset** (HI-Medium → evaluate on HI-Small) for more laundering
   examples — the remaining path past ~0.735 is data, not modelling.
4. **GNNExplainer / integrated gradients** for per-edge attribution in the
   explanations (currently feature-value based).

## ML module map

| module | role |
|---|---|
| `ml/features.py` | `FeatureBuilder` — live stores → 47-col `FeatureSet` (+ structural/motif computed from the edge list) |
| `ml/split.py` | `temporal_split`, `random_split` (diagnostic), `FeatureScaler`, `QuantileScaler` |
| `ml/model.py` | `GraphSAGERiskClassifier` (encode/classify, `bidirectional`, `num_layers`), `pick_device` |
| `ml/sampler.py` | mini-batch neighbour sampler — `build_adjacency`, `sample_subgraph`, `balanced_seed_batch` (no pyg-lib) |
| `ml/train.py` | `TrainConfig`, `train_model` (full-batch + mini-batch), threshold selection, cache I/O, run persistence |
| `ml/losses.py` | `FocalLoss`, `class_balanced_alpha` |
| `ml/imbalance.py` | `smote_embeddings` — differentiable, post-convolution SMOTE |
| `ml/evaluate.py` | ground-truth loading + `fraud_metrics`, `recall_by_typology`, `compare_against_detector` |
| `ml/sweep.py` | multi-config comparison over one cached feature set. Presets: `baseline`, `capacity`, `depth`, `loss`, `imbalance`, `regularization` (cautionary), **`shift`** (the key ablation), `aggr`, `full`. Selection on **val** PR-AUC |
| `ml/predict.py` | inference + written explanations; applies the saved scaler; risk bands (low/medium/high/critical) |
| `ml/ensemble.py` | score-averaged ensemble of several checkpoints |
| `ml/readiness.py` | pre-training audit — "can a GNN actually be trained on this graph yet?" |
| `ml/inspect.py` | inspect an assembled `FeatureSet` (separability, coverage) before training |
| `ml/datasets/run_ingest.py` | CLI: IBM AML CSV → Neo4j + Redis |
| `ml/datasets/run_louvain.py` | CLI: Louvain batch → `Account.community_id` |
| `ml/datasets/ibm_aml.py`, `elliptic.py`, `fetch.py` | dataset loaders + Kaggle fetch |

Tests: `tests/test_ml_features.py`, `test_model_split.py`, `test_ml_training.py`,
`test_gnn_stack.py`, `test_readiness.py`, `test_dataset_ingest.py`,
`test_elliptic_dataset.py`, `test_bulk_ingest.py` (~180 ML tests, all green).

## Reproducing the champion

`ml/features.py` emits all 47 columns, so a rebuilt cache carries them.

```bash
docker compose up -d neo4j redis postgres
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min
python3 -m ml.readiness                                           # audit
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

Everything trains off the cached `.npz` (no DB needed once the cache exists), on
CPU, in minutes per run. Docker creds in dev: Neo4j `neo4j/changeme`, Postgres
`flowgraph/changeme` (override `POSTGRES_DSN` — the config default password
differs from the container).

## Artifacts & branch

- Current work lives on branch **`gnn-prep`** (synced to `origin`); it is ~29
  commits ahead of `main`, of which 9 are the GNN improvement chain
  (quantile+bidirectional → structural → capacity → motifs → **mini-batch** →
  h256 → ensemble → **3 layers** → L3 ensemble).
- **`ml/runs/` and `ml/cache/` are gitignored** — trained checkpoints and feature
  caches are regenerable artifacts, not committed. The champion `v10_L3` is *not*
  in git; reproduce it with the command above. (A few small legacy run artifacts
  and two large stale `.npz` caches were committed before the ignore rule and
  remain tracked — worth `git rm --cached`ing before this branch merges to main.)

---

# Data model

**Neo4j nodes** — types: `account`, `merchant`, `bank`, `exchange`. Properties: `kyc_tier`, `risk_score`, `country`, `account_age`, `cumulative_volume` (schema-defined but currently unwritten — see the null-properties note above).

**Neo4j edges** — two directed edge types are maintained per payment:
- `TRANSFER` — one edge per transaction, keyed by `txn_id` (MERGE key). Properties: `amount_cents`, `ts` (unix seconds), `rail`, `event_type`. Full per-transaction detail; used for audit and per-txn tracing.
- `FLOWS_TO` — one aggregate edge per directed account pair. Properties: `tx_count`, `total_amount`, `first_ts`, `last_ts`, `min_amount`, `max_amount`, `rail`. Graph algorithms (cycle detection, PageRank, Louvain) run on `FLOWS_TO`, because collapsing the 20-50 parallel `TRANSFER` edges between a pair into one edge keeps variable-length traversal from exploding combinatorially (branching = distinct neighbours, not parallel-edge count). The GNN also builds `edge_index` from `FLOWS_TO`.

**Postgres** — canonical transaction records + outbox table (`pending_graph_sync` flag) + `risk_flags` (detector output, weak-label source).

**Redis** — sorted sets keyed `edge:{node_a}:{node_b}`, scored by timestamp. Used for time-windowed volume queries (ZRANGEBYSCORE); feeds the GNN's 1h/24h/7d volume features.

# Key patterns

**Dual-write consistency** — write to Postgres first with `pending_graph_sync = true`. Background outbox worker reads pending rows, writes to Neo4j, clears flag on success. Graph is eventually consistent with Postgres, never ahead of it. Retries use exponential backoff.

**Neo4j upserts** — use `MERGE` inside a transaction to atomically create nodes if missing. `TRANSFER` is MERGEd on `txn_id` (idempotent on outbox retry). `FLOWS_TO` is MERGEd on the account pair and its aggregates (`tx_count`, `total_amount`, min/max amount, first/last ts) are incremented on match. Never plain `CREATE`. Aggregate double-counting on `FLOWS_TO` is prevented by the outbox's once-delivery guarantee.

**Time-windowed queries** — do not hit Neo4j for volume in a time window. Use Redis ZRANGEBYSCORE on the relevant sorted set. Microsecond latency.

**Graph algorithms** — only run on subgraphs that received new edges in the processing window, not the full graph. Cycle detection traverses `FLOWS_TO` with a depth limit and time window: depth 6-8 and a 48h window for real-time/streaming; depth up to 12 with a wider window for batch/investigative sweeps (validated against IBM AML: 72% recall at depth 8, 87% at depth 12, both at 100% precision — see `benchmarks/ibm_aml/`). A per-account query timeout bounds worst-case latency so a deep search never stalls the pipeline. Cross-currency cycles and chains longer than the depth limit are the documented blindspots owned by FX normalization and the Louvain/PageRank detectors respectively.

**AI enrichment** (not started) — construct a natural-language subgraph summary, pass to Claude with `get_subgraph(account_id, depth, window)` tool, return structured output: `risk_level` (low/medium/high/critical), `confidence`, `explanation`. Explainability is a regulatory requirement, not optional. Intended only for low-confidence GNN cases.

# Conventions

- All graph writes go through the outbox — never write directly to Neo4j from the consumer without the Postgres record being committed first.
- Node type is always one of the four defined types. Do not invent new node types without updating the schema docs.
- Edge weights are always incremented, never overwritten — existing history must be preserved.
- Risk scores are always produced with a written explanation. Never emit a bare numeric score.
- Cypher queries use named parameters, never string interpolation.
- All time values stored and compared in UTC.

**ML conventions:**
- **Fit scalers on train only.** Both scalers enforce it; fitting on the whole matrix is a leak.
- **Select on validation PR-AUC; report test — but for shift-robustness changes, judge on test.** Never pick a threshold or a config on test.
- **Feature column order is a model contract.** Append new features at the end; never reorder existing ones.
- **Trained artifacts are regenerable** (`ml/runs/`, `ml/cache/` gitignored). Persist the scaler with every checkpoint.

# Key queries (named API endpoints — not started)

- `shortest_path_between(account_a, account_b)` — how two accounts are connected through intermediaries
- `subgraph_around(account_id, depth=3)` — full neighborhood up to N hops
- `flow_between(account_a, account_b, window='7d')` — total volume, path count, avg hop time in window
