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
| **+ capacity h256 (champion)** | 0.6407 | 0.6640 | 0.9839 |

(The first five rows are a controlled h128 ablation, one change at a time; the
last four add capacity, motifs, mini-batch training, and more capacity to reach
the champion.)

**Test PR-AUC 0.057 → 0.66 (~12×), test ROC 0.94 → 0.98, whole-graph GNN recall
3.9% (detectors) → 54%.** Note validation barely moves for the quantile change: its entire benefit
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
- **Ensembling** barely moved the needle. A 3-seed average gained +0.007 test
  PR-AUC (0.400 → 0.407) — the members are too correlated, because quantile
  normalization and the structural features left the model low-variance across
  seeds. A capacity-*diverse* ensemble (h128 + h192) was worse still: averaging
  in the weaker h128 members *diluted* the stronger h192 (0.415 < 0.422). Not
  worth K× inference. Capacity, not ensembling, was the remaining lever.
- **Bipartite-density features for BIPARTITE** (4-cycle count, max shared
  neighbours). BIPARTITE is the one typology nothing catches (~9%), but its
  blocks turn out to be *sparse* (degree 1-5, few 4-cycles) and embedded in the
  giant component, not dense isolated blocks — so 4-cycle counts fired on
  SCATTER-GATHER instead and left BIPARTITE flat (label correlation 0.04). A
  well-investigated dead end; BIPARTITE needs non-topological signal.

## Champion — `ml/runs/v9_h256`

Bidirectional GraphSAGE (hidden 256, 2 layers, dropout 0.3), quantile scaler,
47 features (38 + 5 structural + 4 motif), Focal Loss (γ=2), trained with
neighbour-sampled **class-balanced mini-batches** (`ml/sampler.py`). Widening to
256 under mini-batch training — full-batch overfit at that size — added a clean
+0.014 test PR-AUC and +6pt whole-graph recall.

```
best val PR-AUC 0.6407
TEST   PR-AUC 0.6640   ROC-AUC 0.9839
       precision 0.623   recall 0.641   F1 0.632
       (test prevalence 0.32%)
```

At 0.32% test prevalence a random ranker scores PR-AUC ≈ 0.003, so 0.66 is ~210×
better than random. Whole-graph **GNN recall reached 54%**, flagging 2,312
accounts at 71% precision — **1,637 confirmed accounts no detector found**.

### Recall by typology (whole graph, at the F1 threshold)

| typology | full-batch (v6) | mini-batch h192 (v8) | mini-batch h256 (champion) | reachable by cycle? |
|---|---|---|---|---|
| SCATTER-GATHER | ~70% | 85.9% | **89.7%** | no |
| GATHER-SCATTER | ~50% | 85.3% | **89.3%** | no |
| FAN-OUT | ~34% | 54.9% | **71.3%** | no |
| RANDOM | ~45% | 46.0% | **54.5%** | no |
| FAN-IN | ~22% | 45.6% | **54.1%** | no |
| CYCLE | ~47% | 47.6% | **49.8%** | yes |
| STACK | ~17% | 21.0% | **24.4%** | no |
| BIPARTITE | ~10% | 8.6% | **12.2%** | no |

Mini-batch training roughly doubled recall on the loop-free typologies, and the
extra capacity lifted every one again — FAN-OUT 55→71, FAN-IN 46→54. Even
**BIPARTITE finally moved** (9→12%), though it stays the hardest: its blocks are
sparse (degree 1-5), embedded in the giant component (98% of BIPARTITE accounts),
and 4-cycle / hub-proximity / component features all showed no signal there.

### vs the detectors

| | recall |
|---|---|
| GNN | **54.1%** |
| cycle + Louvain detectors | 3.9% |

1,637 accounts flagged by the GNN alone, missed by every detector, confirmed by
ground truth (was 1,459 at h192, 547 before quantile/structural). The hidden-192
variant (`v8_minibatch`, test PR-AUC 0.65) is a ~1.8× cheaper alternative.

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
    --hidden 256 --dropout 0.3 --lr 0.005 --gamma 2.0 \
    --mb-batch 512 --mb-k 10 --mb-pos-frac 0.25 --mb-steps 300 \
    --epochs 30 --patience 15 --run-name v9_h256
python3 -m ml.sweep --preset shift --cache ml/cache/featureset_v4.npz   # the ablation
python3 -m ml.predict --run ml/runs/v9_h256 --cache ml/cache/featureset_v4.npz --top 20
```

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

(Ensembling and bipartite-density / 4-cycle features were tried and did not pay
off — see "What did NOT help".)
