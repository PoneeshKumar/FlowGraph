-- Pipeline run job-status table for the community visualiser.
-- Tracks the async "Run pipeline" jobs launched from /viz: their stage,
-- progress, per-stage counts, and terminal status. Regenerable job state,
-- not payment data — written directly, outside the outbox convention.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','completed','failed')),
    stage        TEXT,
    progress     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    counts       JSONB NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs (status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs (started_at DESC);
