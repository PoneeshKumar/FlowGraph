import asyncio
import time
from neo4j import AsyncGraphDatabase
import redis.asyncio as aioredis

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "changeme"
REDIS_URL = "redis://localhost:6379/0"

# Mock dataset covering distinct AML topologies
MOCK_ACCOUNTS = [
    # 1. Laundering Cycle (A -> B -> C -> A)
    {"id": "acc_cycle_alpha_01", "label": "Cycle Node A", "risk_score": 0.92, "node_type": "account"},
    {"id": "acc_cycle_beta_02", "label": "Cycle Node B", "risk_score": 0.88, "node_type": "account"},
    {"id": "acc_cycle_gamma_03", "label": "Cycle Node C", "risk_score": 0.90, "node_type": "account"},
    
    # 2. Fan-Out Dispersal Hub (Layering)
    {"id": "acc_fanout_hub_10", "label": "Dispersal Hub", "risk_score": 0.78, "node_type": "account"},
    {"id": "acc_mule_leaf_11", "label": "Mule Receiver 1", "risk_score": 0.65, "node_type": "account"},
    {"id": "acc_mule_leaf_12", "label": "Mule Receiver 2", "risk_score": 0.68, "node_type": "account"},
    {"id": "acc_mule_leaf_13", "label": "Mule Receiver 3", "risk_score": 0.62, "node_type": "account"},
    {"id": "acc_mule_leaf_14", "label": "Mule Receiver 4", "risk_score": 0.71, "node_type": "account"},

    # 3. Gather-Scatter Aggregator
    {"id": "acc_gather_dest_20", "label": "Aggregation Sink", "risk_score": 0.85, "node_type": "account"},
    {"id": "acc_smurf_src_21", "label": "Smurf Source 1", "risk_score": 0.45, "node_type": "account"},
    {"id": "acc_smurf_src_22", "label": "Smurf Source 2", "risk_score": 0.42, "node_type": "account"},
    {"id": "acc_smurf_src_23", "label": "Smurf Source 3", "risk_score": 0.49, "node_type": "account"},

    # 4. Low-Risk Merchant / Benchmark
    {"id": "acc_clean_retail_99", "label": "E-Commerce Merchant", "risk_score": 0.05, "node_type": "merchant"},
]

now = int(time.time())

MOCK_FLOWS = [
    # Cycle Edges
    {"src": "acc_cycle_alpha_01", "dst": "acc_cycle_beta_02", "amount": 250000.0, "tx_count": 8, "ts": now - 3600},
    {"src": "acc_cycle_beta_02", "dst": "acc_cycle_gamma_03", "amount": 248000.0, "tx_count": 7, "ts": now - 2400},
    {"src": "acc_cycle_gamma_03", "dst": "acc_cycle_alpha_01", "amount": 245000.0, "tx_count": 6, "ts": now - 1200},

    # Fan-Out Edges
    {"src": "acc_fanout_hub_10", "dst": "acc_mule_leaf_11", "amount": 49000.0, "tx_count": 3, "ts": now - 7200},
    {"src": "acc_fanout_hub_10", "dst": "acc_mule_leaf_12", "amount": 48500.0, "tx_count": 2, "ts": now - 6800},
    {"src": "acc_fanout_hub_10", "dst": "acc_mule_leaf_13", "amount": 50000.0, "tx_count": 4, "ts": now - 6400},
    {"src": "acc_fanout_hub_10", "dst": "acc_mule_leaf_14", "amount": 47200.0, "tx_count": 2, "ts": now - 6000},

    # Gather Edges
    {"src": "acc_smurf_src_21", "dst": "acc_gather_dest_20", "amount": 9800.0, "tx_count": 5, "ts": now - 14400},
    {"src": "acc_smurf_src_22", "dst": "acc_gather_dest_20", "amount": 9500.0, "tx_count": 4, "ts": now - 13200},
    {"src": "acc_smurf_src_23", "dst": "acc_gather_dest_20", "amount": 9900.0, "tx_count": 6, "ts": now - 12000},

    # Legitimate edge
    {"src": "acc_mule_leaf_11", "dst": "acc_clean_retail_99", "amount": 120.0, "tx_count": 1, "ts": now - 500},
]


async def seed_data():
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    print("Connecting and cleaning existing test nodes...")
    async with driver.session() as session:
        # Clear mock nodes if present
        await session.run("MATCH (n:Account) WHERE n.id STARTS WITH 'acc_' DETACH DELETE n")

        # 1. Upsert Nodes
        for acc in MOCK_ACCOUNTS:
            query = """
            MERGE (a:Account {id: $id})
            SET a.label = $label,
                a.risk_score = $risk_score,
                a.node_type = $node_type,
                a.community_id = 101,
                a.pagerank_score = 0.045
            """
            await session.run(query, **acc)

        # 2. Upsert FLOWS_TO Edges in Neo4j
        for flow in MOCK_FLOWS:
            edge_query = """
            MATCH (src:Account {id: $src}), (dst:Account {id: $dst})
            MERGE (src)-[r:FLOWS_TO]->(dst)
            SET r.total_amount = $amount,
                r.tx_count = $tx_count,
                r.first_ts = $ts,
                r.last_ts = $ts
            """
            await session.run(edge_query, **flow)

            # 3. Seed Redis ZSET for time-window queries
            redis_key = f"edge:{flow['src']}:{flow['dst']}"
            await redis.zadd(redis_key, {f"{flow['amount']}:mock_txn_id": flow["ts"]})

    await driver.close()
    await redis.close()
    print("Seeding complete.")

if __name__ == "__main__":
    asyncio.run(seed_data())