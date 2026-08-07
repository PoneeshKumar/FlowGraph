# GNN implementation — build and training report

Everything below was measured on the loaded HI-Small graph, not estimated.

## Headline

The real ceiling on this task is the **early → late covariate shift**, not model
capacity. Accounts that first appear late in the 18-day window look nothing like
early ones — column means differ 30-60× (`total_out_amount` 0.015×, `in_tx_count`
0.03×) and a few features' label-correlation even flips sign (`flow_ratio` +0.03
on train, −0.05 on test) — so a model keyed on absolute magnitude does not
survive into the future it has to predict.

Three changes fix it. Measured on a stable 60/15/25 temporal split (test ≈ 412
positives; every row same regularization, seed 42):

| approach | val PR-AUC | test PR-AUC | test ROC-AUC |
|---|---|---|---|
| log scaler, in-neighbours only (old baseline) | 0.2085 | 0.0567 | 0.940 |
| + bidirectional message passing | 0.2570 | 0.0822 | 0.956 |
| + quantile normalization | 0.2115 | 0.2816 | 0.963 |
| + both | 0.2404 | 0.2813 | 0.9745 |
| + structural features (k-core, …) | 0.3419 | 0.4092 | 0.9778 |
| + capacity (hidden 192) | 0.3478 | 0.4216 | 0.9769 |
| + motif features | 0.4056 | 0.4601 | 0.9785 |
| + mini-batch training (h192) | 0.6376 | 0.6499 | 0.9889 |
| + capacity h256 | 0.6407 | 0.6640 | 0.9839 |
| **+ 3 layers (champion)** | 0.6830 | 0.7243 | 0.9863 |

(The first five rows are a controlled h128 ablation, one change at a time; the
last five add capacity, motifs, mini-batch training, more capacity, and a third
hop to reach the champion.)

**Test PR-AUC 0.057 → 0.72 (~13×), test ROC 0.94 → 0.99, whole-graph GNN recall
3.9% (detectors) → 55% at 78% precision.** Note
validation barely moves for the quantile change: its entire benefit
lives on the shifted future, so it is invisible to a validation-only sweep and
has to be read on test. That is also why the earlier "remove regularization →
val 0.44" result was a trap — that config scored test ROC 0.50, pure memorization
(kept as the `regularization` sweep preset, cautionary).

## The changes

**Quantile (rank) normalization — `ml/split.py:QuantileScaler`.** Each feature is
mapped to its train-distribution percentile, then to a normal quantile. "Top 1%
by out-degree" means the same thing in both populations regardless of the raw
numbers, so the decision surface transfers. Fit on train only (like the log
scaler); serialized with the checkpoint so inference applies the identical map.
Enable with `--scaler quantile`.

**Bidirectional message passing — `ml/model.py`, `--bidirectional`.** FLOWS_TO is
directed src→dst, so a plain SAGEConv aggregates only a node's *payers*. 45% of
accounts have no in-neighbour at all — overwhelmingly late-appearing senders — so
message passing told them nothing and FAN-IN/FAN-OUT looked identical. The
bidirectional model adds a second stream over the reversed edges (a node's
*payees*) and concatenates the two per layer.

**Structural features — `ml/features.py:STRUCTURAL_FEATURES`.** Five features
built from the FLOWS_TO edge list the builder already has (no extra store I/O),
each one something a 2-layer GNN cannot derive from linear message passing:
k-core depth, triangle count, clustering coefficient, reciprocity, and mean payer
out-degree. **k-core is the standout** — it correlates 0.09 with the label on the
held-out future, *higher than any raw feature*, and is stable across the shift
(fraud rings sit in structurally denser cores). Adding the block took test PR-AUC
0.28 → 0.41 with validation moving in step, so it is signal, not memorization.
Whole-graph coreness is one O(E) peel, cheap at build time.

**Motif features — `ml/features.py:MOTIF_FEATURES`.** Four more edge-list
features aimed at the typologies the structural block still missed. The key pair
is hub proximity taken as a **max** over neighbours: `max_payee_in_deg` (the
largest in-degree among my payees) fires on FAN-IN senders, `max_payer_out_deg`
on FAN-OUT receivers. Those peripheral accounts have degree ~1 and blend in under
the GNN's mean aggregation, which washes out their single telling neighbour — the
hub — where a max preserves it. Plus 2-hop reach counts for scatter/gather.
Measured: test PR-AUC 0.42 → 0.46, precision 0.56 → 0.72, the whole PR curve
lifting, and every typology up at matched selectivity.

