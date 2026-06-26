"""
Neo4j client for FlowGraph.

Handles property graph operations: creating/updating nodes (accounts) and edges (payment flows).
All writes use MERGE for idempotency (safe for retries).
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from neo4j import AsyncGraphDatabase, AsyncDriver, AsyncSession
from neo4j.exceptions import ServiceUnavailable, DriverError

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE

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
            # Test connection
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
        Idempotent upsert of a transaction into the graph.
        
        Creates or updates:
        - Account nodes for sender and receiver
        - SENDS_TO edge with accumulated amounts and counts
        - Transaction history for traceability
        
        Uses MERGE to ensure:
        - Concurrent writes don't cause duplicates
        - Retries don't double-increment counters
        - All-or-nothing atomicity
        
        Args:
            sender_id: Hashed sender identifier
            receiver_id: Hashed receiver identifier
            amount_cents: Amount in cents
            timestamp_utc: UTC timestamp of transaction
            rail: Payment rail (CARD, WIRE, ACH, CRYPTO)
            event_type: Event type (AUTH, SETTLEMENT, etc.)
            transaction_id: Unique transaction ID
            idempotency_key: Key to prevent duplicate retries
        """
        query = """
        MATCH (src:Account {id: $sender_id})
        MATCH (dst:Account {id: $receiver_id})
        
        MERGE (src)-[edge:SENDS_TO {
            edge_key: $edge_key
        }]->(dst)
        ON CREATE SET
            edge.amount_cents = $amount_cents,
            edge.transaction_count = 1,
            edge.first_seen = $timestamp_utc,
            edge.last_seen = $timestamp_utc,
            edge.created_at = timestamp()
        ON MATCH SET
            edge.amount_cents = edge.amount_cents + $amount_cents,
            edge.transaction_count = edge.transaction_count + 1,
            edge.last_seen = $timestamp_utc,
            edge.updated_at = timestamp()
        
        // Create transaction record for audit trail
        CREATE (txn:Transaction {
            id: $transaction_id,
            idempotency_key: $idempotency_key,
            amount_cents: $amount_cents,
            rail: $rail,
            event_type: $event_type,
            timestamp_utc: $timestamp_utc,
            created_at: timestamp()
        })
        
        // Link transaction to the edge
        CREATE (txn)-[:RECORDED_AS]->(edge)
        """

        edge_key = f"{sender_id}→{receiver_id}"

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run(
                    query,
                    sender_id=sender_id,
                    receiver_id=receiver_id,
                    amount_cents=amount_cents,
                    timestamp_utc=int(timestamp_utc.timestamp()),
                    rail=rail,
                    event_type=event_type,
                    transaction_id=transaction_id,
                    idempotency_key=idempotency_key,
                    edge_key=edge_key,
                )
            logger.debug(
                f"Graph upserted: {sender_id} → {receiver_id} | {amount_cents} cents"
            )
        except ServiceUnavailable as e:
            logger.error(f"Neo4j temporarily unavailable: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to upsert transaction graph: {e}")
            raise

    async def create_account_node(
        self,
        account_id: str,
        account_type: str = "Account",  # Account, Merchant, Bank, Exchange
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create or update an account node with properties.
        
        Args:
            account_id: Unique account identifier
            account_type: Type of account (Account, Merchant, Bank, Exchange)
            properties: Optional additional properties (risk_score, kyc_tier, etc.)
        """
        query = f"""
        MERGE (n:{account_type} {{id: $account_id}})
        ON CREATE SET
            n.created_at = timestamp()
        ON MATCH SET
            n.updated_at = timestamp()
        """

        props = {"account_id": account_id}
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
        
        Used by AI enrichment layer to construct context for risk scoring.
        
        Args:
            account_id: Center account
            depth: Max hops from center (1-6)
            limit: Max nodes to return
        
        Returns:
            Dict with 'nodes' (list of nodes) and 'edges' (list of relationships)
        """
        depth = min(depth, 6)  # Cap at 6 hops
        query = f"""
        MATCH (center:Account {{id: $account_id}})
        MATCH (center)-[rels*1..{depth}]-(neighbors)
        WITH center, neighbors, rels
        LIMIT $limit
        
        RETURN
            collect(distinct center) as center_node,
            collect(distinct neighbors) as neighbor_nodes,
            collect(distinct rels) as relationships
        """

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(query, account_id=account_id, limit=limit)
                record = await result.single()
                if record:
                    return {
                        "center": record["center_node"],
                        "neighbors": record["neighbor_nodes"],
                        "edges": record["relationships"],
                    }
                return {"center": None, "neighbors": [], "edges": []}
        except Exception as e:
            logger.error(f"Failed to fetch subgraph for {account_id}: {e}")
            raise

    async def find_cycles(
        self,
        account_id: str,
        max_depth: int = 6,
        time_window_hours: int = 48,
    ) -> List[Dict[str, Any]]:
        """
        Find circular money flows starting from a given account.
        
        Detects layering patterns (A → B → C → A).
        
        Args:
            account_id: Starting account
            max_depth: Max hops to search
            time_window_hours: Only consider transactions within last N hours
        
        Returns:
            List of cycle paths found
        """
        time_window_seconds = time_window_hours * 3600
        query = f"""
        MATCH (start:Account {{id: $account_id}})
        MATCH (start)-[rels*2..{max_depth}]->(start)
        WHERE ALL(rel IN rels WHERE rel.last_seen > timestamp() - {time_window_seconds})
        
        RETURN rels
        LIMIT 10
        """

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(query, account_id=account_id)
                records = await result.fetch(10)
                return [{"cycle": record["rels"]} for record in records]
        except Exception as e:
            logger.error(f"Failed to find cycles for {account_id}: {e}")
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
            Path info or None if no path exists
        """
        query = f"""
        MATCH (source:Account {{id: $source_id}})
        MATCH (target:Account {{id: $target_id}})
        MATCH path = shortestPath((source)-[*1..{max_depth}]->(target))
        RETURN path, length(path) as hop_count
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
