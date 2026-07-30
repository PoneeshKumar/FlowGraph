"""
Elliptic Bitcoin dataset loader.

WHAT IT IS
----------
203,769 Bitcoin transaction nodes, 234,355 edges, 49 time steps. Labels:
4,545 illicit, 42,019 licit, and ~157,205 unknown — so about 77% of nodes carry
no label at all. It is the standard published benchmark for graph-based
financial crime detection, which makes it useful as an outside check on the
model: if the architecture cannot learn here, the problem is the model rather
than your data.

WHY IT BYPASSES Neo4j / Redis
-----------------------------
Unlike the IBM AML loader, this does NOT write to the graph stores. Elliptic's
166 features are pre-computed and anonymized — the publishers never released
which real quantity each column represents, and there are no account
identities, banks, or amounts to derive features from. There is nothing to
compute a flow_ratio or a 24h volume out of.

So it loads straight into a FeatureSet, the same container ml/features.py
produces. That is the useful seam: FeatureSet carries node_ids, x, y,
labelled_mask, edge_index and feature_names, and nothing about it is specific to
your schema. The model and training loop consume either source unchanged.

Consequence worth being explicit about: a good score here validates the
*architecture*, not your feature pipeline. Only the IBM AML path exercises
FLOWS_TO aggregation, PageRank, Louvain stats and the Redis windows.

FILES
-----
Three CSVs from the Kaggle dataset `ellipticco/elliptic-data-set`:

    elliptic_txs_features.csv   203769 rows, no header: txId, time_step, f1..f165
    elliptic_txs_classes.csv    txId,class   where class is '1' illicit,
                                '2' licit, 'unknown'
    elliptic_txs_edgelist.csv   txId1,txId2

Place them under benchmarks/data/elliptic/.

LABEL CONVENTION
----------------
Elliptic's own encoding is inverted relative to intuition: class '1' means
illicit, '2' means licit. Mapped here onto the project's 4-class risk scale as
critical (3) for illicit and low (0) for licit, with `unknown` left unlabelled
in labelled_mask — never silently folded into 'licit'. 77% of the dataset is
unknown, so treating it as clean would teach the model that most of the graph is
safe on no evidence at all.
"""

import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_ELLIPTIC_DIR = "benchmarks/data/elliptic"

FEATURES_FILE = "elliptic_txs_features.csv"
CLASSES_FILE = "elliptic_txs_classes.csv"
EDGELIST_FILE = "elliptic_txs_edgelist.csv"

# Elliptic's own codes, which are the opposite way round to what you'd guess.
ILLICIT_CODE = "1"
LICIT_CODE = "2"
UNKNOWN_CODE = "unknown"

# Mapped onto ml.features.RISK_LEVEL_TO_CLASS.
ILLICIT_CLASS = 3   # critical
LICIT_CLASS = 0     # low

# The publishers describe the first 94 columns as local (properties of the
# transaction itself) and the remaining 72 as aggregated from one-hop
# neighbours. Column 0 of the feature file is the id and column 1 the time step.
NUM_LOCAL_FEATURES = 93
NUM_AGGREGATE_FEATURES = 72


def _resolve(directory: Union[str, Path]) -> Path:
    path = Path(directory)
    missing = [
        name
        for name in (FEATURES_FILE, CLASSES_FILE, EDGELIST_FILE)
        if not (path / name).exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"Missing {', '.join(missing)} in {path}. Download the Elliptic "
            f"dataset from Kaggle (ellipticco/elliptic-data-set) and extract "
            f"the three CSVs into {path}/."
        )
    return path


def _load_classes(path: Path) -> Dict[str, str]:
    """txId -> raw class code, skipping the header if present."""
    classes: Dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            if row[0].strip().lower() in ("txid", "txid1"):
                continue  # header
            classes[row[0].strip()] = row[1].strip()
    return classes


