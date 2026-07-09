"""
Main entry point for FlowGraph Backend.

Initializes all database connections, starts Faust consumer,
and launches background workers (outbox sync, metrics reporting).
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from config import LOG_LEVEL

# Configure root logger
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Import clients and workers
from db.postgres import PostgresClient
from db.neo4j import Neo4jClient
from db.redis import RedisClient
from consumer.faust_app import app as faust_app, initialize_clients
from consumer.dead_letter_handler import DeadLetterHandler
from worker.outbox_sync_worker import OutboxSyncWorker
from worker.metrics import MetricsReporter


class FlowGraphBackend:
    """Main backend orchestrator."""

    def __init__(self):
        """Initialize all components."""
        self.postgres_client: Optional[PostgresClient] = None
        self.neo4j_client: Optional[Neo4jClient] = None
        self.redis_client: Optional[RedisClient] = None
        self.dlq_handler: Optional[DeadLetterHandler] = None
        self.outbox_worker: Optional[OutboxSyncWorker] = None
        self.metrics_reporter: Optional[MetricsReporter] = None
        
        # Background tasks
        self.tasks = []

    async def startup(self) -> None:
        """Initialize all clients and start background workers."""
        try:
            logger.info("=" * 60)
            logger.info("FlowGraph Backend Startup")
            logger.info("=" * 60)

            # ==================== INITIALIZE DATABASE CLIENTS ====================
            logger.info("Initializing database clients...")
            
            self.postgres_client = PostgresClient()
            await self.postgres_client.initialize()
            
            self.neo4j_client = Neo4jClient()
            await self.neo4j_client.initialize()
            await self.neo4j_client.init_constraints()   # Account + TRANSFER uniqueness

            self.redis_client = RedisClient()
            await self.redis_client.initialize()

            logger.info("✓ All database clients initialized")

            # ==================== INITIALIZE HANDLERS ====================
            self.dlq_handler = DeadLetterHandler(kafka_producer=None)
            self.dlq_handler.log_dlq_stats()

            # ==================== HEALTH CHECKS ====================
            logger.info("Running health checks...")
            
            postgres_healthy = await self.postgres_client.health_check()
            neo4j_healthy = await self.neo4j_client.health_check()
            redis_healthy = await self.redis_client.health_check()
            
            if not (postgres_healthy and neo4j_healthy and redis_healthy):
                raise RuntimeError("Health check failed on one or more services")
            
            logger.info("✓ All health checks passed")

            # ==================== INJECT CLIENTS INTO FAUST ====================
            initialize_clients(
                postgres_cli=self.postgres_client,
                neo4j_cli=self.neo4j_client,
                redis_cli=self.redis_client,
                dlq_hdlr=self.dlq_handler,
            )

            # ==================== START BACKGROUND WORKERS ====================
            logger.info("Starting background workers...")
            
            # Outbox sync worker
            self.outbox_worker = OutboxSyncWorker(
                postgres_client=self.postgres_client,
                neo4j_client=self.neo4j_client,
                redis_client=self.redis_client,
                metrics_reporter=None,  # Will be set below
            )
            
            # Metrics reporter
            self.metrics_reporter = MetricsReporter(
                postgres_client=self.postgres_client
            )
            
            # Update outbox worker with metrics reporter
            self.outbox_worker.metrics = self.metrics_reporter
            
            # Create async tasks
            self.tasks = [
                asyncio.create_task(self.outbox_worker.sync_loop()),
                asyncio.create_task(self.metrics_reporter.report_loop()),
            ]
            
            logger.info("✓ Background workers started")

            # ==================== REGISTER SIGNAL HANDLERS ====================
            loop = asyncio.get_running_loop()
            for sig in [signal.SIGTERM, signal.SIGINT]:
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(self.shutdown(signal_name=sig.name)),
                )

            logger.info("=" * 60)
            logger.info("FlowGraph Backend Ready")
            logger.info("=" * 60)
            logger.info(f"Faust consumer group: {faust_app.conf.group_id}")
            logger.info(f"Topics: payments.raw.card")
            logger.info(f"Partition key: sender_id (for temporal ordering)")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Startup failed: {e}", exc_info=True)
            await self.shutdown(error=e)
            raise

    async def shutdown(
        self,
        signal_name: Optional[str] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Gracefully shutdown all components."""
        try:
            if signal_name:
                logger.warning(f"Received signal: {signal_name}")
            if error:
                logger.error(f"Shutdown due to error: {error}")
            
            logger.info("Starting graceful shutdown...")

            # Cancel all tasks
            if self.tasks:
                for task in self.tasks:
                    if not task.done():
                        task.cancel()
                
                # Wait for tasks to complete
                await asyncio.gather(*self.tasks, return_exceptions=True)

            # Stop workers
            if self.outbox_worker:
                await self.outbox_worker.stop()
            
            if self.metrics_reporter:
                await self.metrics_reporter.stop()

            # Close clients
            if self.redis_client:
                await self.redis_client.close()
            
            if self.neo4j_client:
                await self.neo4j_client.close()
            
            if self.postgres_client:
                await self.postgres_client.close()

            logger.info("Graceful shutdown complete")
            
            # Exit with appropriate code
            if error:
                sys.exit(1)
            else:
                sys.exit(0)

        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
            sys.exit(1)


async def main() -> None:
    """Main entry point."""
    backend = FlowGraphBackend()
    
    try:
        await backend.startup()
        
        # Keep the event loop running
        # The Faust consumer runs in its own thread via app.main()
        await asyncio.Event().wait()
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await backend.shutdown(signal_name="SIGINT")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        await backend.shutdown(error=e)


if __name__ == "__main__":
    """
    Run the backend.
    
    Usage:
        # Start backend (async components + Faust consumer)
        python main.py
        
        # The Faust consumer group will automatically join the Kafka topic
        # and process events. Background workers will sync to Neo4j/Redis.
    """
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Backend stopped")
