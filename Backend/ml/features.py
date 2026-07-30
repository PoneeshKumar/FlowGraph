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

# Which detectors are allowed to define labels. CYCLE only, deliberately.
#
# COMMUNITY flags are excluded to stop target leakage. score_community derives
# risk_level from risk_score by fixed thresholds (community_detector.py:349),
# and community_risk_score feeds that same risk_score in as a feature — so a
# COMMUNITY-labelled account would arrive with its own answer in column 11 and
# the model would just relearn the thresholds.
#
# The split also matches the intended architecture: Louvain is a feature
# provider, cycle detection is the label source, and no feature shares a
# computation with the label it predicts.
LABEL_FLAG_TYPES: Tuple[str, ...] = ("CYCLE",)

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
    "account_age_days",     # from FLOWS_TO.first_ts, NOT Account.created_at
)

# Self-transfer behaviour, held separately so it cannot contaminate the
# counterparty aggregates above. See the self-loop handling in _assemble.
SELF_LOOP_FEATURES: Tuple[str, ...] = (
    "self_loop_count",
    "self_loop_amount",
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
    # Per-node first activity, unix seconds, NaN where unknown. Metadata, NOT a
    # feature column — a chronological train/test split needs it, but feeding
    # absolute time to the model would teach it calendar dates rather than
    # behaviour.
    node_first_ts: Optional[np.ndarray] = None

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
        export_timeout_seconds: float = 1800.0,
        drop_self_loops: bool = True,
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
            export_timeout_seconds:
                            Timeout for both Neo4j exports. Must far exceed
                            LOUVAIN_EXPORT_TIMEOUT_SECONDS (120s), which both
                            export methods default to: that budget is sized for
                            the Louvain window, not a whole graph. Measured on
                            the loaded HI-Small graph, 513,987 nodes and
                            1,010,384 edges take well over two minutes to
                            stream, so the default would abort every build.
        """
        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)

        nodes = await self.neo4j.export_account_nodes(
            query_timeout_seconds=export_timeout_seconds
        )
        edges = await self.neo4j.export_flows_to_edges(
            window_days=window_days,
            reference_time=ref,
            query_timeout_seconds=export_timeout_seconds,
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

        return self._assemble(
            nodes, edges, volumes, flags, windows_hours, ref, drop_self_loops
        )

    def _assemble(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        volumes: Dict[str, Dict[str, float]],
        flags: List[Dict[str, Any]],
        windows_hours: Sequence[int],
        ref: datetime,
        drop_self_loops: bool = True,
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
        all_edges_df = pd.DataFrame(
            edges,
            columns=["src", "dst", "total_amount", "tx_count", "first_ts", "last_ts"],
        )

        if not all_edges_df.empty:
            for column in ("total_amount", "tx_count", "first_ts", "last_ts"):
                all_edges_df[column] = pd.to_numeric(
                    all_edges_df[column], errors="coerce"
                ).fillna(0.0)

        # ---- split self-loops out before aggregating anything ---------------
        #
        # An account transferring to itself is not a counterparty, and counting
        # it as one corrupts the most diagnostic feature in the set. A
        # self-loop-only account would otherwise present out_degree=1,
        # in_degree=1, net_flow=0 and flow_ratio=0.5 — an exact pass-through
        # mule signature, produced by an account that never touched anyone.
        # Measured on the loaded HI-Small graph that is 92,154 accounts (17.9%)
        # faking the pattern, off 365,987 self-loop edges (36% of all edges).
        #
        # The information is not discarded: it moves into SELF_LOOP_FEATURES so
        # the model can still use self-transfer behaviour if it is predictive.
        # Self-loops are also excluded from edge_index, because SAGEConv already
        # applies its own root weight and a self-loop makes a node aggregate
        # itself a second time.
        self_loop_df = all_edges_df.iloc[:0]
        if drop_self_loops and not all_edges_df.empty:
            is_self = all_edges_df["src"] == all_edges_df["dst"]
            self_loop_df = all_edges_df[is_self]
            edges_df = all_edges_df[~is_self].reset_index(drop=True)
            if len(self_loop_df):
                logger.info(
                    f"Excluded {len(self_loop_df):,} self-loop edges from "
                    f"aggregates and edge_index "
                    f"({len(self_loop_df) / len(all_edges_df):.1%} of edges)"
                )
        else:
            edges_df = all_edges_df

        # ---- structural aggregates (vectorized groupby, not a Python loop) --
        if not edges_df.empty:
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

        # ---- account age, from first observed ACTIVITY -----------------------
        #
        # Derived from FLOWS_TO.first_ts, not Account.created_at. created_at is
        # set to Neo4j timestamp() when the outbox inserts the node, so it
        # records ingest time rather than account age: on this dataset it made
        # every age negative (data from 2022, inserted in 2026) and clipped the
        # whole column to zero, i.e. a dead feature.
        #
        # first_ts is unix SECONDS (the consumer stores epoch seconds
        # deliberately), unlike created_at which is Neo4j milliseconds.
        # Self-loops are included here: an account paying itself still proves it
        # existed at that time.
        first_seen = self._node_first_activity(all_edges_df, frame.index)
        ref_seconds = ref.timestamp()
        age_days = (ref_seconds - first_seen) / 86_400.0
        # Unknown or future first activity -> 0 rather than a negative age.
        frame["account_age_days"] = np.nan_to_num(
            np.clip(age_days, 0.0, None), nan=0.0
        )

        # ---- self-loop behaviour, kept rather than thrown away ---------------
        if len(self_loop_df):
            loop_agg = self_loop_df.groupby("src").agg(
                self_loop_count=("tx_count", "sum"),
                self_loop_amount=("total_amount", "sum"),
            )
            frame["self_loop_count"] = (
                loop_agg["self_loop_count"].reindex(frame.index).fillna(0.0).to_numpy()
            )
            frame["self_loop_amount"] = (
                loop_agg["self_loop_amount"].reindex(frame.index).fillna(0.0).to_numpy()
            )
        else:
            frame["self_loop_count"] = 0.0
            frame["self_loop_amount"] = 0.0

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
            + list(SELF_LOOP_FEATURES)
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
            node_first_ts=first_seen,
        )

    @staticmethod
    def _node_first_activity(
        all_edges_df: pd.DataFrame, index: pd.Index
    ) -> np.ndarray:
        """Earliest FLOWS_TO.first_ts touching each account, in unix seconds.

        Both directions count: an account is as old as the first edge it appears
        on, whether it sent or received. NaN where the account has no edge with
        a usable timestamp, so the caller can distinguish "unknown" from "new".
        """
        if all_edges_df.empty or "first_ts" not in all_edges_df.columns:
            return np.full(len(index), np.nan, dtype="float64")

        timed = all_edges_df[all_edges_df["first_ts"] > 0]
        if timed.empty:
            return np.full(len(index), np.nan, dtype="float64")

        stacked = pd.concat(
            [
                timed[["src", "first_ts"]].rename(columns={"src": "account"}),
                timed[["dst", "first_ts"]].rename(columns={"dst": "account"}),
            ],
            ignore_index=True,
        )
        earliest = stacked.groupby("account", sort=False)["first_ts"].min()
        return earliest.reindex(index).to_numpy(dtype="float64")

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

        flagged_member_count comes from details, NOT from len(account_ids):
        account_ids is every member of the community, so using its length would
        just duplicate community_size. details["flagged_member_count"] is the
        count of members already carrying flags from other detectors
        (community_detector.py:376), which is the actual signal.

        CAUTION — residual leakage. Both values returned here depend on CYCLE
        flags, which are now the labels: score_community folds
        flagged_member_count/n into risk_score via overlap_score
        (community_detector.py:334), and flagged_accounts comes from
        get_flagged_account_ids(exclude_flag_type='COMMUNITY'). So these two
        columns encode "how many of my community peers are labelled fraud".
        That is a neighbour-label feature, not a self-label one, so it is much
        weaker than the leak LABEL_FLAG_TYPES fixes — but on a single shared
        graph it can still carry test-node labels into train-node features.
        Drop columns 11 and 12 if validation scores look too good to be true.
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
            try:
                flagged[community_id] = float(
                    details.get("flagged_member_count") or 0.0
                )
            except (TypeError, ValueError):
                flagged[community_id] = 0.0

        return risk, flagged

    @staticmethod
    def _weak_labels(
        flags: List[Dict[str, Any]],
        index_of: Dict[str, int],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Derive class labels from risk_flags.

        Only LABEL_FLAG_TYPES (CYCLE) count — see that constant for why
        COMMUNITY flags are excluded.

        These are WEAK labels: the cycle detector is a heuristic, not ground
        truth. Two consequences worth being explicit about:

          - An account with no flag gets class 0 ("low"). Absence of a flag is
            not evidence of innocence, it usually means nothing looked. That is
            why labelled_mask exists — train on the mask if you want to avoid
            teaching the model that unexamined equals safe.
          - An account in several flags takes its HIGHEST level, so a critical
            cycle is not diluted by a medium one.
        """
        num_nodes = len(index_of)
        y = np.zeros(num_nodes, dtype=np.int64)
        labelled = np.zeros(num_nodes, dtype=bool)

        for flag in flags:
            if flag.get("flag_type") not in LABEL_FLAG_TYPES:
                continue
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
            + list(SELF_LOOP_FEATURES)
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
            node_first_ts=np.zeros((0,), dtype="float64"),
        )