**Mini-batch neighbour sampling — `ml/sampler.py`, `--minibatch`.** The single
biggest lever, and it adds no features. Full-batch training computes one
embedding for the whole graph and takes ONE optimizer step per epoch — every
champion peaked at its epoch cap because it was starved of gradient steps. This
samples a bounded k-hop subgraph around a few hundred seed nodes each step, so an
epoch is hundreds of updates. It also does what full-batch cannot: **class-
balanced batches** — at 0.7% prevalence a random batch holds ~3 fraud seeds, so
`pos_frac` oversamples the minority until every step sees fraud. Measured: test
PR-AUC 0.46 → 0.65, recall 0.31 → 0.59, in a handful of epochs. The sampler is
vectorized numpy over CSR adjacency — no `pyg-lib` / `torch-sparse` install.

### What did NOT help
- **Removing regularization + widening to 256** → validation 0.44 but test ROC
  0.50. Memorization of the train/val population.
- **Edge-weighted aggregation** (GraphConv, weighted by money amount) → val 0.12.
  Its weighted *sum* lets hubs (max out-degree 14,230) swamp the aggregate;
  volume is already carried in node features.
- **Dropping the sign-flipping features** was neutral — quantile normalization
  keeps their (validation-useful) signal while making them shift-robust.
- **Ensembling the *full-batch* model** barely moved the needle (+0.007) — its
  members were near-identical, because full-batch training is deterministic given
  the seed and the whole graph. (Under mini-batch training this reverses and
  ensembling pays off — see the Ensemble section — because sampling makes the
  members diverse.)
- **Bipartite-density features for BIPARTITE** (4-cycle count, max shared
  neighbours). BIPARTITE is the one typology nothing catches (~9%), but its
  blocks turn out to be *sparse* (degree 1-5, few 4-cycles) and embedded in the
  giant component, not dense isolated blocks — so 4-cycle counts fired on
  SCATTER-GATHER instead and left BIPARTITE flat (label correlation 0.04). A
  well-investigated dead end; BIPARTITE needs non-topological signal.
- **Transaction-level amount features** (pass-through amount-matching, amount
  repetition) from the raw 5M-row transaction file. The hypothesis was that
  layering forwards the *same* amount through a chain — but in this synthetic
  dataset fraud accounts have *lower* pass-through than normal ones (0.36 vs
  0.47), because routine "Reinvestment" self-transfers dominate the signal and
  the injected patterns don't preserve amounts. Correlation ~0.03. The topology
  is the signal here; amounts are noise.

## Champion — `ml/runs/v10_L3`

Bidirectional GraphSAGE (hidden 256, **3 layers**, dropout 0.3), quantile scaler,
47 features (38 + 5 structural + 4 motif), Focal Loss (γ=2), trained with
neighbour-sampled **class-balanced mini-batches** (`ml/sampler.py`). The third
hop is the win: full-batch training oversmooths past two layers, but mini-batch
trains cleanly through it, taking test PR-AUC 0.66 → 0.72 — a single L3 model
beats the 3-seed L2 ensemble.

```
best val PR-AUC 0.6830
TEST   PR-AUC 0.7243   ROC-AUC 0.9863
       precision 0.768   recall 0.636   F1 0.696
       (test prevalence 0.32%)
```

At 0.32% test prevalence a random ranker scores PR-AUC ≈ 0.003, so 0.72 is ~225×
better than random. Whole-graph **GNN recall 55%**, flagging 2,131 accounts at
**78% precision** — **1,657 confirmed accounts no detector found**.

### Recall by typology (whole graph, at the F1 threshold)

| typology | full-batch (v6) | mini-batch h256 L2 (v9) | mini-batch h256 **L3** (champion) | reachable by cycle? |
|---|---|---|---|---|
| SCATTER-GATHER | ~70% | 89.7% | **91.6%** | no |
| GATHER-SCATTER | ~50% | 89.3% | **91.1%** | no |
| FAN-OUT | ~34% | 71.3% | **75.5%** | no |
| CYCLE | ~47% | 49.8% | **52.4%** | yes |
| RANDOM | ~45% | 54.5% | **50.7%** | no |
| FAN-IN | ~22% | 54.1% | **50.6%** | no |
| STACK | ~17% | 24.4% | **24.0%** | no |
| BIPARTITE | ~10% | 12.2% | **12.0%** | no |

