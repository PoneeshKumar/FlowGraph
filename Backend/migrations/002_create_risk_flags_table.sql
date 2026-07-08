-- Migration 002: risk_flags shared store
--
-- Stores fraud detection results from all detectors (cycle detection now;
-- structuring / CTR to follow). The `flag_type` discriminator lets each
-- detector write its own entries while sharing one table and one reporting
-- surface.
--
-- Design choices:
--   - fingerprint UNIQUE: same logical flag (same ring / same account+window)
--     UPSERTS on conflict, bumping detection_count + last_detected_at instead
--     of creating duplicates. This makes re-detection safe and idempotent.
--   - explanation NOT NULL: regulatory requirement — every flag must carry a
--     human-readable reason. The application layer enforces this before insert.
--   - status 'open' default: forward-compatible with a review/resolve workflow
--     (analyst marks as 'reviewed', 'dismissed', 'escalated') without a schema
--     change.
--   - details JSONB: stores detector-specific raw data (amounts, txn_ids, hop
--     count, timestamps) for audit trails and AI enrichment context.

CREATE TABLE IF NOT EXISTS risk_flags (
    id                BIGSERIAL    PRIMARY KEY,
    flag_type         VARCHAR(20)  NOT NULL,     -- 'CYCLE' | 'STRUCTURING' | 'CTR'
    fingerprint       VARCHAR(128) NOT NULL UNIQUE,
    account_ids       TEXT[]       NOT NULL,
    risk_level        VARCHAR(10)  NOT NULL,     -- 'low' | 'medium' | 'high' | 'critical'
    risk_score        NUMERIC      NOT NULL,
    explanation       TEXT         NOT NULL,     -- never null: regulatory requirement
    details           JSONB,                     -- amounts, timestamps, txn_ids, hop_count
    status            VARCHAR(12)  NOT NULL DEFAULT 'open',
    first_detected_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_detected_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    detection_count   INT          NOT NULL DEFAULT 1,
    created_at        TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_flags_type_level
    ON risk_flags (flag_type, risk_level);

CREATE INDEX IF NOT EXISTS idx_risk_flags_last_detected
    ON risk_flags (last_detected_at DESC)
    WHERE status = 'open';
