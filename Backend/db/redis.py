"""
Redis client for FlowGraph.

Handles time-windowed edge weights and caching for fast temporal queries.
Stores transaction amounts as sorted sets (ZSET) keyed by edge, scored by timestamp.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import redis.asyncio as redis
from redis.asyncio import Redis as RedisClient
from redis.exceptions import RedisError

from config import REDIS_URL, REDIS_ENCODING

logger = logging.getLogger(__name__)


class RedisClient:
    """Async Redis client for time-windowed queries and caching."""

    def __init__(self):
        self.client: Optional[RedisClient] = None

    async def initialize(self) -> None:
        """Initialize Redis connection."""
        try:
            # Connect to Redis with connection pool
            self.client = await redis.from_url(
                REDIS_URL,
                encoding=REDIS_ENCODING,
                decode_responses=True,
            )
            # Test connection
            await self.client.ping()
            logger.info("Redis client initialized and connection verified")
        except Exception as e:
            logger.error(f"Failed to initialize Redis client: {e}")
            raise

    async def close(self) -> None:
        """Close Redis connection."""
        if self.client:
            await self.client.close()
            logger.info("Redis client closed")

    async def add_edge_to_timeseries(
        self,
        sender_id: str,
        receiver_id: str,
        amount_cents: int,
        timestamp_utc: datetime,
    ) -> None:
        """
        Record a transaction in a time-windowed sorted set.
        
        Creates a sorted set per edge (sender → receiver) with:
        - Member: transaction identifier
        - Score: unix timestamp
        - Value (implicit): amount encoded in member
        
        This enables fast range queries like:
        "Get all transactions on edge A→B in the last 24 hours"
        
        Args:
            sender_id: Hashed sender identifier
            receiver_id: Hashed receiver identifier
            amount_cents: Amount in cents
            timestamp_utc: UTC timestamp of transaction
        """
        # Key format: edge:{sender}:{receiver}
        edge_key = f"edge:{sender_id}:{receiver_id}"
        
        # Member format: amount|timestamp|uuid (for uniqueness)
        # Score: unix timestamp (for range queries)
        timestamp_unix = int(timestamp_utc.timestamp())
        member = f"{amount_cents}|{timestamp_unix}"

        try:
            # Add to sorted set
            # ZADD inserts and returns count of new members added
            await self.client.zadd(
                edge_key,
                {member: timestamp_unix},
            )
            
            # Set TTL on edge key (30 days for historical lookback)
            await self.client.expire(edge_key, 30 * 24 * 3600)
            
            logger.debug(
                f"Edge recorded: {sender_id} → {receiver_id} | "
                f"{amount_cents} cents @ {timestamp_unix}"
            )
        except RedisError as e:
            logger.error(f"Failed to add edge to timeseries: {e}")
            raise

    async def get_edge_volume_in_window(
        self,
        sender_id: str,
        receiver_id: str,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """
        Query total volume and transaction count on an edge within a time window.
        
        Args:
            sender_id: Hashed sender identifier
            receiver_id: Hashed receiver identifier
            hours: Time window in hours (default 24)
        
        Returns:
            Dict with 'total_volume_cents', 'transaction_count', 'earliest_timestamp', 'latest_timestamp'
        """
        edge_key = f"edge:{sender_id}:{receiver_id}"
        now = datetime.utcnow().timestamp()
        window_seconds = hours * 3600
        min_timestamp = now - window_seconds

        try:
            # Get all members in time window
            # ZRANGEBYSCORE returns members with score in range [min, max]
            members = await self.client.zrangebyscore(
                edge_key,
                min=min_timestamp,
                max=now,
            )
            
            if not members:
                return {
                    "total_volume_cents": 0,
                    "transaction_count": 0,
                    "earliest_timestamp": None,
                    "latest_timestamp": None,
                }

            # Parse members: format is "{amount_cents}|{timestamp}"
            total_volume = 0
            earliest_timestamp = None
            latest_timestamp = None

            for member in members:
                parts = member.split("|")
                if len(parts) >= 2:
                    amount_cents = int(parts[0])
                    timestamp_unix = int(parts[1])
                    
                    total_volume += amount_cents
                    if earliest_timestamp is None or timestamp_unix < earliest_timestamp:
                        earliest_timestamp = timestamp_unix
                    if latest_timestamp is None or timestamp_unix > latest_timestamp:
                        latest_timestamp = timestamp_unix

            return {
                "total_volume_cents": total_volume,
                "transaction_count": len(members),
                "earliest_timestamp": datetime.utcfromtimestamp(earliest_timestamp) if earliest_timestamp else None,
                "latest_timestamp": datetime.utcfromtimestamp(latest_timestamp) if latest_timestamp else None,
                "window_hours": hours,
            }
        except RedisError as e:
            logger.error(f"Failed to query edge volume: {e}")
            raise

    async def get_edges_above_threshold(
        self,
        threshold_cents: int,
        hours: int = 24,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Find all edges where total volume exceeds threshold in time window.
        
        Scans Redis keys matching pattern "edge:*" and checks each.
        (For large datasets, consider maintaining a separate sorted set of hot edges.)
        
        Args:
            threshold_cents: Volume threshold
            hours: Time window
            limit: Max results to return
        
        Returns:
            List of edges with volume above threshold
        """
        results = []
        cursor = 0

        try:
            while True:
                cursor, keys = await self.client.scan(
                    cursor,
                    match="edge:*",
                    count=100,
                )

                for key in keys:
                    # Extract sender and receiver from key
                    parts = key.split(":")
                    if len(parts) == 3:
                        sender_id = parts[1]
                        receiver_id = parts[2]
                        
                        volume_data = await self.get_edge_volume_in_window(
                            sender_id, receiver_id, hours
                        )
                        
                        if volume_data["total_volume_cents"] >= threshold_cents:
                            results.append({
                                "sender_id": sender_id,
                                "receiver_id": receiver_id,
                                **volume_data,
                            })
                        
                        if len(results) >= limit:
                            return results

                if cursor == 0:
                    break

            return results
        except RedisError as e:
            logger.error(f"Failed to find edges above threshold: {e}")
            raise

    async def get_account_in_degree(
        self,
        account_id: str,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """
        Get total inbound volume to an account in time window.
        
        Args:
            account_id: Target account
            hours: Time window
        
        Returns:
            Dict with 'total_in_volume_cents', 'source_count'
        """
        cursor = 0
        total_volume = 0
        sources = set()

        try:
            while True:
                cursor, keys = await self.client.scan(
                    cursor,
                    match=f"edge:*:{account_id}",
                    count=100,
                )

                for key in keys:
                    parts = key.split(":")
                    if len(parts) == 3:
                        sender_id = parts[1]
                        sources.add(sender_id)
                        
                        volume_data = await self.get_edge_volume_in_window(
                            sender_id, account_id, hours
                        )
                        total_volume += volume_data["total_volume_cents"]

                if cursor == 0:
                    break

            return {
                "total_in_volume_cents": total_volume,
                "source_count": len(sources),
                "sources": list(sources),
                "window_hours": hours,
            }
        except RedisError as e:
            logger.error(f"Failed to get account in-degree: {e}")
            raise

    async def cache_set(
        self,
        key: str,
        value: str,
        ttl_seconds: int = 3600,
    ) -> None:
        """
        Set a cache entry with TTL.
        
        Args:
            key: Cache key
            value: Cache value (JSON string recommended)
            ttl_seconds: Time to live in seconds
        """
        try:
            await self.client.setex(key, ttl_seconds, value)
        except RedisError as e:
            logger.error(f"Failed to set cache: {e}")
            raise

    async def cache_get(self, key: str) -> Optional[str]:
        """Get a cache entry."""
        try:
            return await self.client.get(key)
        except RedisError as e:
            logger.error(f"Failed to get cache: {e}")
            raise

    async def cache_delete(self, key: str) -> None:
        """Delete a cache entry."""
        try:
            await self.client.delete(key)
        except RedisError as e:
            logger.error(f"Failed to delete cache: {e}")
            raise

    async def health_check(self) -> bool:
        """Test Redis connection."""
        try:
            await self.client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False