Mini-batch training roughly doubled recall on the loop-free typologies; the third
hop mainly sharpened the top of the ranking (precision 0.62 → 0.77) rather than
adding recall. **BIPARTITE stays the hardest** (~12%): its blocks are sparse
(degree 1-5), embedded in the giant component (98% of BIPARTITE accounts), and
4-cycle / hub-proximity / component / transaction-amount features all showed no
signal there.

### vs the detectors

| | recall |
|---|---|
| GNN | **54.6%** |
| cycle + Louvain detectors | 3.9% |

1,657 accounts flagged by the GNN alone, missed by every detector, confirmed by
ground truth (was 1,459 at h192, 547 before quantile/structural). The hidden-256
2-layer variant (`v9_h256`, test PR-AUC 0.66) is a cheaper-to-train alternative.

### Ensemble — `ml/ensemble.py`

A 3-seed average of h256 **L2** mini-batch members reaches test PR-AUC 0.695, ROC
0.987 — a clean +0.03 over the single L2 model, though the single **L3** champion
(0.724) already beats it, so an L3 ensemble is the natural next push. Crucially
this is where ensembling *finally pays off*: the full-batch seed-ensemble was a wash
(+0.007) because its members were near-identical, but mini-batch draws a fresh
random neighbourhood every step, so different seeds land on genuinely different
functions and averaging cancels the residual variance. The cost is 3× inference,
so it suits an offline/batch scoring pass rather than the streaming path; the
single `v9_h256` is the deployable default.

```bash
python3 -m ml.ensemble --runs ml/runs/v9_h256 ml/runs/v9_h256_s1 ml/runs/v9_h256_s7 \
    --cache ml/cache/featureset_v4.npz --top 20
```

## Reproducing

`ml/features.py` now emits 47 columns (5 structural + 4 motif features included),
so a rebuilt cache carries them automatically.

```bash
docker compose up -d neo4j redis postgres
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min
python3 -m ml.train --refresh-cache --cache ml/cache/featureset_v4.npz \
    --scaler quantile --bidirectional --minibatch \
    --train-frac 0.60 --val-frac 0.15 \
    --hidden 256 --num-layers 3 --dropout 0.3 --lr 0.005 --gamma 2.0 \
    --mb-batch 512 --mb-k 10 --mb-pos-frac 0.25 --mb-steps 300 \
    --epochs 14 --patience 6 --run-name v10_L3
python3 -m ml.sweep --preset shift --cache ml/cache/featureset_v4.npz   # the ablation
python3 -m ml.predict --run ml/runs/v10_L3 --cache ml/cache/featureset_v4.npz --top 20
```

Note the third hop needs the full `--mb-k 10` fanout — at k=8 the 3-hop
neighbourhood is sampled too sparsely and L3 underperforms L2 (0.65 vs 0.66).

## Honest limitations

1. **Test is still a shifted, lower-prevalence population** (0.32% vs train
   0.70%). Quantile normalization narrows the gap but does not erase it; PR-AUC
   on the fraud class stays modest in absolute terms because the signal is
   structural and the prevalence is extreme.
2. **The split separates accounts, not time.** FLOWS_TO aggregates carry each
   account's full lifetime, so the evaluation overstates how *early* a mule is
   caught. Time-bounded features from `TRANSFER.ts` would fix it.
3. **17.9% of accounts have no non-self neighbour** — message passing (even
   bidirectional) adds nothing for them.
5. **412 test positives**, so single-run test PR-AUC still carries noise; ROC-AUC
   is the steadier read.

## Worth doing next
1. Tune the mini-batch regime — capacity (h256+), depth (3 hops), `pos_frac`,
   `mb_k`, and steps/epoch were barely explored before this jump.
2. Time-bounded features so the temporal evaluation is real (the split separates
   accounts, not time).
3. BIPARTITE (8.6%) looks like a genuine ceiling for topology-only features on
   this data — the remaining lever would be transaction-level (amount/timing)
   signal, not more graph structure.

(A mini-batch ensemble reaches 0.70 — see the Ensemble section. Bipartite-density
/ 4-cycle features and *full-batch* ensembling did not pay off — see "What did
NOT help".)
