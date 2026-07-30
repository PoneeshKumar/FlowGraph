"""
Training-readiness audit for the loaded graph.

Answers one question with evidence rather than opinion: can a GNN actually be
trained on what is in Neo4j + Redis + Postgres right now? Every number is
measured against the live stores.

    python3 -m ml.readiness
    python3 -m ml.readiness --skip-dry-run

Checks, in dependency order:

  1. volume     — node/edge counts and feature width
  2. integrity  — NaN/inf, all-zero columns, zero-variance columns
  3. scale      — per-column magnitude spread; decides whether feature
                  normalization is mandatory before training
  4. structure  — isolated nodes, self-loops, duplicate edges, degree spread
  5. labels     — ground-truth coverage, weak-label coverage, and whether the
                  two actually agree with each other
  6. time split — whether a chronological split is possible at all, and whether
                  both sides would carry positives
  7. dry run    — a real 2-layer SAGEConv forward pass + Focal Loss + backward
                  at full scale, run twice (raw features and standardized) so
                  the effect of scale is measured rather than assumed

Each check reports PASS, WARN or FAIL. FAIL means training cannot proceed
meaningfully; WARN means it will run but something is being wasted or risked.
"""

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("readiness")

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

# Above this ratio between the largest and smallest column magnitude, the big
# columns dominate every gradient and training on raw features is pointless.
SCALE_RATIO_LIMIT = 1e4

# Minimum positives wanted on each side of a chronological split. Below this the
# test-side metrics are too noisy to mean anything.
MIN_POSITIVES_PER_SPLIT = 50


@dataclass
class Check:
    """One readiness check and what it found."""

    name: str
    status: str
    headline: str
    lines: List[str] = field(default_factory=list)


class Report:
    def __init__(self) -> None:
        self.checks: List[Check] = []

    def add(
        self, name: str, status: str, headline: str, lines: Optional[List[str]] = None
    ) -> None:
        self.checks.append(Check(name, status, headline, lines or []))

    def render(self) -> str:
        width = 74
        out: List[str] = ["", "=" * width, "TRAINING READINESS", "=" * width]
        for check in self.checks:
            out.append("")
            out.append(f"[{check.status}] {check.name} — {check.headline}")
            for line in check.lines:
                out.append(f"       {line}")

        failures = [c for c in self.checks if c.status == FAIL]
        warnings = [c for c in self.checks if c.status == WARN]

        out += ["", "=" * width]
        if failures:
            out.append(f"VERDICT: NOT READY — {len(failures)} blocking issue(s)")
            for check in failures:
                out.append(f"  BLOCKER: {check.name} — {check.headline}")
        elif warnings:
            out.append(
                f"VERDICT: READY, with {len(warnings)} caveat(s) to handle in the "
                f"training loop"
            )
        else:
            out.append("VERDICT: READY")
        for check in warnings:
            out.append(f"  CAVEAT: {check.name} — {check.headline}")
        out.append("=" * width)
        return "\n".join(out)


def _fmt(value: float) -> str:
    return f"{value:,.4g}"


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_volume(report: Report, feature_set: Any, store_counts: Dict[str, Any]) -> None:
    nodes = feature_set.num_nodes
    edges = feature_set.edge_index.shape[1]

    lines = [
        f"accounts (nodes)     : {nodes:,}",
        f"FLOWS_TO (edges)     : {edges:,}",
        f"feature width        : {feature_set.num_features}",
    ]
    for label, value in store_counts.items():
        lines.append(f"{label:21s}: {value:,}" if isinstance(value, int) else
                     f"{label:21s}: {value}")

    if nodes == 0 or edges == 0:
        report.add("volume", FAIL, "graph is empty — nothing to train on", lines)
        return
    if nodes < 1_000:
        report.add(
            "volume", WARN, f"only {nodes:,} nodes — very small for a GNN", lines
        )
        return
    report.add("volume", PASS, f"{nodes:,} nodes / {edges:,} edges", lines)


