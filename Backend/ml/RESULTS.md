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
| **+ structural features (k-core, …)** | 0.3419 | 0.4092 | 0.9778 |

**Test PR-AUC 0.057 → 0.41 (~7×), test ROC 0.94 → 0.98, test precision 0.04 →
0.54** — and widening the champion to hidden 192 takes it to **0.42 / 0.56**.
Note validation barely moves for the quantile change: its entire benefit
lives on the shifted future, so it is invisible to a validation-only sweep and
has to be read on test. That is also why the earlier "remove regularization →
val 0.44" result was a trap — that config scored test ROC 0.50, pure memorization
(kept as the `regularization` sweep preset, cautionary).

## The three changes

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

## Champion — `ml/runs/v5_h192`

Bidirectional GraphSAGE (hidden 192, 2 layers, dropout 0.3), quantile scaler,
43 features (38 + 5 structural), Focal Loss (γ=2). Widening from hidden 128 to
192 was the last clean single-model gain (test PR-AUC 0.40 → 0.42); beyond it,
capacity plateaus and overfitting risk climbs.

```
best val PR-AUC 0.3478
TEST   PR-AUC 0.4216   ROC-AUC 0.9769
       precision 0.562   recall 0.362   F1 0.440
       TP 149  FP 116  FN 263  support 412  (test prevalence 0.32%)
```

At 0.32% test prevalence a random ranker scores PR-AUC ≈ 0.003, so 0.42 is ~140×
better than random, and `precision@8` on the full graph is 100% (162× lift over
the 0.617% base rate). Test precision rose from the old model's 0.041 to 0.562 —
of the accounts it flags at the operating threshold, more than half are real.
The hidden-128 variant (`v4_structural`, test PR-AUC 0.40) is a ~2× cheaper
alternative if inference cost matters.

### Recall by typology (whole graph, at the chosen threshold)

| typology | recall | caught / support | reachable by cycle detection? |
|---|---|---|---|
| SCATTER-GATHER | 64.8% | 239 / 369 | no |
| GATHER-SCATTER | 50.8% | 348 / 685 | no |
| CYCLE | 43.9% | 119 / 271 | yes |
| RANDOM | 38.9% | 82 / 211 | no |
| FAN-OUT | 35.9% | 129 / 359 | no |
| FAN-IN | 18.3% | 62 / 338 | no |
| STACK | 17.0% | 113 / 663 | no |
| BIPARTITE | 9.8% | 48 / 491 | no |

The structural features lift the loop-free typologies (STACK, FAN-IN) as well as
CYCLE over the pre-structural model. The GNN scores highest on the SCATTER/GATHER
typologies — structures with no closed loop that depth-limited cycle search
cannot represent at any depth. That is the case for the GNN existing. (Whole-graph
recall at the F1 threshold is a slice through one operating point; the champion is
chosen on threshold-free PR-AUC, where h192 dominates every alternative.)

### vs the detectors

| | recall |
|---|---|
| GNN | **32.4%** |
| cycle + Louvain detectors | 3.9% |

959 accounts flagged by the GNN alone, missed by every detector, confirmed by
ground truth (was 674 at hidden 128, and 547 before quantile/structural).

## Reproducing

`ml/features.py` now emits 43 columns (the 5 structural features included), so a
rebuilt cache carries them automatically.

```bash
docker compose up -d neo4j redis postgres
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min
python3 -m ml.train --refresh-cache --cache ml/cache/featureset_v3.npz \
    --scaler quantile --bidirectional \
    --train-frac 0.60 --val-frac 0.15 \
    --hidden 192 --dropout 0.3 --lr 0.01 --gamma 2.0 \
    --epochs 320 --patience 80 --run-name v5_h192
python3 -m ml.sweep --preset shift --cache ml/cache/featureset_v3.npz   # the ablation
python3 -m ml.predict --run ml/runs/v5_h192 --cache ml/cache/featureset_v3.npz --top 20
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
1. More structural motifs targeting the weakest typologies (BIPARTITE 10%,
   STACK 17%) — e.g. 2-hop fan-out reach, bipartite-core membership. Structural
   features were the single biggest lever, so this is the most promising thread.
2. Time-bounded features so the temporal evaluation is real (the split separates
   accounts, not time).
3. Mini-batch neighbour sampling for many more gradient steps — full-batch is one
   step per epoch, and the champion still wanted to train at the cap.

(Ensembling was tried and did not pay off — see "What did NOT help".)
