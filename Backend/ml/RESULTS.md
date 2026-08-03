# GNN implementation — build and training report

Everything below was measured on the loaded HI-Small graph, not estimated.

## The data it trained on

| | |
|---|---|
| Transactions ingested | 5,044,322 (of 5,078,345 scanned; 34,023 unparseable) |
| Account nodes | 513,987 |
| `FLOWS_TO` edges | 644,397 (after excluding 365,987 self-loops) |
| `TRANSFER` edges | 5,044,315 |
| Feature columns | 38 (34 live) |
| Ground-truth positives | 3,170 accounts — 0.617% prevalence |
| Typologies | all 8, 100% present in the graph |

Labels are the **IBM AML ground truth**, not the `risk_flags` weak labels. The
weak labels are 100% precise but cover 124 accounts against ground truth's
3,170, and training a model to imitate the cycle detector would inherit its
blind spots — recall on the six typologies with no closed loop is the point.

## What was built

| Module | Purpose |
|---|---|
| `ml/model.py` | GraphSAGE classifier, split into `encode()` / `classify()` |
| `ml/split.py` | Chronological split + train-only feature scaler |
| `ml/train.py` | Training loop, early stopping, threshold selection, caching |
| `ml/sweep.py` | Multi-config comparison over one cached feature set |
| `ml/predict.py` | Inference with written explanations |
| `ml/readiness.py` | 7-check pre-training audit |
| `ml/features.py` | Feature assembly (extended with 7 derived ratios) |
| `ml/losses.py` | Focal Loss + class-balanced alpha |
| `ml/imbalance.py` | SMOTE on embeddings, differentiable |
| `ml/evaluate.py` | Ground-truth metrics, per-typology recall |

## Results, ranked by validation PR-AUC

Selection is on **validation**. Test numbers are reported, never used to
choose — picking on test makes it a second validation set.

| run | features | hidden | val PR-AUC | test PR-AUC | test ROC-AUC |
|---|---|---|---|---|---|
| **v2_derived_h128** | 38 | 128 | **0.2704** | 0.1386 | 0.9605 |
| v2_derived_h64 | 38 | 64 | 0.2666 | 0.1190 | 0.9662 |
| baseline (h64) | 31 | 64 | 0.2410 | 0.1299 | 0.9567 |
| hidden32 | 31 | 32 | 0.2227 | 0.1078 | — |
| diag_random* | 31 | 64 | 0.1352 | 0.1654 | 0.9224 |

\* diagnostic only — a random split breaks chronology and is not a reportable
evaluation.

**Best model: `ml/runs/v2_derived_h128`.** Validation PR-AUC improved 0.2227 →
0.2704 (+21%) across the sweep; the derived features alone contributed
0.2410 → 0.2666 at fixed capacity.

### Best model, held-out test

```
PR-AUC 0.1386   ROC-AUC 0.9605
precision 0.041   recall 0.597   F1 0.077
TP 83   FP 1,938   FN 56   support 139
```

At 0.18% test prevalence a random ranker scores PR-AUC ≈ 0.0018, so 0.1386 is
**77× better than random**. `precision@50` on the full graph is 36% against a
0.617% base rate — a **58× lift**.

### Against the detectors

| | recall |
|---|---|
| GNN | **18.5%** |
| cycle + Louvain detectors | 3.9% |

547 accounts flagged by the GNN alone, missed by every detector, and confirmed
by ground truth.

### Recall by typology

| typology | recall | reachable by cycle detection? |
|---|---|---|
| SCATTER-GATHER | 44.2% | no |
| FAN-OUT | 34.3% | no |
| GATHER-SCATTER | 25.5% | no |
| CYCLE | 21.4% | yes |
| RANDOM | 17.1% | no |
| STACK | 9.0% | no |
| FAN-IN | 7.4% | no |
| BIPARTITE | 6.1% | no |

The model scores **higher on SCATTER-GATHER and FAN-OUT than on CYCLE** — the
typologies with no closed loop, which depth-limited cycle search cannot
represent at any depth. That is the case for the GNN existing.

## Reproducing

```bash
docker compose up -d neo4j redis postgres
python3 -m ml.datasets.run_ingest --max-background none --reset   # ~10 min
python3 -m ml.datasets.run_louvain                                # ~4 min
python3 -m ml.readiness                                           # audit
python3 -m ml.train --cache ml/cache/featureset_v2.npz \
    --hidden 128 --epochs 700 --patience 150
python3 -m ml.predict --run ml/runs/v2_derived_h128 --top 20
```

## Honest limitations

1. **The chronological split separates accounts, not time.** `FLOWS_TO`
   aggregates are incremented on `MERGE` and cannot be rewound, so every
   account arrives carrying its full 18-day lifetime. Not label leakage, but it
   overstates how *early* a mule would be caught. Fixing it means rebuilding
   time-bounded features from `TRANSFER.ts` or the Redis ZSETs.

2. **Test is a different population, not just a later one.** Measured train vs
   test column means: `in_tx_count` 0.03×, `total_out_amount` 0.015×,
   `flow_ratio` 2.58×. Test accounts are near-pure senders with almost no
   incoming activity. Prevalence drops 0.72% → 0.18%.

3. **Precision is low (4%) at the F1-optimal threshold.** Usable for ranking an
   analyst queue — `precision@50` is 36% — not for autonomous action.

4. **17.9% of accounts have no non-self neighbour.** Message passing adds
   nothing for them; a GNN cannot beat a per-node model on that slice.

5. **Only 139 positives in the test split**, so test metrics are noisy.

6. **Full-batch training = one gradient step per epoch.** Mini-batch neighbour
   sampling would give far more steps, but needs `pyg-lib`/`torch-sparse`,
   deliberately excluded as an install trap.

## Worth doing next

1. Time-bounded features, so the temporal evaluation is real (limitation 1).
2. Mini-batch neighbour sampling for many more gradient steps.
3. Report metrics separately for reachable vs unreachable accounts.
4. Train on HI-Medium, evaluate on LI-Small — a true cross-dataset test.
5. GNNExplainer for per-edge attribution in the explanations.
