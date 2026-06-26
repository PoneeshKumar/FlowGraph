-- Migration: Create outbox and transactions tables for eventual consistency pattern
-- This ensures Postgres is the source of truth, with Neo4j/Redis as eventually-consistent caches

-- ==================== TRANSACTIONS TABLE ====================
-- Canonical record of all payment events (source of truth)
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY,
    rail VARCHAR(20) NOT NULL,  -- CARD, WIRE, ACH, CRYPTO
    event_type VARCHAR(20) NOT NULL,  -- AUTH, SETTLEMENT, etc.
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING, SETTLED, DECLINED, ORPHANED
    
    -- Parties in the transaction
    sender_id VARCHAR(255) NOT NULL,  -- hashed PAN or account identifier
    receiver_id VARCHAR(255) NOT NULL,  -- hashed PAN or merchant/account identifier
    
    -- Money amount (in cents, never floats)
    amount_cents BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    
    -- Timestamps (all UTC)
    timestamp_utc TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Original raw payload for debugging/audit
    raw_payload JSONB NOT NULL,
    
    -- Metadata
    schema_version INT DEFAULT 1,
    authorization_code VARCHAR(6)  -- for matching auth ↔ settlement
);

CREATE INDEX idx_transactions_sender_id ON transactions(sender_id);
CREATE INDEX idx_transactions_receiver_id ON transactions(receiver_id);
CREATE INDEX idx_transactions_timestamp_utc ON transactions(timestamp_utc DESC);
CREATE INDEX idx_transactions_created_at ON transactions(created_at DESC);
CREATE INDEX idx_transactions_authorization_code ON transactions(authorization_code);
CREATE INDEX idx_transactions_status ON transactions(status);

-- ==================== OUTBOX TABLE ====================
-- Intermediate staging table for eventual consistency
-- Postgres writes create a transaction + outbox row atomically
-- Background worker polls this table and syncs to Neo4j/Redis
CREATE TABLE IF NOT EXISTS outbox (
    id BIGSERIAL PRIMARY KEY,
    
    -- Link to the transaction that triggered this outbox entry
    transaction_id UUID NOT NULL UNIQUE,
    CONSTRAINT fk_outbox_transaction FOREIGN KEY (transaction_id) 
        REFERENCES transactions(id) ON DELETE CASCADE,
    
    -- Serialized transaction data (for retry/audit)
    event_payload JSONB NOT NULL,
    
    -- Sync status: pending (not yet synced), synced (successfully written to Neo4j/Redis), failed (max retries exceeded)
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    
    -- Idempotency: hash of (event_id + timestamp) to detect duplicate retries
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    
    -- Retry tracking
    retry_count INT NOT NULL DEFAULT 0,
    last_error TEXT,
    last_retry_at TIMESTAMP,
    
    -- Serialized Neo4j write payload (for debugging)
    neo4j_write_payload JSONB,
    
    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    synced_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index for polling queries: find all pending records, ordered by age
CREATE INDEX idx_outbox_status_created_at ON outbox(status, created_at ASC)
    WHERE status = 'pending';

-- Index for finding records to retry based on last_retry_at
CREATE INDEX idx_outbox_retry ON outbox(status, last_retry_at)
    WHERE status = 'pending';

-- Index for debugging by transaction
CREATE INDEX idx_outbox_transaction_id ON outbox(transaction_id);

-- ==================== MATERIALIZED VIEW (OPTIONAL) ====================
-- For monitoring: current outbox stats
-- SELECT status, COUNT(*) as count, MAX(age(created_at)) as max_age 
-- FROM outbox GROUP BY status;
-- This query should run in ~milliseconds due to table indexing.

-- ==================== TRIGGER TO UPDATE updated_at ====================
-- Auto-update the updated_at timestamp on any row modification
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER trigger_transactions_updated_at
BEFORE UPDATE ON transactions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trigger_outbox_updated_at
BEFORE UPDATE ON outbox
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