def check_integrity(report: Report, feature_set: Any) -> None:
    x = feature_set.x
    names = feature_set.feature_names

    nonfinite = int((~np.isfinite(x)).sum())
    dead = [names[i] for i in range(x.shape[1]) if not np.any(x[:, i])]
    # Zero variance is the broader failure: a column that is constant at any
    # value teaches the model nothing, not only one that is constant at zero.
    variances = x.var(axis=0)
    constant = [
        names[i] for i in range(x.shape[1])
        if variances[i] == 0.0 and names[i] not in dead
    ]
    live = x.shape[1] - len(dead) - len(constant)

    lines = [
        f"non-finite cells     : {nonfinite}",
        f"live columns         : {live} of {x.shape[1]}",
        f"all-zero columns     : {len(dead)}",
        f"constant (non-zero)  : {len(constant)}",
    ]
    if dead:
        lines.append("dead: " + ", ".join(dead))
    if constant:
        lines.append("constant: " + ", ".join(constant))

    if nonfinite:
        report.add(
            "integrity", FAIL,
            f"{nonfinite} non-finite cells would poison training", lines,
        )
        return
    if live == 0:
        report.add("integrity", FAIL, "no informative columns at all", lines)
        return
    if dead or constant:
        report.add(
            "integrity", WARN,
            f"{len(dead) + len(constant)} of {x.shape[1]} columns carry no signal",
            lines,
        )
        return
    report.add("integrity", PASS, f"all {x.shape[1]} columns informative", lines)


def check_scale(report: Report, feature_set: Any) -> None:
    x = feature_set.x
    names = feature_set.feature_names

    magnitudes = np.abs(x).max(axis=0)
    live = magnitudes > 0
    if not live.any():
        report.add("scale", FAIL, "every column is zero", [])
        return

    largest_idx = int(np.argmax(magnitudes))
    smallest_idx = int(np.argmin(np.where(live, magnitudes, np.inf)))
    ratio = float(magnitudes[largest_idx] / magnitudes[smallest_idx])

    ranked = np.argsort(-magnitudes)[:4]
    lines = [
        f"largest column       : {names[largest_idx]} "
        f"(max {_fmt(float(magnitudes[largest_idx]))})",
        f"smallest live column : {names[smallest_idx]} "
        f"(max {_fmt(float(magnitudes[smallest_idx]))})",
        f"magnitude spread     : {ratio:.3g}x",
        "top magnitudes: "
        + ", ".join(f"{names[i]}={_fmt(float(magnitudes[i]))}" for i in ranked),
    ]

    # float32 keeps ~7 significant digits, so values this large lose precision
    # in the amount columns even before gradients are considered.
    if float(magnitudes[largest_idx]) > 1e7:
        lines.append(
            f"note: {names[largest_idx]} exceeds float32's ~7 significant digits"
        )

    if ratio > SCALE_RATIO_LIMIT:
        report.add(
            "scale", WARN,
            f"{ratio:.3g}x spread — normalization is mandatory, not optional",
            lines + [
                "fix: StandardScaler (or log1p on the amount columns) fitted on "
                "TRAIN nodes only, then applied to val/test",
            ],
        )
        return
    report.add("scale", PASS, f"{ratio:.3g}x spread is trainable as-is", lines)


