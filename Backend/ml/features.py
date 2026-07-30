"""
Feature assembly: live graph -> GNN training tensors.

Reads the three stores and emits one feature matrix plus an edge list:

  Neo4j     account nodes (pagerank_score, community_id, created_at, labels)
            and the aggregate FLOWS_TO edge list
  Redis     1h / 24h / 7d in/out volume and txn counts, one bulk scan
  Postgres  risk_flags, used as weak labels

Every feature here is derived from data the pipeline actually writes. The
account properties CLAUDE.md describes (kyc_tier, country, risk_score,
account_age, cumulative_volume) are NOT included: create_account_node is
their only writer and nothing in production calls it, so they are null on
every node. See OPTIONAL_NODE_PROPERTY_FEATURES to switch them on once
ingestion populates them.

Graph algorithms feed this layer rather than deciding anything: PageRank and
Louvain become columns, cycle/community flags become weak labels.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# risk_level -> class index. Ordered, and the order is meaningful: the model
# predicts one of four escalating levels.
RISK_LEVEL_TO_CLASS: Dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}
NUM_CLASSES = len(RISK_LEVEL_TO_CLASS)

# The four node types from the CLAUDE.md schema, in fixed order. Column order
# is part of the trained model's contract — never reorder these.
NODE_TYPES: Tuple[str, ...] = ("Account", "Merchant", "Bank", "Exchange")

# Structural features derived from the aggregate FLOWS_TO edges.
GRAPH_FEATURES: Tuple[str, ...] = (
    "out_degree",          # distinct accounts paid
    "in_degree",           # distinct accounts paid by
    "total_out_amount",
    "total_in_amount",
    "out_tx_count",
    "in_tx_count",
    "net_flow",            # in - out; near zero for a pass-through mule
    "flow_ratio",          # out / (in + out); ~0.5 means it forwards all it gets
    "pagerank_score",
    "account_age_days",
)

# Louvain output, as derived stats rather than the community_id itself. The id
# is fingerprint[:12] — a hex hash with no numeric meaning, and every re-run
# reshuffles it, so a model keyed on it would learn noise. These three stay
# valid for communities the model has never seen.
COMMUNITY_FEATURES: Tuple[str, ...] = (
    "community_size",
    "community_risk_score",
    "community_flagged_members",
)

# Currently always Account — the consumer hardcodes `MERGE (src:Account ...)`,
# so the other three columns are constant zero until something creates them.
# Kept for a stable feature width as the schema fills in.
NODE_TYPE_FEATURES: Tuple[str, ...] = tuple(f"is_{t.lower()}" for t in NODE_TYPES)

# Node properties that exist in the schema docs but that nothing writes yet.
# To enable: add the property name here once ingestion populates it. The
# builder reads it straight from export_account_nodes — no other change.
OPTIONAL_NODE_PROPERTY_FEATURES: Tuple[str, ...] = ()


@dataclass
class FeatureSet:
    """Assembled features, still framework-agnostic (numpy, not torch)."""

    node_ids: List[str]
    x: np.ndarray                  # [num_nodes, num_features] float32
    y: np.ndarray                  # [num_nodes] int64, class index
    labelled_mask: np.ndarray      # [num_nodes] bool — has a real risk_flag
    edge_index: np.ndarray         # [2, num_edges] int64
    edge_weight: np.ndarray        # [num_edges] float32
    feature_names: List[str] = field(default_factory=list)

    @property
    def num_nodes(self) -> int:
        return len(self.node_ids)

    @property
    def num_features(self) -> int:
        return self.x.shape[1]

    def to_pyg(self):
        """Convert to a torch_geometric Data object.

        torch is imported here rather than at module scope so the numpy half
        of this module stays usable without requirements-ml.txt installed.
        """
        import torch
        from torch_geometric.data import Data

        data = Data(
            x=torch.from_numpy(self.x),
            edge_index=torch.from_numpy(self.edge_index),
            edge_weight=torch.from_numpy(self.edge_weight),
            y=torch.from_numpy(self.y),
        )
        data.labelled_mask = torch.from_numpy(self.labelled_mask)
        data.node_ids = self.node_ids
        data.feature_names = self.feature_names
        return data


class FeatureBuilder:
    """Assembles a FeatureSet from the live stores.

    postgres_client is optional: without it every account comes back
    unlabelled, which is the right shape for inference.
    """

    def __init__(
        self,
        neo4j_client: Any,
        redis_client: Optional[Any] = None,
        postgres_client: Optional[Any] = None,
    ) -> None:
        self.neo4j = neo4j_client
        self.redis = redis_client
        self.postgres = postgres_client

    async def build(
        self,
        window_days: int = 30,
        windows_hours: Sequence[int] = (1, 24, 168),
        reference_time: Optional[datetime] = None,
        flag_limit: int = 100_000,
    ) -> FeatureSet:
        """Pull from every store and assemble the graph.

        Args:
            window_days:    FLOWS_TO edges active within this many days.
            windows_hours:  Redis window widths for volume features.
            reference_time: Anchor for all time maths; defaults to now (UTC).
                            Benchmarks anchor to the dataset's own max ts so
                            historical data isn't filtered out as stale.
            flag_limit:     Cap on risk_flags read for labels. The default is
                            deliberately far above get_risk_flags' own 100, so
                            labels are not silently truncated.
        """
        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)

        nodes = await self.neo4j.export_account_nodes()
        edges = await self.neo4j.export_flows_to_edges(
            window_days=window_days, reference_time=ref
        )

        volumes: Dict[str, Dict[str, float]] = {}
        if self.redis is not None:
            volumes = await self.redis.get_all_account_volumes(
                windows_hours=tuple(windows_hours), reference_time=ref
            )

        flags: List[Dict[str, Any]] = []
        if self.postgres is not None:
            flags = await self.postgres.get_risk_flags(
                status="open", limit=flag_limit
            )

        return self._assemble(nodes, edges, volumes, flags, windows_hours, ref)

    def _assemble(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        volumes: Dict[str, Dict[str, float]],
        flags: List[Dict[str, Any]],
        windows_hours: Sequence[int],
        ref: datetime,
    ) -> FeatureSet:
        """Pure assembly step — no I/O, so it is directly unit-testable."""
        from db.redis import _window_label  # shared naming, single source

        node_ids = [n["id"] for n in nodes if n.get("id")]

        # Edge endpoints should always exist as nodes, but a partially-synced
        # graph could disagree. Adding them beats dropping edges silently.
        edge_endpoints = {e["src"] for e in edges} | {e["dst"] for e in edges}
        orphans = sorted(edge_endpoints - set(node_ids))
        if orphans:
            logger.warning(
                f"{len(orphans)} FLOWS_TO endpoints missing from the node "
                f"export; adding them with zero-valued node properties"
            )
            node_ids.extend(orphans)

        if not node_ids:
            return self._empty_feature_set(windows_hours)

        index_of = {account: i for i, account in enumerate(node_ids)}
        num_nodes = len(node_ids)

        nodes_df = pd.DataFrame(nodes).set_index("id").reindex(node_ids)
        edges_df = pd.DataFrame(
            edges, columns=["src", "dst", "total_amount", "tx_count"]
        )

        # ---- structural aggregates (vectorized groupby, not a Python loop) --
        if not edges_df.empty:
            edges_df["total_amount"] = pd.to_numeric(
                edges_df["total_amount"], errors="coerce"
            ).fillna(0.0)
            edges_df["tx_count"] = pd.to_numeric(
                edges_df["tx_count"], errors="coerce"
            ).fillna(0.0)

            out_agg = edges_df.groupby("src").agg(
                out_degree=("dst", "nunique"),
                total_out_amount=("total_amount", "sum"),
                out_tx_count=("tx_count", "sum"),
            )
            in_agg = edges_df.groupby("dst").agg(
                in_degree=("src", "nunique"),
                total_in_amount=("total_amount", "sum"),
                in_tx_count=("tx_count", "sum"),
            )
        else:
            # dtype matters: without it these come back as object columns and
            # the join below downcasts, which pandas warns about.
            out_agg = pd.DataFrame(
                columns=["out_degree", "total_out_amount", "out_tx_count"],
                dtype="float64",
            )
            in_agg = pd.DataFrame(
                columns=["in_degree", "total_in_amount", "in_tx_count"],
                dtype="float64",
            )

        frame = pd.DataFrame(index=pd.Index(node_ids, name="id"))
        frame = frame.join(out_agg).join(in_agg).astype("float64").fillna(0.0)

        frame["net_flow"] = frame["total_in_amount"] - frame["total_out_amount"]
        # Guard the 0/0 case: an isolated account gets 0.0, not NaN.
        gross = frame["total_in_amount"] + frame["total_out_amount"]
        frame["flow_ratio"] = np.where(
            gross > 0, frame["total_out_amount"] / gross.replace(0, np.nan), 0.0
        )
        frame["flow_ratio"] = frame["flow_ratio"].fillna(0.0)

        frame["pagerank_score"] = self._numeric_column(
            nodes_df, "pagerank_score", num_nodes
        )

        # Neo4j timestamp() is epoch MILLISECONDS, not seconds.
        #
        # created_at must keep its NaN here rather than being zero-filled: a
        # missing timestamp zero-filled to epoch 0 would compute an age of
        # ~20,000 days, which is far worse than admitting we do not know.
        created_column = nodes_df.get("created_at")
        if created_column is None:
            created_ms = np.full(num_nodes, np.nan, dtype="float64")
        else:
            created_ms = pd.to_numeric(
                created_column, errors="coerce"
            ).to_numpy(dtype="float64")
        # A non-positive timestamp is unknown, not 1970.
        created_ms = np.where(created_ms > 0, created_ms, np.nan)

        ref_ms = ref.timestamp() * 1000.0
        age_days = (ref_ms - created_ms) / 86_400_000.0
        # Unknown or future created_at -> age 0 rather than a negative age.
        frame["account_age_days"] = np.nan_to_num(
            np.clip(age_days, 0.0, None), nan=0.0
        )

        # ---- community stats ------------------------------------------------
        community_ids = nodes_df.get("community_id")
        if community_ids is None:
            community_ids = pd.Series([None] * num_nodes, index=frame.index)
        community_ids = pd.Series(
            community_ids.to_numpy(), index=frame.index
        ).where(lambda s: s.notna(), None)

        sizes = community_ids.value_counts()
        frame["community_size"] = (
            community_ids.map(sizes).fillna(0.0).to_numpy(dtype="float64")
        )

        community_risk, community_flagged = self._community_flag_stats(flags)
        frame["community_risk_score"] = (
            community_ids.map(community_risk).fillna(0.0).to_numpy(dtype="float64")
        )
        frame["community_flagged_members"] = (
            community_ids.map(community_flagged).fillna(0.0).to_numpy(dtype="float64")
        )

        # ---- Redis windowed volumes ----------------------------------------
        volume_features: List[str] = []
        for hours in windows_hours:
            label = _window_label(int(hours))
            for prefix in ("volume_out", "volume_in", "txn_out", "txn_in"):
                volume_features.append(f"{prefix}_{label}")

        if volumes:
            vol_df = pd.DataFrame.from_dict(volumes, orient="index")
            vol_df = vol_df.reindex(index=frame.index, columns=volume_features)
        else:
            vol_df = pd.DataFrame(index=frame.index, columns=volume_features)
        for column in volume_features:
            frame[column] = pd.to_numeric(
                vol_df[column], errors="coerce"
            ).fillna(0.0).to_numpy()

        # ---- node type one-hot ---------------------------------------------
        label_lists = (
            nodes_df["labels"] if "labels" in nodes_df.columns
            else pd.Series([None] * num_nodes, index=frame.index)
        )
        label_sets = [
            set(v) if isinstance(v, (list, tuple, set)) else set()
            for v in label_lists.to_numpy()
        ]
        for node_type in NODE_TYPES:
            frame[f"is_{node_type.lower()}"] = np.fromiter(
                (1.0 if node_type in s else 0.0 for s in label_sets),
                dtype="float64",
                count=num_nodes,
            )

        # ---- optional properties, off until ingestion writes them ----------
        for prop in OPTIONAL_NODE_PROPERTY_FEATURES:
            frame[prop] = self._numeric_column(nodes_df, prop, num_nodes)

        self._warn_about_unused_properties(nodes_df)

        feature_names = (
            list(GRAPH_FEATURES)
            + list(COMMUNITY_FEATURES)
            + volume_features
            + list(NODE_TYPE_FEATURES)
            + list(OPTIONAL_NODE_PROPERTY_FEATURES)
        )
        x = frame[feature_names].to_numpy(dtype=np.float32, copy=True)
        if not np.isfinite(x).all():
            raise ValueError(
                "feature matrix contains NaN/inf — a store returned an "
                "unexpected value; refusing to emit poisoned training data"
            )

        # ---- edges ----------------------------------------------------------
        if edges_df.empty:
            edge_index = np.zeros((2, 0), dtype=np.int64)
            edge_weight = np.zeros((0,), dtype=np.float32)
        else:
            edge_index = np.vstack(
                [
                    edges_df["src"].map(index_of).to_numpy(dtype=np.int64),
                    edges_df["dst"].map(index_of).to_numpy(dtype=np.int64),
                ]
            )
            edge_weight = edges_df["total_amount"].to_numpy(dtype=np.float32)

        y, labelled_mask = self._weak_labels(flags, index_of)

        logger.info(
            f"Built features: {num_nodes} nodes, {edge_index.shape[1]} edges, "
            f"{len(feature_names)} features, {int(labelled_mask.sum())} labelled"
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

    @staticmethod
    def _numeric_column(
        nodes_df: pd.DataFrame, name: str, num_nodes: int
    ) -> np.ndarray:
        """Coerce a node property to float, tolerating a missing column.

        An absent column is normal: a node type that never carried the
        property produces no column at all in the DataFrame.
        """
        column = nodes_df.get(name)
        if column is None:
            return np.zeros(num_nodes, dtype="float64")
        return (
            pd.to_numeric(column, errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype="float64")
        )

    @staticmethod
    def _community_flag_stats(
        flags: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Map community_id -> (risk_score, flagged member count).

        COMMUNITY flags carry their community_id inside details, which asyncpg
        hands back as either a dict or a JSON string depending on codec setup.
        """
        import json

        risk: Dict[str, float] = {}
        flagged: Dict[str, float] = {}

        for flag in flags:
            if flag.get("flag_type") != "COMMUNITY":
                continue
            details = flag.get("details")
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (TypeError, ValueError):
                    continue
            if not isinstance(details, dict):
                continue
            community_id = details.get("community_id")
            if not community_id:
                continue
            try:
                risk[community_id] = float(flag.get("risk_score") or 0.0)
            except (TypeError, ValueError):
                risk[community_id] = 0.0
            flagged[community_id] = float(len(flag.get("account_ids") or []))

        return risk, flagged

    @staticmethod
    def _weak_labels(
        flags: List[Dict[str, Any]],
        index_of: Dict[str, int],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Derive class labels from risk_flags.

        These are WEAK labels: they come from the cycle and Louvain detectors,
        which are heuristics, not ground truth. Two consequences worth being
        explicit about:

          - An account with no flag gets class 0 ("low"). Absence of a flag is
            not evidence of innocence, it usually means nothing looked. That is
            why labelled_mask exists — train on the mask if you want to avoid
            teaching the model that unexamined equals safe.
          - An account in several flags takes its HIGHEST level, so a critical
            cycle is not diluted by a medium community flag.
        """
        num_nodes = len(index_of)
        y = np.zeros(num_nodes, dtype=np.int64)
        labelled = np.zeros(num_nodes, dtype=bool)

        for flag in flags:
            level = RISK_LEVEL_TO_CLASS.get(str(flag.get("risk_level", "")).lower())
            if level is None:
                continue
            for account_id in flag.get("account_ids") or []:
                idx = index_of.get(account_id)
                if idx is None:
                    continue
                labelled[idx] = True
                if level > y[idx]:
                    y[idx] = level

        return y, labelled

    @staticmethod
    def _warn_about_unused_properties(nodes_df: pd.DataFrame) -> None:
        """Say so if ingestion started writing a property we still ignore."""
        candidates = (
            "kyc_tier",
            "risk_score",
            "country",
            "account_age",
            "cumulative_volume",
        )
        for prop in candidates:
            if prop in OPTIONAL_NODE_PROPERTY_FEATURES:
                continue
            column = nodes_df.get(prop)
            if column is not None and column.notna().any():
                logger.warning(
                    f"Node property '{prop}' is now populated but is not in "
                    f"OPTIONAL_NODE_PROPERTY_FEATURES, so the GNN ignores it. "
                    f"Add it there to start using it."
                )

    @staticmethod
    def _empty_feature_set(windows_hours: Sequence[int]) -> FeatureSet:
        """An empty graph is valid — an empty-but-wrong-width one is not."""
        from db.redis import _window_label

        volume_features = [
            f"{prefix}_{_window_label(int(hours))}"
            for hours in windows_hours
            for prefix in ("volume_out", "volume_in", "txn_out", "txn_in")
        ]
        feature_names = (
            list(GRAPH_FEATURES)
            + list(COMMUNITY_FEATURES)
            + volume_features
            + list(NODE_TYPE_FEATURES)
            + list(OPTIONAL_NODE_PROPERTY_FEATURES)
        )
        return FeatureSet(
            node_ids=[],
            x=np.zeros((0, len(feature_names)), dtype=np.float32),
            y=np.zeros((0,), dtype=np.int64),
            labelled_mask=np.zeros((0,), dtype=bool),
            edge_index=np.zeros((2, 0), dtype=np.int64),
            edge_weight=np.zeros((0,), dtype=np.float32),
            feature_names=feature_names,
        )
