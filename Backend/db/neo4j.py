"""
Neo4j client for FlowGraph.

Handles property graph operations: creating/updating Account nodes and TRANSFER edges.
Each payment becomes a distinct edge keyed by txn_id (MERGE-safe for outbox retries).

Edge model:
    (src:Account)-[:TRANSFER {txn_id, amount_cents, ts, rail, event_type}]->(dst:Account)

  - One edge per payment (not one aggregated edge per pair). This is required for
    transaction-level cycle detection (tracing a specific flow of funds around a loop).
  - ts is unix SECONDS (int) for fast comparisons in cycle Cypher.
  - Volume/window aggregation lives in Redis, not here.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from neo4j import AsyncGraphDatabase, AsyncDriver, Query
from neo4j.exceptions import ServiceUnavailable

from config import (
    NEO4J_URI,
    NEO4J_USER,
    NEO4J_PASSWORD,
    NEO4J_DATABASE,
    CYCLE_MAX_DEPTH,
    CYCLE_WINDOW_HOURS,
    CYCLE_MAX_HOP_GAP_HOURS,
    CYCLE_MAX_LEAK,
    CYCLE_MIN_VALUE_CENTS,
    CYCLE_MAX_RESULTS,
    CYCLE_CONSERVATION_MODE,
    CYCLE_MAX_CYCLE_LEAK,
    CYCLE_QUERY_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Async Neo4j client for graph operations."""

    def __init__(self):
        self.driver: Optional[AsyncDriver] = None

    async def initialize(self) -> None:
        """Initialize Neo4j driver and test connection."""
        try:
            self.driver = AsyncGraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), encrypted=False
            )
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run("RETURN 1")
            logger.info("Neo4j driver initialized and connection verified")
        except Exception as e:
            logger.error(f"Failed to initialize Neo4j driver: {e}")
            raise

    async def close(self) -> None:
        """Close Neo4j driver."""
        if self.driver:
            await self.driver.close()
            logger.info("Neo4j driver closed")

    async def init_constraints(self) -> None:
        """
        Create uniqueness constraints for Account nodes and TRANSFER edges.

        Called once at startup (safe to re-run — IF NOT EXISTS).
        Relationship property uniqueness requires Neo4j 5.7+; a warning is logged
        if unavailable. The MERGE on txn_id still prevents duplicates either way.
        """
        constraints = [
            "CREATE CONSTRAINT account_id_unique IF NOT EXISTS "
            "FOR (a:Account) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT transfer_txn_unique IF NOT EXISTS "
            "FOR ()-[t:TRANSFER]-() REQUIRE t.txn_id IS UNIQUE",
        ]
        async with self.driver.session(database=NEO4J_DATABASE) as session:
            for stmt in constraints:
                try:
                    await session.run(stmt)
                except Exception as e:
                    # Relationship-property uniqueness constraint may not be available
                    # on Neo4j < 5.7. Log a warning rather than crashing startup.
                    logger.warning(
                        "Could not create constraint (may require Neo4j 5.7+): %s | %s",
                        stmt,
                        e,
                    )
        logger.info("Neo4j constraints initialized")

    async def upsert_transaction_graph(
        self,
        sender_id: str,
        receiver_id: str,
        amount_cents: int,
        timestamp_utc: datetime,
        rail: str,
        event_type: str,
        transaction_id: str,
        idempotency_key: str,
    ) -> None:
        """
        Idempotent upsert of a payment as a per-transaction TRANSFER edge.

        Graph model:
            MERGE (src:Account {id: sender_id})
            MERGE (dst:Account {id: receiver_id})
            MERGE (src)-[:TRANSFER {txn_id: transaction_id}]->(dst)

        MERGE on txn_id means outbox retries are safe — the ON CREATE block only
        fires once per transaction_id. Account nodes are created on first appearance.

        Timestamps are stored as unix seconds (int) for fast integer comparisons in
        the cycle-detection Cypher. Neo4j's timestamp() returns ms; we use explicit
        epoch seconds to avoid the ms/s mixing bug in the previous stub.

        Args:
            sender_id: Hashed sender identifier
            receiver_id: Hashed receiver identifier
            amount_cents: Amount in cents
            timestamp_utc: UTC timestamp of transaction
            rail: Payment rail (CARD, WIRE, ACH, CRYPTO)
            event_type: Event type (AUTH, SETTLEMENT, etc.)
            transaction_id: Unique transaction ID — used as TRANSFER.txn_id (MERGE key)
            idempotency_key: Kept for interface compatibility; MERGE on txn_id is the guard
        """
        # Two edges are maintained per payment:
        #   1. TRANSFER — one per transaction (MERGE on txn_id), full per-txn detail.
        #      Used for audit and any per-txn tracing.
        #   2. FLOWS_TO — one aggregate edge per directed account pair (CLAUDE.md data
        #      model: total_amount, tx_count, first_seen, last_seen, avg_tx_size).
        #      Cycle detection runs on FLOWS_TO because it collapses the 20-50 parallel
        #      TRANSFER edges between a pair into a single edge, so variable-length cycle
        #      search does not explode combinatorially (branching = distinct neighbours,
        #      not parallel-edge count).
        #
        # Idempotency: TRANSFER is safe on retry (MERGE on txn_id). FLOWS_TO aggregates
        # are incremented on every call; the outbox's once-delivery guarantee prevents
        # double-counting in production.
        query = """
        MERGE (src:Account {id: $sender_id})
          ON CREATE SET src.created_at = timestamp()
        MERGE (dst:Account {id: $receiver_id})
          ON CREATE SET dst.created_at = timestamp()
        MERGE (src)-[t:TRANSFER {txn_id: $transaction_id}]->(dst)
          ON CREATE SET
            t.amount_cents = $amount_cents,
            t.ts           = $timestamp_utc,
            t.rail         = $rail,
            t.event_type   = $event_type,
            t.created_at   = timestamp()
        MERGE (src)-[f:FLOWS_TO]->(dst)
          ON CREATE SET
            f.tx_count     = 1,
            f.total_amount = $amount_cents,
            f.first_ts     = $timestamp_utc,
            f.last_ts      = $timestamp_utc,
            f.min_amount   = $amount_cents,
            f.max_amount   = $amount_cents,
            f.rail         = $rail
          ON MATCH SET
            f.tx_count     = f.tx_count + 1,
            f.total_amount = f.total_amount + $amount_cents,
            f.first_ts     = CASE WHEN $timestamp_utc < f.first_ts THEN $timestamp_utc ELSE f.first_ts END,
            f.last_ts      = CASE WHEN $timestamp_utc > f.last_ts  THEN $timestamp_utc ELSE f.last_ts END,
            f.min_amount   = CASE WHEN $amount_cents < f.min_amount THEN $amount_cents ELSE f.min_amount END,
            f.max_amount   = CASE WHEN $amount_cents > f.max_amount THEN $amount_cents ELSE f.max_amount END
        """

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run(
                    query,
                    sender_id=sender_id,
                    receiver_id=receiver_id,
                    amount_cents=amount_cents,
                    timestamp_utc=int(timestamp_utc.timestamp()),  # unix seconds
                    rail=rail,
                    event_type=event_type,
                    transaction_id=transaction_id,
                )
            logger.debug(
                "Graph upserted: %s → %s | txn=%s | %d cents",
                sender_id,
                receiver_id,
                transaction_id,
                amount_cents,
            )
        except ServiceUnavailable as e:
            logger.error(f"Neo4j temporarily unavailable: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to upsert transaction graph: {e}")
            raise

    async def find_cycles(
        self,
        account_id: str,
        max_depth: int = CYCLE_MAX_DEPTH,
        window_hours: int = CYCLE_WINDOW_HOURS,
        max_hop_gap_hours: float = CYCLE_MAX_HOP_GAP_HOURS,
        max_leak: float = CYCLE_MAX_LEAK,
        min_cycle_cents: int = CYCLE_MIN_VALUE_CENTS,
        max_results: int = CYCLE_MAX_RESULTS,
        reference_time: Optional[datetime] = None,
        conservation_mode: str = CYCLE_CONSERVATION_MODE,
        max_cycle_leak: float = CYCLE_MAX_CYCLE_LEAK,
        query_timeout_seconds: float = CYCLE_QUERY_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        """
        Find transaction-level circular flows starting from a given account.

        Returns only TRACED FLOWS where:
        - All hops are within `window_hours` of now
        - Timestamps are monotonically non-decreasing hop-to-hop (money moves forward)
        - Consecutive-hop gap <= max_hop_gap_hours (same flow, not coincidental loop)
        - Amount at each hop is <= previous hop and >= previous * (1 - max_leak)
          (money can bleed off via fees but cannot grow; tight conservation → higher score)
        - The weakest hop clears min_cycle_cents (ignores trivial test noise)

        Implementation note on `max_depth`:
        Cypher requires a literal integer in variable-length path patterns (*2..N).
        max_depth is a config-sourced int clamped to [2, 6] and formatted into the
        query via an f-string. It is NOT user-supplied data (no injection risk) — all
        runtime values (account_id, timestamps, thresholds) are named parameters.

        Args:
            account_id: Starting account
            max_depth: Max hops in cycle, clamped to [2, 6]
            window_hours: Look back this many hours
            max_hop_gap_hours: Max hours allowed between consecutive hops
            max_leak: Max fractional bleed-off per hop (e.g. 0.20 = 20%)
            min_cycle_cents: Minimum weakest-hop amount to flag
            max_results: Cap on number of returned cycles

        Returns:
            List of dicts: {node_ids, amounts, timestamps, txn_ids}
        """
        max_depth = max(2, min(max_depth, max_depth))  # allow config-controlled depth

        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)
        now_epoch = int(ref.timestamp())
        window_start_epoch = now_epoch - int(window_hours * 3600)
        max_hop_gap_seconds = int(max_hop_gap_hours * 3600)

        # Cycle search runs on the aggregated FLOWS_TO edge (one per directed pair),
        # NOT the per-txn TRANSFER edge. This is essential for performance: real payment
        # data has 20-50 parallel TRANSFER edges between the same pair, which makes a
        # variable-length TRANSFER search O(parallel^depth) — billions of paths. FLOWS_TO
        # collapses each pair to a single edge, so branching is the number of distinct
        # neighbours (tiny in a laundering ring).
        #
        # Temporal feasibility on aggregate envelopes:
        #   - last_ts[i+1] >= first_ts[i]  → a forward-ordered txn pair exists
        #   - first_ts[i+1] - last_ts[i] <= max_gap → hops are close enough to be one flow
        # Conservation is currency-aware: when consecutive hops use different currencies
        # (rail differs), raw cent comparison is meaningless across FX, so we skip it.
        # Otherwise the representative (max) amount must be non-growing within max_leak.
        # Temporal model is rotation-invariant. A cycle seeded from an arbitrary account
        # crosses one "wrap point" where time resets (the flow's start node), so requiring
        # strictly increasing timestamps along the path is wrong — it fails for every
        # rotation except one. Instead we require AT MOST ONE time-descent around the ring:
        # a genuine one-directional money flow ascends on every hop except the single wrap,
        # while a coincidental topological ring has many descents.
        #
        # `ft` is the per-hop representative time (first_ts). Index i+1 wraps to 0 so the
        # descent count is computed over the full circular sequence.
        #
        # Conservation has three modes (config CYCLE_CONSERVATION_MODE):
        #   "hop"   — per-hop range-overlap on the aggregate min/max envelope: consecutive
        #             same-currency hops must have overlapping amount ranges within max_leak
        #             (some pair of txns across the two hops forms a conserved step).
        #             Cross-currency hops skip it (FX makes cents incomparable).
        #   "cycle" — whole-ring magnitude consistency: the weakest hop's representative
        #             amount is >= the strongest hop's × (1 - max_cycle_leak). Captures the
        #             laundering signal (money returns to origin at similar magnitude) while
        #             tolerating per-hop fee/split variation. Default.
        #   "off"   — no amount conservation; topology + temporal + value floor only.
        conservation_mode = (conservation_mode or "cycle").lower()
        if conservation_mode == "hop":
            conservation_clause = """
          AND ALL(i IN range(0, n-2) WHERE
                rels[i].rail <> rels[i+1].rail
                OR (
                  rels[i+1].min_amount <= rels[i].max_amount
                  AND rels[i+1].max_amount >= toInteger(rels[i].min_amount * (1.0 - $max_leak))
                ))"""
        elif conservation_mode == "off":
            conservation_clause = ""
        else:  # "cycle" (default)
            conservation_clause = """
          AND reduce(mx = rels[0].max_amount, r IN rels |
                CASE WHEN r.max_amount > mx THEN r.max_amount ELSE mx END)
              * (1.0 - $max_cycle_leak)
              <= reduce(mn = rels[0].max_amount, r IN rels |
                CASE WHEN r.max_amount < mn THEN r.max_amount ELSE mn END)"""

        query = f"""
        MATCH path = (start:Account {{id: $account_id}})-[rels:FLOWS_TO*2..{max_depth}]->(start)
        WITH path, rels, [r IN rels | r.first_ts] AS ft, size(rels) AS n
        WHERE ALL(r IN rels WHERE r.last_ts >= $window_start_epoch)
          AND size([i IN range(0, n-1) WHERE
                (CASE WHEN i+1 = n THEN ft[0] ELSE ft[i+1] END) < ft[i]]) <= 1
          AND ALL(i IN range(0, n-1) WHERE
                (CASE WHEN i+1 = n THEN ft[0] ELSE ft[i+1] END) < ft[i]
                OR (CASE WHEN i+1 = n THEN ft[0] ELSE ft[i+1] END) - ft[i] <= $max_hop_gap_seconds){conservation_clause}
          AND reduce(mn = rels[0].max_amount, r IN rels |
                CASE WHEN r.max_amount < mn THEN r.max_amount ELSE mn END) >= $min_cycle_cents
        RETURN [nd IN nodes(path) | nd.id] AS node_ids,
               [r IN rels | r.max_amount]  AS amounts,
               [r IN rels | r.last_ts]     AS timestamps,
               [r IN rels | r.tx_count]    AS txn_ids
        LIMIT $max_results
        """

        # Transaction timeout bounds the worst case: a deep fruitless search on a
        # high-degree account must never stall the pipeline. On timeout Neo4j raises,
        # which we treat as "no cycle from this account" — correct, since a search that
        # can't finish in the budget yields no actionable flag.
        timed_query = Query(query, timeout=query_timeout_seconds)

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    timed_query,
                    account_id=account_id,
                    window_start_epoch=window_start_epoch,
                    max_hop_gap_seconds=max_hop_gap_seconds,
                    max_leak=max_leak,
                    max_cycle_leak=max_cycle_leak,
                    min_cycle_cents=min_cycle_cents,
                    max_results=max_results,
                )
                records = await result.fetch(max_results)
                return [
                    {
                        "node_ids":   record["node_ids"],
                        "amounts":    record["amounts"],
                        "timestamps": record["timestamps"],
                        "txn_ids":    record["txn_ids"],
                    }
                    for record in records
                ]
        except Exception as e:
            # ClientError with code TransactionTimeoutClientError (or the driver's
            # timeout) means the search exceeded its budget — treat as no cycle found
            # rather than failing the whole detection pass.
            if "timeout" in str(e).lower() or "Timeout" in type(e).__name__:
                logger.warning(
                    "Cycle query for %s exceeded %.1fs budget — treating as no cycle",
                    account_id[:8], query_timeout_seconds,
                )
                return []
            logger.error(f"Failed to find cycles for {account_id}: {e}")
            raise

    async def create_account_node(
        self,
        account_id: str,
        account_type: str = "Account",
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create or update an account node with optional properties.

        account_type must be one of the four defined node types (Account, Merchant,
        Bank, Exchange) per CLAUDE.md. Do not pass user-supplied strings here.

        Args:
            account_id: Unique account identifier
            account_type: Node label (Account, Merchant, Bank, Exchange)
            properties: Optional additional properties (risk_score, kyc_tier, etc.)
        """
        query = f"""
        MERGE (n:{account_type} {{id: $account_id}})
        ON CREATE SET n.created_at = timestamp()
        ON MATCH SET  n.updated_at = timestamp()
        """
        props: Dict[str, Any] = {"account_id": account_id}
        if properties:
            for key, value in properties.items():
                query += f", n.{key} = ${key}"
                props[key] = value

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run(query, **props)
            logger.debug(f"Account node created/updated: {account_id}")
        except Exception as e:
            logger.error(f"Failed to create account node: {e}")
            raise

    async def get_subgraph(
        self,
        account_id: str,
        depth: int = 3,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Fetch a subgraph around a given account up to N hops.

        Used by the AI enrichment layer to build context for risk scoring.

        Args:
            account_id: Center account
            depth: Max hops from center (capped at 6)
            limit: Max nodes to return

        Returns:
            Dict with 'center', 'neighbors', and 'edges'
        """
        depth = min(depth, 6)
        query = f"""
        MATCH (center:Account {{id: $account_id}})
        MATCH (center)-[rels*1..{depth}]-(neighbors)
        WITH center, neighbors, rels
        LIMIT $limit
        RETURN
            collect(distinct center)    AS center_node,
            collect(distinct neighbors) AS neighbor_nodes,
            collect(distinct rels)      AS relationships
        """
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(query, account_id=account_id, limit=limit)
                record = await result.single()
                if record:
                    return {
                        "center":    record["center_node"],
                        "neighbors": record["neighbor_nodes"],
                        "edges":     record["relationships"],
                    }
                return {"center": None, "neighbors": [], "edges": []}
        except Exception as e:
            logger.error(f"Failed to fetch subgraph for {account_id}: {e}")
            raise

    async def shortest_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 4,
    ) -> Optional[Dict[str, Any]]:
        """
        Find shortest path between two accounts.

        Args:
            source_id: Source account
            target_id: Target account
            max_depth: Max hops to search

        Returns:
            Dict with 'path' and 'hops', or None if no path exists
        """
        query = f"""
        MATCH (source:Account {{id: $source_id}})
        MATCH (target:Account {{id: $target_id}})
        MATCH path = shortestPath((source)-[*1..{max_depth}]->(target))
        RETURN path, length(path) AS hop_count
        LIMIT 1
        """
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    query, source_id=source_id, target_id=target_id
                )
                record = await result.single()
                if record:
                    return {"path": record["path"], "hops": record["hop_count"]}
                return None
        except Exception as e:
            logger.error(
                f"Failed to find shortest path {source_id} → {target_id}: {e}"
            )
            raise

    async def health_check(self) -> bool:
        """Test Neo4j connection."""
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run("RETURN 1")
            return True
        except Exception as e:
            logger.error(f"Neo4j health check failed: {e}")
            return False