def load_elliptic(
    directory: Union[str, Path] = DEFAULT_ELLIPTIC_DIR,
    include_unknown: bool = True,
    time_steps: Optional[Tuple[int, int]] = None,
):
    """Load Elliptic into a FeatureSet.

    Args:
        directory:       Folder holding the three CSVs.
        include_unknown: Keep unlabelled nodes in the graph. Default True and
                         recommended: they carry no label but they do carry
                         edges, and message passing needs that structure even
                         for nodes you never compute loss on. Set False to drop
                         them entirely (a smaller, denser-labelled graph).
        time_steps:      Inclusive (first, last) time-step filter, e.g. (1, 34)
                         for the usual train split. Elliptic's 49 steps are the
                         natural axis for a time-based split — use it rather
                         than a random one.

    Returns:
        ml.features.FeatureSet with y in {0, 3} and labelled_mask marking the
        nodes Elliptic actually labelled.
    """
    from ml.features import FeatureSet

    path = _resolve(directory)
    classes = _load_classes(path / CLASSES_FILE)

    node_ids: List[str] = []
    index_of: Dict[str, int] = {}
    feature_rows: List[List[float]] = []
    labels: List[int] = []
    labelled: List[bool] = []
    num_features: Optional[int] = None

    with open(path / FEATURES_FILE, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            tx_id = row[0].strip()
            # The published file has no header, but be tolerant of one.
            if tx_id.lower() in ("txid", "id"):
                continue

            try:
                values = [float(v) for v in row[1:]]
            except ValueError:
                logger.warning("Skipping unparseable feature row for %s", tx_id)
                continue

            if num_features is None:
                num_features = len(values)
                expected = 1 + NUM_LOCAL_FEATURES + NUM_AGGREGATE_FEATURES
                if num_features != expected:
                    logger.warning(
                        "Expected %d feature columns (time_step + %d local + %d "
                        "aggregate), found %d — column names will be generic",
                        expected, NUM_LOCAL_FEATURES, NUM_AGGREGATE_FEATURES,
                        num_features,
                    )
            elif len(values) != num_features:
                logger.warning(
                    "Skipping %s: %d feature columns, expected %d",
                    tx_id, len(values), num_features,
                )
                continue

            # Column 0 of the remainder is the time step.
            step = int(values[0])
            if time_steps is not None and not (time_steps[0] <= step <= time_steps[1]):
                continue

            code = classes.get(tx_id, UNKNOWN_CODE)
            is_labelled = code in (ILLICIT_CODE, LICIT_CODE)
            if not is_labelled and not include_unknown:
                continue

            index_of[tx_id] = len(node_ids)
            node_ids.append(tx_id)
            feature_rows.append(values)
            labels.append(ILLICIT_CLASS if code == ILLICIT_CODE else LICIT_CLASS)
            labelled.append(is_labelled)

    if not node_ids:
        raise ValueError(
            "No Elliptic nodes loaded — check the files and any time_steps filter"
        )

    x = np.asarray(feature_rows, dtype=np.float32)

    sources: List[int] = []
    targets: List[int] = []
    with open(path / EDGELIST_FILE, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            source_id, target_id = row[0].strip(), row[1].strip()
            if source_id.lower() in ("txid1", "txid"):
                continue  # header
            source = index_of.get(source_id)
            target = index_of.get(target_id)
            # Edges to filtered-out nodes are dropped, not remapped.
            if source is None or target is None:
                continue
            sources.append(source)
            targets.append(target)

    if sources:
        edge_index = np.vstack([
            np.asarray(sources, dtype=np.int64),
            np.asarray(targets, dtype=np.int64),
        ])
    else:
        edge_index = np.zeros((2, 0), dtype=np.int64)

    # Elliptic edges carry no amount, so weights are uniform. Kept for shape
    # compatibility with the FLOWS_TO path, which does weight by total_amount.
    edge_weight = np.ones(edge_index.shape[1], dtype=np.float32)

    feature_names = ["time_step"]
    remaining = (num_features or 1) - 1
    local = min(NUM_LOCAL_FEATURES, remaining)
    feature_names += [f"local_{i + 1}" for i in range(local)]
    feature_names += [f"aggregate_{i + 1}" for i in range(remaining - local)]

    y = np.asarray(labels, dtype=np.int64)
    labelled_mask = np.asarray(labelled, dtype=bool)

    logger.info(
        "Elliptic loaded: %d nodes, %d edges, %d features | "
        "illicit=%d licit=%d unknown=%d",
        len(node_ids), edge_index.shape[1], x.shape[1],
        int(((y == ILLICIT_CLASS) & labelled_mask).sum()),
        int(((y == LICIT_CLASS) & labelled_mask).sum()),
        int((~labelled_mask).sum()),
    )

    return FeatureSet(
        node_ids=node_ids,
        x=x,
        y=y,
        labelled_mask=labelled_mask,
        edge_index=edge_index,
        edge_weight=edge_weight,
        feature_names=feature_names,
    )


def time_step_split(
    feature_set,
    train_last_step: int = 34,
) -> Tuple[np.ndarray, np.ndarray]:
    """Boolean train/test masks split on Elliptic's time_step column.

    Split by time, never randomly. A random split lets the model learn from the
    future to predict the past, which inflates the score and does not survive
    production. 34 is the split used in the original paper (steps 1-34 train,
    35-49 test), so results stay comparable to published numbers.

    Returns:
        (train_mask, test_mask), both restricted to labelled nodes.
    """
    try:
        step_column = feature_set.feature_names.index("time_step")
    except ValueError as exc:
        raise ValueError("feature set has no time_step column") from exc

    steps = feature_set.x[:, step_column]
    labelled = feature_set.labelled_mask

    train_mask = (steps <= train_last_step) & labelled
    test_mask = (steps > train_last_step) & labelled
    return train_mask, test_mask
