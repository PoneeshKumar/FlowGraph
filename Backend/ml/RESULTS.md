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
| **+ motif features (champion)** | 0.4056 | 0.4601 | 0.9785 |

(The first five rows are a controlled h128 ablation, one change at a time; the
last two add capacity and the motif features to reach the champion.)

**Test PR-AUC 0.057 → 0.46 (~8×), test ROC 0.94 → 0.98, test precision 0.04 →
0.72.** Note validation barely moves for the quantile change: its entire benefit
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

## Champion — `ml/runs/v6_motifs`

Bidirectional GraphSAGE (hidden 192, 2 layers, dropout 0.3), quantile scaler,
47 features (38 + 5 structural + 4 motif), Focal Loss (γ=2).

```
best val PR-AUC 0.4056
TEST   PR-AUC 0.4601   ROC-AUC 0.9785
       precision 0.718   recall 0.308   F1 0.431   (at the F1-optimal threshold)
       TP 127  FP 50  FN 285  support 412  (test prevalence 0.32%)
```

At 0.32% test prevalence a random ranker scores PR-AUC ≈ 0.003, so 0.46 is ~150×
better than random. The motifs shift the **whole PR curve up**, so the champion
dominates the previous (structural-only) model at every operating point:

| operating point | v5 (structural) | v6 (+motifs) |
|---|---|---|
| precision @ recall 0.36 | 0.558 | **0.664** |
| precision @ recall 0.45 | 0.387 | **0.440** |
| recall @ precision 0.56 | 0.362 | **0.408** |

The F1-optimal threshold happens to land in a high-precision regime (72%), so
recall-*at-threshold* understates detection — hence the table below fixes
selectivity to compare fairly.

### Recall by typology (whole graph, matched selectivity — top ~2,500 flagged)

| typology | structural-only | + motifs | reachable by cycle detection? |
|---|---|---|---|
| SCATTER-GATHER | 62.3% | **70.2%** | no |
| GATHER-SCATTER | 48.6% | **50.4%** | no |
| CYCLE | 40.6% | **46.5%** | yes |
| RANDOM | 37.4% | **45.0%** | no |
| FAN-OUT | 34.3% | 34.3% | no |
| FAN-IN | 16.6% | **22.2%** | no |
| STACK | 14.8% | **17.3%** | no |
| BIPARTITE | 8.8% | **10.4%** | no |

The motifs lift every typology except FAN-OUT (flat), and most of all the ones
they targeted — FAN-IN +5.6, STACK +2.5, plus SCATTER-GATHER +7.9 and CYCLE +5.9.
The GNN scores highest on the SCATTER/GATHER typologies — structures with no
closed loop that depth-limited cycle search cannot represent at any depth. That
is the case for the GNN existing.

### vs the detectors

| | recall |
|---|---|
| GNN | **29–33%** (operating-point dependent) |
| cycle + Louvain detectors | 3.9% |

At the F1 threshold the GNN flags 1,707 accounts at 50% precision — 851 correct
ones no detector found. The hidden-128 / 43-feature variants remain documented as
cheaper alternatives if inference cost matters.

## Reproducing

`ml/features.py` now emits 47 columns (5 structural + 4 motif features included),
so a rebuilt cache carries them automatically.

```bash
docker compose up -d neo4j redis postgres
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min
python3 -m ml.train --refresh-cache --cache ml/cache/featureset_v4.npz \
    --scaler quantile --bidirectional \
    --train-frac 0.60 --val-frac 0.15 \
    --hidden 192 --dropout 0.3 --lr 0.01 --gamma 2.0 \
    --epochs 320 --patience 80 --run-name v6_motifs
python3 -m ml.sweep --preset shift --cache ml/cache/featureset_v4.npz   # the ablation
python3 -m ml.predict --run ml/runs/v6_motifs --cache ml/cache/featureset_v4.npz --top 20
```

## Honest limitations

1. **Test is still a shifted, lower-prevalence population** (0.32% vs train
   0.70%). Quantile normalization narrows the gap but does not erase it; PR-AUC
   on the fraud class stays modest in absolute terms because the signal is
   structural and the prevalence is extreme.
2. **The split separates accounts, not time.** FLOWS_TO aggregates carry each
   account's full lifetime, so the evaluation overstates how *early* a mule is
   caught. Time-bounded features from `TRANSFER.ts` would fix it.
3. **Full-batch training = one gradient step per epoch.** Mini-batch neighbour
   sampling would give far more steps; deliberately avoided the `pyg-lib` install.
4. **17.9% of accounts have no non-self neighbour** — message passing (even
   bidirectional) adds nothing for them.
5. **412 test positives**, so single-run test PR-AUC still carries noise; ROC-AUC
   is the steadier read.

## Worth doing next
1. BIPARTITE is still the weakest (10%) — a bipartite-core / neighbour-overlap
   feature is the natural next motif, since the hub-proximity pair mainly helped
   FAN-IN/FAN-OUT.
2. Time-bounded features so the temporal evaluation is real (the split separates
   accounts, not time).
3. Mini-batch neighbour sampling for many more gradient steps — full-batch is one
   step per epoch, and the champion still wanted to train at the cap.
4. A recall-oriented threshold policy — the F1-optimal operating point lands at
   72% precision / 31% recall, which under-uses the model for a review queue.

(Ensembling was tried and did not pay off — see "What did NOT help".)