def check_structure(report: Report, feature_set: Any) -> None:
    edge_index = feature_set.edge_index
    num_nodes = feature_set.num_nodes
    sources, targets = edge_index[0], edge_index[1]

    out_degree = np.bincount(sources, minlength=num_nodes)
    in_degree = np.bincount(targets, minlength=num_nodes)
    self_loops = int(np.sum(sources == targets))

    # Self-loops must be excluded before judging connectivity. A self-loop gives
    # a node in-degree 1 AND out-degree 1, so counting raw degree reports a node
    # that only pays itself as "connected" when message passing can reach
    # nothing from it. This distinction is not academic on IBM AML: 11.6% of
    # source rows are same-account transfers.
    non_self = sources != targets
    real_out = np.bincount(sources[non_self], minlength=num_nodes)
    real_in = np.bincount(targets[non_self], minlength=num_nodes)
    unreachable = int(np.sum((real_out == 0) & (real_in == 0)))

    # A duplicate directed pair should be impossible: FLOWS_TO is MERGEd on the
    # pair, so two rows for one pair means the aggregate was split.
    pair_keys = sources.astype(np.int64) * num_nodes + targets.astype(np.int64)
    duplicates = int(len(pair_keys) - len(np.unique(pair_keys)))

    real_degree = real_out + real_in
    lines = [
        f"self-loops           : {self_loops:,} of {len(sources):,} edges "
        f"({self_loops / max(len(sources), 1):.1%})",
        f"no non-self neighbour: {unreachable:,} ({unreachable / num_nodes:.2%}) "
        f"— message passing cannot reach anything for these",
        f"duplicate pairs      : {duplicates:,}",
        f"real degree mean/med : {real_degree.mean():.2f} / "
        f"{np.median(real_degree):.0f}",
        f"real degree p99/max  : {np.percentile(real_degree, 99):.0f} / "
        f"{real_degree.max():,}",
        f"zero real in-degree  : {int(np.sum(real_in == 0)):,}",
        f"zero real out-degree : {int(np.sum(real_out == 0)):,}",
    ]

    if duplicates:
        report.add(
            "structure", FAIL,
            f"{duplicates:,} duplicate directed pairs — FLOWS_TO aggregates split",
            lines,
        )
        return
    if unreachable > num_nodes * 0.5:
        report.add(
            "structure", FAIL,
            f"{unreachable / num_nodes:.0%} of nodes have no non-self neighbour — "
            f"a GNN cannot do better than a per-node model on most of the graph",
            lines,
        )
        return
    if unreachable or self_loops:
        advice = []
        if self_loops:
            advice.append(
                "drop the self-loop edges: SAGEConv already applies its own root "
                "weight, so a self-loop makes a node aggregate itself twice, and "
                "it inflates degree/volume features"
            )
        if unreachable:
            advice.append(
                "the unreachable nodes only ever transacted with themselves, so "
                "message passing adds nothing for them — they are trainable but "
                "the GNN cannot beat a per-node model on that slice; consider "
                "reporting metrics separately for reachable vs unreachable nodes"
            )
        headline = (
            f"{self_loops:,} self-loops and {unreachable:,} nodes "
            f"({unreachable / num_nodes:.1%}) with no non-self neighbour"
            if self_loops
            else f"no self-loops, but {unreachable:,} nodes "
                 f"({unreachable / num_nodes:.1%}) have no non-self neighbour"
        )
        report.add("structure", WARN, headline, lines + advice)
        return
    report.add(
        "structure", PASS,
        f"fully connected: no self-loops, no unreachable nodes",
        lines,
    )


def check_labels(
    report: Report, feature_set: Any, ground_truth: Optional[Any]
) -> Dict[str, Any]:
    """Compare the two candidate label sources. Returns what the split needs."""
    weak_labelled = int(feature_set.labelled_mask.sum())
    num_nodes = feature_set.num_nodes

    lines = [
        f"weak labels (CYCLE)  : {weak_labelled:,} accounts "
        f"({weak_labelled / num_nodes:.4%})",
    ]

    truth_mask = None
    if ground_truth is not None:
        truth_mask = ground_truth.labels_for(feature_set.node_ids)
        positives = int(truth_mask.sum())
        lines.append(
            f"ground truth (IBM)   : {positives:,} accounts "
            f"({positives / num_nodes:.4%})"
        )

        # Do the weak labels even describe this graph? Stale flags from an
        # earlier load would show near-zero overlap.
        if weak_labelled:
            overlap = int(np.sum(feature_set.labelled_mask & truth_mask))
            lines.append(
                f"weak-label agreement : {overlap:,} of {weak_labelled:,} "
                f"weak-labelled accounts are in ground truth "
                f"({overlap / weak_labelled:.1%})"
            )

        # Positives with no edges are the ones a GNN cannot help with: message
        # passing has no neighbourhood to aggregate, so they fall back to their
        # own features and the architecture buys nothing.
        if positives:
            edge_index = feature_set.edge_index
            degree = np.bincount(
                edge_index[0], minlength=num_nodes
            ) + np.bincount(edge_index[1], minlength=num_nodes)
            positive_degree = degree[truth_mask]
            isolated_positives = int(np.sum(positive_degree == 0))
            lines.append(
                f"positive degree      : mean {positive_degree.mean():.1f}, "
                f"median {np.median(positive_degree):.0f}, "
                f"{isolated_positives:,} isolated "
                f"({isolated_positives / positives:.1%})"
            )
            lines.append(
                f"  (all nodes for comparison: mean {degree.mean():.1f})"
            )

        for typology, stats in _typology_counts(ground_truth, feature_set).items():
            lines.append(
                f"  {typology:16s} {stats['in_graph']:>6,} of "
                f"{stats['total']:>6,} accounts present"
            )

    if ground_truth is None and weak_labelled == 0:
        report.add("labels", FAIL, "no labels from any source", lines)
        return {"truth_mask": None}

    if ground_truth is not None:
        positives = int(truth_mask.sum())
        if positives == 0:
            report.add(
                "labels", FAIL,
                "ground truth loaded but none of its accounts are in the graph",
                lines,
            )
            return {"truth_mask": truth_mask}
        if weak_labelled < positives / 10:
            report.add(
                "labels", WARN,
                f"ground truth has {positives:,} positives vs {weak_labelled:,} "
                f"weak labels — train on ground truth, keep detectors as features",
                lines,
            )
            return {"truth_mask": truth_mask}
        report.add(
            "labels", PASS,
            f"{positives:,} ground-truth positives available",
            lines,
        )
        return {"truth_mask": truth_mask}

    report.add(
        "labels", WARN,
        f"only weak labels available ({weak_labelled:,}) — cannot measure true "
        f"accuracy",
        lines,
    )
    return {"truth_mask": None}


def _typology_counts(ground_truth: Any, feature_set: Any) -> Dict[str, Dict[str, int]]:
    from ml.evaluate import ALL_TYPOLOGIES

    result: Dict[str, Dict[str, int]] = {}
    for typology in ALL_TYPOLOGIES:
        accounts = ground_truth.accounts_by_typology.get(typology, set())
        if not accounts:
            continue
        present = int(
            ground_truth.typology_labels_for(feature_set.node_ids, typology).sum()
        )
        result[typology] = {"in_graph": present, "total": len(accounts)}
    return result


def check_time_split(
    report: Report,
    feature_set: Any,
    node_first_ts: Optional[Dict[str, int]],
    truth_mask: Optional[np.ndarray],
) -> None:
    """Can the data be split chronologically, and would both sides have positives?

    Splitting payment data randomly lets the model learn from the future to
    predict the past, so a chronological split is not a nicety. It needs a
    per-node timestamp, and the FeatureSet does not currently carry one.
    """
    has_time_feature = any(
        name in ("time_step", "first_active_ts", "account_age_days")
        and np.any(feature_set.x[:, i])
        for i, name in enumerate(feature_set.feature_names)
    )

    lines = [
        f"time column in FeatureSet: {'yes' if has_time_feature else 'NO'}",
    ]

    if not node_first_ts:
        report.add(
            "time split", FAIL,
            "no per-node timestamp available — cannot split chronologically",
            lines + [
                "FLOWS_TO carries first_ts/last_ts, but neither export surfaces "
                "them per node, and account_age_days is derived from "
                "Account.created_at (set at ingest time, not account age)",
            ],
        )
        return

    times = np.array(
        [node_first_ts.get(node_id, 0) for node_id in feature_set.node_ids],
        dtype=np.int64,
    )
    known = times > 0
    lines.append(
        f"nodes with a timestamp   : {int(known.sum()):,} of "
        f"{feature_set.num_nodes:,} ({known.mean():.1%})"
    )

    if not known.any():
        report.add("time split", FAIL, "no usable node timestamps", lines)
        return

    from datetime import datetime, timezone

    span_start = datetime.fromtimestamp(int(times[known].min()), tz=timezone.utc)
    span_end = datetime.fromtimestamp(int(times[known].max()), tz=timezone.utc)
    lines.append(f"activity span            : {span_start:%Y-%m-%d} .. {span_end:%Y-%m-%d}")

    # 70/30 chronological split on first activity.
    cutoff = float(np.percentile(times[known], 70))
    cutoff_dt = datetime.fromtimestamp(int(cutoff), tz=timezone.utc)
    train = known & (times <= cutoff)
    test = known & (times > cutoff)
    lines.append(
        f"70/30 cutoff             : {cutoff_dt:%Y-%m-%d %H:%M} UTC "
        f"-> train {int(train.sum()):,} / test {int(test.sum()):,}"
    )

    if truth_mask is None:
        report.add(
            "time split", WARN,
            "chronological split possible, but no ground truth to check positives",
            lines,
        )
        return

    train_positives = int(np.sum(train & truth_mask))
    test_positives = int(np.sum(test & truth_mask))
    lines.append(
        f"positives per side       : train {train_positives:,} / "
        f"test {test_positives:,}"
    )

    if min(train_positives, test_positives) < MIN_POSITIVES_PER_SPLIT:
        report.add(
            "time split", WARN,
            f"only {min(train_positives, test_positives)} positives on the "
            f"smaller side — test metrics will be noisy",
            lines,
        )
        return

    # The split partitions ACCOUNTS by when they first appear. It does not
    # separate the feature VALUES in time, because FLOWS_TO aggregates
    # (tx_count, total_amount, min/max) are incremented on every MERGE and so
    # span the whole dataset no matter where the cutoff falls. Every account
    # therefore arrives carrying its complete lifetime, including activity after
    # the cutoff. That is not label leakage — the label is not in the features —
    # but it does overstate how EARLY the model would catch a mule in
    # production, where you only ever have history-to-date.
    report.add(
        "time split", WARN,
        f"split works ({train_positives:,}/{test_positives:,} positives) but "
        f"features are cumulative over the full span, so it separates accounts, "
        f"not time",
        lines + [
            "features aggregate all 18 days regardless of the cutoff: FLOWS_TO "
            "aggregates are incremented on MERGE and cannot be rewound",
            "to evaluate honestly, rebuild time-bounded features from TRANSFER.ts "
            "or the Redis ZSETs (both keep per-transaction time), then train on "
            "as-of-T features and test on a later window",
        ],
    )


def check_dry_run(
    report: Report, feature_set: Any, truth_mask: Optional[np.ndarray]
) -> None:
    """Run the real thing once, at full scale, and see whether it survives.

    Runs twice — raw features and standardized — because that difference is the
    evidence for or against the scale warning.
    """
    try:
        import torch
        from torch_geometric.nn import SAGEConv
    except ImportError as exc:
        report.add("dry run", FAIL, f"ML stack unavailable: {exc}", [])
        return

    from ml.losses import FocalLoss, class_balanced_alpha

    x_raw = torch.from_numpy(feature_set.x)
    edge_index = torch.from_numpy(feature_set.edge_index)

    if truth_mask is not None and truth_mask.any():
        labels = np.where(truth_mask, 3, 0).astype(np.int64)
        label_source = "IBM ground truth (laundering -> critical)"
    else:
        labels = feature_set.y
        label_source = "weak labels from risk_flags"
    y = torch.from_numpy(labels)

    lines = [f"labels used          : {label_source}"]

    def one_pass(x: "torch.Tensor", tag: str) -> Tuple[float, float]:
        torch.manual_seed(0)
        conv1 = SAGEConv(feature_set.num_features, 64)
        conv2 = SAGEConv(64, 4)
        alpha = class_balanced_alpha(y, num_classes=4)
        criterion = FocalLoss(gamma=2.0, alpha=alpha)

        started = time.monotonic()
        hidden = conv1(x, edge_index).relu()
        logits = conv2(hidden, edge_index)
        loss = criterion(logits, y)
        loss.backward()
        elapsed = time.monotonic() - started

        grad_norm = float(
            torch.cat([
                p.grad.flatten() for p in list(conv1.parameters()) + list(conv2.parameters())
                if p.grad is not None
            ]).norm()
        )
        lines.append(
            f"{tag:20s}: loss={loss.item():.6g} grad_norm={grad_norm:.6g} "
            f"({elapsed:.1f}s)"
        )
        return loss.item(), grad_norm

    raw_loss, raw_grad = one_pass(x_raw, "raw features")

    # Standardize. Fitted on everything here because this is a numerical probe,
    # NOT a trained model — in the real loop this must be fitted on train only.
    mean = feature_set.x.mean(axis=0, keepdims=True)
    std = feature_set.x.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    x_scaled = torch.from_numpy(((feature_set.x - mean) / std).astype(np.float32))
    scaled_loss, scaled_grad = one_pass(x_scaled, "standardized")

    healthy = np.isfinite([raw_loss, raw_grad, scaled_loss, scaled_grad]).all()
    if not healthy:
        which = "raw" if not np.isfinite([raw_loss, raw_grad]).all() else "standardized"
        report.add(
            "dry run", FAIL,
            f"forward/backward produced non-finite values on {which} features",
            lines,
        )
        return

    if raw_grad > scaled_grad * 100 or raw_grad == 0.0:
        report.add(
            "dry run", WARN,
            f"gradients on raw features are unusable "
            f"({raw_grad:.3g} vs {scaled_grad:.3g} standardized) — normalize first",
            lines,
        )
        return
    report.add(
        "dry run", PASS,
        f"full-scale forward+backward works (loss {scaled_loss:.4g})",
        lines,
    )


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


async def _node_first_activity(
    neo4j_client: Any, timeout_seconds: float
) -> Dict[str, int]:
    """Per-account earliest FLOWS_TO activity, as unix seconds.

    Aggregated from the edge list rather than with a per-node OPTIONAL MATCH:
    scanning 1M relationships once and grouping in pandas is far more
    predictable than 514k subqueries.
    """
    from neo4j import Query

    from config import NEO4J_DATABASE

    query = """
    MATCH (a:Account)-[f:FLOWS_TO]->(b:Account)
    RETURN a.id AS src, b.id AS dst, f.first_ts AS first_ts
    """
    rows: List[Tuple[str, str, Any]] = []
    async with neo4j_client.driver.session(database=NEO4J_DATABASE) as session:
        result = await session.run(Query(query, timeout=timeout_seconds))
        async for record in result:
            rows.append((record["src"], record["dst"], record["first_ts"]))

    if not rows:
        return {}

    frame = pd.DataFrame(rows, columns=["src", "dst", "first_ts"])
    frame["first_ts"] = pd.to_numeric(frame["first_ts"], errors="coerce")
    frame = frame.dropna(subset=["first_ts"])

    # An account's first activity is the earliest edge it touches, either side.
    stacked = pd.concat([
        frame[["src", "first_ts"]].rename(columns={"src": "account"}),
        frame[["dst", "first_ts"]].rename(columns={"dst": "account"}),
    ])
    earliest = stacked.groupby("account", sort=False)["first_ts"].min()
    return {str(k): int(v) for k, v in earliest.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-days", type=int, default=2_000)
    parser.add_argument("--anchor-percentile", type=float, default=0.999)
    parser.add_argument(
        "--patterns", default="benchmarks/data/HI-Small_Patterns.txt"
    )
    parser.add_argument("--export-timeout", type=float, default=1800.0)
    parser.add_argument("--skip-dry-run", action="store_true")
    parser.add_argument("--skip-time-split", action="store_true")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    from datetime import datetime, timezone
    from pathlib import Path

    from db.neo4j import Neo4jClient
    from db.redis import RedisClient
    from ml.features import FeatureBuilder

    neo4j_client = Neo4jClient()
    await neo4j_client.initialize()
    redis_client = RedisClient()
    await redis_client.initialize()

    postgres_client = None
    try:
        from db.postgres import PostgresClient

        postgres_client = PostgresClient()
        await postgres_client.initialize()
    except Exception as exc:
        logger.warning("Postgres unavailable (%s) — weak labels will be empty", exc)
        postgres_client = None

    report = Report()
    try:
        store_counts: Dict[str, Any] = {}
        from config import NEO4J_DATABASE

        async with neo4j_client.driver.session(database=NEO4J_DATABASE) as session:
            # Both use the count store, so they are cheap even at 5M edges.
            result = await session.run("MATCH ()-[t:TRANSFER]->() RETURN count(t) AS n")
            record = await result.single()
            store_counts["TRANSFER edges"] = int(record["n"]) if record else 0
        store_counts["Redis edge keys"] = int(await redis_client.client.dbsize())

        anchor = await neo4j_client.get_flows_to_timestamp(
            percentile=args.anchor_percentile
        )
        reference = (
            datetime.fromtimestamp(anchor, tz=timezone.utc) if anchor else None
        )
        logger.info(
            "Anchoring windows at %s",
            reference.isoformat() if reference else "now (no timestamps found)",
        )

        logger.info("Building feature set (Redis scan is the slow part) …")
        builder = FeatureBuilder(neo4j_client, redis_client, postgres_client)
        feature_set = await builder.build(
            window_days=args.window_days,
            reference_time=reference,
            export_timeout_seconds=args.export_timeout,
        )

        ground_truth = None
        if Path(args.patterns).exists():
            from ml.evaluate import load_ground_truth

            ground_truth = load_ground_truth(args.patterns)
        else:
            logger.warning("Ground truth not found at %s", args.patterns)

        node_first_ts: Dict[str, int] = {}
        if not args.skip_time_split:
            logger.info("Reading per-node first activity …")
            node_first_ts = await _node_first_activity(
                neo4j_client, args.export_timeout
            )

        check_volume(report, feature_set, store_counts)
        check_integrity(report, feature_set)
        check_scale(report, feature_set)
        check_structure(report, feature_set)
        label_info = check_labels(report, feature_set, ground_truth)
        if not args.skip_time_split:
            check_time_split(
                report, feature_set, node_first_ts, label_info.get("truth_mask")
            )
        if not args.skip_dry_run:
            logger.info("Running full-scale forward/backward dry run …")
            check_dry_run(report, feature_set, label_info.get("truth_mask"))

        print(report.render())
        return 1 if any(c.status == FAIL for c in report.checks) else 0
    finally:
        await neo4j_client.close()
        await redis_client.close()
        if postgres_client is not None:
            await postgres_client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
